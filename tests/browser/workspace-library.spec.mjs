import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const route='/prompt_manager/workspace#/library';
const cards=page=>page.locator('article[data-prompt-id]');
const history=page=>page.getByRole('region',{name:'Prompt history',exact:true});
const saved=page=>history(page).getByRole('region',{name:'Saved prompt texts',exact:true});
const gallery=page=>page.getByRole('region',{name:'Shared gallery',exact:true});
const media=page=>gallery(page).locator('article[data-image-key]');

async function openHistory(page,text='Cat portrait') {
    await cards(page).filter({has:page.getByText(text,{exact:true})}).getByRole('link',{name:'Explore history',exact:true}).click();
    await expect(history(page)).toBeVisible();
}
async function selectVariant(page,index) {
    const choice=history(page).getByRole('button',{name:`Select negative variant ${index+1}`,exact:true});
    await choice.click();await expect(choice).toHaveAttribute('aria-pressed','true');
}

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

test('library groups reusable negatives under one positive without exposing negative-only components as parents',async({page})=>{
    await page.goto(route);await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).filter({has:page.getByText('Cat portrait',{exact:true})})).toHaveCount(1);
    await expect(cards(page).filter({has:page.getByText('Dual role portrait',{exact:true})})).toHaveCount(1);
    await expect(cards(page).filter({hasText:'low contrast'})).toHaveCount(0);
    const cat=cards(page).filter({has:page.getByText('Cat portrait',{exact:true})});
    await expect(cat).toContainText(/4.*variants/i);await expect(cat).toContainText(/3.*images/i);
    await expect(page.locator('#workspace-main')).not.toContainText(/\b\d+ (?:tracked )?runs\b/i);
});

test('history displays the selected full negative beneath its positive and filters one shared physical gallery',async({page,request})=>{
    const rows=await(await request.get('/prompt_manager/library')).json();const cat=rows.items.find(item=>item.text==='Cat portrait');
    const data=await(await request.get(`/prompt_manager/library/${cat.id}`)).json();
    await page.goto(route);await openHistory(page);await expect(media(page)).toHaveCount(3);
    for(const negative of ['blurry, watermark','low quality','low contrast','']) {
        const index=data.variants.findIndex(variant=>variant.negative_text===negative);await selectVariant(page,index);
        const neg=saved(page).getByRole('region',{name:'Negative prompt',exact:true});
        await expect(neg).toContainText(negative||'Empty negative prompt');
        await expect(media(page)).toHaveCount(negative==='blurry, watermark'||negative==='low quality'?1:0);
        const positive=saved(page).getByRole('region',{name:'Positive prompt',exact:true});
        await expect(positive).toContainText('Cat portrait');
        const order=await positive.evaluate((element)=>element.compareDocumentPosition(element.parentElement.querySelector('[aria-label="Negative prompt"]')));
        expect(order&4).toBeTruthy();
    }
    await history(page).getByRole('button',{name:'All images for this positive',exact:true}).click();await expect(media(page)).toHaveCount(3);
    expect(await media(page).evaluateAll(nodes=>nodes.map(node=>node.dataset.imageKey))).toEqual(expect.arrayContaining(['0:shared/same.png','1:shared/same.png','0:legacy-unassigned.png']));
});

test('search, category and tag filters preserve their constraints and return standalone positives',async({page})=>{
    await page.goto(route);await page.getByRole('searchbox',{name:'Search prompts',exact:true}).fill('Dog landscape');
    await expect(cards(page)).toHaveCount(1);await expect(cards(page).first()).toContainText('Dog landscape');
    await page.getByRole('searchbox',{name:'Search prompts',exact:true}).fill('');
    await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('Portraits');
    await page.getByRole('textbox',{name:'Tags',exact:true}).fill('studio');await expect(cards(page)).toHaveCount(1);
    await page.getByRole('textbox',{name:'Tags',exact:true}).fill('outdoors');await expect(cards(page)).toHaveCount(0);
    await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('');await expect(cards(page)).toHaveCount(1);
});

test('library infinite loading keeps unique parents and keyboard-accessible history controls',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:125}})).toBeOK();
    await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    await page.goto(route);await expect(cards(page).first()).toBeVisible();
    const more=page.getByRole('button',{name:'Load more prompts',exact:true});
    expect(await cards(page).count()).toBeLessThan(128);
    let hasMore=true;
    while(hasMore) {
        const nextPage=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/prompt_manager/library'&&Boolean(url.searchParams.get('cursor'));});
        await more.focus();await page.keyboard.press('Enter');
        const appended=await (await nextPage).json();expect(appended.items.length).toBeGreaterThan(0);hasMore=appended.has_more;
        if(hasMore)await expect(more).toHaveAttribute('aria-disabled','false');
    }
    await expect(more).toBeHidden();expect(await cards(page).count()).toBeLessThan(80);
    const ids=await cards(page).evaluateAll(nodes=>nodes.map(node=>node.dataset.promptId));expect(new Set(ids).size).toBe(ids.length);
    await expect(page.getByRole('button',{name:/next page|previous page/i})).toHaveCount(0);
    const firstId=await cards(page).first().getAttribute('data-prompt-id');
    const explore=page.locator(`article[data-prompt-id="${firstId}"]`).getByRole('link',{name:'Explore history',exact:true});
    await explore.focus();await expect(explore).toBeFocused();await page.keyboard.press('Enter');await expect(history(page)).toBeVisible();
});

