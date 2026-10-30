import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import gallerySettings from '../../web/js/gallery-settings.js';
import {mountedDom} from './helpers/mounted-dom.mjs';

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
        view.open(forbidden); view.stopSlideshow?.(); view.close(); view.dispose(); view.dispose(); parentScope.dispose();
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
        f.walk(f.container,n=>n.attributes['aria-label']==='Close image viewer')[0].fire('click');assert.deepEqual(closes,[[]]);assert.equal(controller.disposed,0);
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
        f.walk(f.container,n=>n.tagName==='aside')[0].fire('pointerdown');assert.ok(find('Play slideshow'),'explicit detail reading ends slideshow intent');
        const record={identity:{kind:'reported-location',key:'c'.repeat(64),path:'a.png',occurrence:items[0].occurrence,reference:items[0].reference,history_epoch:items[0].history_epoch,current_scope:items[0].current_scope},file:null,library:null,metadata:null,generation:{},prompts:{positive:{status:'resolved',text:'current'},negative:{status:'resolved',text:''}},download:{url:items[0].url,filename:'a.png'}};
        controller.publish({loading:false,error:false,record});assert.ok(f.walk(f.container,n=>n.textContent==='Current file at the reported location · identity unverified').length);
        find('Play slideshow').fire('click');const pair=f.walk(f.container,n=>n.dataset.sendRole==='pair')[0];pair.fire('click');assert.equal(sends,1);assert.ok(find('Play slideshow'));
        find('Play slideshow').fire('click');find('Close image viewer').fire('click');assert.equal(closes,1);assert.equal(controller.listeners,0);media.fire('load');
        view.open({items,selectedKey:'c'.repeat(64)});media=f.walk(f.container,n=>n.tagName==='img')[0];parentScope.dispose();assert.equal(closes,1,'parent disposal suppresses onClose');assert.equal(f.container.children.length,0);media.fire('load');assert.equal(controller.disposed,0);
    } finally {view.dispose();parentScope.dispose();f.restore();}
});

function currentInspectionEnvelope(target) {
    return {success:true,inspection_version:1,history_version:1,history_epoch:target.history_epoch,
        current_output:{occurrence:target.occurrence,reference:target.reference,history_epoch:target.history_epoch,current_scope:target.current_scope,location_key:target.location_key,evidence_class:'locator_candidate',observation:'current-file',file_identity:'unverified',newly_generated:'unknown',context_membership:'unknown',transient:false},
        file:{observation:'current-file',source_label:'output',media_type:'image',extension:'.png',format:'PNG',size:10,width:2,height:3,modified_time:1},
        prompts:{positive:{status:'resolved',text:'current positive',source:'execution'},negative:{status:'resolved',text:'',source:'execution'}},
        metadata:{status:'absent',omitted_reason:null,raw:{},record_formats:{},prompt_graph:{status:'absent',value:null},workflow_graph:{status:'absent',value:null},parameters:null},
        resolution:{state:'absent',sampler_id:null,reason:'no graph'},library:{status:'none',scope:'saved-library',positive_id:null,pair_id:null,title:null,category:null,tags:[],category_scope:null,tags_scope:null}};
}

