import { readFileSync } from 'node:fs';
import { test, expect } from './fixtures.mjs';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';

test.beforeEach(async ({page, browserName}) => {
    await startCoverage(page, browserName);
    page.classicErrors = [];
    page.on('pageerror', error => page.classicErrors.push(error.message));
    await page.route('**/scripts/app.js', route => route.fulfill({contentType:'text/javascript', body:'export const app = window.fixtureApp;'}));
    await page.route('**/scripts/api.js', route => route.fulfill({contentType:'text/javascript', body:'export const api = {fileURL: path => (window.fixtureApiBase ?? "") + path, queuePrompt: async (...args) => args};'}));
    await page.route('**/extensions/promptmanager/prompt_manager.js', route => route.fulfill({contentType:'text/javascript', body:readFileSync(new URL('../../web/comfy/prompt_manager.js', import.meta.url),'utf8')}));
    await page.goto('/prompt_manager/test');
    await page.evaluate(() => {
        document.body.replaceChildren();
        document.body.style.setProperty('--bg-color','#222');
        window.fixtureApp={extensions:[],registerExtension(extension){this.extensions.push(extension);},ui:{settings:new EventTarget()},canvas:{},graph:{setDirtyCanvas(){}}};
    });
    await page.evaluate(async () => {
        await import('/extensions/promptmanager/prompt_manager.js');
        class Node {
            constructor(){
                this.widgets=[...['text','category','tags','search_text'].map(name=>({name,value:'',inputEl:document.createElement('textarea')})),{name:'save_to_library',type:'toggle',value:true}];
                this.widgets_values=[]; this.inputs=[];
            }
            addDOMWidget(name,type,element){document.body.append(element);return {element};}
            setSize(){} setDirtyCanvas(){} serialize(){return {properties:{...this.properties}};}
        }
        await fixtureApp.extensions[0].beforeRegisterNodeDef(Node,{name:'PromptManager'},fixtureApp);
        window.node=new Node();node.onNodeCreated();
    });
    await expect(page.locator('.pm-classic')).toBeVisible();
});

test.afterEach(async ({page,browserName}) => {
    await saveCoverage(page,browserName);
    expect(page.classicErrors).toEqual([]);
});

test('Classic loads an older workflow with Save to library on and keeps a saved off (#90)',async({page})=>{
    // A workflow saved before the switch held the search UI's slot ('') at the switch's index
    const loaded=await page.evaluate(()=>{const widget=node.widgets.find(w=>w.name==='save_to_library');widget.value='';node.configure({});return widget.value;});
    expect(loaded).toBe(true);
    const off=await page.evaluate(()=>{const widget=node.widgets.find(w=>w.name==='save_to_library');widget.value=false;node.configure({});return [widget.value,node.serialize().widgets_values[4]];});
    expect(off).toEqual([false,false]);
});

test('Classic keeps immediate selection, search query and metadata serialization',async({page})=>{
    await page.route('**/prompt_manager/search?**', route=>route.fulfill({json:{success:true,results:[{text:'A new portrait',category:'Portrait',tags:['film grain'],rating:5}]}}));
    await page.evaluate(()=>{node.widgets.find(w=>w.name==='search_text').value='tag:"film grain" portrait';});
    const response=page.waitForRequest('**/prompt_manager/search?**');
    await page.getByRole('button',{name:'🔍 Search',exact:true}).click();
    expect(new URL((await response).url()).searchParams.get('tags')).toBe('film grain');
    const result=page.getByRole('button',{name:/A new portrait/});
    await result.focus();await result.press('Enter');
    expect(await page.evaluate(()=>node.serialize().widgets_values.slice(0,3))).toEqual(['A new portrait','Portrait','film grain']);
    await expect(result).toHaveAttribute('aria-pressed','true');
    const released=await page.evaluate(()=>{
        const previous=node.resultsSection.querySelector('button');
        node.displayResults([{text:'Replacement',tags:[]}]);
        previous.click();
        return node.widgets.find(widget=>widget.name==='text').value;
    });
    expect(released).toBe('A new portrait');
    await expect(page.getByRole('button',{name:'🌐 Web UI',exact:true})).toBeVisible();
});

