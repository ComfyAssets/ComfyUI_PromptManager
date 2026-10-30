import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {installHostHooks} from './host-fixture.mjs';
import {sendablePng,localFixture,POSITIVE_LINES} from '../browser/local-inspect-files.mjs';

// PR L2 Task 7 of the local-Inspect plan (#249): a file read entirely in the browser by the native Inspect page sends
// its exact roles into a real Prompt Manager (+/-) node, in Classic and Nodes 2.0, switching both ways and reloading
// the graph each time. Nothing queues or loads a workflow, and a connected destination input rejects the whole send.
installHostHooks(test,['PromptManagerPair']);

// The PNG's positive spans lines and its negative is a ConditioningZeroOut (explicitly empty); the WebM carries the
// committed core video's per-key tags.
const FILES=[
    {file:()=>sendablePng('host-send.png'),positive:POSITIVE_LINES,negative:'',shown:'No negative prompt (explicitly empty)'},
    {file:()=>localFixture('core-tags.webm','video/webm','host-send.webm'),positive:'a red fox in snow, detailed fur',negative:'blurry, lowres',shown:'blurry, lowres'},
];

async function nodeState(page,id) {
    return page.evaluate(async id=>{
        const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);const serialized=node.serialize();
        return {inputs:(await app.graphToPrompt()).output[String(id)].inputs,widgets:Object.fromEntries(node.widgets.map(widget=>[widget.name,widget.value])),
            serialized:Object.fromEntries(node.widgets.map((widget,index)=>[widget.name,serialized.widgets_values[index]])),effects:window.inspectEffects};
    },id);
}

/**
 * Counts every queue or workflow load until `restore` runs; with `reset`, first sets the destination's roles back to
 * known values (the connected-input case sets them itself, before it links the input, as library-sender.spec does).
 */
async function watchHost(page,id,reset=true) {
    await page.evaluate(async ({id,reset})=>{
        const {app}=await import('/scripts/app.js');const node=app.graph.getNodeById(id);
        if(reset){node.widgets.find(widget=>widget.name==='text').value='Before positive';node.widgets.find(widget=>widget.name==='negative_text').value='Before negative';}
        window.inspectEffects={queue:0,load:0};window.inspectOriginals={};
        for(const [method,key] of [['queuePrompt','queue'],['loadGraphData','load']]){const original=app[method];window.inspectOriginals[method]=original;app[method]=function(...args){window.inspectEffects[key]++;return original.apply(this,args);};}
    },{id,reset});
}
const restore=page=>page.evaluate(async()=>{const {app}=await import('/scripts/app.js');Object.assign(app,window.inspectOriginals);});

/** Chooses `file` on the Inspect page, which opens the shared viewer on its local record (D155, #448). */
async function inspect(workspace,{file,positive,shown}) {
    await workspace.locator('#local-file').setInputFiles(file());
    const open=workspace.locator('#workspace-main .heading-actions').getByRole('button',{name:'Open file metadata',exact:true});
    await expect(open).toHaveAttribute('aria-disabled','false');
    const rail=workspace.locator('dialog#viewer-dialog[open] #viewer-details');
    await expect(rail.locator('.prompt-section.positive p.prompt-text')).toHaveText(positive);
    await expect(rail.locator('.prompt-section.negative p.prompt-text')).toHaveText(shown);
    return rail;
}

/** The rail's pair send, through to a previewed destination; returns the sender dialog. */
async function preview(workspace,rail) {
    await rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /}).click();
    const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
    const hosts=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});await expect(hosts.locator('option')).toHaveCount(2);await hosts.selectOption({index:1});
    await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({index:1});
    await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
    return sender;
}

async function closeViewer(workspace,sender) {
    await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(sender).toHaveCount(0);
    await workspace.keyboard.press('Escape');await expect(workspace.locator('dialog#viewer-dialog[open]')).toHaveCount(0);
}

