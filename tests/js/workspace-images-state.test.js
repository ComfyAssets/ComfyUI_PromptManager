const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/images-state.mjs');
const image = (path, root_index = 0) => ({path, root_index, id:'same-hash'});
const page = (images, total = images.length, next_cursor = null) => ({images, total, next_cursor, has_more:next_cursor !== null});
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };

test('stream serializes allowed filters, appends distinct rooted paths, and emits immutable snapshots', async () => {
    const { createImageStream, imageKey } = await source;
    const calls = [], states = [];
    const pages = [page([image('a'),image('a',1)],3,'next'), page([image('a',1),image('b')],3)];
    const stream = createImageStream({api:{get:async (url, options) => { calls.push([url, options]);return pages.shift(); }},onChange:s => states.push(s),batchSize:2});
    assert.equal(await stream.reset({q:'a & b',root:0,media:'image',sort:'oldest',subfolder:'nested/one',ignored:'unsafe'}), 2);
    const query = new URL(calls[0][0],'http://local').searchParams;
    assert.equal(query.get('q'),'a & b'); assert.equal(query.get('root'),'0');
    assert.equal(query.get('subfolder'),'nested/one'); assert.equal(query.get('mode'),'workspace');
    assert.equal(query.get('limit'),'2'); assert.equal(query.has('cursor'),false); assert.equal(query.has('ignored'),false);
    assert.ok(calls[0][1].signal instanceof AbortSignal);
    assert.equal(imageKey(image('a',0)),'0:a'); assert.notEqual(imageKey(image('a',0)), imageKey(image('a',1)));
    const first = stream.state;
    assert.ok(Object.isFrozen(first)); assert.ok(Object.isFrozen(first.items)); assert.ok(Object.isFrozen(first.items[0]));
    const more = stream.loadMore();
    assert.strictEqual(stream.state.items,first.items);
    assert.equal(await more,1);
    assert.equal(new URL(calls[1][0],'http://local').searchParams.get('cursor'),'next');
    assert.deepEqual(stream.state.items.map(imageKey),['0:a','1:a','0:b']);
    assert.equal(stream.state.loaded,3); assert.equal(stream.state.total,3); assert.equal(stream.state.hasMore,false);
    assert.equal(await stream.loadMore(),0); assert.equal(calls.length,2);
    assert.equal(first.items.length,2); assert.equal(states.at(-1),stream.state);
});

test('reset aborts pending requests and ignores stale success and failure', async () => {
    const { createImageStream } = await source;
    const requests=[]; const states=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},onChange:s=>states.push(s)});
    const old=stream.reset({q:'old'}); const fresh=stream.reset({q:'fresh'});
    assert.equal(requests[0].signal.aborted,true);
    requests[1].resolve(page([image('fresh')]));await fresh;
    requests[0].resolve(page([image('old')])); assert.equal(await old,0);
    assert.equal(stream.state.items[0].path,'fresh');
    const failing=stream.reset({q:'failure'}); const newest=stream.reset();
    requests[2].reject(new Error('stale'));requests[3].resolve(page([]));
    await Promise.all([failing,newest]);assert.equal(stream.state.error,null);
    assert.equal(stream.state.loading,false);
});

test('deferred reset cancels stale source work without fetching until discovery succeeds', async () => {
    const {createImageStream}=await source;const requests=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}}});
    const old=stream.reset({root:1,subfolder:'old'});
    const deferredReset=stream.reset({root:'all',q:'png',media:'image',sort:'name'},{load:false});
    assert.equal(requests[0].signal.aborted,true);assert.equal(requests.length,1);
    assert.equal(await deferredReset,0);
    assert.equal(stream.state.items.length,0);assert.equal(stream.state.cursor,null);assert.equal(stream.state.loading,false);
    requests[0].resolve(page([image('old',1)],2,'stale'));assert.equal(await old,0);assert.equal(stream.state.items.length,0);
    const fresh=stream.loadMore();assert.equal(requests.length,2);
    const query=new URL(requests[1].url,'http://local').searchParams;
    assert.equal(query.has('root'),false);assert.equal(query.has('subfolder'),false);assert.equal(query.get('q'),'png');assert.equal(query.get('media'),'image');assert.equal(query.get('sort'),'name');
    requests[1].resolve(page([image('fresh',0)]));assert.equal(await fresh,1);assert.equal(stream.state.items[0].path,'fresh');
    stream.dispose();assert.equal(await stream.reset({}, {load:false}),0);assert.equal(requests.length,2);
});

