import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

test.beforeEach(async({page,browserName})=>{await startCoverage(page,browserName);page.inspectorErrors=[];page.on('pageerror',error=>page.inspectorErrors.push(error.message));});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.inspectorErrors).toEqual([]);});

test('inspection facts survive actual API to browser transport', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { full_inspector: true } })).toBeOK();
    await page.goto('/prompt_manager/workspace');
    const result = await page.evaluate(async () => {
        if (typeof window.ComfyMetadata?.extractGenerationFacts !== 'function') return { missingFactsExport: true };
        const { inspectPath, toInspectionRecord } = await import('/prompt_manager/js/workspace/inspection-state.mjs');
        const { createApiClient } = window.ApiClient;
        const api = createApiClient();
        const collection = await api.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector&limit=40');
        const records = {};
        for (const name of ['complete', 'ambiguous', 'workflow-only', 'control-heavy']) {
            const image = collection.images.find(entry => entry.root_index === 0 && entry.path === `inspector/${name}.png`);
            const envelope = await api.get(inspectPath(image));
            records[name] = toInspectionRecord(envelope, image, window.ComfyMetadata.extractGenerationFacts);
        }
        return records;
    });
    expect(result.missingFactsExport).toBeUndefined();
    const complete = result.complete;
    expect(Object.values(complete.generation).map(fact => fact.value)).toEqual(['9007199254740993', '28', '6.5', 'dpmpp_2m', 'karras', 'fixture-model.safetensors']);
    expect(Object.values(complete.generation).every(fact => fact.source === 'api-graph' && fact.evidence === 'configuration')).toBe(true);
    expect(Object.values(result.ambiguous.generation).every(fact => fact.status === 'ambiguous' && fact.value === null)).toBe(true);
    expect(result['workflow-only'].generation.seed).toMatchObject({ status: 'known', value: '9007199254740993', source: 'workflow', evidence: 'configuration' });
    expect(result['workflow-only'].prompts.positive.source).toBe('saved-association');
    expect(Object.values(result['control-heavy'].generation).every(fact => fact.status === 'unresolved' && fact.value === null && fact.reason === 'projection-limited')).toBe(true);
    await expect(await request.post('/__test__/reset')).toBeOK();
});

