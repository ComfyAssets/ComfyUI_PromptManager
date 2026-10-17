import {readFileSync} from 'node:fs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';

const pair={id:4,positive_text:'Library positive',negative_text:'Library negative',category:'Portrait',tags:['studio']};
test.beforeEach(async({page,browserName})=>{
    await startCoverage(page,browserName);
    page.pairErrors=[];page.on('pageerror',error=>page.pairErrors.push(error.message));
    await page.route('**/scripts/app.js',route=>route.fulfill({contentType:'text/javascript',body:'export const app=window.fixtureApp;'}));
    await page.route('**/scripts/api.js',route=>route.fulfill({contentType:'text/javascript',body:'export const api={fileURL:path=>path,queuePrompt:async(...args)=>args};'}));
    await page.route('**/extensions/promptmanager/prompt_manager.js',route=>route.fulfill({contentType:'text/javascript',body:readFileSync(new URL('../../web/comfy/prompt_manager.js',import.meta.url),'utf8')}));
    await page.route('**/prompt_manager/pairs?**',route=>route.fulfill({json:{success:true,pairs:[pair],total:1}}));
    await page.goto('/prompt_manager/test');
    await page.evaluate(async()=>{
        document.body.replaceChildren();
        const settings=new EventTarget();settings.getSettingValue=()=> 'dark';
        window.fixtureApp={registerExtension(extension){this.extension=extension;},ui:{settings},graph:{_nodes:[],setDirtyCanvas(){}},canvas:{},graphToPrompt:async()=>({})};
        await import('/extensions/promptmanager/prompt_manager.js');
        class Node {
            constructor(){this.id=1;this.type='PromptManagerPair';this.widgets=['text','negative_text','category','tags','search_text'].map(name=>({name,type:'text',value:'',options:{},inputEl:document.createElement('textarea')}));this.inputs=['clip','prepend_text','append_text'].map(name=>({name,link:null}));this.properties={};this.size=[1100,700];}
            addWidget(type,name,value,callback,options){const widget={type,name,value,callback,options};this.widgets.push(widget);return widget;}
            addDOMWidget(name,type,element){const host=document.createElement('div');host.style.cssText='height:700px;width:1100px;max-width:100%';host.className='fixture-dom-widget';host.append(element);document.body.append(host);return {element};}
            setSize(size){this.size=size;}setDirtyCanvas(){}
            serialize(){return {widgets_values:this.widgets.map(widget=>widget.value),properties:{...this.properties}};}
            configure(data){this.widgets.forEach((widget,index)=>widget.value=data.widgets_values[index]);this.properties={...data.properties};}
        }
        await fixtureApp.extension.beforeRegisterNodeDef(Node,{name:'PromptManagerPair'},fixtureApp);
        window.node=new Node();node.onNodeCreated?.();fixtureApp.graph._nodes=[node];
    });
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.pairErrors).toEqual([]);});

async function selectPair(page){await page.getByRole('button',{name:'Recent',exact:true}).click();await page.locator('.pm-editor-result').click();}
async function draft(page,positive='Draft positive',negative='Draft negative'){await page.getByRole('textbox',{name:'Positive prompt',exact:true}).fill(positive);await page.getByRole('textbox',{name:'Negative prompt',exact:true}).fill(negative);}

