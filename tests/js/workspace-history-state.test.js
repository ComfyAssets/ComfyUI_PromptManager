import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {createApiClient} = createRequire(import.meta.url)('../../web/js/api-client.js');
const {extractGenerationFacts} = createRequire(import.meta.url)('../../web/js/comfy-metadata.js');
import {createInspectionController} from '../../web/js/workspace/inspection-state.mjs';
import {reportedLocationAdapter} from '../../web/js/workspace/history-media.mjs';
const m = await import('../../web/js/workspace/history-state.mjs').catch(e => {if(e.code !== 'ERR_MODULE_NOT_FOUND') throw e; return {};});
const epoch='11111111-1111-1111-1111-111111111111', session='22222222-2222-2222-2222-222222222222';
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const digest=n=>String(n).repeat(64), fields=['raw','effective','prepend','append'];
const proof=(provenance='recorded')=>({provenance,source:provenance==='recorded'?'runtime_return':'unknown',reason:provenance==='recorded'?null:'missing'});
function context(anchor='1',branch='a') {return {anchor_id:anchor,context_id:digest(branch),positive_raw_text_id:digest('e'),positive_snapshot_id:digest('c'),negative_snapshot_id:digest('d'),positive:{raw:'positive\n猫',effective:null,prepend:'',append:''},negative:{raw:'',effective:'negative',prepend:'',append:''},evidence:{format_version:1,role_provenance:'inferred',positive:Object.fromEntries(fields.map(f=>[f,proof(f==='effective'?'unknown':'recorded')])),negative:Object.fromEntries(fields.map(f=>[f,proof()]))},positive_prompt_id:7,negative_prompt_id:8,pair_id:9,reference_epoch:epoch,navigation:{positive_available:true,negative_available:true,pair_available:true}};}
const capture={state:'complete',reason:'captured',format_version:1};
const output={state:'complete',reason:'reported',format_version:1,source:'host_final_history',retained_occurrence_count:4,list_status:'complete'};
function run(n,branch='a') {return {run_id:id(n),session_id:session,storage_epoch:epoch,job_id:id(99),record_version:1,queue_item_id:String(n),observed_dequeue_at:String(9007199254740990n+BigInt(n)),observed_callback_at:null,executor_started_at:null,terminal_event_at:null,outcome:'observed',reason:null,diagnostics_omitted:false,diagnostics_available:false,context_capture:capture,output_summary:output,matching_anchors:[{anchor_id:'1',context_id:digest(branch),positive_snapshot_id:digest('c'),negative_snapshot_id:digest('d')}]};}
function windowPage(items=[run(1),run(2),run(3,'b')],more=false) {const summaries=new Map();for(const r of items) {const a=r.matching_anchors[0];if(!summaries.has(a.context_id)) summaries.set(a.context_id,{context_id:a.context_id,positive_snapshot_id:a.positive_snapshot_id,negative_snapshot_id:a.negative_snapshot_id,negative_raw:{state:'explicit_empty',preview:'',truncated:false,evidence:[]}});summaries.get(a.context_id).negative_raw.evidence.push({run_id:r.run_id,anchor_id:'1',...proof()});}return {success:true,history_version:1,history_epoch:epoch,group:{positive_id:7,raw_text_id:digest('e'),relation:'exact_frozen_raw_text',scope_status:'matched'},recorded_run_count:3,resume_cursor:items.length?'resume-token':null,items,context_summaries:[...summaries.values()],has_more:more,next_cursor:more?'window-token':null};}
const detail=(n=1)=>({success:true,history_version:1,history_epoch:epoch,run:run(n),contexts:[context(),context('2','b')],context_capture:capture,output_summary:output});
function receipt(node='1',ordinal=0) {return {occurrence:{run_id:id(1),output_node_id:node,image_ordinal:ordinal},reference:{folder_type:'output',subfolder:'',filename:'image.png',path_style:'posix',root_key:digest('a')},file_identity:'unverified',newly_generated:'unknown',context_membership:'unknown',transient:false,current_candidate:{status:'unavailable',reason:'missing',evidence_class:'locator_candidate'}};}
const outputs=(items=[receipt('1',0),receipt('1',1)],more=true)=>({success:true,history_version:1,history_epoch:epoch,capture:{state:'complete',reason:'reported',format_version:1,source:'host_final_history'},capture_fingerprint:digest('f'),retained_occurrence_count:4,list_status:'complete',items,has_more:more,next_cursor:more?'outputs-token':null});
function setup(restore) {const requests=[],changes=[];assert.equal(typeof m.createHistoryController,'function','history controller is exported');const c=m.createHistoryController({positiveId:7,restore,onChange:s=>changes.push(s),api:{get:(url,{signal})=>new Promise((resolve,reject)=>requests.push({url,signal,resolve,reject}))}});return {c,requests,changes};}
async function loaded(restore) {const s=setup(restore),p=s.c.load();s.requests.at(-1).resolve(windowPage());await p;return s;}
async function selected() {const s=await loaded(),p=s.c.selectRun(id(1));s.requests.at(-1).resolve(detail());await p;return s;}

