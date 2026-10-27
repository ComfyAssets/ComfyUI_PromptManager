import test from 'node:test';
import assert from 'node:assert/strict';

const modulePath='../../web/js/workspace/file-inspector.mjs';
const load=()=>import(modulePath);
const rawWorkflow='{ "nodes": [], "seed": 9007199254740993, "cfg": 1.0000000000000000001 }';
function record() {
    return {identity:{kind:'rooted-file',key:'2:a.png',root_index:2,path:'a.png',root_scope:'a'.repeat(64)},file:{format:'PNG'},prompts:{positive:{status:'resolved',text:'a\n猫'}},generation:{seed:{status:'known',value:'9007199254740993',source:'workflow',evidence:'configuration'}},library:{category_scope:'pair',tags_scope:'pair',tags:['saved']},download:{filename:'a.png',url:'/prompt_manager/images/serve/a.png?root=2'},metadata:{status:'available',omitted_reason:null,raw:{workflow:rawWorkflow,prompt:'{broken',parameters:'literal <svg> </pre>'},record_formats:{workflow:'original-text',prompt:'original-text',parameters:'original-text'},workflow_graph:{status:'parsed',value:{seed:'9007199254740993'}},prompt_graph:{status:'malformed',value:null},parameters:'literal <svg> </pre>'}};
}

test('metadata export retains literal raw and scoped evidence',async()=>{
    const {inspectionDownloads}=await load(),source=record(),download=inspectionDownloads(source).metadata;
    assert.equal(download.available,true);assert.equal(download.mime,'application/json');assert.equal(download.filename,'a.metadata.json');
    const value=JSON.parse(download.bodyText);assert.equal(value.inspection_version,1);
    for(const key of ['identity','file','prompts','generation','library'])assert.deepEqual(value[key],source[key]);
    assert.deepEqual(value.metadata.raw,source.metadata.raw);assert.deepEqual(value.metadata.record_formats,source.metadata.record_formats);
    assert.equal(value.metadata.prompt_graph,undefined);assert.equal(value.metadata.workflow_graph,undefined);
});
test('workflow export never reserializes unsafe numeric literals',async()=>{
    const {inspectionDownloads}=await load();assert.equal(inspectionDownloads(record()).workflow.bodyText,rawWorkflow);
    const source=record();source.metadata.workflow_graph.status='malformed';assert.equal(inspectionDownloads(source).workflow.available,false);
});
test('omitted raw forbids partial export',async()=>{
    const {inspectionDownloads}=await load(),source=record();source.metadata.status='limited';source.metadata.omitted_reason='response-size-limit';source.metadata.raw={};
    for(const value of Object.values(inspectionDownloads(source))) {assert.equal(value.available,false);assert.equal(value.bodyText,null);assert.equal(value.reason,'Raw metadata exceeds the response limit. Download the original file to inspect it externally.');}
    assert.ok(source.download.url);
});
test('download names cannot introduce path segments',async()=>{
    const {inspectionDownloads}=await load(),source=record();source.download.filename='../../evil\\nested\u0000.png';
    for(const value of Object.values(inspectionDownloads(source)))assert.doesNotMatch(value.filename,/[\\/\x00-\x1f]/);
});
test('already disposed inspector creates no DOM URLs or handlers',async()=>{
    const {mountFileInspector,mountPromptInspector}=await load(),parent=new AbortController();parent.abort();
    const forbidden=new Proxy({}, {get(){assert.fail('disposed mount touched DOM/API');}});
    for(const mount of [mountFileInspector,mountPromptInspector]) {
        const view=mount(forbidden,{parentScope:{signal:parent.signal},sender:forbidden,api:forbidden,image:forbidden});
        view.setState?.({record:record()});view.dispose();view.dispose();
    }
});
test('reserialized container workflow never exports as original',async()=>{
    const {inspectionDownloads}=await load(),source=record();source.metadata.record_formats.workflow='reserialized-json';
    const workflow=inspectionDownloads(source).workflow;assert.equal(workflow.available,false);assert.equal(workflow.bodyText,null);
    assert.equal(workflow.reason,'Original workflow JSON text is unavailable for this container record. Download metadata or the original file.');
});
test('metadata export preserves representation formats and omits private carrier',async()=>{
    const {inspectionDownloads}=await load(),source=record();source.metadata.record_formats.workflow='reserialized-json';source.metadata.raw.private_carrier='SECRET';source.metadata.carrier='SECRET';
    const download=inspectionDownloads(source).metadata,value=JSON.parse(download.bodyText);
    assert.equal(value.metadata?.record_formats?.workflow,'reserialized-json');assert.equal(value.metadata.raw.workflow,rawWorkflow);assert.ok(!download.bodyText.includes('SECRET'));
    assert.equal(value.metadata.representations.workflow,'Reserialized container JSON; numeric spelling/precision may differ');
});