test('failed library reads retry while late search responses cannot overwrite newer results',async({page})=>{
    let fail=true;let release;const hold=new Promise(resolve=>{release=resolve;});
    await page.route('**/prompt_manager/library?*',async request=>{
        const url=new URL(request.request().url());
        if(fail)return request.fulfill({status:503,json:{success:false,error:'Unavailable'}});
        if(url.searchParams.get('text')==='Cat')await hold;
        await request.continue().catch(()=>{});
    });
    await page.goto(route);await expect(page.getByRole('button',{name:'Retry prompt library',exact:true})).toBeVisible();
    fail=false;await page.getByRole('button',{name:'Retry prompt library',exact:true}).click();await expect(cards(page)).toHaveCount(3);
    const search=page.getByRole('searchbox',{name:'Search prompts',exact:true});const oldRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('text')==='Cat');await search.fill('Cat');await oldRequest;await search.fill('Dog');
    await expect(cards(page)).toHaveCount(1);await expect(cards(page).first()).toContainText('Dog landscape');release();await expect(cards(page).first()).toContainText('Dog landscape');
});

test('returning from history restores library context and mobile content stays inside the viewport',async({page})=>{
    await page.setViewportSize({width:390,height:844});await page.goto(route);
    await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('Animals');
    await page.getByRole('searchbox',{name:'Search prompts',exact:true}).fill('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await openHistory(page);await selectVariant(page,0);
    await expect(saved(page).getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Cat portrait');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await history(page).getByRole('link',{name:'Back to library',exact:true}).click();
    await expect(page.getByRole('searchbox',{name:'Search prompts',exact:true})).toHaveValue('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name:'Overview',exact:true}).click();
    await expect(history(page)).toHaveCount(0);await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('history detail failures retry and returning to the list ignores late detail responses',async({page})=>{
    let fail=true;let release;const hold=new Promise(resolve=>{release=resolve;});
    await page.route(/\/prompt_manager\/library\/\d+(?:\?|$)/,async route=>{
        if(fail)return route.fulfill({status:503,json:{success:false,error:'Unavailable'}});
        await hold;await route.continue().catch(()=>{});
    });
    try {
        await page.goto(route);await cards(page).filter({has:page.getByText('Cat portrait',{exact:true})}).getByRole('link',{name:'Explore history',exact:true}).click();
        await expect(page.getByRole('button',{name:'Retry prompt history',exact:true})).toBeVisible();
        fail=false;await page.getByRole('button',{name:'Retry prompt history',exact:true}).click();
        await page.getByRole('link',{name:'Back to library',exact:true}).click();release();
        await expect(cards(page)).toHaveCount(3);await expect(history(page)).toHaveCount(0);
    } finally {release();}
});

test('history shares real image metadata, vendored Viewer and the acknowledged prompt sender',async({page,context,request})=>{
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        const target={id:'pair-1',label:'Library destination',type:'PromptManagerPair',roles:['positive','negative']};
        window.libraryApplied=[];
        window.libraryHost=createBridgeHost({origin:location.origin,namespace:'/',label:'Library ComfyUI',adapter:{
            listTargets:()=>[target],preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:`Previous ${role}`,after})),composition:{prepend:'',append:'',prependConnected:false,appendConnected:false,before:'Previous positive',after:roles.positive??'Previous positive'}},snapshot:roles}),
            apply:roles=>{window.libraryApplied.push(roles);return{targetId:target.id,values:roles};},
        }});
    });
    try {
        await page.goto(route);
        const parentId=await cards(page).filter({has:page.getByText('Cat portrait',{exact:true})}).getAttribute('data-prompt-id');
        const recipe=await(await request.get(`/prompt_manager/library/${parentId}`)).json();
        await openHistory(page);await selectVariant(page,recipe.variants.findIndex(variant=>variant.negative_text==='blurry, watermark'));
        const chosenNegative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre').textContent();
        await saved(page).getByRole('button',{name:'Send prompt pair',exact:true}).click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});
        await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({label:'Library ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Library destination'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();await expect(sender).toContainText(chosenNegative||'(empty)');
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.libraryApplied)).toEqual([{positive:'Cat portrait',negative:chosenNegative}]);
        await sender.getByRole('button',{name:'Close',exact:true}).click();
        await media(page).first().getByRole('button',{name:'Open same.png',exact:true}).click();
        const inspector=page.getByRole('region',{name:'Image details',exact:true});await expect(inspector.getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Cat portrait');
        await inspector.getByRole('button',{name:'Open full preview',exact:true}).click();await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
        await page.keyboard.press('Escape');await expect(page.locator('dialog[open]')).toHaveCount(0);
        await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name:'Overview',exact:true}).click();await expect(page.locator('.viewer-container')).toHaveCount(0);
    } finally {await host.evaluate(()=>window.libraryHost.dispose());await host.close();}
});

test('large variant histories append bounded pages without replacing the selected negative',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_variants:41}})).toBeOK();
    await page.goto(route);await openHistory(page);
    const variants=history(page).getByRole('button',{name:/^Select negative variant \d+$/});await expect(variants).toHaveCount(40);
    await selectVariant(page,0);const chosenNegative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre').textContent();
    await history(page).getByRole('button',{name:'Load more negative variants',exact:true}).click();await expect(variants).toHaveCount(45);
    await expect(history(page).getByRole('button',{name:'Select negative variant 1',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre')).toHaveText(chosenNegative);
});