test('chronology keeps A A B as three runs and two branches with exact decimal ordering',async()=>{const {c,requests}=await loaded();assert.deepEqual(c.state.window.items.map(r=>r.run_id),[id(1),id(2),id(3)]);assert.equal(c.state.window.context_summaries.length,2);assert.equal(c.state.window.items[2].observed_dequeue_at,'9007199254740993');assert.equal(requests[0].url,'/prompt_manager/library/7/history?limit=40');assert.equal(c.state.window.recorded_run_count,3);c.dispose();});

test('earlier windows prepend full identities once and refresh replaces bounded count',async()=>{const s=setup();let p=s.c.load();s.requests[0].resolve(windowPage([run(2),run(3,'b')],true));await p;p=s.c.loadEarlier();assert.match(s.requests[1].url,/cursor=window-token/);s.requests[1].resolve(windowPage([run(1),run(2)],false));await p;assert.deepEqual(s.c.state.window.items.map(r=>r.run_id),[id(1),id(2),id(3)]);p=s.c.refresh();s.requests[2].resolve({...windowPage([run(3)]),recorded_run_count:1});await p;assert.deepEqual(s.c.state.window.items.map(r=>r.run_id),[id(3)]);assert.equal(s.c.state.window.recorded_run_count,1);s.c.dispose();});

test('malformed windows never enable actions',async()=>{for(const mutate of [p=>p.items[0].queue_item_id='01',p=>p.items[0].observed_dequeue_at='9223372036854775808',p=>p.items[0].run_id='bad',p=>p.history_epoch='bad',p=>p.group.positive_id=8,p=>p.context_summaries[0].negative_raw.preview='x'.repeat(101),p=>p.has_more=true,p=>p.next_cursor='',p=>p.items[0].matching_anchors[0].context_id='x',p=>p.items[0].observed_callback_at=1,p=>p.recorded_run_count=-1]) {const {c,requests}=setup();const p=c.load(),data=windowPage();mutate(data);requests[0].resolve(data);await p;assert.ok(c.state.window.error);assert.equal(c.state.actionsEnabled,false);c.dispose();}});

test('frozen roles retain explicit empty and unknown values with their own evidence',()=>{assert.equal(typeof m.frozenPromptRoles,'function');const ctx=context(),raw=m.frozenPromptRoles(ctx,'raw'),effective=m.frozenPromptRoles(ctx,'effective');assert.equal(raw.negative.status,'resolved');assert.equal(raw.negative.text,'');assert.deepEqual(raw.negative.evidence,proof());assert.equal(effective.positive.status,'unresolved');assert.equal(effective.positive.text,null);assert.deepEqual(effective.positive.evidence,proof('unknown'));assert.equal(effective.negative.text,'negative');assert.equal(raw.positive.text,'positive\n猫');assert.throws(()=>m.frozenPromptRoles(ctx,'wrong'),TypeError);});

test('detail validates exact roles evidence IDs and epoch before selection',async()=>{for(const mutate of [p=>p.history_epoch=id(4),p=>p.run.run_id=id(2),p=>p.contexts[0].negative_snapshot_id=null,p=>p.contexts[0].positive.raw=42,p=>p.contexts[0].evidence.positive.raw.source='unknown',p=>p.contexts[0].navigation.pair_available=1,p=>p.contexts[0].positive_prompt_id=0,p=>p.contexts.push(p.contexts[0])]) {const {c,requests}=await loaded();const p=c.selectRun(id(1)),data=detail();mutate(data);requests.at(-1).resolve(data);await p;assert.ok(c.state.run.error || c.state.refreshRequired);assert.equal(c.state.run.detail,null);assert.equal(c.state.actionsEnabled,false);c.dispose();}});

test('dependent selections abort late run output success and errors including disposal',async()=>{const {c,requests,changes}=await loaded();const p=c.selectRun(id(1)),q=c.selectRun(id(2));assert.equal(requests[1].signal.aborted,true);requests[2].resolve(detail(2));await q;requests[1].reject({status:409,code:'history-changed'});await p;assert.equal(c.state.selection.run_id,id(2));assert.equal(c.state.refreshRequired,null);const r=c.selectRun(id(1));requests[3].resolve(detail());await r;for(const change of [()=>c.selectContext('2'),()=>c.setMode('effective')]) {const a=c.loadOutputs();const request=requests.at(-1);change();assert.equal(request.signal.aborted,true);request.resolve(outputs());await a;assert.equal(c.state.outputs.items.length,0);}const a=c.loadOutputs(),request=requests.at(-1);c.dispose();const count=changes.length;request.reject({status:409,code:'history-changed'});await a;assert.equal(changes.length,count);});

test('output pagination preserves binary order and exact repeated receipt selection on retry',async()=>{const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;c.selectReceipt({run_id:id(1),output_node_id:'1',image_ordinal:1});assert.equal(c.state.selection.receipt.image_ordinal,1);p=c.loadOutputs();const url=requests.at(-1).url;requests.at(-1).reject(new Error('offline'));await p;assert.equal(c.state.outputs.items.length,2);assert.equal(c.state.outputs.next_cursor,'outputs-token');p=c.retryOutputs();assert.equal(requests.at(-1).url,url);requests.at(-1).resolve(outputs([receipt('\ue000'),receipt('𐀀')],false));await p;assert.deepEqual(c.state.outputs.items.map(r=>r.occurrence.output_node_id),['1','1','\ue000','𐀀']);assert.equal(c.state.selection.receipt.image_ordinal,1);c.dispose();});

