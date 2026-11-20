// Run with: node --test tests/js/workspace-inspect-choice.test.js
// #659 (design Part 5): Inspect's Choose prompt over the review controller's single-item mode.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mountedDom } from './helpers/mounted-dom.mjs';

const require = createRequire(import.meta.url);
const { createApiClient } = require('../../web/js/api-client.js');
const { createScope } = await import('../../web/js/shared/toolkit/core.js');
const module = await import('../../web/js/workspace/inspect-choice.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});

const ITEM = '0a1b2c3d-0000-4000-8000-00000000abcd';
const EPOCH = '11111111-1111-4111-8111-111111111111';
const SCOPE = 'c'.repeat(64);
const HASH_A = 'a'.repeat(64), HASH_B = 'b'.repeat(64), HASH_C = 'f'.repeat(64);
const ITEMS = '/prompt_manager/review/items';
const OBSERVE = '/prompt_manager/image-annotations/observe';
const CAPTURE = '/prompt_manager/image-annotations/capture/';
const flush = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 8) { for (let i = 0; i < times; i++) await flush(); }
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: `Status ${status}`, json: async () => body });
const fail = (status, code, error) => response(status, { success: false, code, error });
const candidate = (hash, sampler, label, positive, negative, state = 'resolved', reason = '') => ({
    hash, sampler_id: sampler, state, reason, label,
    positive: positive === null ? null : { text: positive }, negative: negative === null ? null : { text: negative },
});
function detail(overrides = {}) {
    return {
        success: true, library_epoch: EPOCH, id: ITEM, state: 'pending', revision: 0, filename: 'a.png',
        target: { root_index: 0, root_scope: SCOPE, path: 'a.png' }, unavailable: false, candidate_count: 3, limited: false,
        total: 1, sha256: 'd'.repeat(64), dependency_fingerprint: 'e'.repeat(64),
        candidates: [
            candidate(HASH_C, '9', 'No positive / Soft negative', null, 'blur', 'unresolved', 'Unsupported latent ancestry'),
            candidate(HASH_A, '12', 'Dunes / Soft negative', 'golden dunes', 'blur'),
            candidate(HASH_B, '28', 'Harbor / Soft negative', 'harbor lights', 'blur'),
        ],
        ...overrides,
    };
}
const observed = { success: true, library_epoch: EPOCH, capture: { token: 'capture-1', sha256: 'd'.repeat(64) } };
function harness(routes = {}) {
    const calls = [];
    const fetch = (url, init = {}) => {
        const call = { url, method: init.method, body: init.body === undefined ? undefined : JSON.parse(init.body) };
        calls.push(call);
        const path = url.startsWith(CAPTURE) ? CAPTURE : url.split('?')[0];
        const all = {
            [`GET ${ITEMS}/${ITEM}`]: () => response(200, detail()),
            [`POST ${OBSERVE}`]: () => response(200, observed),
            [`POST ${ITEMS}/${ITEM}/decision`]: () => response(200, { success: true, library_epoch: EPOCH, total: 0 }),
            ...routes,
        };
        const route = all[`${call.method} ${path}`];
        return Promise.resolve().then(() => (route ? route(call) : response(200, { success: true })));
    };
    return { api: createApiClient(fetch), calls, of: (method, prefix) => calls.filter(c => c.method === method && c.url.startsWith(prefix)) };
}

function contract() {
    assert.equal(typeof module.mountInspectChoice, 'function', 'inspect-choice.mjs is not implemented');
    return module;
}
/** The section mounted in a minimal DOM, with its parts found by text and class. */
async function mounted(routes, options = {}) {
    const { mountInspectChoice } = contract();
    const f = mountedDom();
    f.doc.getElementById = () => null;
    const h = harness(routes), scope = createScope(), decided = [], sent = [];
    const sender = { open: (roles, trigger) => sent.push({ roles, trigger }) };
    const view = mountInspectChoice({ api: h.api, itemId: ITEM, sender, parentScope: scope, onDecided: () => decided.push(true), ...options });
    f.container.append(view.root);
    await settle();
    const find = predicate => f.walk(view.root, predicate);
    const button = text => find(n => n.tagName === 'button' && n.textContent === text)[0];
    const visible = node => { for (let n = node; n && n !== view.root.parent; n = n.parent) if (n.hidden) return false; return true; };
    return {
        f, h, view, scope, decided, sent, button, visible,
        select: () => find(n => n.tagName === 'select')[0],
        options: () => find(n => n.tagName === 'option').map(n => [n.textContent, n.disabled]),
        warning: () => find(n => n.className === 'warning-note')[0],
        status: () => find(n => /inspect-choice-status/.test(n.className ?? ''))[0],
        texts: role => find(n => n.tagName === 'section' && n.className === `prompt-section ${role}`)
            .flatMap(section => f.walk(section, n => n.className === 'prompt-text').map(n => n.textContent)),
        done() { scope.dispose(); f.restore(); },
    };
}
const click = node => node.fire('click');

