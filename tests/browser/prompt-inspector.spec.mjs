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
        await page.getByRole('combobox',{name:'Appearance',exact:true}).selectOption('light');
        await page.screenshot({path:testInfo.outputPath('inspector-light.png'),fullPage:true});
        await details(page).getByRole('button',{name:'Open full preview',exact:true}).click();
        await expect(page.getByRole('dialog',{name:'Image preview',exact:true})).toBeVisible();
        await page.screenshot({path:testInfo.outputPath('viewer-prompts.png')});
        await page.getByRole('button',{name:'Close preview',exact:true}).click();
        await page.setViewportSize({width:390,height:844});await details(page).scrollIntoViewIfNeeded();
        await page.screenshot({path:testInfo.outputPath('inspector-mobile.png')});
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
        await page.getByRole('searchbox',{name:'Search filenames',exact:true}).fill('no-such-file');
        await expect(page.getByText('No matching images or media. Try changing the filters.',{exact:true})).toBeVisible();
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
