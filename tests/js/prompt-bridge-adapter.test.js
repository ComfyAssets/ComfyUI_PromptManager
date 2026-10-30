const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/prompt-bridge-adapter.js');
function fixture(type='PromptManagerPair') {
    const widgets = ['text','negative_text','prepend_text','append_text','category','tags'].map((name,index) => ({name,value:['old','bad',' start ',' end ','cat','tag'][index],inputEl:{value:['old','bad',' start ',' end ','cat','tag'][index]}}));
    const node = {id:7,type,title:'My prompts',widgets,widgets_values:widgets.map(w=>w.value),inputs:[],_focusedView:{render(){node.rendered=(node.rendered||0)+1;}},setDirtyCanvas(){}};
    const app = {graph:{_nodes:[node],_version:1,setDirtyCanvas(){}},canvas:{}};
    return {app,node,widget:name=>node.widgets.find(w=>w.name===name)};
}
test('lists only explicit PromptManager destinations and roles for all five schemas',async()=>{
    const {createPromptAdapter}=await source;
    const types=['PromptManager','PromptManagerText','PromptManagerFocused','PromptManagerSplit','PromptManagerPair'];
    const nodes=types.map((type,id)=>({...fixture(type).node,id})); nodes.push({...fixture().node,id:8,type:'CLIPTextEncode'});
    const targets=createPromptAdapter({graph:{_nodes:nodes}}).listTargets();
    assert.deepEqual(targets.map(t=>t.type),types);
    assert.deepEqual(targets.map(t=>t.roles),types.map(type=>type==='PromptManagerPair'?['positive','negative']:['positive']));
    assert.equal(targets[0].id,'0'); assert.equal(targets[0].label,'My prompts');
    assert.deepEqual(createPromptAdapter({}).listTargets(),[]);
});
test('each destination says which renderer its window draws nodes with (System › Integrations, S10)',async()=>{
    const {createPromptAdapter}=await source; const {app}=fixture();
    assert.equal(createPromptAdapter(app,{runtime:{LiteGraph:{vueNodesMode:true}}}).listTargets()[0].renderer,'nodes2');
    assert.equal(createPromptAdapter(app,{runtime:{LiteGraph:{vueNodesMode:false}}}).listTargets()[0].renderer,'classic');
    assert.equal(createPromptAdapter(app,{runtime:{LiteGraph:{}}}).listTargets()[0].renderer,'classic');
    assert.equal(Object.hasOwn(createPromptAdapter(app,{runtime:{}}).listTargets()[0],'renderer'),false);
    // Read at every listing: a window that switches renderer reports the new one.
    const runtime={LiteGraph:{vueNodesMode:false}}; const adapter=createPromptAdapter(app,{runtime});
    runtime.LiteGraph.vueNodesMode=true;
    assert.equal(adapter.listTargets()[0].renderer,'nodes2');
});
test('previews exact composition, updates requested visible/serialized values, preserves other fields',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture(); const adapter=createPromptAdapter(app);
    const {preview,snapshot}=adapter.preview('7',{positive:'new'});
    assert.deepEqual(preview.fields,[{role:'positive',before:'old',after:'new'}]);
    assert.deepEqual(preview.composition,{prepend:' start ',append:' end ',prependConnected:false,appendConnected:false,before:'start old end',after:'start new end'});
    let callback; widget('text').callback=(value,canvas,target)=>{callback=[value,canvas,target];};
    assert.deepEqual(adapter.apply(snapshot),{targetId:'7',values:{positive:'new'}});
    assert.equal(widget('text').inputEl.value,'new'); assert.equal(node.widgets_values[0],'new'); assert.equal(node.rendered,1);
    assert.deepEqual(callback,['new',app.canvas,node]);
    for(const [name,value] of [['negative_text','bad'],['prepend_text',' start '],['append_text',' end '],['category','cat'],['tags','tag']]) assert.equal(widget(name).value,value);
    assert.throws(()=>adapter.apply(snapshot),/snapshot|preview/i);
});
test('omitted positive stays intact, empty negative clears explicitly',async()=>{
    const {createPromptAdapter}=await source; const {app,widget}=fixture(); const adapter=createPromptAdapter(app);
    adapter.apply(adapter.preview('7',{negative:''}).snapshot);
    assert.equal(widget('text').value,'old'); assert.equal(widget('negative_text').value,'');
});
test('rejects invalid roles, unsupported targets, missing/connected fields before mutation',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture('PromptManagerText'); const adapter=createPromptAdapter(app);
    for(const roles of [null,{},[],{positive:2},{positive:'x',other:'y'},{negative:'x'},Object.create({positive:'x'})]) assert.throws(()=>adapter.preview('7',roles));
    assert.throws(()=>adapter.preview('99',{positive:'x'}));
    node.inputs=[{name:'text',link:0}]; assert.throws(()=>adapter.preview('7',{positive:'x'}),/connect/i);
    node.inputs=[]; node.widgets=node.widgets.filter(w=>w.name!=='text'); assert.throws(()=>adapter.preview('7',{positive:'x'}),/widget/i);
    assert.equal(widget('negative_text').value,'bad');
});
test('connected composition is unknown and never overwritten',async()=>{
    const {createPromptAdapter}=await source; const {app,node}=fixture(); node.inputs=[{name:'prepend_text',link:0}];
    const adapter=createPromptAdapter(app), result=adapter.preview('7',{positive:'x'});
    assert.equal(result.preview.composition.before,null); assert.equal(result.preview.composition.after,null); assert.equal(result.preview.composition.prependConnected,true);
    adapter.apply(result.snapshot); assert.equal(node.inputs[0].link,0);
});
test('rejects stale workflow, node, widget, values, serialization, visible values, and connections',async()=>{
    const {createPromptAdapter}=await source;
    const mutations=[f=>{f.app.graph={...f.app.graph};},f=>{f.app.graph._version++;},f=>{f.app.graph._nodes=[{...f.node}];},f=>{f.node.type='PromptManagerText';},f=>{f.node.widgets[0]={...f.node.widgets[0]};},f=>{f.widget('negative_text').value='changed';},f=>{f.widget('text').inputEl.value='typing';},f=>{f.node.widgets_values[0]='changed';},f=>{f.node.inputs.push({name:'text',link:2});},f=>{f.node.inputs.push({name:'append_text',link:2});}];
    for(const mutate of mutations){const f=fixture(),adapter=createPromptAdapter(f.app),{snapshot}=adapter.preview('7',{positive:'new',negative:'newbad'}); mutate(f); assert.throws(()=>adapter.apply(snapshot),/changed|stale/i); assert.notEqual(f.widget('text').value,'new');}
});
test('validates both roles before writing and rejects forged snapshots',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture(); const adapter=createPromptAdapter(app),{snapshot}=adapter.preview('7',{positive:'new',negative:'newbad'});
    node.inputs.push({name:'negative_text',link:3}); assert.throws(()=>adapter.apply(snapshot)); assert.equal(widget('text').value,'old'); assert.throws(()=>adapter.apply({}));
});
test('callback failure or transformed value rolls all requested values back without an acknowledgement',async()=>{
    const {createPromptAdapter}=await source;
    for(const fail of ['throw','transform','visible','serialization']) {
        const {app,node,widget}=fixture(),adapter=createPromptAdapter(app);
        widget('negative_text').callback=()=>{if(fail==='throw') throw new Error('callback failed'); if(fail==='transform') widget('text').value='altered'; if(fail==='visible') widget('text').inputEl.value='altered'; if(fail==='serialization') node.widgets_values[0]='altered';};
        assert.throws(()=>adapter.apply(adapter.preview('7',{positive:'new',negative:'newbad'}).snapshot),/failed|changed|match/i);
        assert.equal(widget('text').value,'old'); assert.equal(widget('text').inputEl.value,'old'); assert.equal(node.widgets_values[0],'old'); assert.equal(widget('negative_text').value,'bad');
    }
});
test('snapshots isolate caller mutations and require the originating adapter',async()=>{
    const {createPromptAdapter}=await source; const {app,widget}=fixture(),adapter=createPromptAdapter(app),roles={positive:'new'};
    const result=adapter.preview('7',roles); roles.positive='changed'; result.preview.fields[0].after='changed';
    assert.throws(()=>createPromptAdapter(app).apply(result.snapshot)); adapter.apply(result.snapshot); assert.equal(widget('text').value,'new');
});
test('applies positive text for every supported schema without a serialization array',async()=>{
    const {createPromptAdapter}=await source;
    for(const type of ['PromptManager','PromptManagerText','PromptManagerFocused','PromptManagerSplit','PromptManagerPair']) {
        const {app,node,widget}=fixture(type); delete node.widgets_values; delete node._focusedView; delete node.setDirtyCanvas; delete app.graph.setDirtyCanvas;
        const adapter=createPromptAdapter(app); adapter.apply(adapter.preview('7',{positive:''}).snapshot);
        assert.equal(widget('text').value,''); assert.equal(node.widgets_values[0],'');
    }
});
test('matches Python whitespace composition and validates the combined one MiB UTF-8 role limit',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture(); node.title=''; widget('prepend_text').value=' '; widget('append_text').value='';
    const adapter=createPromptAdapter(app);
    assert.equal(adapter.preview('7',{positive:'  x  '}).preview.composition.after,'  x  ');
    assert.equal(adapter.preview('7',{positive:''}).preview.composition.after,'');
    assert.equal(adapter.preview('7',{positive:'x'.repeat(1_048_576)}).preview.fields[0].after.length,1_048_576);
    assert.throws(()=>adapter.preview('7',{positive:'x'.repeat(1_048_577)}));
    assert.throws(()=>adapter.preview('7',{positive:'x'.repeat(524_288),negative:'y'.repeat(524_289)}));
    assert.throws(()=>adapter.preview('7',{positive:'é'.repeat(524_289)}));
    assert.equal(adapter.preview('7',{positive:'é'.repeat(524_288)}).preview.fields[0].after.length,524_288);
    assert.throws(()=>adapter.preview('7',{get positive(){throw new Error('Getter must not run');}}),/strings/i);
    assert.throws(()=>adapter.preview('7',{positive:'x',[Symbol('unknown')]:'y'}));
    assert.equal(adapter.preview('7',Object.assign(Object.create(null),{positive:'x'})).preview.fields[0].after,'x');
    assert.equal(adapter.listTargets()[0].label,'PromptManagerPair');
});
test('callback changes to graph, connections, widgets or protected prompt fields fail and restore fields',async()=>{
    const {createPromptAdapter}=await source;
    const mutations=[f=>{f.app.graph={...f.app.graph};},f=>{f.node.inputs=[{name:'text',link:4}];},f=>{f.node.widgets[0]={...f.node.widgets[0]};},f=>{f.widget('negative_text').value='unexpected';},f=>{f.widget('prepend_text').value='unexpected';},f=>{f.widget('category').value='unexpected';}];
    for(const mutate of mutations){const f=fixture(),adapter=createPromptAdapter(f.app); f.widget('text').callback=()=>mutate(f); assert.throws(()=>adapter.apply(adapter.preview('7',{positive:'new'}).snapshot),/failed/i); assert.equal(f.widget('negative_text').value,'bad'); assert.equal(f.widget('prepend_text').value,' start '); assert.equal(f.widget('category').value,'cat');}
});
test('minimal classic node without optional widgets remains a valid destination',async()=>{
    const {createPromptAdapter}=await source; const {app,node}=fixture('PromptManager'); node.widgets=node.widgets.slice(0,1); node.widgets_values=['old']; delete node.widgets[0].inputEl;
    const adapter=createPromptAdapter(app), result=adapter.preview('7',{positive:'new'});
    assert.equal(result.preview.composition.before,'old'); assert.equal(result.preview.composition.after,'new');
    assert.deepEqual(adapter.apply(result.snapshot).values,{positive:'new'});
});
test('callback removal of serialization or replacement of visible controls cannot acknowledge success',async()=>{
    const {createPromptAdapter}=await source;
    for(const mutate of [f=>{delete f.node.widgets_values;},f=>{f.widget('text').inputEl={value:'new'};},f=>{f.node.id=8;},f=>{f.node.widgets.reverse();},f=>{delete f.node.widgets;},f=>{f.node.widgets=[];}]) {
        const f=fixture(),adapter=createPromptAdapter(f.app); const originalInput=f.widget('text').inputEl;
        f.widget('text').callback=()=>mutate(f);
        assert.throws(()=>adapter.apply(adapter.preview('7',{positive:'new'}).snapshot),/failed/i);
        assert.equal(f.widget('text').value,'old'); assert.equal(originalInput.value,'old'); assert.equal(f.node.widgets_values[0],'old');
    }
});
test('benign host serialization may establish or normalize equivalent serialized values after preview',async()=>{
    const {createPromptAdapter}=await source;
    for (const initial of ['absent','sparse','old']) {
        const {app,node,widget}=fixture();
        if(initial==='absent') delete node.widgets_values;
        if(initial==='sparse') node.widgets_values=[];
        if(initial==='old') node.widgets_values[0]='earlier serialized draft';
        const adapter=createPromptAdapter(app),{snapshot}=adapter.preview('7',{positive:'new',negative:'newbad'});
        // ComfyUI serialize() materializes the live widget values without editing them.
        node.widgets_values=node.widgets.map(item=>item.inputEl ? item.inputEl.value : item.value);
        assert.deepEqual(adapter.apply(snapshot).values,{positive:'new',negative:'newbad'});
        assert.equal(widget('text').value,'new');
    }
});
test('serialization normalization still rejects a different execution value',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture(); delete node.widgets_values;
    const adapter=createPromptAdapter(app),{snapshot}=adapter.preview('7',{positive:'new'});
    node.widgets_values=node.widgets.map(item=>item.value); node.widgets_values[0]='unapproved';
    assert.throws(()=>adapter.apply(snapshot),/changed/i); assert.equal(widget('text').value,'old');
});
test('callback failure preserves serialization normalized between preview and apply',async()=>{
    const {createPromptAdapter}=await source; const {app,node,widget}=fixture(); node.widgets_values[0]='earlier draft';
    const adapter=createPromptAdapter(app),{snapshot}=adapter.preview('7',{positive:'new'});
    node.widgets_values=node.widgets.map(item=>item.value); widget('text').callback=()=>{throw new Error('callback failed');};
    assert.throws(()=>adapter.apply(snapshot),/failed/); assert.equal(node.widgets_values[0],'old'); assert.equal(widget('text').value,'old');
});

