import {readFileSync} from 'node:fs';
import {test,expect} from './fixtures.mjs';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';

for (const variant of ['Focused', 'Split']) {
test.describe(variant, () => {
const isSplit = variant === 'Split';
const uiProperty = isSplit ? 'splitUI' : 'focusedUI';
async function openLibrary(page) {
    if (isSplit) {
        await expect(page.locator('.pm-editor-library')).toBeVisible();
        await expect(page.getByRole('button',{name:'Browse library',exact:true})).toHaveCount(0);
    } else await page.getByRole('button',{name:'Browse library',exact:true}).click();
}

test.beforeEach(async({page,browserName})=>{
    await page.addInitScript(()=>Object.defineProperty(Crypto.prototype,'randomUUID',{value:undefined,configurable:true}));
    await startCoverage(page,browserName);
    page.focusedErrors=[];page.on('pageerror',error=>page.focusedErrors.push(error.message));
    await page.route('**/scripts/app.js',route=>route.fulfill({contentType:'text/javascript',body:'export const app=window.fixtureApp;'}));
    await page.route('**/scripts/api.js',route=>route.fulfill({contentType:'text/javascript',body:'export const api={fileURL:path=>path,queuePrompt:async(...args)=>args};'}));
    await page.route('**/extensions/promptmanager/prompt_manager.js',route=>route.fulfill({contentType:'text/javascript',body:readFileSync(new URL('../../web/comfy/prompt_manager.js',import.meta.url),'utf8')}));
    await page.goto('/prompt_manager/test');
    await page.evaluate(async variant=>{
        document.body.replaceChildren();document.body.style.setProperty('--bg-color','#222');
        window.fixtureApp={registerExtension(extension){this.extension=extension;},ui:{settings:new EventTarget()},graph:{setDirtyCanvas(){}},canvas:{}};
        await import('/extensions/promptmanager/prompt_manager.js');
        class Node {
            constructor(){this.widgets=[...['text','category','tags','search_text'].map(name=>({name,type:'text',value:'',options:{},inputEl:document.createElement('textarea')})),{name:'save_to_library',type:'toggle',value:true,options:{}}];this.inputs=[...['clip','prepend_text','append_text'].map(name=>({name,type:name==='clip'?'CLIP':'STRING',link:null})),...['text','category','tags','search_text'].map(name=>({name,type:'STRING',link:null,widget:{name}})),{name:'save_to_library',type:'BOOLEAN',link:null,widget:{name:'save_to_library'}}];this.properties={};this.size=[550,700];}
            findInputByType(type){const index=this.inputs.findIndex(input=>input.type===type&&input.link==null);return index<0?undefined:{index,slot:this.inputs[index]};}
            addWidget(type,name,value,callback,options){const widget={type,name,value,callback,options};this.widgets.push(widget);return widget;}
            addDOMWidget(name,type,element){const host=document.createElement('div');host.style.height='650px';host.className='fixture-dom-widget';host.append(element);document.body.append(host);return {element};}
            setSize(size){this.size=size;}setDirtyCanvas(){}
            serialize(){return {widgets_values:this.widgets.map(widget=>widget.value),properties:{...this.properties}};}
            configure(data){this.widgets.forEach((widget,index)=>{widget.value=data.widgets_values[index];});this.properties={...data.properties};}
        }
        await fixtureApp.extension.beforeRegisterNodeDef(Node,{name:`PromptManager${variant}`},fixtureApp);
        window.node=new Node();node.onNodeCreated();
    },variant);
    await expect(page.locator('.pm-focused')).toBeVisible();
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.focusedErrors).toEqual([]);});

test(`${variant} Save to library switch saves its value and loads older workflows on (#90)`, async ({page}) => {
    const toggle=page.getByRole('checkbox',{name:'Save to library',exact:true});
    await expect(toggle).toBeChecked();
    expect(await page.evaluate(()=>node.widgets.map(widget=>widget.name))).toEqual(['text','category','tags','search_text','prepend_text','append_text','save_to_library']);
    await toggle.uncheck();
    await expect(page.locator('.pm-focused > .pm-editor-status[role=status]')).toHaveText('Save to library is off: runs of this node are not added to the library.');
    const saved=await page.evaluate(()=>node.serialize());
    expect(saved.widgets_values[6]).toBe(false);
    await page.evaluate(saved=>node.configure(saved),saved);
    await expect(toggle).not.toBeChecked();
    await page.getByLabel('Main prompt',{exact:true}).fill('Typed while off');
    await expect(page.locator('.pm-focused > .pm-editor-status[role=status]')).toHaveText('Save to library is off: runs of this node are not added to the library.');
    // A workflow saved before the switch held the editor's own slot ('') at its index
    await page.evaluate(()=>node.configure({widgets_values:['Old text','','','','','',''],properties:{}}));
    await expect(toggle).toBeChecked();
    expect(await page.evaluate(()=>node.widgets.find(widget=>widget.name==='save_to_library').value)).toBe(true);
    await toggle.check();
    await page.getByLabel('Main prompt',{exact:true}).fill('Typed while on');
    await expect(page.locator('.pm-focused > .pm-editor-status[role=status]')).toHaveText('Main prompt is saved when this node runs.');
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='save_to_library').link=9;node.onConnectionsChange?.();});
    await expect(toggle).toBeDisabled();
});

