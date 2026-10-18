import test from 'node:test';
import assert from 'node:assert/strict';

const module = await import('../../web/js/workspace/viewer-state.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
function session(options) {
    assert.equal(typeof module.createViewerSession, 'function', 'viewer session contract is exported');
    return module.createViewerSession(options);
}
const entries = () => Array.from({length: 161}, (_, index) => ({root_index: index % 2,
    path: `folder/${index}.png`, filename: `${index}.png`, root_scope: 'a'.repeat(64),
    thumbnail_url: `/prompt_manager/images/serve/thumbnails/${index}.png?root=${index % 2}&v=17&root_scope=${'a'.repeat(64)}`,
    url: `/prompt_manager/images/serve/folder/${index}.png?root=${index % 2}&root_scope=${'a'.repeat(64)}`}));

test('viewer freezes loaded identities and wraps across unmounted entries', () => {
    const items = entries(), updates = [];
    const view = session({items, initialKey: '0:folder/0.png', onChange: (state, entry) => updates.push({state, entry})});
    items[160].url = '/wrong-root'; items[160].thumbnail_url = '/wrong-version?v=18';items[160].root_scope = 'b'.repeat(64); items.reverse(); items.push(items[0]);
    view.navigate(-1);
    assert.deepEqual(view.state, {key:'0:folder/160.png',index:160,total:161,phase:'stopped',requested:false,mediaReady:false});
    assert.equal(updates.at(-1).entry.url, `/prompt_manager/images/serve/folder/160.png?root=0&root_scope=${'a'.repeat(64)}`);
    assert.equal(updates.at(-1).entry.root_scope, 'a'.repeat(64));
    assert.equal(updates.at(-1).entry.thumbnail_url, `/prompt_manager/images/serve/thumbnails/160.png?root=0&v=17&root_scope=${'a'.repeat(64)}`);
    assert.ok(Object.isFrozen(updates.at(-1).entry)); assert.ok(Object.isFrozen(view.state));
    view.navigate(1); assert.equal(view.state.key, '0:folder/0.png');
    view.navigate(1); assert.equal(view.state.key, '1:folder/1.png');
    view.dispose();
});

test('viewer rejects empty duplicate or unknown identity', () => {
    for (const [items, initialKey] of [[[], '0:a.png'], [[{path:'a.png'}], 'undefined:a.png'],
        [[{root_index:0,path:''}], '0:'], [[{root_index:0,path:'a.png'},{root_index:0,path:'a.png'}], '0:a.png'],
        [entries(), '0:missing.png']]) assert.throws(() => session({items,initialKey}), TypeError);
    const view = session({items:entries(),initialKey:'0:folder/0.png'});
    for (const direction of [0,2,-2,'1',null]) assert.throws(() => view.navigate(direction), TypeError);
    assert.equal(view.state.index, 0); view.dispose();
});

test('viewer ignores stale readiness after navigation and disposal', () => {
    let calls = 0;
    const view = session({items:entries(),initialKey:'0:folder/0.png',onChange:() => calls++,
        setTimer:() => assert.fail('Task4 must not start a timer'),clearTimer:() => assert.fail('No timer exists')});
    view.ready('0:folder/0.png'); assert.equal(view.state.mediaReady,true);
    view.navigate(1); view.failed('0:folder/0.png'); view.ready('0:folder/0.png');
    assert.equal(view.state.mediaReady,false); assert.equal(view.state.phase,'stopped');
    view.failed('1:folder/1.png'); assert.equal(view.state.phase,'error');
    view.ready('1:folder/1.png'); assert.equal(view.state.phase,'stopped');
    view.dispose(); const after = calls;
    view.ready('1:folder/1.png'); view.failed('1:folder/1.png'); view.navigate(1); view.dispose();
    assert.equal(calls,after); assert.equal(view.state.phase,'disposed'); assert.equal(view.state.requested,false);
});