test('reported location metadata export carries exact occurrence without assigning historical ownership',async()=>{
    const {inspectionDownloads}=await load(),source=record();
    source.identity={kind:'reported-location',key:'b'.repeat(64),path:'folder/a.png',occurrence:{run_id:'22222222-2222-2222-2222-222222222222',output_node_id:'𐀀',image_ordinal:255},history_epoch:'11111111-1111-1111-1111-111111111111',current_scope:'c'.repeat(64),reference:{folder_type:'temp',subfolder:'folder',filename:'a.png',path_style:'posix',root_key:null}};
    const value=JSON.parse(inspectionDownloads(source).metadata.bodyText);assert.deepEqual(value.identity,source.identity);assert.equal(value.identity.root_index,undefined);assert.equal(value.context_id,undefined);assert.equal(value.file_identity,undefined);assert.deepEqual(value.prompts,source.prompts);
});

test('limited workflow record exports its representation and disables workflow download',async()=>{
    const {inspectionDownloads}=await load(),source=record();
    source.metadata.record_formats.workflow='limited';delete source.metadata.raw.workflow;source.metadata.workflow_graph={status:'limited',value:null};
    const downloads=inspectionDownloads(source),value=JSON.parse(downloads.metadata.bodyText);
    assert.equal(value.metadata.record_formats.workflow,'limited');
    assert.equal(value.metadata.representations.workflow,'Record exceeds inspection limits; text is unavailable');
    assert.equal(value.metadata.raw.workflow,undefined);
    assert.equal(downloads.workflow.available,false);assert.equal(downloads.workflow.bodyText,null);
    assert.equal(downloads.workflow.reason,'Workflow exceeds inspection limits. Download the original file to inspect it externally.');
});

test('Show in folder needs the full listing identity (D38)',async()=>{
    const {revealTarget}=await load(),scope='a'.repeat(64);
    assert.deepEqual(revealTarget(record().identity),{root_index:2,root_scope:scope,path:'a.png'});
    for(const identity of [
        {kind:'rooted-file',root_index:2,path:'a.png',root_scope:null},
        {kind:'rooted-file',root_index:'2',path:'a.png',root_scope:scope},
        {kind:'rooted-file',root_index:2,path:'',root_scope:scope},
        {kind:'reported-location',path:'folder/a.png',current_scope:scope},
        null,
    ])assert.equal(revealTarget(identity),null);
});
test('Show in folder outcomes use the D38 toasts',async()=>{
    const {revealOutcome}=await load();
    assert.equal(revealOutcome(null),'Opened the folder on this computer.');
    assert.equal(revealOutcome({status:403,code:'not-local'}),'Show in folder works only in a browser on the computer running ComfyUI.');
    assert.equal(revealOutcome({status:501,code:'unavailable'}),'No file manager is available on this computer.');
    assert.equal(revealOutcome({status:429,code:'busy'}),'The folder is already opening.');
    for(const error of [{status:403,code:null},{status:409,code:'source-changed'},{status:415,code:'unsupported-media-type'},{status:429,code:null},{status:500},new TypeError('offline')])
        assert.equal(revealOutcome(error),'The folder could not be opened. Try again.');
});

