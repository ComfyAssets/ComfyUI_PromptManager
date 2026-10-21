import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const route='/prompt_manager/workspace#/library';
const cards=page=>page.locator('article[data-prompt-id]');
const history=page=>page.getByRole('region',{name:'Prompt history',exact:true});
const saved=page=>history(page).getByRole('region',{name:'Saved prompt texts',exact:true});
const gallery=page=>page.getByRole('region',{name:'Shared gallery',exact:true});
const media=page=>gallery(page).locator('article[data-image-key]');

const refine=page=>page.getByRole('dialog',{name:'Refine this view',exact:true});
/** The Library's Tags filter lives in "Refine this view" (#249 D14). */
async function applyTags(page,tags) {
    // D44: Filters reads "Filters · 1" while a Tags filter is active.
    await page.locator('#workspace-main search.toolbar').getByRole('button',{name:/^Filters(?: · 1)?$/}).click();
    await refine(page).getByRole('textbox',{name:'Tags',exact:true}).fill(tags);
    await refine(page).getByRole('button',{name:'Apply filters',exact:true}).click();await expect(refine(page)).toHaveCount(0);
}
/** The first shelf page, then `pages` more, each waited for by its own response (the caption counts the filtered set). */
async function loadShelfPages(page,more,pages) {
    await expect(page.locator('.section-caption')).toContainText('203 positive prompts');await expect(more).toHaveAttribute('aria-disabled','false');
    for(let index=0;index<pages;index++) {
        const response=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.has('cursor');});
        await more.evaluate(node=>node.click());await response;await expect(more).toHaveAttribute('aria-disabled','false');
    }
}
async function openHistory(page,text='Cat portrait') {
    await cards(page).filter({has:page.getByText(text,{exact:true})}).getByRole('button',{name:'Explore history',exact:true}).click();
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

test('library groups reusable negatives under one positive without exposing negative-only components as parents',async({page,request})=>{
    const rows=await(await request.get('/prompt_manager/library')).json();
    await page.goto(route);await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).filter({has:page.getByText('Cat portrait',{exact:true})})).toHaveCount(1);
    await expect(cards(page).filter({has:page.getByText('Dual role portrait',{exact:true})})).toHaveCount(1);
    await expect(cards(page).filter({hasText:'low contrast'})).toHaveCount(0);
    const cat=cards(page).filter({has:page.getByText('Cat portrait',{exact:true})});
    await expect(cat).toContainText(/4.*variants/i);await expect(cat).toContainText(/3.*images/i);
    // D23: "N runs" is the parent's own run_count, never a recorded-execution count.
    for(const item of rows.items) await expect(page.locator(`article[data-prompt-id="${item.id}"] .card-meta > span`).nth(1)).toHaveText(`${item.run_count} run${item.run_count===1?'':'s'}`);
});

