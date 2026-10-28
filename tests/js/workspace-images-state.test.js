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
    assert.equal(query.has('favorites'),false);
});

test('the Favorites page asks the listing for favorites only (#249 D51)', async () => {
    const {createImageStream}=await source;let query;
    const stream=createImageStream({api:{get:async url=>{query=new URL(url,'http://local').searchParams;return page([]);}}});
    await stream.reset({favorites:true,q:'fog'});
    assert.equal(query.get('favorites'),'1');assert.equal(query.get('q'),'fog');
    await stream.reset({favorites:false});
    assert.equal(query.has('favorites'),false);
    for(const favorites of ['1',1,'true'])await assert.rejects(stream.reset({favorites}),/Invalid favorites filter/);
});

test('a collection page asks the listing for that collection only (#331 D137)', async () => {
    const {createImageStream}=await source;let query;
    const stream=createImageStream({api:{get:async url=>{query=new URL(url,'http://local').searchParams;return page([]);}}});
    await stream.reset({collection:'12',q:'fog'});
    assert.equal(query.get('collection'),'12');assert.equal(query.get('q'),'fog');assert.equal(query.has('favorites'),false);
    await stream.reset({collection:7});
    assert.equal(query.get('collection'),'7');
    for(const collection of ['',null,undefined]) {
        await stream.reset({collection});
        assert.equal(query.has('collection'),false);
    }
    for(const collection of ['0','07','x',-1,1.5,'1234567890',true])await assert.rejects(stream.reset({collection}),/Invalid collection filter/);
    // Favorites and a collection are never both a page's filter.
    await assert.rejects(stream.reset({collection:'3',favorites:true}),/Invalid collection filter/);
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

test('the count line reads as loading, never as an empty result, until a first page answers (#391)', async () => {
    const {countLine,createImageStream}=await source;
    assert.equal(countLine({loaded:0,total:0,positiveCount:null,loading:true,error:null}),'Loading images…');
    // A failed first page and a settled empty one say what the server said; a later page keeps the loaded count.
    assert.equal(countLine({loaded:0,total:0,positiveCount:null,loading:false,error:new Error('x')}),'0 of 0 images');
    assert.equal(countLine({loaded:0,total:0,positiveCount:0,loading:false,error:null}),'0 of 0 images · 0 positive prompts');
    assert.equal(countLine({loaded:40,total:102,positiveCount:2,loading:true,error:null}),'40 of 102 images · 2 positive prompts');
    // While a reset's first page is in flight the line reads as loading (the cleared state before it is replaced in
    // the same task, so it is never painted).
    let answer;const lines=[];
    const stream=createImageStream({api:{get:()=>new Promise(resolve=>{answer=resolve;})},onChange:state=>lines.push(countLine(state))});
    const loading=stream.reset({tags:['cinematic']});
    assert.equal(lines.at(-1),'Loading images…');
    answer({images:[{path:'a.png',root_index:0}],total:1,has_more:false,next_cursor:null,positive_count:1});await loading;
    assert.equal(lines.at(-1),'1 of 1 image · 1 positive prompt');
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
    assert.equal(scopeLabel({root:'0',subfolder:''},roots),'output · All output folders');
    assert.equal(scopeLabel({root:'all',subfolder:'bulk'},roots),'All sources · bulk');
    assert.equal(scopeLabel({root:'7',subfolder:''},roots),'Source 7 · All output folders');
});

test('the Filters count names each active Refine filter once (D44)', async () => {
    const {activeRefinements}=await source;
    assert.equal(activeRefinements({root:'all',subfolder:'',media:'all',tags:[]}),0);
    assert.equal(activeRefinements({root:'',subfolder:'',media:'all',tags:[]}),0);
    assert.equal(activeRefinements({root:'1',subfolder:'',media:'all',tags:[]}),1);
    assert.equal(activeRefinements({root:'all',subfolder:'bulk',media:'video',tags:['a','b']}),3);
    assert.equal(activeRefinements({root:'0',subfolder:'bulk',media:'image',tags:['a']}),4);
});

test('the card title is the confirmed linked title, else the filename (D10)', async () => {
    const {cardTitle}=await source;
    const linked=(status,title)=>({filename:'alpine_00001.png',linked:{status,positive_id:1,title,title_source:'title',category:null,rating:null}});
    assert.equal(cardTitle(linked('linked','Alpine observatory')),'Alpine observatory');
    for (const status of ['ambiguous','unlinked','unavailable','none']) assert.equal(cardTitle(linked(status,'Alpine observatory')),'alpine_00001.png');
    assert.equal(cardTitle(linked('linked',null)),'alpine_00001.png');
    assert.equal(cardTitle(linked('linked','')),'alpine_00001.png');
    assert.equal(cardTitle({filename:'plain.png'}),'plain.png');
    assert.equal(cardTitle({filename:'plain.png',linked:null}),'plain.png');
});

test('the card subtitle is W × H · EXT · Seed N with unknown parts omitted (D11)', async () => {
    const {cardSubtitle, mediaRatio}=await source;
    assert.equal(cardSubtitle({filename:'a.png',width:1536,height:1152,seed:'42001'}),'1536 × 1152 · PNG · Seed 42001');
    // The seed is the listing's decimal string: never a Number, so 2^53 + 1 survives.
    assert.equal(cardSubtitle({filename:'a.png',width:8,height:8,seed:'9007199254740993'}),'8 × 8 · PNG · Seed 9007199254740993');
    assert.equal(cardSubtitle({filename:'clip.webm',width:null,height:null,seed:null}),'WEBM');
    assert.equal(cardSubtitle({filename:'clip.mp4',media_type:'video',width:640,height:360}),'640 × 360 · MP4');
    assert.equal(cardSubtitle({filename:'noext',width:0,height:10,seed:''}),'');
    assert.equal(cardSubtitle({filename:'.hidden',width:10,height:Infinity,seed:42}),'');
    assert.equal(cardSubtitle({filename:'trailing.',seed:'7'}),'Seed 7');
    assert.equal(mediaRatio({width:1536,height:1152}),'1536/1152');
    for (const image of [{}, {width:0,height:5}, {width:5,height:NaN}, {width:Infinity,height:5}]) assert.equal(mediaRatio(image),'4/3');
});

test('updating one item replaces it immutably and later pages append after it', async () => {
    const { createImageStream, imageKey } = await source;
    const pages = [page([{...image('a'), favorite:false}, {...image('b'), favorite:true}], 3, 'next'), page([image('c')], 3)];
    const states = [];
    const hints = [];
    const stream = createImageStream({api:{get:async () => pages.shift()}, onChange:(s, hint) => { states.push(s); hints.push(hint); }, batchSize:2});
    await stream.reset({});
    const before = stream.state, notified = states.length;
    // Page loads carry no hint; a patch says so, so the page can repaint one card instead of laying out the collection.
    assert.ok(hints.every(hint => hint === undefined));
    assert.equal(stream.updateItem('0:a', {favorite:true}), true);
    const after = stream.state;
    assert.equal(states.length, notified + 1); assert.equal(states.at(-1), after);
    assert.deepEqual(hints.at(-1), {patch:'0:a'});
    assert.notEqual(after.items, before.items); assert.ok(Object.isFrozen(after.items)); assert.ok(Object.isFrozen(after.items[0]));
    assert.equal(after.items[0].favorite, true); assert.equal(before.items[0].favorite, false);
    assert.equal(after.items[1], before.items[1]);
    assert.equal(after.loaded, before.loaded); assert.equal(after.cursor, before.cursor); assert.equal(after.total, before.total);
    // Identity fields cannot be patched, and an unknown key changes nothing.
    assert.throws(() => stream.updateItem('0:a', {path:'z'}), TypeError);
    assert.throws(() => stream.updateItem('0:a', {root_index:1}), TypeError);
    assert.equal(stream.updateItem('0:missing', {favorite:true}), false);
    assert.equal(stream.state, after); assert.equal(states.length, notified + 1);
    await stream.loadMore();
    assert.deepEqual(stream.state.items.map(imageKey), ['0:a', '0:b', '0:c']);
    assert.equal(stream.state.items[0].favorite, true);
    stream.dispose();
    assert.equal(stream.updateItem('0:b', {favorite:false}), false);
    assert.equal(stream.state.items[1].favorite, true);
});

test('withItem patches a saved browsing state without touching the other items', async () => {
    const { withItem } = await source;
    const items = Object.freeze([Object.freeze({...image('a'), favorite:false}), Object.freeze({...image('b'), favorite:false})]);
    const state = Object.freeze({items, total:2, cursor:null, hasMore:false, loading:false, error:null, loaded:2, positiveCount:null});
    const next = withItem(state, '0:b', {favorite:true});
    assert.ok(Object.isFrozen(next)); assert.equal(next.items[1].favorite, true); assert.equal(next.items[0], items[0]);
    assert.equal(state.items[1].favorite, false);
    assert.equal(withItem(state, '1:b', {favorite:true}), state);
});

test('toggleSelection returns a new frozen set and leaves the given one untouched', async () => {
    const { toggleSelection } = await source;
    const empty = Object.freeze(new Set());
    const one = toggleSelection(empty, '0:a', true);
    assert.ok(Object.isFrozen(one)); assert.deepEqual([...one], ['0:a']); assert.equal(empty.size, 0);
    const two = toggleSelection(one, '1:a', true);
    assert.deepEqual([...two], ['0:a', '1:a']); assert.deepEqual([...one], ['0:a']);
    assert.equal(toggleSelection(two, '0:a', true), two, 'an already selected key keeps the same set');
    assert.equal(toggleSelection(one, '9:z', false), one, 'an unselected key keeps the same set');
    assert.deepEqual([...toggleSelection(two, '0:a', false)], ['1:a']);
});

test('selectionLabel counts the selected images as the concept bar does', async () => {
    const { selectionLabel } = await source;
    assert.equal(selectionLabel(1), '1 selected');
    assert.equal(selectionLabel(2), '2 selected');
    assert.equal(selectionLabel(1234), '1,234 selected');
});

test('refresh re-reads the loaded range in place and publishes once, replacing the items', async () => {
    const {createImageStream,imageKey}=await source;const requests=[];const states=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},onChange:(s,hint)=>states.push([s,hint]),batchSize:2});
    assert.equal(typeof stream.refresh,'function','refresh is part of the stream');
    const first=stream.reset({q:'tag'});requests[0].resolve(page([image('a'),image('b')],5,'c1'));await first;
    const second=stream.loadMore();requests[1].resolve(page([image('c'),image('d')],5,'c2'));await second;
    const before=stream.state;const published=states.length;
    const refresh=stream.refresh();
    // The current collection stays shown while the re-read runs; Load more waits for it.
    assert.equal(stream.state,before);assert.equal(states.length,published);
    assert.equal(await stream.loadMore(),0);assert.equal(requests.length,3);
    const query=new URL(requests[2].url,'http://local').searchParams;
    // The loaded range is re-read in pages as large as it (at most 200), not in the listing's batches (M2).
    assert.equal(query.get('q'),'tag');assert.equal(query.has('cursor'),false);assert.equal(query.get('limit'),'4');
    requests[2].resolve(page([image('a'),image('new'),image('c')],6,'n1'));
    await new Promise(resolve=>setImmediate(resolve));
    const next=new URL(requests[3].url,'http://local').searchParams;
    assert.equal(next.get('cursor'),'n1');assert.equal(next.get('limit'),'2','the rest, at least one batch');
    assert.equal(stream.state,before);
    requests[3].resolve(page([image('d')],6,'n2'));
    assert.equal(await refresh,4);
    assert.deepEqual(stream.state.items.map(imageKey),['0:a','0:new','0:c','0:d']);
    assert.deepEqual([stream.state.total,stream.state.cursor,stream.state.hasMore,stream.state.loaded,stream.state.loading],[6,'n2',true,4,false]);
    assert.equal(states.length,published+1);assert.deepEqual(states.at(-1)[1],{replaced:true});
    // Load more continues from the refreshed cursor.
    const more=stream.loadMore();assert.equal(new URL(requests[4].url,'http://local').searchParams.get('cursor'),'n2');
    requests[4].resolve(page([image('e')],6));await more;
    assert.deepEqual(stream.state.items.map(imageKey),['0:a','0:new','0:c','0:d','0:e']);
});

