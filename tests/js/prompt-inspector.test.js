import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectPath, promptRoles, createInspectionLoader, mountPromptBlocks } from '../../web/js/workspace/prompt-inspector.mjs';

test('inspection keeps exact physical root and encodes every path component', () => {
    assert.equal(inspectPath({root_index:2,path:'a b/x#%.png'}), '/prompt_manager/images/inspect/a%20b/x%23%25.png?root=2');
});
test('only confirmed strings can be sent, including explicit empty negatives', () => {
    const record = {positive:{status:'resolved',text:'full\npositive'},negative:{status:'resolved',text:''}};
    assert.deepEqual(promptRoles(record), {positive:'full\npositive',negative:''});
    for (const status of ['absent','ambiguous','unresolved','other']) {
        assert.deepEqual(promptRoles({positive:{status,text:'placeholder'},negative:{status:'resolved',text:null}}), {});
    }
    assert.deepEqual(promptRoles(null), {});
});
test('already aborted prompt blocks touch no container DOM or active handlers', () => {
    const parent = new AbortController(); parent.abort();
    const container = new Proxy({}, { get() { assert.fail('aborted mount accessed the container'); } });
    const parentScope = { signal: parent.signal };
    const view = mountPromptBlocks(container, { parentScope, sender: { open() { assert.fail('aborted action'); } } });
    view.update({ positive: { status: 'resolved', text: 'late' } });
    view.setState({ loading: true }); view.dispose();
});
test('superseded requests and disposed loaders cannot replace current metadata', async () => {
    const pending = [], changes = [];
    const loader = createInspectionLoader({get:(url,{signal})=>new Promise(resolve=>pending.push({url,signal,resolve}))}, value=>changes.push(value));
    const first=loader.load({root_index:0,path:'one.png'});
    const second=loader.load({root_index:1,path:'one.png'});
    assert.equal(pending[0].signal.aborted,true);
    pending[1].resolve({prompts:{positive:{status:'resolved',text:'latest'}}}); await second;
    pending[0].resolve({prompts:{positive:{status:'resolved',text:'old'}}}); await first;
    assert.equal(changes.at(-1).prompts.positive.text,'latest');
    const third=loader.load({root_index:0,path:'third.png'}); loader.dispose();
    assert.equal(pending[2].signal.aborted,true);
    pending[2].resolve({prompts:{positive:{status:'resolved',text:'disposed'}}}); await third;
    assert.equal(changes.at(-1).loading,true);
});
test('failed reads report a retryable error and later reads recover', async () => {
    let fail=true; const changes=[];
    const loader=createInspectionLoader({get:async()=>{if(fail)throw Error('bad');return {prompts:{}};}},value=>changes.push(value));
    await loader.load({root_index:0,path:'one.png'}); assert.equal(changes.at(-1).error,true);
    fail=false; await loader.load({root_index:0,path:'one.png'}); assert.deepEqual(changes.at(-1).prompts,{});
    loader.dispose(); await loader.load({root_index:0,path:'one.png'}); assert.equal(changes.at(-1).loading,undefined);
});
