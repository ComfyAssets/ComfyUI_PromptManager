import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept, openConcept, openProduction, SIZES } from './concept-inventory.mjs';
// D10/D20: the heading is the linked positive's title (its confirmed first line here), never the pair's
// "Inspector recipe", which names the negative variant.
const POSITIVE_TITLE='Inspector portrait 猫 😀';

test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
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
    // Differing branches show what the legacy parser shows: the first sampler's seed.
    expect(Object.values(result.ambiguous.generation).map(fact => [fact.status, fact.value])).toEqual([
        ['known', '9007199254740993'], ['known', '28'], ['known', '6.5'], ['known', 'dpmpp_2m'], ['known', 'karras'], ['known', 'fixture-model.safetensors']]);
    expect(Object.values(result.ambiguous.generation).every(fact => fact.sampler_id === null)).toBe(true);
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
    expect(result.library).toMatchObject({ status: 'linked', category_scope: 'pair', tags_scope: 'pair', title: POSITIVE_TITLE, category: 'Inspection', tags: ['fixture', 'geometric'] });
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

const inspector=page=>page.locator('.detail-panel');
/** D19: the selected card's image opens the viewer (the side panel has no full-preview cover). */
const openSelected=page=>page.locator('.media-card.selected .image-open').click();
const selectFile=async(page,name,root=0)=>page.locator(`article[data-image-key="${root}:inspector/${name}"] .image-card-actions button:first-child`).click();
async function fullFixture(page,request) {
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();
    await page.goto('/prompt_manager/workspace#/images');await selectFile(page,'complete.png');
}
async function downloadText(page,action) {
    const [download]=await Promise.all([page.waitForEvent('download'),action.click()]);
    const fs=await import('node:fs/promises');return {name:download.suggestedFilename(),text:await fs.readFile(await download.path(),'utf8')};
}
/** The viewer's full details for one file (D19: the card image opens the viewer; D20: only it shows provenance and raw). */
async function viewerDetails(page,name,root=0) {
    await page.locator(`article[data-image-key="${root}:inspector/${name}"] .image-open`).click();
    const details=page.locator('dialog#viewer-dialog #viewer-details');await expect(details.locator('.panel-heading')).toBeVisible();return details;
}
/** Rewrites the next inspection reads; `null` passes them through. */
async function patchInspection(page) {
    const state={patch:null};
    await page.route('**/images/inspect/**',async route=>{
        if (!state.patch) return route.continue();
        const response=await route.fetch();const data=await response.json();state.patch(data);await route.fulfill({response,json:data});
    });
    return state;
}
/** The side panel's concept structure (app.js imageDetails a400–414): element, class and visible text, in order. */
const panelStructure=panel=>panel.evaluate(node=>{
    const describe=item=>`${item.localName}${item.className?'.'+item.className.trim().split(/\s+/).join('.'):''}`;
    const inner=node.querySelector(':scope > .detail-inner');
    const flatten=item=>item.classList.contains('prompt-inspector')?[...item.children].map(describe):[describe(item)];
    return {outer:[...node.children].map(describe),inner:inner?[...inner.children].flatMap(flatten):null};
});

test('panel matches the concept order and copy',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});const reads=await patchInspection(page);
    await fullFixture(page,request);const panel=page.getByRole('complementary',{name:'Selected image details',exact:true});
    await expect(panel).toHaveAttribute('id','image-inspector');await expect(panel).toHaveClass('detail-panel file-inspector');
    // Linked, workflow inferred: every concept element.
    await expect(panel.locator('.panel-heading h2')).toHaveText(POSITIVE_TITLE);
    expect(await panelStructure(panel)).toEqual({outer:['img.detail-cover','div.detail-inner','div.detail-footer'],
        inner:['div.panel-heading','p.small','div.tags','section.prompt-section.positive','section.prompt-section.negative','div.tags','dl.meta-grid']});
    await expect(panel.locator('img.detail-cover')).toHaveAttribute('alt','');
    await expect(panel.locator('.panel-heading .chip')).toHaveText('PNG');
    await expect(panel.locator('.detail-inner > p.small')).toHaveText('complete.png');
    const badges=panel.locator('.detail-inner > .tags').first();
    await expect(badges.locator('span.chip.blue')).toHaveText('Workflow inferred');
    await expect(badges.locator('span.chip:not(.blue)')).toHaveText('Recipe category: Inspection');
    await expect(badges.locator('span.chip:not(.blue) .ca-visually-hidden')).toHaveText('Recipe category: ');
    // Recipe tags are not the positive tags the Images Tags filter matches, so they stay plain chips (D21 ruling).
    await expect(panel.locator('.detail-inner > .tags').last().locator('span.chip')).toHaveText(['fixture','geometric']);
    await expect(panel.locator('.detail-inner > .tags button')).toHaveCount(0);
    await expect(panel.locator('.meta-grid dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);
    await expect(panel.locator('.meta-grid dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','fixture-model.safetensors']);
    const footer=panel.locator('.detail-footer');
    await expect(footer.locator('button')).toHaveText(['Send to ComfyUI','Prompt history']);
    await expect(footer.locator('button.send')).toHaveAccessibleName(`Send prompt pair to ComfyUI: ${POSITIVE_TITLE}`);
    await expect(footer.locator('button.send svg.icon')).toHaveCount(1);await expect(footer.locator('button:not(.send) svg.icon')).toHaveCount(1);
    await expect(panel).not.toContainText(/prompt evidence|Recipe tags|Positive prompt tags/);
    // Execution recorded and embedded parameters (D21), with the extension chip naming an unverified format.
    reads.patch=data=>{for (const role of ['positive','negative']) data.prompts[role].source='execution';};
    await selectFile(page,'explicit-empty.png');await expect(panel.locator('.panel-heading h2')).toHaveText('explicit-empty.png');
    await expect(panel.locator('span.chip.blue')).toHaveText('Execution recorded');
    reads.patch=data=>{data.prompts={positive:{status:'resolved',text:'embedded positive',source:'embedded-parameters'},negative:{status:'resolved',text:'embedded negative',source:'embedded-parameters'}};data.file.format=null;};
    await selectFile(page,'ambiguous.png');await expect(panel.locator('.prompt-section.positive .prompt-text')).toHaveText('embedded positive');
    await expect(panel.locator('span.chip.blue')).toHaveText('Embedded parameters');
    await expect(panel.locator('.panel-heading .chip')).toHaveText('File extension PNG');await expect(panel.locator('.panel-heading .chip .ca-visually-hidden')).toHaveText('File extension ');
    await expect(panel.locator('.detail-footer button')).toHaveText(['Send to ComfyUI']);
    // Unlinked: no badge, category, tags, pair or history.
    reads.patch=null;await selectFile(page,'empty.png');await expect(panel.locator('.panel-heading h2')).toHaveText('empty.png');
    expect(await panelStructure(panel)).toEqual({outer:['img.detail-cover','div.detail-inner'],
        inner:['div.panel-heading','p.small','section.prompt-section.positive','section.prompt-section.negative','dl.meta-grid']});
    await expect(panel.locator('.meta-grid dd')).toHaveText(Array(6).fill('Not recorded'));
    // Ambiguous saved association: the same honest absence.
    reads.patch=data=>{data.library={...data.library,status:'ambiguous',positive_id:null,pair_id:null,title:null,category:null,tags:[],category_scope:null,tags_scope:null};};
    await selectFile(page,'workflow-only.png');await expect(panel.locator('.panel-heading h2')).toHaveText('workflow-only.png');
    await expect(panel.locator('.chip:not(.blue)')).toHaveCount(1);await expect(panel.locator('.detail-inner > .tags button')).toHaveCount(0);
    await expect(panel.getByRole('button',{name:'Prompt history',exact:true})).toHaveCount(0);
    // D39 mixed scopes: each field follows its own source, so the prefix and the tag chips can disagree.
    const tagRow=panel.locator('.detail-inner > .tags').last();
    reads.patch=data=>{data.library={...data.library,status:'linked',category:'Subject',category_scope:'pair',tags_scope:'positive',tags:['alpha','beta']};};
    await selectFile(page,'complete.png');
    await expect(panel.locator('span.chip:not(.blue) .ca-visually-hidden')).toHaveText('Recipe category: ');
    await expect(tagRow.getByRole('button')).toHaveText(['alpha','beta']);
    reads.patch=data=>{data.library={...data.library,status:'linked',category:'Subject',category_scope:'positive',tags_scope:'pair',tags:['alpha','beta']};};
    await selectFile(page,'workflow-only.png');
    await expect(panel.locator('span.chip:not(.blue) .ca-visually-hidden')).toHaveText('Positive prompt category: ');
    await expect(tagRow.locator('span.chip')).toHaveText(['alpha','beta']);await expect(tagRow.getByRole('button')).toHaveCount(0);
    // Loading and error states render in `.detail-inner` as `.small` text with the existing buttons.
    reads.patch=null;await page.unroute('**/images/inspect/**');
    await page.route('**/images/inspect/**',route=>route.fulfill({status:500,json:{success:false,error:'broken'}}));
    await selectFile(page,'malformed.png');
    await expect(panel.locator('.detail-inner > p.small[role="alert"]')).toHaveText('Could not read prompts from this file.');
    await expect(panel.locator('.detail-inner > button')).toHaveText(['Retry prompt details']);
});

test('tag chip filters Images by that tag',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();
    const list=await(await request.get('/prompt_manager/library')).json();const id=list.items.find(item=>item.text.startsWith('Inspector portrait')).id;
    await expect(await request.post('/prompt_manager/prompts/tags',{data:{prompt_id:id,tags:['geometric']}})).toBeOK();
    // workflow-only.png is linked to the positive alone, so its chips are the positive's tags, which the filter matches.
    await page.goto('/prompt_manager/workspace#/images');await selectFile(page,'workflow-only.png');
    const panel=page.getByRole('complementary',{name:'Selected image details',exact:true});
    const listing=page.waitForRequest(item=>item.url().includes('/images/output?')&&new URL(item.url()).searchParams.has('tags'));
    await panel.locator('.detail-inner > .tags').getByRole('button',{name:'geometric',exact:true}).click();
    expect(new URL((await listing).url()).searchParams.get('tags')).toBe('geometric');
    await expect(page.locator('article[data-image-key="0:inspector/workflow-only.png"]')).toBeVisible();
    await expect(page.locator('article[data-image-key="0:inspector/complete.png"]')).toBeVisible();
    await expect(page.locator('article[data-image-key="0:inspector/empty.png"]')).toHaveCount(0);
    // #249 D44: the chip's Tags filter shows on the toolbar's Filters button.
    await page.getByRole('button',{name:'Filters · 1',exact:true}).click();
    await expect(page.getByRole('dialog').getByRole('textbox',{name:'Tags',exact:true})).toHaveValue('geometric');
});