test('concurrent loads do not duplicate requests, and failures retain data and cursor for retry', async () => {
    const {createImageStream}=await source; const pending=deferred(); const calls=[];
    const responses=[page([image('a')],2,'next'),pending.promise,page([image('b')],2)];
    const stream=createImageStream({api:{get:async url=>{calls.push(url);return responses.shift();}}});
    await stream.reset();const more=stream.loadMore();assert.equal(await stream.loadMore(),0);assert.equal(calls.length,2);
    pending.reject(new Error('offline'));assert.equal(await more,0);
    assert.equal(stream.state.items.length,1);assert.equal(stream.state.cursor,'next');assert.equal(stream.state.error.message,'offline');
    assert.equal(stream.state.hasMore,true);assert.equal(await stream.loadMore(),1);assert.equal(stream.state.error,null);
    assert.equal(calls[1],calls[2]);
});

test('malformed pages fail atomically and allow retry without corrupting the stream', async () => {
    const {createImageStream}=await source;
    for(const invalid of [null,{},page('bad'),page([{}]),page([image('')]),page([image('x',-1)]),page([], -1),{...page([]),has_more:'yes'},page([],2,''),{...page([],2,'next'),has_more:false}]) {
        const replies=[page([image('existing')],2,'next'),invalid,page([image('fixed')],2)];
        const stream=createImageStream({api:{get:async()=>replies.shift()}});await stream.reset();
        await stream.loadMore();assert.match(stream.state.error.message,/image|page|cursor/i);
        assert.equal(stream.state.items[0].path,'existing'); assert.equal(stream.state.cursor,'next');
        await stream.loadMore();assert.equal(stream.state.items.length,2);assert.equal(stream.state.error,null);
    }
});

test('duplicate pages can advance cursors, but repeated cursors fail instead of looping', async () => {
    const {createImageStream}=await source;
    const replies=[page([image('a')],2,'one'),page([image('a')],2,'two'),page([image('b')],2,'two')];
    const stream=createImageStream({api:{get:async()=>replies.shift()}});await stream.reset();
    assert.equal(await stream.loadMore(),0);assert.equal(stream.state.cursor,'two');assert.equal(stream.state.hasMore,true);
    await stream.loadMore();assert.match(stream.state.error.message,/cursor/i);assert.equal(stream.state.items.length,1);
});

test('dispose aborts the active request and suppresses notifications and further requests', async () => {
    const {createImageStream}=await source;const d=deferred();let signal,count=0,notifications=0;
    const stream=createImageStream({api:{get:(url,opts)=>{signal=opts.signal;count++;return d.promise;}},onChange:()=>notifications++});
    const request=stream.reset();stream.dispose();stream.dispose();assert.equal(signal.aborted,true);
    const before=notifications;d.reject(Object.assign(new Error('aborted'),{name:'AbortError'}));await request;
    assert.equal(notifications,before);assert.equal(await stream.reset(),0);assert.equal(await stream.loadMore(),0);assert.equal(count,1);
});

test('stream validates constructor and filter input before requesting', async () => {
    const {createImageStream}=await source;
    assert.throws(()=>createImageStream({api:{}}),/api/i);
    for(const batchSize of [0,-1,1.5,Infinity,201]) assert.throws(()=>createImageStream({api:{get(){}},batchSize}),/batch/i);
    const stream=createImageStream({api:{get:async()=>page([])}});
    for(const filters of [{root:-1},{root:'oops'},{media:'executable'},{sort:'random'},{q:{}},{subfolder:[]}]) await assert.rejects(stream.reset(filters),/filter/i);
    await stream.reset({root:'all',media:'all',sort:'newest'});assert.equal(stream.state.hasMore,false);
});

test('the metadata stream retains earlier images beyond one rendered window and deduplicates within a batch', async () => {
    const {createImageStream}=await source;
    let request=0;
    const stream=createImageStream({api:{get:async()=>{
        const start=request++*40;
        return page(Array.from({length:40},(_,index)=>image(`image-${start+index}`)),320,start+40<320?String(start+40):null);
    }}});
    await stream.reset();
    while(stream.state.hasMore) await stream.loadMore();
    assert.equal(stream.state.loaded,320);assert.equal(stream.state.items[0].path,'image-0');
    assert.equal(stream.state.items.at(-1).path,'image-319');
    const duplicate=createImageStream({api:{get:async()=>page([image('a'),image('a')],1)}});
    assert.equal(await duplicate.reset(),1);assert.equal(duplicate.state.items.length,1);
});

test('all-source defaults are omitted and descending name sort remains supported', async () => {
    const {createImageStream}=await source;let query;
    const stream=createImageStream({api:{get:async url=>{query=new URL(url,'http://local').searchParams;return page([]);}}});
    await stream.reset({root:'all',media:'all',sort:'name-desc'});
    assert.equal(query.has('root'),false);assert.equal(query.has('media'),false);
    assert.equal(query.get('sort'),'name-desc');assert.equal(query.get('limit'),'40');
});

