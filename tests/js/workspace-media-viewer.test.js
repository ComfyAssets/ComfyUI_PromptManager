import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import gallerySettings from '../../web/js/gallery-settings.js';

const module = await import('../../web/js/workspace/media-viewer.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
test('disposed media viewer touches no DOM observers or requests', () => {
    assert.equal(typeof module.createMediaViewer,'function','media viewer contract is exported');
    const forbidden = new Proxy({}, {get() {assert.fail('Disposed viewer touched DOM or inspection');}});
    for (const aborted of [false,true]) {
        const parentScope = createScope(); if (aborted) parentScope.dispose();
        const view = module.createMediaViewer({container:forbidden,parentScope,inspectionController:forbidden,sender:forbidden,
            onSelect:() => assert.fail('Disposed viewer selected an entry'),onRefresh:() => assert.fail('Disposed viewer refreshed')});
        if (!aborted) view.dispose();
        view.open(forbidden); view.stopSlideshow?.(); view.dispose(); view.dispose(); parentScope.dispose();
    }
});

test('preferences handle denied malformed storage without mutation', () => {
    assert.equal(typeof module.readViewerPreferences,'function');
    const normalize=gallerySettings.normalizeGallerySettings,defaults={videoAutoplay:false,videoMute:true,videoLoop:true};
    for(const getStorage of [() => {throw new Error('Denied');},() => ({getItem:() => {throw new Error('Denied');}}),
        ...['{broken','null','[]','42'].map(value=>() => ({getItem:key=>{assert.equal(key,'gallerySettings');return value;},setItem:()=>assert.fail('Read-only preferences')}))]) {
        assert.deepEqual(module.readViewerPreferences(getStorage,normalize),defaults);
    }
    let raw=JSON.stringify({videoAutoplay:true,videoMute:false,videoLoop:false,infiniteScroll:true});
    const storage={getItem:key=>{assert.equal(key,'gallerySettings');return raw;},setItem:()=>assert.fail('Read-only preferences')};
    assert.deepEqual(module.readViewerPreferences(()=>storage,normalize),{videoAutoplay:true,videoMute:false,videoLoop:false});
    raw=JSON.stringify({videoAutoplay:'true',videoMute:0,videoLoop:null});
    assert.deepEqual(module.readViewerPreferences(()=>storage,normalize),defaults);
});

function mountedDom() {
    class Node {
        constructor(tag) {this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this._text='';this.classList={add(){},remove(){}};}
        get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
        set textContent(value){this._text=String(value);this.children=[];}
        get childNodes(){return this.children;}
        get isConnected(){return this.root || !!this.parent?.isConnected;}
        append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
        prepend(...nodes){for(const node of nodes)node.parent=this;this.children.unshift(...nodes);}
        replaceChildren(...nodes){for(const child of this.children)child.parent=null;this.children=[];this._text='';this.append(...nodes);}
        remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}
        setAttribute(key,value){this.attributes[key]=String(value);}
        getAttribute(key){return this.attributes[key]??null;}
        removeAttribute(key){delete this.attributes[key];}
        addEventListener(type,callback,options={}){const list=this.listeners.get(type)||[];list.push({callback,signal:options.signal});this.listeners.set(type,list);}
        removeEventListener(type,callback){this.listeners.set(type,(this.listeners.get(type)||[]).filter(e=>e.callback!==callback));}
        fire(type,event={}){for(const e of [...(this.listeners.get(type)||[])])if(!e.signal?.aborted)e.callback({target:this,preventDefault(){},...event});}
        focus(){this.focused=true;}
        showModal(){this.open=true;}
        close(){this.open=false;this.fire('close');}
        contains(node){return node===this||this.children.some(c=>c.contains(node));}
        closest(selector){if(selector==='section'&&this.tagName==='section')return this;if(selector==='[data-send-role]'&&this.dataset.sendRole)return this;if(selector==='[data-copy-role]'&&this.dataset.copyRole)return this;return this.parent?.closest(selector)||null;}
        querySelector(selector){return walk(this,n=>n.tagName===selector)[0]||null;}
        pause(){this.paused=true;}
        load(){this.loaded=true;}
        play(){this.played=true;return Promise.resolve();}
    }
    const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...node.children.flatMap(child=>walk(child,predicate))];
    const oldDocument=globalThis.document,oldWindow=globalThis.window;
    const container=new Node('main');container.root=true;
    const doc=new Node('document');doc.createElement=tag=>new Node(tag);doc.createElementNS=(_,tag)=>new Node(tag);
    doc.querySelectorAll=()=>walk(container,n=>n.tagName==='dialog'&&n.open);doc.activeElement=new Node('button');doc.activeElement.root=true;
    const win=new Node('window');win.location={href:'http://localhost/prompt_manager/workspace'};win.getSelection=()=>({isCollapsed:true});
    globalThis.document=doc;globalThis.window=win;
    return {container,doc,win,walk,Node,restore(){globalThis.document=oldDocument;globalThis.window=oldWindow;}};
}
function occurrenceMedia(node,ordinal,key) {
    const run='22222222-2222-2222-2222-222222222222',epoch='11111111-1111-1111-1111-111111111111',scope='a'.repeat(64);
    const query=new URLSearchParams({node,ordinal:String(ordinal),epoch,current_scope:scope}),base=`/prompt_manager/history/runs/${run}/output/current`;
    return {kind:'reported-location',location_key:key.repeat(64),history_epoch:epoch,current_scope:scope,occurrence:{run_id:run,output_node_id:node,image_ordinal:ordinal},reference:{folder_type:'output',subfolder:'',filename:'a.png',path_style:'posix',root_key:'b'.repeat(64)},filename:'a.png',is_video:false,url:`${base}/media?${query}`,thumbnail_url:`${base}/thumbnail?${query}`,inspection_url:`${base}/inspection?${query}`};
}
function borrowedController() {
    const listeners=new Set();let disposed=0;
    return {get state(){return {loading:false,error:false,record:null};},subscribe(fn){listeners.add(fn);fn(this.state);return ()=>listeners.delete(fn);},publish(state){for(const fn of listeners)fn(state);},retry(){},dispose(){disposed++;},get disposed(){return disposed;},get listeners(){return listeners.size;}};
}