test('refresh stops early when the result shrank, and a reset or failure leaves the collection as it was', async () => {
    const {createImageStream,imageKey}=await source;const requests=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},batchSize:2});
    assert.equal(typeof stream.refresh,'function','refresh is part of the stream');
    const first=stream.reset({tags:['x']});requests[0].resolve(page([image('a'),image('b')],4,'c1'));await first;
    const shrink=stream.refresh();requests[1].resolve(page([image('b')],1));
    assert.equal(await shrink,1);assert.deepEqual(stream.state.items.map(imageKey),['0:b']);assert.equal(stream.state.hasMore,false);
    const before=stream.state;
    const failing=stream.refresh();requests[2].reject(new Error('offline'));
    await assert.rejects(failing,/offline/,'a failed re-read is reported to the page');assert.equal(stream.state,before);
    const stale=stream.refresh();const fresh=stream.reset({tags:['y']});
    assert.equal(requests[3].signal.aborted,true);
    requests[4].resolve(page([image('y')]));await fresh;
    requests[3].resolve(page([image('stale')]));assert.equal(await stale,0);
    assert.deepEqual(stream.state.items.map(imageKey),['0:y']);
    stream.dispose();assert.equal(await stream.refresh(),0);
});

test('a refresh asked while a page loads runs once that page settles (I6)', async () => {
    const {createImageStream,imageKey}=await source;const requests=[];const states=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},onChange:(s,hint)=>states.push(hint),batchSize:2});
    const first=stream.reset({tags:['x']});requests[0].resolve(page([image('a'),image('b')],4,'c1'));await first;
    for (const settle of ['resolve','reject']) {
        const more=stream.loadMore();const sent=requests.length;
        assert.equal(await stream.refresh(),0,'nothing is read beside the page in flight');
        assert.equal(await stream.refresh(),0);
        assert.equal(requests.length,sent);
        if (settle==='resolve') requests[sent-1].resolve(page([image('c'),image('d')],4,'c2'));
        else requests[sent-1].reject(new Error('offline'));
        await more;
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(requests.length,sent+1,'exactly one pending refresh follows');
        assert.equal(new URL(requests[sent].url,'http://local').searchParams.has('cursor'),false);
        requests[sent].resolve(page([image('a'),image('b'),image('c'),image('d')],6,'r1'));
        await new Promise(resolve=>setImmediate(resolve));
        assert.deepEqual(states.at(-1),{replaced:true});
        assert.equal(states.filter(hint=>hint?.replaced).length,settle==='resolve'?1:2);
    }
    // A reset drops a pending refresh.
    const more=stream.loadMore();const sent=requests.length;
    assert.equal(await stream.refresh(),0);
    const fresh=stream.reset({tags:['y']});
    requests[sent].resolve(page([image('y')]));await fresh;
    requests[sent-1].resolve(page([image('late')],1));await more;
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests.length,sent+1);assert.deepEqual(stream.state.items.map(imageKey),['0:y']);
    assert.equal(stream.refreshing,false);
});