test('real inspection navigation preserves slideshow across two advances and deferred metadata loads',async()=>{
    const {reportedLocationAdapter:adapter}=await import('../../web/js/workspace/history-media.mjs');
    const {createInspectionController}=await import('../../web/js/workspace/inspection-state.mjs');
    const {extractGenerationFacts}=(await import('../../web/js/comfy-metadata.js')).default;
    const f=mountedDom(),parentScope=createScope(),calls=[],reads=[],selected=[],timers=new Map();let serial=0,closed=0,sent=0;
    const oldSetTimeout=globalThis.setTimeout,oldClearTimeout=globalThis.clearTimeout;
    globalThis.setTimeout=(callback,delay)=>{assert.equal(delay,3500);timers.set(++serial,callback);return serial;};
    globalThis.clearTimeout=token=>timers.delete(token);
    const controller=createInspectionController({adapter,extractFacts:extractGenerationFacts,api:{get:(url,{signal})=>new Promise((resolve,reject)=>calls.push({url,signal,resolve,reject}))}});
    const items=[occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d'),occurrenceMedia('3',0,'e')];
    const view=module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,
        onSelect(entry){selected.push(entry.occurrence);reads.push(controller.select(entry));},onClose(){closed++;},sender:{open(){sent++;}}});
    const find=label=>f.walk(f.container,n=>n.attributes['aria-label']===label)[0];
    const image=()=>f.walk(f.container,n=>n.tagName==='img')[0];
    const advance=()=>{assert.equal(timers.size,1);const [token,callback]=timers.entries().next().value;timers.delete(token);callback();};
    try {
        view.open({items,selectedKey:items[0].location_key});assert.equal(controller.state.loading,true);
        calls[0].resolve(currentInspectionEnvelope(items[0]));await reads[0];image().fire('load');find('Play slideshow').fire('click');
        advance();assert.equal(controller.state.loading,true);assert.ok(find('Pause slideshow'),'automatic next-file inspection preserves intent');
        assert.deepEqual(selected.map(o=>o.output_node_id),['1','2']);assert.equal(timers.size,0,'readiness still gates the next advance');
        image().fire('load');advance();assert.equal(controller.state.loading,true);assert.ok(find('Pause slideshow'));
        assert.deepEqual(selected.map(o=>o.output_node_id),['1','2','3']);assert.equal(calls[1].signal.aborted,true);
        image().fire('load');assert.equal(timers.size,1);calls[1].reject(new Error('late superseded inspection'));await reads[1];assert.ok(find('Pause slideshow'));
        calls[2].resolve(currentInspectionEnvelope(items[2]));await reads[2];assert.ok(find('Pause slideshow'));assert.equal(timers.size,1);
        f.walk(f.container,n=>n.tagName==='aside')[0].fire('keydown');assert.ok(find('Play slideshow'));assert.equal(timers.size,0);
        find('Play slideshow').fire('click');f.walk(f.container,n=>n.dataset.sendRole==='pair')[0].fire('click');assert.equal(sent,1);assert.ok(find('Play slideshow'));assert.equal(timers.size,0);
        find('Play slideshow').fire('click');const retry=controller.retry();calls.at(-1).reject(new Error('inspection read failed'));await retry;
        assert.ok(find('Play slideshow'),'inspection errors stop intent');assert.equal(timers.size,0);
        const retryButton=f.walk(f.container,n=>n.textContent==='Retry prompt details')[0];assert.ok(retryButton);
        find('Play slideshow').fire('click');f.walk(f.container,n=>n.tagName==='aside')[0].fire('click',{target:retryButton});retryButton.fire('click');
        assert.ok(find('Play slideshow'),'explicit retry interaction stops intent');assert.equal(controller.state.loading,true);assert.equal(timers.size,0);
        calls.at(-1).resolve(currentInspectionEnvelope(items[2]));await new Promise(resolve=>setImmediate(resolve));
        find('Play slideshow').fire('click');const lateTimer=[...timers.values()][0];find('Close image viewer').fire('click');assert.equal(closed,1);assert.equal(timers.size,0);
        lateTimer();assert.equal(selected.length,3);assert.equal(controller.state.record.identity.occurrence.output_node_id,'3','closing keeps the borrowed controller live');
        view.open({items,selectedKey:items[2].location_key});const lateMedia=image();lateMedia.fire('load');find('Play slideshow').fire('click');parentScope.dispose();assert.equal(timers.size,0);assert.equal(closed,1);lateMedia.fire('load');
        assert.equal(f.container.children.length,0);assert.equal(controller.state.record.identity.occurrence.output_node_id,'3');
    } finally {view.dispose();controller.dispose();parentScope.dispose();globalThis.setTimeout=oldSetTimeout;globalThis.clearTimeout=oldClearTimeout;f.restore();}
});

