import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const workspace='/prompt_manager/workspace#/images';
const details=page=>page.getByRole('region',{name:'Image details',exact:true});
const role=(scope,name)=>scope.getByRole('region',{name:`${name} prompt`,exact:true});
const select=async(page,key)=>page.locator(`article[data-image-key="${key}"]`).getByRole('button',{name:/^Open /}).click();
const dialog=page=>page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{prompt_inspector:true}})).toBeOK();
    page.promptErrors=[];page.on('pageerror',error=>page.promptErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.promptErrors).toEqual([]);});

async function fakeHost(context,page,{rejectApply=false}={}) {
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async rejectApply=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        window.hostValues={positive:'Existing positive',negative:'Existing negative'};window.applied=[];
        const target={id:'pair-1',label:'My prompt pair',type:'PromptManagerPair',roles:['positive','negative']};
        window.fixtureHost=createBridgeHost({origin:location.origin,namespace:'/',label:'Test ComfyUI',adapter:{
            listTargets:()=>[target],
            preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:window.hostValues[role],after})),composition:{prepend:'trigger',append:'detail',prependConnected:false,appendConnected:false,before:`trigger ${window.hostValues.positive} detail`,after:`trigger ${roles.positive??window.hostValues.positive} detail`}},snapshot:roles}),
            apply:roles=>{if(rejectApply)throw new Error('Workflow changed. Preview again.');Object.assign(window.hostValues,roles);window.applied.push(roles);return{targetId:target.id,values:roles};},
        }});
    },rejectApply);
    return host;
}
async function chooseDestination(page) {
    const sender=dialog(page);await expect(sender).toBeVisible();
    const host=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});
    await expect(host.locator('option')).toContainText(['Test ComfyUI']);
    await host.selectOption({label:'Test ComfyUI'});
    await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'My prompt pair'});
    return sender;
}

