// Run with: node --test tests/js/workspace-library-editor-state.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { createApiClient } = require('../../web/js/api-client.js');

// Only an absent module is tolerated, so RED is an assertion failure, not a crash.
const source = import('../../web/js/workspace/library-editor-state.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});

const EPOCH = '11111111-1111-4111-8111-111111111111';
const RESTORED = '22222222-2222-4222-8222-222222222222';
const TOKEN = 'a'.repeat(64);
const TOKEN2 = 'b'.repeat(64);

const flush = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 6) { for (let i = 0; i < times; i++) await flush(); }
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function response(status, body) {
    return { ok: status >= 200 && status < 300, status, statusText: `Status ${status}`, json: async () => body };
}

/** Real createApiClient over a fetch whose answers come from handler(call). */
function harness(handler) {
    const calls = [];
    const fetch = (url, init = {}) => {
        const call = { url, method: init.method, init, body: init.body === undefined ? undefined : JSON.parse(init.body), aborted: false };
        calls.push(call);
        return new Promise((resolve, reject) => {
            init.signal?.addEventListener('abort', () => {
                call.aborted = true;
                reject(new DOMException('The operation was aborted.', 'AbortError'));
            });
            Promise.resolve().then(() => handler(call)).then(resolve, reject);
        });
    };
    return { api: createApiClient(fetch), calls, writes: () => calls.filter(call => call.method !== 'GET') };
}

const positive = (overrides = {}) => ({
    id: 7, text: 'First line\nsecond line', title: null, category: 'Studio', tags: ['soft', 'warm'],
    rating: 3, notes: null, edit_token: TOKEN, created_at: '2026-01-01 00:00:00', variant_count: 1, image_count: 2,
    ...overrides,
});
const variant = (id, negative = 'blurry') => ({
    id, negative_text: negative, category: null, title: null, archived: false,
    explicit_empty: negative === '', image_count: 1, tags: [],
});
const pair = (overrides = {}) => ({
    id: 31, positive_prompt_id: 7, negative_prompt_id: 12, title: null, category: null, rating: null, notes: null,
    created_at: '', updated_at: '', last_used_at: null, run_count: 4, archived: false,
    positive_text: 'First line\nsecond line', negative_text: 'blurry', image_count: 2, tags: [],
    ...overrides,
});
const detail = (epoch = EPOCH, owner = positive(), variants = [variant(31)]) => ({
    success: true, library_epoch: epoch, positive: { ...owner, archived_variant_count: 0, previews: [] },
    variants, unassigned_image_count: 0, variant_total: variants.length, variants_has_more: false, variants_next_cursor: null,
});
const options = (epoch = EPOCH) => ({ success: true, library_epoch: epoch, categories: ['Studio', 'Portrait'] });
const recipe = (overrides = {}) => ({
    success: true, library_epoch: EPOCH, positive: positive({ id: 9, text: 'a portrait', title: 'Portrait', category: 'Uncategorized', tags: [], rating: null, notes: '' }),
    pair: pair({ id: 40, positive_prompt_id: 9, positive_text: 'a portrait', negative_text: '', negative_prompt_id: null }),
    positive_created: true, pair_created: true, parent_metadata_applied: true, ...overrides,
});

/** Answer the standard Library reads; `write` answers anything else. */
function library({ epoch = EPOCH, owner = positive(), variants = [variant(31)], selected = pair(), write } = {}) {
    return call => {
        const path = call.url.split('?')[0];
        if (call.method === 'GET' && path === '/prompt_manager/library/options') return response(200, options(epoch));
        if (call.method === 'GET' && path === `/prompt_manager/library/${owner.id}`) return response(200, detail(epoch, owner, variants));
        if (call.method === 'GET' && path === `/prompt_manager/pairs/${selected.id}`) return response(200, { success: true, pair: selected });
        if (write) return write(call);
        throw new Error(`Unexpected ${call.method} ${call.url}`);
    };
}
/** Reads for the owner a create receipt returns. */
const created = (write, epoch = EPOCH) => library({ epoch, owner: recipe().positive, selected: recipe().pair, write });