test(`${variant} library autocompletes filters and searches without changing editor metadata`, async ({page}) => {
    const requests=[];
    await page.route('**/prompt_manager/categories',route=>route.fulfill({json:{success:true,categories:['Portrait']}}));
    await page.route('**/prompt_manager/tags/suggest?**',route=>route.fulfill({json:{success:true,suggestions:[{name:'studio'}]}}));
    await page.route('**/prompt_manager/search?**',route=>{
        requests.push(new URL(route.request().url()));
        return route.fulfill({json:{success:true,results:[{text:'Filtered portrait',category:'Portrait',tags:['studio']}]}});
    });
    await page.getByLabel('Main prompt',{exact:true}).fill('My draft');
    await openLibrary(page);
    const category=page.getByLabel('Filter category',{exact:true});
    await category.fill('por');
    await expect(page.getByRole('option',{name:'Portrait',exact:true})).toBeVisible();
    await category.press('ArrowDown');
    await category.press('Enter');
    expect(requests).toHaveLength(0);
    const tags=page.getByLabel('Filter tags',{exact:true});
    await tags.fill('stu');
    await page.getByRole('option',{name:'studio',exact:true}).click();
    await expect(tags).toHaveValue('studio, ');
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('portrait tag:studio tag:"film grain"');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await expect(page.locator('.pm-editor-result')).toContainText('Filtered portrait');
    expect(requests[0].searchParams.get('text')).toBe('portrait');
    expect(requests[0].searchParams.get('category')).toBe('Portrait');
    expect(requests[0].searchParams.get('tags')).toBe('studio,film grain');
    expect(await page.evaluate(()=>node.serialize().widgets_values.slice(0,3))).toEqual(['My draft','','']);
    await page.locator('.pm-editor-result').click();
    await category.fill('Landscape');
    await expect(page.locator('.pm-editor-selection button')).toBeDisabled();
    const saved=await page.evaluate(()=>{const saved=node.serialize();node.configure(saved);return saved;});
    expect(saved.properties[uiProperty].libraryFilters).toEqual({category:'Landscape',tags:'studio, '});
    await expect(category).toHaveValue('Landscape');
    await expect(tags).toHaveValue('studio, ');
    let recentRequest;
    await page.route('**/prompt_manager/recent?**',route=>{recentRequest=new URL(route.request().url());return route.fulfill({json:{success:true,results:[]}});});
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await expect.poll(()=>Boolean(recentRequest)).toBe(true);
    expect(recentRequest.searchParams.has('category')).toBe(false);
    expect(recentRequest.searchParams.has('tags')).toBe(false);
    if (isSplit) await page.getByRole('searchbox',{name:'Search saved prompts'}).focus();
    else await page.getByRole('button',{name:'Hide library',exact:true}).click();
    await expect(page.getByRole('listbox')).toBeHidden();
});