test('listTargets reports which roles are linked to an upstream node',async()=>{
    const {createPromptAdapter}=await import('../../web/js/prompt-bridge-adapter.js');
    const widgets=[{name:'text',value:''},{name:'negative_text',value:''}];
    const app={graph:{_nodes:[{id:1,type:'PromptManagerPair',widgets,inputs:[{name:'text',link:null},{name:'negative_text',link:4}]},{id:2,type:'PromptManager',widgets,inputs:[]}]}};
    const [pair,plain]=createPromptAdapter(app,{runtime:{}}).listTargets();
    assert.deepEqual(pair.linkedRoles,['negative']);assert.deepEqual(plain.linkedRoles,[]);
});

test('the revision moves when the graph is edited and when a load replaces the node, even at the same version',async()=>{
    const {createPromptAdapter}=await import('../../web/js/prompt-bridge-adapter.js');
    const node=()=>({id:1,type:'PromptManager',widgets:[{name:'text',value:''}],inputs:[]});
    const app={graph:{_version:9,_nodes:[node()]}};
    const adapter=createPromptAdapter(app,{runtime:{}});
    const first=adapter.listTargets()[0].revision;
    assert.equal(adapter.preview('1',{positive:'x'}).preview.revision,first);
    app.graph._version=10;const edited=adapter.listTargets()[0].revision;assert.notEqual(edited,first);
    app.graph={_version:10,_nodes:[node()]};assert.notEqual(adapter.listTargets()[0].revision,edited);
});
