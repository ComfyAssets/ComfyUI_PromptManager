import {readFileSync} from 'node:fs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';

test.beforeEach(async({page,browserName})=>{
    await page.addInitScript(()=>Object.defineProperty(Crypto.prototype,'randomUUID',{value:undefined,configurable:true}));
    await startCoverage(page,browserName);
    page.focusedErrors=[];page.on('pageerror',error=>page.focusedErrors.push(error.message));
    await page.route('**/scripts/app.js',route=>route.fulfill({contentType:'text/javascript',body:'export const app=window.fixtureApp;'}));
    await page.route('**/scripts/api.js',route=>route.fulfill({contentType:'text/javascript',body:'export const api={fileURL:path=>path,queuePrompt:async(...args)=>args};'}));
    await page.route('**/extensions/promptmanager/prompt_manager.js',route=>route.fulfill({contentType:'text/javascript',body:readFileSync(new URL('../../web/comfy/prompt_manager.js',import.meta.url),'utf8')}));
    await page.goto('/prompt_manager/test');
    await page.evaluate(async()=>{
        document.body.replaceChildren();document.body.style.setProperty('--bg-color','#222');
        window.fixtureApp={registerExtension(extension){this.extension=extension;},ui:{settings:new EventTarget()},graph:{setDirtyCanvas(){}},canvas:{}};
        await import('/extensions/promptmanager/prompt_manager.js');
        class Node {
            constructor(){this.widgets=['text','category','tags','search_text'].map(name=>({name,type:'text',value:'',options:{},inputEl:document.createElement('textarea')}));this.inputs=['clip','prepend_text','append_text'].map(name=>({name,link:null}));this.properties={};this.size=[550,700];}
            addWidget(type,name,value,callback,options){const widget={type,name,value,callback,options};this.widgets.push(widget);return widget;}
            addDOMWidget(name,type,element){document.body.append(element);return {element};}
            setSize(size){this.size=size;}setDirtyCanvas(){}
            serialize(){return {widgets_values:this.widgets.map(widget=>widget.value),properties:{...this.properties}};}
            configure(data){this.widgets.forEach((widget,index)=>{widget.value=data.widgets_values[index];});this.properties={...data.properties};}
        }
        await fixtureApp.extension.beforeRegisterNodeDef(Node,{name:'PromptManagerFocused'},fixtureApp);
        window.node=new Node();node.onNodeCreated();
    });
    await expect(page.locator('.pm-focused')).toBeVisible();
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.focusedErrors).toEqual([]);});

test('Focused previews before loading, keeps metadata optional and composes text',async({page})=>{
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{id:1,text:'Library portrait',category:'Portrait',tags:['studio']}]}}));
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('My draft');
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await page.getByRole('button',{name:/Library portrait/}).click();
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('My draft');
    await page.getByRole('switch',{name:'Load metadata'}).uncheck();
    await page.getByRole('button',{name:'Use prompt',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('Library portrait');
    await page.getByText('Composition · prepend / append',{exact:true}).click();
    await page.getByLabel('Prepend',{exact:true}).fill('trigger');
    await page.getByLabel('Append',{exact:true}).fill('detailed');
    await page.getByText('Composed text preview',{exact:true}).click();
    await expect(page.locator('[data-composed-preview]')).toHaveText('trigger Library portrait detailed');
    expect(await page.evaluate(()=>node.serialize().widgets_values.slice(0,6))).toEqual(['Library portrait','','','','trigger','detailed']);
});

test('Focused restores workflow values and disclosures and protects connected text',async({page})=>{
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('Saved draft');
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    await page.getByLabel('Category',{exact:true}).fill('Saved category');
    await page.evaluate(()=>{const saved=node.serialize();node.configure(saved);});
    await expect(page.getByLabel('Category',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Category',{exact:true})).toHaveValue('Saved category');
    await page.evaluate(()=>{node.inputs.push({name:'text',link:9});node.onConnectionsChange();});
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toBeHidden();
    await expect(page.getByText('Text is supplied by an upstream connection.',{exact:true})).toBeVisible();
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='text').link=null;node.onConnectionsChange();});
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('Saved draft');
});