test('collection viewer rejects stale source and sends only refreshed API evidence',async({page,request,context})=>{
    const host=await fakeHost(context,page);
    await page.addInitScript(()=>{window.copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copied.push(text)}});});
    try {
        const loaded=await(await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();const stale=loaded.images.find(image=>image.root_index===0&&image.path==='shared/same.png');
        await page.goto(workspace);await select(page,'1:shared/same.png');await expect(details(page)).toContainText('Dog landscape');
        await expect(await request.post('/prompt_manager/settings',{data:{gallery_root_paths:['input','output']}})).toBeOK();
        const response=page.waitForResponse(result=>result.url().includes('/images/inspect/shared/same.png?root=0'));
        await select(page,'0:shared/same.png');const changed=await response;expect(changed.status()).toBe(409);expect((await changed.json()).code).toBe('source-changed');
        await page.locator('article[data-image-key="0:shared/same.png"] .image-card-footer button').click();const preview=page.getByRole('dialog',{name:'Image preview',exact:true});
        const message='The source changed. Refresh the image collection before inspecting this file.';
        await expect(preview).toContainText(message);await expect(preview.locator('[data-copy-role],[data-send-role],.file-inspector a[download]')).toHaveCount(0);
        const original=preview.getByRole('group',{name:'Media controls',exact:true}).getByLabel('Download original',{exact:true});
        await expect(original).toHaveAttribute('aria-disabled','true');await expect(original).not.toHaveAttribute('href',/./);
        let downloads=0;page.on('download',()=>downloads++);await original.focus();await page.keyboard.press('Enter');expect(downloads).toBe(0);
        await expect(preview.getByRole('button',{name:'Retry media',exact:true})).toBeHidden();
        await expect(preview.getByRole('button',{name:'Retry prompt details',exact:true})).toHaveCount(0);await expect(preview).not.toContainText('Dog landscape');await expect(preview).not.toContainText('Cat portrait');
        expect(await page.evaluate(()=>window.copied)).toEqual([]);expect(await host.evaluate(()=>window.applied)).toEqual([]);
        const refreshed=page.waitForResponse(result=>result.url().includes('/images/output?'));
        await preview.getByRole('button',{name:'Refresh image collection',exact:true}).click();await refreshed;await expect(preview).toHaveCount(0);
        const collection=await(await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();const fresh=collection.images.find(image=>image.root_index===0&&image.path===stale.path);expect(fresh.root_scope).not.toBe(stale.root_scope);
        const evidence=await(await request.get(`/prompt_manager/images/inspect/shared/same.png?root=0&root_scope=${fresh.root_scope}`)).json();
        await select(page,'0:shared/same.png');await details(page).getByRole('button',{name:'Open full preview',exact:true}).click();await expect(preview).toContainText(evidence.prompts.positive.text);
        await preview.getByRole('button',{name:'Send prompt pair',exact:true}).click();const sender=await chooseDestination(page);await sender.getByRole('button',{name:'Preview changes',exact:true}).click();await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.applied)).toEqual([{positive:evidence.prompts.positive.text,negative:evidence.prompts.negative.text}]);expect(await page.evaluate(()=>window.copied)).toEqual([]);
    } finally {await expect(await request.post('/prompt_manager/settings',{data:{gallery_root_paths:['output','input']}})).toBeOK();await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

test('physical image inspector resolves positive and negative metadata separately for duplicate filenames',async({page})=>{
    await page.goto(workspace);await select(page,'0:shared/same.png');
    await expect(role(details(page),'Positive')).toContainText('Cat portrait');await expect(role(details(page),'Negative')).toContainText('blurry, watermark');
    await expect(details(page).getByRole('button',{name:'Send prompt pair',exact:true})).toBeEnabled();
    await select(page,'1:shared/same.png');await expect(role(details(page),'Positive')).toContainText('Dog landscape');
    await expect(role(details(page),'Negative')).toContainText('low quality');await expect(details(page)).not.toContainText('Cat portrait');
});

test('explicit empty negative is sendable while unrecorded text is never invented',async({page})=>{
    await page.goto(workspace);await select(page,'0:empty-negative.png');
    await expect(role(details(page),'Negative')).toContainText('Empty negative prompt');
    await expect(details(page).getByRole('button',{name:'Send negative prompt',exact:true})).toBeEnabled();
    await select(page,'0:uncertain.png');await expect(details(page)).toContainText('Not recorded in this file.');
    await expect(details(page).getByRole('button',{name:/^Send (positive|negative) prompt$/})).toHaveCount(0);
});

test('inspection failures retry and a delayed previous selection cannot replace the current prompt',async({page})=>{
    let fail=true;let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/inspect/**',async route=>{
        const url=new URL(route.request().url());
        if(url.searchParams.get('root')==='0'&&url.pathname.endsWith('same.png')){
            if(fail){return route.fulfill({status:503,json:{success:false,error:'Temporarily unavailable'}});}
            await held;
        }
        await route.continue().catch(()=>{});
    });
    await page.goto(workspace);await select(page,'0:shared/same.png');
    await expect(details(page)).toContainText('Could not read prompts from this file.');
    fail=false;await details(page).getByRole('button',{name:'Retry prompt details',exact:true}).click();
    await select(page,'1:shared/same.png');await expect(role(details(page),'Positive')).toContainText('Dog landscape');
    release();await expect(role(details(page),'Positive')).toContainText('Dog landscape');
});

test('sender requires explicit destination, previews full changes and acknowledges only the applied pair',async({page,context},testInfo)=>{
    const host=await fakeHost(context,page);
    try {
        await page.goto(workspace);await select(page,'0:shared/same.png');await details(page).getByRole('button',{name:'Send prompt pair',exact:true}).click();
        const sender=dialog(page);await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeDisabled();
        const chosen=await chooseDestination(page);await chosen.getByRole('button',{name:'Preview changes',exact:true}).click();
        for(const text of ['Existing positive','Existing negative','Cat portrait','blurry, watermark','trigger Cat portrait detail'])await expect(chosen).toContainText(text);
        expect(await host.evaluate(()=>window.applied)).toEqual([]);
        await page.screenshot({path:testInfo.outputPath('sender-preview.png')});
        await chosen.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(chosen).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.applied)).toEqual([{positive:'Cat portrait',negative:'blurry, watermark'}]);
        await chosen.getByRole('button',{name:'Close',exact:true}).click();
        await page.evaluate(()=>scrollTo(0,0));
        await page.screenshot({path:testInfo.outputPath('inspector-dark.png'),fullPage:true});
        await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption('light');
        await page.screenshot({path:testInfo.outputPath('inspector-light.png'),fullPage:true});
        await details(page).getByRole('button',{name:'Open full preview',exact:true}).click();
        await expect(page.getByRole('dialog',{name:'Image preview',exact:true})).toBeVisible();
        await page.screenshot({path:testInfo.outputPath('viewer-prompts.png')});
        await page.getByRole('button',{name:'Close preview',exact:true}).click();
        await page.setViewportSize({width:390,height:844});await details(page).scrollIntoViewIfNeeded();
        await page.screenshot({path:testInfo.outputPath('inspector-mobile.png')});
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

// D6: ComfyUI ↗ is a link to the canvas; on Images with a selected file whose two roles resolved, a plain click opens
// the acknowledged pair send instead (concept app.js 1750-1757).
test('ComfyUI ↗ opens the pair send for a selected resolved image, else follows the link',async({page,context})=>{
    const host=await fakeHost(context,page);
    try {
        await page.goto('/prompt_manager/workspace#/overview');
        const link=page.locator('header.topbar').getByRole('link',{name:'ComfyUI',exact:true});
        await expect(link).toHaveAttribute('href','/');
        // Record whether the link's own handler let the navigation through, then stop it so the page stays.
        await page.evaluate(()=>{window.followed=[];document.addEventListener('click',event=>{if(!event.target.closest?.('.canvas-link'))return;window.followed.push(!event.defaultPrevented);event.preventDefault();});});
        await link.click();await expect(dialog(page)).toHaveCount(0);
        await page.locator('nav [data-route="images"]').click();await expect(page.locator('article[data-image-key]').first()).toBeVisible();
        await select(page,'0:uncertain.png');await expect(details(page)).toContainText('Not recorded in this file.');
        await link.click();await expect(dialog(page)).toHaveCount(0);
        expect(await page.evaluate(()=>window.followed)).toEqual([true,true]);
        await select(page,'0:shared/same.png');await expect(details(page).getByRole('button',{name:'Send prompt pair',exact:true})).toBeEnabled();
        await link.click();
        const sender=await chooseDestination(page);
        expect(await page.evaluate(()=>window.followed)).toEqual([true,true,false]);
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        for(const text of ['Cat portrait','blurry, watermark'])await expect(sender).toContainText(text);
        expect(await host.evaluate(()=>window.applied)).toEqual([]);
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.applied)).toEqual([{positive:'Cat portrait',negative:'blurry, watermark'}]);
        await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(dialog(page)).toHaveCount(0);await expect(link).toBeFocused();
        // Another page has no image selection, so the link is a plain link again.
        await page.locator('nav [data-route="library"]').click();await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
        await link.click();await expect(dialog(page)).toHaveCount(0);
        expect(await page.evaluate(()=>window.followed)).toEqual([true,true,false,true]);
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

test('host apply rejection retains the current text and requires a new preview',async({page,context})=>{
    const host=await fakeHost(context,page,{rejectApply:true});
    try {
        await page.goto(workspace);await select(page,'0:shared/same.png');await details(page).getByRole('button',{name:'Send positive prompt',exact:true}).click();
        const sender=await chooseDestination(page);await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText(/Workflow changed/);
        await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeDisabled();
        expect(await host.evaluate(()=>window.hostValues.positive)).toBe('Existing positive');
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

test('nested viewer sender Escape restores focus without closing the image and navigation disposes both overlays',async({page})=>{
    await page.goto(workspace);await select(page,'0:shared/same.png');
    await details(page).getByRole('button',{name:'Open full preview',exact:true}).click();
    const viewer=page.locator('dialog[open]').filter({has:page.locator('.viewer-canvas')});await expect(viewer).toBeVisible();
    const send=viewer.getByRole('button',{name:'Send positive prompt',exact:true});await send.click();await expect(dialog(page)).toBeVisible();
    await page.keyboard.press('Escape');await expect(dialog(page)).toBeHidden();await expect(viewer).toBeVisible();await expect(send).toBeFocused();
    await send.click();await page.evaluate(()=>{location.hash='#/overview';});await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('empty negative send clears only the negative destination and preserves its positive',async({page,context})=>{
    const host=await fakeHost(context,page);
    try {
        await page.goto(workspace);await select(page,'0:empty-negative.png');
        await details(page).getByRole('button',{name:'Send negative prompt',exact:true}).click();
        const sender=await chooseDestination(page);await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.hostValues)).toEqual({positive:'Existing positive',negative:''});
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

test('no open ComfyUI window is actionable and rediscovery finds a newly opened host',async({page,context})=>{
    await page.goto(workspace);await select(page,'0:shared/same.png');
    await details(page).getByRole('button',{name:'Send positive prompt',exact:true}).click();
    const sender=dialog(page);await expect(sender).toContainText('No compatible ComfyUI windows found.');
    const host=await fakeHost(context,page);
    try {
        await sender.getByRole('button',{name:'Find ComfyUI windows',exact:true}).click();
        await chooseDestination(page);await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.hostValues)).toEqual({positive:'Cat portrait',negative:'Existing negative'});
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
});

test('mobile inspector exposes full long prompts, truthful uncertainty and copy failure feedback',async({page})=>{
    const text=`A long detailed prompt ${'with readable full composition, '.repeat(150)}END OF FULL PROMPT`;
    await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('Permission denied');}}}));
    await page.route('**/images/inspect/**',route=>route.fulfill({json:{success:true,root_scope:new URL(route.request().url()).searchParams.get('root_scope'),image:{root_index:0,path:'shared/same.png',filename:'same.png'},prompts:{positive:{status:'resolved',text,source:'execution'},negative:{status:'ambiguous',text:null,source:null}}}}));
    await page.setViewportSize({width:390,height:844});await page.goto(workspace);await select(page,'0:shared/same.png');
    const positive=role(details(page),'Positive');await expect(positive.locator('pre')).toHaveText(text);
    await expect(role(details(page),'Negative')).toContainText('Multiple possible prompts. Source could not be determined.');
    await expect(details(page).getByRole('button',{name:'Send negative prompt',exact:true})).toHaveCount(0);
    await positive.getByRole('button',{name:'Copy positive prompt',exact:true}).click();await expect(positive).toContainText('Copy unavailable. Select the prompt text to copy it.');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await positive.getByRole('button',{name:'Send positive prompt',exact:true}).click();
    await expect(dialog(page)).toBeVisible();const bounds=await dialog(page).boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(391);
});

test('filtering away a selected image aborts its pending metadata request',async({page})=>{
    await page.addInitScript(()=>{
        const fetch=window.fetch;window.inspectionAborts=0;
        window.fetch=function(input,options){
            if(String(input).includes('/images/inspect/')&&options?.signal)options.signal.addEventListener('abort',()=>window.inspectionAborts++,{once:true});
            return fetch.call(this,input,options);
        };
    });
    let started;const requested=new Promise(resolve=>{started=resolve;});let release;
    const hold=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/inspect/**',async route=>{started();await hold;await route.continue().catch(()=>{});});
    try {
        await page.goto(workspace);await select(page,'0:shared/same.png');await requested;
        const baseline=await page.evaluate(()=>window.inspectionAborts);
        await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('no-such-file');
        await expect(page.getByText('No matching images · 0 images',{exact:true})).toBeVisible();
        await expect.poll(()=>page.evaluate(()=>window.inspectionAborts),{timeout:1500}).toBeGreaterThan(baseline);
    } finally {release();}
});

test('responsive gallery defers observed size writes and still fits the new viewport',async({page})=>{
    await page.addInitScript(()=>{
        const Native=window.ResizeObserver;window.inlineResizeWrites=0;
        window.ResizeObserver=class extends Native {
            constructor(callback){super((entries,observer)=>{
                const sizes=entries.map(entry=>entry.target.style.height);
                callback(entries,observer);
                entries.forEach((entry,index)=>{
                    if(entry.target.classList.contains('image-grid')&&entry.target.style.height!==sizes[index])window.inlineResizeWrites++;
                });
            });}
        };
    });
    await page.setViewportSize({width:1280,height:900});await page.goto(workspace);
    await expect(page.locator('.image-card')).toHaveCount(4);
    await page.setViewportSize({width:390,height:844});
    await expect.poll(()=>page.locator('.image-card').first().evaluate(node=>node.getBoundingClientRect().width)).toBeLessThan(390);
    await expect.poll(()=>page.locator('.image-grid').evaluate(node=>Number.parseFloat(node.style.height))).toBeCloseTo(383.75,0);
    expect(await page.evaluate(()=>window.inlineResizeWrites)).toBe(0);
});

test('unverified saved associations explain uncertainty and cannot be sent',async({page})=>{
    await page.route('**/images/inspect/uncertain.png?root=0*',route=>route.fulfill({json:{success:true,root_scope:new URL(route.request().url()).searchParams.get('root_scope'),image:{root_index:0,path:'uncertain.png',filename:'uncertain.png'},prompts:{
        positive:{status:'unresolved',text:null,source:'saved-association',message:'A saved library association exists, but it cannot be verified against this file.'},
        negative:{status:'absent',text:null,source:null},
    }}}));
    await page.goto(workspace);await select(page,'0:uncertain.png');
    await expect(role(details(page),'Positive')).toContainText('cannot be verified against this file');
    await expect(details(page).getByRole('button',{name:/^Send /})).toHaveCount(0);
});