test('new prompt from Images waits for a fresh options epoch', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const read = deferred();
    const committed = [], reconciled = [];
    const server = harness(created(() => response(200, recipe())));
    const original = server.api.get;
    let first = true;
    server.api.get = (url, init) => {
        if (first) { first = false; return read.promise.then(() => original(url, init)); }
        return original(url, init);
    };
    const controller = createLibraryEditorController({ api: server.api, mode: 'new', onCommitted: c => committed.push(c), onReconciled: r => reconciled.push(r) });
    const initialization = controller.initialize();
    assert.equal(controller.state.phase, 'initializing');
    controller.setField('title', '  Portrait ');
    controller.setField('positive_text', 'a portrait');
    await controller.submit();
    assert.equal(server.writes().length, 0, 'pending initialization cannot save');
    read.resolve();
    await initialization;
    const request = server.calls[0];
    assert.equal(request.url, '/prompt_manager/library/options');
    assert.equal(request.init.cache, 'no-store');
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.draft.title, '  Portrait ', 'typing survives the pending read');
    assert.deepEqual(controller.state.categories, ['Studio', 'Portrait']);
    await controller.submit();
    const [write] = server.writes();
    assert.equal(write.method, 'POST');
    assert.equal(write.url, '/prompt_manager/library');
    const sentBody = write.body;
    assert.equal(sentBody.expected_epoch, EPOCH);
    assert.deepEqual(sentBody, {
        expected_epoch: EPOCH, positive_text: 'a portrait', negative_text: '',
        parent_metadata: { title: 'Portrait', category: 'Uncategorized', tags: [], rating: null, notes: '' },
    });
    assert.equal(controller.state.phase, 'complete');
    assert.equal(committed.length, 1);
    assert.equal(committed[0].kind, 'create');
    assert.equal(reconciled.length, 1);
    assert.equal(reconciled[0].pair.id, 40);
    assert.equal(reconciled[0].detail.positive.id, 9);
    assert.equal(controller.state.receipt.pair.id, 40);
    assert.equal(controller.state.outcome.metadataNotApplied, false);
    await controller.submit();
    assert.equal(server.writes().length, 1, 'submit once');
    controller.dispose();
});

test('failed initialization retries only the read and retains draft', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let fail = 2;
    const server = harness(call => {
        if (fail-- > 0) return fail === 1 ? Promise.reject(new TypeError('Failed to fetch')) : response(200, { success: true, library_epoch: 'not-a-uuid', categories: [] });
        return library()(call);
    });
    const controller = createLibraryEditorController({ api: server.api, mode: 'new' });
    const pending = controller.initialize();
    controller.setField('notes', 'kept\n');
    await pending;
    assert.equal(controller.state.phase, 'initialization-error');
    await controller.submit();
    await controller.retryInitialization();
    assert.equal(controller.state.phase, 'initialization-error', 'invalid epoch is not ready');
    await controller.retryInitialization();
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.draft.notes, 'kept\n');
    assert.equal(controller.state.error, null);
    assert.equal(server.writes().length, 0);
    assert.ok(server.calls.every(call => call.method === 'GET' && call.url === '/prompt_manager/library/options'));
    assert.equal(server.calls.length, 3);
    controller.dispose();
});

test('late initialization cannot enable a replacement dialog', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const answers = [deferred(), deferred(), deferred()];
    let index = 0;
    const server = harness(() => answers[index++].promise);
    const old = createLibraryEditorController({ api: server.api, mode: 'new' });
    const oldInit = old.initialize();
    old.dispose();
    assert.equal(server.calls[0].aborted, true, 'disposal aborts the read');
    assert.equal(old.state.phase, 'disposed');
    const replacement = createLibraryEditorController({ api: server.api, mode: 'new' });
    const replacementInit = replacement.initialize();
    await settle();
    answers[0].resolve(response(200, options(RESTORED)));
    await oldInit;
    await settle();
    assert.equal(replacement.state.phase, 'initializing', 'an older dialog cannot enable this one');
    assert.equal(replacement.state.epoch, null);
    // A retried read supersedes the earlier attempt inside the same dialog too.
    answers[1].reject(new TypeError('offline'));
    await replacementInit;
    assert.equal(replacement.state.phase, 'initialization-error');
    const retry = replacement.retryInitialization();
    await settle();
    answers[2].resolve(response(200, options(EPOCH)));
    await retry;
    assert.equal(replacement.state.epoch, EPOCH);
    assert.equal(replacement.state.phase, 'ready');
    replacement.dispose();
});

