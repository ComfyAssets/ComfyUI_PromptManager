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
        await workspace.getByRole('searchbox',{name:'Search prompts',exact:true}).fill(positive);
        await workspace.locator('article[data-prompt-id]').filter({has:workspace.getByText(positive,{exact:true})}).getByRole('link',{name:'Explore history',exact:true}).click();
        const history=workspace.getByRole('region',{name:'Prompt history',exact:true});
        await history.getByRole('button',{name:'Select negative variant 1',exact:true}).click();
        await expect(history.getByRole('region',{name:'Saved prompt texts',exact:true}).getByRole('region',{name:'Negative prompt',exact:true})).toContainText(negative);
        await history.getByRole('region',{name:'Saved prompt texts',exact:true}).getByRole('button',{name:'Send prompt pair',exact:true}).click();
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