test('receipt selection invalidates pending responses without losing loaded coordinates',async()=>{const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;p=c.loadOutputs();const pending=requests.at(-1);c.selectReceipt({run_id:id(1),output_node_id:'1',image_ordinal:0});assert.equal(pending.signal.aborted,true);pending.reject({status:409,code:'capture-changed'});await p;assert.equal(c.state.outputs.items.length,2);assert.equal(c.state.selection.receipt.image_ordinal,0);assert.equal(c.state.refreshRequired,null);c.dispose();});

test('response-limit retries only the failed cursor at the permitted smaller limit',async()=>{const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;p=c.loadOutputs();requests.at(-1).reject({status:422,code:'response-limit',recovery:{action:'smaller-limit',limit:20}});await p;assert.equal(c.state.outputs.items.length,2);p=c.retryOutputs();assert.match(requests.at(-1).url,/limit=20/);assert.match(requests.at(-1).url,/cursor=outputs-token/);requests.at(-1).resolve(outputs([receipt('2',0),receipt('2',1)],false));await p;assert.equal(c.state.outputs.items.length,4);c.dispose();});

test('real ApiClient response recovery retries the same failed output cursor at its smaller limit',async()=>{
    const calls=[];let reads=0;
    const api=createApiClient(async(url,init)=>{
        calls.push({url,method:init.method});
        const path=new URL(url,'http://localhost');
        if(path.pathname.endsWith('/history'))return Response.json(windowPage());
        if(!path.pathname.endsWith('/outputs'))return Response.json(detail());
        reads++;
        if(reads===1)return Response.json(outputs());
        if(reads===2)return Response.json({success:false,history_version:1,code:'response-limit',error:'Reported outputs exceed the response budget',recovery:{action:'smaller-limit',limit:20}},{status:422});
        return Response.json(outputs([receipt('2',0),receipt('2',1)],false));
    });
    const c=m.createHistoryController({api,positiveId:7});
    try {
        await c.load();await c.selectRun(id(1));await c.loadOutputs();
        c.selectReceipt(receipt('1',1).occurrence);const retained=c.state.outputs.items;
        await c.loadOutputs();
        assert.deepEqual(c.state.outputs.error.recovery,{action:'smaller-limit',limit:20});
        assert.equal(c.state.outputs.items,retained);assert.equal(c.state.selection.receipt.image_ordinal,1);
        await c.retryOutputs();
        const pages=calls.filter(call=>call.url.includes('/outputs?')).map(call=>new URL(call.url,'http://localhost'));
        assert.deepEqual(pages.map(url=>url.searchParams.get('limit')),['40','40','20']);
        assert.deepEqual(pages.map(url=>url.searchParams.get('cursor')),[null,'outputs-token','outputs-token']);
        assert.deepEqual(c.state.outputs.items.map(item=>[item.occurrence.output_node_id,item.occurrence.image_ordinal]),[['1',0],['1',1],['2',0],['2',1]]);
        assert.equal(c.state.selection.receipt.image_ordinal,1);assert.equal(c.state.outputs.has_more,false);
        assert.ok(calls.every(call=>call.method==='GET'));
    } finally {c.dispose();}
});

test('history change clears every actionable object and revalidates parent independently',async()=>{const {c,requests}=await selected();const p=c.loadOutputs();requests.at(-1).reject({status:409,code:'history-changed',recovery:{action:'refresh-history'}});await p;assert.equal(c.state.refreshRequired,'history-changed');assert.equal(c.state.window.items.length,0);assert.equal(c.state.run.detail,null);assert.equal(c.state.selection.run_id,null);assert.equal(c.state.outputs.next_cursor,null);assert.equal(c.state.actionsEnabled,false);assert.equal(requests.at(-1).url,'/prompt_manager/library/7');requests.at(-1).resolve({positive:{id:7,text:'saved'},variants:[]});await new Promise(resolve=>setImmediate(resolve));assert.equal(c.state.parent.detail.positive.id,7);assert.equal(c.state.refreshLabel,'Reload recorded history');c.dispose();});

test('capture and candidate changes clear candidates and reload output page one',async()=>{for(const code of ['capture-changed','candidate-stale']) {const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;p=c.loadOutputs();requests.at(-1).reject({status:409,code});await p;assert.equal(c.state.outputs.items.length,0);assert.equal(c.state.actionsEnabled,false);assert.doesNotMatch(requests.at(-1).url,/cursor=/);requests.at(-1).resolve({...outputs([],false),retained_occurrence_count:0});await new Promise(resolve=>setImmediate(resolve));assert.equal(c.state.outputs.error,null);c.dispose();}});

