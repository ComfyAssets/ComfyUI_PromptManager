// Run with: node --test tests/js/workspace-file-inspector-choice.test.js
// #659: the inspector mounts Choose prompt after its prompt blocks while the file's review item is pending.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mountedDom } from './helpers/mounted-dom.mjs';

const { createScope } = await import('../../web/js/shared/toolkit/core.js');
const { mountFileInspector } = await import('../../web/js/workspace/file-inspector.mjs');

const ITEM = '0a1b2c3d-0000-4000-8000-00000000abcd';
const OTHER = '0a1b2c3d-0000-4000-8000-00000000ffff';
const EPOCH = '11111111-1111-4111-8111-111111111111';
const flush = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 8) { for (let i = 0; i < times; i++) await flush(); }

function record(review) {
    return {
        identity: { kind: 'rooted-file', key: '0:a.png', root_index: 0, path: 'a.png', root_scope: 'a'.repeat(64) },
        file: { format: 'PNG' }, download: null, preview: null, historyTarget: null, imageTags: null, metadata: null,
        prompts: { positive: { status: 'ambiguous', text: null, source: null }, negative: { status: 'ambiguous', text: null, source: null } },
        generation: {}, library: { status: 'none', tags: [] }, review,
    };
}
function detail(id) {
    return {
        success: true, library_epoch: EPOCH, id, state: 'pending', revision: 0, filename: 'a.png',
        target: { root_index: 0, root_scope: 'c'.repeat(64), path: 'a.png' }, unavailable: false, limited: false,
        candidate_count: 2, total: 1, sha256: 'd'.repeat(64), dependency_fingerprint: 'e'.repeat(64),
        candidates: [
            { hash: 'a'.repeat(64), sampler_id: '11', state: 'resolved', reason: '', label: 'Dunes / blur', positive: { text: 'dunes' }, negative: { text: 'blur' } },
            { hash: 'b'.repeat(64), sampler_id: '13', state: 'resolved', reason: '', label: 'Harbor / blur', positive: { text: 'harbor' }, negative: { text: 'blur' } },
        ],
    };
}
function fakeApi() {
    const calls = [];
    return {
        calls,
        api: {
            get: url => { calls.push(['GET', url]); return Promise.resolve(detail(url.split('/').at(-1))); },
            post: url => {
                calls.push(['POST', url]);
                return Promise.resolve(url.endsWith('/observe') ? { library_epoch: EPOCH, capture: { token: 't', sha256: 'd'.repeat(64) } }
                    : { success: true, library_epoch: EPOCH, total: 0 });
            },
            del: () => Promise.resolve({}),
        },
    };
}
function mount(options = {}) {
    const f = mountedDom();
    f.doc.getElementById = () => null;
    const scope = createScope(), decided = [], { api, calls } = fakeApi();
    const view = mountFileInspector(f.container, { api, sender: { open() {} }, parentScope: scope, variant: 'panel',
        onReviewDecided: () => decided.push(true), ...options });
    const sections = () => f.walk(f.container, n => n.className === 'inspect-choice');
    return { f, view, calls, decided, sections, done() { view.dispose(); scope.dispose(); f.restore(); } };
}

test('a pending review item mounts Choose prompt right after the prompt blocks', async () => {
    const m = mount();
    try {
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        const [section] = m.sections();
        assert.ok(section, 'Choose prompt is mounted');
        const inner = section.parent;
        const index = inner.children.indexOf(section);
        assert.ok(inner.children[index - 1].children.some(n => n.className === 'prompt-section positive'),
            'the section follows the prompt blocks');
        assert.deepEqual(m.calls, [['GET', `/prompt_manager/review/items/${ITEM}`]]);
    } finally { m.done(); }
});

test('a re-render keeps the same section, and another item replaces it', async () => {
    const m = mount();
    try {
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        const [first] = m.sections();
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        assert.equal(m.sections()[0], first);
        assert.equal(m.calls.length, 1, 'the item is read once');
        m.view.setState({ record: record({ state: 'pending', item_id: OTHER }) });
        await settle();
        assert.notEqual(m.sections()[0], first);
        assert.equal(m.sections().length, 1);
        assert.deepEqual(m.calls.at(-1), ['GET', `/prompt_manager/review/items/${OTHER}`]);
    } finally { m.done(); }
});

test('no section without a pending item, or without a way to re-read the inspection', async () => {
    for (const review of [null, { state: 'decided' }, undefined]) {
        const m = mount();
        try {
            m.view.setState({ record: record(review) });
            await settle();
            assert.equal(m.sections().length, 0, JSON.stringify(review));
            assert.equal(m.calls.length, 0);
        } finally { m.done(); }
    }
    const m = mount({ onReviewDecided: undefined });
    try {
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        assert.equal(m.sections().length, 0);
    } finally { m.done(); }
});

test('a decision re-reads the inspection; a record without the item removes the section', async () => {
    const m = mount();
    try {
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        const use = m.f.walk(m.sections()[0], n => n.tagName === 'button' && n.textContent === 'Use this prompt')[0];
        use.fire('click');
        await settle();
        assert.deepEqual(m.decided, [true]);
        m.view.setState({ record: { ...record(null), library: { status: 'linked', tags: [] } } });
        await settle();
        assert.equal(m.sections().length, 0);
    } finally { m.done(); }
});

test('reading another file ends the section', async () => {
    const m = mount();
    try {
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        const [section] = m.sections();
        m.view.setState({ loading: true });
        assert.equal(m.sections().length, 0);
        m.view.setState({ record: record({ state: 'pending', item_id: ITEM }) });
        await settle();
        assert.notEqual(m.sections()[0], section, 'a new section for the newly read record');
    } finally { m.done(); }
});
