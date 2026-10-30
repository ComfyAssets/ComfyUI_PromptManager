import {execFileSync} from 'node:child_process';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {installHostHooks} from './host-fixture.mjs';

installHostHooks(test,['PromptManagerPair']);

test('native image inspector previews and sends a pair into real visible widgets without loading or queueing',async({page,context,request},testInfo)=>{
    const positive=`Inspector host positive ${testInfo.project.name}`;const negative='host negative, watermark';
    const image=execFileSync(process.env.PM_TEST_PYTHON||'python',['-c',[
        'import io,json,sys',
        'from PIL import Image',
        'from PIL.PngImagePlugin import PngInfo',
        'info=PngInfo()',
        'graph={"1":{"class_type":"CLIPTextEncode","inputs":{"text":sys.argv[1]}},"2":{"class_type":"CLIPTextEncode","inputs":{"text":sys.argv[2]}},"3":{"class_type":"KSampler","inputs":{"positive":["1",0],"negative":["2",0],"model":["4",0],"seed":9007199254740993,"steps":28,"cfg":6.5,"sampler_name":"dpmpp_2m","scheduler":"karras"}},"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"host-fixture.safetensors"}}}',
        'info.add_text("prompt",json.dumps(graph))',
        'info.add_text("workflow",json.dumps({"nodes":[],"links":[]}))',
        'buffer=io.BytesIO()',
        'Image.new("RGB",(64,64),(30,80,140)).save(buffer,format="PNG",pnginfo=info)',
        'sys.stdout.buffer.write(buffer.getvalue())',
    ].join('\n'),positive,negative]);
    const filename=`sender-${testInfo.project.name}.png`;
    const upload=await request.post('/upload/image',{multipart:{image:{name:filename,mimeType:'image/png',buffer:image},type:'output',subfolder:'prompt-sender',overwrite:'true'}});
    expect(upload.ok()).toBe(true);
    const id=await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');const node=LiteGraph.createNode('PromptManagerPair');app.graph.add(node);
        node.setSize([700,1000]);app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);
        return node.id;
    });
    const nodeRoot=page.locator('.pm-pair');
    await nodeRoot.getByRole('textbox',{name:'Positive prompt',exact:true}).fill('Before positive');
    await nodeRoot.getByRole('textbox',{name:'Negative prompt',exact:true}).fill('Before negative');
    await nodeRoot.getByText('Composition · prepend / append',{exact:true}).click();
    await nodeRoot.getByRole('textbox',{name:'Prepend',exact:true}).fill('LoRA trigger');
    await nodeRoot.getByRole('textbox',{name:'Append',exact:true}).fill('fine detail');
    await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');window.senderEffects={queue:0,load:0};
        for(const [method,key]of [['queuePrompt','queue'],['loadGraphData','load']]){
            const original=app[method];app[method]=function(...args){window.senderEffects[key]++;return original.apply(this,args);};
        }
    });
    const workspace=await context.newPage();const errors=[];workspace.on('pageerror',error=>errors.push(error.message));
    try {
        await workspace.goto('/prompt_manager/workspace#/images');
        // D15: Refresh is Import → Rescan.
        await workspace.getByRole('button',{name:'Import',exact:true}).click();
        await workspace.getByRole('dialog',{name:'Import & inspect',exact:true}).getByRole('button',{name:'Rescan',exact:true}).click();
        const card=workspace.locator('article[data-image-key]').filter({has:workspace.getByRole('button',{name:`Open ${filename}`,exact:true})});
        await card.getByRole('button',{name:`Inspect ${filename}`,exact:true}).click();
        const inspector=workspace.getByRole('complementary',{name:'Selected image details',exact:true});
        await expect(inspector.getByRole('region',{name:'Positive prompt',exact:true})).toContainText(positive);
        await card.getByRole('button',{name:`Open ${filename}`,exact:true}).click();
        const viewer=workspace.locator('dialog#viewer-dialog');
        await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
        await expect(viewer.locator('.meta-grid').first().locator('dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','host-fixture.safetensors']);
        await viewer.getByText('Workflow & raw metadata',{exact:true}).click();
        await expect(viewer.locator('pre code').first()).toContainText('9007199254740993');
        await expect(viewer.getByRole('button',{name:'Download workflow JSON',exact:true})).not.toHaveAttribute('aria-disabled','true');
        expect(await page.evaluate(()=>window.senderEffects)).toEqual({queue:0,load:0});
        for(const renderer of [!testInfo.project.metadata.initialRenderer,testInfo.project.metadata.initialRenderer]) {
            await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);await app.loadGraphData(app.graph.serialize());app.canvas.centerOnNode(app.graph._nodes[0]);},renderer);
            await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);
            await expect(viewer).toBeVisible();await expect(viewer.locator('.meta-grid dd').first()).toHaveText('9007199254740993');
            await expect(nodeRoot.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('Before positive');
        }
        expect(await page.evaluate(()=>window.senderEffects.queue)).toBe(0);
        await page.evaluate(()=>{window.senderSendBaseline={...window.senderEffects};});
        await viewer.getByRole('button',{name:/^Send prompt pair to ComfyUI: /}).click();
        const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});
        await expect(hosts.locator('option')).toHaveCount(2);
        // #535: the window is named by its tab, and one window with one node needs no choosing.
        const tabTitle=await page.title();
        await expect(hosts.locator('option').nth(1)).toHaveText(tabTitle);
        expect(tabTitle).not.toContain('PromptManager');
        await expect(hosts).not.toHaveValue('');
        await expect(sender.getByRole('combobox',{name:'Destination node',exact:true})).not.toHaveValue('');
        await expect(sender.getByRole('status')).toContainText(`in ${tabTitle}. Preview changes before applying.`);
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        for(const text of ['Before positive','Before negative',positive,negative,`LoRA trigger ${positive} fine detail`])await expect(sender).toContainText(text);
        await expect(nodeRoot.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('Before positive');
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();
        await expect(sender).toContainText('Sent to ComfyUI.');
        await expect(nodeRoot.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue(positive);
        await expect(nodeRoot.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue(negative);
        const state=await page.evaluate(async id=>{
            const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);const serialized=node.serialize();
            return{inputs:(await app.graphToPrompt()).output[String(id)].inputs,
                serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),
                effects:{queue:window.senderEffects.queue-window.senderSendBaseline.queue,load:window.senderEffects.load-window.senderSendBaseline.load}};
        },id);
        for(const name of ['inputs','serialized'])expect(state[name]).toMatchObject({text:positive,negative_text:negative,prepend_text:'LoRA trigger',append_text:'fine detail'});
        expect(state.effects).toEqual({queue:0,load:0});expect(errors).toEqual([]);
        await workspace.screenshot({path:testInfo.outputPath('full-inspector-host-send.png')});
        // #535: the status line says why a window cannot receive.
        const find=sender.getByRole('button',{name:'Find ComfyUI windows',exact:true});
        const preview=sender.getByRole('button',{name:'Preview changes',exact:true});
        await page.evaluate(async id=>{
            const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');
            const node=app.graph.getNodeById(id);const widget=node.widgets.find(widget=>widget.name==='text');
            if(!node.inputs.some(input=>input.name==='text'))convertToInput(node,widget);
            const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);
            source.connect(0,node,node.inputs.findIndex(input=>input.name==='text'));
        },id);
        await find.click();
        await expect(sender).toContainText(/has its positive prompt linked to another node/);
        await expect(preview).toBeDisabled();
        await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');app.graph.clear();});
        await find.click();
        await expect(sender).toContainText('has no Prompt Manager node.');
        await expect(preview).toBeDisabled();
    } finally {await workspace.close();}
});