test('the section lists the server labels, with a disabled candidate and its reason', async () => {
    const m = await mounted();
    try {
        assert.equal(m.view.itemId, ITEM);
        assert.equal(m.view.root.className, 'inspect-choice');
        assert.ok(m.f.walk(m.view.root, n => n.textContent === 'Choose prompt').length);
        assert.deepEqual(m.options(), [
            ['Sampler 9 · No positive / Soft negative (Unsupported latent ancestry)', true],
            ['Sampler 12 · Dunes / Soft negative', false],
            ['Sampler 28 · Harbor / Soft negative', false],
        ]);
        assert.equal(m.select().value, HASH_A);
        assert.deepEqual(m.texts('positive'), ['golden dunes']);
        assert.deepEqual(m.texts('negative'), ['blur']);
        assert.equal(m.visible(m.button('Use this prompt')), true);
        assert.equal(m.visible(m.button('Leave unresolved')), true);
        assert.equal(m.visible(m.button('Open in Needs review')), false);
        assert.equal(m.visible(m.warning()), false);
        // Choosing another option shows its prompts; the prompt blocks' Send opens the shared sender.
        m.select().value = HASH_B;
        m.select().fire('change');
        assert.deepEqual(m.texts('positive'), ['harbor lights']);
        const send = m.f.walk(m.view.root, n => n.dataset?.sendRole === 'positive')[0];
        // The prompt blocks listen on their container (the section's parent).
        m.f.walk(m.view.root, n => n.className === 'prompt-section positive')[0].parent.fire('click', { target: send });
        assert.deepEqual(m.sent.map(entry => entry.roles), [{ positive: 'harbor lights' }]);
        assert.equal(m.h.of('GET', ITEMS).length, 1, 'only the item is read, never the queue');
    } finally { m.done(); }
});

test('Use this prompt decides the chosen candidate and re-reads the inspection', async () => {
    const m = await mounted();
    try {
        m.select().value = HASH_B;
        m.select().fire('change');
        click(m.button('Use this prompt'));
        await settle();
        const [decision] = m.h.of('POST', `${ITEMS}/${ITEM}/decision`);
        assert.equal(decision.body.choice, HASH_B);
        assert.equal(decision.body.capture_token, 'capture-1');
        assert.equal(m.h.of('POST', OBSERVE).length, 1);
        assert.deepEqual(m.decided, [true]);
        // A decided item offers nothing more.
        assert.equal(m.visible(m.button('Use this prompt')), false);
        click(m.button('Use this prompt'));
        await settle();
        assert.equal(m.h.of('POST', `${ITEMS}/${ITEM}/decision`).length, 1);
    } finally { m.done(); }
});

test('a refusal shows the server sentence and keeps the choice', async () => {
    const m = await mounted({ [`POST ${ITEMS}/${ITEM}/decision`]: () => fail(409, 'file-changed', 'The file changed since it was queued.') });
    try {
        click(m.button('Use this prompt'));
        await settle();
        assert.equal(m.warning().textContent, 'The file changed since it was queued.');
        assert.equal(m.visible(m.warning()), true);
        assert.deepEqual(m.decided, []);
        assert.equal(m.select().value, HASH_A);
        assert.equal(m.button('Use this prompt').getAttribute('aria-disabled'), null);
        // The refused capture is released.
        assert.equal(m.h.of('DELETE', CAPTURE).length, 1);
    } finally { m.done(); }
});