test('restore after initialization rejects the old New submission', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let epoch = EPOCH;
    const server = harness(call => created(write => write.body.expected_epoch === RESTORED
        ? response(200, recipe({ library_epoch: RESTORED }))
        : response(409, { success: false, code: 'storage-changed', error: 'Prompt storage was restored; refresh the library', library_epoch: RESTORED }), epoch)(call));
    const controller = createLibraryEditorController({ api: server.api, mode: 'new' });
    await controller.initialize();
    controller.setField('title', 'Portrait');
    controller.setField('positive_text', 'a portrait');
    epoch = RESTORED;
    await controller.submit();
    assert.equal(controller.state.phase, 'storage-changed');
    assert.equal(controller.state.error.code, 'storage-changed');
    assert.equal(controller.state.draft.title, 'Portrait');
    assert.equal(controller.state.epoch, EPOCH, 'the error response epoch is never borrowed');
    await controller.submit();
    assert.equal(server.writes().length, 1, 'Save is disabled after storage changed');
    await controller.reloadOwner({ keepDraft: true });
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.epoch, RESTORED);
    assert.equal(controller.state.reviewRequired, true);
    assert.equal(controller.state.draft.positive_text, 'a portrait');
    assert.equal(server.writes().length, 1, 'reload never replays the write');
    await controller.submit();
    assert.equal(server.writes().length, 2);
    assert.equal(server.writes()[1].body.expected_epoch, RESTORED);
    assert.equal(controller.state.phase, 'complete');
    controller.dispose();
});

test('editor recovery preserves hyphenated ApiError codes', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const errors = [], phases = [];
    for (const [code, extra] of [['metadata-conflict', { positive: positive({ edit_token: TOKEN2 }) }], ['storage-changed', {}], ['other-conflict', {}], [null, {}]]) {
        const server = harness(library({ write: () => response(409, { success: false, code, error: 'Conflict', library_epoch: EPOCH, ...extra }) }));
        const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
        await controller.initialize();
        controller.setField('rating', 5);
        await controller.submit();
        phases.push(controller.state.phase);
        if (controller.state.phase !== 'write-error') errors.push(controller.state.error);
        assert.equal(controller.state.draft.rating, 5, 'draft preserved');
        assert.equal(controller.state.receipt, null);
        controller.dispose();
    }
    assert.deepEqual(errors.map(e => e.code), ['metadata-conflict', 'storage-changed']);
    assert.equal(errors[0].status, 409);
    assert.deepEqual(phases, ['conflict', 'storage-changed', 'write-error', 'write-error'], 'an unrelated 409 is generic');
});

test('lost save response never manufactures a receipt', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const committed = [];
    let lose = true;
    const reused = recipe({ positive_created: false, pair_created: false, parent_metadata_applied: false });
    const server = harness(created(() => {
        if (lose) { lose = false; return Promise.reject(new TypeError('Failed to fetch')); }
        return response(200, reused);
    }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'new', onCommitted: commit => committed.push(commit) });
    await controller.initialize();
    controller.setField('title', 'Portrait');
    controller.setField('positive_text', 'a portrait');
    controller.setField('category', 'Uncategorized');
    await controller.submit();
    assert.equal(controller.state.receipt, null);
    assert.equal(controller.state.phase, 'unknown-outcome');
    assert.equal(committed.length, 0);
    assert.equal(server.writes().length, 1, 'no automatic retry');
    await settle();
    assert.equal(server.writes().length, 1);
    controller.setField('title', 'Changed after the loss');
    await controller.submit();
    assert.equal(server.writes().length, 1, 'Save cannot replace the unconfirmed write');
    await controller.retryUnconfirmed();
    assert.equal(server.writes().length, 2);
    assert.deepEqual(server.writes()[1].body, server.writes()[0].body, 'identical payload');
    assert.equal(controller.state.phase, 'complete');
    assert.equal(controller.state.receipt.positive_created, false);
    assert.equal(controller.state.outcome.metadataNotApplied, false, 'stored metadata equals the submitted draft');
    assert.equal(committed.length, 1);
    controller.dispose();
});