test(`${variant} library can search by filters alone and Enter searches after autocomplete closes`, async ({page}) => {
    const requests=[];
    await page.route('**/prompt_manager/search?**',route=>{requests.push(new URL(route.request().url()));return route.fulfill({json:{success:true,results:[]}});});
    await openLibrary(page);
    await page.getByLabel('Filter category',{exact:true}).fill('New category');
    await page.getByLabel('Filter category',{exact:true}).press('Enter');
    await expect.poll(()=>requests.length).toBe(1);
    expect(requests[0].searchParams.get('category')).toBe('New category');
    await page.getByLabel('Filter category',{exact:true}).fill('');
    await page.getByLabel('Filter tags',{exact:true}).fill('custom, ');
    await page.getByLabel('Filter tags',{exact:true}).press('Enter');
    await expect.poll(()=>requests.length).toBe(2);
    expect(requests[1].searchParams.get('tags')).toBe('custom');
});

test(`${variant} completes saved tags with keyboard and pointer and appends commas`, async ({page}) => {
    await page.route('**/prompt_manager/tags/suggest?**', route => {
        const q = new URL(route.request().url()).searchParams.get('q');
        return route.fulfill({json:{success:true,suggestions:(q === 'bl' ? ['blue eyes','blue sky'] : ['portrait','blue eyes']).map(name => ({name}))}});
    });
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    const tags = page.getByLabel('Tags',{exact:true});
    await tags.fill('portrait, bl');
    await expect(page.getByRole('option',{name:'blue eyes',exact:true})).toBeVisible();
    await tags.press('ArrowDown');
    await tags.press('Enter');
    await expect(tags).toHaveValue('portrait, blue eyes, ');
    expect(await page.evaluate(()=>node.serialize().widgets_values[2])).toBe('portrait, blue eyes, ');
    await tags.fill('bl');
    await page.getByRole('option',{name:'blue sky',exact:true}).click();
    await expect(tags).toHaveValue('blue sky, ');
    await tags.fill('portrait, ');
    await expect(page.getByRole('option',{name:'portrait',exact:true})).toHaveCount(0);
    await expect(page.getByRole('option',{name:'blue eyes',exact:true})).toBeVisible();
    await tags.press('Escape');
    await expect(page.getByRole('listbox')).toBeHidden();
    await tags.fill('bl');
    await expect(page.getByRole('option',{name:'blue eyes',exact:true})).toBeVisible();
    await page.locator('.pm-focused').dispatchEvent('scroll');
    await expect(page.getByRole('listbox')).toBeHidden();
    await tags.fill('A brand new tag');
    await tags.press('Tab');
    await expect(tags).toHaveValue('A brand new tag');
});

test(`${variant} focused node tag suggestions include a newly created tag`, async ({page,request}) => {
    const name=`created_${variant.toLowerCase()}_vocabulary`;
    const listing=await request.get('/prompt_manager/vocabulary/tags?limit=1');await expect(listing).toBeOK();
    const created=await request.post('/prompt_manager/vocabulary/tags',{data:{expected_epoch:(await listing.json()).library_epoch,name}});
    await expect(created).toBeOK();expect((await created.json()).tag).toMatchObject({name,prompt_count:0});
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    const tags=page.getByLabel('Tags',{exact:true});
    await tags.fill(`created_${variant.toLowerCase()}`);
    await expect(page.getByRole('option',{name,exact:true})).toBeVisible();
    await tags.press('ArrowDown');await tags.press('Enter');
    await expect(tags).toHaveValue(`${name}, `);
});