test('an unanswered decision is resent once by the next press', async () => {
    let attempts = 0;
    const m = await mounted({
        [`POST ${ITEMS}/${ITEM}/decision`]: () => (++attempts === 1 ? Promise.reject(new TypeError('Failed to fetch'))
            : response(200, { success: true, library_epoch: EPOCH, total: 0 })),
    });
    try {
        click(m.button('Use this prompt'));
        await settle();
        assert.equal(m.warning().textContent, 'The decision may not have been saved. Apply again to send the same decision once more.');
        click(m.button('Use this prompt'));
        await settle();
        const [first, second] = m.h.of('POST', `${ITEMS}/${ITEM}/decision`);
        assert.deepEqual(second.body, first.body);
        assert.equal(m.h.of('POST', OBSERVE).length, 1);
        assert.deepEqual(m.decided, [true]);
    } finally { m.done(); }
});

test('Leave unresolved decides the item as unresolved', async () => {
    const m = await mounted();
    try {
        click(m.button('Leave unresolved'));
        await settle();
        const [decision] = m.h.of('POST', `${ITEMS}/${ITEM}/decision`);
        assert.equal(decision.body.choice, 'unresolved');
        assert.deepEqual(m.decided, [true]);
    } finally { m.done(); }
});

for (const [name, overrides] of [
    ['a limited item', { limited: true, candidates: [], candidate_count: 0 }],
    ['an item whose candidates cannot be applied', {
        candidates: [candidate(HASH_C, '9', 'No positive / Soft negative', null, 'blur', 'unresolved', 'Unsupported latent ancestry')],
    }],
    ['an unavailable file', { target: null, unavailable: true }],
]) {
    test(`${name} offers Open in Needs review instead of options`, async () => {
        const m = await mounted({ [`GET ${ITEMS}/${ITEM}`]: () => response(200, detail(overrides)) });
        try {
            assert.equal(m.status().textContent, 'None of the possible prompts can be applied here. Decide this image in Needs review.');
            const open = m.button('Open in Needs review');
            assert.equal(m.visible(open), true);
            for (const text of ['Use this prompt', 'Leave unresolved']) assert.equal(m.visible(m.button(text)), false, text);
            assert.equal(m.visible(m.select()), false);
            click(open);
            assert.equal(m.f.win.location.hash, '#/review');
        } finally { m.done(); }
    });
}

test('a failed read offers Retry and Open in Needs review; Retry reads the item again', async () => {
    let reads = 0;
    const m = await mounted({
        [`GET ${ITEMS}/${ITEM}`]: () => (++reads === 1 ? fail(500, 'review-failed', 'The review queue could not be read.') : response(200, detail())),
    });
    try {
        assert.equal(m.visible(m.button('Retry')), true);
        assert.equal(m.visible(m.button('Open in Needs review')), true);
        assert.equal(m.warning().textContent, 'This item could not be read. Select it again to retry.');
        click(m.button('Retry'));
        await settle();
        assert.equal(reads, 2);
        assert.equal(m.visible(m.button('Retry')), false);
        assert.equal(m.visible(m.button('Use this prompt')), true);
    } finally { m.done(); }
});

test('the loading state says so, and disposing stops the section', async () => {
    let answer;
    const m = await mounted({ [`GET ${ITEMS}/${ITEM}`]: () => new Promise(resolve => { answer = resolve; }) });
    try {
        assert.equal(m.status().textContent, 'Loading the possible prompts…');
        assert.equal(m.visible(m.button('Use this prompt')), false);
        m.view.dispose();
        m.view.dispose();
        answer(response(200, detail()));
        await settle();
        assert.equal(m.view.root.parent, null);
        assert.equal(m.status().textContent, 'Loading the possible prompts…');
    } finally { m.done(); }
});

test('an aborted parent mounts nothing', () => {
    const { mountInspectChoice } = contract();
    const f = mountedDom();
    try {
        const scope = createScope();
        scope.dispose();
        const view = mountInspectChoice({ api: null, itemId: ITEM, sender: null, parentScope: scope });
        assert.equal(view.root.children.length, 0);
        view.dispose();
    } finally { f.restore(); }
});

test('choiceMode names what the section shows', () => {
    const { choiceMode } = contract();
    const ready = { detailStatus: 'ready', detail: detail(), options: [{ disabled: false }], decided: false };
    assert.equal(choiceMode(ready), 'choose');
    assert.equal(choiceMode({ ...ready, decided: true }), 'decided');
    assert.equal(choiceMode({ ...ready, detailStatus: 'error' }), 'error');
    assert.equal(choiceMode({ ...ready, detailStatus: 'loading', detail: null }), 'loading');
    assert.equal(choiceMode({ ...ready, options: [{ disabled: true }] }), 'review');
});