test('pair send and history appear only with real roles and link',async({page,request})=>{
    await fullFixture(page,request);const panel=page.getByRole('complementary',{name:'Selected image details',exact:true});
    await panel.getByRole('button',{name:'Prompt history',exact:true}).click();await expect(page).toHaveURL(/#\/library\/\d+$/);
    await page.goto('/prompt_manager/workspace#/images');await selectFile(page,'explicit-empty.png');
    await expect(panel.getByRole('button',{name:'Send prompt pair to ComfyUI: explicit-empty.png',exact:true})).toBeVisible();
    await expect(panel.getByRole('button',{name:'Prompt history',exact:true})).toHaveCount(0);
    await selectFile(page,'empty.png');await expect(panel.locator('.panel-heading h2')).toHaveText('empty.png');
    await expect(panel.locator('.detail-footer')).toHaveCount(0);await expect(panel.getByRole('button',{name:/^Send prompt pair/})).toHaveCount(0);
});

test('no provenance, raw or download controls in the side panel',async({page,request})=>{
    await fullFixture(page,request);const panel=page.getByRole('complementary',{name:'Selected image details',exact:true});
    await expect(panel.locator('.panel-heading h2')).toHaveText(POSITIVE_TITLE);
    await expect(panel.locator('details,summary,pre,a')).toHaveCount(0);
    await expect(panel.getByRole('button',{name:/Download|Open full preview/})).toHaveCount(0);
    await expect(panel).not.toContainText(/File & provenance|Workflow & raw metadata|Download/);
    // The viewer keeps them until its own full details (D3).
    const details=await viewerDetails(page,'complete.png');
    await expect(details.locator('summary')).toHaveText(['File & provenance','Workflow & raw metadata']);
    await details.getByText('Workflow & raw metadata',{exact:true}).click();
    await expect(details.getByRole('button',{name:'Download metadata JSON',exact:true})).toBeVisible();
});

/** The viewer's full details (app.js imageDetails(i, true)): its children, and the class-less wrapper's in order. */
const viewerStructure=details=>details.evaluate(node=>{
    const describe=item=>`${item.localName}${item.className?'.'+item.className.trim().split(/\s+/).join('.'):''}`;
    const inner=node.querySelector(':scope > div');
    const flatten=item=>item.classList.contains('prompt-inspector')?[...item.children].map(describe):[describe(item)];
    return {outer:[...node.children].map(describe),inner:inner?[...inner.children].flatMap(flatten):null};
});

test('viewer details hold provenance, raw records and downloads',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});const reads=await patchInspection(page);
    await fullFixture(page,request);let details=await viewerDetails(page,'complete.png');
    // The panel content without the cover, D-NEW-1's Copy settings summary after the facts, then the two disclosures,
    // then the pair send and history buttons (D20).
    expect(await viewerStructure(details)).toEqual({outer:['div','button.send','button'],
        inner:['div.panel-heading','p.small','div.tags','section.prompt-section.positive','section.prompt-section.negative','div.tags','dl.meta-grid','button','details','details']});
    await expect(details.locator(':scope > div > button')).toHaveText('Copy settings summary');
    await expect(details.locator('.detail-cover,.detail-inner,.detail-footer')).toHaveCount(0);
    await expect(details.locator('.panel-heading h2')).toHaveText(POSITIVE_TITLE);await expect(details.locator('.panel-heading .chip')).toHaveText('PNG');
    await expect(details.locator(':scope > div > p.small')).toHaveText('complete.png');
    await expect(details.locator('span.chip.blue')).toHaveText('Workflow inferred');
    await expect(details.locator(':scope > div > .tags').last().locator('span.chip')).toHaveText(['fixture','geometric']);
    await expect(details.locator('.meta-grid').first().locator('dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','fixture-model.safetensors']);
    await expect(details.locator('summary')).toHaveText(['File & provenance','Workflow & raw metadata']);
    await expect(details.locator(':scope > button')).toHaveText(['Send to ComfyUI','Prompt history']);
    await expect(details.locator(':scope > button.send')).toHaveAccessibleName(`Send prompt pair to ComfyUI: ${POSITIVE_TITLE}`);
    // File & provenance: the honest facts and the concept's "Prompt evidence" line; no per-fact source lines.
    const provenance=details.locator('details').first();await provenance.locator('summary').click();
    await expect(provenance.locator('p.small').first()).toHaveText('Current file at this source; historical bytes are not verified.');
    await expect(provenance.locator('dl.meta-grid dt')).toHaveText(['Source','Path','Size','Dimensions','Format','Media type','Modified']);
    // D38: the Path value stays text, with "Show in folder" right after it.
    await expect(provenance.locator('dl.meta-grid dd').nth(1)).toHaveText('inspector/complete.png Show in folder');
    await expect(provenance.getByText('Prompt evidence: workflow inferred',{exact:true})).toBeVisible();
    await expect(provenance.getByText('Saved association; not verified against current file',{exact:true})).toBeVisible();
    await expect(provenance.getByRole('link',{name:'Download original',exact:true})).toBeVisible();
    await expect(details).not.toContainText(/Configuration evidence|Embedded parameter evidence|prompt evidence|extension|Recipe tags/);
    // Workflow & raw metadata: the concept's focusable `pre`, then the downloads with their unavailable reasons.
    const raw=details.locator('details').last();await raw.locator('summary').click();
    await expect(raw.locator('pre[tabindex="0"] > code').first()).toBeVisible();
    for (const name of ['Download metadata JSON','Download workflow JSON']) await expect(raw.getByRole('button',{name,exact:true})).toBeVisible();
    // D21 wording for execution and embedded parameters; an unverified format names its extension without a dot.
    reads.patch=data=>{for (const role of ['positive','negative']) data.prompts[role].source='execution';};
    await page.keyboard.press('Escape');details=await viewerDetails(page,'explicit-empty.png');await details.getByText('File & provenance',{exact:true}).click();
    await expect(details.getByText('Prompt evidence: execution recorded',{exact:true})).toBeVisible();
    reads.patch=data=>{data.prompts={positive:{status:'resolved',text:'embedded positive',source:'embedded-parameters'},negative:{status:'resolved',text:'embedded negative',source:'embedded-parameters'}};data.file.format=null;};
    await page.keyboard.press('Escape');details=await viewerDetails(page,'ambiguous.png');await details.getByText('File & provenance',{exact:true}).click();
    await expect(details.getByText('Prompt evidence: embedded parameters',{exact:true})).toBeVisible();
    await expect(details.locator('.panel-heading .chip')).toHaveText('File extension PNG');
    await expect(details.locator('details dl.meta-grid dd').nth(4)).toHaveText('PNG extension');await expect(details).not.toContainText('.PNG');
    // No badge, no line; no resolved pair or link, no buttons after the details.
    reads.patch=null;await page.keyboard.press('Escape');details=await viewerDetails(page,'empty.png');
    expect((await viewerStructure(details)).outer).toEqual(['div']);
    await details.getByText('File & provenance',{exact:true}).click();await expect(details).not.toContainText('Prompt evidence');
    // The history button leaves the viewer for the prompt's history.
    await page.keyboard.press('Escape');details=await viewerDetails(page,'complete.png');
    await details.getByRole('button',{name:'Prompt history',exact:true}).click();await expect(page).toHaveURL(/#\/library\/\d+$/);
    await expect(page.locator('#viewer-dialog')).toHaveCount(0);
});

// D38 Show in folder: a listed file's Path offers it; the stubbed opener records what the route resolved.
test('Show in folder asks the server to open the listed file and announces each outcome',async({page,request})=>{
    await fullFixture(page,request);
    await expect(inspector(page).getByRole('button',{name:/in folder$/})).toHaveCount(0);
    const details=await viewerDetails(page,'complete.png');
    const provenance=details.locator('details').first();await provenance.locator('summary').click();
    const path=provenance.locator('dl.meta-grid > div').nth(1);await expect(path.locator('dt')).toHaveText('Path');
    const show=path.locator('dd > button');
    await expect(show).toHaveText('Show in folder');await expect(show).toHaveAccessibleName('Show complete.png in folder');
    const toast=page.locator('#workspace-toast');
    const [sent]=await Promise.all([page.waitForRequest(entry=>entry.url().endsWith('/prompt_manager/images/reveal')),show.click()]);
    expect(sent.method()).toBe('POST');
    // The route takes only a same-origin JSON request, as a real browser sends it.
    const sentHeaders=await sent.allHeaders();
    expect(sentHeaders.origin).toBe(new URL(page.url()).origin);expect(sentHeaders['content-type']).toBe('application/json');
    const body=sent.postDataJSON();
    expect(Object.keys(body).sort()).toEqual(['path','root_index','root_scope']);
    expect(body).toMatchObject({root_index:0,path:'inspector/complete.png'});expect(body.root_scope).toMatch(/^[0-9a-f]{64}$/);
    await expect(toast).toHaveText('Opened the folder on this computer.');
    const opened=async()=>(await (await request.get('/__test__/reveal')).json()).opened;
    expect(await opened()).toEqual(['output/inspector/complete.png']);
    for (const [outcome,message] of [['unavailable','No file manager is available on this computer.'],['fail','The folder could not be opened. Try again.']]) {
        await expect(await request.post('/__test__/reveal',{data:{outcome}})).toBeOK();
        await show.click();await expect(toast).toHaveText(message);
    }
    // A reveal already in progress answers 429 busy.
    await page.route('**/prompt_manager/images/reveal',route=>route.fulfill({status:429,contentType:'application/json',body:JSON.stringify({success:false,code:'busy',error:'The folder is already opening.'})}));
    await show.click();await expect(toast).toHaveText('The folder is already opening.');
    await page.unroute('**/prompt_manager/images/reveal');
    // A proxied request counts as remote, so the real route answers 403 not-local.
    await expect(await request.post('/__test__/reveal',{data:{outcome:'opened'}})).toBeOK();
    await page.route('**/prompt_manager/images/reveal',route=>route.continue({headers:{...route.request().headers(),'x-forwarded-for':'203.0.113.7'}}));
    await show.click();await expect(toast).toHaveText('Show in folder works only in a browser on the computer running ComfyUI.');
    expect(await opened()).toEqual(['output/inspector/complete.png']);
});
test('full inspector shows six facts and scoped provenance',async({page,request},testInfo)=>{
    await page.setViewportSize({width:1600,height:1100});await fullFixture(page,request);const panel=inspector(page);
    await expect(panel.getByRole('heading',{name:POSITIVE_TITLE,exact:true})).toBeVisible();await expect(panel.locator('.panel-heading .chip')).toHaveText('PNG');await expect(panel.locator('.detail-inner > p.small')).toHaveText('complete.png');
    expect(await panel.evaluate(node=>node.getBoundingClientRect().width)).toBe(310);
    expect(await panel.locator('.detail-inner').evaluate(node=>getComputedStyle(node).padding)).toBe('17px');
    if (!await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)) {
        // The concept's prompt actions (`.prompt-actions button.icon-btn`) measure 31×31 in the mock.
        const copy=await panel.getByRole('button',{name:/^Copy positive prompt: /}).boundingBox();expect(copy.width).toBe(31);expect(copy.height).toBe(31);
    }
    await expect(panel.locator('.meta-grid dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);
    await expect(panel.locator('.meta-grid dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','fixture-model.safetensors']);
    await selectFile(page,'empty.png');await expect(panel.locator('.panel-heading .chip')).toHaveText('PNG');await expect(panel.getByRole('heading',{name:'empty.png',exact:true})).toBeVisible();
    await expect(panel.getByRole('button',{name:/^Send negative prompt to ComfyUI: /})).toHaveCount(0);await expect(panel).toContainText('Not recorded in this file.');await expect(panel.getByRole('button',{name:'Prompt history',exact:true})).toHaveCount(0);
    await selectFile(page,'explicit-empty.png');await expect(panel.locator('.prompt-section.negative p.prompt-text')).toHaveText('No negative prompt (explicitly empty)');await expect(panel.getByRole('button',{name:/^Send prompt pair/})).toHaveCount(1);
    // Provenance lives in the viewer's full details (D20): one "Prompt evidence" line replaces the per-fact source lines.
    const details=await viewerDetails(page,'complete.png');
    await expect(details.locator('span.chip.blue')).toHaveText('Workflow inferred');await expect(details).toContainText('Recipe category: Inspection');
    await details.getByText('File & provenance',{exact:true}).click();
    await expect(details).toContainText('Current file at this source; historical bytes are not verified.');await expect(details).toContainText('Saved association; not verified against current file');
    await expect(details.getByText('Prompt evidence: workflow inferred',{exact:true})).toBeVisible();
    await expect(details).not.toContainText(/Configuration evidence|Embedded API configuration|Saved workflow configuration/);
    await expect(details.getByRole('button',{name:'Prompt history',exact:true})).toBeVisible();
    if (testInfo.project.name === 'chromium') {
        await details.getByText('Prompt evidence: workflow inferred',{exact:true}).scrollIntoViewIfNeeded();
        const path=testInfo.outputPath('prompt-evidence.png');await page.screenshot({path});await testInfo.attach('prompt-evidence',{path,contentType:'image/png'});
    }
    await expect(details).not.toContainText(/Run:|Original file unchanged|Execution recorded/);
});

test('full inspector preserves long role text and exact sends',async({page,request,context})=>{
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');const target={id:'pair',label:'Exact pair',type:'PromptManagerPair',roles:['positive','negative']};window.sent=[];window.bridge=createBridgeHost({origin:location.origin,namespace:'/',label:'Exact host',adapter:{listTargets:()=>[target],preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:'',after}))},snapshot:roles}),apply:roles=>{window.sent.push(roles);return{targetId:target.id,values:roles};}}});});
    try {
        await fullFixture(page,request);const panel=inspector(page);const positive=panel.locator('.prompt-section.positive .prompt-text');await expect(positive).toContainText('猫 😀');const text=await positive.textContent();expect(text.length).toBeGreaterThan(1000);
        await panel.locator('.detail-footer button.send').click();const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        await expect(sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).locator('option')).toContainText(['Exact host']);
        await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'Exact host'});await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Exact pair'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.sent)).toEqual([{positive:text,negative:'blurred edges, watermark'}]);
    } finally {await host.evaluate(()=>window.bridge.dispose());await host.close();}
});