test(`${variant} metadata autocomplete ignores stale responses, recovers from errors and cleans up`, async ({page}) => {
    let release;
    let mode='delayed';
    await page.route('**/prompt_manager/tags/suggest?**', async route => {
        const q = new URL(route.request().url()).searchParams.get('q');
        if(q === 'old') await new Promise(resolve=>{release=resolve;});
        if(mode === 'error') return route.fulfill({status:503,body:'Unavailable'});
        await route.fulfill({json:{success:true,suggestions:[{name:q+' result'}]}}).catch(()=>{});
    });
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    const tags=page.getByLabel('Tags',{exact:true});
    await tags.fill('old');
    await expect.poll(()=>Boolean(release)).toBe(true);
    await tags.fill('new');
    await expect(page.getByRole('option',{name:'new result',exact:true})).toBeVisible();
    release();
    await expect(page.getByRole('option',{name:'old result',exact:true})).toHaveCount(0);
    mode='error';
    await tags.fill('unavailable');
    await expect(page.getByRole('listbox')).toBeHidden();
    await expect(tags).toHaveValue('unavailable');
    mode='ok';
    await tags.fill('retry');
    await expect(page.getByRole('option',{name:'retry result',exact:true})).toBeVisible();
    await page.evaluate(()=>node.onRemoved());
    await expect(page.getByRole('listbox')).toHaveCount(0);
});

test(`${variant} completes one category, allows new values and dismisses suggestions on blur`, async ({page}) => {
    await page.route('**/prompt_manager/categories',route=>route.fulfill({json:{success:true,categories:['Portrait','Landscape','Portrait studio']}}));
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    const category=page.getByLabel('Category',{exact:true});
    await category.fill('por');
    await expect(page.getByRole('listbox',{name:'Saved categories'})).toBeVisible();
    await category.press('ArrowUp');
    await expect(page.getByRole('option',{name:'Portrait studio',exact:true})).toHaveAttribute('aria-selected','true');
    await category.press('Enter');
    await expect(category).toHaveValue('Portrait studio');
    expect(await page.evaluate(()=>node.serialize().widgets_values[1])).toBe('Portrait studio');
    await category.fill('por');
    await expect(page.getByRole('option',{name:'Portrait',exact:true})).toBeVisible();
    await category.dispatchEvent('keydown',{key:'ArrowDown',isComposing:true});
    await expect(category).not.toHaveAttribute('aria-activedescendant',/.+/);
    await page.getByLabel('Tags',{exact:true}).focus();
    await expect(page.getByRole('listbox',{name:'Saved categories'})).toBeHidden();
    await category.fill('New category');
    await expect(page.getByRole('listbox',{name:'Saved categories'})).toBeHidden();
    await expect(category).toHaveValue('New category');
});

test(`${variant} shares suggestion controls safely with another bundled toolkit copy`, async ({page}) => {
    await page.evaluate(async()=>{
        const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
        const first=await import('/prompt_manager/js/shared/toolkit/suggestions.js?pack=first');
        const second=await import('/prompt_manager/js/shared/toolkit/suggestions.js?pack=second');
        window.completionFixture={scope:createScope(),selected:[],inserted:[]};
        const one=first.createSuggestionView(completionFixture.scope,null);
        const two=second.createSuggestionView(completionFixture.scope,null,{label:'Other pack'});
        completionFixture.views=[one,two];
        one.hide();
        const items=[{name:'plain'},{name:'tag',type:'tag'},{name:'model',type:'lora'},{name:'nested',type:'embedding',fullPath:'folder/nested'},{name:'flat',type:'embedding',fullPath:'flat'}];
        one.render(items,index=>{completionFixture.selected.push(index);one.select(index);},item=>completionFixture.inserted.push(item.name));
        one.select(-1);
        one.position({left:innerWidth-5,bottom:innerHeight,top:innerHeight-20});
        two.render([{name:'Other pack suggestion'}],()=>{},()=>{});
        two.position({left:0,bottom:5,top:0});
    });
    const lists=page.getByRole('listbox');
    const ids=await lists.evaluateAll(elements=>elements.map(element=>element.id));
    expect(new Set(ids).size).toBe(ids.length);
    await page.getByRole('option',{name:'[LORA] model'}).hover();
    await expect(page.getByRole('option',{name:'[LORA] model'})).toHaveAttribute('aria-selected','true');
    await page.getByRole('option',{name:'[EMB] folder/nested'}).click();
    expect(await page.evaluate(()=>completionFixture.inserted)).toEqual(['nested']);
    await page.evaluate(()=>completionFixture.scope.dispose());
    await expect(lists).toHaveCount(0);
});