test('restored browsing state resumes its cursor and filters without an initial request', async () => {
    const {createImageStream}=await source;
    const calls=[];
    const cachedItems=[image('existing')];
    const cache={filters:{q:'saved search',root:1,sort:'oldest',media:'image',subfolder:'nested'},state:{items:cachedItems,total:3,cursor:'saved-cursor',hasMore:true,loaded:1,loading:true,error:new Error('old error')}};
    const stream=createImageStream({api:{get:async url=>{calls.push(url);return page([image('existing'),image('new')],3,'next');}},restore:cache});
    assert.equal(calls.length,0);assert.equal(stream.state.items.length,1);
    assert.equal(stream.state.loading,false);assert.equal(stream.state.error,null);
    assert.ok(Object.isFrozen(stream.state));assert.ok(Object.isFrozen(stream.state.items));assert.ok(Object.isFrozen(stream.state.items[0]));
    cachedItems[0].path='external mutation';cachedItems.push(image('external'));
    assert.equal(stream.state.items[0].path,'existing');assert.equal(stream.state.items.length,1);
    assert.equal(await stream.loadMore(),1);
    const query=new URL(calls[0],'http://local').searchParams;
    for(const [key,value] of Object.entries({q:'saved search',root:'1',sort:'oldest',media:'image',subfolder:'nested',cursor:'saved-cursor'})) assert.equal(query.get(key),value);
    assert.deepEqual(stream.state.items.map(item=>item.path),['existing','new']);
    await stream.reset({q:'new search'});
    assert.equal(new URL(calls[1],'http://local').searchParams.has('cursor'),false);
    assert.equal(new URL(calls[1],'http://local').searchParams.get('q'),'new search');
    stream.dispose();assert.equal(await stream.loadMore(),0);
});

test('restoring frozen state reuses immutable metadata and respects a completed collection', async () => {
    const {createImageStream}=await source;
    const first=createImageStream({api:{get:async()=>page([image('saved')])}});
    await first.reset();let requests=0;
    const restored=createImageStream({api:{get:async()=>{requests++;return page([]);}},restore:{filters:{},state:first.state}});
    assert.strictEqual(restored.state.items,first.state.items);assert.equal(restored.state.loaded,1);
    assert.equal(await restored.loadMore(),0);assert.equal(requests,0);
});

test('restored interrupted initial loading can restart from the first request',async()=>{
    const {createImageStream}=await source;let called=0;
    const stream=createImageStream({api:{get:async url=>{called++;assert.equal(new URL(url,'http://local').searchParams.has('cursor'),false);return page([]);}},
        restore:{filters:{},state:{items:[],total:0,cursor:null,hasMore:true,loading:true}}});
    assert.equal(called,0);assert.equal(await stream.loadMore(),0);assert.equal(called,1);assert.equal(stream.state.hasMore,false);
});

test('malformed restored state is rejected before making requests',async()=>{
    const {createImageStream}=await source;
    const base={items:[image('a')],total:2,cursor:'saved',hasMore:true};
    const invalid=[null,{}, {filters:{},state:null},
        ...[{items:'bad'},{items:[{}]},{items:[image('a'),image('a')]},{total:-1},{cursor:''},{cursor:null},{hasMore:'yes'},{hasMore:false}].map(change=>({filters:{},state:{...base,...change}})),
        {filters:{root:-1},state:base}, {filters:null,state:base}];
    for(const restore of invalid) assert.throws(()=>createImageStream({api:{get(){assert.fail('unexpected request');}},restore}),TypeError);
});

test('custom collection pages reuse image-stream cancellation, cursors and identity checks',async()=>{
    const {createImageStream}=await source;const calls=[];
    const stream=createImageStream({fetchPage:async({parameters,signal})=>{calls.push({parameters,signal});return calls.length===1?page([image('linked.png')],2,'next'):page([image('other.png')],2);},batchSize:1});
    assert.equal(await stream.reset(),1);assert.equal(calls[0].parameters.get('limit'),'1');assert.ok(calls[0].signal instanceof AbortSignal);
    assert.equal(await stream.loadMore(),1);assert.equal(calls[1].parameters.get('cursor'),'next');assert.equal(stream.state.loaded,2);stream.dispose();
    assert.throws(()=>createImageStream({api:{get:async()=>{}},fetchPage:'invalid'}),/page/i);
});