test('history displays the selected full negative beneath its positive and filters one shared physical gallery',async({page,request})=>{
    const rows=await(await request.get('/prompt_manager/library')).json();const cat=rows.items.find(item=>item.text==='Cat portrait');
    const data=await(await request.get(`/prompt_manager/library/${cat.id}`)).json();
    // The concept opens on the first negative variant and filters the gallery to it (app.js navigate: state.variant = 0).
    await page.goto(route);await openHistory(page);
    await expect(history(page).getByRole('button',{name:'Select negative variant 1',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(media(page)).toHaveCount(['blurry, watermark','low quality'].includes(data.variants[0].negative_text)?1:0);
    await expect(history(page).getByRole('button',{name:'All images for this positive',exact:true})).toHaveAttribute('aria-pressed','false');
    for(const negative of ['blurry, watermark','low quality','low contrast','']) {
        const index=data.variants.findIndex(variant=>variant.negative_text===negative);await selectVariant(page,index);
        const neg=saved(page).getByRole('region',{name:'Negative prompt',exact:true});
        await expect(neg.locator('p.prompt-text')).toHaveText(negative||'No negative prompt (explicitly empty)');
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
    await page.goto(route);await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('Dog landscape');
    await expect(cards(page)).toHaveCount(1);await expect(cards(page).first()).toContainText('Dog landscape');
    await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('');
    await page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('Portraits');
    await applyTags(page,'studio');await expect(cards(page)).toHaveCount(1);
    await applyTags(page,'outdoors');await expect(cards(page)).toHaveCount(0);
    await page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('');await expect(cards(page)).toHaveCount(1);
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
    const explore=page.locator(`article[data-prompt-id="${firstId}"]`).getByRole('button',{name:'Explore history',exact:true});
    await explore.focus();await expect(explore).toBeFocused();await page.keyboard.press('Enter');await expect(history(page)).toBeVisible();
});

// D40/D15: with no Refresh, Retry also re-reads a category list that failed with the shelf.
test('Retry re-reads a failed category list with the shelf',async({page})=>{
    let fail=true;const reads=[];
    await page.route(url=>['/prompt_manager/library','/prompt_manager/library/options'].includes(url.pathname),async request=>{
        reads.push(new URL(request.request().url()).pathname);
        if(fail)return request.fulfill({status:503,json:{success:false,error:'Unavailable'}});
        await request.continue().catch(()=>{});
    });
    await page.goto(route);
    const retry=page.getByRole('button',{name:'Retry prompt library',exact:true});await expect(retry).toBeVisible();
    const categoriesFailed=page.getByRole('alert').filter({hasText:'Categories could not be loaded. Open the Prompt library again to retry.'});
    await expect(categoriesFailed).toBeVisible();
    fail=false;reads.length=0;await retry.click();await expect(cards(page)).toHaveCount(3);
    await expect(categoriesFailed).toBeHidden();
    await expect(page.getByRole('combobox',{name:'Filter by category',exact:true}).locator('option',{hasText:'Portraits'})).toHaveCount(1);
    expect(reads).toContain('/prompt_manager/library/options');
    // Apply with the same Tags, and a search that lands on the values already shown, reload nothing.
    reads.length=0;
    await page.locator('#workspace-main search.toolbar').getByRole('button',{name:'Filters',exact:true}).click();
    await refine(page).getByRole('button',{name:'Apply filters',exact:true}).click();await expect(refine(page)).toHaveCount(0);
    const search=page.getByRole('searchbox',{name:'Search workspace',exact:true});await search.fill(' ');
    await page.waitForTimeout(400);expect(reads).toEqual([]);await expect(cards(page)).toHaveCount(3);
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
    const search=page.getByRole('searchbox',{name:'Search workspace',exact:true});const oldRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('text')==='Cat');await search.fill('Cat');await oldRequest;await search.fill('Dog');
    await expect(cards(page)).toHaveCount(1);await expect(cards(page).first()).toContainText('Dog landscape');release();await expect(cards(page).first()).toContainText('Dog landscape');
});

test('library toolbar matches the concept and tags live in Refine this view',async({page})=>{
    await page.goto(route);await expect(cards(page)).toHaveCount(3);
    // app.js toolbar() a347–360: search.toolbar > label.search-field (input#search), select#category-filter, select#sort, Filters.
    const toolbar=page.locator('#workspace-main search.toolbar');await expect(toolbar).toHaveCount(1);
    expect(await toolbar.evaluate(node=>[...node.children].map(child=>child.tagName.toLowerCase()+(child.id?`#${child.id}`:'')+(child.className?`.${child.className.split(' ').filter(name=>!name.startsWith('ca-')).join('.')}`:'')))).toEqual(['label.search-field','select#category-filter','select#sort','button']);
    await expect(toolbar.locator('#search')).toHaveAttribute('placeholder','Search prompts, tags, filenames…');
    await expect(toolbar.locator('#sort option')).toHaveText(['Recently added','Name A–Z','Highest rated','Oldest first']);
    await expect(toolbar.getByRole('button')).toHaveText(['Filters']);
    await expect(page.getByRole('button',{name:'Refresh library',exact:true})).toHaveCount(0);
    await expect(page.getByRole('textbox',{name:'Tags',exact:true})).toHaveCount(0);
    await expect(page.locator('.library-shelf > search.toolbar:first-child')).toHaveCount(1);
    // Tags apply from the dialog only; Clear clears them and the toolbar category, as the concept's does.
    await page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('Portraits');await applyTags(page,'studio');await expect(cards(page)).toHaveCount(1);
    // D44: the active Tags filter shows on the button; the category is the toolbar's own and is not counted.
    await expect(toolbar.getByRole('button')).toHaveText(['Filters · 1']);
    await toolbar.getByRole('button',{name:'Filters · 1',exact:true}).click();
    await expect(refine(page).locator('.field label')).toHaveText(['Tags']);await expect(refine(page).getByRole('textbox',{name:'Tags',exact:true})).toHaveValue('studio');
    await refine(page).getByRole('button',{name:'Clear filters',exact:true}).click();await expect(refine(page)).toHaveCount(0);
    await expect(toolbar.getByRole('button',{name:'Filters',exact:true})).toBeFocused();
    await expect(page.getByRole('combobox',{name:'Filter by category',exact:true})).toHaveValue('');await expect(cards(page)).toHaveCount(3);
    const sorted=page.waitForRequest(item=>new URL(item.url()).searchParams.get('sort')==='name');
    await page.getByRole('combobox',{name:'Sort results',exact:true}).selectOption({label:'Name A–Z'});await sorted;
});

test('caption counts equal the filtered cards',async({page,request})=>{
    const totals=async query=>{const data=await(await request.get(`/prompt_manager/library?${query}`)).json();return [data.items.length,data.items.reduce((sum,item)=>sum+item.variant_count,0),data.variant_total];};
    await page.goto(route);await expect(cards(page)).toHaveCount(3);
    const caption=page.locator('#workspace-main .section-caption');
    for(const [query,act] of [['',null],['category=Portraits',()=>page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('Portraits')],['category=Portraits&tag=outdoors',()=>applyTags(page,'outdoors')]]) {
        if(act)await act();
        const [count,variants,total]=await totals(query);expect(variants).toBe(total);
        await expect(cards(page)).toHaveCount(count);
        await expect(caption.locator('span')).toHaveText([`${count} positive prompt${count===1?'':'s'} · ${variants} negative variant${variants===1?'':'s'}`,'Images are grouped by positive prompt']);
    }
});

// #249 final review M5: the count is a status region, written only when it changes, so Load more never re-announces it.
test('the caption count is announced once per change and kept while the shelf reloads',async({page,request})=>{
    await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}});
    await page.goto(route);const more=page.getByRole('button',{name:'Load more prompts',exact:true});
    const count=page.locator('#workspace-main .section-caption > span').first();
    await expect(count).toHaveAttribute('role','status');await expect(count).toHaveText(/^203 positive prompts · \d+ negative variants?$/);
    await count.evaluate(node=>{window.__countWrites=0;new MutationObserver(records=>{window.__countWrites+=records.length;}).observe(node,{childList:true,characterData:true,subtree:true});});
    await loadShelfPages(page,more,1);
    expect(await page.evaluate(()=>window.__countWrites)).toBe(0);
    // A filter change keeps the last count shown while the next page loads, then writes the new one.
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(url=>url.pathname==='/prompt_manager/library'&&url.searchParams.get('category')==='Portraits',async route=>{await held;await route.continue();});
    await page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('Portraits');
    await expect(page.locator('.library-status')).toHaveText('Loading prompt library…');
    await expect(page.locator('#workspace-main .section-caption')).toBeVisible();await expect(count).toHaveText(/^203 positive prompts/);
    release();await expect(count).not.toHaveText(/^203 /);
    expect(await page.evaluate(()=>window.__countWrites)).toBeGreaterThan(0);
});

test('a Library-embedded media card stars its file and the star survives a reload',async({page})=>{
    await page.goto(route);await openHistory(page);
    await history(page).getByRole('button',{name:'All images for this positive',exact:true}).click();await expect(media(page)).toHaveCount(3);
    const card=gallery(page).locator('article[data-image-key="0:legacy-unassigned.png"]');
    const star=card.getByRole('button',{name:'Favorite legacy-unassigned.png',exact:true});
    await expect(star).toHaveAttribute('aria-pressed','false');await expect(card.locator('input[type=checkbox]')).toHaveCount(0);
    await expect(page.locator('.bulkbar, #bulkbar')).toHaveCount(0);
    const saved=page.waitForResponse(item=>new URL(item.url()).pathname==='/prompt_manager/images/favorite'&&item.request().method()==='PUT');
    await star.click();expect((await saved).ok()).toBe(true);
    await expect(star).toHaveAttribute('aria-pressed','true');await expect(star).toHaveText('★');
    await expect(page.getByText('Added to favorites.',{exact:true})).toBeVisible();
    await page.reload();await history(page).getByRole('button',{name:'All images for this positive',exact:true}).click();
    await expect(gallery(page).locator('article[data-image-key="0:legacy-unassigned.png"]').getByRole('button',{name:'Favorite legacy-unassigned.png',exact:true})).toHaveAttribute('aria-pressed','true');
});