test('Classic Web UI opens the workspace Library in the saved display mode, through the mount prefix (#525)',async({page})=>{
    await page.evaluate(()=>{window.fixtureApiBase='/mounted';window.open=(...args)=>{window.openArgs=args;return {};};});
    const webUi=page.getByRole('button',{name:'🌐 Web UI',exact:true});
    for (const [mode,args] of [
        ['newtab',['/mounted/prompt_manager/workspace#/library','_blank']],
        ['popup',['/mounted/prompt_manager/workspace#/library','_blank','width=1200,height=800,scrollbars=yes,resizable=yes']],
    ]) {
        await page.evaluate(value=>{node.properties.webuiDisplayMode=value;},mode);
        await webUi.click();
        expect(await page.evaluate(()=>window.openArgs)).toEqual(args);
    }
});

test('Classic Web UI reports a blocked popup and keeps the canvas open (#525)',async({page})=>{
    const before=page.url();
    await page.evaluate(()=>{window.open=()=>null;});
    await page.getByRole('button',{name:'🌐 Web UI',exact:true}).click();
    await expect(page.getByText('Allow popups to open Web UI, then try again.')).toBeVisible();
    expect(page.url()).toBe(before);
    await expect(page.locator('.pm-classic')).toBeVisible();
});

test('Classic detaches native text synchronization on removal',async({page})=>{
    // The page installs the text sync (serializeValue and its listeners) in a 50 ms timer; wait for it, not for time.
    await expect.poll(()=>page.evaluate(()=>typeof node.widgets.find(item=>item.name==='text').serializeValue)).toBe('function');
    const values=await page.evaluate(()=>{
        const widget=node.widgets.find(item=>item.name==='text');
        widget.inputEl.value='Before removal';widget.inputEl.dispatchEvent(new Event('input'));
        const before=widget.value;node.onRemoved();
        widget.inputEl.value='After removal';widget.inputEl.dispatchEvent(new Event('input'));
        return [before,widget.value];
    });
    expect(values).toEqual(['Before removal','Before removal']);
});

test('Classic follows host themes, keeps restore behavior and disposes resources',async({page})=>{
    const panel=page.locator('.pm-classic');
    await expect(panel).toHaveAttribute('data-ca-theme','dark');
    await page.evaluate(()=>document.body.style.setProperty('--bg-color','#fff'));
    await expect(panel).toHaveAttribute('data-ca-theme','light');
    await page.evaluate(()=>{node.properties.resultTimeout=0;node.displayResults([{text:'Example',tags:[]}]);node.hideResults();});
    await page.getByRole('button',{name:/Results auto-hidden/}).click();
    await expect(page.getByRole('button',{name:/Example/})).toBeVisible();
    const state=await page.evaluate(()=>{
        node.showNotification('Failure','error');node.onRemoved();
        const disposed=node._classicScope.signal.aborted;
        node.showNotification('Late response','info');
        return {disposed,notifications:document.querySelectorAll('.pm-classic-notification').length,style:node._styleSheet.isConnected};
    });
    expect(state).toEqual({disposed:true,notifications:0,style:false});
    await page.evaluate(()=>document.body.style.setProperty('--bg-color','#222'));
    await expect(panel).toHaveAttribute('data-ca-theme','light');
});