// PR L2 Task 4(c): a record read in this browser renders the D66 local rail.
const {mountedDom} = await import('./helpers/mounted-dom.mjs');
const {createScope} = await import('../../web/js/shared/toolkit/core.js');
const {createRequire} = await import('node:module');
const fs = await import('node:fs');
const require = createRequire(import.meta.url);
const PngMetadata = require('../../web/js/png-metadata.js'), ComfyMetadata = require('../../web/js/comfy-metadata.js');
const {raw:pngRaw, ihdr, idat, iend, tEXt} = await import('./helpers/png-bytes.mjs');
const LOCAL_PROMPT = '{"3":{"class_type":"KSampler","inputs":{"seed":7,"steps":20,"cfg":8.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["4",0],"positive":["6",0],"negative":["7",0],"latent_image":["5",0]}},"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"synthetic-v1.safetensors"}},"5":{"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}},"6":{"class_type":"CLIPTextEncode","inputs":{"text":"a red fox in snow, detailed fur","clip":["4",1]}},"7":{"class_type":"CLIPTextEncode","inputs":{"text":"blurry, lowres","clip":["4",1]}}}';
const LOCAL_URL = 'blob:http://localhost/11111111-2222-3333-4444-555555555555';
const PARITY = new URL('../fixtures/inspection_parity/', import.meta.url);

async function localRecord(file) {
    const local = await import('../../web/js/workspace/local-inspection.mjs');
    const previous = {png:globalThis.PngMetadata, comfy:globalThis.ComfyMetadata};
    globalThis.PngMetadata = PngMetadata; globalThis.ComfyMetadata = ComfyMetadata;
    try {
        const classification = PngMetadata.classifyPrefix(new Uint8Array(await file.slice(0, 32).arrayBuffer()));
        const handle = local.createLocalFileHandle({file, url:LOCAL_URL, classification});
        return local.localFileAdapter.toRecord(await local.localFileAdapter.read(handle), handle, ComfyMetadata.extractGenerationFacts);
    } finally {
        if (previous.png === undefined) delete globalThis.PngMetadata; else globalThis.PngMetadata = previous.png;
        if (previous.comfy === undefined) delete globalThis.ComfyMetadata; else globalThis.ComfyMetadata = previous.comfy;
    }
}
const localPng = name => new File([pngRaw(ihdr(640, 480), idat(), tEXt('prompt', LOCAL_PROMPT), iend())], name, {type:'image/png', lastModified:1_700_000_000_000});
function renderFull(record) {
    const f = mountedDom(), scope = createScope();
    const forbidden = new Proxy({}, {get(_, key) {assert.fail(`api.${String(key)} touched by a local record`);}});
    const view = mountFileInspector(f.container, {api:forbidden, sender:{open() {}}, parentScope:scope, variant:'full'});
    view.setState({loading:false, error:false, record});
    const texts = predicate => f.walk(f.container, predicate).map(node => node.textContent);
    return {f, texts, done() {view.dispose(); scope.dispose(); f.restore();}};
}
const {mountFileInspector} = await load();

test('local record renders local provenance', async () => {
    const record = await localRecord(localPng('Fox Render.png'));
    const {f, texts, done} = renderFull(record);
    try {
        const section = f.walk(f.container, n => n.tagName === 'details' && n.children[0]?.textContent === 'File & provenance')[0];
        assert.ok(section, 'the full variant has File & provenance');
        const lines = f.walk(section, n => n.tagName === 'p').map(n => n.textContent);
        assert.equal(lines[0], 'Selected local file · read in this browser');
        assert.ok(lines.includes('Local file; no saved library association was checked.'));
        assert.ok(!lines.some(line => /historical bytes|saved positive prompt|Saved association/.test(line)));
        assert.deepEqual(f.walk(section, n => n.tagName === 'dt').map(n => n.textContent), ['Source', 'Size', 'Dimensions', 'Format', 'Media type', 'Modified']);
        const values = Object.fromEntries(f.walk(section, n => n.tagName === 'dl')[0].children.map(row => [row.children[0].textContent, row.children[1].textContent]));
        assert.equal(values.Source, 'Selected local file');
        assert.equal(values.Dimensions, '640 × 480');
        assert.equal(values.Format, 'PNG');
        assert.equal(values['Media type'], 'image');
        assert.equal(texts(n => n.tagName === 'button' && /Show .* in folder|Show in folder/.test(n.textContent)).length, 0);
        assert.equal(f.walk(f.container, n => n.tagName === 'button' && n.textContent.includes('Prompt history')).length, 0);
        const original = f.walk(section, n => n.tagName === 'a' && n.textContent === 'Download original')[0];
        assert.ok(original, 'Download original is present');
        assert.equal(original.href, LOCAL_URL);
        assert.equal(original.download, 'Fox Render.png');
        // The heading names the file itself; no library title replaces it.
        assert.equal(f.walk(f.container, n => n.tagName === 'h2')[0].textContent, 'Fox Render.png');
    } finally { done(); }
});

