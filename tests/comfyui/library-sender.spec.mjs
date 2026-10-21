import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {installHostHooks} from './host-fixture.mjs';

installHostHooks(test,['PromptManagerPair']);

test('native library sends the selected saved pair into a real ComfyUI destination',async({page,context,request},testInfo)=>{
    const positive=`Native library host ${testInfo.project.name}`;const negative=`Saved negative ${testInfo.project.name}`;
    const response=await request.post('/prompt_manager/pairs',{data:{positive_text:positive,negative_text:negative,title:'Host saved pair',category:'Native host',tags:['library-host']}});
    expect(response.ok()).toBe(true);
    const id=await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');const node=LiteGraph.createNode('PromptManagerPair');app.graph.add(node);node.setSize([700,1000]);
        app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);return node.id;
    });
    const editor=page.locator('.pm-pair');await editor.getByRole('textbox',{name:'Positive prompt',exact:true}).fill('Original positive');
    await editor.getByRole('textbox',{name:'Negative prompt',exact:true}).fill('Original negative');
    await editor.getByText('Composition · prepend / append',{exact:true}).click();
    await editor.getByRole('textbox',{name:'Prepend',exact:true}).fill('Keep trigger');await editor.getByRole('textbox',{name:'Append',exact:true}).fill('Keep detail');
    await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');window.libraryEffects={queue:0,load:0};
        for(const [method,key]of [['queuePrompt','queue'],['loadGraphData','load']]){const original=app[method];app[method]=function(...args){window.libraryEffects[key]++;return original.apply(this,args);};}
    });
    const workspace=await context.newPage();const errors=[];workspace.on('pageerror',error=>errors.push(error.message));
    try {
        await workspace.goto('/prompt_manager/workspace#/library');
        await workspace.getByRole('searchbox',{name:'Search workspace',exact:true}).fill(positive);
        await workspace.locator('article[data-prompt-id]').filter({has:workspace.getByText(positive,{exact:true})}).getByRole('button',{name:'Explore history',exact:true}).click();
        const history=workspace.getByRole('region',{name:'Prompt history',exact:true});
        await history.getByRole('button',{name:'Select negative Host saved pair',exact:true}).click();
        await expect(history.getByRole('region',{name:'Saved prompt texts',exact:true}).getByRole('region',{name:'Negative prompt',exact:true})).toContainText(negative);
        // The pair send lives in the history heading, as in the approved concept.
        await workspace.locator('#workspace-main > .heading').getByRole('button',{name:`Send prompt pair to ComfyUI: ${positive}`,exact:true}).click();
        const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({index:1});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({index:1});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await expect(sender).toContainText(positive);await expect(sender).toContainText(negative);await expect(sender).toContainText(`Keep trigger ${positive} Keep detail`);
        await expect(editor.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('Original positive');
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        await expect(editor.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue(positive);await expect(editor.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue(negative);
        const state=await page.evaluate(async id=>{
            const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);const serialized=node.serialize();
            return{inputs:(await app.graphToPrompt()).output[String(id)].inputs,serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),effects:window.libraryEffects};
        },id);
        for(const field of ['inputs','serialized'])expect(state[field]).toMatchObject({text:positive,negative_text:negative,prepend_text:'Keep trigger',append_text:'Keep detail'});
        expect(state.effects).toEqual({queue:0,load:0});expect(errors).toEqual([]);
    } finally {await workspace.close();}
});