test('reentry validates epoch before actions while restore accepts only harmless intent',async()=>{const {c,requests}=await loaded({search:'猫',scrollTop:120,mode:'effective',state:{window:windowPage()},pairId:9});assert.equal(c.state.intent.search,'猫');assert.equal(c.state.selection.mode,'effective');const p=c.revalidate();assert.equal(c.state.actionsEnabled,false);requests.at(-1).resolve({...windowPage(),history_epoch:id(4)});await p;assert.equal(c.state.refreshRequired,'history-changed');assert.equal(c.state.window.items.length,0);requests.at(-1).resolve({positive:{id:7,text:'saved'},variants:[]});c.dispose();});

test('invalid caller identifiers cannot request history or select nonexistent contexts and receipts',async()=>{assert.equal(typeof m.createHistoryController,'function');for(const positiveId of [0,'7',NaN,9007199254740992]) assert.throws(()=>m.createHistoryController({positiveId,api:{}}),TypeError);const {c,requests}=await selected();await assert.rejects(c.selectRun('bad'),TypeError);assert.throws(()=>c.selectContext('missing'),TypeError);assert.throws(()=>c.selectReceipt({run_id:id(1),output_node_id:'1',image_ordinal:0}),TypeError);assert.equal(requests.length,2);c.dispose();});

test('malformed output coordinates capture fingerprints evidence and continuations fail closed',async()=>{for(const mutate of [p=>p.items[0].occurrence.image_ordinal=256,p=>p.items[0].occurrence.run_id=id(2),p=>p.capture_fingerprint='bad',p=>p.has_more=false,p=>p.history_epoch=id(4),p=>p.items[0].file_identity='verified',p=>p.items[0].reference.root_key='bad',p=>p.items[0].current_candidate.url='/fake',p=>p.items[0].occurrence.output_node_id='\ud800']) {const {c,requests}=await selected();const p=c.loadOutputs(),data=outputs();mutate(data);requests.at(-1).resolve(data);await p;assert.ok(c.state.outputs.error || c.state.refreshRequired);assert.equal(c.state.outputs.items.length,0);c.dispose();}});

test('restored identifiers select only matching freshly validated run and context',async()=>{const {c,requests}=setup({runId:id(1),anchorId:'2',contextId:digest('b'),mode:'effective',outputs:outputs(),receipt:receipt().occurrence});assert.equal(c.state.selection.run_id,null);assert.equal(c.state.outputs.items.length,0);let p=c.load();requests[0].resolve(windowPage());await p;assert.equal(requests.length,2);assert.equal(c.state.run.loading,true);requests[1].resolve(detail());await new Promise(resolve=>setImmediate(resolve));assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.selection.context_id,digest('b'));assert.equal(c.state.selection.mode,'effective');assert.equal(c.state.selection.receipt,null);c.dispose();});

test('new run selection invalidates a pending reentry epoch check and its late error',async()=>{const {c,requests}=await selected();const validation=c.revalidate(),old=requests.at(-1),next=c.selectRun(id(2));assert.equal(old.signal.aborted,true);requests.at(-1).resolve(detail(2));await next;old.reject({status:409,code:'history-changed'});await validation;assert.equal(c.state.selection.run_id,id(2));assert.equal(c.state.run.detail.run.run_id,id(2));assert.equal(c.state.refreshRequired,null);assert.equal(c.state.validating,false);c.dispose();});

test('explicit detail limit errors stay bounded read failures and retry only the detail',async()=>{const {c,requests}=await loaded();let p=c.selectRun(id(1),{anchorId:'2',contextId:digest('b')});requests.at(-1).reject({status:422,code:'response-limit'});await p;assert.equal(c.state.run.error.code,'response-limit');assert.equal(c.state.window.items.length,3);assert.equal(c.state.refreshRequired,null);p=c.retryRun();assert.match(requests.at(-1).url,/\/runs\/00000000-0000-0000-0000-000000000001\?epoch=/);requests.at(-1).resolve(detail());await p;assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.run.error,null);assert.equal(requests.length,3);c.dispose();});

test('repeated stale output responses stop automatic recovery and retain explicit reload state',async()=>{const {c,requests}=await selected();const p=c.loadOutputs();requests.at(-1).reject({status:409,code:'candidate-stale'});await p;assert.equal(requests.length,4);requests.at(-1).reject({status:409,code:'candidate-stale'});await new Promise(resolve=>setImmediate(resolve));assert.equal(requests.length,4,'recovery must not poll indefinitely');assert.equal(c.state.refreshRequired,'candidate-stale');assert.equal(c.state.outputs.error.code,'candidate-stale');c.dispose();});

test('known and unknown frozen evidence cannot contradict their field or declared role provenance',async()=>{for(const mutate of [p=>p.contexts[0].evidence.role_provenance='recorded',p=>p.contexts[0].evidence.negative.raw.reason='missing',p=>p.contexts[0].evidence.positive.effective.reason=null,p=>p.run.outcome='conflicting',p=>p.run.reason='terminal_error']) {const {c,requests}=await loaded();const p=c.selectRun(id(1)),data=detail();mutate(data);requests.at(-1).resolve(data);await p;assert.ok(c.state.run.error);assert.equal(c.state.run.detail,null);c.dispose();}});