test('Viewer.js is built only on a loaded image and an unbuilt viewer is released at once', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController(), items = [occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')];
    const instances = [];
    // On a loaded image Viewer.js schedules its inline build; until it runs `ready` is false and destroy() cancels it.
    f.win.Viewer = class { constructor(node,options) { this.node = node; this.options = options; this.destroyed = 0; this.ready = false; this.viewed = false; instances.push(this); }
        destroy() { this.destroyed++; } resize() {} };
    const globals = {ResizeObserver:globalThis.ResizeObserver, requestAnimationFrame:globalThis.requestAnimationFrame, cancelAnimationFrame:globalThis.cancelAnimationFrame};
    Object.assign(globalThis, {ResizeObserver:class { observe() {} disconnect() {} }, requestAnimationFrame:() => 1, cancelAnimationFrame() {}});
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}}});
    try {
        const stageImage = () => f.walk(f.container,n => n.tagName === 'img')[0];
        view.open({items,selectedKey:'d'.repeat(64)});
        // Viewer.js's own cancel misses an image whose load event is still queued, so none exists before the load.
        const unloaded = stageImage(), dropped = [];unloaded.removeAttribute = key => dropped.push(key);
        assert.equal(instances.length, 0, 'no Viewer.js before the image loads');
        f.walk(f.container,n => n.attributes['aria-label'] === 'Previous media')[0].fire('click');
        assert.deepEqual(dropped, ['src'], 'an image left before it loads is released at once');
        unloaded.fire('load');
        assert.equal(instances.length, 0, 'its late load builds no Viewer.js');
        stageImage().fire('load');
        const [loading] = instances, image = loading.node, removed = [];
        image.removeAttribute = key => removed.push(key);
        // Next drops the image whose build is pending; a deferred destroy would let that build run there.
        f.walk(f.container,n => n.attributes['aria-label'] === 'Next media')[0].fire('click');
        stageImage().fire('load');assert.equal(instances.length, 2, 'the next image builds its own Viewer.js once loaded');
        assert.equal(loading.destroyed, 1, 'an unbuilt viewer is destroyed at once');
        assert.deepEqual(removed, ['src'], 'and its image is released at once');
        loading.options.viewed.call(loading);
        assert.equal(loading.destroyed, 1, 'never destroyed twice');
    } finally { view.dispose(); parentScope.dispose(); f.restore(); Object.assign(globalThis, globals); }
});

test('an inline Viewer still loading its image is destroyed only after it has viewed it', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController(), items = [occurrenceMedia('1',1,'c')];
    const instances = [];
    // A built (`ready`) inline Viewer.js's destroy() leaves an in-flight view running; its callback then needs a live instance.
    f.win.Viewer = class { constructor(node,options) { this.node = node; this.options = options; this.destroyed = 0; this.ready = true; this.viewed = false; instances.push(this); }
        destroy() { this.destroyed++; } resize() {} };
    const globals = {ResizeObserver:globalThis.ResizeObserver, requestAnimationFrame:globalThis.requestAnimationFrame, cancelAnimationFrame:globalThis.cancelAnimationFrame};
    Object.assign(globalThis, {ResizeObserver:class { observe() {} disconnect() {} }, requestAnimationFrame:() => 1, cancelAnimationFrame() {}});
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}}});
    try {
        view.open({items,selectedKey:'c'.repeat(64)});f.walk(f.container,n => n.tagName === 'img')[0].fire('load');
        const [loading] = instances, image = loading.node, removed = [];
        image.removeAttribute = key => removed.push(key);
        f.walk(f.container,n => n.attributes['aria-label'] === 'Close image viewer')[0].fire('click');
        assert.equal(loading.destroyed, 0, 'not destroyed while its image view is pending');
        // Viewer.js lists only images that still have a src; the pending view needs it.
        assert.deepEqual(removed, [], 'the image keeps its src while the view is pending');
        loading.viewed = true; loading.options.viewed.call(loading);
        assert.equal(loading.destroyed, 1, 'destroyed once its pending view settles');
        assert.deepEqual(removed, ['src'], 'the src is released after the viewer');
        loading.options.viewed.call(loading);
        assert.equal(loading.destroyed, 1, 'never destroyed twice');

        view.open({items,selectedKey:'c'.repeat(64)});f.walk(f.container,n => n.tagName === 'img')[0].fire('load');
        const shown = instances[1]; shown.viewed = true; shown.options.viewed.call(shown);
        f.walk(f.container,n => n.attributes['aria-label'] === 'Close image viewer')[0].fire('click');
        assert.equal(shown.destroyed, 1, 'a viewed image is destroyed at once');
    } finally { view.dispose(); parentScope.dispose(); f.restore(); Object.assign(globalThis, globals); }
});