test('a sort change atomically discards old row geometry and renders changed parent data',async({page,request})=>{
    await page.goto(route);await expect(cards(page)).toHaveCount(3);
    const card=cards(page).filter({has:page.getByText('Dog landscape',{exact:true})});const id=await card.getAttribute('data-prompt-id');
    await expect(await request.put(`/prompt_manager/prompts/${id}`,{data:{text:'Revised dog landscape'}})).toBeOK();
    await expect(await request.put(`/prompt_manager/prompts/${id}/rating`,{data:{rating:5}})).toBeOK();
    await page.evaluate(()=>{
        const native=requestAnimationFrame;const held=[];let paused=true;window.releaseLibraryFrames=()=>{paused=false;window.requestAnimationFrame=native;for(const callback of held)callback();};
        window.requestAnimationFrame=callback=>native(time=>{if(paused)held.push(()=>callback(time));else callback(time);});
    });
    try {
        // D15: no Refresh; any filter change restarts the stream the same way.
        await page.getByRole('combobox',{name:'Sort results',exact:true}).selectOption('oldest');
        await expect(page.locator('.library-status')).not.toHaveText('Loading prompt library…');
    } finally {await page.evaluate(()=>window.releaseLibraryFrames());}
    await expect(cards(page)).toHaveCount(3);
    const changed=page.locator(`article[data-prompt-id="${id}"]`);await expect(changed).toContainText('Revised dog landscape');
    await expect(changed.locator('.card-footer .ca-visually-hidden')).toHaveText('Rated 5 out of 5');await expect(changed.locator('.card-footer > span > [aria-hidden="true"]')).toHaveText('★★★★★');
    await expect(page.getByRole('button',{name:'Retry prompt library',exact:true})).toBeHidden();
});

test('returning from history restores library context and mobile content stays inside the viewport',async({page})=>{
    await page.setViewportSize({width:390,height:844});await page.goto(route);
    await page.getByRole('combobox',{name:'Filter by category',exact:true}).selectOption('Animals');
    await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await openHistory(page);await selectVariant(page,0);
    await expect(saved(page).getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Cat portrait');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page.getByRole('searchbox',{name:'Search workspace',exact:true})).toHaveValue('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(history(page)).toHaveCount(0);await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('empty shelf visits restore saved scroll without inventing a parent anchor',async({page})=>{
    await page.setViewportSize({width:1600,height:300});await page.goto(route);
    await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('No matching saved positive');
    await expect(cards(page)).toHaveCount(0);await expect(page.locator('.library-status')).toContainText('No matching prompts.');
    await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
    await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(0);
    const position=await page.evaluate(()=>scrollY);
    await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).evaluate(node=>node.click());
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name: /^Prompt library(?:\s*[\d,]+)?$/}).evaluate(node=>node.click());
    await expect(page.getByRole('searchbox',{name:'Search workspace',exact:true})).toHaveValue('No matching saved positive');
    await expect(cards(page)).toHaveCount(0);
    await expect.poll(()=>page.evaluate(expected=>Math.abs(scrollY-expected),position)).toBeLessThanOrEqual(1);
    const search=page.getByRole('searchbox',{name:'Search workspace',exact:true});await search.focus();await search.fill('Dog landscape');
    await expect(cards(page)).toHaveCount(1);await expect(search).toBeFocused();
    await page.evaluate(()=>scrollTo(0,0));await page.getByRole('combobox',{name:'Sort results',exact:true}).selectOption('oldest');await expect(cards(page)).toHaveCount(1);
    expect(await page.evaluate(()=>scrollY)).toBe(0);
    const anchor=await firstVisibleParent(page);expect(anchor).not.toBeNull();
    await cards(page).getByRole('button',{name:'Explore history',exact:true}).evaluate(node=>node.click());await expect(history(page)).toBeVisible();
    // A short phone window, so the one-card shelf can scroll far enough to put the parent back at its offset (nothing
    // follows the shelf's continuation since the legacy link moved to Overview, #249 E3).
    await page.setViewportSize({width:390,height:600});await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await retainedParent(page,anchor);await expect(page.getByRole('searchbox',{name:'Search workspace',exact:true})).toHaveValue('Dog landscape');
});

test('history detail failures retry and returning to the list ignores late detail responses',async({page})=>{
    let fail=true;let release;const hold=new Promise(resolve=>{release=resolve;});
    await page.route(/\/prompt_manager\/library\/\d+(?:\?|$)/,async route=>{
        if(fail)return route.fulfill({status:503,json:{success:false,error:'Unavailable'}});
        await hold;await route.continue().catch(()=>{});
    });
    try {
        await page.goto(route);await cards(page).filter({has:page.getByText('Cat portrait',{exact:true})}).getByRole('button',{name:'Explore history',exact:true}).click();
        await expect(page.getByRole('button',{name:'Retry prompt history',exact:true})).toBeVisible();
        fail=false;await page.getByRole('button',{name:'Retry prompt history',exact:true}).click();
        await page.getByRole('button',{name:'← Prompt library',exact:true}).click();release();
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
        const chosenNegative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text').textContent();
        await page.locator('#workspace-main > .heading').getByRole('button',{name:'Send prompt pair to ComfyUI: Cat portrait',exact:true}).click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});
        await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({label:'Library ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Library destination'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();await expect(sender).toContainText(chosenNegative||'(empty)');
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.libraryApplied)).toEqual([{positive:'Cat portrait',negative:chosenNegative}]);
        await sender.getByRole('button',{name:'Close',exact:true}).click();
        await media(page).first().getByRole('button',{name:'Inspect same.png',exact:true}).click();
        const inspector=page.getByRole('complementary',{name:'Selected image details',exact:true});await expect(inspector.getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Cat portrait');
        await expect(inspector.locator('.meta-grid dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);
        // D20: provenance and raw records are the viewer's full details, opened from the card image (D19).
        await expect(inspector.locator('summary')).toHaveCount(0);
        await media(page).first().getByRole('button',{name:'Open same.png',exact:true}).click();await expect(page.locator('dialog[open] .viewer-canvas img')).toBeVisible();
        await expect(page.locator('dialog[open] #viewer-details summary')).toHaveText(['File & provenance','Workflow & raw metadata']);
        await page.keyboard.press('Escape');await expect(page.locator('dialog[open]')).toHaveCount(0);
        await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).click();await expect(page.locator('.viewer-container')).toHaveCount(0);
    } finally {await host.evaluate(()=>window.libraryHost.dispose());await host.close();}
});