test('inspection wire preserves exact rooted envelope', async ({ request }) => {
    for (const data of [
        { full_inspector: 'true' }, { full_inspector: 1 },
        { full_inspector: true, workspace_visual: true },
        { full_inspector: true, prompt_inspector: true },
        { full_inspector: true, native_library: true },
        { full_inspector: true, extra_prompts: 1 },
        { full_inspector: true, extra_variants: 1 },
        { full_inspector: true, extra_images: true },
        { full_inspector: true, include_video: 'true' },
    ]) expect((await request.post('/__test__/reset', { data })).status()).toBe(400);
    await expect(await request.post('/__test__/reset', { data: { full_inspector: true, include_video: true, extra_images: 2 } })).toBeOK();
    const collection = await (await request.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector&limit=40')).json();
    expect(collection.total).toBe(11);
    const complete = collection.images.find(image => image.root_index === 0 && image.path === 'inspector/complete.png');
    const collision = collection.images.find(image => image.root_index === 1 && image.path === complete.path);
    expect(complete.root_scope).toMatch(/^[0-9a-f]{64}$/);
    expect(collision.root_scope).not.toBe(complete.root_scope);
    const inspect = async entry => {
        const response = await request.get(`/prompt_manager/images/inspect/${entry.path.split('/').map(encodeURIComponent).join('/')}?root=${entry.root_index}&root_scope=${entry.root_scope}`);
        await expect(response).toBeOK();
        expect(response.headers()['cache-control']).toBe('no-store');
        expect((await response.body()).length).toBeLessThanOrEqual(8 * 1024 * 1024);
        return response.json();
    };
    const result = await inspect(complete);
    expect(result.inspection_version).toBe(1);
    expect(result.image).toEqual({ root_index: 0, path: complete.path, filename: 'complete.png' });
    expect(result.root_scope).toBe(complete.root_scope);
    expect(result.prompts.positive.text).toContain('Inspector portrait 猫 😀');
    expect(result.prompts.negative.text).toBe('blurred edges, watermark');
    expect(result.metadata.prompt_graph.value['3'].inputs.seed).toBe('9007199254740993');
    expect(result.metadata.prompt_graph.value['3'].inputs.cfg).toBe('6.5');
    expect(result.metadata.record_formats).toEqual({ prompt: 'original-text', workflow: 'original-text' });
    expect(result.file).toMatchObject({ observation: 'current-file', format: 'PNG', width: 720, height: 480 });
    expect(result.library).toMatchObject({ status: 'linked', metadata_scope: 'pair', title: 'Inspector recipe', category: 'Inspection', tags: ['fixture', 'geometric'] });
    expect((await inspect(collision)).prompts.positive.text).toBe('Input collision');
    const gallery = await (await request.get(`/prompt_manager/library/${result.library.positive_id}/images?pair_id=${result.library.pair_id}`)).json();
    expect(gallery.images[0].root_scope).toBe(complete.root_scope);
    const library = await (await request.get('/prompt_manager/library')).json();
    expect(library.items.find(item => item.id === result.library.positive_id).previews.some(entry => entry.root_scope === complete.root_scope)).toBe(true);
    const workflow = await inspect(collection.images.find(image => image.path === 'inspector/workflow-only.png'));
    expect(workflow.prompts.positive.source).toBe('saved-association');
    const limited = await inspect(collection.images.find(image => image.path === 'inspector/control-heavy.png'));
    expect(limited.metadata).toMatchObject({ status: 'limited', omitted_reason: 'response-size-limit', raw: {}, parameters: null });
    expect(collection.images.some(image => image.path.endsWith('clip.webm'))).toBe(true);
    await expect(await request.post('/__test__/reset')).toBeOK();
});

const inspector=page=>page.locator('.image-details');
const selectFile=async(page,name,root=0)=>page.locator(`article[data-image-key="${root}:inspector/${name}"] .image-open`).click();
async function fullFixture(page,request) {
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();
    await page.goto('/prompt_manager/workspace#/images');await selectFile(page,'complete.png');
}
async function downloadText(page,action) {
    const [download]=await Promise.all([page.waitForEvent('download'),action.click()]);
    const fs=await import('node:fs/promises');return {name:download.suggestedFilename(),text:await fs.readFile(await download.path(),'utf8')};
}

test('full inspector shows six facts and scoped provenance',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});await fullFixture(page,request);const panel=inspector(page);
    await expect(panel.getByRole('heading',{name:'Inspector recipe',exact:true})).toBeVisible();await expect(panel.locator('.file-format')).toHaveText('PNG');await expect(panel.locator('.file-name')).toHaveText('complete.png');
    await expect(panel.locator('.file-evidence')).toHaveText('Workflow prompt evidence');
    expect(await panel.evaluate(node=>node.getBoundingClientRect().width)).toBe(310);
    expect(await panel.locator('.file-inspector-inner').evaluate(node=>getComputedStyle(node).padding)).toBe('17px');
    if (!await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)) {
        const copy=await panel.getByRole('button',{name:'Copy positive prompt',exact:true}).boundingBox();expect(copy.width).toBe(30);expect(copy.height).toBe(26);
        expect((await panel.getByRole('button',{name:'Send prompt pair',exact:true}).boundingBox()).height).toBe(30);
    }
    await expect(panel.locator('.generation-facts dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);
    await expect(panel.locator('.generation-facts dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','fixture-model.safetensors']);
    await expect(panel).toContainText('Recipe tags');await expect(panel).toContainText('Recipe category');
    await expect(panel.locator('.file-vocabulary button')).toHaveCount(0);
    await panel.getByText('File & provenance',{exact:true}).click();
    await expect(panel).toContainText('Current file at this source; historical bytes are not verified.');await expect(panel).toContainText('Saved association; not verified against current file');
    await expect(panel).toContainText('Configuration evidence');await expect(panel.getByRole('link',{name:'Prompt history',exact:true})).toHaveAttribute('href',/^#\/library\/\d+$/);
    await expect(panel).not.toContainText(/Run:|Original file unchanged|Execution recorded/);
    await selectFile(page,'empty.png');await expect(panel.locator('.file-format')).toHaveText('PNG');await expect(panel.getByRole('heading',{name:'empty.png',exact:true})).toBeVisible();
    await expect(panel.getByRole('button',{name:'Send negative prompt',exact:true})).toHaveCount(0);await expect(panel).toContainText('Not recorded in this file.');await expect(panel.getByRole('link',{name:'Prompt history',exact:true})).toHaveCount(0);
    await selectFile(page,'explicit-empty.png');await expect(panel).toContainText('Empty negative prompt');await expect(panel.getByRole('button',{name:'Send prompt pair',exact:true})).toHaveCount(1);
});

test('full inspector preserves long role text and exact sends',async({page,request,context})=>{
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');const target={id:'pair',label:'Exact pair',type:'PromptManagerPair',roles:['positive','negative']};window.sent=[];window.bridge=createBridgeHost({origin:location.origin,namespace:'/',label:'Exact host',adapter:{listTargets:()=>[target],preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:'',after}))},snapshot:roles}),apply:roles=>{window.sent.push(roles);return{targetId:target.id,values:roles};}}});});
    try {
        await fullFixture(page,request);const panel=inspector(page);const positive=panel.locator('.prompt-positive .prompt-text');await expect(positive).toContainText('猫 😀');const text=await positive.textContent();expect(text.length).toBeGreaterThan(1000);
        await panel.getByRole('button',{name:'Send prompt pair',exact:true}).click();const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        await expect(sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).locator('option')).toContainText(['Exact host']);
        await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'Exact host'});await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Exact pair'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.sent)).toEqual([{positive:text,negative:'blurred edges, watermark'}]);
    } finally {await host.evaluate(()=>window.bridge.dispose());await host.close();}
});

