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
    await s.session.discover(); assert.equal(s.session.state.hostId,'');assert.equal(s.session.state.targetId,'');
    await s.session.preview(); assert.equal(s.calls.length,0);
    s.session.selectHost('host');assert.equal(s.session.state.targetId,''); s.session.selectTarget('one');
    await s.session.apply();assert.equal(s.calls.length,0);
    await s.session.preview();assert.equal(s.session.state.phase,'ready');assert.equal(s.session.state.preview.composition.after,'prefix new');
    await s.session.apply();assert.equal(s.session.state.phase,'success');assert.equal(s.calls.filter(call=>call[0]==='apply').length,1);
    await s.session.apply();assert.equal(s.calls.filter(call=>call[0]==='apply').length,1); s.session.dispose();
});
test('pair roles filter incompatible nodes and do not silently choose the sole destination', async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{}, {positive:'',negative:'bad'});
    await s.session.discover();assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.id),['pair']);
    s.session.selectHost('host');s.session.selectTarget('one');assert.equal(s.session.state.targetId,'');
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
    const {createSendSession}=await source;const s=setup(createSendSession,{discover:async()=>[]});await s.session.discover();assert.match(s.session.state.message,/No compatible/i);s.session.dispose();
    const failed=setup(createSendSession,{discover:async()=>{throw Error('unavailable');}});await failed.session.discover();assert.match(failed.session.state.message,/unavailable/);failed.session.dispose();
    const stale=setup(createSendSession,{preview:async()=>{throw Error('Workflow changed. Preview again.');}});await stale.session.discover();stale.session.selectHost('host');stale.session.selectTarget('one');await stale.session.preview();assert.match(stale.session.state.message,/Workflow changed/);stale.session.dispose();
});
test('rediscovery invalidates selections and old discovery cannot replace new results',async()=>{
    const {createSendSession}=await source;let resolve;let count=0;const s=setup(createSendSession,{discover:()=>++count===1?new Promise(done=>resolve=done):Promise.resolve(hosts)});
    const first=s.session.discover();await s.session.discover();resolve([]);await first;assert.equal(s.session.state.hosts.length,1);
    s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();await s.session.discover();assert.equal(s.session.state.hostId,'');assert.equal(s.session.state.preview,null);s.session.dispose();
});
test('pending apply explains closing cannot undo the sent update',async()=>{
    const {createSendSession}=await source;let done;const s=setup(createSendSession,{apply:()=>new Promise(resolve=>done=resolve)});
    await s.session.discover();s.session.selectHost('host');s.session.selectTarget('one');await s.session.preview();const pending=s.session.apply();
    assert.match(s.session.state.message,/Closing this dialog does not undo/i);s.session.dispose();done({targetId:'one',values:{positive:'new'}});await pending;
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
        assert.equal(host.value,'');assert.equal(target.value,'');host.value='host';host.dispatchEvent(new Event('change'));target.value='one';target.dispatchEvent(new Event('change'));
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