const left0 = dialog => dialog.children[1].children[0];
test('viewer renders the concept shell and toolbar order', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController(), items = [occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')];
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}}});
    try {
        view.open({items,selectedKey:'c'.repeat(64)});
        const [dialog] = f.container.children;
        assert.equal(dialog.tagName,'dialog');assert.equal(dialog.id,'viewer-dialog');assert.equal(dialog.attributes['aria-labelledby'],'viewer-title');
        assert.equal(dialog.attributes['aria-label'],undefined,'the title names the dialog');
        const [header,layout] = dialog.children;
        assert.equal(header.className,'dialog-header');
        const [heading,keys,mode,close] = header.children, [title,subtitle] = heading.children;
        // #532 D170: ⌘ before the view switch opens the shortcuts.
        assert.deepEqual([keys.tagName,keys.id,keys.className,keys.attributes['aria-label'],keys.title],['button','viewer-keys','icon-btn','Keyboard shortcuts','Keyboard shortcuts']);
        // #450 D156: the view switch before ✕ names the view it switches to (Centered is the stored default).
        assert.deepEqual([mode.tagName,mode.id,mode.className,mode.attributes['aria-label'],mode.title],['button','viewer-mode','icon-btn','Full screen view','Full screen view']);
        mode.fire('click');
        assert.equal(mode.attributes['aria-label'],'Centered view');
        assert.ok(f.walk(left0(dialog),n => n.textContent === 'Hide details').length === 1,'full screen adds the details toggle');
        mode.fire('click');
        assert.equal(mode.attributes['aria-label'],'Full screen view');
        assert.deepEqual([heading.tagName,title.tagName,title.id,title.textContent],['div','h2','viewer-title','a.png']);
        assert.deepEqual([subtitle.tagName,subtitle.className,subtitle.textContent],['p','muted small','One viewer · image, prompts, generation data, workflow']);
        assert.deepEqual([close.tagName,close.id,close.textContent,close.attributes['aria-label']],['button','viewer-close','×','Close image viewer']);
        assert.equal(layout.className,'viewer-layout');
        const [left,details] = layout.children;
        assert.equal(left.className,'viewer-left');
        assert.deepEqual([details.tagName,details.id,details.attributes['aria-label']],['aside','viewer-details','Image metadata']);
        const [stage,toolbar] = left.children;
        assert.deepEqual([stage.tagName,stage.id,toolbar.tagName,toolbar.id,toolbar.className],['div','viewer-stage','div','viewer-toolbar','viewer-toolbar']);
        // #249 D34 #8: the toolbar keeps production's named group (the concept's has no role; adding one is invisible).
        assert.deepEqual([toolbar.attributes.role,toolbar.attributes['aria-label']],['group','Media controls']);
        // With more than one loaded item, Previous/Next are enabled and not described by the position line.
        for (const label of ['Previous media','Next media']) {
            const [node] = f.walk(f.container,n => n.attributes['aria-label'] === label);
            assert.deepEqual([node.attributes['aria-disabled'],node.attributes['aria-describedby']],['false',undefined],label);
        }
        // The concept's toolbar order, then production's position status and its two reasons.
        assert.deepEqual(toolbar.children.map(node => node.textContent.split(':')[0]),
            ['← Previous','−','+','Fit','Rotate','Flip','Play','Download','Next →','1 of 2 loaded','Image transforms are unavailable because Viewer.js could not load.','']);
        assert.deepEqual(toolbar.children.slice(9).map(node => [node.tagName,node.attributes.role ?? null]),[['p','status'],['p',null],['p',null]]);
        f.walk(f.container,n => n.attributes['aria-label'] === 'Next media')[0].fire('click');
        assert.equal(toolbar.children[9].textContent,'2 of 2 loaded: a.png');
    } finally { view.dispose(); parentScope.dispose(); f.restore(); }
});

