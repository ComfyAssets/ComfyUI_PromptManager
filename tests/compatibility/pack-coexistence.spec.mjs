import {readFile} from 'node:fs/promises';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import runtime from '../../tools/vendor/comfyui-runtime.json' with {type:'json'};

const bridgeSource=await readFile(new URL('../../web/js/shared/toolkit/prompt-bridge.js',import.meta.url),'utf8');

test('independent and combined packs preserve real controls, bridge isolation and workflow state',async({page,context,request},testInfo)=>{
    const {mode,initialRenderer}=testInfo.project.metadata;
    const hasPM=mode!=='kiko',hasKiko=mode!=='promptmanager';const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const response=await request.get('/system_stats');expect(response.ok()).toBe(true);
    expect((await response.json()).system.comfyui_version).toBe(runtime.serverVersion);
    await context.route('**/*',async route=>{
        const request=route.request();const path=new URL(request.url()).pathname.replace(/^\/api\//,'/');
        if(path==='/settings'&&request.method()==='GET') {
            const response=await route.fetch();return route.fulfill({json:{...await response.json(),
                'Comfy.VueNodes.Enabled':initialRenderer,'Comfy.TutorialCompleted':true,
                'Comfy.Workflow.Persist':false,'Comfy.Notification.ShowVersionUpdates':false}});
        }
        if(!['GET','HEAD','OPTIONS'].includes(request.method())&&!path.startsWith('/prompt_manager/'))return route.fulfill({json:{}});
        return route.continue();
    });
    await page.goto('/',{waitUntil:'domcontentloaded'});
    await expect(page.getByRole('status',{name:'Loading ComfyUI'})).toBeHidden({timeout:30000});
    await page.waitForFunction(async()=>Boolean((await import('/scripts/app.js')).app.graph));
    await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(initialRenderer);
    const definitions=await(await request.get('/object_info')).json();
    expect(Boolean(definitions.PromptManagerFocused)).toBe(hasPM);expect(Boolean(definitions.TextInput)).toBe(hasKiko);
    await page.waitForFunction(({hasPM,hasKiko})=>
        (!hasPM||Boolean(LiteGraph.registered_node_types.PromptManagerFocused))&&
        (!hasKiko||Boolean(LiteGraph.registered_node_types.TextInput)),{hasPM,hasKiko});
    const ids=await page.evaluate(async({hasPM,hasKiko})=>{
        const {app}=await import('/scripts/app.js');app.graph.clear();const ids={};
        for(const [key,type]of [...(hasPM?[['pm','PromptManagerFocused']]:[]),...(hasKiko?[['kiko','TextInput']]:[])]) {
            const node=LiteGraph.createNode(type);app.graph.add(node);node.setSize([680,1000]);
            node.pos=[key==='kiko'?800:0,0];ids[key]=node.id;
        }
        app.canvas.ds.scale=.8;app.canvas.centerOnNode(app.graph.getNodeById(ids.kiko??ids.pm));return ids;
    },{hasPM,hasKiko});
    const clientPage=await context.newPage();clientPage.on('pageerror',error=>errors.push(error.message));
    try {
        await clientPage.goto(new URL(hasPM?'/prompt_manager/workspace#/overview':'/system_stats',page.url()).href,{waitUntil:'domcontentloaded'});
        await clientPage.evaluate(async source=>{
            const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
            try {const {createBridgeClient}=await import(url);window.packClient=createBridgeClient({origin:location.origin,namespace:'/'});}
            finally {URL.revokeObjectURL(url);}
        },bridgeSource);
        for(const [index,renderer]of [initialRenderer,!initialRenderer,initialRenderer].entries()) {
            await page.evaluate(async({renderer,ids})=>{
                const {app}=await import('/scripts/app.js');await app.ui.settings.setSettingValue('Comfy.VueNodes.Enabled',renderer);
                app.canvas.centerOnNode(app.graph.getNodeById(ids.kiko??ids.pm));
            },{renderer,ids});
            await expect.poll(()=>page.evaluate(()=>LiteGraph.vueNodesMode)).toBe(renderer);
            const kikoText=`Kiko independent edit ${index}`;
            if(hasKiko) {
                const control=page.locator('textarea:visible:not(.pm-focused textarea)');
                await expect(control).toHaveCount(1);await expect(control).toBeVisible();await control.fill(kikoText);
                expect(await control.evaluate(async(_element,id)=>{
                    const {app}=await import('/scripts/app.js');return(await app.graphToPrompt()).output[String(id)].inputs.text;
                },ids.kiko)).toBe(kikoText);
            }
            const hosts=await clientPage.evaluate(()=>window.packClient.discover({waitMs:200}));
            expect(hosts).toHaveLength(hasPM?1:0);
            if(hasPM) {
                expect(hosts[0].targets).toHaveLength(1);
                const target=hosts[0].targets[0];expect(String(target.id)).toBe(String(ids.pm));
                expect(target.type).toBe('PromptManagerFocused');
                const text=`PM isolated send ${index}`;
                const result=await clientPage.evaluate(async({hostId,target,text})=>{
                    const preview=await window.packClient.preview(hostId,target.id,{positive:text});
                    return window.packClient.apply(hostId,preview.ticket);
                },{hostId:hosts[0].hostId,target,text});
                expect(result.values.positive).toBe(text);
                const state=await page.evaluate(async ids=>{
                    const {app}=await import('/scripts/app.js');const prompt=(await app.graphToPrompt()).output;
                    return {pm:prompt[String(ids.pm)].inputs.text,kiko:ids.kiko?prompt[String(ids.kiko)].inputs.text:null};
                },ids);
                expect(state.pm).toBe(text);if(hasKiko)expect(state.kiko).toBe(kikoText);
            }
            await page.evaluate(async ids=>{
                const {app}=await import('/scripts/app.js');await app.loadGraphData(app.graph.serialize());
                app.canvas.centerOnNode(app.graph.getNodeById(ids.kiko??ids.pm));
            },ids);
            if(hasKiko)expect(await page.evaluate(async id=>{
                const {app}=await import('/scripts/app.js');return(await app.graphToPrompt()).output[String(id)].inputs.text;
            },ids.kiko)).toBe(kikoText);
        }
        expect(errors).toEqual([]);
    } finally {
        await clientPage.evaluate(()=>window.packClient?.dispose()).catch(()=>{});await clientPage.close();
    }
});