test('full inspector downloads actual bounded evidence',async({page,request})=>{
    await fullFixture(page,request);let panel=await viewerDetails(page,'complete.png');await panel.getByText('Workflow & raw metadata',{exact:true}).click();
    const list=await(await request.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector')).json();const image=list.images.find(image=>image.root_index===0&&image.path==='inspector/complete.png');
    const result=await(await request.get(`/prompt_manager/images/inspect/inspector/complete.png?root=0&root_scope=${image.root_scope}`)).json();
    await expect(panel).toContainText('Original embedded text');const metadata=await downloadText(page,panel.getByRole('button',{name:'Download metadata JSON',exact:true}));expect(metadata.name).toBe('complete.metadata.json');expect(JSON.parse(metadata.text).metadata.raw).toEqual(result.metadata.raw);expect(JSON.parse(metadata.text).identity.root_scope).toBe(image.root_scope);
    const workflow=await downloadText(page,panel.getByRole('button',{name:'Download workflow JSON',exact:true}));expect(workflow.name).toBe('complete.workflow.json');expect(workflow.text).toBe(result.metadata.raw.workflow);
    await panel.getByText('File & provenance',{exact:true}).click();await expect(panel.getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',image.url);
    await page.keyboard.press('Escape');panel=await viewerDetails(page,'malformed.png');await panel.getByText('Workflow & raw metadata',{exact:true}).click();await expect(panel.locator('pre code').filter({hasText:'{broken'})).toHaveText('{broken');await expect(panel.getByRole('button',{name:'Download workflow JSON',exact:true})).toHaveAttribute('aria-disabled','true');
    await page.keyboard.press('Escape');panel=await viewerDetails(page,'control-heavy.png');await panel.getByText('Workflow & raw metadata',{exact:true}).click();await expect(panel).toContainText('Raw metadata exceeds the response limit. Download the original file to inspect it externally.');
    let downloads=0;page.on('download',()=>downloads++);for(const name of ['Download metadata JSON','Download workflow JSON']) {const button=panel.getByRole('button',{name,exact:true});await expect(button).toHaveAttribute('aria-disabled','true');await button.focus();await page.keyboard.press('Enter');}expect(downloads).toBe(0);
    await panel.getByText('File & provenance',{exact:true}).click();await expect(panel.getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',/root=0&root_scope=[a-f0-9]{64}/);
});

test('full inspector labels a limited workflow record without hiding prompt facts',async({page,request})=>{
    // The server marks a workflow over its 4 MiB read budget `limited`: no raw text, a limited graph.
    await page.route('**/images/inspect/inspector/complete.png?*',async route=>{const response=await route.fetch();const data=await response.json();data.metadata.record_formats.workflow='limited';delete data.metadata.raw.workflow;data.metadata.workflow_graph={status:'limited',value:null};await route.fulfill({response,json:data});});
    await fullFixture(page,request);const panel=await viewerDetails(page,'complete.png');
    await expect(panel.locator('.meta-grid').first().locator('dd')).toHaveText(['9007199254740993','28','6.5','dpmpp_2m','karras','fixture-model.safetensors']);
    await expect(panel.getByRole('button',{name:/^Send prompt pair to ComfyUI: /})).toBeVisible();
    await panel.getByText('Workflow & raw metadata',{exact:true}).click();
    await expect(panel.getByText('Record exceeds inspection limits; text is unavailable',{exact:true})).toBeVisible();await expect(panel.locator('pre code')).toHaveCount(1);
    const workflow=panel.getByRole('button',{name:'Download workflow JSON',exact:true});await expect(workflow).toHaveAttribute('aria-disabled','true');
    await expect(workflow).toHaveAccessibleDescription('Workflow exceeds inspection limits. Download the original file to inspect it externally.');
    let downloads=0;const count=()=>downloads++;page.on('download',count);await workflow.focus();await page.keyboard.press('Enter');expect(downloads).toBe(0);page.off('download',count);
    const metadata=JSON.parse((await downloadText(page,panel.getByRole('button',{name:'Download metadata JSON',exact:true}))).text);
    expect(metadata.metadata.record_formats.workflow).toBe('limited');expect(metadata.metadata.raw.workflow).toBeUndefined();expect(metadata.metadata.representations.workflow).toBe('Record exceeds inspection limits; text is unavailable');
});

test('full inspector rejects stale shared action state',async({page,request})=>{
    let reads=0,stale=false;await page.route('**/images/inspect/**',route=>{reads++;return stale?route.fulfill({status:409,json:{success:false,code:'source-changed',error:'Source changed'}}):route.continue();});
    await fullFixture(page,request);const panel=inspector(page);await expect(panel.locator('.detail-footer button.send')).toBeVisible();const before=reads;
    await openSelected(page);const preview=page.locator('dialog#viewer-dialog');await expect(preview.locator('.meta-grid dd').first()).toHaveText('9007199254740993');expect(reads).toBe(before);await preview.getByRole('button',{name:'Close image viewer',exact:true}).click();
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
    await page.setViewportSize({width:390,height:844});await fullFixture(page,request);const panel=inspector(page);await expect(panel.locator('.prompt-section.positive p.prompt-text')).toContainText('fine geometry and light.');await panel.getByRole('button',{name:/^Copy positive prompt: /}).click();const fallback=page.getByRole('dialog',{name:'Copy prompt',exact:true});await expect(fallback.getByRole('textbox')).toHaveValue(/^Inspector portrait 猫 😀\nFull prompt context, fine geometry and light\./);await page.keyboard.press('Escape');await expect(fallback).toBeHidden();
    await expect(panel.locator('.meta-grid dd').last()).toHaveText(literal);
    for(const zoom of [1,2]) {await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom);},zoom);await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);const footer=panel.locator('.detail-footer button.send');await footer.focus();await expect(footer).toBeFocused();}
    await page.evaluate(()=>{document.documentElement.style.zoom='1';});
    const details=await viewerDetails(page,'complete.png');await details.getByText('Workflow & raw metadata',{exact:true}).click();await expect(details.locator('pre code').filter({hasText:literal}).last()).toHaveText(literal);
    const summary=details.getByText('File & provenance',{exact:true});await summary.focus();await page.keyboard.press('Enter');await expect(details).toContainText('historical bytes are not verified');
    expect(await page.evaluate(()=>window.executed)).toBeUndefined();expect(external).toBe(0);
});

test('embedded gallery uses the same full inspector',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();const list=await(await request.get('/prompt_manager/library')).json();const id=list.items.find(item=>item.text.startsWith('Inspector portrait')).id;
    await page.goto(`/prompt_manager/workspace#/library/${id}`);await selectFile(page,'complete.png');const panel=inspector(page);await expect(panel.getByRole('heading',{name:POSITIVE_TITLE,exact:true})).toBeVisible();await expect(panel.locator('.meta-grid dt')).toHaveText(['Seed','Steps','CFG','Sampler','Scheduler','Model']);await expect(panel.locator('.detail-inner > .tags').last().locator('span.chip')).toHaveText(['fixture','geometric']);await expect(panel).not.toHaveAttribute('id','image-inspector');await openSelected(page);await expect(page.locator('#viewer-dialog .file-inspector')).toHaveCount(1);
});