test('⌘ opens the shortcuts on top of the viewer and closing them leaves it open (#532)', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController(), items = [occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')];
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}}});
    try {
        view.open({items,selectedKey:'c'.repeat(64)});
        const [dialog] = f.container.children, [keys] = f.walk(dialog,n => n.id === 'viewer-keys');
        const position = () => f.walk(dialog,n => n.attributes.role === 'status')[0].textContent;
        const overlays = () => f.walk(dialog,n => n.id === 'viewer-keys-dialog');
        assert.equal(overlays().length,0,'built on demand');
        assert.match(position(),/^1 of 2/);
        f.doc.fire('keydown',{key:'ArrowRight',target:f.walk(dialog,n => n.id === 'viewer-close')[0]});
        assert.match(position(),/^2 of 2/,'the arrows work with focus on ×');
        keys.fire('click');
        const [overlay] = overlays();
        assert.equal(overlay.open,true);
        assert.equal(overlay.attributes['aria-labelledby'],'viewer-keys-title');
        assert.deepEqual(f.walk(overlay,n => n.tagName === 'kbd').map(n => n.textContent),['←','→','Space','+','-','0','R','H','F','I','?','Esc','D']);
        f.doc.fire('keydown',{key:'ArrowLeft',target:keys});
        assert.match(position(),/^2 of 2/,'the arrows wait for the overlay');
        f.walk(overlay,n => n.attributes['aria-label'] === 'Close keyboard shortcuts')[0].fire('click');
        assert.equal(overlays().length,0);
        assert.equal(dialog.open,true,'the viewer stays open');
    } finally { view.dispose(); parentScope.dispose(); f.restore(); }
});