test(`${variant} previews before loading, keeps metadata optional and composes text`,async({page})=>{
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{id:1,text:'Library portrait',category:'Portrait',tags:['studio']}]}}));
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('My draft');
    await openLibrary(page);
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

test(`${variant} restores workflow values and disclosures and protects connected text`,async({page})=>{
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('Saved draft');
    await page.getByText('Metadata · category / tags',{exact:true}).click();
    await page.getByLabel('Category',{exact:true}).fill('Saved category');
    await page.evaluate(()=>{const saved=node.serialize();node.configure(saved);});
    await expect(page.getByLabel('Category',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Category',{exact:true})).toHaveValue('Saved category');
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='text').link=9;node.onConnectionsChange();});
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toBeHidden();
    await expect(page.getByText('Main prompt comes from a link. Your typed text is kept for when you disconnect it.',{exact:true})).toBeVisible();
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='text').link=null;node.onConnectionsChange();});
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('Saved draft');
});

test(`${variant} offers text as a link socket, keeps typed text while linked and through a reload (#343)`,async({page})=>{
    const sockets=()=>page.evaluate(()=>Object.fromEntries(node.inputs.map(input=>[input.name,input.widget?.name??null])));
    // The hidden text widget no longer owns the input, so the host draws it as a socket; the other widget inputs are unchanged.
    expect(await sockets()).toEqual({clip:null,prepend_text:null,append_text:null,text:null,category:'category',tags:'tags',search_text:'search_text',save_to_library:'save_to_library'});
    expect(await page.evaluate(()=>node.findInputByType('STRING').slot.name)).toBe('text');
    expect(await page.evaluate(()=>node.findInputByType('CLIP').slot.name)).toBe('clip');
    const main=page.getByRole('textbox',{name:'Main prompt',exact:true});
    await main.fill('Typed main');
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='text').link=21;node.onConnectionsChange();});
    await expect(main).toBeHidden();
    const note=page.getByText('Main prompt comes from a link. Your typed text is kept for when you disconnect it.',{exact:true});
    await expect(note).toBeVisible();
    await expect(page.locator('[data-composed-preview]')).toHaveText('[text supplied by connection]');
    // A second STRING link dropped on the editor falls back to the frontend's own choice.
    expect(await page.evaluate(()=>node.findInputByType('STRING').slot.name)).toBe('prepend_text');
    // An older saved workflow brings the widget binding back with its inputs: configure exposes the socket again.
    const saved=await page.evaluate(()=>{const saved=node.serialize();node.inputs.find(input=>input.name==='text').widget={name:'text'};node.configure(saved);return saved;});
    expect(saved.widgets_values[0]).toBe('Typed main');
    expect((await sockets()).text).toBeNull();
    await expect(note).toBeVisible();
    await page.evaluate(()=>{node.inputs.find(input=>input.name==='text').link=null;node.onConnectionsChange();});
    await expect(main).toBeVisible();
    await expect(main).toHaveValue('Typed main');
    await expect(note).toBeHidden();
});

test(`${variant} searches quoted tags with Enter and explicitly loads metadata`, async ({page}) => {
    let requested;
    await page.route('**/prompt_manager/search?**', async route => {
        requested = new URL(route.request().url());
        await route.fulfill({json:{success:true,results:[{id:7,text:'Selected fox',category:'Animals',tags:['film grain','fox']}]}});
    });
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('Preserved draft');
    await openLibrary(page);
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

test(`${variant} reports empty queries, no results, and safely renders backend and HTTP errors`, async ({page}) => {
    let requests=0;
    let mode='empty';
    const malicious='<img src=x onerror="window.focusedInjected=true">';
    await page.route('**/prompt_manager/search?**', async route => {
        requests++;
        if(mode==='http') return route.fulfill({status:503,body:'Unavailable'});
        await route.fulfill({json:mode==='error'?{success:false,error:malicious}:{success:true,results:[]}});
    });
    await openLibrary(page);
    const search=page.getByRole('button',{name:'Search',exact:true});
    await search.click();
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('Enter search text, a category or tags.');
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
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('Enter search text, a category or tags.');
    mode='empty';
    await page.getByRole('searchbox',{name:'Search saved prompts'}).fill('missing');
    await search.click();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.locator('.pm-editor-library [role="status"]')).toHaveText('No matching prompts.');
});

test(`${variant} treats saved prompt content and metadata as text`, async ({page}) => {
    const malicious='<img src=x onerror="window.focusedInjected=true">';
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{id:8,text:malicious,category:'<script>bad()</script>',tags:['<svg onload=bad()>']}]}}));
    await openLibrary(page);
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