test('reused retry discloses unapplied metadata only when it differs', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let lose = true;
    const other = recipe({ positive_created: false, pair_created: true, parent_metadata_applied: false,
        positive: positive({ id: 9, text: 'a portrait', title: 'Someone else', tags: ['x'] }) });
    const server = harness(created(() => {
        if (lose) { lose = false; return Promise.reject(new TypeError('Failed to fetch')); }
        return response(200, other);
    }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'new' });
    await controller.initialize();
    controller.setField('title', 'Portrait');
    controller.setField('positive_text', 'a portrait');
    await controller.submit();
    await controller.retryUnconfirmed();
    assert.equal(controller.state.phase, 'complete');
    assert.equal(controller.state.outcome.metadataNotApplied, true);
    assert.equal(controller.state.outcome.pairCreated, true);
    assert.equal(controller.state.outcome.positiveCreated, false);
    controller.dispose();
});

test('confirmed save refresh retry is read only', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let failReads = 1;
    const committed = [], reconciled = [];
    const receipt = { success: true, library_epoch: EPOCH, positive: positive({ rating: null, notes: 'keep\ntext', edit_token: TOKEN2 }) };
    const base = library({ write: () => response(200, receipt) });
    const server = harness(call => {
        if (call.method === 'GET' && server.writes().length && failReads-- > 0) return Promise.reject(new TypeError('offline'));
        return base(call);
    });
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31,
        onCommitted: commit => committed.push(commit), onReconciled: result => reconciled.push(result) });
    await controller.initialize();
    controller.setField('rating', null);
    controller.setField('notes', 'keep\ntext');
    await controller.submit();
    assert.equal(controller.state.phase, 'saved-refresh-error');
    assert.equal(controller.state.receipt.positive.edit_token, TOKEN2);
    assert.equal(committed.length, 1);
    assert.equal(committed[0].kind, 'metadata');
    const writeCalls = server.writes();
    await controller.submit();
    await controller.retryReconciliation();
    assert.equal(writeCalls.length, 1); // retryReconciliation only GETs
    assert.equal(server.writes().length, 1);
    assert.equal(controller.state.phase, 'complete');
    assert.equal(reconciled.length, 1);
    assert.equal(reconciled[0].commit, committed[0]);
    assert.equal(reconciled[0].pair, null);
    assert.equal(reconciled[0].options.library_epoch, EPOCH);
    controller.dispose();
});