test('Prompt history to the shown route closes the viewer and re-renders it, as the concept go() does',async({page,request})=>{
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();const list=await(await request.get('/prompt_manager/library')).json();const id=list.items.find(item=>item.text.startsWith('Inspector portrait')).id;
    await page.goto(`/prompt_manager/workspace#/library/${id}`);await selectFile(page,'complete.png');await expect(inspector(page).getByRole('heading',{name:POSITIVE_TITLE,exact:true})).toBeVisible();
    await openSelected(page);const viewer=page.locator('dialog#viewer-dialog');await expect(viewer).toBeVisible();
    const heading=page.locator('#workspace-main h1');await heading.evaluate(node=>{node.dataset.beforeHistory='true';});
    await viewer.locator('#viewer-details').getByRole('button',{name:'Prompt history',exact:true}).click();
    await expect(page.locator('dialog#viewer-dialog[open]')).toHaveCount(0);await expect(page).toHaveURL(new RegExp(`#/library/${id}$`));
    await expect(page.locator('#workspace-main h1[data-before-history]')).toHaveCount(0);await expect(heading).toBeFocused();
    // The side panel's Prompt history does the same.
    await selectFile(page,'complete.png');await heading.evaluate(node=>{node.dataset.beforeHistory='true';});
    await inspector(page).getByRole('button',{name:'Prompt history',exact:true}).click();
    await expect(page.locator('#workspace-main h1[data-before-history]')).toHaveCount(0);await expect(heading).toBeFocused();
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
    await selectFile(page,'empty.png');await expect(panel.locator('.meta-grid dd')).toHaveText(Array(6).fill('Not recorded'));
    await page.locator('article[data-image-key="0:architecture/study-01.png"] .image-card-actions button:first-child').click();await expect(panel.locator('.meta-grid dd')).toHaveText(Array(6).fill('Not recorded'));
    await selectFile(page,'control-heavy.png');
    const explanation='Configuration exceeds inspection limits; download the original file to inspect it locally.';
    await expect(panel.getByText(explanation,{exact:true})).toHaveCount(1);await expect(panel.getByText(explanation,{exact:true})).toBeVisible();
    for(const value of await panel.locator('.meta-grid dd').all())await expect(value).toHaveAccessibleDescription(explanation);
    await page.evaluate(async()=>{
        const {mountFileInspector}=await import('/prompt_manager/js/workspace/file-inspector.mjs');
        const {toInspectionRecord}=await import('/prompt_manager/js/workspace/inspection-state.mjs');
        const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
        const container=document.createElement('section');container.id='legacy-facts';document.body.append(container);
        window.legacyFactsScope=createScope();window.legacyFactsView=mountFileInspector(container,{parentScope:window.legacyFactsScope});
        window.legacyFactsView.setState({record:toInspectionRecord({prompts:{}},{root_index:0,path:'legacy.png'})});
    });
    const legacy=page.locator('#legacy-facts');await expect(legacy.locator('.meta-grid').first().locator('dd')).toHaveText(Array(6).fill('Unresolved'));await expect(legacy).not.toContainText(explanation);await expect(legacy.locator('.meta-grid dd[aria-describedby]')).toHaveCount(0);
    await page.evaluate(()=>{window.legacyFactsView.dispose();window.legacyFactsScope.dispose();});
});

test.describe('full inspector coarse pointer',()=>{
    test.use({hasTouch:true,viewport:{width:390,height:844}});
    test('full inspector meets coarse target sizes and keeps long content reachable at zoom',async({page,request})=>{
        await fullFixture(page,request);expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(true);const panel=inspector(page);
        await expect(panel.locator('.prompt-section.positive p.prompt-text')).toContainText('Full prompt context, fine geometry and light.');
        const fullText=await panel.locator('.prompt-section.positive p.prompt-text').textContent();expect(fullText.length).toBeGreaterThan(1000);
        const bounds=await panel.locator('button,a,summary').evaluateAll(nodes=>nodes.map(node=>({name:node.getAttribute('aria-label')||node.textContent,role:node.matches('.prompt-actions button'),width:node.getBoundingClientRect().width,height:node.getBoundingClientRect().height})));
        for(const target of bounds){expect(target.width,target.name).toBeGreaterThanOrEqual(target.role?36:40);expect(target.height,target.name).toBeGreaterThanOrEqual(target.role?36:40);}
        for(const zoom of [1,2]) {
            await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom);},zoom);
            await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
            await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
            for(const target of [panel.locator('.detail-footer button.send'),panel.getByRole('button',{name:'Prompt history',exact:true})]) {
                await target.scrollIntoViewIfNeeded();await target.focus();await expect(target).toBeFocused();await expect(target).toBeInViewport();
            }
            await expect(panel.locator('.prompt-section.positive p.prompt-text')).toHaveText(fullText);
        }
        await openSelected(page);const viewer=page.locator('dialog#viewer-dialog');
        await expect(viewer.locator('.viewer-canvas img')).toBeVisible();
        for(const target of await viewer.locator('#viewer-toolbar > :is(button,a),.dialog-header > button').all()) {
            const box=await target.evaluate(node=>{const {width,height}=node.getBoundingClientRect();return {width,height,name:node.getAttribute('aria-label')};});
            expect(box.width,box.name).toBeGreaterThanOrEqual(80);expect(box.height,box.name).toBeGreaterThanOrEqual(80);
        }
        const raw=viewer.getByText('Workflow & raw metadata',{exact:true});await raw.scrollIntoViewIfNeeded();await raw.focus();await expect(raw).toBeInViewport();
        // #249 D34: the viewer details' disclosure summaries and "Download original" keep 40px coarse targets as well.
        const rail=viewer.locator('#viewer-details');await rail.getByText('File & provenance',{exact:true}).click();await expect(rail.getByRole('link',{name:'Download original',exact:true})).toBeVisible();
        const details=await rail.locator(':is(summary,a)').evaluateAll(nodes=>nodes.map(node=>{const {width,height}=node.getBoundingClientRect();return {name:node.textContent,width,height};}));
        expect(details.map(target=>target.name)).toEqual(['File & provenance','Download original','Workflow & raw metadata']);
        for(const target of details){expect(target.width,target.name).toBeGreaterThanOrEqual(40);expect(target.height,target.name).toBeGreaterThanOrEqual(40);}
    });
});

const mediaDialog=page=>page.locator('dialog#viewer-dialog');
const mediaStage=page=>mediaDialog(page).getByRole('region',{name:'Media preview',exact:true});
const position=page=>mediaDialog(page).locator('.media-position');
async function openCollection(page) {
    await openSelected(page);
    await expect(mediaDialog(page).locator('.viewer-canvas img')).toBeVisible();
    await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
}

// #249 C6: the concept viewer shell (h84–103, a889–926, s1008–1058, ≤700 s1456–1474), measured by one function in the
// concept and in production. #viewer-stage and #viewer-details content is not part of the shell (C8 owns the details).
const VIEWER_SHELL=()=>{
    const dialog=document.querySelector('dialog#viewer-dialog');
    const describe=node=>`${node.tagName.toLowerCase()}${node.id?`#${node.id}`:''}${[...node.classList].sort().map(value=>`.${value}`).join('')}`;
    const paths=[];
    const walk=(node,prefix)=>{for(const child of node.children){const path=`${prefix} > ${describe(child)}`;paths.push(path);if(!child.matches('#viewer-stage,#viewer-toolbar,#viewer-details'))walk(child,path);}};
    walk(dialog,describe(dialog));
    const box=node=>{const bounds=node.getBoundingClientRect();return [bounds.x,bounds.y,bounds.width,bounds.height].map(Math.round);};
    const toolbar=dialog.querySelector('#viewer-toolbar');
    const part=selector=>dialog.querySelector(selector);
    return {open:dialog.open,labelledBy:dialog.getAttribute('aria-labelledby'),paths,
        title:part('#viewer-title').textContent.trim(),subtitle:part('.dialog-header > div > p').textContent.trim(),
        close:{text:part('#viewer-close').textContent.trim(),label:part('#viewer-close').getAttribute('aria-label')},
        details:part('#viewer-details').getAttribute('aria-label'),
        toolbar:[...toolbar.children].map(node=>({tag:node.tagName.toLowerCase(),text:node.textContent.trim(),size:box(node).slice(2)})),
        geometry:Object.fromEntries(['dialog','.dialog-header','#viewer-title','#viewer-close','.viewer-layout','.viewer-left','#viewer-details']
            .map(selector=>[selector,box(selector==='dialog'?dialog:part(selector))])),
        toolbarBox:box(toolbar),stageBox:box(part('#viewer-stage'))};
};

test('viewer matches the concept shell and toolbar order',async({page,request})=>{
    await seedConcept(request);
    for(const [width,height] of SIZES) {
        await page.setViewportSize({width,height});
        await openConcept(page,'images','dark');
        const card=page.locator('#image-grid > article.media-card').nth(1).locator('.image-open');
        const filename=(await card.getAttribute('aria-label')).replace(/^Open /,'');
        await card.click();await expect(page.locator('#viewer-dialog .viewer-canvas img')).toBeVisible();
        const concept=await page.evaluate(VIEWER_SHELL);
        expect(concept.toolbar.map(control=>control.text)).toEqual(['← Previous','−','+','Fit','Rotate','Flip','Play','Download','Next →']);
        await openProduction(page,'images','dark');
        const opener=page.locator(`article[data-image-key] .image-open[aria-label="Open ${filename}"]`);await opener.click();
        const viewer=page.locator('dialog#viewer-dialog');
        await expect(viewer.locator('.viewer-canvas img')).toBeVisible();await expect(viewer.locator('#viewer-title')).toHaveText(concept.title);
        await expect(viewer.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
        // The dialog is named by its title, as the concept's aria-labelledby does.
        await expect(page.getByRole('dialog',{name:concept.title,exact:true})).toBeVisible();
        const production=await page.evaluate(VIEWER_SHELL);
        // C8 rebuilds the details content; until then the shared file inspector marks the aside it renders into.
        production.paths=production.paths.map(path=>path.replace('aside#viewer-details.file-inspector','aside#viewer-details'));
        // #249 D34: after Next → production appends its own status lines (position, then the transform and slideshow
        // reasons), and Download stays the original-file link; every other control keeps the concept's text and order.
        const appended=production.toolbar.slice(9);
        expect(appended.map(control=>control.tag)).toEqual(['p','p','p']);
        await expect(viewer.locator('#viewer-toolbar > p[role="status"]')).toHaveText(new RegExp(`^\\d+ of \\d+ loaded: ${filename.replace(/[\\^$.*+?()[\]{}|]/g,'\\$&')}$`));
        expect(production.toolbar[7].tag).toBe('a');
        production.toolbar=production.toolbar.slice(0,9).map((control,index)=>index===7?{...control,tag:'button'}:control);
        // #249 D34: Viewer.js's own `.viewer-toolbar` rule makes the concept's toolbar absolute across the dialog's
        // bottom edge, over the details; production keeps it in `.viewer-left` under the stage.
        const [leftX,leftY,leftWidth,leftHeight]=production.geometry['.viewer-left'],[barX,barY,barWidth,barHeight]=production.toolbarBox;
        // Inside the left box's 1px border (boxes are rounded to whole pixels).
        for(const [actual,expected] of [[barX,leftX+1],[barWidth,leftWidth-2],[barY+barHeight,leftY+leftHeight-1],[production.stageBox[1]+production.stageBox[3],barY]])
            expect(Math.abs(actual-expected),`${width}x${height} toolbar inside .viewer-left`).toBeLessThanOrEqual(1);
        expect(concept.toolbarBox[2]).toBeGreaterThan(concept.geometry['.viewer-left'][2]);
        for(const shell of [concept,production]) {
            delete shell.toolbarBox;delete shell.stageBox;
            // At 700px and below the details and the stacked layout grow with the details content, which C8 rebuilds.
            if(width<=700) for(const selector of ['#viewer-details','.viewer-layout']) shell.geometry[selector]=shell.geometry[selector].slice(0,3);
        }
        if(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)) {
            // #249 D34: coarse pointers keep production's 40px square targets, wider than the concept's ×, "−" and "+" (the title beside × narrows by the difference).
            for(const shell of [concept,production]) {shell.toolbar=shell.toolbar.map(({tag,text})=>({tag,text}));delete shell.geometry['#viewer-close'];shell.geometry['#viewer-title'].splice(2,1);}
            expect(production.close).toEqual(concept.close);
        }
        expect(production,`${width}x${height}`).toEqual(concept);
        await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);await expect(opener).toBeFocused();
    }
});