test('hotkeys click the toolbar\'s own controls, so an unavailable control does nothing (#532)', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController(), items = [occurrenceMedia('1',1,'c'),occurrenceMedia('2',0,'d')], deletes = [];
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}},onDelete:(entry,button) => deletes.push([entry,button])});
    try {
        view.open({items,selectedKey:'c'.repeat(64)});
        const [dialog] = f.container.children, by = (test) => f.walk(dialog,test)[0];
        const stage = by(n => n.id === 'viewer-stage'), mode = by(n => n.id === 'viewer-mode'), remove = by(n => n.textContent === 'Delete…');
        const press = (key,init = {}) => { let prevented = false; f.doc.fire('keydown',{key,target:stage,preventDefault(){prevented = true;},...init});return prevented; };
        // No media is ready: every transform is aria-disabled, so its key is swallowed by the same check as a click.
        for (const key of ['+','=','-','0','r','H']) assert.equal(press(key),true,key);
        // Modifiers, repeats and composition leave every key alone; Shift does not.
        for (const init of [{ctrlKey:true},{metaKey:true},{altKey:true},{repeat:true},{isComposing:true}]) assert.equal(press('f',init),false);
        assert.equal(press('+',{repeat:true}),true,'zoom may repeat');
        assert.equal(press('Unbound'),false);
        // F clicks the switch, and does nothing while the switch is hidden.
        assert.equal(mode.attributes['aria-label'],'Full screen view');
        press('F',{shiftKey:true});assert.equal(mode.attributes['aria-label'],'Centered view');
        mode.layoutHidden = true;assert.equal(press('f'),false);assert.equal(mode.attributes['aria-label'],'Centered view');mode.layoutHidden = false;
        // I needs the toggle: it is there in full screen and gone when Centered.
        const details = by(n => n.id === 'viewer-details');
        press('i');assert.equal(details.hidden,true);press('i');assert.equal(details.hidden,false);
        press('f');assert.equal(press('i'),false,'no toggle when Centered');
        // Delete only opens the page's review, for this file.
        press('Delete');assert.equal(deletes.length,1);assert.equal(deletes[0][1],remove);
        press('Delete',{repeat:true});assert.equal(deletes.length,1);
        // D goes through the link's own check while the file cannot be downloaded.
        controller.publish({loading:false,error:false,record:null,refreshRequired:true,message:'Refresh required.'});
        const download = by(n => n.attributes['aria-label'] === 'Download original');
        let blocked = false;download.fire('click',{preventDefault(){blocked = true;}});assert.equal(blocked,true);
        assert.equal(press('d'),true);
        // Space and ? on the stage; a target that keeps its keys leaves them alone.
        assert.equal(press(' '),true);
        assert.equal(press('?'),true);
        const overlay = by(n => n.id === 'viewer-keys-dialog');assert.equal(overlay.open,true);
        assert.equal(press('r'),false,'off under the overlay');
        overlay.close();
        assert.equal(press('r',{target:new f.Node('body')}),false,'a target outside the viewer that is not the page');
    } finally { view.dispose(); parentScope.dispose(); f.restore(); }
    const g = mountedDom(), scope = createScope();
    const plain = module.createMediaViewer({container:g.container,parentScope:scope,inspectionController:borrowedController(),adapter,sender:{open(){}}});
    try {
        plain.open({items,selectedKey:'c'.repeat(64)});
        const stage = g.walk(g.container,n => n.id === 'viewer-stage')[0];
        let prevented = false;g.doc.fire('keydown',{key:'Delete',target:stage,preventDefault(){prevented = true;}});
        assert.equal(prevented,false,'no Delete… in this viewer, so no Delete key');
        g.doc.fire('keydown',{key:'?',target:stage});
        assert.ok(!g.walk(g.container,n => n.tagName === 'kbd').some(n => n.textContent === 'Delete'),'and none listed');
    } finally { plain.dispose(); scope.dispose(); g.restore(); }
});

test('viewer titles a linked card "<title> · <filename>" before any inspection arrives', async () => {
    const {reportedLocationAdapter:adapter} = await import('../../web/js/workspace/history-media.mjs');
    const f = mountedDom(), parentScope = createScope(), controller = borrowedController();
    const linked = {...occurrenceMedia('1',1,'c'),linked:{status:'linked',title:'Quiet geometry'}};
    const unlinked = {...occurrenceMedia('2',0,'d'),linked:{status:'unlinked',title:'Ignored'}};
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,sender:{open(){}}});
    try {
        view.open({items:[linked,unlinked],selectedKey:'c'.repeat(64)});
        const title = f.walk(f.container,n=>n.id==='viewer-title')[0];
        assert.equal(title.textContent,`Quiet geometry · ${linked.filename}`);
    } finally { view.dispose(); parentScope.dispose(); f.restore(); }
    const g = mountedDom(), scope = createScope();
    const other = module.createMediaViewer({container:g.container,parentScope:scope,inspectionController:borrowedController(),adapter,sender:{open(){}}});
    try {
        other.open({items:[linked,unlinked],selectedKey:'d'.repeat(64)});
        assert.equal(g.walk(g.container,n=>n.id==='viewer-title')[0].textContent,unlinked.filename);
    } finally { other.dispose(); scope.dispose(); g.restore(); }
});