test(`${variant} keeps the latest result when searches and recent requests overlap`, async ({page}) => {
    await deferLibraryRequests(page);
    await openLibrary(page);
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

test(`${variant} removal aborts work and stops theme and control listeners`, async ({page}) => {
    await deferLibraryRequests(page);
    await openLibrary(page);
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
    if (isSplit) {
        const count=await page.evaluate(()=>libraryRequests.length);
        await page.getByRole('button',{name:'Recent',exact:true}).click();
        expect(await page.evaluate(()=>libraryRequests.length)).toBe(count);
        await expect(page.locator('.pm-editor-library')).toBeVisible();
    } else {
        await page.getByRole('button',{name:'Hide library',exact:true}).click();
        await expect(page.getByRole('button',{name:'Hide library',exact:true})).toBeVisible();
    }
});

test(`${variant} fields reflow with node width and follow live host themes`, async ({page}) => {
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

test(`${variant} composed preview preserves main prompt whitespace like queued execution`, async ({page}) => {
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('  draft  ');
    await page.getByText('Composition · prepend / append',{exact:true}).click();
    await page.getByLabel('Prepend',{exact:true}).fill(' trigger ');
    await page.getByLabel('Append',{exact:true}).fill(' detail ');
    await page.getByText('Composed text preview',{exact:true}).click();
    expect(await page.locator('[data-composed-preview]').textContent()).toBe('trigger   draft   detail');
});

test(`${variant} exposes the visible text element for autocomplete and host extension listeners`, async ({page}) => {
    expect(await page.evaluate(()=>node.widgets.find(widget=>widget.name==='text').inputEl===document.querySelector('.pm-focused textarea'))).toBe(true);
});

test(`${variant} expands its main editor when the node gets taller`,async({page})=>{
    await page.locator('.pm-focused').evaluate((element,isSplit)=>{element.style.width=isSplit?'900px':'550px';element.style.height='800px';},isSplit);
    const editor=await page.getByRole('textbox',{name:'Main prompt',exact:true}).boundingBox();
    expect(editor.height).toBeGreaterThan(400);
});

test(`${variant} respects the existing Web UI display preference`,async({page})=>{
    await page.route('**/prompt_manager/settings',route=>route.fulfill({json:{success:true,settings:{webui_display_mode:'newtab'}}}));
    await page.evaluate(()=>{
        const Node=node.constructor;node.onRemoved();node._focusedView.element.remove();node=new Node();node.onNodeCreated();
        window.open=(...args)=>{window.openArgs=args;return {};};
    });
    await page.evaluate(()=>node._focusedSettingsReady);
    await page.getByRole('button',{name:'Web UI ↗',exact:true}).click();
    expect(await page.evaluate(()=>window.openArgs)).toEqual(['/prompt_manager/web','_blank']);
});

test(`${variant} reports blocked Web UI popups without leaving the workflow`,async({page})=>{
    await page.evaluate(()=>{window.open=()=>null;});
    await page.getByRole('button',{name:'Web UI ↗',exact:true}).click();
    await expect(page.locator('.pm-focused [role="alert"]')).toContainText('Allow popups');
    await expect(page.locator('.pm-focused')).toBeVisible();
});

if (isSplit) test('Split keeps the library beside the editor when wide and stacks it when narrow', async ({page}) => {
    await page.setViewportSize({width:1200,height:1000});
    const root=page.locator('.pm-split');
    await expect(root).toHaveAttribute('aria-label','Split prompt editor');
    await expect(page.getByRole('button',{name:'Hide library',exact:true})).toHaveCount(0);
    await root.evaluate(element=>{element.style.width='900px';element.style.height='800px';});
    const compose=root.locator('.pm-editor-workspace > .pm-editor-compose');
    const library=root.locator('.pm-editor-workspace > .pm-editor-library');
    await expect(compose.locator('.pm-editor-main')).toBeVisible();
    let a=await compose.boundingBox(),b=await library.boundingBox();
    expect(Math.abs(a.y-b.y)).toBeLessThan(2);
    expect(b.x).toBeGreaterThanOrEqual(a.x+a.width);
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:[{text:'Library example'}]}}));
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    await expect(page.locator('.pm-editor-result')).toContainText('Library example');
    await page.getByRole('textbox',{name:'Main prompt',exact:true}).fill('Editing beside the library');
    await expect(library).toBeVisible();
    await expect(page.locator('.pm-editor-result')).toContainText('Library example');
    expect(await page.evaluate(()=>node.serialize().widgets_values[0])).toBe('Editing beside the library');
    await root.evaluate(element=>{element.style.width='340px';});
    await expect.poll(async()=>{const a=await compose.boundingBox(),b=await library.boundingBox();return b.y>=a.y+a.height;}).toBe(true);
    expect(await root.evaluate(element=>element.scrollWidth<=element.clientWidth+1)).toBe(true);
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('Editing beside the library');
    await expect(library).toBeVisible();
});
if (isSplit) test('Split bounds long recent results and selected previews at the default node size', async ({page}) => {
    await page.setViewportSize({width:1200,height:1000});
    const root=page.locator('.pm-split');
    await root.evaluate(element=>{element.style.width='900px';element.style.height='650px';});
    const longPrompt='Long saved prompt '+('detailed portrait in a studio with natural light '.repeat(110));
    const prompts=Array.from({length:20},(_,index)=>({id:index+1,text:index===0?longPrompt:`Saved prompt ${index+1}`,category:'Portrait',tags:['studio']}));
    await page.route('**/prompt_manager/recent?**',route=>route.fulfill({json:{success:true,results:prompts}}));
    await page.getByRole('button',{name:'Recent',exact:true}).click();
    const results=root.locator('.pm-editor-results');
    await expect(results.locator('.pm-editor-result')).toHaveCount(20);
    const rowHeights=await results.locator('.pm-editor-result > span:first-child').evaluateAll(elements=>elements.map(element=>element.getBoundingClientRect().height));
    expect(rowHeights.every(height=>height>0)).toBe(true);
    const resultSize=await results.evaluate(element=>({height:element.getBoundingClientRect().height,scroll:element.scrollHeight,client:element.clientHeight}));
    expect.soft(resultSize.height).toBeLessThanOrEqual(300);
    expect(resultSize.scroll).toBeGreaterThan(resultSize.client);
    await results.locator('.pm-editor-result').first().click();
    const preview=root.locator('.pm-editor-selection > .pm-editor-preview');
    await expect(preview).toHaveText(longPrompt);
    const previewSize=await preview.evaluate(element=>({height:element.getBoundingClientRect().height,scroll:element.scrollHeight,client:element.clientHeight}));
    expect.soft(previewSize.height).toBeLessThanOrEqual(180);
    expect.soft(previewSize.scroll).toBeGreaterThan(previewSize.client);
    await page.getByRole('button',{name:'Use prompt',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue(longPrompt);
    const bounds=await root.boundingBox();
    for (const selector of ['.pm-editor-compose','.pm-editor-library']) {
        const pane=await root.locator(selector).boundingBox();
        expect(pane.y).toBeGreaterThanOrEqual(bounds.y);
        expect(pane.y+pane.height).toBeLessThanOrEqual(bounds.y+bounds.height);
    }
    expect(await root.evaluate(element=>element.scrollWidth<=element.clientWidth+1)).toBe(true);
});
});
}