test('large variant histories append bounded pages without replacing the selected negative',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_variants:41}})).toBeOK();
    await page.goto(route);await openHistory(page);
    const variants=history(page).getByRole('button',{name:/^Select negative variant \d+$/});await expect(variants).toHaveCount(40);
    await selectVariant(page,0);const chosenNegative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text').textContent();
    await history(page).getByRole('button',{name:'Load more negative variants',exact:true}).click();await expect(variants).toHaveCount(45);
    await expect(history(page).getByRole('button',{name:'Select negative variant 1',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(chosenNegative);
});

async function observeLibrary(page, hold=false) {
    await page.addInitScript(({hold})=>{
        const NativeResizeObserver=window.ResizeObserver;
        const records=[];let holding=hold;
        const raf=window.requestAnimationFrame.bind(window),cancel=window.cancelAnimationFrame.bind(window);
        const pending=new Set(),heldFrames=new Map();let requested=0,mutations=0,unmounted=0,holdingFrames=false;
        window.requestAnimationFrame=callback=>{requested++;const id=raf(time=>{const deliver=()=>{pending.delete(id);callback(time);};if(holdingFrames)heldFrames.set(id,deliver);else deliver();});pending.add(id);return id;};
        window.cancelAnimationFrame=id=>{pending.delete(id);heldFrames.delete(id);cancel(id);};
        window.ResizeObserver=class {
            constructor(callback) {
                const record={callback,targets:new Set(),entries:new Map(),latest:new Map(),disconnected:false};
                record.native=new NativeResizeObserver(entries=>{
                    const ready=[];
                    for(const entry of entries) {
                        if(entry.target.matches('.prompt-card')) {
                            if(!entry.target.isConnected)unmounted++;
                            record.latest.set(entry.target,entry);
                            if(holding) {record.entries.set(entry.target,entry);continue;}
                        }
                        ready.push(entry);
                    }
                    if(ready.length)callback(ready,this);
                });records.push(record);this.record=record;
            }
            observe(target,options){this.record.targets.add(target);this.record.native.observe(target,options);}
            unobserve(target){this.record.targets.delete(target);this.record.entries.delete(target);this.record.latest.delete(target);this.record.native.unobserve(target);}
            disconnect(){this.record.disconnected=true;this.record.targets.clear();this.record.entries.clear();this.record.native.disconnect();}
        };
        new MutationObserver(entries=>{mutations+=entries.filter(entry=>entry.target.matches('.prompt-card,.library-row,.library-row-spacer')).length;}).observe(document,{subtree:true,attributes:true,attributeFilter:['style']});
        window.libraryObservation={
            hold(){holding=true;},
            release(){holding=false;for(const record of records){const entries=[...record.entries.values()].filter(entry=>record.targets.has(entry.target));record.entries.clear();if(entries.length)record.callback(entries,record.native);}},
            holdFrames(){holdingFrames=true;},
            releaseFrames(){holdingFrames=false;const callbacks=[...heldFrames.values()];heldFrames.clear();for(const callback of callbacks)callback();},
            equal(){for(const record of records){const entries=[...record.latest.values()].filter(entry=>record.targets.has(entry.target));if(entries.length)record.callback(entries,record.native);}},
            stats(){return {requested,pending:pending.size,heldFrames:heldFrames.size,measurements:records.reduce((sum,record)=>sum+record.entries.size,0),mutations,unmounted,targets:records.reduce((sum,record)=>sum+record.targets.size,0)};},
        };
    },{hold});
}
async function settleLibrary(page) {
    await expect.poll(async()=>page.evaluate(()=>window.libraryObservation.stats().pending)).toBe(0);
    await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,60)));
    await expect.poll(async()=>page.evaluate(()=>window.libraryObservation.stats().pending)).toBe(0);
}
async function firstVisibleParent(page) {
    return cards(page).evaluateAll(nodes=>{
        const card=nodes.find(node=>{const box=node.getBoundingClientRect();return box.bottom>0&&box.top<innerHeight;});
        return card?{id:card.dataset.promptId,offset:card.getBoundingClientRect().top}:null;
    });
}
async function retainedParent(page, anchor) {
    const card=page.locator(`article[data-prompt-id="${anchor.id}"]`);
    await expect(card).toHaveCount(1);
    await expect.poll(async()=>Math.abs(await card.evaluate(node=>node.getBoundingClientRect().top)-anchor.offset)).toBeLessThanOrEqual(2);
}