test('contradictory page counts and unrelated summary evidence are rejected before showing branches',async()=>{for(const mutate of [p=>{p.items=[];p.has_more=true;p.next_cursor='more';},p=>p.context_summaries[0].negative_raw.evidence[0].run_id=id(88),p=>p.context_summaries[0].positive_snapshot_id=digest('f')]){const {c,requests}=setup(),p=c.load(),data=windowPage();mutate(data);requests[0].resolve(data);await p;assert.ok(c.state.window.error);c.dispose();}const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;p=c.loadOutputs();requests.at(-1).resolve(outputs([receipt('2',0)],false));await p;assert.ok(c.state.outputs.error);assert.equal(c.state.outputs.items.length,2);assert.equal(c.state.outputs.next_cursor,'outputs-token');c.dispose();});

function availableReceipt(currentScope,node='1',ordinal=0) {
    const r=receipt(node,ordinal),locationKey=currentScope==='a'?digest('b'):digest('9');
    const query=new URLSearchParams({node,ordinal:String(ordinal),epoch,current_scope:digest(currentScope)}),base=`/prompt_manager/history/runs/${id(1)}/output/current`;
    const media={kind:'reported-location',occurrence:r.occurrence,reference:r.reference,history_epoch:epoch,current_scope:digest(currentScope),location_key:locationKey,filename:'image.png',is_video:false,url:`${base}/media?${query}`,thumbnail_url:`${base}/thumbnail?${query}`,inspection_url:`${base}/inspection?${query}`};
    return {...r,current_candidate:{status:'available',reason:null,evidence_class:'locator_candidate',current_scope:digest(currentScope),location_key:locationKey,relative_path:'image.png',source_label:'output',gallery_match:'none',gallery_image_id:null,media}};
}

test('every revalidation emission clears old available candidates and selected receipt before enabling actions',async()=>{
    const {c,requests,changes}=await selected();let p=c.loadOutputs();
    requests.at(-1).resolve({...outputs([availableReceipt('a')],false),retained_occurrence_count:1});await p;
    c.selectReceipt({run_id:id(1),output_node_id:'1',image_ordinal:0});assert.equal(c.state.actionsEnabled,true);
    const first=changes.length,validation=c.revalidate();assert.equal(c.state.validating,true);assert.equal(c.state.actionsEnabled,false);
    requests.at(-1).resolve(detail());await new Promise(resolve=>setImmediate(resolve));
    const pending=requests.at(-1);assert.match(pending.url,/\/outputs\?/);assert.equal(c.state.outputs.loading,true);
    const assertSafe=state=>{if(state.actionsEnabled){assert.equal(state.selection.receipt,null,'an enabled emission cannot retain the pre-validation receipt');assert.ok(state.outputs.items.every(r=>r.current_candidate.media.current_scope!==digest('a')),'an enabled emission cannot expose old current scope');}};
    try {
        changes.slice(first).forEach(assertSafe);
        pending.resolve({...outputs([availableReceipt('9')],false),retained_occurrence_count:1});await validation;
        changes.slice(first).forEach(assertSafe);assert.equal(c.state.outputs.items[0].current_candidate.media.current_scope,digest('9'));
        assert.equal(c.state.selection.receipt,null);assert.equal(c.state.validating,false);assert.equal(c.state.actionsEnabled,true);
    } finally {c.dispose();}
});

test('pending mode-independent detail keeps the current mode and derives its exact effective roles without another GET',async()=>{
    const {c,requests}=await loaded(),pending=c.selectRun(id(1)),read=requests.at(-1);
    c.setMode('effective');assert.equal(read.signal.aborted,false);assert.equal(requests.length,2);
    read.resolve(detail());await pending;
    assert.equal(c.state.selection.mode,'effective');const ctx=c.state.run.detail.contexts.find(ctx=>ctx.anchor_id===c.state.selection.anchor_id);
    const roles=m.frozenPromptRoles(ctx,c.state.selection.mode);assert.equal(roles.negative.text,'negative');assert.equal(roles.positive.text,null);assert.equal(roles.positive.status,'unresolved');assert.deepEqual(roles.positive.evidence,proof('unknown'));
    assert.equal(requests.length,2);assert.equal(c.state.run.error,null);c.dispose();
});

test('active inspection history failure clears every emitted owning state for HTTP and stale-echo errors',async()=>{
    for(const transport of ['http','echo']) {
        const {c,requests,changes}=await selected();let pending=c.loadOutputs();
        const item=availableReceipt('a');requests.at(-1).resolve({...outputs([item],false),retained_occurrence_count:1});await pending;c.selectReceipt(item.occurrence);
        const target=item.current_candidate.media,client=createApiClient(async()=>transport==='http'
            ? Response.json({success:false,code:'history-changed',error:'Recorded history changed',recovery:{action:'refresh-history'}},{status:409})
            : Response.json({success:true,history_version:1,inspection_version:1,history_epoch:id(8)}));
        const inspection=createInspectionController({api:client,adapter:reportedLocationAdapter,extractFacts:extractGenerationFacts});
        const remove=inspection.subscribe(state=>c.inspectionFailed?.(target,state)),first=changes.length;
        try {
            await inspection.select(target);
            assert.equal(c.state.refreshRequired,'history-changed');
            for(const state of changes.slice(first)) {
                assert.equal(state.actionsEnabled,false);assert.deepEqual(state.window.items,[]);assert.equal(state.window.history_epoch,null);
                assert.equal(state.run.detail,null);assert.equal(state.selection.run_id,null);assert.equal(state.selection.anchor_id,null);assert.equal(state.selection.context_id,null);assert.equal(state.selection.receipt,null);
                assert.equal(state.window.next_cursor,null);assert.equal(state.outputs.next_cursor,null);assert.deepEqual(state.outputs.items,[]);
            }
            assert.equal(requests.at(-1).url,'/prompt_manager/library/7');requests.at(-1).resolve({positive:{id:7,text:'saved'},variants:[]});await new Promise(resolve=>setImmediate(resolve));
            assert.equal(c.state.parent.detail.positive.text,'saved');assert.equal(c.state.actionsEnabled,false);
        } finally {remove();inspection.dispose();c.dispose();}
    }
});