test('occurrence Viewer retains exact caller selection and synchronously closes without disposing borrowed controller',async()=>{
    const {reportedLocationAdapter:adapter}=await import('../../web/js/workspace/history-media.mjs');assert.ok(adapter);
    const f=mountedDom(),parentScope=createScope(),controller=borrowedController(),selections=[],closes=[],items=[occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')];
    const trigger=f.doc.activeElement;
    const view=module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}},onSelect:entry=>selections.push(entry.occurrence),onClose(...args){assert.equal(f.walk(f.container,n=>n.tagName==='dialog').length,0);assert.equal(controller.listeners,0);assert.equal(trigger.focused,true);closes.push(args);}});
    try {
        view.open({items,selectedKey:'c'.repeat(64),trigger});assert.deepEqual(selections,[{run_id:items[0].occurrence.run_id,output_node_id:'1',image_ordinal:1}]);
        const media=f.walk(f.container,n=>n.tagName==='img')[0];assert.match(media.src,/\/history\/runs\/.*\/output\/current\/media\?/);assert.match(media.src,/_pm_viewer=/);assert.doesNotMatch(media.src,/root=/);
        const download=f.walk(f.container,n=>n.attributes['aria-label']==='Download original')[0];assert.equal(download.href,items[0].url);
        f.walk(f.container,n=>n.attributes['aria-label']==='Next media')[0].fire('click');assert.equal(selections.at(-1).output_node_id,'2');
        f.walk(f.container,n=>n.attributes['aria-label']==='Close preview')[0].fire('click');assert.deepEqual(closes,[[]]);assert.equal(controller.disposed,0);
        const count=selections.length;media.fire('load');controller.publish({loading:true});assert.equal(selections.length,count);
        view.dispose();view.open({items,selectedKey:'c'.repeat(64)});assert.equal(f.container.children.length,0);assert.equal(controller.disposed,0);
    } finally {view.dispose();parentScope.dispose();f.restore();}
});

test('slideshow stops for detail reading nested send close and parent disposal with late media callbacks ignored',async()=>{
    const {reportedLocationAdapter:adapter}=await import('../../web/js/workspace/history-media.mjs');const f=mountedDom(),parentScope=createScope(),controller=borrowedController(),items=[occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')];let closes=0,sends=0;
    const view=module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,onClose:()=>closes++,sender:{open(){sends++;}}});
    try {
        view.open({items,selectedKey:'c'.repeat(64)});
        const find=label=>f.walk(f.container,n=>n.attributes['aria-label']===label)[0];
        let media=f.walk(f.container,n=>n.tagName==='img')[0];media.fire('load');find('Play slideshow').fire('click');assert.ok(find('Pause slideshow'));
        controller.publish({loading:true,error:false});assert.ok(find('Play slideshow'),'detail reading ends slideshow intent');
        const record={identity:{kind:'reported-location',key:'c'.repeat(64),path:'a.png',occurrence:items[0].occurrence,reference:items[0].reference,history_epoch:items[0].history_epoch,current_scope:items[0].current_scope},file:null,library:null,metadata:null,generation:{},prompts:{positive:{status:'resolved',text:'current'},negative:{status:'resolved',text:''}},download:{url:items[0].url,filename:'a.png'}};
        controller.publish({loading:false,error:false,record});assert.ok(f.walk(f.container,n=>n.textContent==='Current file at the reported location · identity unverified').length);
        find('Play slideshow').fire('click');const pair=f.walk(f.container,n=>n.dataset.sendRole==='pair')[0];pair.fire('click');assert.equal(sends,1);assert.ok(find('Play slideshow'));
        find('Play slideshow').fire('click');find('Close preview').fire('click');assert.equal(closes,1);assert.equal(controller.listeners,0);media.fire('load');
        view.open({items,selectedKey:'c'.repeat(64)});media=f.walk(f.container,n=>n.tagName==='img')[0];parentScope.dispose();assert.equal(closes,1,'parent disposal suppresses onClose');assert.equal(f.container.children.length,0);media.fire('load');assert.equal(controller.disposed,0);
    } finally {view.dispose();parentScope.dispose();f.restore();}
});