test('Library cards clamp long prompts and keep real content inside the card',async({page,request})=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    const data=await(await request.get('/prompt_manager/library')).json();
    for(const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});await page.goto(route);await expect(cards(page)).toHaveCount(6);
        await expect(page.locator('#workspace-main > .heading p.muted')).toContainText('One positive.');
        await expect(page.locator('#workspace-main p').filter({hasText:'One positive. Reusable negatives.'})).toHaveCount(1);
        for(const theme of ['dark','light']) {
            await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
            await expect(page.locator('.prompt-section.positive .role-label').first()).toHaveCSS('color',theme==='dark'?'rgb(115, 221, 169)':'rgb(23, 107, 70)');
        }
        await expect(page.locator('.prompt-card .card-art').first()).toHaveCSS('height',width===1600?'170px':'150px');
        await expect(page.locator('.card-content').first()).toHaveCSS('padding',width===1600?'18px':'15px');
        await expect(page.locator('.library-grid')).toHaveCSS('gap','18px');
        const long=cards(page).filter({hasText:'A study in very long creative context'});
        const prompt=long.locator('.prompt-text');expect((await prompt.textContent()).length).toBeGreaterThan(1500);
        // D23: two lines of the concept's 12px/1.75 prompt text, the rest clipped inside the card.
        expect(await prompt.evaluate(node=>({height:node.clientHeight,clipped:node.scrollHeight>node.clientHeight+1}))).toEqual({height:42,clipped:true});
        expect(await long.evaluate(node=>node.getBoundingClientRect().height)).toBeLessThan(560);
        for(const selector of ['.card-title','.card-content','.card-content > .tags','.card-footer']) {
            expect(await long.locator(selector).evaluate(node=>node.scrollHeight<=node.clientHeight+1&&node.scrollWidth<=node.clientWidth+1),selector).toBe(true);
        }
        await prompt.focus();await expect(prompt).toBeFocused();
        await long.getByRole('button',{name:'Explore history',exact:true}).scrollIntoViewIfNeeded();
        await expect(long.getByRole('button',{name:'Explore history',exact:true})).toBeVisible();
        for(const item of data.items) {
            const card=page.locator(`article[data-prompt-id="${item.id}"]`);
            const footer=card.locator('.card-footer > span');
            await expect(footer.locator('[aria-hidden="true"]')).toHaveText(item.rating?'★'.repeat(item.rating):[]);
            await expect(footer.locator('.ca-visually-hidden')).toHaveText(item.rating?`Rated ${item.rating} out of 5`:[]);
            await expect(card.locator('.card-content > .tags .chip')).toHaveText(item.tags);
            await expect(card.locator('.card-meta > span').nth(1)).toHaveText(`${item.run_count} run${item.run_count===1?'':'s'}`);
        }
        expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
        const boxes=await cards(page).evaluateAll(nodes=>nodes.map(node=>{const b=node.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,bottom:b.bottom};}));
        for(let i=0;i<boxes.length;i++) for(const b of boxes.slice(i+1)) {const a=boxes[i];expect(a.right<=b.x+1||b.right<=a.x+1||a.bottom<=b.y+1||b.bottom<=a.y+1).toBe(true);}
    }
});