test('full inspector downloads actual bounded evidence',async({page,request})=>{
    await fullFixture(page,request);const panel=inspector(page);await panel.getByText('Workflow & raw metadata',{exact:true}).click();
    const list=await(await request.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector')).json();const image=list.images.find(image=>image.root_index===0&&image.path==='inspector/complete.png');
    const result=await(await request.get(`/prompt_manager/images/inspect/inspector/complete.png?root=0&root_scope=${image.root_scope}`)).json();
    await expect(panel).toContainText('Original embedded text');const metadata=await downloadText(page,panel.getByRole('button',{name:'Download metadata JSON',exact:true}));expect(metadata.name).toBe('complete.metadata.json');expect(JSON.parse(metadata.text).metadata.raw).toEqual(result.metadata.raw);expect(JSON.parse(metadata.text).identity.root_scope).toBe(image.root_scope);
    const workflow=await downloadText(page,panel.getByRole('button',{name:'Download workflow JSON',exact:true}));expect(workflow.name).toBe('complete.workflow.json');expect(workflow.text).toBe(result.metadata.raw.workflow);
    await panel.getByText('File & provenance',{exact:true}).click();await expect(panel.getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',image.url);
    await selectFile(page,'malformed.png');await panel.getByText('Workflow & raw metadata',{exact:true}).click();await expect(panel.locator('pre code').filter({hasText:'{broken'})).toHaveText('{broken');await expect(panel.getByRole('button',{name:'Download workflow JSON',exact:true})).toHaveAttribute('aria-disabled','true');
    await selectFile(page,'control-heavy.png');await panel.getByText('Workflow & raw metadata',{exact:true}).click();await expect(panel).toContainText('Raw metadata exceeds the response limit. Download the original file to inspect it externally.');
    let downloads=0;page.on('download',()=>downloads++);for(const name of ['Download metadata JSON','Download workflow JSON']) {const button=panel.getByRole('button',{name,exact:true});await expect(button).toHaveAttribute('aria-disabled','true');await button.focus();await page.keyboard.press('Enter');}expect(downloads).toBe(0);
    await panel.getByText('File & provenance',{exact:true}).click();await expect(panel.getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',/root=0&root_scope=[a-f0-9]{64}/);
});

test('full inspector rejects stale shared action state',async({page,request})=>{
    let reads=0,stale=false;await page.route('**/images/inspect/**',route=>{reads++;return stale?route.fulfill({status:409,json:{success:false,code:'source-changed',error:'Source changed'}}):route.continue();});
    await fullFixture(page,request);const panel=inspector(page);await expect(panel.getByRole('button',{name:'Send prompt pair',exact:true})).toBeVisible();const before=reads;
    await panel.getByRole('button',{name:'Open full preview',exact:true}).click();const preview=page.getByRole('dialog',{name:'Image preview',exact:true});await expect(preview.locator('.generation-facts dd').first()).toHaveText('9007199254740993');expect(reads).toBe(before);await preview.getByRole('button',{name:'Close preview',exact:true}).click();
    stale=true;await selectFile(page,'complete.png',1);await expect(panel).toContainText('The source changed. Refresh the image collection before inspecting this file.');
    await expect(panel.getByRole('button',{name:/Retry/})).toHaveCount(0);await expect(panel.locator('[data-copy-role],[data-send-role],a[download]')).toHaveCount(0);await expect(panel.getByRole('button',{name:/Download/})).toHaveCount(0);
    stale=false;const refreshed=page.waitForResponse(response=>response.url().includes('/images/output?'));await panel.getByRole('button',{name:'Refresh image collection',exact:true}).click();await refreshed;await selectFile(page,'complete.png',1);await expect(panel).toContainText('Input collision');
    const urls=await page.evaluate(()=>performance.getEntriesByType('resource').map(item=>item.name).filter(name=>name.includes('/images/inspect/')));expect(urls.every(value=>new URL(value).searchParams.has('root_scope'))).toBe(true);
});

test('full inspector keeps long literal content reachable on mobile',async({page,request})=>{
    await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Denied');}}}));
    const literal='<svg onload="window.executed=true"><image href="https://invalid.test/never"/></svg></pre>'+'verylongmodelpath/'.repeat(30);
    let external=0;await page.route('https://invalid.test/**',route=>{external++;return route.abort();});
    await page.route('**/images/inspect/**',async route=>{const response=await route.fetch();const data=await response.json();data.metadata.raw.parameters=literal;data.metadata.record_formats.parameters='original-text';data.metadata.parameters=literal;data.metadata.prompt_graph.value['4'].inputs.ckpt_name=literal;await route.fulfill({response,json:data});});
    await page.setViewportSize({width:390,height:844});await fullFixture(page,request);const panel=inspector(page);await expect(panel.locator('.prompt-positive pre')).toContainText('fine geometry and light.');await panel.getByRole('button',{name:'Copy positive prompt',exact:true}).click();await expect(panel).toContainText('Copy unavailable. Select the prompt text to copy it.');
    await panel.getByText('Workflow & raw metadata',{exact:true}).click();await expect(panel.locator('pre code').filter({hasText:literal}).last()).toHaveText(literal);await expect(panel.locator('.generation-facts dd').last()).toHaveText(literal);
    for(const zoom of [1,2]) {await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom);},zoom);await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);const footer=panel.getByRole('button',{name:'Send prompt pair',exact:true});await footer.focus();await expect(footer).toBeFocused();const summary=panel.getByText('File & provenance',{exact:true});await summary.focus();await page.keyboard.press('Enter');await expect(panel).toContainText('historical bytes are not verified');}
    expect(await page.evaluate(()=>window.executed)).toBeUndefined();expect(external).toBe(0);
});

