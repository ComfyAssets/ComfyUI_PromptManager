const test=require('node:test');
const assert=require('node:assert/strict');
const source=import('../../web/js/workspace/library-state.mjs');
const item=id=>({id,text:`Positive ${id}`,category:'Art',tags:['blue'],rating:3,variant_count:2,image_count:4,previews:[]});
const page=(items,cursor=null)=>({items,total:3,has_more:cursor!==null,next_cursor:cursor});
test('parent stream builds literal filters and deduplicates stable identities across infinite pages',async()=>{
 const {createLibraryStream}=await source;const calls=[];const pending=[page([item(1),item(2)],'next'),page([item(2),item(3)])];const s=createLibraryStream({api:{get:async(url)=>{calls.push(url);return pending.shift();}}});
 assert.equal(await s.reset({text:'a%_',category:'Art',tags:['blue','red'],sort:'rating'}),2);assert.equal(await s.loadMore(),1);assert.deepEqual(s.state.items.map(row=>row.id),[1,2,3]);assert.equal(s.state.hasMore,false);
 const query=new URL(calls[0],'https://x').searchParams;assert.equal(query.get('text'),'a%_');assert.deepEqual(query.getAll('tag'),['blue','red']);assert.equal(query.get('sort'),'rating');assert.equal(new URL(calls[1],'https://x').searchParams.get('cursor'),'next');assert.equal(await s.loadMore(),0);s.dispose();
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
 assert.throws(()=>createLibraryStream({api:{},restore:{filters:{},state:{items:[item(1),item(1)],total:2,cursor:null,hasMore:false}}}));
});
test('parent shelf window bounds DOM and pins focused rows without changing logical order',async()=>{
 const {libraryWindow}=await source;const first=libraryWindow(500,2,0,900);assert.equal(first[0],0);assert.ok(first.length<24);
 const middle=libraryWindow(500,2,50000,900,0);assert.equal(middle[0],0);assert.ok(middle.some(index=>index>150));assert.ok(middle.length<24);assert.deepEqual(middle,[...middle].sort((a,b)=>a-b));assert.deepEqual(libraryWindow(3,2,0,900),[0,1,2]);
});
test('negative variants append continuously without losing selected identity, and failures retry same cursor',async()=>{
 const {createLibraryDetail}=await source;const calls=[];let count=0;const v=id=>({id,negative_text:id===9?'':'bad',explicit_empty:id===9});
 const loader=createLibraryDetail({api:{get:async url=>{calls.push(url);count++;if(count===2)throw Error('offline');return {positive:item(1),variants:count===1?[v(9)]:[v(9),v(10)],variant_total:2,variants_has_more:count===1,variants_next_cursor:count===1?'next':null,unassigned_image_count:0};}},onChange:()=>{}});
 await loader.load(1);assert.equal(loader.state.detail.variants.length,1);await loader.loadMore();assert.match(loader.state.variantsError.message,/offline/);assert.equal(loader.state.detail.variants[0].id,9);await loader.loadMore();assert.deepEqual(loader.state.detail.variants.map(row=>row.id),[9,10]);assert.match(calls[1],/variant_cursor=next/);assert.equal(calls[1],calls[2]);assert.equal(await loader.loadMore(),0);loader.dispose();
});