test('legacy title fallback is not an edit', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const receipt = { success: true, library_epoch: EPOCH, positive: positive({ rating: null, notes: 'keep\ntext' }) };
    const server = harness(library({ write: () => response(200, receipt) }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await controller.initialize();
    assert.equal(controller.state.draft.title, 'First line', 'displayed fallback seeds the input');
    assert.equal(controller.state.baseline.title, null, 'stored title stays distinct');
    assert.equal(controller.state.draft.tags, 'soft, warm');
    assert.equal(controller.state.draft.negative_text, 'blurry', 'the selected pair negative');
    controller.setField('rating', null);
    controller.setField('notes', 'keep\ntext');
    controller.setField('tags', 'warm, soft,');
    controller.setField('category', ' Studio ');
    await controller.submit();
    const [write] = server.writes();
    assert.equal(write.method, 'PATCH');
    assert.equal(write.url, '/prompt_manager/library/7');
    const sentBody = write.body;
    assert.equal(sentBody.expected_epoch, EPOCH);
    assert.equal(sentBody.if_match, TOKEN);
    assert.deepEqual(sentBody.metadata, { rating: null, notes: 'keep\ntext' });

    const unchanged = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await unchanged.initialize();
    unchanged.setField('notes', '');
    await unchanged.submit();
    assert.equal(server.writes().length, 1, 'unchanged edit submits nothing');
    assert.equal(unchanged.state.phase, 'complete');
    assert.equal(unchanged.state.receipt, null);

    const edited = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await edited.initialize();
    edited.setField('title', '  Studio portrait ');
    edited.setField('category', '');
    await edited.submit();
    assert.deepEqual(server.writes()[1].body.metadata, { title: 'Studio portrait', category: '' }, 'Edit keeps a blank category blank');
    for (const item of [controller, unchanged, edited]) item.dispose();
});

test('existing-parent forms read detail, options and the exact selected pair fresh', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const many = Array.from({ length: 40 }, (_, i) => variant(100 - i));
    const outside = pair({ id: 31, run_count: 9 });
    const server = harness(library({ variants: many, selected: outside }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await controller.initialize();
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.pair.id, 31);
    assert.equal(controller.state.pair.run_count, 9);
    assert.ok(server.calls.every(call => call.init.cache === 'no-store'));
    assert.deepEqual(server.calls.map(call => call.url.split('?')[0]).sort(),
        ['/prompt_manager/library/7', '/prompt_manager/library/options', '/prompt_manager/pairs/31']);
    controller.dispose();

    for (const broken of [
        library({ selected: pair({ positive_prompt_id: 8 }) }),
        library({ selected: pair({ positive_text: 'other text' }) }),
        call => call.url.startsWith('/prompt_manager/library/options') ? response(200, options(RESTORED)) : library()(call),
        library({ owner: positive({ id: 7, edit_token: 'short' }) }),
    ]) {
        const reads = harness(broken);
        const rejected = createLibraryEditorController({ api: reads.api, mode: 'revision', positiveId: 7, pairId: 31 });
        await rejected.initialize();
        assert.equal(rejected.state.phase, 'initialization-error');
        await rejected.submit();
        assert.equal(reads.writes().length, 0);
        rejected.dispose();
    }
    const wrongOwner = harness(call => call.url.startsWith('/prompt_manager/library/7') ? response(200, detail(EPOCH, positive({ id: 8 }))) : library()(call));
    const mismatch = createLibraryEditorController({ api: wrongOwner.api, mode: 'negative', positiveId: 7 });
    await mismatch.initialize();
    assert.equal(mismatch.state.phase, 'initialization-error');
    mismatch.dispose();
    assert.throws(() => createLibraryEditorController({ api: server.api, mode: 'metadata' }), TypeError);
    assert.throws(() => createLibraryEditorController({ api: server.api, mode: 'new', positiveId: 7 }), TypeError);
    assert.throws(() => createLibraryEditorController({ api: server.api, mode: 'archive', positiveId: 7 }), TypeError);
});

test('metadata conflict reload keeps changed fields for review and never replays', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let owner = positive();
    const server = harness(call => library({ owner, write: write => write.body.if_match === TOKEN2
        ? response(200, { success: true, library_epoch: EPOCH, positive: { ...owner, rating: 5 } })
        : response(409, { success: false, code: 'metadata-conflict', error: 'changed', library_epoch: EPOCH, positive: owner }) })(call));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await controller.initialize();
    controller.setField('rating', '5');
    owner = positive({ title: 'Renamed elsewhere', notes: 'other writer', edit_token: TOKEN2 });
    await controller.submit();
    assert.equal(controller.state.phase, 'conflict');
    await controller.submit();
    assert.equal(server.writes().length, 1);
    await controller.reloadOwner({ keepDraft: true });
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.reviewRequired, true);
    assert.equal(controller.state.draft.rating, 5, 'changed field kept');
    assert.equal(controller.state.draft.title, 'Renamed elsewhere', 'untouched field follows the current owner');
    assert.equal(controller.state.draft.notes, 'other writer');
    assert.equal(server.writes().length, 1);
    await controller.submit();
    assert.deepEqual(server.writes()[1].body, { expected_epoch: EPOCH, if_match: TOKEN2, metadata: { rating: 5 } });
    assert.equal(controller.state.phase, 'complete');
    controller.dispose();

    const discard = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await discard.initialize();
    discard.setField('rating', 1);
    await discard.reloadOwner({ keepDraft: false });
    assert.equal(discard.state.draft.rating, 3);
    assert.equal(discard.state.reviewRequired, false);
    discard.dispose();
});

test('metadata unknown outcome retry reloads the owner instead of replaying', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let owner = positive();
    const server = harness(call => library({ owner, write: () => {
        owner = positive({ rating: 5, edit_token: TOKEN2 });
        return Promise.reject(new TypeError('Failed to fetch'));
    } })(call));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await controller.initialize();
    controller.setField('rating', 5);
    await controller.submit();
    assert.equal(controller.state.phase, 'unknown-outcome');
    await controller.retryUnconfirmed();
    assert.equal(server.writes().length, 1, 'a metadata retry requires review, not a replay');
    assert.equal(controller.state.phase, 'ready');
    assert.equal(controller.state.reviewRequired, true);
    assert.equal(controller.state.owner.edit_token, TOKEN2);
    await controller.submit();
    assert.equal(server.writes().length, 1, 'the applied edit is now unchanged');
    assert.equal(controller.state.phase, 'complete');
    controller.dispose();
});