test('embedded gallery uses the same full inspector',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();const list=await(await request.get('/prompt_manager/library')).json();const id=list.items.find(item=>item.text.startsWith('Inspector portrait')).id;
    await page.goto(`/prompt_manager/workspace#/library/${id}`);await selectFile(page,'complete.png');const panel=inspector(page);await expect(panel.getByRole('heading',{name:'Inspector recipe',exact:true})).toBeVisible();await expect(panel.locator('.generation-facts dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);await expect(panel).toContainText('Recipe tags');await panel.getByRole('button',{name:'Open full preview',exact:true}).click();await expect(page.locator('.image-preview .file-inspector')).toHaveCount(1);
});

test('full inspector labels container representations and releases action URLs',async({page,request})=>{
    await fullFixture(page,request);
    await page.evaluate(async()=>{
        const {mountFileInspector}=await import('/prompt_manager/js/workspace/file-inspector.mjs');
        const {toInspectionRecord,inspectPath}=await import('/prompt_manager/js/workspace/inspection-state.mjs');
        const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
        const api=window.ApiClient.createApiClient();const list=await api.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector');const image=list.images.find(item=>item.root_index===0&&item.path==='inspector/complete.png');
        const record=toInspectionRecord(await api.get(inspectPath(image)),image);record.file.media_type='video';record.metadata.record_formats.workflow='reserialized-json';record.metadata.record_formats.comfyassets_prompt_pairs='invalid';
        window.renderFixture={record,scope:createScope(),created:[],revoked:[],timers:0};
        const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL),timeout=window.setTimeout.bind(window);
        URL.createObjectURL=blob=>{const url=create(blob);window.renderFixture.created.push(url);return url;};URL.revokeObjectURL=url=>{window.renderFixture.revoked.push(url);revoke(url);};
        window.setTimeout=(callback,delay,...args)=>{if(delay===1000)window.renderFixture.timers++;return timeout(callback,delay,...args);};
        const container=document.createElement('section');container.id='render-fixture';document.body.append(container);
        window.renderFixture.view=mountFileInspector(container,{parentScope:window.renderFixture.scope});window.renderFixture.view.setState({record});
    });
    const panel=page.locator('#render-fixture');await panel.getByText('Workflow & raw metadata',{exact:true}).click();
    await expect(panel).toContainText('Reserialized container JSON; numeric spelling/precision may differ');await expect(panel).toContainText('Invalid container record; text is unavailable');
    await expect(panel.getByRole('button',{name:'Download workflow JSON',exact:true})).toHaveAttribute('aria-disabled','true');await expect(panel).toContainText('Original workflow JSON text is unavailable for this container record. Download metadata or the original file.');
    const metadata=await downloadText(page,panel.getByRole('button',{name:'Download metadata JSON',exact:true}));const value=JSON.parse(metadata.text);expect(value.metadata.record_formats.workflow).toBe('reserialized-json');expect(value.metadata.raw.workflow).toContain('9007199254740993');expect(value.metadata.raw.comfyassets_prompt_pairs).toBeUndefined();
    await page.evaluate(()=>window.renderFixture.view.setState({error:true,refreshRequired:true,message:'The source changed. Refresh the image collection before inspecting this file.'}));
    await expect(panel).toContainText('Reopen this file from a refreshed image collection.');await expect(panel.locator('button,a[download]')).toHaveCount(0);
    expect(await page.evaluate(()=>({created:window.renderFixture.created,revoked:window.renderFixture.revoked,timers:window.renderFixture.timers}))).toMatchObject({timers:1});
    await expect.poll(()=>page.evaluate(()=>window.renderFixture.created.length===window.renderFixture.revoked.length)).toBe(true);
    await page.evaluate(()=>{window.renderFixture.view.dispose();window.renderFixture.view.dispose();window.renderFixture.scope.dispose();});await expect(panel).toBeEmpty();
    expect(await page.evaluate(()=>window.renderFixture.revoked)).toEqual(await page.evaluate(()=>window.renderFixture.created));
});