test('native editor preview sends exact saved roles across renderer transitions',async({page,context,request},testInfo)=>{
    test.setTimeout(150000);
    const title=`Native editor host ${testInfo.project.name}`;const positive=`${title} positive ${Date.now()}`;
    const id=await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');const node=LiteGraph.createNode('PromptManagerPair');app.graph.add(node);
        node.setSize([700,1000]);app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);return node.id;
    });
    const editor=page.locator('.pm-pair');
    await editor.getByText('Composition · prepend / append',{exact:true}).click();
    await editor.getByRole('textbox',{name:'Prepend',exact:true}).fill('Keep trigger');await editor.getByRole('textbox',{name:'Append',exact:true}).fill('Keep detail');
    const workspace=await context.newPage();const errors=[];workspace.on('pageerror',error=>errors.push(error.message));
    try {
        // Created through the native editor entry action, with an explicitly empty negative.
        await workspace.goto('/prompt_manager/workspace#/library');
        await workspace.locator('#workspace-main > .heading').getByRole('button',{name:'+ New prompt',exact:true}).click();
        const dialog=workspace.locator('dialog.library-editor');await expect(dialog.getByRole('button',{name:'Save',exact:true})).toBeEnabled();
        await dialog.getByLabel('Title',{exact:true}).fill(title);await dialog.getByLabel('Positive prompt',{exact:true}).fill(positive);
        await dialog.getByRole('button',{name:'Save',exact:true}).click();await expect(dialog).toHaveCount(0);
        await expect(workspace).toHaveURL(/#\/library\/\d+$/);const positiveId=Number(new URL(workspace.url()).hash.split('/').at(-1));
        await expect(workspace.locator('#workspace-main h1')).toHaveText(title);
        const saved=workspace.getByRole('region',{name:'Saved prompt texts',exact:true});
        await expect(saved.getByRole('region',{name:'Negative prompt',exact:true})).toContainText('No negative prompt (explicitly empty)');
        const evidence=async()=>{
            const history=await(await request.get(`/prompt_manager/library/${positiveId}/history`)).json();
            const detail=await(await request.get(`/prompt_manager/library/${positiveId}?archive=all`)).json();
            return {history:{count:history.recorded_run_count,runs:history.items.map(item=>item.run_id)},
                pairMetadata:detail.variants.map(({id,title,category,tags,archived,negative_text})=>({id,title,category,tags,archived,negative_text}))};
        };
        const before=await evidence();expect(before.pairMetadata).toHaveLength(1);
        const send=workspace.locator('#workspace-main > .heading').getByRole('button',{name:`Send prompt pair to ComfyUI: ${title}`,exact:true});
        for(const renderer of [testInfo.project.metadata.initialRenderer,!testInfo.project.metadata.initialRenderer,testInfo.project.metadata.initialRenderer]) {
            await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);await app.loadGraphData(app.graph.serialize());app.canvas.centerOnNode(app.graph._nodes[0]);},renderer);
            await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);
            await page.evaluate(async id=>{
                const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);
                node.widgets.find(widget=>widget.name==='text').value='Before positive';node.widgets.find(widget=>widget.name==='negative_text').value='Before negative';
                window.editorEffects={queue:0,load:0};window.editorOriginals={};
                for(const [method,key] of [['queuePrompt','queue'],['loadGraphData','load']]){const original=app[method];window.editorOriginals[method]=original;app[method]=function(...args){window.editorEffects[key]++;return original.apply(this,args);};}
            },id);
            try {
                await expect(send).toBeEnabled();await send.click();
                const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
                const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({index:1});
                await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({index:1});
                await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
                for(const text of ['Before positive','Before negative',positive,'(empty)',`Keep trigger ${positive} Keep detail`])await expect(sender).toContainText(text);
                await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
                const state=await page.evaluate(async id=>{
                    const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);const serialized=node.serialize();
                    return {inputs:(await app.graphToPrompt()).output[String(id)].inputs,serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),effects:window.editorEffects};
                },id);
                expect(state.effects).toEqual({queue:0,load:0});
                for(const field of ['inputs','serialized'])expect(state[field]).toMatchObject({text:positive,negative_text:'',prepend_text:'Keep trigger',append_text:'Keep detail'});
                await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(sender).toHaveCount(0);await expect(send).toBeFocused();
            } finally {await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');Object.assign(app,window.editorOriginals);});}
        }
        // A connected target rejects the whole preview: neither role changes, nothing queues or loads.
        const link=await page.evaluate(async id=>{
            const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');
            const node=app.graph.getNodeById(id);const widget=node.widgets.find(item=>item.name==='negative_text');
            if(!node.inputs.some(input=>input.name==='negative_text'))convertToInput(node,widget);
            node.widgets.find(item=>item.name==='text').value='Before positive';widget.value='Before negative';
            const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);source.widgets.find(item=>item.name==='value').value='Upstream negative';
            source.connect(0,node,node.inputs.findIndex(input=>input.name==='negative_text'));
            window.editorEffects={queue:0,load:0};window.editorOriginals={};
            for(const [method,key] of [['queuePrompt','queue'],['loadGraphData','load']]){const original=app[method];window.editorOriginals[method]=original;app[method]=function(...args){window.editorEffects[key]++;return original.apply(this,args);};}
            return [String(source.id),0];
        },id);
        try {
            await send.click();
            const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
            const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({index:1});
            await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({index:1});
            await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
            await expect(sender.getByRole('alert')).toContainText(/connected/i);
            await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeDisabled();
            const rejected=await page.evaluate(async id=>{
                const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);const serialized=node.serialize();
                return {inputs:(await app.graphToPrompt()).output[String(id)].inputs,widgets:Object.fromEntries(node.widgets.map(widget=>[widget.name,widget.value])),
                    serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),effects:window.editorEffects};
            },id);
            expect(rejected.effects).toEqual({queue:0,load:0});
            expect(rejected.inputs.negative_text).toEqual(link);
            for(const field of ['widgets','serialized'])expect(rejected[field]).toMatchObject({text:'Before positive',negative_text:'Before negative',prepend_text:'Keep trigger',append_text:'Keep detail'});
            await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(sender).toHaveCount(0);
        } finally {await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');Object.assign(app,window.editorOriginals);});}
        const after=await evidence();
        expect(after.history).toEqual(before.history);expect(after.pairMetadata).toEqual(before.pairMetadata);
        expect(errors).toEqual([]);
    } finally {await workspace.close();}
});