test('Classic handles empty queries, empty results, failed requests and late responses',async({page})=>{
    await page.getByRole('button',{name:'🔍 Search',exact:true}).click();
    await expect(page.locator('.pm-classic-results')).toContainText('Enter search text');
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[]}}));
    await page.getByRole('button',{name:'📋 Recent',exact:true}).click();
    await expect(page.locator('.pm-classic-results')).toContainText('No prompts found');
    await page.evaluate(async()=>{
        node.callNodeMethod=async()=>{throw new Error('<img src=x onerror=alert(1)>');};
        await node.loadRecentPrompts();
    });
    await expect(page.locator('.pm-classic-results [role="alert"]')).toContainText('<img src=x');
    await expect(page.locator('.pm-classic-results img')).toHaveCount(0);
    const late=await page.evaluate(async()=>{
        let finish; node.callNodeMethod=()=>new Promise(resolve=>{finish=resolve;});
        const loading=node.loadRecentPrompts();node.onRemoved();finish({results:[{text:'Late'}]});await loading;
        return {notifications:node._classicNotifications.size,late:node.resultsSection.textContent.includes('Late')};
    });
    expect(late).toEqual({notifications:0,late:false});
});

test('shipped shared factories keep keyboard behavior and theme isolation across mounts',async({page})=>{
    await page.evaluate(async()=>{
        const ui=await import('/prompt_manager/js/shared/toolkit/core.js');
        const scope=ui.createScope();const panel=document.createElement('div');panel.className='ca-tools';panel.id='shared-contract';
        window.shared={ui,scope,panel,events:[]};
        document.body.append(panel);
        ui.mountTheme(panel,null,scope);
        const save=ui.createButton('Save contract','ca-primary');scope.listen(save,'click',()=>shared.events.push('save'));
        panel.append(save,ui.createSlider('Size',{id:'contract-size',min:0,max:100,step:1,value:50,unit:'px'},scope,value=>shared.events.push(value)),ui.createSwitch('Enabled','','contract-enabled'),ui.createField('Order',ui.createSelect([['name','Name'],['date','Date']],''),'contract-order'),ui.createSegments('View',[['grid','Grid'],['list','List']],'contract-view',scope,value=>shared.events.push(value)));
    });
    await page.getByRole('button',{name:'Save contract',exact:true}).press('Enter');
    await page.getByRole('slider',{name:'Size'}).press('ArrowRight');
    await expect(page.locator('#shared-contract output')).toHaveText('51 px');
    await page.getByRole('switch',{name:'Enabled'}).uncheck();
    await page.getByLabel('Order').selectOption('date');
    await page.getByRole('radio',{name:'List',exact:true}).check();
    expect(await page.evaluate(()=>shared.events)).toEqual(['save',51,'list']);
    await page.evaluate(()=>{document.body.style.removeProperty('--bg-color');fixtureApp.ui.settings.dispatchEvent(new Event('Comfy.ColorPalette.change'));});
    await page.emulateMedia({colorScheme:'light'});
    await expect(page.locator('#shared-contract')).toHaveAttribute('data-ca-theme','light');
    await page.emulateMedia({colorScheme:'dark'});
    await expect(page.locator('#shared-contract')).toHaveAttribute('data-ca-theme','dark');
    await page.evaluate(()=>{shared.scope.dispose();document.body.style.setProperty('--bg-color','#fff');});
    await expect(page.locator('#shared-contract')).toHaveAttribute('data-ca-theme','dark');
    await expect(page.locator('.pm-classic')).toHaveAttribute('data-ca-theme','light');
});

test('Classic test button reports success and failure and releases expired notifications',async({page})=>{
    await page.clock.install();
    await page.evaluate(()=>{node.properties.showTestButton=true;node.createSearchUI(node._classicContainer);});
    await page.getByRole('button',{name:'🔧 Test API',exact:true}).click();
    await expect(page.locator('.pm-classic-results')).toContainText('API Connection Successful');
    await page.route('**/prompt_manager/test',route=>route.fulfill({status:503,body:'unavailable'}));
    await page.getByRole('button',{name:'🔧 Test API',exact:true}).click();
    await expect(page.locator('.pm-classic-results [role="alert"]')).toContainText('503');
    await page.route('**/prompt_manager/test',route=>route.fulfill({json:{success:false,message:'Not ready'}}));
    await page.getByRole('button',{name:'🔧 Test API',exact:true}).click();
    await expect(page.locator('.pm-classic-results [role="alert"]')).toContainText('Not ready');
    await page.clock.fastForward(3001);
    await expect(page.locator('.pm-classic-notification')).toHaveCount(0);
});

