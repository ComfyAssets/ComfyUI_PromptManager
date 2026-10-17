import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {installHostHooks} from './host-fixture.mjs';

installHostHooks(test,['PromptManagerPair']);
async function addPair(page){return page.evaluate(async()=>{const {app}=await import('/scripts/app.js');const node=LiteGraph.createNode('PromptManagerPair');app.graph.add(node);app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);return node.id;});}
async function seed(request,positive,negative){const response=await request.post('/prompt_manager/pairs',{data:{positive_text:positive,negative_text:negative,category:'Paired host tests',tags:['pair-host']}});expect(response.ok()).toBe(true);return (await response.json()).pair;}
async function values(page,id){return page.evaluate(async id=>{const {app}=await import('/scripts/app.js');return Object.fromEntries(app.graph.getNodeById(id).widgets.map(w=>[w.name,w.value]));},id);}
async function select(page,text){const root=page.locator('.pm-pair');await root.getByRole('button',{name:'Recent',exact:true}).click();await root.locator('.pm-editor-result').filter({has:page.getByText(text,{exact:true})}).click();return root;}

test('paired definition exposes both conditioning/text outputs and positive-only composition',async({request})=>{
    const definitions=await(await request.get('/object_info')).json();const pair=definitions.PromptManagerPair;
    expect(pair).toBeDefined();expect(pair.output).toEqual(['CONDITIONING','CONDITIONING','STRING','STRING']);
    expect(pair.input.required.text[0]).toBe('STRING');expect(pair.input.required.negative_text[0]).toBe('STRING');expect(pair.input.required.clip[0]).toBe('CLIP');
    for(const name of ['prepend_text','append_text'])expect(pair.input.optional[name]).toMatchObject(['STRING',{default:'',forceInput:true}]);
    expect(pair.input.optional.negative_prepend_text).toBeUndefined();
});

test('paired library previews both, atomically loads, queues and survives renderer switches and reload',async({page,request},testInfo)=>{
    const positive=`Pair positive ${testInfo.project.name}`;const negative=`Pair negative ${testInfo.project.name}`;await seed(request,positive,negative);const id=await addPair(page);const root=page.locator('.pm-pair');
    const pos=root.getByRole('textbox',{name:'Positive prompt',exact:true});const neg=root.getByRole('textbox',{name:'Negative prompt',exact:true});
    await pos.fill('Local positive');await neg.fill('Local negative');
    for(const [name,field]of [['text',pos],['negative_text',neg]])expect(await field.evaluate(async(element,{id,name})=>{const {app}=await import('/scripts/app.js');return app.graph.getNodeById(id).widgets.find(w=>w.name===name).inputEl===element;},{id,name})).toBe(true);
    await select(page,positive);await expect(root.locator('.pm-editor-selection')).toContainText(negative);await expect(pos).toHaveValue('Local positive');await expect(neg).toHaveValue('Local negative');
    await root.getByRole('button',{name:'Use pair',exact:true}).click();await expect(pos).toHaveValue(positive);await expect(neg).toHaveValue(negative);
    await root.getByText('Composition · prepend / append',{exact:true}).click();await root.getByRole('textbox',{name:'Prepend',exact:true}).fill('trigger');await root.getByRole('textbox',{name:'Append',exact:true}).fill('detail');
    for(const renderer of [!testInfo.project.metadata.initialRenderer,testInfo.project.metadata.initialRenderer]){
        await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);app.canvas.centerOnNode(app.graph._nodes[0]);},renderer);
        await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);await expect(pos).toHaveValue(positive);await expect(neg).toHaveValue(negative);
    }
    const queued=await page.evaluate(async id=>{const {app}=await import('/scripts/app.js');const inputs=(await app.graphToPrompt()).output[String(id)].inputs;await app.loadGraphData(app.graph.serialize());return inputs;},id);
    expect(queued).toMatchObject({text:positive,negative_text:negative,prepend_text:'trigger',append_text:'detail'});await expect(pos).toHaveValue(positive);await expect(neg).toHaveValue(negative);
    await page.screenshot({path:testInfo.outputPath('pair-reloaded.png')});
    await page.evaluate(async id=>{const {app}=await import('/scripts/app.js');app.graph.remove(app.graph.getNodeById(id));},id);await expect(root).toHaveCount(0);await expect(page.locator('.ca-completions')).toHaveCount(0);
});

