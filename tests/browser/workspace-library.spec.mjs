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

test('refresh atomically discards old row geometry and renders changed parent data',async({page,request})=>{
    await page.goto(route);await expect(cards(page)).toHaveCount(3);
    const card=cards(page).filter({has:page.getByText('Dog landscape',{exact:true})});const id=await card.getAttribute('data-prompt-id');
    await expect(await request.put(`/prompt_manager/prompts/${id}`,{data:{text:'Revised dog landscape'}})).toBeOK();
    await expect(await request.put(`/prompt_manager/prompts/${id}/rating`,{data:{rating:5}})).toBeOK();
    await page.evaluate(()=>{
        const native=requestAnimationFrame;const held=[];let paused=true;window.releaseLibraryFrames=()=>{paused=false;window.requestAnimationFrame=native;for(const callback of held)callback();};
        window.requestAnimationFrame=callback=>native(time=>{if(paused)held.push(()=>callback(time));else callback(time);});
    });
    try {
        await page.getByRole('button',{name:'Refresh library',exact:true}).click();
        await expect(page.locator('.library-status')).not.toHaveText('Loading prompt library…');
    } finally {await page.evaluate(()=>window.releaseLibraryFrames());}
    await expect(cards(page)).toHaveCount(3);
    const changed=page.locator(`article[data-prompt-id="${id}"]`);await expect(changed).toContainText('Revised dog landscape');
    await expect(changed.getByRole('img',{name:'Rated 5 out of 5',exact:true})).toHaveCount(1);
    await expect(page.getByRole('button',{name:'Retry prompt library',exact:true})).toBeHidden();
});