test('local file roles send exactly across renderer transitions',async({page,context},testInfo)=>{
    test.setTimeout(180000);
    const id=await page.evaluate(async()=>{
        const {app}=await import('/scripts/app.js');const node=LiteGraph.createNode('PromptManagerPair');app.graph.add(node);
        node.setSize([700,1000]);app.canvas.ds.scale=.8;app.canvas.centerOnNode(node);return node.id;
    });
    const workspace=await context.newPage();const errors=[];workspace.on('pageerror',error=>errors.push(error.message));
    const requests=[];workspace.on('request',request=>{if(request.method()!=='GET')requests.push(`${request.method()} ${request.url()}`);});
    const logged=[];workspace.on('console',message=>logged.push(message.text()));
    try {
        await workspace.goto('/prompt_manager/workspace#/inspect');
        await expect(workspace.locator('#workspace-main h1')).toHaveText('Inspect a file');
        const initial=testInfo.project.metadata.initialRenderer;
        for(const renderer of [initial,!initial,initial]) {
            // The renderer switch reloads the graph, as a workflow reload does.
            await page.evaluate(async renderer=>{const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);await app.loadGraphData(app.graph.serialize());app.canvas.centerOnNode(app.graph._nodes[0]);},renderer);
            await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);
            for(const entry of FILES) {
                await watchHost(page,id);
                try {
                    const rail=await inspect(workspace,entry);
                    const sender=await preview(workspace,rail);
                    for(const text of ['Before positive','Before negative',...entry.positive.split('\n'),entry.negative||'(empty)'])await expect(sender).toContainText(text);
                    await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
                    const state=await nodeState(page,id);
                    expect(state.effects).toEqual({queue:0,load:0});
                    for(const field of ['inputs','serialized'])expect(state[field]).toMatchObject({text:entry.positive,negative_text:entry.negative});
                    await closeViewer(workspace,sender);
                } finally {await restore(page);}
            }
        }
        // A connected destination input blocks the whole send: neither role changes, nothing queues or loads.
        const link=await page.evaluate(async id=>{
            const {app}=await import('/scripts/app.js');const {convertToInput}=await import('/extensions/core/widgetInputs.js');
            const node=app.graph.getNodeById(id);const widget=node.widgets.find(item=>item.name==='negative_text');
            if(!node.inputs.some(input=>input.name==='negative_text'))convertToInput(node,widget);
            node.widgets.find(item=>item.name==='text').value='Before positive';widget.value='Before negative';
            const source=LiteGraph.createNode('PrimitiveString');app.graph.add(source);source.widgets.find(item=>item.name==='value').value='Upstream negative';
            source.connect(0,node,node.inputs.findIndex(input=>input.name==='negative_text'));
            return [String(source.id),0];
        },id);
        await watchHost(page,id,false);
        try {
            const before=await nodeState(page,id);
            expect(before.widgets).toMatchObject({text:'Before positive',negative_text:'Before negative'});
            const rail=await inspect(workspace,FILES[1]);
            // #535: the linked node is not offered, and the status line says why.
            await rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /}).click();
            const sender=workspace.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
            await expect(sender).toContainText(/has its negative prompt linked to another node/);
            await expect(sender.getByRole('button',{name:'Preview changes',exact:true})).toBeDisabled();
            await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeDisabled();
            const rejected=await nodeState(page,id);
            expect(rejected.effects).toEqual({queue:0,load:0});
            expect(rejected.inputs.negative_text).toEqual(link);
            for(const field of ['widgets','serialized'])expect(rejected[field]).toMatchObject({text:'Before positive',negative_text:'Before negative'});
            await closeViewer(workspace,sender);
        } finally {await restore(page);}
        // The workspace page sent nothing to the server (the only crossing is the host bridge) and logged nothing of
        // the files: no name and no prompt text.
        expect(requests).toEqual([]);
        expect(logged.filter(text=>/host-send|lighthouse|long exposure|red fox|lowres/.test(text))).toEqual([]);
        expect(errors).toEqual([]);
    } finally {await workspace.close();}
});