test('candidate and capture inspection failures keep their narrower recovery and obsolete targets cannot invalidate selection',async()=>{
    for(const code of ['candidate-stale','capture-changed']) {
        const {c,requests}=await selected();let pending=c.loadOutputs();const item=availableReceipt('a');
        requests.at(-1).resolve({...outputs([item],false),retained_occurrence_count:1});await pending;c.selectReceipt(item.occurrence);
        c.inspectionFailed?.(item.current_candidate.media,{error:true,refreshRequired:true,code});
        assert.equal(c.state.refreshRequired,code);assert.equal(c.state.selection.run_id,id(1));assert.equal(c.state.run.detail.run.run_id,id(1));assert.equal(c.state.selection.receipt,null);assert.deepEqual(c.state.outputs.items,[]);
        assert.match(requests.at(-1).url,/\/outputs\?/);requests.at(-1).resolve({...outputs([availableReceipt('9')],false),retained_occurrence_count:1});await new Promise(resolve=>setImmediate(resolve));
        c.selectReceipt(item.occurrence);c.inspectionFailed?.(item.current_candidate.media,{error:true,refreshRequired:true,code:'history-changed'});
        assert.equal(c.state.refreshRequired,null,'old candidate scope cannot invalidate the fresh receipt');assert.equal(c.state.selection.run_id,id(1));
        c.dispose();
    }
});

test('targeted unavailable receipt retry replaces only its bounded page and keeps all exact visit selections',async()=>{
    const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;
    p=c.loadOutputs();requests.at(-1).resolve(outputs([receipt('2',0),receipt('2',1)],false));await p;
    c.selectContext('2');c.setMode('effective');c.selectReceipt(receipt('2',1).occurrence);
    const earlier=c.state.outputs.items.slice(0,2),readCount=requests.length;
    p=c.retryReceipt?.(receipt('2',1).occurrence);
    assert.equal(requests.length,readCount+1,'retry must read the successful page that owns this occurrence');
    assert.match(requests.at(-1).url,/limit=40&cursor=outputs-token/);
    requests.at(-1).resolve(outputs([receipt('2',0),availableReceipt('9','2',1)],false));await p;
    assert.deepEqual(c.state.outputs.items.slice(0,2),earlier);assert.equal(c.state.outputs.items.length,4);
    assert.equal(c.state.outputs.items[3].current_candidate.status,'available');assert.deepEqual(c.state.selection.receipt,receipt('2',1).occurrence);
    assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.selection.context_id,digest('b'));assert.equal(c.state.selection.mode,'effective');assert.equal(c.state.outputs.next_cursor,null);
    const before=requests.length;await c.loadOutputs();assert.equal(requests.length,before,'availability retry cannot invent another page');c.dispose();
});

test('targeted retry retains the receipt on read failure and suppresses late availability after a newer selection',async()=>{
    const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;c.selectReceipt(receipt().occurrence);
    p=c.retryReceipt?.(receipt().occurrence);assert.equal(requests.length,4);
    requests.at(-1).reject(new Error('offline'));await p;assert.equal(c.state.outputs.items.length,2);assert.deepEqual(c.state.selection.receipt,receipt().occurrence);
    p=c.retryOutputs();const pending=requests.at(-1);c.selectReceipt(receipt('1',1).occurrence);assert.equal(pending.signal.aborted,true);
    pending.resolve(outputs([availableReceipt('9'),receipt('1',1)]));await p;
    assert.equal(c.state.outputs.items[0].current_candidate.status,'unavailable');assert.equal(c.state.selection.receipt.image_ordinal,1);c.dispose();
});

test('targeted page retry honors a smaller budget and seeks only within that already loaded receipt page',async()=>{
    const {c,requests}=await selected();let p=c.loadOutputs();requests.at(-1).resolve(outputs());await p;
    p=c.loadOutputs();requests.at(-1).resolve(outputs([receipt('2',0),receipt('2',1)],false));await p;c.selectReceipt(receipt('2',1).occurrence);
    p=c.retryReceipt?.(receipt('2',1).occurrence);assert.equal(requests.length,5);
    requests.at(-1).reject({status:422,code:'response-limit',recovery:{action:'smaller-limit',limit:1}});await p;
    p=c.retryOutputs();assert.match(requests.at(-1).url,/limit=1&cursor=outputs-token/);
    requests.at(-1).resolve({...outputs([receipt('2',0)],true),next_cursor:'target-next'});await new Promise(resolve=>setImmediate(resolve));
    assert.match(requests.at(-1).url,/limit=1&cursor=target-next/);requests.at(-1).resolve(outputs([availableReceipt('9','2',1)],false));await p;
    assert.equal(c.state.outputs.items.length,4);assert.equal(c.state.outputs.items[3].current_candidate.status,'available');assert.equal(c.state.outputs.next_cursor,null);assert.equal(c.state.selection.receipt.image_ordinal,1);assert.equal(requests.length,7);c.dispose();
});