test('returning from history restores library context and mobile content stays inside the viewport',async({page})=>{
    await page.setViewportSize({width:390,height:844});await page.goto(route);
    await page.getByRole('combobox',{name:'Category',exact:true}).selectOption('Animals');
    await page.getByRole('searchbox',{name:'Search prompts',exact:true}).fill('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await openHistory(page);await selectVariant(page,0);
    await expect(saved(page).getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Cat portrait');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page.getByRole('searchbox',{name:'Search prompts',exact:true})).toHaveValue('Cat portrait');await expect(cards(page)).toHaveCount(1);
    await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(history(page)).toHaveCount(0);await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('empty shelf visits restore saved scroll without inventing a parent anchor',async({page})=>{
    await page.setViewportSize({width:1600,height:300});await page.goto(route);
    await page.getByRole('searchbox',{name:'Search prompts',exact:true}).fill('No matching saved positive');
    await expect(cards(page)).toHaveCount(0);await expect(page.locator('.library-status')).toContainText('No matching prompts.');
    await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
    await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(0);
    const position=await page.evaluate(()=>scrollY);
    await page.getByRole('navigation', { name: 'Tools and system', exact: true }).getByRole('link', { name: 'Overview', exact: true }).evaluate(node=>node.click());
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name: /^Prompt library(?:\s*[\d,]+)?$/}).evaluate(node=>node.click());
    await expect(page.getByRole('searchbox',{name:'Search prompts',exact:true})).toHaveValue('No matching saved positive');
    await expect(cards(page)).toHaveCount(0);
    await expect.poll(()=>page.evaluate(expected=>Math.abs(scrollY-expected),position)).toBeLessThanOrEqual(1);
    const search=page.getByRole('searchbox',{name:'Search prompts',exact:true});await search.focus();await search.fill('Dog landscape');
    await expect(cards(page)).toHaveCount(1);await expect(search).toBeFocused();
    await page.evaluate(()=>scrollTo(0,0));await page.getByRole('button',{name:'Refresh library',exact:true}).click();await expect(cards(page)).toHaveCount(1);
    expect(await page.evaluate(()=>scrollY)).toBe(0);
    const anchor=await firstVisibleParent(page);expect(anchor).not.toBeNull();
    await cards(page).getByRole('link',{name:'Explore history',exact:true}).evaluate(node=>node.click());await expect(history(page)).toBeVisible();
    await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await retainedParent(page,anchor);await expect(page.getByRole('searchbox',{name:'Search prompts',exact:true})).toHaveValue('Dog landscape');
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
                        if(entry.target.matches('.library-card')) {
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
        new MutationObserver(entries=>{mutations+=entries.filter(entry=>entry.target.matches('.library-card,.library-row,.library-row-spacer')).length;}).observe(document,{subtree:true,attributes:true,attributeFilter:['style']});
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

test('Library natural rows retain long prompt content without overflow',async({page,request})=>{
    await request.post('/__test__/reset',{data:{workspace_visual:true}});
    const data=await(await request.get('/prompt_manager/library')).json();
    for(const [width,height] of [[1600,1100],[390,844]]) {
        await page.setViewportSize({width,height});await page.goto(route);await expect(cards(page)).toHaveCount(6);
        expect(await cards(page).filter({hasText:'A study in very long creative context'}).evaluate(node=>node.getBoundingClientRect().height)).toBeGreaterThan(560);
        await expect(page.locator('#workspace-main > .heading p.muted')).toContainText('One positive.');
        await expect(page.locator('#workspace-main p').filter({hasText:'One positive. Reusable negatives.'})).toHaveCount(1);
        for(const theme of ['dark','light']) {
            await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
            await expect(page.locator('.prompt-section.positive .role-label').first()).toHaveCSS('color',theme==='dark'?'rgb(115, 221, 169)':'rgb(23, 107, 70)');
        }
        await expect(page.locator('.library-card-art').first()).toHaveCSS('height',width===1600?'170px':'150px');
        await expect(page.locator('.library-card-content').first()).toHaveCSS('padding',width===1600?'18px':'15px');
        await expect(page.locator('.library-grid')).toHaveCSS('gap','18px');
        const long=cards(page).filter({hasText:'A study in very long creative context'});
        const prompt=long.locator('.prompt-text');expect((await prompt.textContent()).length).toBeGreaterThan(1500);
        expect(await long.evaluate(node=>node.getBoundingClientRect().height)).toBeGreaterThan(560);
        for(const selector of ['.library-card-title','.library-card-prompts','.prompt-text','.library-card-content > .library-tags']) {
            expect(await long.locator(selector).evaluate(node=>node.scrollHeight<=node.clientHeight+1)).toBe(true);
        }
        await prompt.focus();await expect(prompt).toBeFocused();
        await long.getByRole('link',{name:'Explore history',exact:true}).scrollIntoViewIfNeeded();
        await expect(long.getByRole('link',{name:'Explore history',exact:true})).toBeVisible();
        for(const item of data.items) {
            const card=page.locator(`article[data-prompt-id="${item.id}"]`);
            await expect(card.getByRole('img',{name:item.rating?`Rated ${item.rating} out of 5`:'Unrated',exact:true})).toHaveCount(1);
            await expect(card.locator('.library-card-content > .library-tags .library-chip')).toHaveText(item.tags);
        }
        expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
        await expect(page.locator('.library-shelf')).not.toContainText(/\b\d+ (?:tracked )?runs\b|Used today/i);
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
        for(const count of [40,80,120]) {await expect(page.locator('.library-caption')).toContainText(`${count} of 203`);await more.evaluate(node=>node.click());}
        await expect(page.locator('.library-caption')).toContainText('160 of 203');
        await page.evaluate(()=>scrollTo(0,document.querySelector('.library-grid').getBoundingClientRect().top+scrollY+40*486+25));
        await settleLibrary(page);const anchor=await firstVisibleParent(page);expect(anchor).not.toBeNull();
        const pinned=cards(page).first();const pinnedId=await pinned.getAttribute('data-prompt-id');await pinned.locator('a').first().evaluate(node=>node.focus({preventScroll:true}));
        await page.evaluate(()=>window.libraryObservation.release());await settleLibrary(page);await retainedParent(page,anchor);
        expect(await cards(page).count()).toBeLessThan(80);await expect(page.locator(`article[data-prompt-id="${pinnedId}"] a`).first()).toBeFocused();
        const after=await firstVisibleParent(page);release();await page.evaluate(async()=>{await Promise.all([...document.querySelectorAll('.library-card img')].map(image=>image.decode().catch(()=>{})));});await settleLibrary(page);await retainedParent(page,after);
        await more.evaluate(node=>node.click());await expect(page.locator('.library-caption')).toContainText('200 of 203');await settleLibrary(page);await retainedParent(page,after);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] a`).first()).toBeFocused();
        await page.setViewportSize({width:390,height:844});await settleLibrary(page);await retainedParent(page,after);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] a`).first()).toBeFocused();
        expect(await cards(page).count()).toBeLessThan(80);
        const ids=await cards(page).evaluateAll(nodes=>nodes.map(node=>node.dataset.promptId));expect(new Set(ids).size).toBe(ids.length);
        for(const changedWidth of [false,true]) {
            const before=await firstVisibleParent(page);
            await page.locator(`article[data-prompt-id="${before.id}"] .library-history-link`).evaluate(node=>node.click());await expect(history(page)).toBeVisible();
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
    for(const count of [40,80,120]) {await expect(page.locator('.library-caption')).toContainText(`${count} of 203`);await more.evaluate(node=>node.click());}
    await expect(page.locator('.library-caption')).toContainText('160 of 203');
    await page.evaluate(()=>scrollTo(0,document.querySelector('.library-grid').getBoundingClientRect().top+scrollY+20*486+25));
    await settleLibrary(page);await page.mouse.move(1100,550);
    const pinned=cards(page).first(),pinnedId=await pinned.getAttribute('data-prompt-id');await pinned.locator('a').first().evaluate(node=>node.focus({preventScroll:true}));
    try {
        // The same queued-frame wheel sequence is stable without changed measurements.
        const control=await firstVisibleParent(page),controlY=await page.evaluate(()=>scrollY);
        await page.evaluate(()=>window.libraryObservation.holdFrames());await page.mouse.wheel(0,160);
        await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(controlY+160);
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().heldFrames)).toBeGreaterThan(0);
        await page.evaluate(()=>window.libraryObservation.releaseFrames());await settleLibrary(page);
        expect(await page.evaluate(()=>scrollY)).toBe(controlY+160);await retainedParent(page,{...control,offset:control.offset-160});

        const before=await firstVisibleParent(page),beforeY=await page.evaluate(()=>scrollY);
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.library-card-content'))content.style.paddingBlockEnd='45px';});
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
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.library-card-content'))content.style.paddingBlockEnd='65px';});
        await expect.poll(()=>page.evaluate(()=>window.libraryObservation.stats().measurements)).toBeGreaterThan(0);
        await page.evaluate(()=>window.libraryObservation.release());await settleLibrary(page);await retainedParent(page,corrected);
        await expect(page.locator(`article[data-prompt-id="${pinnedId}"] a`).first()).toBeFocused();
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
        for(const count of [40,80,120]) {await expect(page.locator('.library-caption')).toContainText(`${count} of 203`);await more.evaluate(node=>node.click());}
        await expect(page.locator('.library-caption')).toContainText('160 of 203');await settleLibrary(page);
        if(session)expect(await page.evaluate(()=>window.libraryLayoutCalls)).toBeGreaterThan(0);
        const before=await page.evaluate(()=>({stats:window.libraryObservation.stats(),calls:window.libraryLayoutCalls}));
        await page.evaluate(()=>{for(let i=0;i<12;i++){window.libraryObservation.equal();dispatchEvent(new Event('scroll'));}});await settleLibrary(page);
        const after=await page.evaluate(()=>({stats:window.libraryObservation.stats(),calls:window.libraryLayoutCalls}));
        expect(after.stats.requested-before.stats.requested).toBeLessThanOrEqual(1);expect(after.stats.mutations).toBe(before.stats.mutations);if(session)expect(after.calls).toBe(before.calls);
        for(let i=0;i<3;i++) {await page.evaluate(()=>scrollBy(0,1));await settleLibrary(page);}
        if(session)expect(await page.evaluate(()=>window.libraryLayoutCalls)).toBe(after.calls);
        const equalBefore=await page.evaluate(()=>window.libraryObservation.stats().requested);await page.evaluate(()=>{for(let i=0;i<12;i++)window.libraryObservation.equal();});await settleLibrary(page);
        expect((await page.evaluate(()=>window.libraryObservation.stats())).requested).toBe(equalBefore);
        await page.evaluate(()=>{window.libraryObservation.hold();for(const content of document.querySelectorAll('.library-card-content'))content.style.paddingBlockEnd='35px';});await settleLibrary(page);
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