test('collection viewer follows filtered loaded order beyond mounted cards',async({page,request})=>{
    await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true,extra_images:200}})).toBeOK();
    await page.goto('/prompt_manager/workspace#/images');
    // #249 C2: Source and Folder live in the concept's "Refine this view"; sort is the toolbar's.
    await page.getByRole('button',{name:'Filters',exact:true}).click();
    const refine=page.getByRole('dialog',{name:'Refine this view',exact:true});
    await refine.getByLabel('Source',{exact:true}).selectOption('0');await refine.getByLabel('Folder',{exact:true}).selectOption('inspector');
    await refine.getByRole('button',{name:'Apply filters',exact:true}).click();
    await page.getByRole('combobox',{name:'Sort results',exact:true}).selectOption('name');
    const more=page.getByRole('button',{name:'Load more images',exact:true});
    await expect(page.locator('.section-caption')).toContainText('40 of');
    for(const n of [80,120,160]) {await more.click();await expect(page.locator('.section-caption')).toContainText(`${n} of`);}
    const listed=await(await request.get('/prompt_manager/images/output?mode=workspace&root=0&subfolder=inspector&sort=name&limit=200')).json();
    const loaded=listed.images.slice(0,160);expect(loaded).toHaveLength(160);
    await page.evaluate(()=>scrollTo(0,0));await expect.poll(()=>page.locator('article[data-image-key]').count()).toBeLessThan(120);
    let release,started;const held=new Promise(resolve=>{release=resolve;});const hit=new Promise(resolve=>{started=resolve;});
    // A grid page, not the sidebar's `limit=1` Images count.
    await page.route(url=>url.pathname.endsWith('/images/output') && url.searchParams.get('limit')!=='1',async route=>{started();await held;await route.continue();},{times:1});
    await more.click();await hit;await page.evaluate(()=>scrollTo(0,0));
    const opener=page.locator(`article[data-image-key="0:${loaded[0].path}"] .image-open`);
    await opener.click();release();await expect(page.locator('.section-caption')).toContainText('200 of');
    const modal=mediaDialog(page);await expect(position(page)).toHaveText(`1 of 160 loaded: ${loaded[0].filename}`);
    let pages=0;page.on('request',req=>{if(req.url().includes('/images/output?'))pages++;});
    await modal.getByRole('button',{name:'Previous media',exact:true}).click();
    await expect(position(page)).toHaveText(`160 of 160 loaded: ${loaded[159].filename}`);
    await expect(modal.locator('#viewer-details > div > p.small')).toHaveText(loaded[159].filename);
    await expect(modal.locator('#viewer-toolbar').getByRole('link',{name:'Download original',exact:true})).toHaveAttribute('href',loaded[159].url);
    await expect(page.locator(`article[data-image-key="0:${loaded[159].path}"]`)).toHaveCount(0);
    await modal.getByRole('button',{name:'Next media',exact:true}).click();await expect(position(page)).toHaveText(`1 of 160 loaded: ${loaded[0].filename}`);
    await mediaStage(page).focus();await page.keyboard.press('ArrowRight');await expect(position(page)).toHaveText(`2 of 160 loaded: ${loaded[1].filename}`);
    expect(pages).toBe(0);await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();
    await expect(opener).toBeFocused();
    // #249 D44: Source and Folder are active.
    await page.getByRole('button',{name:'Filters · 2',exact:true}).click();
    await refine.getByLabel('Source',{exact:true}).selectOption('1');await refine.getByLabel('Folder',{exact:true}).selectOption('inspector');
    await refine.getByRole('button',{name:'Apply filters',exact:true}).click();
    await selectFile(page,'complete.png',1);await openCollection(page);await expect(position(page)).toHaveText('1 of 1 loaded: complete.png');
    for(const name of ['Previous media','Next media'])await expect(mediaDialog(page).getByRole('button',{name,exact:true})).toHaveAttribute('aria-disabled','true');
    const onePlay=mediaDialog(page).getByRole('button',{name:'Play slideshow',exact:true});
    await expect(onePlay).toHaveAccessibleDescription('Slideshow requires at least two loaded media entries.');await onePlay.dispatchEvent('click');await expect(onePlay).toHaveText('Play');
    await mediaDialog(page).getByRole('button',{name:'Close image viewer',exact:true}).click();
    const library=await(await request.get('/prompt_manager/library')).json();const id=library.items.find(item=>item.text.startsWith('Inspector portrait')).id;
    await page.goto(`/prompt_manager/workspace#/library/${id}`);await page.getByRole('button',{name:'Select negative Inspector recipe',exact:true}).click();await selectFile(page,'complete.png');await openCollection(page);
    await expect(position(page)).toContainText('of 1 loaded: complete.png');
    await mediaDialog(page).getByRole('button',{name:'Close image viewer',exact:true}).click();
    // #249 D26: the whole positive's images are in the All images dialog's own embedded gallery.
    await page.getByRole('button',{name:'All images for this positive',exact:true}).click();
    const listing=page.getByRole('dialog',{name:'All images for this positive',exact:true});
    await listing.locator('article[data-image-key="0:inspector/complete.png"] .image-card-actions button:first-child').click();
    await listing.locator('.media-card.selected .image-open').click();await expect(mediaDialog(page).locator('.viewer-canvas img')).toBeVisible();
    await expect(position(page)).toContainText('of 2 loaded:');
});

test('collection viewer uses real public transforms and resets on navigation',async({page,request})=>{
    await fullFixture(page,request);
    await page.evaluate(()=>{
        window.viewerCalls=[];window.viewerInstances=[];
        const Native=window.Viewer;
        for(const method of ['zoom','reset','rotate','scaleX']) {const original=Native.prototype[method];Native.prototype[method]=function(...args){window.viewerCalls.push([method,...args]);return original.apply(this,args);};}
        window.Viewer=class extends Native {constructor(...args){super(...args);window.viewerInstances.push(this);}};
    });
    await openCollection(page);const modal=mediaDialog(page),controls=modal.locator('#viewer-toolbar');
    await expect(modal.locator('.viewer-container')).toHaveCount(1);await expect(modal.locator('.viewer-container :is(.viewer-toolbar,.viewer-navbar,.viewer-title,.viewer-button):visible')).toHaveCount(0);
    const fitted=await page.evaluate(()=>({...window.viewerInstances.at(-1).imageData}));
    await controls.getByRole('button',{name:'Zoom in',exact:true}).click();expect(await page.evaluate(()=>window.viewerInstances.at(-1).imageData.width)).toBeGreaterThan(fitted.width);
    await controls.getByRole('button',{name:'Zoom out',exact:true}).click();
    await controls.getByRole('button',{name:'Rotate image 90 degrees',exact:true}).click();await controls.getByRole('button',{name:'Flip image horizontally',exact:true}).click();
    expect(await page.evaluate(()=>({rotate:window.viewerInstances.at(-1).imageData.rotate,scaleX:window.viewerInstances.at(-1).imageData.scaleX}))).toEqual({rotate:90,scaleX:-1});
    const fit=controls.getByRole('button',{name:'Fit image',exact:true});await fit.click();await expect(fit).toBeFocused();
    expect(await page.evaluate(()=>({rotate:window.viewerInstances.at(-1).imageData.rotate,scaleX:window.viewerInstances.at(-1).imageData.scaleX}))).toEqual({rotate:0,scaleX:1});
    expect(await page.evaluate(()=>window.viewerCalls)).toEqual(expect.arrayContaining([['zoom',0.2],['zoom',-0.2],['reset'],['rotate',90],['scaleX',-1]]));
    await page.evaluate(()=>{window.currentMedia=document.querySelector('.viewer-canvas img');window.currentFit=document.querySelector('[aria-label="Fit image"]');});
    await modal.getByText('File & provenance',{exact:true}).click();await modal.getByText('Workflow & raw metadata',{exact:true}).click();
    expect(await page.evaluate(()=>window.currentMedia===document.querySelector('.viewer-canvas img')&&window.currentFit===document.querySelector('[aria-label="Fit image"]'))).toBe(true);
    await controls.getByRole('button',{name:'Rotate image 90 degrees',exact:true}).click();await controls.getByRole('button',{name:'Next media',exact:true}).click();
    await expect(controls.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');await expect(modal.locator('.viewer-container')).toHaveCount(1);
    expect(await page.evaluate(()=>window.currentMedia!==document.querySelector('.viewer-canvas img')&&window.currentFit===document.querySelector('[aria-label="Fit image"]'))).toBe(true);
    expect(await page.evaluate(()=>({rotate:window.viewerInstances.at(-1).imageData.rotate,scaleX:window.viewerInstances.at(-1).imageData.scaleX}))).toEqual({rotate:0,scaleX:1});
    await expect(controls.getByRole('button',{name:'Play slideshow',exact:true})).toBeVisible();
});

async function slideshowFixture(page,request,preferences={videoAutoplay:false,videoMute:true,videoLoop:false}) {
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true,include_video:true}})).toBeOK();
    await page.goto('/prompt_manager/workspace#/images');
    await page.evaluate(async preferences=>{
        if(preferences) localStorage.setItem('gallerySettings',JSON.stringify(preferences));
        const [{createMediaViewer},{createInspectionController},{createScope},{createPromptSender}]=await Promise.all([
            import('/prompt_manager/js/workspace/media-viewer.mjs'),import('/prompt_manager/js/workspace/inspection-state.mjs'),
            import('/prompt_manager/js/shared/toolkit/core.js'),import('/prompt_manager/js/workspace/prompt-sender.mjs')]);
        const api=window.ApiClient.createApiClient(),collection=await api.get('/prompt_manager/images/output?mode=workspace&subfolder=inspector&limit=40');
        const items=['complete.png','clip.webm','explicit-empty.png'].map(filename=>collection.images.find(image=>image.root_index===0&&image.filename===filename));
        window.slideshowTimers=new Set();window.slideshowCallbacks=[];window.slideshowReady=[];
        const schedule=window.setTimeout.bind(window),cancel=window.clearTimeout.bind(window);
        window.setTimeout=(callback,delay,...args)=>{let id;id=schedule(()=>{window.slideshowTimers.delete(id);callback(...args);},delay);if(delay===3500){window.slideshowTimers.add(id);window.slideshowCallbacks.push(callback);}return id;};
        window.clearTimeout=id=>{window.slideshowTimers.delete(id);cancel(id);};
        const Native=window.Viewer;
        window.Viewer=class extends Native {constructor(node,options){window.slideshowReady.push(options.viewed);super(node,options);}};
        const scope=createScope(),controller=createInspectionController({api}),sender=createPromptSender({scope});scope.cleanup(()=>controller.dispose());
        const view=createMediaViewer({container:document.getElementById('workspace-main'),parentScope:scope,inspectionController:controller,sender,onSelect:entry=>controller.select(entry)});
        window.slideshowFixture={scope,controller,view,items,open:()=>view.open({items,selectedKey:'0:inspector/complete.png'})};window.slideshowFixture.open();
    },preferences);
    await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
    await expect(mediaDialog(page).locator('#viewer-details > div > p.small')).toHaveText('complete.png');
    await page.clock.install();await page.clock.pauseAt(new Date(Date.now()+1000));
}
const slideshowButton=page=>mediaDialog(page).locator('#viewer-toolbar').getByRole('button',{name:/^(Play|Pause|Stop) slideshow$/});
const timerCount=page=>page.evaluate(()=>window.slideshowTimers.size);
const mediaStep=async(page,direction)=>mediaDialog(page).getByRole('button',{name:`${direction} media`,exact:true}).click();
async function readyClockImage(page) {
    await expect.poll(async()=>{await page.clock.runFor(50);return mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true}).getAttribute('aria-disabled');}).toBe('false');
}

