import {readFileSync} from 'node:fs';
import {test,expect} from './fixtures.mjs';
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
            constructor(){this.id=1;this.type='PromptManagerPair';this.widgets=[...['text','negative_text','category','tags','search_text'].map(name=>({name,type:'text',value:'',options:{},inputEl:document.createElement('textarea')})),{name:'save_to_library',type:'toggle',value:true,options:{}}];this.inputs=[...['clip','prepend_text','append_text'].map(name=>({name,type:name==='clip'?'CLIP':'STRING',link:null})),...['text','negative_text','category','tags','search_text'].map(name=>({name,type:'STRING',link:null,widget:{name}})),{name:'save_to_library',type:'BOOLEAN',link:null,widget:{name:'save_to_library'}}];this.properties={};this.size=[1100,700];}
            findInputByType(type){const index=this.inputs.findIndex(input=>input.type===type&&input.link==null);return index<0?undefined:{index,slot:this.inputs[index]};}
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

test('paired editor Save to library switch keeps the widget order and loads older workflows on (#90)',async({page})=>{
    await expect(page.locator('.pm-pair')).toBeVisible();
    const toggle=page.getByRole('checkbox',{name:'Save to library',exact:true});
    await expect(toggle).toBeChecked();
    expect(await page.evaluate(()=>node.widgets.map(widget=>widget.name))).toEqual(['text','negative_text','category','tags','search_text','prepend_text','append_text','save_to_library']);
    await toggle.uncheck();
    expect(await page.evaluate(()=>node.serialize().widgets_values[7])).toBe(false);
    await expect(page.locator('.pm-pair > .pm-editor-status[role=status]').last()).toHaveText('Save to library is off: runs of this node are not added to the library.');
    await page.evaluate(()=>node.configure({widgets_values:['P','N','','','','','',''],properties:{}}));
    await expect(toggle).toBeChecked();
});

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

test('paired editor offers positive and negative link sockets and keeps both typed texts (#343)',async({page})=>{
    await draft(page,'Typed positive','Typed negative');
    expect(await page.evaluate(()=>node.inputs.filter(input=>!input.widget).map(input=>input.name))).toEqual(['clip','prepend_text','append_text','text','negative_text']);
    const target=()=>page.evaluate(()=>node.findInputByType('STRING').slot.name);
    expect(await target()).toBe('text');
    const positive=page.getByRole('textbox',{name:'Positive prompt',exact:true}),negative=page.getByRole('textbox',{name:'Negative prompt',exact:true});
    const positiveNote=page.getByText('Positive prompt comes from a link. Your typed text is kept for when you disconnect it.',{exact:true});
    const negativeNote=page.getByText('Negative prompt comes from a link. Your typed text is kept for when you disconnect it.',{exact:true});
    const link=(name,value)=>page.evaluate(({name,value})=>{node.inputs.find(input=>input.name===name).link=value;node.onConnectionsChange();},{name,value});
    for(const [linked,next] of [[['text'],'negative_text'],[['negative_text'],'text'],[['text','negative_text'],'prepend_text'],[[],'text']]){
        for(const name of ['text','negative_text'])await link(name,linked.includes(name)?30:null);
        expect(await target()).toBe(next);
        await expect(positive).toBeVisible({visible:!linked.includes('text')});
        await expect(negative).toBeVisible({visible:!linked.includes('negative_text')});
        await expect(positiveNote).toBeVisible({visible:linked.includes('text')});
        await expect(negativeNote).toBeVisible({visible:linked.includes('negative_text')});
        const saved=await page.evaluate(()=>{const saved=node.serialize();for(const input of node.inputs)if(['text','negative_text'].includes(input.name))input.widget={name:input.name};node.configure(saved);return saved.widgets_values.slice(0,2);});
        expect(saved).toEqual(['Typed positive','Typed negative']);
        expect(await page.evaluate(()=>node.inputs.filter(input=>input.widget&&['text','negative_text'].includes(input.name)).length)).toBe(0);
    }
    await expect(positive).toHaveValue('Typed positive');await expect(negative).toHaveValue('Typed negative');
});