test('Focused searches quoted tags with Enter and explicitly loads metadata', async ({page}) => {
    let requested;
    await page.route('**/prompt_manager/search?**', async route => {
        requested = new URL(route.request().url());
        await route.fulfill({json:{success:true,results:[{id:7,text:'Selected fox',category:'Animals',tags:['film grain','fox']}]}});
    });
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('Preserved draft');
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    const query=page.getByRole('searchbox',{name:'Search saved prompts'});
    await query.fill('fox tag:"film grain"');
    await query.press('Enter');
    await page.getByRole('button',{name:/Selected fox/}).click();
    expect(requested.searchParams.get('text')).toBe('fox');
    expect(requested.searchParams.get('tags')).toBe('film grain');
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('Preserved draft');
    await page.getByRole('button',{name:'Use prompt',exact:true}).click();
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    await expect(page.getByLabel('Category',{exact:true})).toHaveValue('Animals');
    await expect(page.getByLabel('Tags',{exact:true})).toHaveValue('film grain, fox');
    expect(await page.evaluate(()=>node.serialize().widgets_values.slice(0,4))).toEqual(['Selected fox','Animals','film grain, fox','fox tag:"film grain"']);
});

test('Focused reports empty queries, no results, and safely renders backend and HTTP errors', async ({page}) => {
    let requests=0;
    let mode='empty';
    const malicious='<img src=x onerror="window.focusedInjected=true">';
    await page.route('**/prompt_manager/search?**', async route => {
        requests++;
        if(mode==='http') return route.fulfill({status:503,body:'Unavailable'});
        await route.fulfill({json:mode==='error'?{success:false,error:malicious}:{success:true,results:[]}});
    });
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    const search=page.getByRole('button',{name:'Search',exact:true});
    await search.click();
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('Enter search text or tag:name.');
    expect(requests).toBe(0);
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('missing');
    await search.click();
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('No matching prompts.');
    await expect(page.locator('.pm-editor-result')).toHaveCount(0);
    mode='error';
    await search.click();
    await expect(page.getByRole('alert')).toHaveText(`Could not load prompts: ${malicious}`);
    await expect(page.locator('.pm-editor-library img')).toHaveCount(0);
    expect(await page.evaluate(()=>window.focusedInjected)).toBeUndefined();
    mode='http';
    await search.click();
    await expect(page.getByRole('alert')).toContainText('HTTP 503');
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('');
    await search.click();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('Enter search text or tag:name.');
    mode='empty';
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('missing');
    await search.click();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('No matching prompts.');
});

test('Focused treats saved prompt content and metadata as text', async ({page}) => {
    const malicious='<img src=x onerror="window.focusedInjected=true">';
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{id:8,text:malicious,category:'<script>bad()</script>',tags:['<svg onload=bad()>']}]}}));
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await page.locator('.pm-editor-result').click();
    await expect(page.locator('.pm-editor-selection .pm-editor-preview')).toHaveText(malicious);
    await expect(page.locator('.pm-editor-library img, .pm-editor-library svg, .pm-editor-library script')).toHaveCount(0);
    await page.getByRole('button',{name:'Use prompt',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue(malicious);
    expect(await page.evaluate(()=>window.focusedInjected)).toBeUndefined();
});

async function deferLibraryRequests(page) {
    await page.evaluate(()=>{
        const originalFetch=window.fetch;
        window.libraryRequests=[];
        window.fetch=(url,options)=>{
            if(!/\/prompt_manager\/(search|recent)\?/.test(String(url))) return originalFetch(url,options);
            return new Promise(resolve=>window.libraryRequests.push({url:String(url),signal:options.signal,resolve}));
        };
        window.completeLibraryRequest=(index,text)=>libraryRequests[index].resolve(new Response(JSON.stringify({success:true,results:[{id:index,text}]}),{status:200,headers:{'Content-Type':'application/json'}}));
    });
}

test('Focused keeps the latest result when searches and recent requests overlap', async ({page}) => {
    await deferLibraryRequests(page);
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('older search');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    expect(await page.evaluate(()=>libraryRequests[0].signal.aborted)).toBe(true);
    await page.evaluate(()=>completeLibraryRequest(1,'Latest recent'));
    await expect(page.locator('.pm-editor-result')).toHaveText('Latest recent');
    await page.evaluate(()=>completeLibraryRequest(0,'Stale search'));
    await expect(page.locator('.pm-editor-result')).toHaveText('Latest recent');
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('latest search');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    expect(await page.evaluate(()=>libraryRequests[2].signal.aborted)).toBe(true);
    await page.evaluate(()=>completeLibraryRequest(3,'Latest search'));
    await expect(page.locator('.pm-editor-result')).toHaveText('Latest search');
    await page.evaluate(()=>completeLibraryRequest(2,'Stale recent'));
    await expect(page.locator('.pm-editor-result')).toHaveText('Latest search');
});