test('Classic shows linked text, keeps the typed text through save and refuses to overwrite it (#343)',async({page})=>{
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{text:'Saved prompt',tags:[]}]}}));
    const note=page.locator('.pm-classic').getByText('Text comes from a link. Your typed text is kept for when you disconnect it.',{exact:true});
    await expect(note).toBeHidden();
    await page.evaluate(()=>{
        const widget=node.widgets.find(item=>item.name==='text');widget.value=widget.inputEl.value='Typed classic';
        node.inputs=[{name:'text',type:'STRING',link:3,widget:{name:'text'}}];node.onConnectionsChange();
    });
    await expect(note).toBeVisible();
    // The link drives the run; the saved workflow keeps what the user typed, not a stale or empty value.
    expect(await page.evaluate(()=>node.serialize().widgets_values[0])).toBe('Typed classic');
    await page.getByRole('button',{name:'📋 Recent',exact:true}).click();
    await page.getByRole('button',{name:/Saved prompt/}).click();
    expect(await page.evaluate(()=>node.widgets.find(item=>item.name==='text').value)).toBe('Typed classic');
    await expect(page.getByRole('alert').filter({hasText:'Text comes from a link. Disconnect it to use a saved prompt.'})).toBeVisible();
    await page.evaluate(()=>{node.inputs[0].link=null;node.onConnectionsChange();});
    await expect(note).toBeHidden();
    await page.getByRole('button',{name:/Saved prompt/}).click();
    expect(await page.evaluate(()=>node.widgets.find(item=>item.name==='text').value)).toBe('Saved prompt');
    // A workflow load re-evaluates the link state.
    await page.evaluate(()=>{node.inputs[0].link=8;node.configure({});});
    await expect(note).toBeVisible();
});

test('PromptManagerText shows linked text in its legacy controls and keeps the typed text (#343)',async({page})=>{
    await page.evaluate(async()=>{
        class TextNode {
            constructor(){this.widgets=['text','category','tags','search_text'].map(name=>({name,value:'',inputEl:document.createElement('textarea')}));this.widgets_values=[];this.inputs=[{name:'text',type:'STRING',link:null,widget:{name:'text'}}];}
            addDOMWidget(name,type,element){element.id='legacy-text-node';document.body.append(element);return {element};}
            setSize(){} setDirtyCanvas(){} serialize(){return {properties:{...this.properties}};}
        }
        await fixtureApp.extensions[0].beforeRegisterNodeDef(TextNode,{name:'PromptManagerText'},fixtureApp);
        window.textNode=new TextNode();textNode.onNodeCreated();
        const widget=textNode.widgets[0];widget.value=widget.inputEl.value='Typed text node';
    });
    const note=page.locator('#legacy-text-node').getByText('Text comes from a link. Your typed text is kept for when you disconnect it.',{exact:true});
    await expect(note).toBeHidden();
    await page.evaluate(()=>{textNode.inputs[0].link=5;textNode.onConnectionsChange();});
    await expect(note).toBeVisible();
    expect(await page.evaluate(()=>textNode.serialize().widgets_values[0])).toBe('Typed text node');
    await page.evaluate(()=>{textNode.usePrompt({text:'Replacement'},0);});
    expect(await page.evaluate(()=>textNode.widgets[0].value)).toBe('Typed text node');
    await page.evaluate(()=>{textNode.inputs[0].link=null;textNode.onConnectionsChange();textNode.onRemoved();});
    await expect(note).toBeHidden();
});
