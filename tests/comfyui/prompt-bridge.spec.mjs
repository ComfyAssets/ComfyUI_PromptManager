import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {installHostHooks} from './host-fixture.mjs';

const TYPES=['PromptManager','PromptManagerText','PromptManagerFocused','PromptManagerSplit','PromptManagerPair'];
installHostHooks(test,TYPES);

async function addNode(page,type,values={}) {
    return page.evaluate(async({type,values})=>{
        const {app}=await import('/scripts/app.js');
        app.graph.clear();
        const node=LiteGraph.createNode(type);app.graph.add(node);
        node.setSize([type==='PromptManagerSplit'?1000:700,1050]);
        app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);
        for(const [name,value]of Object.entries(values)) {
            const widget=node.widgets.find(widget=>widget.name===name);
            widget.value=value;if(widget.inputEl)widget.inputEl.value=value;
            widget.callback?.(value,app.canvas,node);
        }
        return node.id;
    },{type,values});
}

async function withClient(page,context,run) {
    const clientPage=await context.newPage();const errors=[];
    clientPage.on('pageerror',error=>errors.push(error.message));
    try {
        await clientPage.goto(new URL('/prompt_manager/workspace#/overview',page.url()).href,{waitUntil:'domcontentloaded'});
        await clientPage.evaluate(async()=>{
            const {createBridgeClient}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
            window.bridgeClient=createBridgeClient({origin:location.origin,namespace:'/'});
        });
        await run(clientPage);
        expect(errors).toEqual([]);
    } finally {
        await clientPage.evaluate(()=>window.bridgeClient?.dispose()).catch(()=>{});
        await clientPage.close();
    }
}

async function destination(clientPage,id) {
    const hosts=await clientPage.evaluate(()=>window.bridgeClient.discover({waitMs:200}));
    const host=hosts.find(host=>host.targets.some(target=>String(target.id)===String(id)));
    expect(host,'independently opened workspace discovers the real host').toBeDefined();
    const target=host.targets.find(target=>String(target.id)===String(id));
    return {hostId:host.hostId,target};
}

async function preview(clientPage,hostId,target,payload) {
    return clientPage.evaluate(async({hostId,target,payload})=>{
        window.bridgePreview=await window.bridgeClient.preview(hostId,target.id,payload);
        return window.bridgePreview;
    },{hostId,target,payload});
}

async function apply(clientPage,hostId) {
    return clientPage.evaluate(hostId=>window.bridgeClient.apply(hostId,window.bridgePreview.ticket),hostId);
}

async function rejection(clientPage,action) {
    return clientPage.evaluate(async action=>{
        try {
            if(action.kind==='preview')await window.bridgeClient.preview(action.hostId,action.targetId,action.payload);
            else await window.bridgeClient.apply(action.hostId,window.bridgePreview.ticket);
            return null;
        } catch(error) {return String(error.message);}
    },action);
}

async function snapshot(page,id) {
    return page.evaluate(async id=>{
        const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);
        const serialized=node.serialize();
        return {
            widgets:Object.fromEntries(node.widgets.map(widget=>[widget.name,widget.value])),
            inputs:Object.fromEntries(node.widgets.filter(widget=>widget.inputEl).map(widget=>[widget.name,widget.inputEl.value])),
            serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),
            prompt:(await app.graphToPrompt()).output[String(id)].inputs,
        };
    },id);
}

async function connect(page,id,field) {
    return page.evaluate(async({id,field})=>{
        const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');
        const node=app.graph.getNodeById(id);const widget=node.widgets.find(widget=>widget.name===field);
        if(!node.inputs.some(input=>input.name===field))convertToInput(node,widget);
        const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);
        source.widgets.find(widget=>widget.name==='value').value=`Upstream ${field}`;
        source.connect(0,node,node.inputs.findIndex(input=>input.name===field));
        return [String(source.id),0];
    },{id,field});
}