test('row measurement preserves anchor after width invalidation and append',async({page,request})=>{
    await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}});
    await observeLibrary(page,true);await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/serve/**',async route=>{await held;await route.continue().catch(()=>{});});
    try {
        await page.setViewportSize({width:1600,height:1100});await page.goto(route);
        const more=page.getByRole('button',{name:'Load more prompts',exact:true});
        await loadShelfPages(page,more,3);
        await page.evaluate(()=>scrollTo(0,document.querySelector('.library-grid').getBoundingClientRect().top+scrollY+40*486+25));
        await settleLibrary(page);const anchor=await firstVisibleParent(page);expect(anchor).not.toBeNull();
        const pinned=cards(page).first();const pinnedId=await pinned.getAttribute('data-prompt-id');await pinned.locator('button').first().evaluate(node=>node.focus({preventScroll:true}));
        await page.evaluate(()=>window.libraryObservation.release());await settleLibrary(page);await retainedParent(page,anchor);
        expect(await cards(page).count()).toBeLessThan(80);await expect(page.locator(`article[data-prompt-id="${pinnedId}"] button`).first()).toBeFocused();
        const after=await firstVisibleParent(page);release();await page.evaluate(async()=>{await Promise.all([...document.querySelectorAll('.prompt-card img')].map(image=>image.decode().catch(()=>{})));});await settleLibrary(page);await retainedParent(page,after);
        {const response=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.has('cursor');});await more.evaluate(node=>node.click());await response;await expect(more).toHaveAttribute('aria-disabled','false');}await settleLibrary(page);await retainedParent(page,after);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] button`).first()).toBeFocused();
        await page.setViewportSize({width:390,height:844});await settleLibrary(page);await retainedParent(page,after);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] button`).first()).toBeFocused();
        expect(await cards(page).count()).toBeLessThan(80);
        const ids=await cards(page).evaluateAll(nodes=>nodes.map(node=>node.dataset.promptId));expect(new Set(ids).size).toBe(ids.length);
        for(const changedWidth of [false,true]) {
            const before=await firstVisibleParent(page);
            await page.locator(`article[data-prompt-id="${before.id}"]`).getByRole('button',{name:'Explore history',exact:true}).evaluate(node=>node.click());await expect(history(page)).toBeVisible();
            if(changedWidth)await page.setViewportSize({width:1600,height:1100});
            await page.getByRole('button',{name:'← Prompt library',exact:true}).click();await settleLibrary(page);await retainedParent(page,before);
            expect(await cards(page).count()).toBeLessThan(80);
        }
        expect((await page.evaluate(()=>window.libraryObservation.stats())).unmounted).toBe(0);
    } finally {release();}
});

test('queued row growth preserves newer native wheel movement and correction events',async({page,request},testInfo)=>{
    await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}});
    await observeLibrary(page);await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    await page.setViewportSize({width:1600,height:1100});await page.goto(route);
    const more=page.getByRole('button',{name:'Load more prompts',exact:true});
    await loadShelfPages(page,more,3);
    await page.evaluate(()=>scrollTo(0,document.querySelector('.library-grid').getBoundingClientRect().top+scrollY+20*486+25));
    await settleLibrary(page);await page.mouse.move(1100,550);
    const pinned=cards(page).first(),pinnedId=await pinned.getAttribute('data-prompt-id');await pinned.locator('button').first().evaluate(node=>node.focus({preventScroll:true}));
    try {
        // The same queued-frame wheel sequence is stable without changed measurements.
        const control=await firstVisibleParent(page),controlY=await page.evaluate(()=>scrollY);
        await page.evaluate(()=>window.libraryObservation.holdFrames());await page.mouse.wheel(0,160);
        await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(controlY+160);
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().heldFrames)).toBeGreaterThan(0);
        await page.evaluate(()=>window.libraryObservation.releaseFrames());await settleLibrary(page);
        expect(await page.evaluate(()=>scrollY)).toBe(controlY+160);await retainedParent(page,{...control,offset:control.offset-160});

        const before=await firstVisibleParent(page),beforeY=await page.evaluate(()=>scrollY);
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.card-content'))content.style.paddingBlockEnd='45px';});
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().measurements)).toBeGreaterThan(0);
        const parent=page.locator(`article[data-prompt-id="${before.id}"]`);
        const growth=await parent.evaluate(node=>node.getBoundingClientRect().top)-before.offset;
        expect(growth).toBeGreaterThan(0);expect(await page.evaluate(()=>scrollY)).toBe(beforeY);
        await page.evaluate(()=>{window.libraryObservation.holdFrames();window.libraryObservation.release();});
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().heldFrames)).toBeGreaterThan(0);
        await page.mouse.wheel(0,160);await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(beforeY+160);
        expect(await page.evaluate(()=>document.documentElement.scrollHeight-innerHeight-scrollY)).toBeGreaterThan(1000);
        await page.evaluate(()=>window.libraryObservation.releaseFrames());await settleLibrary(page);
        await testInfo.attach('queued-growth-wheel-geometry',{body:JSON.stringify({beforeY,offset:before.offset,growth,wheel:160,finalY:await page.evaluate(()=>scrollY),finalOffset:await parent.evaluate(node=>node.getBoundingClientRect().top)}),contentType:'application/json'});
        await retainedParent(page,{...before,offset:before.offset-160});
        expect(Math.abs(await page.evaluate(()=>scrollY)-(beforeY+160+growth))).toBeLessThanOrEqual(2);

        // The correction's own native scroll event must not become new user motion.
        const corrected=await firstVisibleParent(page);
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.card-content'))content.style.paddingBlockEnd='65px';});
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().measurements)).toBeGreaterThan(0);
        await page.evaluate(()=>window.libraryObservation.release());await settleLibrary(page);await retainedParent(page,corrected);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] button`).first()).toBeFocused();
        const ids=await cards(page).evaluateAll(nodes=>nodes.map(node=>node.dataset.promptId));expect(ids.length).toBeLessThan(80);expect(new Set(ids).size).toBe(ids.length);
        await page.evaluate(()=>window.libraryObservation.holdFrames());await page.mouse.wheel(0,160);
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().heldFrames)).toBeGreaterThan(0);
        await page.evaluate(()=>{location.hash='#/overview';});await expect(cards(page)).toHaveCount(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).targets).toBe(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).pending).toBe(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).heldFrames).toBe(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).unmounted).toBe(0);
    } finally {await page.evaluate(()=>{window.libraryObservation.release();window.libraryObservation.releaseFrames();});}
});

test('unchanged row observations settle without resize feedback',async({page,request,browserName})=>{
    await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}});
    await observeLibrary(page);await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    let session,breakpoint;
    try {
        if(browserName==='chromium') {
            session=await page.context().newCDPSession(page);await session.send('Debugger.enable');
            await page.addInitScript(()=>{window.libraryLayoutCalls=0;});
            const scripts=[];session.on('Debugger.scriptParsed',script=>scripts.push(script));
            await page.goto(route);await expect(cards(page).first()).toBeVisible();await settleLibrary(page);
            const script=scripts.find(script=>script.url.endsWith('/workspace/library-layout.mjs'));expect(script).toBeTruthy();
            const {scriptSource}=await session.send('Debugger.getScriptSource',{scriptId:script.scriptId});
            const lines=scriptSource.split('\n');const start=lines.findIndex(line=>line.includes('export function layoutLibraryRows('));expect(start).toBeGreaterThanOrEqual(0);
            const possible=await session.send('Debugger.getPossibleBreakpoints',{start:{scriptId:script.scriptId,lineNumber:start},end:{scriptId:script.scriptId,lineNumber:start+3},restrictToFunction:false});
            const location=possible.locations.find(location=>location.lineNumber>start);expect(location).toBeTruthy();
            breakpoint=(await session.send('Debugger.setBreakpoint',{location,condition:'(window.libraryLayoutCalls++, false)'})).breakpointId;
        } else {await page.goto(route);await expect(cards(page).first()).toBeVisible();await settleLibrary(page);}
        const more=page.getByRole('button',{name:'Load more prompts',exact:true});
        await loadShelfPages(page,more,3);await settleLibrary(page);
        if(session)expect(await page.evaluate(()=>window.libraryLayoutCalls)).toBeGreaterThan(0);
        const before=await page.evaluate(()=>({stats:window.libraryObservation.stats(),calls:window.libraryLayoutCalls}));
        await page.evaluate(()=>{for(let i=0;i<12;i++){window.libraryObservation.equal();dispatchEvent(new Event('scroll'));}});await settleLibrary(page);
        const after=await page.evaluate(()=>({stats:window.libraryObservation.stats(),calls:window.libraryLayoutCalls}));
        expect(after.stats.requested-before.stats.requested).toBeLessThanOrEqual(1);expect(after.stats.mutations).toBe(before.stats.mutations);if(session)expect(after.calls).toBe(before.calls);
        for(let i=0;i<3;i++) {await page.evaluate(()=>scrollBy(0,1));await settleLibrary(page);}
        if(session)expect(await page.evaluate(()=>window.libraryLayoutCalls)).toBe(after.calls);
        const equalBefore=await page.evaluate(()=>window.libraryObservation.stats().requested);await page.evaluate(()=>{for(let i=0;i<12;i++)window.libraryObservation.equal();});await settleLibrary(page);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).requested).toBe(equalBefore);
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.card-content'))content.style.paddingBlockEnd='35px';});await settleLibrary(page);
        const calls=await page.evaluate(()=>window.libraryLayoutCalls);await page.evaluate(()=>window.libraryObservation.release());await settleLibrary(page);
        if(session)expect(await page.evaluate(()=>window.libraryLayoutCalls)-calls).toBe(1);
        await page.evaluate(()=>{dispatchEvent(new Event('scroll'));location.hash='#/overview';});await expect(cards(page)).toHaveCount(0);await settleLibrary(page);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).targets).toBe(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).pending).toBe(0);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).unmounted).toBe(0);
    } finally {if(breakpoint)await session.send('Debugger.removeBreakpoint',{breakpointId:breakpoint});if(session)await session.detach();}
});

test('saved choices retain curved staggered lanes and the full negative immediately follows its positive',async({page,request})=>{
    await page.goto(route);await openHistory(page);await selectVariant(page,1);
    const positive=saved(page).getByRole('region',{name:'Positive prompt',exact:true});
    expect(await positive.evaluate(node=>node.nextElementSibling?.getAttribute('aria-label'))).toBe('Negative prompt');
    const values=await page.locator('.library-variant-lane').evaluateAll(nodes=>nodes.map(node=>({offset:parseFloat(getComputedStyle(node).marginInlineStart),radius:getComputedStyle(node,'::before').borderBottomLeftRadius})));
    expect(values).toEqual([{offset:38,radius:'22px'},{offset:85,radius:'22px'},{offset:132,radius:'22px'},{offset:179,radius:'22px'}]);
    const order=await page.locator('.library-history').evaluate(node=>[...node.querySelectorAll('section[aria-label]')].filter(section=>['Saved negative variants','Recorded executions','Shared gallery'].includes(section.getAttribute('aria-label'))).map(section=>section.getAttribute('aria-label')));
    expect(order).toEqual(['Saved negative variants','Recorded executions','Shared gallery']);
    const rows=await(await request.get('/prompt_manager/library')).json();const cat=rows.items.find(item=>item.text==='Cat portrait');
    const details=await(await request.get(`/prompt_manager/library/${cat.id}`)).json();
    const selected=details.variants[1].negative_text;
    expect(await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text').evaluate(node=>node.textContent)).toBe(selected);
});

/** Rewrites each shelf item the server sends: run counts, last use and long texts are values the fixture never records. */
async function patchShelf(page,patch) {
    await page.route(url=>url.pathname==='/prompt_manager/library',async route=>{
        const response=await route.fetch();const body=await response.json();
        body.items=body.items.map(item=>({...item,...patch(item)}));
        await route.fulfill({response,json:body});
    });
}
/** The footer line as seen: everything but its visually hidden rating sentence. */
const shownText=node=>[...node.childNodes].filter(child=>!child.classList?.contains('ca-visually-hidden')).map(child=>child.textContent).join('');
const longPrompt=[`${'A luminous alpine observatory with layered glass terraces '.repeat(6)}`,'second paragraph keeps the full text',`final line ${'detail '.repeat(30)}end of prompt`].join('\n');