test('same-epoch refresh restores exact selected context mode and receipt only from fresh reads',async()=>{
    const {c,requests,changes}=await selected();let p=c.loadOutputs();requests.at(-1).resolve({...outputs([availableReceipt('a')],false),retained_occurrence_count:1});await p;
    c.selectContext('2');c.setMode('effective');c.selectReceipt(receipt().occurrence);const first=changes.length;
    p=c.refresh();requests.at(-1).resolve(windowPage());await p;
    assert.match(requests.at(-1).url,/\/history\/runs\//,'refresh must revalidate the selected run');assert.equal(c.state.actionsEnabled,false);
    requests.at(-1).resolve(detail());await new Promise(resolve=>setImmediate(resolve));
    assert.match(requests.at(-1).url,/\/outputs\?/);assert.equal(c.state.actionsEnabled,false);
    assert.ok(changes.slice(first).every(state=>!state.actionsEnabled),'old objects must never be briefly enabled');
    requests.at(-1).resolve({...outputs([availableReceipt('9')],false),retained_occurrence_count:1});await new Promise(resolve=>setImmediate(resolve));
    assert.equal(c.state.selection.run_id,id(1));assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.selection.context_id,digest('b'));assert.equal(c.state.selection.mode,'effective');assert.deepEqual(c.state.selection.receipt,receipt().occurrence);
    assert.equal(c.state.outputs.items[0].current_candidate.media.current_scope,digest('9'));assert.equal(c.state.actionsEnabled,true);c.dispose();
});

test('fresh exact group membership keeps an older selected run outside a refreshed newest prefix',async()=>{
    for(const member of [true,false]) {
        const {c,requests}=await selected();c.selectContext('2');c.setMode('effective');
        const p=c.refresh();requests.at(-1).resolve({...windowPage([run(60),run(61)]),recorded_run_count:61});await p;
        assert.match(requests.at(-1).url,/\/runs\/00000000-0000-0000-0000-000000000001\?/);
        const fresh=detail();if(!member)fresh.contexts.forEach(context=>context.positive_raw_text_id=digest('f'));
        requests.at(-1).resolve(fresh);await new Promise(resolve=>setImmediate(resolve));
        assert.deepEqual(c.state.window.items.map(run=>run.run_id),[id(60),id(61)]);
        if(member) {assert.equal(c.state.selection.run_id,id(1));assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.actionsEnabled,true);}
        else {assert.equal(c.state.run.detail,null);assert.equal(c.state.actionsEnabled,false);assert.ok(c.state.run.error);assert.equal(c.state.selection.run_id,null,'an incompatible saved selection must be discarded');}
        c.dispose();
    }
});

test('reentry restores exactly the prior bounded timeline pages before enabling the older selection',async()=>{
    const restored={historyEpoch:epoch,resumeCursor:'old-upper-resume',loadedCount:80,runId:id(1),anchorId:'2',contextId:digest('b'),mode:'effective',outputCount:4,receipt:receipt('2',1).occurrence,scrollLeft:731,scrollTop:95};
    const {c,requests,changes}=setup(restored),p=c.load();assert.match(requests.at(-1).url,/resume=old-upper-resume/);
    requests.at(-1).resolve({...windowPage(Array.from({length:40},(_,i)=>run(41+i)),true),recorded_run_count:80});await new Promise(resolve=>setImmediate(resolve));
    assert.equal(c.state.actionsEnabled,false);assert.match(requests.at(-1).url,/cursor=window-token/);
    requests.at(-1).resolve({...windowPage(Array.from({length:40},(_,i)=>run(1+i))),recorded_run_count:80});await p;
    assert.equal(c.state.window.items.length,80);assert.equal(c.state.actionsEnabled,false);assert.match(requests.at(-1).url,/\/runs\//);
    requests.at(-1).resolve(detail());await new Promise(resolve=>setImmediate(resolve));
    requests.at(-1).resolve(outputs([receipt('1',0),receipt('1',1),receipt('2',0),receipt('2',1)],false));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests.length,4);assert.equal(c.state.selection.run_id,id(1));assert.equal(c.state.selection.anchor_id,'2');assert.equal(c.state.selection.mode,'effective');assert.deepEqual(c.state.selection.receipt,receipt('2',1).occurrence);
    assert.equal(c.state.intent.scrollLeft,731);assert.equal(c.state.intent.scrollTop,95);assert.equal(c.state.actionsEnabled,true);
    assert.ok(changes.slice(0,-1).every(state=>!state.actionsEnabled));c.dispose();
});