test('a link dropped on either prompt box, or its note, fills that box (#343 review M1)',async({page})=>{
    const keys=await page.evaluate(()=>[...document.querySelectorAll('.pm-pair .lg-node-widget [data-slot-key]')].map(key=>key.dataset.slotKey));
    // Nodes 2.0 resolves a drop through the nearest .lg-node-widget and its slot key: positive box and note, negative box and note.
    expect(keys).toEqual(['1-in-3','1-in-3','1-in-4','1-in-4']);
    const at=(name,connecting=true)=>page.evaluate(({name,connecting})=>{
        fixtureApp.canvas={linkConnector:{state:{connectingTo:connecting?'input':undefined}},ds:{convertOffsetToCanvas:point=>point},canvas:{getBoundingClientRect:()=>({left:0,top:0})}};
        const box=[...document.querySelectorAll('.pm-pair .lg-node-widget')].find(element=>element.getClientRects().length&&element.textContent.includes(name)).getBoundingClientRect();
        return node.getInputOnPos([box.left+box.width/2,box.top+box.height/2])?.name??null;
    },{name,connecting});
    expect(await at('Negative prompt')).toBe('negative_text');
    expect(await at('Positive prompt')).toBe('text');
    expect(await at('Negative prompt',false)).toBeNull();
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='negative_text').link=4;node.onConnectionsChange();});
    expect(await at('Negative prompt comes from a link')).toBe('negative_text');
    expect(await page.evaluate(()=>{fixtureApp.canvas={};return node.getInputOnPos([1,1])??null;})).toBeNull();
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

for(const field of ['category','tags'])test(`manual pair save waits for execution when ${field} comes from an upstream link`,async({page})=>{
    let requests=0;await page.route('**/prompt_manager/pairs',route=>{requests++;return route.fulfill({json:{success:true,pair}});});await draft(page);
    await page.evaluate(field=>{node.inputs.push({name:field,link:19});node.onConnectionsChange();},field);
    const save=page.getByRole('button',{name:'Save pair',exact:true});await expect(save).toBeDisabled();await expect(page.getByRole('status').filter({hasText:'Run the node to save connected values.'})).toBeVisible();
    await save.evaluate(button=>button.dispatchEvent(new Event('click',{bubbles:true})));expect(requests).toBe(0);
    await page.evaluate(()=>{node.inputs=node.inputs.filter(input=>input.link!==19);node.onConnectionsChange();});await expect(save).toBeEnabled();await save.click();await expect.poll(()=>requests).toBe(1);
});

for(const field of ['positive','negative','category','tags'])test(`save completion identifies an earlier draft after editing ${field}`,async({page})=>{
    let release;await page.route('**/prompt_manager/pairs',async route=>{await new Promise(resolve=>release=resolve);await route.fulfill({json:{success:true,pair}});});await draft(page);
    await page.getByText('Metadata · category / tags',{exact:true}).click();await page.getByRole('button',{name:'Save pair',exact:true}).click();await expect.poll(()=>Boolean(release)).toBe(true);
    const input=field==='positive'?page.getByRole('textbox',{name:'Positive prompt',exact:true}):field==='negative'?page.getByRole('textbox',{name:'Negative prompt',exact:true}):page.getByRole('combobox',{name:field==='category'?'Category':'Tags',exact:true});
    await input.fill(`Changed ${field}`);release();await expect(page.getByRole('status').filter({hasText:'Earlier draft saved. Current changes are not saved yet.'})).toBeVisible();await expect(input).toHaveValue(`Changed ${field}`);
});

test('pair library rows distinguish negative variants and explicitly show empty negatives as text',async({page})=>{
    const longNegative='Variant one '+ 'x'.repeat(200);
    await page.route('**/prompt_manager/pairs?**',route=>route.fulfill({json:{success:true,pairs:[{...pair,negative_text:longNegative},{...pair,id:5,negative_text:'Variant two'},{...pair,id:6,negative_text:''},{...pair,id:7,negative_text:'<img src=x onerror=alert(1)>'}]}}));
    await page.getByRole('button',{name:'Recent',exact:true}).click();const rows=page.locator('.pm-editor-result');await expect(rows).toHaveCount(4);await expect(rows.nth(0)).toContainText('Negative: Variant one');await expect(rows.nth(1)).toContainText('Negative: Variant two');await expect(rows.nth(2)).toContainText('Negative: (empty)');await expect(rows.nth(3)).toContainText('Negative: <img src=x onerror=alert(1)>');await expect(rows.locator('img')).toHaveCount(0);await expect(rows.nth(0)).not.toContainText('x'.repeat(180));await expect(rows.nth(0).locator('span').nth(1)).toHaveAttribute('title',longNegative);
});