test('pair archive and restore patch the selected pair and reconcile', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let selected = pair();
    const committed = [], reconciled = [];
    const server = harness(call => library({ selected, write: write => {
        selected = pair({ archived: write.body.archived });
        return response(200, { success: true, pair: selected });
    } })(call));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31,
        onCommitted: commit => committed.push(commit), onReconciled: result => reconciled.push(result) });
    await controller.initialize();
    controller.setField('notes', 'unsaved');
    await controller.setArchive(true);
    const [write] = server.writes();
    assert.equal(write.method, 'PATCH');
    assert.equal(write.url, '/prompt_manager/pairs/31');
    assert.deepEqual(write.body, { archived: true });
    assert.equal(committed[0].kind, 'archive-pair');
    assert.equal(controller.state.phase, 'complete');
    assert.equal(reconciled[0].pair.archived, true);
    controller.dispose();

    const restore = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31,
        onCommitted: commit => committed.push(commit) });
    await restore.initialize();
    assert.equal(restore.state.pair.archived, true);
    await restore.setArchive(false);
    assert.equal(committed[1].kind, 'restore-pair');
    assert.deepEqual(server.writes()[1].body, { archived: false });
    restore.dispose();

    let optionReads = 0;
    const restored = harness(call => call.url === '/prompt_manager/library/options' && ++optionReads > 1
        ? response(200, options(RESTORED)) : library()(call));
    const guarded = createLibraryEditorController({ api: restored.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await guarded.initialize();
    await guarded.setArchive(true);
    assert.equal(guarded.state.phase, 'storage-changed', 'a restored database cannot archive a reused pair ID');
    assert.equal(restored.writes().length, 0);
    guarded.dispose();

    const none = harness(library());
    const noPair = createLibraryEditorController({ api: none.api, mode: 'metadata', positiveId: 7 });
    await noPair.initialize();
    assert.equal(noPair.state.pair, null);
    assert.equal(noPair.state.draft.negative_text, '');
    await noPair.setArchive(true);
    assert.equal(none.writes().length, 0);
    assert.match(noPair.state.error.message, /saved negative/);
    noPair.dispose();
});

test('add negative sends only the negative under the loaded token', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const archived = recipe({ positive: positive(), pair: pair({ id: 55, archived: true, negative_text: '' }),
        positive_created: false, pair_created: false, parent_metadata_applied: false });
    const server = harness(library({ selected: pair({ id: 55, archived: true, negative_text: '' }), write: () => response(200, archived) }));
    const committed = [];
    const controller = createLibraryEditorController({ api: server.api, mode: 'negative', positiveId: 7, onCommitted: c => committed.push(c) });
    await controller.initialize();
    assert.ok(server.calls.every(call => !call.url.startsWith('/prompt_manager/library/options')), 'Add negative needs only the owner read');
    await controller.submit();
    const [write] = server.writes();
    assert.equal(write.url, '/prompt_manager/library/7/variants');
    assert.deepEqual(write.body, { expected_epoch: EPOCH, if_match: TOKEN, negative_text: '' });
    assert.equal(committed[0].kind, 'add-negative');
    assert.equal(controller.state.outcome.pairArchived, true);
    assert.equal(controller.state.outcome.metadataNotApplied, false);
    controller.dispose();

    const forged = harness(library({ write: () => response(200, { ...archived, positive: positive({ id: 8 }) }) }));
    const unconfirmed = createLibraryEditorController({ api: forged.api, mode: 'negative', positiveId: 7 });
    await unconfirmed.initialize();
    await unconfirmed.submit();
    assert.equal(unconfirmed.state.phase, 'unknown-outcome', 'an invalid receipt is not confirmation');
    assert.equal(unconfirmed.state.receipt, null);
    unconfirmed.dispose();
});

test('revision seeds text from the selected pair and sends Uncategorized for a blank category', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const server = harness(library({ owner: positive({ title: 'Portrait', category: null, notes: 'n' }), variants: [variant(30, 'first'), variant(31)], write: () => response(200, recipe()) }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'revision', positiveId: 7, pairId: 31 });
    await controller.initialize();
    assert.equal(controller.state.draft.negative_text, 'blurry', 'never the first arbitrary negative');
    assert.equal(controller.state.draft.positive_text, 'First line\nsecond line');
    controller.setField('positive_text', 'First line\nrevised');
    controller.setField('tags', ' a, b ,a,, ');
    await controller.submit();
    assert.deepEqual(server.writes()[0].body, {
        expected_epoch: EPOCH, positive_text: 'First line\nrevised', negative_text: 'blurry',
        parent_metadata: { title: 'Portrait', category: 'Uncategorized', tags: ['a', 'b'], rating: 3, notes: 'n' },
    });
    controller.dispose();
});