test('local record shows no image-tag chips', async () => {
    const record = await localRecord(localPng('a.png'));
    assert.equal(record.library.status, 'unavailable');
    // A local payload carries no image_tags facet (#281), so the record has none and only linked chips could show.
    assert.equal(record.imageTags, null);
    const {inspectorTagChips} = await load();
    assert.deepEqual(inspectorTagChips(record, {filterable:true}), []);
    const {f, done} = renderFull(record);
    try {
        // Only the format chip and the D21 evidence badge: no category, saved tag or image-tag chips.
        const chips = f.walk(f.container, n => /(^| )chip( |$)/.test(n.className ?? '')).map(n => n.textContent);
        assert.deepEqual(chips, ['PNG', 'Workflow inferred']);
        assert.equal(f.walk(f.container, n => n.tagName === 'button' && /^Tag/.test(n.textContent)).length, 0);
    } finally { done(); }
});

test('legacy fill applies to local records', async () => {
    // f09: the server's inspection is ambiguous; the local record shows the legacy parse, labelled as inferred.
    const ambiguous = await localRecord(new File([fs.readFileSync(new URL('f09.png', PARITY))], 'f09.png'));
    const graphs = {prompt:ambiguous.metadata.prompt_graph.value, workflow:ambiguous.metadata.workflow_graph.status === 'parsed' ? ambiguous.metadata.workflow_graph.value : null};
    const legacy = ComfyMetadata.extractGenerationParams(graphs);
    assert.deepEqual(ambiguous.prompts.positive, {status:'resolved', text:legacy.positivePrompt, source:'graph-inferred'});
    assert.deepEqual(ambiguous.prompts.negative, {status:'resolved', text:legacy.negativePrompt, source:'graph-inferred'});
    assert.equal(ambiguous.prompts.positive.text, 'a red fox in snow, detailed fur');
    const view = renderFull(ambiguous);
    try {
        assert.deepEqual(view.texts(n => n.className === 'chip blue'), ['Workflow inferred']);
        assert.ok(view.texts(n => n.tagName === 'p').includes('Prompt evidence: workflow inferred'));
    } finally { view.done(); }
    // f01b: the legacy parse would read the positive text through ConditioningZeroOut; the guard shows the zeroed negative.
    const zeroed = await localRecord(new File([fs.readFileSync(new URL('f01b.png', PARITY))], 'f01b.png'));
    assert.deepEqual(zeroed.prompts.negative, {status:'resolved', text:'', source:'zeroed-conditioning'});
    assert.notEqual(zeroed.prompts.positive.text, '');
});

test('a branch named by recorded run history shows the Recorded run badge', async () => {
    // #386: the server labels sides whose save node recorded history named; Execution recorded stays stronger.
    const base = await localRecord(new File([fs.readFileSync(new URL('f09.png', PARITY))], 'f09.png'));
    const recorded = {...base, prompts:{positive:{...base.prompts.positive, source:'recorded-run'}, negative:{...base.prompts.negative, source:'recorded-run'}}};
    let view = renderFull(recorded);
    try {
        assert.deepEqual(view.texts(n => n.className === 'chip blue'), ['Recorded run']);
        assert.ok(view.texts(n => n.tagName === 'p').includes('Prompt evidence: recorded run'));
    } finally { view.done(); }
    view = renderFull({...recorded, prompts:{...recorded.prompts, negative:{...recorded.prompts.negative, source:'execution'}}});
    try {
        assert.deepEqual(view.texts(n => n.className === 'chip blue'), ['Execution recorded']);
    } finally { view.done(); }
});