test('slideshow exposes exact state labels and respects video playback',async({page,request})=>{
    const originals=[];page.on('request',request=>{if(request.url().includes('_pm_viewer='))originals.push(request.url());});
    await slideshowFixture(page,request);const button=slideshowButton(page),modal=mediaDialog(page);
    await expect(button).toHaveText('Play');await expect(button).not.toHaveAttribute('aria-pressed');
    expect(originals).toHaveLength(1);
    await button.click();await expect(button).toHaveText('Pause');await expect(button).toBeFocused();await expect(position(page)).toContainText('Slideshow playing.');expect(await timerCount(page)).toBe(1);
    await page.clock.runFor(3499);await expect(position(page)).toContainText('1 of 3 loaded: complete.png');
    await page.clock.runFor(1);await expect(position(page)).toContainText('2 of 3 loaded: clip.webm');
    await expect(button).toHaveAccessibleName('Stop slideshow');await expect(button).toHaveText('Stop');await expect(button).toBeFocused();
    await expect(position(page)).toContainText('Slideshow paused for video. Next or Previous continues it on an image; Stop ends slideshow.');expect(await timerCount(page)).toBe(0);
    const video=modal.locator('video');await expect.poll(()=>video.evaluate(node=>node.readyState)).toBeGreaterThanOrEqual(1);
    expect(await video.evaluate(node=>({controls:node.controls,inline:node.playsInline,muted:node.muted,loop:node.loop}))).toEqual({controls:true,inline:true,muted:true,loop:false});
    await expect(modal.getByRole('button',{name:'Zoom in',exact:true})).toHaveAccessibleDescription('Image transforms are unavailable for video.');
    await video.evaluate(node=>node.play());await expect.poll(()=>video.evaluate(node=>node.ended)).toBe(true);
    await page.clock.runFor(10500);await expect(position(page)).toContainText('2 of 3 loaded');await expect(button).toHaveText('Stop');
    await video.evaluate(node=>{node.loop=true;node.currentTime=0;return node.play();});
    await button.click();await expect(button).toHaveText('Play');expect(await video.evaluate(node=>node.paused)).toBe(false);
    await expect(button).toHaveAttribute('aria-disabled','true');await mediaStep(page,'Next');
    await readyClockImage(page);await expect(button).toHaveText('Play');expect(await timerCount(page)).toBe(0);
    await button.click();await mediaStep(page,'Previous');await expect(button).toHaveText('Stop');
    await mediaStep(page,'Previous');await expect(button).toHaveText('Pause');await expect(position(page)).toContainText('Slideshow waiting for image.');await readyClockImage(page);await expect.poll(()=>timerCount(page)).toBe(1);
    expect(new Set(originals.map(url=>new URL(url).pathname)).size).toBe(3);
    await button.click();await modal.locator('#viewer-details').evaluate(node=>{node.scrollTop=node.scrollHeight;});await page.clock.runFor(50);
    await button.click();await mediaStep(page,'Previous');await readyClockImage(page);await expect(button).toHaveText('Pause');expect(await timerCount(page)).toBe(1);
});

test('slideshow stop fences old timer and readiness callbacks',async({page,request})=>{
    await slideshowFixture(page,request);const button=slideshowButton(page);
    await button.click();await page.clock.runFor(3499);await button.click();
    await page.evaluate(()=>{for(const callback of window.slideshowCallbacks)callback();for(const callback of window.slideshowReady)callback();});
    await page.clock.runFor(7000);await expect(position(page)).toContainText('1 of 3 loaded');await expect(button).toHaveText('Play');expect(await timerCount(page)).toBe(0);
    await button.click();await mediaStep(page,'Previous');await expect(position(page)).toContainText('Slideshow waiting for image.');await button.click();
    await page.evaluate(()=>{for(const callback of window.slideshowCallbacks)callback();for(const callback of window.slideshowReady)callback();});
    await expect(button).toHaveText('Play');expect(await timerCount(page)).toBe(0);await mediaStep(page,'Next');await readyClockImage(page);
    await page.route('**/images/serve/inspector/explicit-empty.png?*',route=>route.abort());
    await button.click();await mediaStep(page,'Previous');await expect(position(page)).toContainText('Media could not be loaded.');
    await expect(button).toHaveText('Play');await expect(button).toHaveAttribute('aria-disabled','true');expect(await timerCount(page)).toBe(0);
    await mediaStep(page,'Next');await expect(button).toHaveText('Play');
    await page.evaluate(()=>window.slideshowFixture.scope.dispose());await expect(mediaDialog(page)).toHaveCount(0);
    await page.evaluate(()=>{for(const callback of window.slideshowCallbacks)callback();for(const callback of window.slideshowReady)callback();window.slideshowFixture.view.stopSlideshow();});
    expect(await timerCount(page)).toBe(0);
});

test('slideshow production query and route disposal cancel the active deadline',async({page,request})=>{
    await fullFixture(page,request);await page.clock.install();
    await page.evaluate(()=>{
        window.slideshowTimers=new Set();const schedule=window.setTimeout.bind(window),cancel=window.clearTimeout.bind(window);
        window.setTimeout=(callback,delay,...args)=>{let id;id=schedule(()=>{window.slideshowTimers.delete(id);callback(...args);},delay);if(delay===3500)window.slideshowTimers.add(id);return id;};
        window.clearTimeout=id=>{window.slideshowTimers.delete(id);cancel(id);};
    });
    await openCollection(page);await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
    await page.clock.pauseAt(new Date(Date.now()+1000));await slideshowButton(page).click();expect(await timerCount(page)).toBe(1);
    await page.clock.runFor(3499);
    const pendingAtReset=await page.evaluate(()=>{const sort=document.getElementById('sort');sort.value='name';sort.dispatchEvent(new Event('change'));return window.slideshowTimers.size;});
    expect(pendingAtReset).toBe(0);await page.clock.runFor(1);
    await expect(mediaDialog(page)).toHaveCount(0);expect(await timerCount(page)).toBe(0);
    await selectFile(page,'complete.png');await openSelected(page);await readyClockImage(page);await expect(slideshowButton(page)).toHaveText('Play');
    await slideshowButton(page).click();await page.evaluate(()=>{location.hash='/overview';});await expect(page.locator('h1')).toHaveText('Overview');await expect(mediaDialog(page)).toHaveCount(0);expect(await timerCount(page)).toBe(0);
    await page.clock.runFor(7000);await expect(page.locator('.viewer-container')).toHaveCount(0);
});

test('slideshow interaction visibility sender and disposal clear intent',async({page,request})=>{
    // A working clipboard keeps the copy action on the toast, so no "Copy prompt" fallback covers the viewer.
    await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{}}}));
    await slideshowFixture(page,request);const button=slideshowButton(page),modal=mediaDialog(page);
    for(const action of [
        ()=>modal.locator('.prompt-section.positive p.prompt-text').click(),
        ()=>modal.locator('#viewer-details').dispatchEvent('wheel',{deltaY:100}),
        ()=>modal.getByRole('button',{name:/^Copy positive prompt: /}).click(),
        async()=>{await modal.getByText('Workflow & raw metadata',{exact:true}).click();await downloadText(page,modal.getByRole('button',{name:'Download metadata JSON',exact:true}));},
        async()=>{await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});},
        async()=>{const trigger=modal.getByRole('button',{name:/^Send prompt pair to ComfyUI: /});await trigger.click();await expect(page.getByRole('dialog',{name:'Send to ComfyUI',exact:true})).toBeVisible();await page.keyboard.press('Escape');await expect(trigger).toBeFocused();},
        ()=>modal.locator('#viewer-toolbar').getByRole('link',{name:'Download original',exact:true}).click(),
        ()=>page.evaluate(()=>window.slideshowFixture.view.stopSlideshow()),
    ]) {
        await button.click();expect(await timerCount(page)).toBe(1);await action();await expect(button).toHaveText('Play');expect(await timerCount(page)).toBe(0);await page.clock.runFor(3500);await expect(position(page)).toContainText('1 of 3 loaded');
    }
    for(let mount=0;mount<3;mount++) {
        await button.click();await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();await expect(modal).toHaveCount(0);expect(await timerCount(page)).toBe(0);
        await page.evaluate(()=>window.slideshowFixture.open());await expect(button).toHaveText('Play');
        await readyClockImage(page);
    }
    await button.click();await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await expect(modal).toHaveCount(0);expect(await timerCount(page)).toBe(0);
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await expect(modal).toHaveCount(0);
});

test('slideshow preference failures leave native playback usable',async({page,request})=>{
    await slideshowFixture(page,request,null);const modal=mediaDialog(page);
    for(const failure of ['denied','malformed','rejected']) {
        await page.evaluate(failure=>{
            if(failure==='denied') {window.normalStorage=Object.getOwnPropertyDescriptor(window,'localStorage');Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new DOMException('Denied','SecurityError');}});}
            else {if(window.normalStorage)Object.defineProperty(window,'localStorage',window.normalStorage);localStorage.setItem('gallerySettings',failure==='malformed'?'{broken':JSON.stringify({videoAutoplay:true,videoMute:true,videoLoop:false}));}
            if(failure==='rejected'){window.nativePlay=HTMLMediaElement.prototype.play;window.playRejections=0;HTMLMediaElement.prototype.play=function(){window.playRejections++;return Promise.reject(new DOMException('Blocked','NotAllowedError'));};}
        },failure);
        await mediaStep(page,'Next');const video=modal.locator('video');await expect.poll(()=>video.evaluate(node=>node.readyState)).toBeGreaterThanOrEqual(1);
        expect(await video.evaluate(node=>({controls:node.controls,inline:node.playsInline,muted:node.muted,loop:node.loop}))).toEqual({controls:true,inline:true,muted:true,loop:failure!=='rejected'});
        if(failure==='rejected') {expect(await page.evaluate(()=>window.playRejections)).toBe(1);await page.evaluate(()=>{HTMLMediaElement.prototype.play=window.nativePlay;});}
        await video.evaluate(node=>node.play());expect(await video.evaluate(node=>node.paused)).toBe(false);
        await mediaStep(page,'Previous');await expect(slideshowButton(page)).toHaveText('Play');
    }
});