test('unresolved roles name the blocking node as text and offer no prompt actions',async({page})=>{
    const line='Prompt uses a node the inspector does not support (<img src=x onerror="window.injected=1">).';
    await page.route('**/images/inspect/uncertain.png?root=0*',route=>route.fulfill({json:{success:true,root_scope:new URL(route.request().url()).searchParams.get('root_scope'),image:{root_index:0,path:'uncertain.png',filename:'uncertain.png'},prompts:{
        positive:{status:'unresolved',text:null,source:null,message:line},negative:{status:'unresolved',text:null,source:null,message:line},
    }}}));
    await page.goto(workspace);await select(page,'0:uncertain.png');
    for (const name of ['Positive','Negative']) await expect(role(details(page),name)).toContainText(line);
    await expect(details(page).locator('img[onerror]')).toHaveCount(0);
    expect(await page.evaluate(()=>window.injected)).toBeUndefined();
    await expect(details(page).getByRole('button',{name:/^(Send|Copy) /})).toHaveCount(0);
});

test('prompt role headings contain compact real actions and honest source evidence',async({page})=>{
    await page.goto(workspace);await select(page,'0:shared/same.png');
    for (const name of ['Positive','Negative']) {
        const block=role(details(page),name),heading=block.locator('.prompt-block-heading');
        await expect(heading.getByRole('heading',{name:`${name} prompt`,exact:true})).toBeVisible();
        await expect(heading.getByRole('button',{name:`Copy ${name.toLowerCase()} prompt`,exact:true})).toBeVisible();
        await expect(heading.getByRole('button',{name:`Send ${name.toLowerCase()} prompt`,exact:true})).toBeVisible();
        await expect(heading.locator('[data-copy-role] svg')).toHaveCount(1);
        expect(await heading.evaluate(node=>node.nextElementSibling.classList.contains('prompt-source'))).toBe(true);
    }
    await expect(role(details(page),'Positive').locator('.prompt-source')).toHaveText('Inferred from the saved workflow');
    await select(page,'0:empty-negative.png');
    await expect(role(details(page),'Negative')).toContainText('Empty negative prompt');
    await expect(role(details(page),'Negative').locator('.prompt-block-heading [data-copy-role]')).toHaveCount(1);
    await select(page,'0:uncertain.png');await expect(details(page).locator('[data-copy-role]')).toHaveCount(0);
    await expect(details(page)).toContainText('Not recorded in this file.');
});