test('refresh reads a long loaded range in at most 200-file pages and exposes refreshing (M2, M3)', async () => {
    const {createImageStream}=await source;const requests=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},batchSize:150});
    const many=(prefix,n)=>Array.from({length:n},(_,i)=>image(`${prefix}${i}`));
    const first=stream.reset({q:'x'});requests[0].resolve(page(many('a',150),1000,'c1'));await first;
    const second=stream.loadMore();requests[1].resolve(page(many('b',150),1000,'c2'));await second;
    const refresh=stream.refresh();
    assert.equal(stream.refreshing,true);
    assert.equal(new URL(requests[2].url,'http://local').searchParams.get('limit'),'200');
    requests[2].resolve(page(many('r',200),1000,'n1'));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(new URL(requests[3].url,'http://local').searchParams.get('limit'),'150');
    requests[3].resolve(page(many('s',150),1000,'n2'));
    assert.equal(await refresh,350);assert.equal(requests.length,4);
    assert.equal(stream.refreshing,false);
});

test('a second refresh supersedes the first, repeats in a re-read are dropped, and dispose abandons one in flight', async () => {
    const {createImageStream,imageKey}=await source;const requests=[];const states=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},onChange:(s,hint)=>states.push(hint),batchSize:2});
    const first=stream.reset({q:'x'});requests[0].resolve(page([image('a'),image('b')],2));await first;
    const older=stream.refresh();const newer=stream.refresh();
    assert.equal(requests[1].signal.aborted,true,'the older re-read is aborted');
    requests[2].resolve(page([image('b'),image('b'),image('a')],2));
    assert.equal(await newer,2);
    requests[1].resolve(page([image('old')],1));assert.equal(await older,0);
    assert.deepEqual(stream.state.items.map(imageKey),['0:b','0:a']);
    assert.equal(states.filter(hint=>hint?.replaced).length,1);
    const pending=stream.refresh();
    stream.dispose();
    assert.equal(requests[3].signal.aborted,true);
    requests[3].resolve(page([image('late')],1));
    assert.equal(await pending,0);assert.deepEqual(stream.state.items.map(imageKey),['0:b','0:a']);
});