test('full inspector attaches matched theme and mobile evidence with reachable content',async({page,request},testInfo)=>{
    test.setTimeout(120_000); // 32 evidence captures across four viewports, like the concept comparisons
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true,include_video:true}})).toBeOK();
    await page.goto('/prompt_manager/workspace#/images');
    const measurements=[];
    async function capture(name,viewer=false) {
        await page.evaluate(()=>document.fonts.ready);
        if(viewer) {
            const modal=mediaDialog(page),rail=modal.locator('#viewer-details');
            expect(await modal.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
            measurements.push({name,...await modal.evaluate(node=>{
                const box=element=>{const bounds=element.getBoundingClientRect();return{x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height};};
                return{frame:box(node),header:box(node.querySelector('.dialog-header')),stage:box(node.querySelector('#viewer-stage')),
                    toolbar:box(node.querySelector('#viewer-toolbar')),rail:box(node.querySelector('#viewer-details')),
                    pair:node.querySelector('#viewer-details > button.send')?box(node.querySelector('#viewer-details > button.send')):null};
            })});
            if(page.viewportSize().width===1600)expect(await rail.evaluate(node=>node.getBoundingClientRect().width)).toBe(340);
            else await expect(rail).toHaveCSS('overflow-y','visible');
        }
        const file=testInfo.outputPath(`${name}.png`);await page.screenshot({path:file});await testInfo.attach(name,{path:file,contentType:'image/png'});
    }
    for(const [width,height,theme]of [[1600,1100,'dark'],[1600,1100,'light'],[390,844,'dark'],[390,844,'light']]) {
        const label=`${theme}-${width}`;await page.setViewportSize({width,height});
        await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
        await selectFile(page,'complete.png');await expect(inspector(page).locator('.meta-grid dd').first()).toHaveText('9007199254740993');
        if(width===1600)await page.evaluate(()=>scrollTo(0,0));else await inspector(page).locator('.detail-cover').evaluate(node=>node.scrollIntoView({block:'start'}));
        await capture(`inline-${label}`);
        await openCollection(page);const modal=mediaDialog(page),rail=modal.locator('#viewer-details');
        await expect(modal.locator('.viewer-canvas img')).toBeVisible();await expect.poll(()=>modal.locator('.viewer-canvas img').evaluate(node=>node.complete&&node.naturalWidth>0)).toBe(true);
        await modal.evaluate(node=>{node.scrollTop=0;});await capture(`viewer-${label}`,true);
        await expect(rail.locator('.prompt-section.negative')).toContainText('blurred edges, watermark');await rail.locator('.meta-grid').first().scrollIntoViewIfNeeded();await capture(`facts-${label}`,true);
        await rail.getByText('File & provenance',{exact:true}).click();await rail.locator('details .meta-grid').scrollIntoViewIfNeeded();await capture(`provenance-${label}`,true);
        await rail.getByText('Workflow & raw metadata',{exact:true}).click();const raw=rail.locator('pre code').first();await raw.scrollIntoViewIfNeeded();await expect(raw).toBeInViewport();await capture(`raw-${label}`,true);
        const send=rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /});await send.focus();await expect(send).toBeInViewport();await send.click();await expect(page.getByRole('dialog',{name:'Send to ComfyUI',exact:true})).toBeVisible();await capture(`sender-${label}`);await page.keyboard.press('Escape');await expect(send).toBeFocused();
        await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();await selectFile(page,'clip.webm');await openSelected(page);
        await expect.poll(()=>modal.locator('video').evaluate(node=>node.readyState)).toBeGreaterThanOrEqual(1);await capture(`video-${label}`,true);await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();
        await selectFile(page,'control-heavy.png');await openSelected(page);
        await expect(modal).toContainText('Unavailable within inspection limits');await modal.getByText('Workflow & raw metadata',{exact:true}).click();
        const limitedDownload=modal.getByRole('button',{name:'Download metadata JSON',exact:true});await limitedDownload.scrollIntoViewIfNeeded();await expect(limitedDownload).toHaveAttribute('aria-disabled','true');await capture(`limited-${label}`,true);
        await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();
    }
    const fs=await import('node:fs/promises'),geometryPath=testInfo.outputPath('inspector-geometry.json');
    await fs.writeFile(geometryPath,JSON.stringify(measurements,null,2));await testInfo.attach('inspector-geometry',{path:geometryPath,contentType:'application/json'});
});

test('collection viewer preserves original download and rooted retries',async({page,request})=>{
    await fullFixture(page,request);let deny=true;const originals=[];
    await page.route('**/images/serve/inspector/complete.png?*',route=>{originals.push(route.request().url());return deny?route.fulfill({status:404,body:'Missing'}):route.continue();});
    await openSelected(page);const modal=mediaDialog(page);
    await expect(modal).toContainText('Media could not be loaded. Retry this file.');await expect(modal.locator('.meta-grid dd').first()).toHaveText('9007199254740993');
    await expect(modal.locator('.media-position')).toContainText('Media could not be loaded.');
    const download=modal.locator('#viewer-toolbar').getByRole('link',{name:'Download original',exact:true});const original=await download.getAttribute('href');
    await expect(modal.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','true');
    const denied=page.waitForResponse(response=>response.url().includes('/images/serve/inspector/complete.png?')&&response.status()===404);
    await modal.getByRole('button',{name:'Retry media',exact:true}).click();await denied;
    await expect.poll(()=>mediaStage(page).locator(':scope > img').evaluate(node=>node.complete&&node.naturalWidth===0)).toBe(true);
    await expect(modal.getByRole('button',{name:'Retry media',exact:true})).toBeVisible();
    deny=false;await modal.getByRole('button',{name:'Retry media',exact:true}).click();await expect(modal.locator('.viewer-canvas img')).toBeVisible();
    await expect(download).toHaveAttribute('href',original);
    const stages=originals.map(value=>new URL(value)).filter(url=>url.searchParams.has('_pm_viewer'));
    expect(new Set(stages.map(url=>url.searchParams.get('_pm_viewer'))).size).toBe(3);
    for(const value of originals) {const actual=new URL(value),expected=new URL(original,'http://127.0.0.1');expect(actual.pathname).toBe(expected.pathname);for(const key of ['root','root_scope','v'])expect(actual.searchParams.get(key)).toBe(expected.searchParams.get(key));expect(actual.searchParams.getAll('_pm_viewer').length).toBeLessThanOrEqual(1);expect([...actual.searchParams.keys()].filter(key=>!expected.searchParams.has(key))).toEqual(actual.searchParams.has('_pm_viewer')?['_pm_viewer']:[]);}
    const [saved]=await Promise.all([page.waitForEvent('download'),download.click()]);expect(saved.suggestedFilename()).toBe('complete.png');
    await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();await page.evaluate(()=>{window.availableViewer=window.Viewer;window.Viewer=undefined;});
    await page.route('**/images/inspect/inspector/explicit-empty.png?*',route=>route.fulfill({status:503,json:{success:false,error:'Unavailable'}}),{times:1});
    await selectFile(page,'explicit-empty.png');await expect(inspector(page)).toContainText('Could not read prompts from this file.');
    await page.locator('article[data-image-key="0:inspector/explicit-empty.png"] .image-open').click();
    await expect(mediaStage(page).locator('img')).toBeVisible();await expect(mediaStage(page).locator('img')).toHaveJSProperty('naturalWidth',720);
    await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAccessibleDescription('Image transforms are unavailable because Viewer.js could not load.');
    await page.evaluate(()=>{window.Viewer=window.availableViewer;});await mediaDialog(page).getByRole('button',{name:'Retry prompt details',exact:true}).click();
    await expect(mediaDialog(page).locator('#viewer-details > div > p.small')).toHaveText('explicit-empty.png');await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','true');
});

test('collection viewer keeps nested dialog focus and route cleanup',async({page,request})=>{
    await fullFixture(page,request);await openCollection(page);const modal=mediaDialog(page);
    const send=modal.getByRole('button',{name:/^Send prompt pair to ComfyUI: /});await send.click();const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
    await expect(sender).toBeVisible();const before=await position(page).textContent();await page.keyboard.press('ArrowRight');await expect(position(page)).toHaveText(before);
    await page.keyboard.press('Escape');await expect(sender).toBeHidden();await expect(modal).toBeVisible();await expect(send).toBeFocused();
    const text=modal.locator('.prompt-section.positive p.prompt-text');await text.focus();await page.keyboard.press('ArrowRight');await expect(position(page)).toHaveText(before);
    await modal.getByText('Workflow & raw metadata',{exact:true}).focus();await page.keyboard.press('ArrowLeft');await expect(position(page)).toHaveText(before);
    await modal.getByRole('button',{name:'Next media',exact:true}).click();const key=(await position(page).textContent()).split('loaded: ')[1];
    // D19: focus returns to the opening image; the panel follows the file the viewer moved to.
    await page.keyboard.press('Escape');await expect(modal).toHaveCount(0);await expect(page.locator('article[data-image-key="0:inspector/complete.png"] .image-open')).toBeFocused();await expect(inspector(page).locator('.detail-inner > p.small')).toHaveText(key);
    await openSelected(page);await page.evaluate(()=>{location.hash='/overview';});
    await expect(page.locator('h1')).toHaveText('Overview');await expect(page.locator('.viewer-container,#viewer-dialog')).toHaveCount(0);
});

test('collection viewer matches approved responsive frame',async({page,request},testInfo)=>{
    await page.setViewportSize({width:1600,height:1100});await fullFixture(page,request);await openCollection(page);
    const modal=mediaDialog(page),body=modal.locator('.viewer-layout'),rail=modal.locator('#viewer-details');
    // Absolute gallery cards relayout through ResizeObserver and rAF after resize or CSS zoom.
    const settleViewport=width=>expect.poll(()=>page.evaluate(()=>({inner:innerWidth,client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})),{message:`Images gallery relayout settles the ${width}px viewport`}).toEqual({inner:width,client:width,scroll:width});
    await settleViewport(1600);
    for(const theme of ['dark','light']) {
        await page.evaluate(theme=>{const preference=document.getElementById('theme');preference.value=theme;preference.dispatchEvent(new Event('change'));},theme);
        const geometry=await modal.evaluate(node=>{const style=getComputedStyle(node),box=node.getBoundingClientRect(),rail=node.querySelector('#viewer-details').getBoundingClientRect();return{width:box.width,height:box.height,padding:style.padding,radius:style.borderRadius,rail:rail.width,gap:getComputedStyle(node.querySelector('.viewer-layout')).columnGap};});
        expect(geometry).toEqual({width:1500,height:940,padding:'18px',radius:'9px',rail:340,gap:'20px'});
        await page.screenshot({path:testInfo.outputPath(`viewer-${theme}-1600.png`)});
    }
    for(const [width,expected] of [[1200,300],[900,270]]) {await page.setViewportSize({width,height:1000});await settleViewport(width);await expect(rail).toHaveCSS('width',`${expected}px`);}
    await page.setViewportSize({width:390,height:844});await settleViewport(390);expect(await modal.evaluate(node=>node.getBoundingClientRect().width)).toBeCloseTo(356,1);
    for(const [width,expected]of [[840,806],[850,816],[860,825.6]]) {
        await page.setViewportSize({width,height:1000});await settleViewport(width);expect(await modal.evaluate(node=>node.getBoundingClientRect().width)).toBeCloseTo(expected,1);
    }
    await page.setViewportSize({width:390,height:844});await settleViewport(390);
    for(const zoom of [1,2]) {
        await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom);},zoom);
        await settleViewport(390);
        expect(await modal.evaluate(node=>node.getBoundingClientRect().width)).toBeCloseTo(zoom===1?356:322,1);
        expect(await modal.evaluate(node=>{const box=node.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight+1;})).toBe(true);
        await expect.poll(()=>modal.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
        await expect(rail).toHaveCSS('overflow-y','visible');expect(await body.evaluate(node=>{const style=getComputedStyle(node);return [style.display,style.flexDirection];})).toEqual(['flex','column']);
        await rail.getByText('Workflow & raw metadata',{exact:true}).click();const raw=rail.locator('pre code').first();await expect(raw).toBeVisible();
        await rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /}).focus();await expect(rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /})).toBeInViewport();
        await page.screenshot({path:testInfo.outputPath(`viewer-mobile-${zoom}x.png`)});await rail.getByText('Workflow & raw metadata',{exact:true}).click();
    }
    await rail.evaluate(node=>{node.style.fontFamily='"DejaVu Sans", sans-serif';});
    await rail.getByText('Workflow & raw metadata',{exact:true}).click();
    const sendPair=rail.getByRole('button',{name:/^Send prompt pair to ComfyUI: /});await sendPair.focus();await expect(sendPair).toBeInViewport();
    await rail.getByText('Workflow & raw metadata',{exact:true}).click();await sendPair.focus();await expect(sendPair).toBeFocused();await expect(sendPair).toBeInViewport();
    const footer=await sendPair.evaluate(node=>{const box=node.getBoundingClientRect(),parent=node.closest('#viewer-details').getBoundingClientRect();return{width:box.width,right:box.right,available:parent.width,parentRight:parent.right,height:box.height,minimum:matchMedia('(pointer: coarse)').matches?80:60};});
    expect(footer.width).toBeLessThanOrEqual(footer.available);expect(footer.right).toBeLessThanOrEqual(footer.parentRight);expect(footer.height).toBeGreaterThanOrEqual(footer.minimum);await expect(sendPair.locator('svg')).toHaveCount(1);
    expect(await modal.evaluate(node=>node.getBoundingClientRect().width)).toBeCloseTo(322,1);await expect.poll(()=>modal.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await testInfo.attach('wider-font-footer-geometry',{body:JSON.stringify({footer,modal:await modal.evaluate(node=>({width:node.getBoundingClientRect().width,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth}))}),contentType:'application/json'});
    await page.screenshot({path:testInfo.outputPath('viewer-mobile-wider-font-2x.png')});
    await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();await page.evaluate(()=>{document.documentElement.style.zoom='1';});await settleViewport(390);
    const longFiles=await(await request.get('/prompt_manager/images/output?mode=workspace&q=a-very-long&limit=40')).json();const long=longFiles.images[0];
    await page.locator(`article[data-image-key="${long.root_index}:${long.path}"] .image-card-actions button:first-child`).click();await openCollection(page);
    await page.evaluate(()=>{document.documentElement.style.zoom='2';});await settleViewport(390);await modal.evaluate(node=>{node.scrollTop=0;});
    await expect(modal.getByRole('button',{name:'Close image viewer',exact:true})).toBeInViewport();
});