test('paired editor previews both texts before explicitly replacing both',async({page})=>{
    await expect(page.locator('.pm-pair')).toBeVisible();
    await draft(page);
    await selectPair(page);
    await expect(page.locator('.pm-editor-selection')).toContainText('Library positive');
    await expect(page.locator('.pm-editor-selection')).toContainText('Library negative');
    await expect(page.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('Draft positive');
    await expect(page.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue('Draft negative');
    await page.getByRole('switch',{name:'Load metadata'}).uncheck();
    await page.getByRole('button',{name:'Use pair',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue(pair.positive_text);
    await expect(page.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue(pair.negative_text);
    expect(await page.evaluate(()=>Object.fromEntries(node.widgets.map(w=>[w.name,w.value])))).toMatchObject({text:pair.positive_text,negative_text:pair.negative_text,category:'',tags:''});
    await page.evaluate(()=>{const saved=node.serialize();node.configure(saved);});
    await expect(page.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue(pair.negative_text);
});

test('paired editor sizes both editors equally and composes positive text only',async({page},testInfo)=>{
    await draft(page);
    const positive=page.getByRole('textbox',{name:'Positive prompt',exact:true});const negative=page.getByRole('textbox',{name:'Negative prompt',exact:true});
    const [p,n]=await Promise.all([positive.boundingBox(),negative.boundingBox()]);expect(Math.abs(p.height-n.height)).toBeLessThan(1);expect(Math.abs(p.width-n.width)).toBeLessThan(1);
    await page.getByText('Composition · prepend / append',{exact:true}).click();
    await page.getByRole('textbox',{name:'Prepend',exact:true}).fill('trigger');
    await page.getByRole('textbox',{name:'Append',exact:true}).fill('detail');
    await page.getByText('Composed text preview',{exact:true}).click();
    await expect(page.locator('[data-composed-preview]')).toHaveText('trigger Draft positive detail');
    await expect(negative).toHaveValue('Draft negative');
    await page.screenshot({path:testInfo.outputPath('pair-editor.png')});
});

for(const field of ['text','negative_text']) test(`paired loading is atomic when ${field} is connected`,async({page})=>{
    await draft(page);
    await selectPair(page);
    await page.evaluate(field=>{node.inputs.push({name:field,link:8});node.onConnectionsChange();},field);
    await expect(page.getByRole('button',{name:'Use pair',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Use pair',exact:true}).evaluate(button=>button.dispatchEvent(new Event('click',{bubbles:true})));
    expect(await page.evaluate(()=>Object.fromEntries(node.widgets.map(w=>[w.name,w.value])))).toMatchObject({text:'Draft positive',negative_text:'Draft negative'});
    await page.evaluate(()=>{node.inputs=node.inputs.filter(input=>input.link!==8);node.onConnectionsChange();});
    await expect(page.getByRole('button',{name:'Use pair',exact:true})).toBeEnabled();
});

test('save pair accepts empty negatives, recovers after errors and never clears the draft',async({page})=>{
    let attempts=0;const bodies=[];
    await page.route('**/prompt_manager/pairs',route=>{bodies.push(route.request().postDataJSON());return ++attempts===1?route.fulfill({status:503,json:{success:false,error:'Temporary save error'}}):route.fulfill({json:{success:true,pair:{...pair,negative_text:''}}});});
    await draft(page,'Save this','');
    await page.getByRole('button',{name:'Save pair',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('Temporary save error');
    await expect(page.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('Save this');
    await page.getByRole('button',{name:'Save pair',exact:true}).click();
    await expect(page.getByRole('status').filter({hasText:'Pair saved'})).toBeVisible();
    expect(bodies).toEqual([{positive_text:'Save this',negative_text:'',category:'',tags:[]},{positive_text:'Save this',negative_text:'',category:'',tags:[]}]);
});

test('pair filters use repeated tags and removal cleans up',async({page})=>{
    const urls=[];await page.route('**/prompt_manager/pairs?**',route=>{urls.push(new URL(route.request().url()));return route.fulfill({json:{success:true,pairs:[pair]}});});
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('portrait tag:studio');
    await page.getByLabel('Filter category',{exact:true}).fill('Portrait');
    await page.getByLabel('Filter tags',{exact:true}).fill('film, studio, ');
    await page.getByRole('button',{name:'Search',exact:true}).click();await expect(page.locator('.pm-editor-result')).toBeVisible();
    expect(urls[0].searchParams.get('category')).toBe('Portrait');expect(urls[0].searchParams.getAll('tag')).toEqual(['studio','film']);
    await page.evaluate(()=>node.onRemoved());await expect(page.locator('.ca-completions')).toHaveCount(0);
});

test('validating and saving prevents duplicate submissions while preserving pair metadata and drafts',async({page})=>{
    let release;let requests=0;
    await page.route('**/prompt_manager/pairs',async route=>{requests++;await new Promise(resolve=>release=resolve);await route.fulfill({json:{success:true,pair:{...pair,category:'Original category',tags:['original-tag']}}});});
    await page.getByRole('button',{name:'Save pair',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('positive');expect(requests).toBe(0);
    await draft(page);
    await page.getByRole('button',{name:'Save pair',exact:true}).click();
    await expect(page.getByRole('button',{name:'Save pair',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Save pair',exact:true}).evaluate(button=>button.dispatchEvent(new Event('click',{bubbles:true})));
    expect(requests).toBe(1);release();
    await expect(page.getByRole('status').filter({hasText:'Pair saved'})).toBeVisible();
    expect(await page.evaluate(()=>Object.fromEntries(node.widgets.map(w=>[w.name,w.value])))).toMatchObject({text:'Draft positive',negative_text:'Draft negative',category:'',tags:''});
});

test('a superseded library response never replaces the latest pair selection',async({page})=>{
    let release;let started=false;
    await page.route('**/prompt_manager/pairs?**',async route=>{
        if(new URL(route.request().url()).searchParams.get('text')==='old'){started=true;await new Promise(resolve=>release=resolve);await route.fulfill({json:{success:true,pairs:[{...pair,positive_text:'Stale positive'}]}}).catch(()=>{});}
        else await route.fulfill({json:{success:true,pairs:[pair]}});
    });
    const search=page.getByRole('searchbox',{name:'Search saved prompts'});
    await search.fill('old');await page.getByRole('button',{name:'Search',exact:true}).click();await expect.poll(()=>started).toBe(true);
    await search.fill('new');await page.getByRole('button',{name:'Search',exact:true}).click();
    await expect(page.locator('.pm-editor-result')).toContainText('Library positive');release();
    await expect(page.locator('.pm-editor-result')).not.toContainText('Stale positive');
});

test('queue hook synchronizes both editors, preserves links and forwards branch execution arguments',async({page})=>{
    await draft(page);
    const args=await page.evaluate(async()=>{
        await fixtureApp.extension.setup();
        const {api}=await import('/scripts/api.js');
        const output={1:{inputs:{text:'stale',negative_text:'stale'}}};
        const first=await api.queuePrompt(7,{output,workflow:{name:'workflow'}},['branch'],{preview:'latent'});
        node.inputs.push({name:'negative_text',link:17});
        const linked={1:{inputs:{text:'old',negative_text:['17',0]}}};
        const second=await api.queuePrompt(8,{output:linked,workflow:{}},['another']);
        return {first,second};
    });
    expect(args.first).toEqual([7,{output:{1:{inputs:{text:'Draft positive',negative_text:'Draft negative'}}},workflow:{name:'workflow'}},['branch'],{preview:'latent'}]);
    expect(args.second[1].output[1].inputs).toEqual({text:'Draft positive',negative_text:['17',0]});
});

test('paired editors stack at narrow widths while keeping equal editing areas',async({page})=>{
    await page.locator('.fixture-dom-widget').evaluate(host=>host.style.width='560px');
    const [p,n]=await Promise.all(['Positive prompt','Negative prompt'].map(name=>page.getByRole('textbox',{name,exact:true}).boundingBox()));expect(n.y).toBeGreaterThanOrEqual(p.y+p.height);expect(Math.abs(p.width-n.width)).toBeLessThan(1);expect(Math.abs(p.height-n.height)).toBeLessThan(1);
});

test('removal aborts an in-flight pair save and restores host-owned textarea references',async({page})=>{
    await draft(page);
    const state=await page.evaluate(async()=>{
        const previous=window.fetch;let signal;
        window.fetch=(url,options)=>{if(String(url).endsWith('/pairs')){signal=options.signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));}return previous(url,options);};
        const visible=node.widgets.find(w=>w.name==='negative_text').inputEl;
        [...document.querySelectorAll('button')].find(button=>button.textContent==='Save pair').click();
        node.onRemoved();
        return {aborted:signal.aborted,restored:node.widgets.find(w=>w.name==='negative_text').inputEl!==visible};
    });
    expect(state).toEqual({aborted:true,restored:true});
});

test('actual pair saves coexist with resetting the disposable browser fixture',async({page,request})=>{
    await draft(page,'Actual browser pair','Actual negative');await page.getByRole('button',{name:'Save pair',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'Pair saved'})).toBeVisible();
    const response=await request.get('/prompt_manager/pairs?text=Actual%20browser%20pair');expect(response.ok()).toBe(true);expect((await response.json()).pairs).toEqual(expect.arrayContaining([expect.objectContaining({positive_text:'Actual browser pair',negative_text:'Actual negative'})]));
    const reset=await request.post('/__test__/reset');expect(reset.ok()).toBe(true);
    const after=await request.get('/prompt_manager/pairs?text=Actual%20browser%20pair');expect((await after.json()).pairs).toHaveLength(0);
});