test('card matches the concept and never wraps the category mid-word',async({page,request})=>{
    const before=await(await request.get('/prompt_manager/library')).json();
    const dog=before.items.find(item=>item.text==='Dog landscape'),cat=before.items.find(item=>item.text==='Cat portrait');
    const dual=before.items.find(item=>item.text==='Dual role portrait');
    await expect(await request.post('/prompt_manager/bulk/category',{data:{prompt_ids:[dog.id],category:'asian'}})).toBeOK();
    await expect(await request.post('/prompt_manager/bulk/category',{data:{prompt_ids:[dual.id],category:''}})).toBeOK();
    // A long saved title fills the title row, so only the chip's own min-content keeps "Uncategorized" and "asian" whole.
    const wide='A remarkably long saved positive prompt title that fills the whole card title row';
    await patchShelf(page,item=>item.id===cat.id?{run_count:4,last_used_at:new Date().toISOString(),rating:5}:item.id===dual.id||item.id===dog.id?{title:wide,run_count:1,last_used_at:null,rating:null}:{});
    for(const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});await page.goto(route);await expect(cards(page)).toHaveCount(3);
        const card=page.locator(`article[data-prompt-id="${cat.id}"]`);
        // app.js renderLibrary a586: article.prompt-card > .card-art, .card-content, .card-footer.
        await expect(card).toHaveClass('prompt-card');
        expect(await card.evaluate(node=>[...node.children].map(child=>child.className))).toEqual(['card-art','card-content','card-footer']);
        expect(await card.locator('.card-art').evaluate(node=>[...node.children].map(child=>child.tagName))).toEqual(['IMG','IMG']);
        expect(await card.locator('.card-content').evaluate(node=>[...node.children].map(child=>child.className))).toEqual(['card-title','prompt-inspector','card-meta','tags']);
        expect(await card.locator('.card-title').evaluate(node=>[...node.children].map(child=>`${child.tagName}.${child.className}`))).toEqual(['BUTTON.','SPAN.chip']);
        await expect(card.locator('.card-title > button')).toHaveText('Cat portrait');await expect(card.locator('.card-title > .chip')).toHaveText('Animals');
        await expect(card.locator('.card-title > button')).toHaveCSS('font-size','16px');await expect(card.locator('.card-title > button')).toHaveCSS('font-weight','600');
        const positive=card.locator('.card-content > .prompt-inspector > section.prompt-section.positive');await expect(positive).toHaveCount(1);
        await expect(card.locator('section.prompt-section.negative')).toHaveCount(0);
        await expect(positive.getByRole('button',{name:'Copy positive prompt: Cat portrait',exact:true})).toHaveCount(1);
        await expect(positive.getByRole('button',{name:'Send positive prompt to ComfyUI: Cat portrait',exact:true})).toHaveCount(1);
        await expect(card.locator('.card-meta > *')).toHaveText(['− 4 negative variants','4 runs','3 images']);
        await expect(card.locator('.card-meta > .chip.red')).toHaveCount(1);
        await expect(card.locator('.tags > .chip')).toHaveText(['cats','studio']);
        const footer=card.locator('.card-footer');
        expect(await footer.evaluate(node=>[...node.children].map(child=>`${child.tagName}.${child.className}`))).toEqual(['SPAN.','DIV.row-actions']);
        await expect(footer.locator('> span > [aria-hidden="true"]')).toHaveText('★★★★★');
        await expect(footer.locator('> span > .ca-visually-hidden')).toHaveText('Rated 5 out of 5');
        expect(await footer.locator('> span').evaluate(shownText)).toBe('★★★★★ · Used today');
        const actions=footer.locator('.row-actions > button');await expect(actions).toHaveCount(2);
        await expect(actions.first()).toHaveClass('send icon-btn');await expect(actions.first()).toHaveAccessibleName('Send positive prompt to ComfyUI: Cat portrait');
        await expect(actions.nth(1)).toHaveAccessibleName('Explore history');await expect(actions.nth(1).locator('svg')).toHaveCount(1);
        // Unrated and never used: the used label alone; no negatives still reads as a count.
        const unrated=page.locator(`article[data-prompt-id="${dual.id}"]`);
        expect(await unrated.locator('.card-footer > span').evaluate(shownText)).toBe('Not used yet');
        await expect(unrated.locator('.card-footer > span > [aria-hidden="true"]')).toHaveCount(0);
        await expect(unrated.locator('.card-meta > *')).toHaveText([`− ${dual.variant_count} negative variant${dual.variant_count===1?'':'s'}`,'1 run',`${dual.image_count} image${dual.image_count===1?'':'s'}`]);
        for(const [id,text] of [[dual.id,'Uncategorized'],[dog.id,'asian']]) {
            const chip=page.locator(`article[data-prompt-id="${id}"] .card-title > .chip`);await expect(chip).toHaveText(text);
            expect(await chip.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);return range.getClientRects().length;}),`${text} at ${width}`).toBe(1);
            expect(await page.locator(`article[data-prompt-id="${id}"] .card-title`).evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
        }
        expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    }
});

