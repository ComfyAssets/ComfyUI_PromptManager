import { readFileSync } from 'node:fs';
import { test, expect } from './fixtures.mjs';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';

test.beforeEach(async ({page, browserName}) => {
    await startCoverage(page, browserName);
    page.classicErrors = [];
    page.on('pageerror', error => page.classicErrors.push(error.message));
    await page.route('**/scripts/app.js', route => route.fulfill({contentType:'text/javascript', body:'export const app = window.fixtureApp;'}));
    await page.route('**/scripts/api.js', route => route.fulfill({contentType:'text/javascript', body:'export const api = {fileURL: path => path, queuePrompt: async (...args) => args};'}));
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
                this.widgets=['text','category','tags','search_text'].map(name=>({name,value:'',inputEl:document.createElement('textarea')}));
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

test('Classic detaches native text synchronization on removal',async({page})=>{
    await page.waitForTimeout(60);
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