test('independent workspace discovers all Prompt Manager destinations and writes real widgets without executing or loading workflows',async({page,context})=>{
    await withClient(page,context,async clientPage=>{
        for(const type of TYPES) {
            const id=await addNode(page,type,{text:'Before bridge'});
            const draft=`Visible draft ${type}`;
            await page.locator('textarea:visible').first().fill(draft);
            const {hostId,target}=await destination(clientPage,id);
            expect(target.type).toBe(type);expect(target.label).toBeTruthy();expect(target.roles).toContain('positive');
            expect(target.roles.includes('negative')).toBe(type==='PromptManagerPair');
            await page.evaluate(async()=>{
                const {app}=await import('/scripts/app.js');window.bridgeSideEffects={queue:0,load:0};
                for(const [method,key]of [['queuePrompt','queue'],['loadGraphData','load']]) {
                    const original=app[method];app[method]=function(...args){window.bridgeSideEffects[key]++;return original.apply(this,args);};
                }
            });
            const text=`Bridge sent to ${type}`;const proposed=await preview(clientPage,hostId,target,{positive:text});
            expect(proposed.ticket).toBeTruthy();expect(proposed.fields).toContainEqual({role:'positive',before:draft,after:text});
            expect((await snapshot(page,id)).widgets.text).toBe(draft);
            expect(await apply(clientPage,hostId)).toMatchObject({targetId:target.id,values:{positive:text}});
            await expect.poll(()=>page.locator('textarea:visible').evaluateAll(inputs=>inputs.map(input=>input.value))).toContain(text);
            const state=await snapshot(page,id);
            for(const name of ['widgets','inputs','serialized','prompt'])expect(state[name].text).toBe(text);
            expect(await page.evaluate(()=>window.bridgeSideEffects)).toEqual({queue:0,load:0});
        }
    });
});

