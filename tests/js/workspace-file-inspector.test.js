import test from 'node:test';
import assert from 'node:assert/strict';

const modulePath='../../web/js/workspace/file-inspector.mjs';
const load=()=>import(modulePath);
const rawWorkflow='{ "nodes": [], "seed": 9007199254740993, "cfg": 1.0000000000000000001 }';
function record() {
    return {identity:{kind:'rooted-file',key:'2:a.png',root_index:2,path:'a.png',root_scope:'a'.repeat(64)},file:{format:'PNG'},prompts:{positive:{status:'resolved',text:'a\n猫'}},generation:{seed:{status:'known',value:'9007199254740993',source:'workflow',evidence:'configuration'}},library:{metadata_scope:'pair',tags:['saved']},download:{filename:'a.png',url:'/prompt_manager/images/serve/a.png?root=2'},metadata:{status:'available',omitted_reason:null,raw:{workflow:rawWorkflow,prompt:'{broken',parameters:'literal <svg> </pre>'},record_formats:{workflow:'original-text',prompt:'original-text',parameters:'original-text'},workflow_graph:{status:'parsed',value:{seed:'9007199254740993'}},prompt_graph:{status:'malformed',value:null},parameters:'literal <svg> </pre>'}};
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
    for(const error of [{status:403,code:null},{status:409,code:'source-changed'},{status:500},new TypeError('offline')])
        assert.equal(revealOutcome(error),'The folder could not be opened. Try again.');
});
