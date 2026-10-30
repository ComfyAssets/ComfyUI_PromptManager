const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/prompt-sender.mjs');
const hosts = [{hostId:'host',label:'Workflow',targets:[{id:'one',label:'Positive',roles:['positive']},{id:'pair',label:'Pair',roles:['positive','negative']}]}];
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(createSendSession, overrides = {}, roles = {positive:'new'}) {
    const calls = [];
    const client = {discover:async () => hosts, preview:async (...args) => {calls.push(['preview',...args]);return {ticket:'ticket',target:{id:args[1]},fields:[{role:'positive',before:'old',after:'new'}],composition:{before:'prefix old',after:'prefix new'}};},apply:async (...args) => {calls.push(['apply',...args]);return {targetId:'one',values:{positive:'new'}};},dispose:() => calls.push(['dispose']),...overrides};
    const states = []; const session = createSendSession({client,roles,onChange:state=>states.push(state)});
    return {client,calls,states,session};
}
test('sender requires explicit compatible host and target, preview, then acknowledged apply', async () => {
    const {createSendSession} = await source; const s=setup(createSendSession);
    await s.session.discover(); assert.equal(s.session.state.hostId,'host');assert.equal(s.session.state.targetId,'');
    await s.session.preview(); assert.equal(s.calls.length,0);
    s.session.selectTarget('one');
    await s.session.apply();assert.equal(s.calls.length,0);
    await s.session.preview();assert.equal(s.session.state.phase,'ready');assert.equal(s.session.state.preview.composition.after,'prefix new');
    await s.session.apply();assert.equal(s.session.state.phase,'success');assert.equal(s.calls.filter(call=>call[0]==='apply').length,1);
    await s.session.apply();assert.equal(s.calls.filter(call=>call[0]==='apply').length,1); s.session.dispose();
});
test('pair roles filter incompatible nodes and select the sole destination', async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{}, {positive:'',negative:'bad'});
    await s.session.discover();assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.id),['pair']);
    assert.equal(s.session.state.targetId,'pair');s.session.selectTarget('one');assert.equal(s.session.state.targetId,'');
    s.session.selectTarget('pair');assert.equal(s.session.state.targetId,'pair');s.session.dispose();
});
test('late previews are ignored after target changes; aborted and disposed work cannot update state',async()=>{
    const {createSendSession}=await source;let resolve;const s=setup(createSendSession,{preview:()=>new Promise(done=>resolve=done)});
    await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');const pending=s.session.preview();
    s.session.selectTarget('pair');resolve({ticket:'old'});await pending;assert.equal(s.session.state.preview,null);
    s.session.selectTarget('one');const pending2=s.session.preview();s.session.dispose();const count=s.states.length;resolve({ticket:'old'});await pending2;assert.equal(s.states.length,count);
});
test('unknown apply outcome never retries a ticket and requires a new preview',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{apply:async()=>{throw Error('timed out');}});
    await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();await s.session.apply();
    assert.equal(s.session.state.phase,'error');assert.match(s.session.state.message,/may have applied/i);assert.equal(s.session.state.preview,null);
    await s.session.apply();assert.equal(s.session.state.phase,'error');await s.session.preview();assert.equal(s.session.state.phase,'ready');s.session.dispose();
});
test('only matching explicit acknowledgements count as success',async()=>{
    const {createSendSession}=await source;
    for(const ack of [undefined,{targetId:'other',values:{positive:'new'}},{targetId:'one',values:{positive:'wrong'}}]){
        const s=setup(createSendSession,{apply:async()=>ack});await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();await s.session.apply();assert.equal(s.session.state.phase,'error');s.session.dispose();
    }
});
test('discovery, empty results and stale preview errors remain actionable',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{discover:async()=>[]});await s.session.discover();assert.match(s.session.state.message,/No ComfyUI window answered/i);s.session.dispose();
    const failed=setup(createSendSession,{discover:async()=>{throw Error('unavailable');}});await failed.session.discover();assert.match(failed.session.state.message,/unavailable/);failed.session.dispose();
    const stale=setup(createSendSession,{preview:async()=>{throw Error('Workflow changed. Preview again.');}});await stale.session.discover();stale.session.selectHost('host');stale.session.selectTarget('one');await stale.session.preview();assert.match(stale.session.state.message,/Workflow changed/);stale.session.dispose();
});
test('rediscovery invalidates selections and old discovery cannot replace new results',async()=>{
    const {createSendSession}=await source;let resolve;let count=0;const s=setup(createSendSession,{discover:()=>++count===1?new Promise(done=>resolve=done):Promise.resolve(hosts)});
    const first=s.session.discover();await s.session.discover();resolve([]);await first;assert.equal(s.session.state.hosts.length,1);
    s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();await s.session.discover();assert.equal(s.session.state.hostId,'host');assert.equal(s.session.state.targetId,'');assert.equal(s.session.state.preview,null);s.session.dispose();
});
test('pending apply explains closing cannot undo the sent update',async()=>{
    const {createSendSession}=await source;let done;const s=setup(createSendSession,{apply:()=>new Promise(resolve=>done=resolve)});
    await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();const pending=s.session.apply();
    assert.match(s.session.state.message,/Closing this dialog does not undo/i);s.session.dispose();done({targetId:'one',values:{positive:'new'}});await pending;
});
const window1=(targets,label='Flow - ComfyUI',hostId='a')=>({hostId,label,targets});
const pm=(id,roles=['positive'],extra={})=>({id,label:`Node ${id}`,roles,...extra});
async function discovered(createSendSession,found,roles={positive:'new'}) {
    const s=setup(createSendSession,{discover:async()=>found},roles);await s.session.discover();return s;
}
test('one window with one eligible node is selected so Preview is available at once',async()=>{
    const {createSendSession}=await source;const s=await discovered(createSendSession,[window1([pm('1')])]);
    assert.equal(s.session.state.hostId,'a');assert.equal(s.session.state.targetId,'1');
    assert.match(s.session.state.message,/Node 1 in Flow - ComfyUI.*Preview changes/);
    await s.session.preview();assert.equal(s.session.state.phase,'ready');s.session.dispose();
});
test('several windows are left to choose, and choosing one with a single node selects that node',async()=>{
    const {createSendSession}=await source;const s=await discovered(createSendSession,[window1([pm('1')],'A','a'),window1([pm('2'),pm('3')],'B','b')]);
    assert.equal(s.session.state.hostId,'');assert.equal(s.session.state.targetId,'');assert.match(s.session.state.message,/Choose a ComfyUI window/);
    s.session.selectHost('a');assert.equal(s.session.state.targetId,'1');
    s.session.selectHost('b');assert.equal(s.session.state.targetId,'');assert.match(s.session.state.message,/Choose a destination node in B/);
    s.session.selectHost('');assert.match(s.session.state.message,/Choose a ComfyUI window/);
    s.session.selectHost('b');s.session.selectTarget('2');s.session.selectTarget('');assert.match(s.session.state.message,/Choose a destination node/);s.session.dispose();
});
test('a window that cannot receive says why, naming the window and the node',async()=>{
    const {createSendSession}=await source;
    let s=await discovered(createSendSession,[window1([])]);
    assert.match(s.session.state.message,/Flow - ComfyUI has no Prompt Manager node/);assert.equal(s.session.state.hosts.length,0);s.session.dispose();
    s=await discovered(createSendSession,[window1([]),window1([],'Other','b')]);
    assert.match(s.session.state.message,/None of the 2 ComfyUI windows has a Prompt Manager node/);s.session.dispose();
    s=await discovered(createSendSession,[window1([pm('1',['positive'],{linkedRoles:['positive']})])]);
    assert.match(s.session.state.message,/Node 1 in Flow - ComfyUI has its positive prompt linked/);assert.equal(s.session.state.hosts.length,0);s.session.dispose();
    s=await discovered(createSendSession,[window1([pm('1')])],{positive:'p',negative:'n'});
    assert.match(s.session.state.message,/Flow - ComfyUI has no Prompt Manager node that accepts a positive and negative prompt.*\(\+\/-\)/);s.session.dispose();
});
test('a linked node is not offered beside an eligible one, and an unlinked role still is',async()=>{
    const {createSendSession}=await source;
    let s=await discovered(createSendSession,[window1([pm('1',['positive'],{linkedRoles:['positive']}),pm('2')])]);
    assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.id),['2']);assert.equal(s.session.state.targetId,'2');s.session.dispose();
    s=await discovered(createSendSession,[window1([pm('1',['positive','negative'],{linkedRoles:['negative']})])]);
    assert.equal(s.session.state.targetId,'1');s.session.dispose();
});
test('an auto-selected destination says how many other nodes cannot receive, and the review names the destination',async()=>{
    const {createSendSession}=await source;
    const s=await discovered(createSendSession,[window1([pm('2')],'A','a'),window1([pm('1',['positive'],{linkedRoles:['positive']})],'B','b')]);
    assert.equal(s.session.state.targetId,'2');assert.match(s.session.state.message,/1 other Prompt Manager node cannot receive/);
    await s.session.preview();s.session.dispose();
    const named=setup(createSendSession,{discover:async()=>[window1([pm('one')])],preview:async(_,id)=>({ticket:'t',target:{id,label:'Live title'},fields:[{role:'positive',before:'',after:'new'}]})});
    await named.session.discover();await named.session.preview();assert.match(named.session.state.message,/Sending to Live title in Flow - ComfyUI/);named.session.dispose();
});
test('window order and numbering do not depend on which tab answered first',async()=>{
    const {createSendSession}=await source;const s=await discovered(createSendSession,[window1([pm('1')],'T','b'),window1([pm('2')],'T','a')]);
    assert.deepEqual(s.session.state.hosts.map(host=>[host.hostId,host.label]),[['a','T'],['b','T (window 2)']]);s.session.dispose();
});
test('same-named windows and nodes stay distinguishable',async()=>{
    const {createSendSession}=await source;const twin=id=>({id,label:'Same',roles:['positive']});
    const s=await discovered(createSendSession,[window1([twin('7'),twin('8')],'T','a'),window1([pm('1')],'T','b')]);
    assert.deepEqual(s.session.state.hosts.map(host=>host.label),['T','T (window 2)']);
    assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.label),['Same #7','Same #8']);s.session.dispose();
});
function fakeDOM() {
    class Node extends EventTarget {
        constructor(tag){super();this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.value='';this.textContent='';this.isConnected=true;this.open=false;}
        append(...children){this.children.push(...children);}
        add(option){this.append(option);}
        replaceChildren(...children){this.children=children;}
        setAttribute(key,value){this.attributes[key]=value;}
        showModal(){this.open=true;}
        close(){this.open=false;this.dispatchEvent(new Event('close'));}
        remove(){this.isConnected=false;}
        focus(){this.focused=true;}
    }
    const previous={document:global.document,window:global.window,Option:global.Option};
    const body=new Node('body');
    global.document={body,createElement:tag=>new Node(tag),createElementNS:(namespace,tag)=>new Node(tag)};
    global.Option=class extends Node {constructor(text,value){super('option');this.textContent=text;this.value=value;}};
    global.window={location:new URL('https://example.test/prefix/prompt_manager/workspace'),ApiClient:{resolveUrl:()=>'/prefix/prompt_manager/'}};
    const find=(root,predicate)=>predicate(root)?root:root.children.map(child=>find(child,predicate)).find(Boolean);
    return {body,Node,find,restore(){Object.assign(global,previous);}};
}
test('native sender dialog uses prefixed namespace, full safe text, explicit choices and owned cleanup',async()=>{
    const {createPromptSender,createSendButton}=await source;
    const {createScope}=await import('../../web/js/shared/toolkit/core.js');
    const dom=fakeDOM();const scope=createScope();const trigger=new dom.Node('button');let options,disposed=0;
    const client={discover:async()=>hosts,preview:async()=>({ticket:'t',target:{id:'one'},fields:[{role:'positive',before:'',after:'<unsafe>new</unsafe>'}],composition:{prepend:'prefix',append:'suffix',before:null,after:null,prependConnected:true}}),apply:async()=>({targetId:'one',values:{positive:'<unsafe>new</unsafe>'}}),dispose:()=>disposed++};
    try {
        const button=createSendButton('Send positive prompt');assert.equal(button.attributes['aria-label'],'Send positive prompt');assert.equal(button.children[0].attributes['aria-hidden'],'true');
        const sender=createPromptSender({scope,clientFactory:value=>{options=value;return client;}});sender.open({positive:'<unsafe>new</unsafe>'},trigger);await tick();
        assert.deepEqual(options,{namespace:'/prefix/',origin:'https://example.test'});
        const dialog=dom.body.children.at(-1);assert.equal(dialog.open,true);assert.equal(dialog.attributes['aria-label'],'Send to ComfyUI');
        const host=dom.find(dialog,node=>node.id==='prompt-sender-host');const target=dom.find(dialog,node=>node.id==='prompt-sender-target');
        assert.equal(host.value,'host');assert.equal(target.value,'');target.value='one';target.dispatchEvent(new Event('change'));
        dom.find(dialog,node=>node.textContent==='Preview changes').dispatchEvent(new Event('click'));await tick();
        assert.ok(dom.find(dialog,node=>node.tagName==='code' && node.textContent==='(empty)'));assert.ok(dom.find(dialog,node=>node.tagName==='code' && node.textContent==='<unsafe>new</unsafe>'));
        const apply=dom.find(dialog,node=>node.textContent==='Apply to ComfyUI');assert.equal(apply.className,'ca-primary');assert.equal(apply.disabled,false);apply.dispatchEvent(new Event('click'));await tick();
        assert.ok(dom.find(dialog,node=>node.textContent==='Sent to ComfyUI.'));scope.dispose();assert.equal(dialog.isConnected,false);assert.equal(trigger.focused,true);assert.equal(disposed,1);
        sender.open({positive:'new'},trigger);assert.equal(dom.body.children.length,1);
    } finally {scope.dispose();dom.restore();}
});
test('unsupported client or deployment errors are visible and close stays available',async()=>{
    const {createPromptSender}=await source;const {createScope}=await import('../../web/js/shared/toolkit/core.js');const dom=fakeDOM();
    try {
        for(const baseUrl of ['https://other.test/prompt_manager/','/prefix/not-prompt-manager/','/prefix/prompt_manager/']) {
            const scope=createScope();const sender=createPromptSender({scope,baseUrl,clientFactory:()=>{throw Error('BroadcastChannel unavailable');}});
            sender.open({positive:'new'});const dialog=dom.body.children.at(-1);const alert=dom.find(dialog,node=>node.attributes.role==='alert');assert.match(alert.textContent,/unavailable|Invalid/);
            dom.find(dialog,node=>node.textContent==='Close').dispatchEvent(new Event('click'));assert.equal(dialog.isConnected,false);scope.dispose();
        }
    } finally {dom.restore();}
});
test('approval requires an exact destination and complete unambiguous matching preview',async()=>{
    const {createSendSession}=await source;
    const valid={ticket:'ticket',target:{id:'one'},fields:[{role:'positive',before:'old',after:'new'}]};
    for(const preview of [null,{}, {...valid,ticket:''},{...valid,target:{id:'other'}},{...valid,fields:[]},{...valid,fields:new Array(1)},{...valid,fields:[null]},{...valid,fields:[...valid.fields,...valid.fields]},{...valid,fields:[{role:'negative',before:'old',after:'new'}]},{...valid,fields:[{role:'positive',before:null,after:'new'}]},{...valid,fields:[{role:'positive',before:'old',after:'unexpected'}]}]) {
        const s=setup(createSendSession,{preview:async()=>preview});await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();
        assert.equal(s.session.state.phase,'error');assert.equal(s.session.state.preview,null);await s.session.apply();assert.equal(s.calls.filter(call=>call[0]==='apply').length,0);s.session.dispose();
    }
});
test('acknowledgement cannot claim an extra role beyond approved changes',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{apply:async()=>({targetId:'one',values:{positive:'new',negative:'also changed'}})});
    await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();await s.session.apply();assert.equal(s.session.state.phase,'error');assert.match(s.session.state.message,/unexpected acknowledgement/);s.session.dispose();
});