test('stream sends category, tags and the rating sort and validates them', async () => {
    const {createImageStream}=await source;const calls=[];
    const stream=createImageStream({api:{get:async url=>{calls.push(new URL(url,'http://local').searchParams);return page([]);}}});
    await stream.reset({q:'cat',category:'Animals',tags:['cats','studio'],sort:'rating'});
    assert.equal(calls[0].get('category'),'Animals');assert.equal(calls[0].get('tags'),'cats,studio');assert.equal(calls[0].get('sort'),'rating');
    await stream.reset({category:'',tags:[]});
    assert.equal(calls[1].has('category'),false);assert.equal(calls[1].has('tags'),false);
    for(const filters of [{category:3},{tags:'cats'},{tags:[1]},{tags:['a,b']},{tags:['']}]) await assert.rejects(stream.reset(filters),/filter/i);
});

test('positive count comes from the first page, survives plain cursor pages and hides when unknown', async () => {
    const {createImageStream}=await source;
    const replies=[{...page([image('a')],3,'one'),positive_count:2},page([image('b')],3,'two'),{...page([image('c')],3),positive_count:null}];
    const stream=createImageStream({api:{get:async()=>replies.shift()}});
    assert.equal(stream.state.positiveCount,null);
    await stream.reset();assert.equal(stream.state.positiveCount,2);
    await stream.loadMore();assert.equal(stream.state.positiveCount,2);
    await stream.loadMore();assert.equal(stream.state.positiveCount,2);
    const failed=createImageStream({api:{get:async()=>({...page([image('a')]),positive_count:null})}});
    await failed.reset();assert.equal(failed.state.positiveCount,null);
    const absent=createImageStream({api:{get:async()=>page([image('a')])}});
    await absent.reset();assert.equal(absent.state.positiveCount,null);
    for(const positive_count of [-1,'2',1.5]) {
        const invalid=createImageStream({api:{get:async()=>({...page([image('a')]),positive_count})}});
        await invalid.reset();assert.match(invalid.state.error.message,/page/i);
    }
    const restored=createImageStream({api:{get(){assert.fail('unexpected request');}},restore:{filters:{},state:{...stream.state}}});
    assert.equal(restored.state.positiveCount,2);
    assert.throws(()=>createImageStream({api:{get(){}},restore:{filters:{},state:{...stream.state,positiveCount:-3}}}),TypeError);
});

test('the count line reads N of M images and K positive prompts only when known', async () => {
    const {countLine}=await source;
    assert.equal(countLine({loaded:40,total:102,positiveCount:2}),'40 of 102 images · 2 positive prompts');
    assert.equal(countLine({loaded:1,total:1,positiveCount:1}),'1 of 1 image · 1 positive prompt');
    assert.equal(countLine({loaded:2,total:2,positiveCount:null}),'2 of 2 images');
    assert.equal(countLine({loaded:1200,total:3400,positiveCount:0}),'1,200 of 3,400 images · 0 positive prompts');
});

test('the caption date names the newest result day under Newest first only', async () => {
    const {captionDate}=await source;
    const now=new Date(2026,9,17,15,0);
    const at=(...parts)=>({modified_time:new Date(...parts).getTime()/1000});
    assert.equal(captionDate([at(2026,9,17,9),at(2026,9,10)],'newest',now),'Today · October 17');
    assert.equal(captionDate([at(2026,9,16,23),at(2026,9,10)],'newest',now),'Yesterday · October 16');
    // D17 ruling: only under Newest first; Oldest first hides the date too.
    assert.equal(captionDate([at(2026,9,10),at(2026,9,16,23)],'oldest',now),'');
    assert.equal(captionDate([at(2026,9,13)],'newest',now),'Tuesday · October 13');
    assert.equal(captionDate([at(2025,2,3)],'newest',now),'Monday · March 3, 2025');
    for(const sort of ['name','name-desc','rating']) assert.equal(captionDate([at(2026,9,17)],sort,now),'');
    assert.equal(captionDate([],'newest',now),'');
    assert.equal(captionDate([{modified_time:'bad'}],'newest',now),'');
});

test('the scope chip summarizes source and folder', async () => {
    const {scopeLabel}=await source;
    const roots=[{index:0,label:'output'},{index:1,label:'input'}];
    assert.equal(scopeLabel({root:'all',subfolder:''},roots),'All output folders');
    assert.equal(scopeLabel({root:'1',subfolder:'shared'},roots),'input · shared');
    assert.equal(scopeLabel({root:'0',subfolder:''},roots),'output · All folders');
    assert.equal(scopeLabel({root:'all',subfolder:'bulk'},roots),'All sources · bulk');
    assert.equal(scopeLabel({root:'7',subfolder:''},roots),'Source 7 · All folders');
});