test('refresh epoch change clears old selection before independent parent revalidation',async()=>{
    const {c,requests}=await selected();let p=c.refresh();requests.at(-1).resolve({...windowPage(),history_epoch:id(8)});await p;
    assert.equal(c.state.refreshRequired,'history-changed');assert.equal(c.state.selection.run_id,null);assert.equal(c.state.window.resume_cursor,null);assert.equal(c.state.run.detail,null);assert.deepEqual(c.state.outputs.items,[]);assert.equal(c.state.actionsEnabled,false);
    requests.at(-1).resolve({positive:{id:7,text:'saved'},variants:[]});c.dispose();
});

test('obsolete inspection errors after another exact receipt and disposal cannot reach the owning history',async()=>{
    const {c,requests,changes}=await selected();const first=availableReceipt('a'),second=availableReceipt('9','2',0);let p=c.loadOutputs();
    requests.at(-1).resolve({...outputs([first,second],false),retained_occurrence_count:2});await p;
    const pending=[];let target=first.current_candidate.media;
    const inspection=createInspectionController({api:{get:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))},adapter:reportedLocationAdapter,extractFacts:extractGenerationFacts});
    const remove=inspection.subscribe(state=>c.inspectionFailed(target,state));
    c.selectReceipt(first.occurrence);p=inspection.select(target);
    target=second.current_candidate.media;c.selectReceipt(second.occurrence);const next=inspection.select(target);
    pending[1].reject({status:503,code:'storage-unavailable'});await next;const before=changes.length;
    pending[0].reject({status:409,code:'history-changed'});await p;
    assert.equal(changes.length,before);assert.equal(c.state.refreshRequired,null);assert.deepEqual(c.state.selection.receipt,second.occurrence);
    p=inspection.retry();inspection.dispose();c.dispose();const count=changes.length;pending[2].reject({status:409,code:'history-changed'});await p;
    assert.equal(changes.length,count);remove();
});

test('malformed resume and raw membership scalars cannot enable restored actions',async()=>{
    for(const mutate of [page=>page.resume_cursor='',page=>page.resume_cursor='x'.repeat(4097),page=>page.resume_cursor=42,page=>page.resume_cursor=null]) {
        const {c,requests}=setup({historyEpoch:epoch,resumeCursor:'old-range',loadedCount:80,runId:id(1)}),p=c.load(),page=windowPage();mutate(page);requests.at(-1).resolve(page);await p;
        assert.ok(c.state.window.error);assert.equal(c.state.actionsEnabled,false);assert.equal(c.state.run.detail,null);assert.equal(requests.length,1);c.dispose();
    }
    for(const value of [undefined,null,'bad',42]) {
        const {c,requests}=await loaded(),p=c.selectRun(id(1)),fresh=detail();fresh.contexts[0].positive_raw_text_id=value;requests.at(-1).resolve(fresh);await p;
        assert.ok(c.state.run.error);assert.equal(c.state.run.detail,null);assert.equal(c.state.actionsEnabled,false);c.dispose();
    }
});

test('a resumed page success or error cannot replace a later run choice',async()=>{
    for(const outcome of ['success','error']) {
        const {c,requests}=setup({historyEpoch:epoch,resumeCursor:'old-range',loadedCount:80,runId:id(1)}),p=c.load();
        requests[0].resolve({...windowPage(Array.from({length:40},(_,i)=>run(41+i)),true),recorded_run_count:80});await new Promise(resolve=>setImmediate(resolve));
        const old=requests[1],next=c.selectRun(id(41));assert.equal(old.signal.aborted,true);requests[2].resolve(detail(41));await next;
        if(outcome==='success')old.resolve({...windowPage(Array.from({length:40},(_,i)=>run(i+1))),recorded_run_count:80});else old.reject({status:409,code:'history-changed'});
        await p;assert.equal(c.state.selection.run_id,id(41));assert.equal(c.state.window.items.length,40);assert.equal(c.state.refreshRequired,null);assert.equal(c.state.actionsEnabled,true);c.dispose();
    }
});

test('added terms split, trim and exact-diff like the concept',()=>{
    // The concept's rule (app.js a600-614): split on commas, trim, drop empty terms, keep what the baseline lacks exactly.
    assert.equal(typeof m.addedTerms,'function','addedTerms is exported');
    assert.deepEqual(m.addedTerms('blurry, lowres','lowres,  watermark , text'),['watermark','text']);
    assert.deepEqual(m.addedTerms('',''),[]);assert.deepEqual(m.addedTerms('blurry',''),[]);assert.deepEqual(m.addedTerms('','blurry'),['blurry']);
    assert.deepEqual(m.addedTerms('a','a, ,\n,  b\t'),['b']);
    // Duplicates in the selected negative stay, as the concept lists every added term in order.
    assert.deepEqual(m.addedTerms('a','c, c, a'),['c','c']);
    // Case and inner whitespace are exact.
    assert.deepEqual(m.addedTerms('Blurry, low res','blurry, Blurry, low  res'),['blurry','low  res']);
    assert.deepEqual(m.addedTerms('same, text','text, same'),[]);
    assert.throws(()=>m.addedTerms(null,'a'),TypeError);assert.throws(()=>m.addedTerms('a',undefined),TypeError);
});