test('full inspector distinguishes missing, limited and legacy unknown generation facts',async({page,request})=>{
    await fullFixture(page,request);const panel=inspector(page);
    await selectFile(page,'empty.png');await expect(panel.locator('.generation-facts dd')).toHaveText(Array(6).fill('Not recorded'));
    await page.locator('article[data-image-key="0:architecture/study-01.png"] .image-open').click();await expect(panel.locator('.generation-facts dd')).toHaveText(Array(6).fill('Not recorded'));
    await selectFile(page,'control-heavy.png');
    const explanation='Configuration exceeds inspection limits; download the original file to inspect it locally.';
    await expect(panel.getByText(explanation,{exact:true})).toHaveCount(1);await expect(panel.getByText(explanation,{exact:true})).toBeVisible();
    for(const value of await panel.locator('.generation-facts dd').all())await expect(value).toHaveAccessibleDescription(explanation);
    await page.evaluate(async()=>{
        const {mountFileInspector}=await import('/prompt_manager/js/workspace/file-inspector.mjs');
        const {toInspectionRecord}=await import('/prompt_manager/js/workspace/inspection-state.mjs');
        const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
        const container=document.createElement('section');container.id='legacy-facts';document.body.append(container);
        window.legacyFactsScope=createScope();window.legacyFactsView=mountFileInspector(container,{parentScope:window.legacyFactsScope});
        window.legacyFactsView.setState({record:toInspectionRecord({prompts:{}},{root_index:0,path:'legacy.png'})});
    });
    const legacy=page.locator('#legacy-facts');await expect(legacy.locator('.generation-facts dd')).toHaveText(Array(6).fill('Unresolved'));await expect(legacy).not.toContainText(explanation);await expect(legacy.locator('.generation-facts dd[aria-describedby]')).toHaveCount(0);
    await page.evaluate(()=>{window.legacyFactsView.dispose();window.legacyFactsScope.dispose();});
});

