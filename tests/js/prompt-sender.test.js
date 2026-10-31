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
    assert.match(s.session.state.message,/#1 Node 1 in Flow - ComfyUI.*Apply/);
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
    await named.session.discover();await named.session.preview();assert.match(named.session.state.message,/Sending to #one Live title in Flow - ComfyUI/);named.session.dispose();
});
test('window order and numbering do not depend on which tab answered first',async()=>{
    const {createSendSession}=await source;const s=await discovered(createSendSession,[window1([pm('1')],'T','b'),window1([pm('2')],'T','a')]);
    assert.deepEqual(s.session.state.hosts.map(host=>[host.hostId,host.label]),[['a','T'],['b','T (window 2)']]);s.session.dispose();
});
const applied=calls=>calls.filter(call=>call[0]==='apply').length;
test('Apply without Preview fetches the ticket and applies in one click',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{discover:async()=>[window1([pm('one')])]});
    await s.session.discover();await s.session.apply();
    assert.equal(s.session.state.phase,'success');assert.deepEqual(s.calls.map(call=>call[0]),['preview','apply']);s.session.dispose();
});
test('a double click applies once, during the send and after it',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{discover:async()=>[window1([pm('one')])]});
    await s.session.discover();const first=s.session.apply();const second=s.session.apply();await Promise.all([first,second]);
    assert.equal(applied(s.calls),1);await s.session.apply();assert.equal(applied(s.calls),1);s.session.dispose();
});
test('Apply after a preview uses its ticket and does not preview again',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession,{discover:async()=>[window1([pm('one')])]});
    await s.session.discover();await s.session.preview();await s.session.apply();
    assert.deepEqual(s.calls.map(call=>call[0]),['preview','apply']);assert.equal(s.session.state.phase,'success');s.session.dispose();
});
test('Apply needs a destination',async()=>{
    const {createSendSession}=await source;const s=setup(createSendSession);
    await s.session.discover();await s.session.apply();assert.equal(s.calls.length,0);s.session.dispose();
});
test('a refusal at the fetched preview says so plainly and writes nothing; Apply can try again',async()=>{
    const {createSendSession}=await source;let refuse=true;
    const s=setup(createSendSession,{discover:async()=>[window1([pm('one')])],preview:async(_,id)=>{if(refuse)throw Error('positive is connected to an upstream input.');return{ticket:'t',target:{id},fields:[{role:'positive',before:'',after:'new'}]};}});
    await s.session.discover();await s.session.apply();
    assert.equal(s.session.state.phase,'error');assert.match(s.session.state.message,/connected/);assert.doesNotMatch(s.session.state.message,/may have applied/);
    refuse=false;await s.session.apply();assert.equal(s.session.state.phase,'success');s.session.dispose();
});
test('a host refusal at apply is a refusal; only a lost reply is an unknown outcome',async()=>{
    const {createSendSession}=await source;
    const s=setup(createSendSession,{discover:async()=>[window1([pm('one')])],apply:async()=>{throw Error('Workflow or prompt changed. Preview again.');}});
    await s.session.discover();await s.session.apply();assert.match(s.session.state.message,/Workflow or prompt changed/);assert.doesNotMatch(s.session.state.message,/may have applied/);s.session.dispose();
    const lost=setup(createSendSession,{discover:async()=>[window1([pm('one')])],apply:async()=>{throw Error('Bridge request timed out');}});
    await lost.session.discover();await lost.session.apply();assert.match(lost.session.state.message,/may have applied/);
    await lost.session.apply();assert.equal(lost.calls.filter(call=>call[0]==='preview').length,1);
    lost.session.selectTarget('one');await lost.session.apply();assert.equal(lost.calls.filter(call=>call[0]==='preview').length,2);lost.session.dispose();
});
test('a workflow changed since discovery refuses before anything is written',async()=>{
    const {createSendSession}=await source;
    for(const op of ['apply','preview']){
        const s=setup(createSendSession,{discover:async()=>[window1([pm('one',['positive'],{revision:'1:4'})])],preview:async(_,id)=>({ticket:'t',revision:'1:5',target:{id},fields:[{role:'positive',before:'',after:'new'}]})});
        await s.session.discover();await s.session[op]();
        assert.equal(s.session.state.phase,'error');assert.match(s.session.state.message,/workflow changed/i);assert.equal(applied(s.calls),0);s.session.dispose();
    }
    const stale=setup(createSendSession,{discover:async()=>[window1([pm('one')])]});await stale.session.discover();await stale.session.apply();assert.equal(stale.session.state.phase,'success');stale.session.dispose();
});
test('two or more eligible nodes are never auto-selected, and each option shows id and title',async()=>{
    const {createSendSession}=await source;const twin=id=>({id,label:'Prompt Manager (+/-)',roles:['positive']});
    const s=await discovered(createSendSession,[window1([twin('72'),twin('73')])]);
    assert.equal(s.session.state.targetId,'');assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.label),['#72 Prompt Manager (+/-)','#73 Prompt Manager (+/-)']);
    await s.session.apply();assert.equal(s.calls.length,0);s.session.dispose();
    const one=await discovered(createSendSession,[window1([twin('72')])]);assert.equal(one.session.state.hosts[0].targets[0].label,'#72 Prompt Manager (+/-)');assert.equal(one.session.state.targetId,'72');one.session.dispose();
});
test('same-named windows and nodes stay distinguishable',async()=>{
    const {createSendSession}=await source;const twin=id=>({id,label:'Same',roles:['positive']});
    const s=await discovered(createSendSession,[window1([twin('7'),twin('8')],'T','a'),window1([pm('1')],'T','b')]);
    assert.deepEqual(s.session.state.hosts.map(host=>host.label),['T','T (window 2)']);
    assert.deepEqual(s.session.state.hosts[0].targets.map(target=>target.label),['#7 Same','#8 Same']);s.session.dispose();
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
// #542: "All N eligible nodes" sends to every eligible node of one window, previewing every node first (Q542-2 C).
function allHost({nodes=['10','9','2'],revision,preview,apply,discover}={}) {
    const calls=[];const sent=new Set();
    const targets=nodes.map(id=>pm(id,['positive'],revision?{revision}:{}));
    const client={
        discover:discover ?? (async()=>[window1(targets)]),
        preview:async(host,id,roles)=>{calls.push(['preview',id]);if(preview)return preview(id,calls,sent);return{ticket:`t-${id}-${calls.length}`,revision,target:{id,label:`Node ${id}`},fields:[{role:'positive',before:sent.has(id)?'new':'old',after:roles.positive}]};},
        apply:async(host,ticket)=>{const id=ticket.split('-')[1];calls.push(['apply',id,ticket]);if(apply)return apply(id,calls);sent.add(id);return{targetId:id,values:{positive:'new'}};},
        dispose:()=>calls.push(['dispose']),
    };
    return {client,calls,sent};
}
async function allSession(createSendSession,options) {
    const host=allHost(options);const session=createSendSession({client:host.client,roles:{positive:'new'},onChange:()=>{}});
    await session.discover();return {...host,session};
}
const order=calls=>calls.filter(call=>call[0]!=='dispose').map(call=>`${call[0]} ${call[1]}`);
test('All is offered only with two or more eligible nodes and is never selected for you',async()=>{
    const {createSendSession,ALL_NODES}=await source;
    const one=await allSession(createSendSession,{nodes:['1']});
    one.session.selectTarget(ALL_NODES);assert.equal(one.session.state.targetId,'');assert.equal(one.calls.length,0);one.session.dispose();
    const two=await allSession(createSendSession,{nodes:['1','2']});
    assert.equal(two.session.state.targetId,'');
    two.session.selectTarget(ALL_NODES);assert.equal(two.session.state.targetId,ALL_NODES);
    assert.match(two.session.state.message,/all 2 eligible nodes in Flow - ComfyUI/);
    await two.session.discover();assert.equal(two.session.state.targetId,'');two.session.dispose();
});
test('Apply to All previews every node first, then sends one after another in node id order',async()=>{
    const {createSendSession,ALL_NODES}=await source;const s=await allSession(createSendSession);
    s.session.selectTarget(ALL_NODES);await s.session.apply();
    assert.deepEqual(order(s.calls),['preview 2','preview 9','preview 10','apply 2','preview 9','apply 9','preview 10','apply 10']);
    assert.equal(s.session.state.phase,'success');assert.equal(s.session.state.message,'Sent to 3 of 3 nodes in Flow - ComfyUI.');
    await s.session.apply();assert.equal(s.calls.filter(call=>call[0]==='apply').length,3);s.session.dispose();
});
test('a double click on Apply to All sends to each node once',async()=>{
    const {createSendSession,ALL_NODES}=await source;const s=await allSession(createSendSession,{nodes:['1','2']});
    s.session.selectTarget(ALL_NODES);await Promise.all([s.session.apply(),s.session.apply()]);
    assert.deepEqual(s.calls.filter(call=>call[0]==='apply').map(call=>call[1]),['1','2']);s.session.dispose();
});
test('a node refusing its preview stops All before anything is written and names the node',async()=>{
    const {createSendSession,ALL_NODES}=await source;
    const s=await allSession(createSendSession,{preview:id=>{if(id==='9')throw Error('positive is connected to an upstream input.');return{ticket:`t-${id}`,target:{id},fields:[{role:'positive',before:'old',after:'new'}]};}});
    s.session.selectTarget(ALL_NODES);await s.session.apply();
    assert.equal(s.calls.filter(call=>call[0]==='apply').length,0);assert.equal(s.session.state.phase,'error');
    assert.match(s.session.state.message,/^#9 Node 9: positive is connected to an upstream input\. Nothing was sent\./);
    assert.equal(s.session.state.outcomeUnknown,false);s.session.dispose();
});
test('a refusal while sending to All is reported by node and the other nodes are still sent',async()=>{
    const {createSendSession,ALL_NODES}=await source;
    const s=await allSession(createSendSession,{apply:id=>{if(id==='9')throw Error('Workflow or prompt changed. Preview again.');return{targetId:id,values:{positive:'new'}};}});
    s.session.selectTarget(ALL_NODES);await s.session.apply();
    assert.deepEqual(s.calls.filter(call=>call[0]==='apply').map(call=>call[1]),['2','9','10']);
    assert.equal(s.session.state.phase,'error');assert.equal(s.session.state.outcomeUnknown,false);
    assert.equal(s.session.state.message,'Sent to 2 of 3 nodes in Flow - ComfyUI. #9 Node 9: Workflow or prompt changed. Preview again.');s.session.dispose();
});
test('a lost reply on one node of All leaves the later nodes unsent and that outcome unknown',async()=>{
    const {createSendSession,ALL_NODES}=await source;
    const s=await allSession(createSendSession,{apply:id=>{if(id==='9')throw Error('Bridge request timed out');return{targetId:id,values:{positive:'new'}};}});
    s.session.selectTarget(ALL_NODES);await s.session.apply();
    assert.deepEqual(s.calls.filter(call=>call[0]==='apply').map(call=>call[1]),['2','9']);
    assert.equal(s.session.state.outcomeUnknown,true);
    assert.match(s.session.state.message,/^Sent to 1 of 3 nodes in Flow - ComfyUI\. No confirmation received from #9 Node 9; the update may have applied\. Not sent: #10 Node 10\. Check ComfyUI, then preview again\. Bridge request timed out$/);
    await s.session.apply();assert.equal(s.calls.filter(call=>call[0]==='apply').length,2);s.session.dispose();
});
test('closing the dialog during All stops after the current node',async()=>{
    const {createSendSession,ALL_NODES}=await source;let release;
    const s=await allSession(createSendSession,{apply:id=>id==='2'?new Promise(done=>release=()=>done({targetId:id,values:{positive:'new'}})):{targetId:id,values:{positive:'new'}}});
    s.session.selectTarget(ALL_NODES);const pending=s.session.apply();
    for(let i=0;i<50 && !release;i++)await tick();assert.ok(release,'the first node was sent');
    s.session.dispose();release();await pending;
    assert.deepEqual(s.calls.filter(call=>call[0]==='apply').map(call=>call[1]),['2']);
});
test('All accepts the revision our own write moved, but not a workflow changed before the run',async()=>{
    const {createSendSession,ALL_NODES}=await source;
    // The host moves the graph version on every write: later nodes preview with the new revision.
    let version=4;
    const bumped=await allSession(createSendSession,{revision:'1:4',preview:id=>({ticket:`t-${id}`,revision:`1:${version}`,target:{id},fields:[{role:'positive',before:'old',after:'new'}]}),apply:id=>{version++;return{targetId:id,values:{positive:'new'}};}});
    bumped.session.selectTarget(ALL_NODES);await bumped.session.apply();
    assert.equal(bumped.session.state.message,'Sent to 3 of 3 nodes in Flow - ComfyUI.');bumped.session.dispose();
    const moved=await allSession(createSendSession,{revision:'1:4',preview:id=>({ticket:`t-${id}`,revision:'1:5',target:{id},fields:[{role:'positive',before:'old',after:'new'}]})});
    moved.session.selectTarget(ALL_NODES);await moved.session.apply();
    assert.match(moved.session.state.message,/workflow changed since this window was found/i);assert.equal(moved.calls.filter(call=>call[0]==='apply').length,0);moved.session.dispose();
});
test('a node whose text changed after the preview pass is refused, not overwritten',async()=>{
    const {createSendSession,ALL_NODES}=await source;let edited=false;
    const s=await allSession(createSendSession,{preview:id=>({ticket:`t-${id}`,target:{id},fields:[{role:'positive',before:id==='10'&&edited?'typed in ComfyUI':'old',after:'new'}]}),apply:id=>{edited=true;return{targetId:id,values:{positive:'new'}};}});
    s.session.selectTarget(ALL_NODES);await s.session.apply();
    assert.deepEqual(s.calls.filter(call=>call[0]==='apply').map(call=>call[1]),['2','9']);
    assert.match(s.session.state.message,/^Sent to 2 of 3 nodes in Flow - ComfyUI\. #10 Node 10: Its prompt changed since the preview\./);s.session.dispose();
});
test('Preview with All shows every node in order, and Apply sends the reviewed first ticket',async()=>{
    const {createSendSession,ALL_NODES}=await source;const s=await allSession(createSendSession);
    s.session.selectTarget(ALL_NODES);await s.session.preview();
    assert.equal(s.session.state.phase,'ready');assert.equal(s.session.state.preview,null);
    assert.deepEqual(s.session.state.previews.map(item=>item.label),['#2 Node 2','#9 Node 9','#10 Node 10']);
    assert.match(s.session.state.message,/Sending to all 3 eligible nodes in Flow - ComfyUI\. Review the complete changes, then apply\./);
    const first=s.session.state.previews[0].preview.ticket;
    await s.session.apply();
    assert.deepEqual(order(s.calls),['preview 2','preview 9','preview 10','apply 2','preview 9','apply 9','preview 10','apply 10']);
    assert.equal(s.calls.find(call=>call[0]==='apply')[2],first);
    s.session.selectTarget('2');assert.deepEqual(s.session.state.previews,[]);s.session.dispose();
});
test('the dialog offers All for two or more nodes and reviews each node under its name',async()=>{
    const {createPromptSender,ALL_NODES}=await source;const {createScope}=await import('../../web/js/shared/toolkit/core.js');
    const dom=fakeDOM();const scope=createScope();
    const {client}=allHost({nodes:['1','2']});
    try {
        createPromptSender({scope,clientFactory:()=>client}).open({positive:'new'});await tick();
        const dialog=dom.body.children.at(-1);const target=dom.find(dialog,node=>node.id==='prompt-sender-target');
        assert.deepEqual(target.children.map(option=>[option.value,option.textContent]),[['','Choose a destination node'],['1','#1 Node 1'],['2','#2 Node 2'],[ALL_NODES,'All 2 eligible nodes']]);
        assert.equal(target.value,'');target.value=ALL_NODES;target.dispatchEvent(new Event('change'));
        dom.find(dialog,node=>node.textContent==='Preview changes').dispatchEvent(new Event('click'));for(let i=0;i<5;i++)await tick();
        const sections=[];(function walk(node){if(node.className==='prompt-sender-node')sections.push(node);node.children.forEach(walk);})(dialog);
        assert.deepEqual(sections.map(section=>section.children[0].textContent),['#1 Node 1','#2 Node 2']);
        dom.find(dialog,node=>node.textContent==='Apply to ComfyUI').dispatchEvent(new Event('click'));for(let i=0;i<10;i++)await tick();
        assert.ok(dom.find(dialog,node=>node.textContent==='Sent to 2 of 2 nodes in Flow - ComfyUI.'));
    } finally {scope.dispose();dom.restore();}
});