test('paired real STRING connections stay linked and restore both local editors after disconnect',async({page,request},testInfo)=>{
    const positive=`Connected pair ${testInfo.project.name}`;await seed(request,positive,'Connected library negative');const id=await addPair(page);const root=page.locator('.pm-pair');
    await root.getByRole('textbox',{name:'Positive prompt',exact:true}).fill('Local positive');await root.getByRole('textbox',{name:'Negative prompt',exact:true}).fill('Local negative');
    await root.getByText('Composition · prepend / append',{exact:true}).click();await root.getByRole('textbox',{name:'Prepend',exact:true}).fill('Local prepend');await root.getByRole('textbox',{name:'Append',exact:true}).fill('Local append');
    const links=await page.evaluate(async id=>{const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');const node=app.graph.getNodeById(id);for(const name of ['text','negative_text'])convertToInput(node,node.widgets.find(w=>w.name===name));const result={};for(const name of ['text','negative_text','prepend_text','append_text']){const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);source.widgets.find(w=>w.name==='value').value=`Connected ${name}`;source.connect(0,node,node.inputs.findIndex(i=>i.name===name));result[name]=[String(source.id),0];}app.canvas.centerOnNode(node);return result;},id);
    await select(page,positive);await expect(root.getByRole('button',{name:'Use pair',exact:true})).toBeDisabled();await expect(root.getByRole('button',{name:'Save pair',exact:true})).toBeDisabled();
    for(const name of ['Positive prompt','Negative prompt','Prepend','Append'])await expect(root.getByRole('textbox',{name,exact:true})).toBeHidden();
    const before=await values(page,id);await root.locator('textarea,input').evaluateAll(inputs=>{for(const input of inputs){if(!input.hidden&&!input.closest('[hidden]'))continue;input.value='Overwrite attempt';input.dispatchEvent(new Event('input',{bubbles:true}));}});expect(await values(page,id)).toMatchObject(before);
    for(const reload of [false,true]){const queued=await page.evaluate(async({id,reload})=>{const {app}=await import('/scripts/app.js');if(reload)await app.loadGraphData(app.graph.serialize());return(await app.graphToPrompt()).output[String(id)].inputs;},{id,reload});expect(queued).toMatchObject(links);}
    const local=await page.evaluate(async id=>{const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);for(const name of ['text','negative_text','prepend_text','append_text'])node.disconnectInput(node.inputs.findIndex(i=>i.name===name));return(await app.graphToPrompt()).output[String(id)].inputs;},id);
    expect(local).toMatchObject({text:'Local positive',negative_text:'Local negative',prepend_text:'Local prepend',append_text:'Local append'});
    for(const [name,value]of [['Positive prompt','Local positive'],['Negative prompt','Local negative'],['Prepend','Local prepend'],['Append','Local append']]){const field=root.getByRole('textbox',{name,exact:true});await expect(field).toHaveCount(1);await expect(field).toBeEditable();await expect(field).toHaveValue(value);}
});

test('paired default layout uses equal editors and follows real host themes',async({page},testInfo)=>{
    await addPair(page);const root=page.locator('.pm-pair');await root.getByRole('textbox',{name:'Positive prompt',exact:true}).fill('Positive draft');await root.getByRole('textbox',{name:'Negative prompt',exact:true}).fill('Negative draft');
    for(const palette of ['light','dark']){await page.evaluate(async palette=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.ColorPalette',palette);},palette);await expect(root).toHaveAttribute('data-ca-theme',palette);
        const [p,n]=await Promise.all(['Positive prompt','Negative prompt'].map(name=>root.getByRole('textbox',{name,exact:true}).boundingBox()));expect(Math.abs(p.width-n.width)).toBeLessThan(1);expect(Math.abs(p.height-n.height)).toBeLessThan(1);expect(p.height).toBeGreaterThan(100);
        await page.screenshot({path:testInfo.outputPath(`pair-${palette}.png`)});
    }
});

test('paired explicit Save persists an empty negative through actual API and preserves local draft',async({page,request},testInfo)=>{
    const id=await addPair(page);const root=page.locator('.pm-pair');const text=`Saved pair empty negative ${testInfo.project.name}`;await root.getByRole('textbox',{name:'Positive prompt',exact:true}).fill(text);await root.getByRole('button',{name:'Save pair',exact:true}).click();
    await expect(root.getByRole('status').filter({hasText:'Pair saved.'})).toBeVisible();const response=await request.get(`/prompt_manager/pairs?text=${encodeURIComponent(text)}`);expect(response.ok()).toBe(true);expect((await response.json()).pairs).toEqual(expect.arrayContaining([expect.objectContaining({positive_text:text,negative_text:''})]));expect(await values(page,id)).toMatchObject({text,negative_text:''});
});

test('paired library filters search both texts, persist on reload and keep editor metadata separate',async({page,request},testInfo)=>{
    const token=`paired-filter-${testInfo.project.name}`;await seed(request,`Matching ${token}`,`Negative ${token}`);const other=await request.post('/prompt_manager/pairs',{data:{positive_text:`Other ${token}`,negative_text:'different',category:'Other paired category',tags:['other-pair-tag']}});expect(other.ok()).toBe(true);
    const id=await addPair(page);const root=page.locator('.pm-pair');await root.getByRole('textbox',{name:'Positive prompt',exact:true}).fill('Draft positive');await root.getByRole('textbox',{name:'Negative prompt',exact:true}).fill('Draft negative');
    await root.getByText('Metadata · category / tags',{exact:true}).click();await root.getByRole('combobox',{name:'Category',exact:true}).fill('Keep category');await root.getByRole('combobox',{name:'Tags',exact:true}).fill('keep-tag');await root.getByText('Metadata · category / tags',{exact:true}).click();
    await root.getByRole('searchbox',{name:'Search saved prompts'}).fill(token);await root.getByLabel('Filter category',{exact:true}).fill('Paired host tests');await root.getByLabel('Filter tags',{exact:true}).fill('pair-host, ');await root.getByRole('button',{name:'Search',exact:true}).click();
    await expect(root.locator('.pm-editor-result')).toHaveCount(1);await root.locator('.pm-editor-result').click();await expect(root.locator('.pm-editor-selection')).toContainText(`Negative ${token}`);
    expect(await values(page,id)).toMatchObject({text:'Draft positive',negative_text:'Draft negative',category:'Keep category',tags:'keep-tag'});await root.getByRole('switch',{name:'Load metadata'}).uncheck();await root.getByRole('button',{name:'Use pair',exact:true}).click();expect(await values(page,id)).toMatchObject({text:`Matching ${token}`,negative_text:`Negative ${token}`,category:'Keep category',tags:'keep-tag'});
    await page.screenshot({path:testInfo.outputPath('pair-library-preview.png')});
    await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');await app.loadGraphData(app.graph.serialize());});await expect(root.getByLabel('Filter category',{exact:true})).toHaveValue('Paired host tests');await expect(root.getByLabel('Filter tags',{exact:true})).toHaveValue('pair-host, ');
    await page.evaluate(async id=>{const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);node.setSize([560,1000]);app.canvas.centerOnNode(node);},id);
    await expect.poll(async()=>{
        const [p,n]=await Promise.all(['Positive prompt','Negative prompt'].map(name=>root.getByRole('textbox',{name,exact:true}).boundingBox()));
        return Boolean(p&&n&&p.width>0&&p.height>0&&n.height>0&&n.y>p.y+p.height-1&&Math.abs(p.width-n.width)<1);
    },{message:'Narrow prompt editors stack with equal widths after canvas resize'}).toBe(true);
    await page.screenshot({path:testInfo.outputPath('pair-narrow.png')});
});

for(const field of ['category','tags'])test(`manual Save pair guards real connected ${field} and resumes after disconnect`,async({page},testInfo)=>{
    const id=await addPair(page);const root=page.locator('.pm-pair');await root.getByRole('textbox',{name:'Positive prompt',exact:true}).fill(`Metadata link ${field} ${testInfo.project.name}`);
    let saves=0;await page.route('**/prompt_manager/pairs',async route=>{if(route.request().method()==='POST')saves++;await route.continue();});
    const link=await page.evaluate(async({id,field})=>{const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');const target=app.graph.getNodeById(id);convertToInput(target,target.widgets.find(widget=>widget.name===field));const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);source.widgets.find(widget=>widget.name==='value').value='Connected metadata';source.connect(0,target,target.inputs.findIndex(input=>input.name===field));app.canvas.centerOnNode(target);return [String(source.id),0];},{id,field});
    const save=root.getByRole('button',{name:'Save pair',exact:true});await expect(save).toBeDisabled();await expect(root.getByRole('status').filter({hasText:'Run the node to save connected values.'})).toBeVisible();await save.evaluate(button=>button.dispatchEvent(new Event('click',{bubbles:true})));expect(saves).toBe(0);
    const input=await page.evaluate(async({id,field})=>{const {app}=await import('/scripts/app.js');return(await app.graphToPrompt()).output[String(id)].inputs[field];},{id,field});expect(input).toEqual(link);
    await page.evaluate(async({id,field})=>{const {app}=await import('/scripts/app.js');const target=app.graph.getNodeById(id);target.disconnectInput(target.inputs.findIndex(input=>input.name===field));},{id,field});await expect(save).toBeEnabled();await save.click();await expect(root.getByRole('status').filter({hasText:'Pair saved.'})).toBeVisible();expect(saves).toBe(1);
});