test('long prompt is clamped to two lines while copy sends the full text',async({page,context,request})=>{
    const before=await(await request.get('/prompt_manager/library')).json();const cat=before.items.find(item=>item.text==='Cat portrait');
    await page.addInitScript(()=>{window.copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied.push(text);}}});});
    await patchShelf(page,item=>item.id===cat.id?{text:longPrompt,title:'Long observatory'}:{});
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        const target={id:'positive-1',label:'Card destination',type:'PromptManager',roles:['positive']};
        window.cardApplied=[];
        window.cardHost=createBridgeHost({origin:location.origin,namespace:'/',label:'Card ComfyUI',adapter:{
            listTargets:()=>[target],preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:`Previous ${role}`,after})),composition:{prepend:'',append:'',prependConnected:false,appendConnected:false,before:'Previous positive',after:roles.positive}},snapshot:roles}),
            apply:roles=>{window.cardApplied.push(roles);return{targetId:target.id,values:roles};},
        }});
    });
    try {
        for(const [width,height] of [[1600,1100],[390,844]]) {
            await page.setViewportSize({width,height});await page.goto(route);
            const card=page.locator(`article[data-prompt-id="${cat.id}"]`),text=card.locator('.prompt-text');
            await expect(text).toHaveText(longPrompt);
            const box=await text.evaluate(node=>{const style=getComputedStyle(node);return {lines:Math.round(node.clientHeight/parseFloat(style.lineHeight)),clipped:node.scrollHeight>node.clientHeight+1,clamp:style.webkitLineClamp,overflow:style.overflowY};});
            expect(box).toEqual({lines:2,clipped:true,clamp:'2',overflow:'hidden'});
        }
        const card=page.locator(`article[data-prompt-id="${cat.id}"]`);
        await card.getByRole('button',{name:'Copy positive prompt: Long observatory',exact:true}).click();
        await expect.poll(()=>page.evaluate(()=>window.copied)).toEqual([longPrompt]);
        await card.locator('.card-footer .row-actions > button.send').click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'Card ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Card destination'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.cardApplied)).toEqual([{positive:longPrompt}]);
        await sender.getByRole('button',{name:'Close',exact:true}).click();
    } finally {await host.evaluate(()=>window.cardHost.dispose());await host.close();}
});

test('title and Explore history are buttons that open history',async({page,request})=>{
    const rows=await(await request.get('/prompt_manager/library')).json();const cat=rows.items.find(item=>item.text==='Cat portrait');
    await page.goto(route);const card=page.locator(`article[data-prompt-id="${cat.id}"]`);
    await expect(card.locator('a')).toHaveCount(0);
    const title=card.locator('.card-title > button');await expect(title).toHaveAttribute('type','button');
    await title.click();await expect(page).toHaveURL(new RegExp(`#/library/${cat.id}$`));await expect(history(page)).toBeVisible();
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();await expect(cards(page)).toHaveCount(3);
    const explore=page.locator(`article[data-prompt-id="${cat.id}"]`).getByRole('button',{name:'Explore history',exact:true});
    await explore.focus();await page.keyboard.press('Enter');await expect(page).toHaveURL(new RegExp(`#/library/${cat.id}$`));await expect(history(page)).toBeVisible();
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    // The card's navigation buttons keep the shelf's Home/End and arrow movement between cards.
    const first=cards(page).first();await first.locator('.card-title > button').focus();await page.keyboard.press('End');
    await expect(cards(page).last().locator('.card-title > button')).toBeFocused();
    await page.keyboard.press('ArrowLeft');await expect(cards(page).nth(1).locator('.card-title > button')).toBeFocused();
});

test('virtualized rows keep measured heights with the clamp',async({page,request,isMobile})=>{
    await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}});
    await observeLibrary(page);await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    await patchShelf(page,item=>({text:`${item.text}\n${longPrompt}`}));
    // Two columns on a desktop window; the touch project's phone gives the one-column shelf.
    {
        if(!isMobile)await page.setViewportSize({width:1600,height:1100});await page.goto(route);
        const more=page.getByRole('button',{name:'Load more prompts',exact:true});
        await loadShelfPages(page,more,3);
        await page.evaluate(()=>scrollTo(0,document.querySelector('.library-grid').getBoundingClientRect().top+scrollY+3000));await settleLibrary(page);
        const geometry=await page.evaluate(()=>[...document.querySelector('.library-grid').children].map(node=>{
            const box=node.getBoundingClientRect();
            return {row:node.classList.contains('library-row'),top:box.top,height:box.height,
                cards:[...node.querySelectorAll('.prompt-card')].map(card=>({height:card.getBoundingClientRect().height,text:card.querySelector('.prompt-text').clientHeight,clipped:card.querySelector('.prompt-text').scrollHeight>card.querySelector('.prompt-text').clientHeight+1}))};
        }));
        const rows=geometry.filter(item=>item.row);expect(rows.length).toBeGreaterThan(2);expect(rows.length).toBeLessThan(60);
        for(const row of rows) {
            expect(row.cards.length).toBeGreaterThan(0);
            for(const card of row.cards) {expect(card.text).toBe(42);expect(card.clipped).toBe(true);expect(Math.abs(card.height-row.height)).toBeLessThanOrEqual(1);}
        }
        // Consecutive mounted rows sit one concept gap apart: every height the layout used is the measured one.
        for(let index=1;index<geometry.length;index++) if(geometry[index].row&&geometry[index-1].row) expect(Math.abs(geometry[index].top-(geometry[index-1].top+geometry[index-1].height+18))).toBeLessThanOrEqual(1);
        const anchor=await firstVisibleParent(page);await page.evaluate(()=>window.libraryObservation.equal());await settleLibrary(page);await retainedParent(page,anchor);
    }
});