test('a refresh deferred behind a page load is dropped when the stream is disposed meanwhile', async () => {
    const {createImageStream}=await source;const requests=[];
    const stream=createImageStream({api:{get:(url,{signal})=>{const d=deferred();requests.push({...d,url,signal});return d.promise;}},batchSize:2});
    const first=stream.reset({q:'x'});requests[0].resolve(page([image('a'),image('b')],4,'c1'));await first;
    const more=stream.loadMore();
    assert.equal(await stream.refresh(),0);
    stream.dispose();
    requests[1].resolve(page([image('c')],4,'c2'));await more;
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests.length,2,'no refresh runs after dispose');
});

test('defaults apply to a new visit but not to the current view or a saved view', async () => {
    const { initialImagesView } = await source;
    assert.equal(typeof initialImagesView, 'function', 'initialImagesView is exported');
    const preferences = { workspaceLayout: 'masonry', workspaceDensity: 'compact' };
    // A new visit starts from System › Appearance.
    assert.deepEqual(initialImagesView({ preferences }), { layout: 'masonry', density: 'compact' });
    // The current view (the last visit) keeps its own toolbar choices.
    assert.deepEqual(initialImagesView({ prior: { layout: 'list', density: 'large' }, preferences }), { layout: 'list', density: 'large' });
    assert.deepEqual(initialImagesView({ prior: {}, preferences }), { layout: 'grid', density: 'comfortable' });
    // A saved view opens as it always did.
    assert.deepEqual(initialImagesView({ saved: { q: '' }, preferences }), { layout: 'grid', density: 'comfortable' });
    // Without readable preferences nothing changes.
    assert.deepEqual(initialImagesView({}), { layout: 'grid', density: 'comfortable' });
    assert.deepEqual(initialImagesView({ preferences: {} }), { layout: 'grid', density: 'comfortable' });
});

test('listingFilter is the listing query as an object, without paging (#392)', async () => {
    const { listingFilter } = await source;
    assert.deepEqual(listingFilter({q:'cat', root:'1', subfolder:'a/b', category:'Animals', tags:[' x ','y'], media:'video', sort:'oldest', favorites:true}),
        {q:'cat', root:'1', subfolder:'a/b', category:'Animals', tags:'x,y', media:'video', sort:'oldest', favorites:'1'});
    assert.deepEqual(listingFilter({root:'all', q:'', tags:[], media:'all'}), {sort:'newest'});
    // #331: "Select all N matching" on a collection page acts on that collection's listing.
    assert.deepEqual(listingFilter({collection:'4', q:'cat'}), {q:'cat', sort:'newest', collection:'4'});
    assert.throws(() => listingFilter({sort:'sideways'}), TypeError);
});