// PR L2 Task 4(d): an adapter's own media URL (a session-owned blob:) is used exactly, never cache-busted.
async function localHandles() {
    const {createLocalFileHandle} = await import('../../web/js/workspace/local-inspection.mjs');
    const image = createLocalFileHandle({file:new File([new Uint8Array(8)], 'a.png'), url:'blob:http://localhost/aaaaaaaa-0000-0000-0000-000000000001', classification:{media:'image', kind:'png'}});
    const video = createLocalFileHandle({file:new File([new Uint8Array(8)], 'b.mp4'), url:'blob:http://localhost/aaaaaaaa-0000-0000-0000-000000000002', classification:{media:'video', kind:'iso-bmff'}});
    return {image, video};
}
function withViewerGlobals(f) {
    const globals = {ResizeObserver:globalThis.ResizeObserver, requestAnimationFrame:globalThis.requestAnimationFrame, cancelAnimationFrame:globalThis.cancelAnimationFrame, GallerySettings:globalThis.GallerySettings};
    Object.assign(globalThis, {ResizeObserver:class { observe() {} disconnect() {} }, requestAnimationFrame:() => 1, cancelAnimationFrame() {}, GallerySettings:gallerySettings});
    f.win.localStorage = {getItem:() => null, setItem:() => assert.fail('read-only preferences')};
    return () => Object.assign(globalThis, globals);
}

test('viewer uses adapter.mediaUrl for image and video', async () => {
    const {localFileAdapter:adapter} = await import('../../web/js/workspace/local-inspection.mjs');
    const {image, video} = await localHandles();
    const f = mountedDom(), restoreGlobals = withViewerGlobals(f), parentScope = createScope(), instances = [];
    f.win.Viewer = class { constructor(node) { this.node = node; instances.push(this); } destroy() {} resize() {} };
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:borrowedController(),adapter,sender:{open(){}}});
    try {
        view.open({items:[image, video],selectedKey:image.key});
        const img = f.walk(f.container,n => n.tagName === 'img')[0];
        assert.equal(img.src, image.url, 'the blob URL is used exactly');
        assert.doesNotMatch(img.src, /\?/);
        img.fire('load');assert.equal(instances[0].node, img, 'Viewer.js shows the blob image');
        const download = f.walk(f.container,n => n.attributes['aria-label'] === 'Download original')[0];
        assert.equal(download.href, image.url);assert.equal(download.download, 'a.png');
        f.walk(f.container,n => n.attributes['aria-label'] === 'Next media')[0].fire('click');
        const node = f.walk(f.container,n => n.tagName === 'video')[0];
        assert.equal(node.src, video.url);assert.doesNotMatch(node.src, /\?/);
        assert.equal(f.walk(f.container,n => n.tagName === 'img').length, 0);
        view.dispose();
        assert.equal(node.attributes.src, undefined);assert.equal(node.loaded, true, 'closing releases the video source');
    } finally { view.dispose(); parentScope.dispose(); restoreGlobals(); f.restore(); }
});

test('retry media keeps the blob URL exact', async () => {
    const {localFileAdapter:adapter} = await import('../../web/js/workspace/local-inspection.mjs');
    const {image} = await localHandles();
    const f = mountedDom(), restoreGlobals = withViewerGlobals(f), parentScope = createScope();
    const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:borrowedController(),adapter,sender:{open(){}}});
    try {
        view.open({items:[image],selectedKey:image.key});
        const first = f.walk(f.container,n => n.tagName === 'img')[0];
        assert.equal(first.src, image.url);
        first.fire('error');
        const failure = f.walk(f.container,n => n.className === 'media-failure')[0];
        assert.ok(!failure.hidden, 'the media failure shows');
        f.walk(f.container,n => n.tagName === 'button' && n.textContent === 'Retry media')[0].fire('click');
        const second = f.walk(f.container,n => n.tagName === 'img')[0];
        assert.notEqual(second, first, 'retry rebuilds the media element');
        assert.equal(second.src, image.url, 'retry reuses the exact blob URL');
    } finally { view.dispose(); parentScope.dispose(); restoreGlobals(); f.restore(); }
});