// #249 PR C Task C3 (D88): one chip row, the file's current image tags first, then D21's linked tags not already shown.
function tagged(imageTags, library = {status:'linked', tags:['Minimal', 'warm_light', 'editorial'], tags_scope:'positive'}) {
    return {...record(), imageTags, library};
}
test('renders current image tags before linked tags without duplicates', async () => {
    const {inspectorTagChips} = await load();
    assert.equal(typeof inspectorTagChips, 'function', 'inspectorTagChips is exported');
    assert.deepEqual(inspectorTagChips(tagged({state:'current', tags:['dunes', 'minimal']}), {filterable:true}),
        [{name:'dunes', filter:true}, {name:'minimal', filter:true}, {name:'warm_light', filter:true}, {name:'editorial', filter:true}]);
    // D21's rule still decides the linked chips: a recipe's own tags stay plain, an image tag stays a filter.
    assert.deepEqual(inspectorTagChips(tagged({state:'current', tags:['Editorial']}, {status:'linked', tags:['editorial', 'cover'], tags_scope:'pair'}), {filterable:true}),
        [{name:'Editorial', filter:true}, {name:'cover', filter:false}]);
    // An unlinked file shows only its own tags; a facet-less record (history, local) only the linked ones.
    assert.deepEqual(inspectorTagChips(tagged({state:'current', tags:['loose']}, {status:'none', tags:[], tags_scope:null}), {filterable:true}), [{name:'loose', filter:true}]);
    assert.deepEqual(inspectorTagChips(tagged(null), {filterable:true}).map(chip => chip.name), ['Minimal', 'warm_light', 'editorial']);
    assert.deepEqual(inspectorTagChips(tagged({state:'current', tags:[]}, {status:'ambiguous', tags:['x'], tags_scope:'positive'}), {filterable:true}), []);
});
test('image tag chips filter in the panel and are plain in the viewer', async () => {
    const {inspectorTagChips} = await load();
    const source = tagged({state:'current', tags:['dunes']});
    assert.deepEqual(inspectorTagChips(source, {filterable:true}).map(chip => chip.filter), [true, true, true, true]);
    assert.deepEqual(inspectorTagChips(source, {filterable:false}).map(chip => chip.filter), [false, false, false, false]);
});
test('none and unavailable render no image chips', async () => {
    const {inspectorTagChips} = await load();
    for (const state of ['none', 'unavailable']) {
        assert.deepEqual(inspectorTagChips(tagged({state, tags:[]}), {filterable:true}).map(chip => chip.name), ['Minimal', 'warm_light', 'editorial']);
        assert.deepEqual(inspectorTagChips(tagged({state, tags:[]}, {status:'none', tags:[], tags_scope:null}), {filterable:true}), []);
    }
});

// LI-4 (D117): "Copy settings summary" after the six facts of the full inspector consolidates the legacy metadata
// page's COPY ALL (web/metadata.html copyAllData). The legacy template is read from that page itself and run on the
// legacy parse, so the summary's text is the legacy page's, line for line, wherever the two agree on the values.
const METADATA_PAGE = fs.readFileSync(new URL('../../web/metadata.html', import.meta.url), 'utf8');
function legacyCopyAll(params) {
    const template = METADATA_PAGE.match(/const allData = `([\s\S]*?)`;/)[1];
    return new Function('currentWorkflowData', `return \`${template}\`;`)(params);
}
const legacyParams = record => ComfyMetadata.extractGenerationParams({prompt:record.metadata.prompt_graph.status === 'parsed' ? record.metadata.prompt_graph.value : null,
    workflow:record.metadata.workflow_graph.status === 'parsed' ? record.metadata.workflow_graph.value : null});

/**
 * Each summary line against the legacy page's, allowing only the recorded differences (D117): a number keeps the
 * inspector's (the server's) spelling ("8.0" where the legacy page wrote 8); a fact the legacy parse left "Unknown" may
 * be known; and a negative the #249 ZeroOut guard zeroed reads "No negative prompt found" where the legacy parse read
 * the positive's text through the zero-out.
 */