test('Focused removal aborts work and stops theme and control listeners', async ({page}) => {
    await deferLibraryRequests(page);
    await page.getByRole('button',{name:'Browse library',exact:true}).click();
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await expect(page.locator('.pm-focused')).toHaveAttribute('data-ca-theme','dark');
    const removed=await page.evaluate(()=>{
        node.onRemoved();
        document.body.style.setProperty('--bg-color','#ffffff');
        fixtureApp.ui.settings.dispatchEvent(new Event('Comfy.ColorPalette.change'));
        return {requestAborted:libraryRequests[0].signal.aborted,scopeAborted:node._focusedScope.signal.aborted};
    });
    expect(removed).toEqual({requestAborted:true,scopeAborted:true});
    await page.evaluate(()=>completeLibraryRequest(0,'Late result after removal'));
    await expect(page.locator('.pm-editor-result')).toHaveCount(0);
    await expect(page.locator('.pm-focused')).toHaveAttribute('data-ca-theme','dark');
    await page.getByRole('button',{name:'Hide library',exact:true}).click();
    await expect(page.getByRole('button',{name:'Hide library',exact:true})).toBeVisible();
});

test('Focused fields reflow with node width and follow live host themes', async ({page}) => {
    await page.setViewportSize({width:1000,height:1000});
    await page.getByText('Composition · prepend / append',{exact:true}).click();
    await page.evaluate(()=>{node._focusedView.element.style.width='700px';node._focusedView.element.style.height='850px';});
    const prepend=page.getByLabel('Prepend',{exact:true});
    const append=page.getByLabel('Append',{exact:true});
    let first=await prepend.boundingBox(),second=await append.boundingBox();
    expect(Math.abs(first.y-second.y)).toBeLessThan(2);
    expect(second.x).toBeGreaterThan(first.x+first.width);
    await page.evaluate(()=>{node._focusedView.element.style.width='300px';});
    await expect.poll(async()=>{const a=await prepend.boundingBox(),b=await append.boundingBox();return b.y>a.y+a.height;}).toBe(true);
    expect(await page.locator('.pm-focused').evaluate(element=>element.scrollWidth<=element.clientWidth+1)).toBe(true);
    const dark=await prepend.evaluate(element=>getComputedStyle(element).backgroundColor);
    await page.evaluate(()=>document.body.style.setProperty('--bg-color','#ffffff'));
    await expect(page.locator('.pm-focused')).toHaveAttribute('data-ca-theme','light');
    await expect.poll(()=>prepend.evaluate(element=>getComputedStyle(element).backgroundColor)).not.toBe(dark);
    await page.evaluate(()=>document.body.style.setProperty('--bg-color','#222222'));
    await expect(page.locator('.pm-focused')).toHaveAttribute('data-ca-theme','dark');
    await expect.poll(()=>prepend.evaluate(element=>getComputedStyle(element).backgroundColor)).toBe(dark);
});

test('Focused composed preview preserves main prompt whitespace like queued execution', async ({page}) => {
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('  draft  ');
    await page.getByText('Composition · prepend / append',{exact:true}).click();
    await page.getByLabel('Prepend',{exact:true}).fill(' trigger ');
    await page.getByLabel('Append',{exact:true}).fill(' detail ');
    await page.getByText('Composed text preview',{exact:true}).click();
    expect(await page.locator('[data-composed-preview]').textContent()).toBe('trigger   draft   detail');
});

test('Focused exposes the visible text element for autocomplete and host extension listeners', async ({page}) => {
    expect(await page.evaluate(()=>node.widgets.find(widget=>widget.name==='text').inputEl===document.querySelector('.pm-focused textarea'))).toBe(true);
});

test('Focused expands its main editor when the node gets taller',async({page})=>{
    await page.locator('.pm-focused').evaluate(element=>{element.style.width='550px';element.style.height='800px';});
    const editor=await page.getByRole('textbox',{name:'Main prompt',exact:true}).boundingBox();
    expect(editor.height).toBeGreaterThan(400);
});

test('Focused respects the existing Web UI display preference',async({page})=>{
    await page.route('**/prompt_manager/settings',route=>route.fulfill({json:{success:true,settings:{webui_display_mode:'newtab'}}}));
    await page.evaluate(()=>{
        const Node=node.constructor;node.onRemoved();node._focusedView.element.remove();node=new Node();node.onNodeCreated();
        window.open=(...args)=>{window.openArgs=args;return {};};
    });
    await page.evaluate(()=>node._focusedSettingsReady);
    await page.getByRole('button',{name:'Web UI ↗',exact:true}).click();
    expect(await page.evaluate(()=>window.openArgs)).toEqual(['/prompt_manager/web','_blank']);
});

test('Focused reports blocked Web UI popups without leaving the workflow',async({page})=>{
    await page.evaluate(()=>{window.open=()=>null;});
    await page.getByRole('button',{name:'Web UI ↗',exact:true}).click();
    await expect(page.locator('.pm-focused [role="alert"]')).toContainText('Allow popups');
    await expect(page.locator('.pm-focused')).toBeVisible();
});