async function currentStageFit(page) {
    return page.evaluate(()=>{
        const instance=window.fitInstances.at(-1),stage=document.querySelector('#viewer-stage');
        const bounds=stage.getBoundingClientRect(),image=instance.image.getBoundingClientRect();
        return {stage:{width:stage.offsetWidth,height:stage.offsetHeight},viewer:{...instance.viewerData},
            image:{width:image.width,height:image.height},
            center:{x:image.x+image.width/2-bounds.x-bounds.width/2,y:image.y+image.height/2-bounds.y-bounds.height/2},
            contained:image.left>=bounds.left-1&&image.top>=bounds.top-1&&image.right<=bounds.right+1&&image.bottom<=bounds.bottom+1};
    });
}
async function openStageFitFixture(page,request) {
    await expect(await request.post('/__test__/reset',{data:{full_inspector:true}})).toBeOK();
    await page.setViewportSize({width:1600,height:1100});await page.goto('/prompt_manager/workspace#/images');
    let release;const pending=new Promise(resolve=>{release=resolve;});
    await page.route('**/images/inspect/architecture/study-02.png?*',async route=>{
        const response=await route.fetch(),data=await response.json();data.library.title='W'.repeat(220);await pending;
        await route.fulfill({response,json:data}).catch(()=>{});
    });
    await page.evaluate(async()=>{
        const [{createMediaViewer},{createInspectionController},{createScope},{createPromptSender}]=await Promise.all([
            import('/prompt_manager/js/workspace/media-viewer.mjs'),import('/prompt_manager/js/workspace/inspection-state.mjs'),
            import('/prompt_manager/js/shared/toolkit/core.js'),import('/prompt_manager/js/workspace/prompt-sender.mjs')]);
        const api=window.ApiClient.createApiClient(),list=await api.get('/prompt_manager/images/output?mode=workspace&limit=40');
        const tall=list.images.find(image=>image.root_index===1&&image.path==='architecture/study-02.png');
        const next=list.images.find(image=>image.root_index===0&&image.path==='inspector/explicit-empty.png');
        window.fitInstances=[];window.fitResizeCalls=[];window.fitObservers=[];
        const Native=window.Viewer,resize=Native.prototype.resize;
        Native.prototype.resize=function(...args){window.fitResizeCalls.push({destroyed:!!this.destroyed,connected:this.element.isConnected});return resize.apply(this,args);};
        window.Viewer=class extends Native {constructor(...args){super(...args);window.fitInstances.push(this);}};
        const NativeObserver=window.ResizeObserver;
        window.ResizeObserver=class extends NativeObserver {
            constructor(callback){super(callback);this.probe={callback,observer:this,disconnected:false};window.fitObservers.push(this.probe);}
            observe(target,...args){this.probe.target=target;return super.observe(target,...args);}
            disconnect(){this.probe.disconnected=true;super.disconnect();}
        };
        const scope=createScope(),controller=createInspectionController({api}),sender=createPromptSender({scope});scope.cleanup(()=>controller.dispose());
        const view=createMediaViewer({container:document.getElementById('workspace-main'),parentScope:scope,inspectionController:controller,sender,onSelect:entry=>controller.select(entry)});
        window.stageFitFixture={scope,controller,view};view.open({items:[tall,next],selectedKey:`${tall.root_index}:${tall.path}`,trigger:document.activeElement});
    });
    await expect(mediaDialog(page).getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
    await expect(mediaDialog(page).locator('.media-reason')).toBeHidden();
    return release;
}
async function attachStageFit(page,testInfo,name) {
    const data=await currentStageFit(page);await testInfo.attach(name,{body:JSON.stringify(data,null,2),contentType:'application/json'});return data;
}

test('collection viewer fits current stage after readiness reason removal',async({page,request},testInfo)=>{
    const release=await openStageFitFixture(page,request);
    try {
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        await attachStageFit(page,testInfo,'ready-stage-fit');
        await page.screenshot({path:testInfo.outputPath('ready-stage-fit.png')});
        await expect.poll(async()=>{const fit=await currentStageFit(page);return Math.abs(fit.viewer.height-fit.stage.height);}).toBeLessThanOrEqual(1);
        const fit=await currentStageFit(page);expect(Math.abs(fit.center.x)).toBeLessThanOrEqual(1);expect(Math.abs(fit.center.y)).toBeLessThanOrEqual(1);expect(fit.contained).toBe(true);
    } finally {release();await page.evaluate(()=>window.stageFitFixture.scope.dispose());}
});

test('collection viewer refits a late wrapped title without replacing current media',async({page,request},testInfo)=>{
    const release=await openStageFitFixture(page,request);
    try {
        const modal=mediaDialog(page),fit=modal.getByRole('button',{name:'Fit image',exact:true});
        await page.evaluate(()=>{window.fitImage=document.querySelector('.viewer-canvas img');window.fitControl=document.querySelector('[aria-label="Fit image"]');});
        await fit.focus();const initial=await attachStageFit(page,testInfo,'before-late-title');release();
        await expect(modal.locator('#viewer-title')).toHaveText(`${'W'.repeat(220)} · study-02.png`);
        await expect(fit).toBeFocused();await fit.click();await attachStageFit(page,testInfo,'late-title-fit');
        await expect.poll(async()=>{const data=await currentStageFit(page);return Math.abs(data.viewer.height-data.stage.height);}).toBeLessThanOrEqual(1);
        const current=await currentStageFit(page);expect(current.stage.height).toBeLessThan(initial.stage.height);expect(Math.abs(current.center.y)).toBeLessThanOrEqual(1);expect(current.contained).toBe(true);
        await page.screenshot({path:testInfo.outputPath('late-title-fit.png')});
        expect(await page.evaluate(()=>window.fitImage===document.querySelector('.viewer-canvas img')&&window.fitControl===document.querySelector('[aria-label="Fit image"]')&&window.fitInstances.length===1)).toBe(true);
        await modal.getByRole('button',{name:'Zoom in',exact:true}).click();await modal.getByRole('button',{name:'Rotate image 90 degrees',exact:true}).click();await modal.getByRole('button',{name:'Flip image horizontally',exact:true}).click();
        const transformed=await page.evaluate(()=>({...window.fitInstances[0].imageData}));await page.evaluate(()=>window.stageFitFixture.controller.retry());
        await expect(modal.locator('#viewer-details > div > p.small')).toHaveText('study-02.png');await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        expect(await page.evaluate(()=>window.fitInstances[0].imageData)).toEqual(transformed);
        await fit.click();const fitted=await currentStageFit(page);expect(Math.abs(fitted.center.y)).toBeLessThanOrEqual(1);expect(fitted.contained).toBe(true);
        await modal.getByRole('button',{name:'Next media',exact:true}).click();await expect(modal.getByRole('button',{name:'Zoom in',exact:true})).toHaveAttribute('aria-disabled','false');
        await page.evaluate(()=>{for(const probe of window.fitObservers.filter(probe=>probe.disconnected))probe.callback([],probe.observer);});
        await modal.getByRole('button',{name:'Close image viewer',exact:true}).click();await expect(modal).toHaveCount(0);
        await page.evaluate(async()=>{for(const probe of window.fitObservers)probe.callback([],probe.observer);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
        expect(await page.evaluate(()=>window.fitObservers.every(probe=>probe.disconnected))).toBe(true);
        expect(await page.evaluate(()=>window.fitResizeCalls.filter(call=>call.destroyed||!call.connected))).toEqual([]);
    } finally {release();await page.evaluate(()=>window.stageFitFixture.scope.dispose());}
});
