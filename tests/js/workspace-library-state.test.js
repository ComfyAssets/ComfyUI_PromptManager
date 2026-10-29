const test=require('node:test');
const assert=require('node:assert/strict');
const source=import('../../web/js/workspace/library-state.mjs');
const item=id=>({id,text:`Positive ${id}`,category:'Art',tags:['blue'],rating:3,variant_count:2,image_count:4,previews:[]});
const page=(items,cursor=null)=>({items,total:3,variant_total:6,has_more:cursor!==null,next_cursor:cursor});
test('parent stream builds literal filters and deduplicates stable identities across infinite pages',async()=>{
 const {createLibraryStream}=await source;const calls=[];const pending=[page([item(1),item(2)],'next'),page([item(2),item(3)])];const s=createLibraryStream({api:{get:async(url)=>{calls.push(url);return pending.shift();}}});
 assert.equal(await s.reset({text:'a%_',category:'Art',tags:['blue','red'],sort:'rating'}),2);assert.equal(await s.loadMore(),1);assert.deepEqual(s.state.items.map(row=>row.id),[1,2,3]);assert.equal(s.state.hasMore,false);
 const query=new URL(calls[0],'https://x').searchParams;assert.equal(query.get('text'),'a%_');assert.deepEqual(query.getAll('tag'),['blue','red']);assert.equal(query.get('sort'),'rating');assert.equal(new URL(calls[1],'https://x').searchParams.get('cursor'),'next');assert.equal(await s.loadMore(),0);s.dispose();
});
test('the shelf caption total of negative variants comes from variant_total and resets with the filters',async()=>{
 const {createLibraryStream}=await source;const pending=[page([item(1)],'next'),{...page([item(2)]),variant_total:7},{...page([item(3)]),variant_total:1}];
 const s=createLibraryStream({api:{get:async()=>pending.shift()}});assert.equal(s.state.variantTotal,null);
 await s.reset();assert.equal(s.state.variantTotal,6);await s.loadMore();assert.equal(s.state.variantTotal,7);
 const reset=s.reset({text:'x'});assert.equal(s.state.variantTotal,null);await reset;assert.equal(s.state.variantTotal,1);
 const restored=createLibraryStream({api:{},restore:{filters:{},state:s.state}});assert.equal(restored.state.variantTotal,1);restored.dispose();
 for(const bad of [undefined,-1,1.5,'2']){const broken=createLibraryStream({api:{get:async()=>({...page([item(1)]),variant_total:bad})}});await broken.reset();assert.ok(broken.state.error);assert.equal(broken.state.variantTotal,null);broken.dispose();}
 s.dispose();
});
test('reset aborts older requests and late success/failure cannot replace current parents',async()=>{
 const {createLibraryStream}=await source;const requests=[];const s=createLibraryStream({api:{get:(url,{signal})=>new Promise((resolve,reject)=>requests.push({signal,resolve,reject}))}});
 const first=s.reset({text:'first'});const second=s.reset({text:'second'});assert.equal(requests[0].signal.aborted,true);requests[1].resolve(page([item(2)]));await second;requests[0].resolve(page([item(1)]));await first;assert.deepEqual(s.state.items.map(row=>row.id),[2]);
 const third=s.reset();s.dispose();requests[2].reject(Error('late'));await third;assert.equal(await s.loadMore(),0);
});
test('failed pages retain cursor/data and can retry while duplicate load is guarded',async()=>{
 const {createLibraryStream}=await source;let calls=0,release;const s=createLibraryStream({api:{get:async()=>{calls++;if(calls===1)return page([item(1)],'next');if(calls===2)throw Error('offline');return new Promise(resolve=>release=resolve);}}});
 await s.reset();await s.loadMore();assert.equal(s.state.cursor,'next');assert.equal(s.state.items.length,1);assert.match(s.state.error.message,/offline/);const request=s.loadMore();assert.equal(await s.loadMore(),0);release(page([item(2)]));await request;assert.equal(s.state.error,null);assert.equal(s.state.items.length,2);s.dispose();
});
test('malformed/nonprogressing pages fail atomically and invalid filters never send requests',async()=>{
 const {createLibraryStream}=await source;
 for(const malformed of [{},page([{...item(1),id:-1}]),{...page([]),has_more:true},page([{...item(1),text:null}])]){const s=createLibraryStream({api:{get:async()=>malformed}});await s.reset();assert.ok(s.state.error);assert.equal(s.state.items.length,0);s.dispose();}
 const s=createLibraryStream({api:{get:async()=>page([],'same')}});await s.reset();await s.loadMore();assert.ok(s.state.error);s.dispose();
 const invalid=createLibraryStream({api:{get:()=>assert.fail()}});for(const filters of [{sort:'bad'},{text:2},{tags:'a'}])await assert.rejects(invalid.reset(filters));invalid.dispose();
});
test('library detail selection preserves full strings and distinguishes all, empty and unknown',async()=>{
 const {libraryPromptRoles}=await source;const detail={positive:item(1),variants:[{id:9,negative_text:'',explicit_empty:true},{id:10,negative_text:'long\nnegative',explicit_empty:false}]};
 const all=libraryPromptRoles(detail,null);assert.equal(all.positive.text,'Positive 1');assert.equal(all.negative.status,'unresolved');assert.equal(libraryPromptRoles(detail,9).negative.text,'');assert.equal(libraryPromptRoles(detail,10).negative.text,'long\nnegative');assert.throws(()=>libraryPromptRoles(detail,999));
});
test('detail reads validate identities, abort stale selection and expose retryable errors',async()=>{
 const {createLibraryDetail}=await source;const pending=[],states=[];const loader=createLibraryDetail({api:{get:(url,{signal})=>new Promise((resolve,reject)=>pending.push({url,signal,resolve,reject}))},onChange:state=>states.push(state)});
 const first=loader.load(1);const second=loader.load(2);assert.equal(pending[0].signal.aborted,true);pending[1].resolve({positive:item(2),variants:[],unassigned_image_count:0});await second;pending[0].resolve({positive:item(1),variants:[]});await first;assert.equal(states.at(-1).detail.positive.id,2);
 const third=loader.load(3);pending[2].resolve({positive:item(4),variants:[]});await third;assert.ok(states.at(-1).error);
 const fourth=loader.load(3);loader.dispose();const count=states.length;pending[3].reject(Error('late'));await fourth;assert.equal(states.length,count);await loader.load(5);assert.equal(pending.length,4);
});
test('restored shelves keep filters, loaded parents and continuation without a fresh first page',async()=>{
 const {createLibraryStream}=await source;const old=createLibraryStream({api:{get:async()=>page([item(1)],'next')}});await old.reset({text:'retained',sort:'name'});old.dispose();const urls=[];
 const next=createLibraryStream({api:{get:async url=>{urls.push(url);return page([item(2)]);}},restore:{filters:{text:'retained',sort:'name'},state:old.state}});assert.equal(next.state.items.length,1);await next.loadMore();assert.match(urls[0],/text=retained/);assert.match(urls[0],/cursor=next/);assert.equal(next.state.items.length,2);next.dispose();
 assert.throws(()=>createLibraryStream({api:{},restore:{filters:{},state:{items:[item(1),item(1)],total:2,variantTotal:0,cursor:null,hasMore:false}}}));
});
test('a shelf left before its first page answered or after it failed restores and reads page one',async()=>{
 const {createLibraryStream}=await source;
 // The shelf saves whatever state it holds when the route changes; a first page still pending or failed has no variant total yet.
 let pending;const left=createLibraryStream({api:{get:()=>new Promise(resolve=>{pending=resolve;})}});const first=left.reset({text:'kept'});
 assert.equal(left.state.loading,true);assert.equal(left.state.variantTotal,null);const saved=left.state;left.dispose();pending(page([item(9)]));await first;
 const failed=createLibraryStream({api:{get:async()=>{throw Error('offline');}}});await failed.reset();assert.ok(failed.state.error);failed.dispose();
 for(const state of [saved,failed.state]) {
  const urls=[];const next=createLibraryStream({api:{get:async url=>{urls.push(url);return page([item(1)]);}},restore:{filters:{text:'kept'},state}});
  assert.deepEqual([next.state.items.length,next.state.loading,next.state.error,next.state.variantTotal,next.state.hasMore],[0,false,null,null,true]);
  assert.equal(await next.loadMore(),1);assert.equal(new URL(urls[0],'https://x').searchParams.get('text'),'kept');assert.equal(new URL(urls[0],'https://x').searchParams.has('cursor'),false);
  assert.equal(next.state.variantTotal,6);next.dispose();
 }
 // A pending first page is only the empty, cursorless state: anything else without a variant total stays invalid.
 for(const state of [{items:[item(1)],total:1,variantTotal:null,cursor:null,hasMore:false},{items:[],total:0,variantTotal:null,cursor:'next',hasMore:true},{items:[],total:3,variantTotal:null,cursor:null,hasMore:true},{items:[],total:0,variantTotal:null,cursor:null,hasMore:false}])
  assert.throws(()=>createLibraryStream({api:{},restore:{filters:{},state}}),/Invalid library page/);
});
test('parent shelf window bounds DOM and pins focused rows without changing logical order',async()=>{
 const {libraryWindow}=await source;const first=libraryWindow(500,2,0,900);assert.equal(first[0],0);assert.ok(first.length<24);
 const middle=libraryWindow(500,2,50000,900,0);assert.equal(middle[0],0);assert.ok(middle.some(index=>index>150));assert.ok(middle.length<24);assert.deepEqual(middle,[...middle].sort((a,b)=>a-b));assert.deepEqual(libraryWindow(3,2,0,900),[0,1,2]);
});
test('uniform compatibility window handles huge counts without collection allocation',async()=>{
 const {libraryWindow}=await source;const original=Array.from;const lengths=[];
 Array.from=function(value,...args){lengths.push(value.length);assert.ok(!Number.isFinite(value.length)||value.length<32,'collection-sized allocation');return original.call(this,value,...args);};
 try {const indexes=libraryWindow(Number.MAX_SAFE_INTEGER,2,50000,900,0);assert.ok(indexes.length<32);assert.ok(indexes.includes(0));assert.ok(indexes.includes(2*Math.floor(50000/578)));assert.deepEqual(indexes,[...new Set(indexes)].sort((a,b)=>a-b));assert.ok(lengths.every(length=>length<32));}
 finally {Array.from=original;}
});
test('negative variants append continuously without losing selected identity, and failures retry same cursor',async()=>{
 const {createLibraryDetail}=await source;const calls=[];let count=0;const v=id=>({id,negative_text:id===9?'':'bad',explicit_empty:id===9});
 const loader=createLibraryDetail({api:{get:async url=>{calls.push(url);count++;if(count===2)throw Error('offline');return {positive:item(1),variants:count===1?[v(9)]:[v(9),v(10)],variant_total:2,variants_has_more:count===1,variants_next_cursor:count===1?'next':null,unassigned_image_count:0};}},onChange:()=>{}});
 await loader.load(1);assert.equal(loader.state.detail.variants.length,1);await loader.loadMore();assert.match(loader.state.variantsError.message,/offline/);assert.equal(loader.state.detail.variants[0].id,9);await loader.loadMore();assert.deepEqual(loader.state.detail.variants.map(row=>row.id),[9,10]);assert.match(calls[1],/variant_cursor=next/);assert.equal(calls[1],calls[2]);assert.equal(await loader.loadMore(),0);loader.dispose();
});
test('a separately verified pair outside the loaded page supplies roles only when it belongs to the parent',async()=>{
 const {libraryPromptRoles}=await source;const owner=item(1);const detail={positive:owner,variants:[{id:9,negative_text:'',explicit_empty:true}]};
 const outside={id:70,positive_prompt_id:1,positive_text:owner.text,negative_text:'old, kept',archived:false};
 assert.equal(libraryPromptRoles(detail,70,outside).negative.text,'old, kept');assert.equal(libraryPromptRoles(detail,9,outside).negative.text,'');
 assert.throws(()=>libraryPromptRoles(detail,70,{...outside,positive_prompt_id:2}));assert.throws(()=>libraryPromptRoles(detail,70,{...outside,positive_text:'other'}));
 assert.throws(()=>libraryPromptRoles(detail,71,outside));assert.throws(()=>libraryPromptRoles(detail,70,null));
});
test('accept replaces the detail with validated reconciled data and aborts the request it supersedes',async()=>{
 const {createLibraryDetail}=await source;const pending=[],states=[];const loader=createLibraryDetail({api:{get:(url,{signal})=>new Promise((resolve,reject)=>pending.push({url,signal,resolve,reject}))},onChange:state=>states.push(state)});
 assert.equal(typeof loader.accept,'function');
 const first=loader.load(1);pending[0].resolve({positive:item(1),variants:[{id:9,negative_text:'a',explicit_empty:false}],variant_total:1,variants_has_more:false,variants_next_cursor:null});await first;
 const more=loader.load(1);
 loader.accept({positive:{...item(1),category:'Edited'},variants:[{id:10,negative_text:'b',explicit_empty:false}],variant_total:85,variants_has_more:true,variants_next_cursor:'next'});
 assert.equal(pending[1].signal.aborted,true);pending[1].resolve({positive:item(1),variants:[],variant_total:0,variants_has_more:false,variants_next_cursor:null});await more;
 assert.equal(loader.state.detail.positive.category,'Edited');assert.deepEqual(loader.state.detail.variants.map(row=>row.id),[10]);assert.equal(loader.state.loading,false);assert.equal(loader.state.error,null);
 assert.throws(()=>loader.accept({positive:item(2),variants:[]}));assert.throws(()=>loader.accept({positive:item(1),variants:[{id:-1}]}));assert.equal(loader.state.detail.positive.category,'Edited');
 assert.equal(pending.length,2);loader.dispose();assert.throws(()=>loader.accept({positive:item(1),variants:[]}));
});
test('archived variant reads use their own scope and cursor without changing the active default',async()=>{
 const {createLibraryDetail}=await source;const urls=[];const v=id=>({id,negative_text:`n${id}`,explicit_empty:false,archived:true});let call=0;
 const loader=createLibraryDetail({api:{get:async url=>{urls.push(url);call++;return {positive:item(1),variants:[v(call)],variant_total:2,variants_has_more:call===1,variants_next_cursor:call===1?'arch':null};}},onChange:()=>{},archive:'archived'});
 await loader.load(1);await loader.loadMore();assert.deepEqual(loader.state.detail.variants.map(row=>row.id),[1,2]);
 for(const url of urls)assert.equal(new URL(url,'https://x').searchParams.get('archive'),'archived');assert.equal(new URL(urls[1],'https://x').searchParams.get('variant_cursor'),'arch');
 const active=[];const plain=createLibraryDetail({api:{get:async url=>{active.push(url);return {positive:item(1),variants:[]};}},onChange:()=>{}});await plain.load(1);assert.equal(new URL(active[0],'https://x').searchParams.has('archive'),false);
 assert.throws(()=>createLibraryDetail({api:{},onChange:()=>{},archive:'parents'}));
});
test('tag mode untagged is sent, echoed and never combined with tags (#444)',async()=>{
 const {createLibraryStream}=await source;const calls=[];const pending=[{...page([item(1)]),tag_mode:'untagged'},page([item(2)]),page([item(3)])];
 const s=createLibraryStream({api:{get:async(url)=>{calls.push(url);return pending.shift();}}});
 assert.equal(await s.reset({tagMode:'untagged'}),1);assert.equal(new URL(calls[0],'https://x').searchParams.get('tag_mode'),'untagged');
 // An older server that ignored tag_mode answers without the echo: an error, never the whole Library.
 assert.equal(await s.reset({tagMode:'untagged'}),0);assert.ok(s.state.error instanceof TypeError);assert.deepEqual(s.state.items,[]);
 // The default sends no tag_mode, so it needs no echo.
 assert.equal(await s.reset({tagMode:'all'}),1);assert.equal(new URL(calls[2],'https://x').searchParams.has('tag_mode'),false);
 await assert.rejects(s.reset({tagMode:'untagged',tags:['a']}),TypeError);await assert.rejects(s.reset({tagMode:'none'}),TypeError);
 assert.equal(calls.length,3);s.dispose();
});
test('the stream keeps the first page\'s storage epoch and refuses a later page from another one (#437)',async()=>{
 const {createLibraryStream}=await source;const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
 const pending=[{...page([item(1)],'next'),library_epoch:A},{...page([item(2)],'more'),library_epoch:A},{...page([item(3)]),library_epoch:B}];
 const s=createLibraryStream({api:{get:async()=>pending.shift()}});assert.equal(s.state.epoch,null);
 await s.reset();assert.equal(s.state.epoch,A);await s.loadMore();assert.equal(s.state.epoch,A);assert.equal(s.state.items.length,2);
 await s.loadMore();assert.ok(s.state.error);assert.equal(s.state.items.length,2);assert.equal(s.state.epoch,A);
 const restored=createLibraryStream({api:{},restore:{filters:{},state:s.state}});assert.equal(restored.state.epoch,A);restored.dispose();
 const fresh=[{...page([item(4)]),library_epoch:B}];const reset=s.reset({text:'x'});assert.equal(s.state.epoch,null);
 s.dispose();const next=createLibraryStream({api:{get:async()=>fresh.shift()}});await next.reset();assert.equal(next.state.epoch,B);next.dispose();await reset;
});