function assertLegacySummary(summary, legacy, record) {
    const lines = summary.split('\n'), expected = legacy.split('\n');
    assert.deepEqual(lines.map(line => line.split(':')[0]), expected.map(line => line.split(':')[0]), 'the legacy labels, in order');
    lines.forEach((line, index) => {
        if (line === expected[index]) return;
        const [label, value] = [line.slice(0, line.indexOf(': ')), line.slice(line.indexOf(': ') + 2)];
        const old = expected[index].slice(label.length + 2);
        if (label === 'Negative Prompt' && record.prompts.negative?.source === 'zeroed-conditioning' && value === 'No negative prompt found') return;
        if (old === 'Unknown' && !/Prompt$/.test(label)) return;
        assert.ok(value.trim() !== '' && Number(value) === Number(old), `${label}: "${value}" differs from the legacy "${old}"`);
    });
}

test('settings summary is the legacy COPY ALL text for a local file', async () => {
    const {settingsSummary} = await load();
    assert.equal(typeof settingsSummary, 'function', 'settingsSummary is exported');
    const record = await localRecord(localPng('fox.png'));
    assertLegacySummary(settingsSummary(record), legacyCopyAll(legacyParams(record)), record);
    assert.equal(settingsSummary(record), ['Checkpoint: synthetic-v1.safetensors', 'Positive Prompt: a red fox in snow, detailed fur',
        'Negative Prompt: blurry, lowres', 'Steps: 20', 'CFG Scale: 8.0', 'Sampler: euler', 'Seed: 7'].join('\n'));
    // Every parity PNG read in this browser: the legacy page's text but for the recorded differences, and exactly the
    // legacy text for most of them (f09 among them: the server is ambiguous, so the record shows the legacy fill).
    const exact = [];
    for (const name of fs.readdirSync(PARITY).filter(item => item.endsWith('.png')).sort()) {
        const parity = await localRecord(new File([fs.readFileSync(new URL(name, PARITY))], name));
        const summary = settingsSummary(parity), legacy = legacyCopyAll(legacyParams(parity));
        assertLegacySummary(summary, legacy, parity);
        if (summary === legacy) exact.push(name);
    }
    assert.ok(exact.includes('f09.png'));
    assert.deepEqual(fs.readdirSync(PARITY).filter(item => item.endsWith('.png') && !exact.includes(item)).sort(), ['f01b.png', 'f17.png', 'f23.png']);
});

test('settings summary uses the legacy placeholders for what the record does not know', async () => {
    const {settingsSummary} = await load();
    const unknown = {...record(), prompts:{positive:{status:'unresolved'}, negative:{status:'ambiguous'}}, generation:{}};
    assert.equal(settingsSummary(unknown), legacyCopyAll({}).replace('Positive Prompt: undefined', 'Positive Prompt: No prompt found')
        .replace('Negative Prompt: undefined', 'Negative Prompt: No negative prompt found'));
    assert.equal(settingsSummary(unknown), ['Checkpoint: Unknown', 'Positive Prompt: No prompt found', 'Negative Prompt: No negative prompt found',
        'Steps: Unknown', 'CFG Scale: Unknown', 'Sampler: Unknown', 'Seed: Unknown'].join('\n'));
    // A server answer is shown as the inspector shows it: known facts verbatim, every other state the legacy "Unknown".
    const server = {...record(), prompts:{positive:{status:'resolved', text:'a\n猫'}, negative:{status:'resolved', text:''}}, generation:{
        model:{status:'known', value:'m.safetensors'}, steps:{status:'not-recorded', value:null}, cfg:{status:'known', value:'6.50'},
        sampler:{status:'ambiguous', value:null}, seed:{status:'known', value:'9007199254740993'}, scheduler:{status:'known', value:'karras'}}};
    assert.equal(settingsSummary(server), ['Checkpoint: m.safetensors', 'Positive Prompt: a\n猫', 'Negative Prompt: No negative prompt found',
        'Steps: Unknown', 'CFG Scale: 6.50', 'Sampler: Unknown', 'Seed: 9007199254740993'].join('\n'));
});