test('pair sends preserve omitted negatives, accept an empty negative and synchronize visible editors and composition',async({page,context})=>{
    const id=await addNode(page,'PromptManagerPair',{text:'Old positive',negative_text:'Keep negative',prepend_text:'LoRA trigger',append_text:'fine detail'});
    await withClient(page,context,async clientPage=>{
        const {hostId,target}=await destination(clientPage,id);
        const proposed=await preview(clientPage,hostId,target,{positive:'New positive'});
        expect(proposed.composition).toMatchObject({prepend:'LoRA trigger',append:'fine detail',prependConnected:false,appendConnected:false,before:'LoRA trigger Old positive fine detail',after:'LoRA trigger New positive fine detail'});
        await apply(clientPage,hostId);
        expect((await snapshot(page,id)).widgets).toMatchObject({text:'New positive',negative_text:'Keep negative',prepend_text:'LoRA trigger',append_text:'fine detail'});
        const root=page.locator('.pm-pair');
        await expect(root.getByRole('textbox',{name:'Positive prompt',exact:true})).toHaveValue('New positive');
        await expect(root.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue('Keep negative');
        await root.getByText('Composed text preview',{exact:true}).click();
        await expect(root.locator('[data-composed-preview]')).toHaveText('LoRA trigger New positive fine detail');
        await preview(clientPage,hostId,target,{positive:'Paired positive',negative:''});
        await apply(clientPage,hostId);
        await expect(root.getByRole('textbox',{name:'Negative prompt',exact:true})).toHaveValue('');
        const state=await snapshot(page,id);
        for(const name of ['widgets','inputs','serialized','prompt'])expect(state[name]).toMatchObject({text:'Paired positive',negative_text:''});
    });
});

test('connected requested fields reject atomically while connected composition remains linked',async({page,context})=>{
    const id=await addNode(page,'PromptManagerPair',{text:'Keep positive',negative_text:'Keep negative',prepend_text:'Local prepend'});
    const negativeLink=await connect(page,id,'negative_text');const prependLink=await connect(page,id,'prepend_text');
    await withClient(page,context,async clientPage=>{
        const {hostId,target}=await destination(clientPage,id);
        expect(await rejection(clientPage,{kind:'preview',hostId,targetId:target.id,payload:{positive:'Must not apply',negative:'Blocked'}})).toMatch(/connected/i);
        expect((await snapshot(page,id)).widgets).toMatchObject({text:'Keep positive',negative_text:'Keep negative'});
        const proposed=await preview(clientPage,hostId,target,{positive:'Allowed positive'});
        expect(proposed.composition.prependConnected).toBe(true);
        await apply(clientPage,hostId);
        expect((await snapshot(page,id)).prompt).toMatchObject({text:'Allowed positive',negative_text:negativeLink,prepend_text:prependLink});
        const positiveLink=await connect(page,id,'text');
        expect(await rejection(clientPage,{kind:'preview',hostId,targetId:target.id,payload:{positive:'Blocked positive'}})).toMatch(/connected/i);
        expect((await snapshot(page,id)).prompt.text).toEqual(positiveLink);
    });
});

test('user edits and workflow replacement invalidate acknowledged previews without overwriting changes',async({page,context})=>{
    const id=await addNode(page,'PromptManagerFocused',{text:'Original draft'});
    await withClient(page,context,async clientPage=>{
        let {hostId,target}=await destination(clientPage,id);
        await preview(clientPage,hostId,target,{positive:'Pending change'});
        await page.locator('.pm-focused').getByRole('textbox',{name:'Main prompt',exact:true}).fill('User edited draft');
        expect(await rejection(clientPage,{kind:'apply',hostId})).toMatch(/changed|stale|workflow/i);
        expect((await snapshot(page,id)).widgets.text).toBe('User edited draft');
        ({hostId,target}=await destination(clientPage,id));
        await preview(clientPage,hostId,target,{positive:'Pending after replacement'});
        await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');await app.loadGraphData(app.graph.serialize());});
        expect(await rejection(clientPage,{kind:'apply',hostId})).toMatch(/changed|stale|workflow/i);
        expect((await snapshot(page,id)).widgets.text).toBe('User edited draft');
    });
});

test('bridge remains live through both renderer switches and workflow reload with synchronized Focused controls',async({page,context},testInfo)=>{
    const id=await addNode(page,'PromptManagerFocused',{text:'Initial',prepend_text:'trigger',append_text:'detail'});
    await withClient(page,context,async clientPage=>{
        for(const renderer of [!testInfo.project.metadata.initialRenderer,testInfo.project.metadata.initialRenderer]) {
            await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);app.canvas.centerOnNode(app.graph._nodes[0]);},renderer);
            await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);
            const {hostId,target}=await destination(clientPage,id);const text=`Renderer ${renderer}`;
            await preview(clientPage,hostId,target,{positive:text});await apply(clientPage,hostId);
            await expect(page.locator('.pm-focused').getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue(text);
            await page.evaluate(async()=>{const {app}=await import('/scripts/app.js');await app.loadGraphData(app.graph.serialize());});
            const state=await snapshot(page,id);
            for(const name of ['widgets','inputs','serialized','prompt'])expect(state[name].text).toBe(text);
        }
        const {hostId,target}=await destination(clientPage,id);
        await preview(clientPage,hostId,target,{positive:'After reload'});await apply(clientPage,hostId);
        await expect(page.locator('.pm-focused').getByRole('textbox',{name:'Main prompt',exact:true})).toHaveValue('After reload');
    });
});

test('each destination reports its window renderer, and System › Integrations shows it (S10)',async({page,context},testInfo)=>{
    const initial=testInfo.project.metadata.initialRenderer;
    const id=await addNode(page,'PromptManager',{text:'Renderer check'});
    await withClient(page,context,async clientPage=>{
        const name=renderer=>renderer?'Nodes 2.0':'Classic';
        const status=clientPage.locator('#workspace-main #renderer-status');
        expect((await destination(clientPage,id)).target.renderer).toBe(initial?'nodes2':'classic');
        await clientPage.evaluate(()=>{location.hash='#/system/integrations';});
        await expect(status).toHaveText(`Detected: ${name(initial)} (1 ComfyUI window).`);
        // Switching the renderer in ComfyUI is what the next check reports; nothing is sent or queued.
        await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);await app.loadGraphData(app.graph.serialize());},!initial);
        await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(!initial);
        expect((await destination(clientPage,id)).target.renderer).toBe(initial?'classic':'nodes2');
        await clientPage.reload();
        await expect(status).toHaveText(`Detected: ${name(!initial)} (1 ComfyUI window).`);
    });
});