test.describe('full inspector coarse pointer',()=>{
    test.use({hasTouch:true,viewport:{width:390,height:844}});
    test('full inspector meets coarse target sizes and keeps long content reachable at zoom',async({page,request})=>{
        await fullFixture(page,request);expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(true);const panel=inspector(page);
        await expect(panel.locator('.prompt-positive pre')).toContainText('Full prompt context, fine geometry and light.');
        const fullText=await panel.locator('.prompt-positive pre').textContent();expect(fullText.length).toBeGreaterThan(1000);
        await panel.getByText('File & provenance',{exact:true}).click();await panel.getByText('Workflow & raw metadata',{exact:true}).click();
        const bounds=await panel.locator('button,a,summary').evaluateAll(nodes=>nodes.map(node=>({name:node.getAttribute('aria-label')||node.textContent,role:node.matches('.prompt-actions button'),width:node.getBoundingClientRect().width,height:node.getBoundingClientRect().height})));
        for(const target of bounds){expect(target.width,target.name).toBeGreaterThanOrEqual(target.role?36:40);expect(target.height,target.name).toBeGreaterThanOrEqual(target.role?36:40);}
        for(const zoom of [1,2]) {
            await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom);},zoom);
            await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
            await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
            for(const target of [panel.locator('summary').first(),panel.getByRole('button',{name:'Download metadata JSON',exact:true}),panel.getByRole('link',{name:'Prompt history',exact:true})]) {
                await target.scrollIntoViewIfNeeded();await target.focus();await expect(target).toBeFocused();await expect(target).toBeInViewport();
            }
            await expect(panel.locator('.prompt-positive pre')).toHaveText(fullText);
        }
    });
});