/** The full inspector with a toast node and `clipboard` as `navigator.clipboard`; returns the summary button. */
function renderSummary(record, {variant = 'full', clipboard} = {}) {
    const f = mountedDom(), scope = createScope(), fallbacks = [], toast = new f.Node('div');
    f.doc.getElementById = id => id === 'workspace-toast' ? toast : null;
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
    Object.defineProperty(globalThis, 'navigator', {configurable:true, value:clipboard === undefined ? {} : {clipboard}});
    const view = mountFileInspector(f.container, {sender:{open() {}}, parentScope:scope, variant, copyFallback:(text, options) => fallbacks.push({text, ...options})});
    view.setState({loading:false, error:false, record});
    const button = f.walk(f.container, n => n.tagName === 'button' && n.textContent === 'Copy settings summary')[0] ?? null;
    return {f, button, toast, fallbacks, scope, done() {
        view.dispose(); scope.dispose(); f.restore();
        if (previous) Object.defineProperty(globalThis, 'navigator', previous); else delete globalThis.navigator;
    }};
}

test('Copy settings summary follows the facts in the full inspector only', async () => {
    const full = renderSummary(record());
    try {
        assert.ok(full.button, 'the full inspector offers Copy settings summary');
        const inner = full.button.parent, index = inner.children.indexOf(full.button);
        assert.equal(inner.children[index - 1].tagName, 'dl', 'it follows the six facts');
        assert.equal(inner.children[index + 1].tagName, 'details', 'File & provenance follows it');
        // M7: the D117 wrap rule targets this button by its class, never any button of a details block.
        assert.equal(full.button.className, 'settings-summary');
    } finally { full.done(); }
    const panel = renderSummary(record(), {variant:'panel'});
    try { assert.equal(panel.button, null, 'the Images side panel keeps the concept footer'); } finally { panel.done(); }
});

test('Copy settings summary copies the summary and shows the shared toast', async () => {
    const {settingsSummary} = await load(), copied = [];
    const view = renderSummary(record(), {clipboard:{writeText:async text => {copied.push(text);}}});
    try {
        await view.button.listeners.get('click')[0].callback({target:view.button});
        assert.deepEqual(copied, [settingsSummary(record())]);
        assert.equal(view.toast.textContent, 'Settings summary copied.');
        assert.equal(view.fallbacks.length, 0);
    } finally { view.done(); }
});

test('Copy settings summary without a clipboard opens the select-to-copy fallback', async () => {
    const {settingsSummary} = await load();
    for (const clipboard of [undefined, {writeText:async () => {throw new Error('Denied');}}]) {
        const view = renderSummary(record(), {clipboard});
        try {
            await view.button.listeners.get('click')[0].callback({target:view.button});
            assert.equal(view.fallbacks.length, 1);
            assert.equal(view.fallbacks[0].text, settingsSummary(record()));
            assert.equal(view.fallbacks[0].title, 'Copy settings summary');
            assert.equal(view.fallbacks[0].trigger, view.button);
            assert.notEqual(view.toast.textContent, 'Settings summary copied.');
        } finally { view.done(); }
    }
});

test('the inspector is busy while a read is loading, then not', async () => {
    const f = mountedDom(), scope = createScope();
    const view = mountFileInspector(f.container, {sender:{open() {}}, parentScope:scope, variant:'full'});
    try {
        view.setState({loading:true, error:false, record:null});
        assert.equal(f.container.getAttribute('aria-busy'), 'true');
        assert.ok(f.walk(f.container, n => n.textContent === 'Loading file details…').length);
        view.setState({loading:false, error:false, record:record()});
        assert.equal(f.container.getAttribute('aria-busy'), null);
        view.setState({loading:true, error:false, record:null});
        view.setState({loading:false, error:true, record:null});
        assert.equal(f.container.getAttribute('aria-busy'), null);
        view.setState({loading:true, error:false, record:null});
    } finally {
        view.dispose();
        assert.equal(f.container.getAttribute('aria-busy'), null, 'dispose clears the busy state');
        scope.dispose(); f.restore();
    }
});

test('the D117 wrap rule targets only the settings summary button', async () => {
    const fs = await import('node:fs');
    const css = fs.readFileSync(new URL('../../web/js/workspace/workspace.css', import.meta.url), 'utf8');
    const rules = css.split('\n').filter(line => line.includes('D117'));
    assert.deepEqual(rules.map(line => line.trim()), ['.ca-tools #viewer-details .settings-summary { white-space: normal; /* D117 */ }']);
    assert.doesNotMatch(css, /#viewer-details > div > button/);
});