test('invalid drafts and server failures keep the draft without a receipt', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let status = 500;
    const server = harness(library({ write: () => response(status, { success: false, code: null, error: 'Library command failed' }) }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'new' });
    await controller.initialize();
    controller.setField('title', '   ');
    controller.setField('positive_text', 'x');
    await controller.submit();
    assert.equal(server.writes().length, 0);
    assert.equal(controller.state.phase, 'ready');
    assert.match(controller.state.error.message, /Title/);
    assert.throws(() => controller.setField('unknown', 'x'), TypeError);
    assert.throws(() => controller.setField('rating', 7), TypeError);
    controller.setField('title', 'T');
    await controller.submit();
    assert.equal(controller.state.phase, 'write-error');
    assert.equal(controller.state.draft.title, 'T');
    status = 504;
    await controller.submit();
    assert.equal(controller.state.phase, 'unknown-outcome', 'a gateway timeout cannot confirm either outcome');
    controller.dispose();
    controller.setField('title', 'ignored');
    assert.equal(controller.state.phase, 'disposed');
});

test('disposal suppresses late write results without aborting the write', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    const answer = deferred();
    const committed = [], changes = [];
    const server = harness(library({ write: () => answer.promise }));
    const controller = createLibraryEditorController({ api: server.api, mode: 'new', onCommitted: c => committed.push(c), onChange: s => changes.push(s) });
    await controller.initialize();
    controller.setField('title', 'T');
    controller.setField('positive_text', 'x');
    const saving = controller.submit();
    await settle();
    assert.equal(controller.state.phase, 'saving');
    assert.ok(Object.isFrozen(controller.state));
    await controller.submit();
    assert.equal(server.writes().length, 1, 'duplicate submit is disabled');
    controller.dispose();
    assert.equal(server.writes()[0].aborted, false, 'abort cannot undo a write');
    const count = changes.length;
    answer.resolve(response(200, recipe()));
    await saving;
    await settle();
    assert.equal(committed.length, 0);
    assert.equal(changes.length, count);
    assert.equal(controller.state.phase, 'disposed');
});

test('reconcileLibraryReceipt reads detail, options and the verified pair', async () => {
    const { reconcileLibraryReceipt } = await source;
    assert.equal(typeof reconcileLibraryReceipt, 'function', 'reconcile export');
    const receipt = recipe({ positive: positive(), pair: pair() });
    const server = harness(library());
    const result = await reconcileLibraryReceipt(server.api, { kind: 'add-negative', receipt }, {});
    assert.equal(result.detail.positive.id, 7);
    assert.equal(result.pair.id, 31);
    assert.ok(!result.detail.variants.some(item => item === result.pair), 'the verified pair stays separate');
    assert.equal(server.writes().length, 0);
    const restored = harness(library({ epoch: RESTORED }));
    await assert.rejects(reconcileLibraryReceipt(restored.api, { kind: 'add-negative', receipt }, {}), error => error.code === 'storage-changed');
    const moved = harness(library({ selected: pair({ positive_prompt_id: 8 }) }));
    await assert.rejects(reconcileLibraryReceipt(moved.api, { kind: 'add-negative', receipt }, {}), TypeError);
    await assert.rejects(reconcileLibraryReceipt(server.api, { kind: 'archive', receipt }, {}), TypeError);
});

test('unconfirmed pair archive retry is fenced against restored storage', async () => {
    const { createLibraryEditorController } = await source;
    assert.equal(typeof createLibraryEditorController, 'function', 'controller export');
    let epoch = EPOCH;
    const server = harness(call => library({ epoch, write: () => {
        epoch = RESTORED;
        return Promise.reject(new TypeError('Failed to fetch'));
    } })(call));
    const controller = createLibraryEditorController({ api: server.api, mode: 'metadata', positiveId: 7, pairId: 31 });
    await controller.initialize();
    await controller.setArchive(true);
    assert.equal(controller.state.phase, 'unknown-outcome');
    assert.equal(server.writes().length, 1);
    await controller.retryUnconfirmed();
    assert.equal(server.writes().length, 1, 'a restored database cannot take the retried pair write');
    assert.equal(controller.state.phase, 'storage-changed');
    assert.equal(controller.state.error.code, 'storage-changed');
    controller.dispose();
});