test('server adapters still cache-bust the original on open and retry', async () => {
    const {rootedFileAdapter, createInspectionController} = await import('../../web/js/workspace/inspection-state.mjs');
    const {reportedLocationAdapter} = await import('../../web/js/workspace/history-media.mjs');
    const scope = 'a'.repeat(64);
    const rooted = {root_index:0,path:'folder/a.png',filename:'a.png',root_scope:scope,is_video:false,url:`/prompt_manager/images/serve/folder/a.png?root=0&root_scope=${scope}`};
    for (const [adapter, item, key, path] of [[rootedFileAdapter, rooted, '0:folder/a.png', `/prompt_manager/images/inspect/folder/a.png?root=0&root_scope=${scope}`],
        [reportedLocationAdapter, occurrenceMedia('1',1,'c'), 'c'.repeat(64), occurrenceMedia('1',1,'c').inspection_url]]) {
        const f = mountedDom(), restoreGlobals = withViewerGlobals(f), parentScope = createScope(), paths = [];
        const controller = createInspectionController({adapter,api:{get(url) {paths.push(url);return new Promise(() => {});}}});
        const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter,onSelect:entry => controller.select(entry),sender:{open(){}}});
        try {
            view.open({items:[item],selectedKey:key});
            assert.deepEqual(paths, [path], 'the same api.get path');
            const first = f.walk(f.container,n => n.tagName === 'img')[0];
            assert.match(first.src, /_pm_viewer=/);
            const url = new URL(first.src);url.searchParams.delete('_pm_viewer');
            assert.equal(`${url.pathname}${url.search}`, item.url);
            first.fire('error');
            f.walk(f.container,n => n.tagName === 'button' && n.textContent === 'Retry media')[0].fire('click');
            const second = f.walk(f.container,n => n.tagName === 'img')[0];
            assert.match(second.src, /_pm_viewer=/);assert.notEqual(second.src, first.src, 'retry busts the cache again');
        } finally { view.dispose(); controller.dispose(); parentScope.dispose(); restoreGlobals(); f.restore(); }
    }
});

test('the viewer and its download always use the original, never the thumbnail in any format (#317)', async () => {
    const {rootedFileAdapter, createInspectionController} = await import('../../web/js/workspace/inspection-state.mjs');
    const scope = 'a'.repeat(64);
    for (const extension of ['.webp', '.jxl', '.jpg', '.png']) {
        const item = {root_index:0,path:'folder/a.png',filename:'a.png',root_scope:scope,is_video:false,
            url:`/prompt_manager/images/serve/folder/a.png?root=0&root_scope=${scope}`,
            thumbnail_url:`/prompt_manager/images/serve/thumbnails/folder/a_thumb${extension}?v=3&root=0&root_scope=${scope}`};
        const f = mountedDom(), restoreGlobals = withViewerGlobals(f), parentScope = createScope(), paths = [];
        const controller = createInspectionController({adapter:rootedFileAdapter,api:{get(url) {paths.push(url);return new Promise(() => {});}}});
        const view = module.createMediaViewer({container:f.container,parentScope,inspectionController:controller,adapter:rootedFileAdapter,
            onSelect:entry => controller.select(entry),sender:{open(){}}});
        try {
            view.open({items:[item],selectedKey:'0:folder/a.png'});
            // The metadata read is the original's inspection, never a path under thumbnails/.
            assert.deepEqual(paths, [`/prompt_manager/images/inspect/folder/a.png?root=0&root_scope=${scope}`], extension);
            const media = f.walk(f.container,n => n.tagName === 'img');
            assert.equal(media.length, 1, extension);
            const url = new URL(media[0].src);url.searchParams.delete('_pm_viewer');
            assert.equal(`${url.pathname}${url.search}`, item.url, extension);
            assert.doesNotMatch(media[0].src, /thumbnails/, extension);
            const download = f.walk(f.container,n => n.attributes['aria-label'] === 'Download original')[0];
            if (download) assert.equal(download.href, item.url, extension);
        } finally { view.dispose(); controller.dispose(); parentScope.dispose(); restoreGlobals(); f.restore(); }
    }
});
