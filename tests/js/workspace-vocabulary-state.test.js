const test = require('node:test');
const assert = require('node:assert/strict');
const {ApiError} = require('../../web/js/api-client.js');
// Guarded import: a missing module fails the assertions, not the file load.
const imported = import('../../web/js/workspace/vocabulary-state.mjs').catch(() => ({}));
const source = imported.then(module => {
    for (const name of ['createVocabularyStream', 'createVocabularyCommand', 'browseFilters']) {
        assert.equal(typeof module[name], 'function', `vocabulary-state.mjs exports ${name}`);
    }
    return module;
});

const EPOCH = '11111111-2222-4333-8444-555555555555';
const OTHER_EPOCH = '99999999-2222-4333-8444-555555555555';
const TOKEN = 'a'.repeat(64);
const tag = (id, name = `tag-${id}`, prompt_count = id) => ({id, name, prompt_count});
const category = (value, prompt_count = 1) => ({value, prompt_count});
const tagPage = (tags, cursor = null, {epoch = EPOCH, total = 3} = {}) =>
    ({success: true, library_epoch: epoch, total, has_more: cursor !== null, next_cursor: cursor, tags});
const categoryPage = (categories, cursor = null, {epoch = EPOCH, total = 3} = {}) =>
    ({success: true, library_epoch: epoch, total, has_more: cursor !== null, next_cursor: cursor, categories});
const queued = pages => {
    const calls = [];
    return {calls, api: {get: async (url, options) => {calls.push({url, options}); return pages.shift();}}};
};
const deferred = () => {
    const requests = [];
    return {requests, api: {get: (url, {signal}) => new Promise((resolve, reject) => requests.push({url, signal, resolve, reject}))}};
};
const params = url => new URL(url, 'https://x').searchParams;
const impact = {positive_prompts: 3, other_prompts: 1, negative_variants: 2, archived_negative_variants: 1};
const preview = (overrides = {}) => ({success: true, library_epoch: EPOCH, source: 'Landscapes', target: 'Scenery',
    target_exists: false, impact, impact_token: TOKEN, ...overrides});
const tagReceipt = (overrides = {}) => ({success: true, library_epoch: EPOCH, created: true, tag: tag(7, 'sunset', 0), ...overrides});
const renameReceipt = (overrides = {}) => ({success: true, library_epoch: EPOCH, merged: false, tag: tag(1, 'dusk', 2),
    components_changed: 2, pairs_changed: 1, ...overrides});

test('tag stream reads bounded fresh pages, carries the first epoch and dedupes appended ids', async () => {
    const {createVocabularyStream} = await source;
    const {calls, api} = queued([tagPage([tag(1), tag(2)], 'next'), tagPage([tag(2), tag(3)])]);
    const states = [];
    const stream = createVocabularyStream({api, kind: 'tags', onChange: state => states.push(state)});
    assert.equal(Object.isFrozen(stream.state), true);
    assert.equal(await stream.loadMore(), 2);
    assert.equal(stream.state.epoch, EPOCH);
    assert.equal(await stream.loadMore(), 1);
    assert.deepEqual(stream.state.items.map(row => row.id), [1, 2, 3]);
    assert.equal(Object.isFrozen(stream.state.items[0]), true);
    assert.equal(stream.state.hasMore, false);
    assert.equal(stream.state.total, 3);
    assert.equal(await stream.loadMore(), 0);
    assert.equal(calls.length, 2);
    assert.match(calls[0].url, /^\/prompt_manager\/vocabulary\/tags\?/);
    assert.equal(params(calls[0].url).get('limit'), '100');
    assert.equal(params(calls[0].url).has('cursor'), false);
    assert.equal(params(calls[1].url).get('cursor'), 'next');
    assert.equal(calls[0].options.cache, 'no-store');
    assert.ok(states.some(state => state.loading));
    stream.dispose();
});

test('category stream dedupes by value and keeps the first occurrence when keyset order shifts', async () => {
    const {createVocabularyStream} = await source;
    const {calls, api} = queued([categoryPage([category('Landscapes', 3), category('Portraits', 2)], 'c1'),
        categoryPage([category('Landscapes', 9), category('Studio', 1)])]);
    const stream = createVocabularyStream({api, kind: 'categories'});
    await stream.loadMore();
    assert.equal(await stream.loadMore(), 1);
    assert.deepEqual(stream.state.items.map(row => [row.value, row.prompt_count]),
        [['Landscapes', 3], ['Portraits', 2], ['Studio', 1]]);
    assert.match(calls[0].url, /^\/prompt_manager\/vocabulary\/categories\?/);
    stream.dispose();
});

test('malformed and nonprogressing pages are rejected atomically', async () => {
    const {createVocabularyStream} = await source;
    const malformedTags = [
        null, {}, {...tagPage([]), success: undefined}, {...tagPage([]), library_epoch: 'nope'},
        tagPage([tag(1, 'a', 1.5)]), tagPage([tag(1, 'a', -1)]), tagPage([tag(1, 'a', '3')]),
        tagPage([tag(0)]), tagPage([{...tag(1), name: 2}]), tagPage([tag(1), tag(1, 'other')]),
        {...tagPage([]), has_more: true}, {...tagPage([], 'next'), has_more: false},
        {...tagPage([]), next_cursor: ''}, {...tagPage([]), total: 1.5}, {...tagPage([]), tags: 'x'},
        {...tagPage([]), tags: undefined, categories: []},
    ];
    for (const malformed of malformedTags) {
        const stream = createVocabularyStream({api: {get: async () => malformed}, kind: 'tags'});
        await stream.loadMore();
        assert.ok(stream.state.error, JSON.stringify(malformed));
        assert.equal(stream.state.items.length, 0);
        assert.equal(stream.state.epoch, null);
        stream.dispose();
    }
    for (const malformed of [categoryPage([category('A'), category('A')]), categoryPage([category('A', 1.5)]),
        categoryPage([{value: 3, prompt_count: 1}]), categoryPage([category('')])]) {
        const stream = createVocabularyStream({api: {get: async () => malformed}, kind: 'categories'});
        await stream.loadMore();
        assert.ok(stream.state.error, JSON.stringify(malformed));
        stream.dispose();
    }
    const repeated = createVocabularyStream({api: {get: async () => tagPage([tag(1)], 'same')}, kind: 'tags'});
    await repeated.loadMore();
    await repeated.loadMore();
    assert.ok(repeated.state.error);
    assert.deepEqual(repeated.state.items.map(row => row.id), [1]);
    assert.equal(repeated.state.cursor, 'same');
    repeated.dispose();
    assert.throws(() => createVocabularyStream({api: {}, kind: 'images'}), TypeError);
});

test('reset aborts the older request and its late response cannot replace the first page', async () => {
    const {createVocabularyStream} = await source;
    const {requests, api} = deferred();
    const stream = createVocabularyStream({api, kind: 'tags'});
    const first = stream.loadMore();
    const second = stream.reset();
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(params(requests[1].url).has('cursor'), false);
    requests[1].resolve(tagPage([tag(2)]));
    assert.equal(await second, 1);
    requests[0].resolve(tagPage([tag(1)], 'old', {epoch: OTHER_EPOCH}));
    assert.equal(await first, 0);
    assert.deepEqual(stream.state.items.map(row => row.id), [2]);
    assert.equal(stream.state.epoch, EPOCH);
    const third = stream.reset();
    requests[2].reject(new Error('late failure'));
    const fourth = stream.reset();
    await third;
    assert.equal(stream.state.error, null);
    requests[3].resolve(tagPage([tag(5)]));
    await fourth;
    assert.deepEqual(stream.state.items.map(row => row.id), [5]);
    stream.dispose();
});

test('dispose aborts the in-flight read and ignores its late result', async () => {
    const {createVocabularyStream} = await source;
    const {requests, api} = deferred();
    const states = [];
    const stream = createVocabularyStream({api, kind: 'categories', onChange: state => states.push(state)});
    const pending = stream.loadMore();
    stream.dispose();
    assert.equal(requests[0].signal.aborted, true);
    const count = states.length;
    requests[0].resolve(categoryPage([category('A')]));
    assert.equal(await pending, 0);
    assert.equal(states.length, count);
    assert.equal(await stream.loadMore(), 0);
    assert.equal(await stream.reset(), 0);
    assert.equal(requests.length, 1);
});

test('a changed epoch on append is an error that requires a reset', async () => {
    const {createVocabularyStream} = await source;
    const {calls, api} = queued([tagPage([tag(1)], 'next'), tagPage([tag(2)], null, {epoch: OTHER_EPOCH}),
        tagPage([tag(4)], null, {epoch: OTHER_EPOCH})]);
    const stream = createVocabularyStream({api, kind: 'tags'});
    await stream.loadMore();
    assert.equal(await stream.loadMore(), 0);
    assert.equal(stream.state.error.code, 'storage-changed');
    assert.equal(stream.state.resetRequired, true);
    assert.deepEqual(stream.state.items.map(row => row.id), [1]);
    assert.equal(stream.state.epoch, EPOCH);
    assert.equal(await stream.loadMore(), 0);
    assert.equal(calls.length, 2);
    assert.equal(await stream.reset(), 1);
    assert.equal(stream.state.resetRequired, false);
    assert.equal(stream.state.epoch, OTHER_EPOCH);
    assert.deepEqual(stream.state.items.map(row => row.id), [4]);
    stream.dispose();
});

test('failed reads keep loaded rows and cursor so Retry continues', async () => {
    const {createVocabularyStream} = await source;
    let calls = 0;
    const stream = createVocabularyStream({kind: 'tags', api: {get: async () => {
        calls += 1;
        if (calls === 1) return tagPage([tag(1)], 'next');
        if (calls === 2) throw new Error('offline');
        return tagPage([tag(2)]);
    }}});
    await stream.loadMore();
    await stream.loadMore();
    assert.match(stream.state.error.message, /offline/);
    assert.equal(stream.state.resetRequired, false);
    assert.equal(stream.state.cursor, 'next');
    assert.equal(await stream.loadMore(), 1);
    assert.equal(stream.state.error, null);
    stream.dispose();
});

test('create tag sends the epoch once, completes with the receipt and re-reads streams from the first page', async () => {
    const {createVocabularyCommand, createVocabularyStream} = await source;
    const reads = queued([tagPage([tag(1)], 'next'), tagPage([tag(1), tag(7, 'sunset', 0)])]);
    const stream = createVocabularyStream({api: reads.api, kind: 'tags'});
    await stream.loadMore();
    const posts = [];
    const invalidations = [];
    const command = createVocabularyCommand({
        api: {post: async (url, body) => {posts.push({url, body}); return tagReceipt();}},
        onInvalidate: event => {invalidations.push(event); stream.reset();},
    });
    assert.equal(command.state.phase, 'idle');
    await command.createTag('sunset', EPOCH);
    assert.deepEqual(posts, [{url: '/prompt_manager/vocabulary/tags', body: {expected_epoch: EPOCH, name: 'sunset'}}]);
    assert.equal(command.state.phase, 'done');
    assert.equal(command.state.kind, 'create-tag');
    assert.equal(command.state.receipt.tag.name, 'sunset');
    assert.equal(Object.isFrozen(command.state.receipt), true);
    assert.equal(invalidations.length, 1);
    assert.equal(invalidations[0].phase, 'done');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(params(reads.calls[1].url).has('cursor'), false);
    assert.deepEqual(stream.state.items.map(row => row.id), [1, 7]);
    await command.createTag('again', EPOCH);
    assert.equal(posts.length, 1, 'done is terminal until reset()');
    command.reset();
    assert.equal(command.state.phase, 'idle');
    stream.dispose();
});

test('rename and delete carry the exact tag identity and invalidate after success', async () => {
    const {createVocabularyCommand} = await source;
    const calls = [];
    const invalidations = [];
    const command = createVocabularyCommand({
        api: {
            patch: async (url, body) => {calls.push({method: 'patch', url, body}); return renameReceipt({merged: true});},
            post: async (url, body) => {calls.push({method: 'post', url, body});
                return {success: true, library_epoch: EPOCH, deleted: true, components_changed: 1, pairs_changed: 0};},
        },
        onInvalidate: event => invalidations.push(event),
    });
    await command.renameTag({id: 1, name: 'dawn', prompt_count: 2}, 'dusk', EPOCH);
    assert.deepEqual(calls[0], {method: 'patch', url: '/prompt_manager/vocabulary/tags/1',
        body: {expected_epoch: EPOCH, name: 'dawn', new_name: 'dusk'}});
    assert.equal(command.state.receipt.merged, true);
    command.reset();
    await command.deleteTag({id: 1, name: 'dawn'}, EPOCH);
    assert.deepEqual(calls[1], {method: 'post', url: '/prompt_manager/vocabulary/tags/1/delete',
        body: {expected_epoch: EPOCH, name: 'dawn'}});
    assert.equal(command.state.phase, 'done');
    assert.equal(command.state.kind, 'delete-tag');
    assert.deepEqual(invalidations.map(event => event.phase), ['done', 'done']);
    command.reset();
    assert.throws(() => command.renameTag({id: 0, name: 'x'}, 'y', EPOCH), TypeError);
    assert.throws(() => command.createTag('x', 'not-an-epoch'), TypeError);
    assert.throws(() => command.deleteTag({id: 1}, EPOCH), TypeError);
    assert.equal(calls.length, 2);
});

test('409 storage-changed and vocabulary-conflict map to recovery phases; other failures stay honest', async () => {
    const {createVocabularyCommand} = await source;
    const cases = [
        [new ApiError(409, 'restored', 'storage-changed'), 'storage-changed', true],
        [new ApiError(409, 'changed', 'vocabulary-conflict'), 'conflict', true],
        [new ApiError(409, 'other', null), 'write-error', false],
        [new ApiError(400, 'Tag names cannot contain commas', null), 'write-error', false],
        [new ApiError(502, 'Bad gateway'), 'unknown-outcome', true],
        [new ApiError(200, 'Invalid JSON response'), 'unknown-outcome', true],
    ];
    for (const [error, phase, invalidates] of cases) {
        const invalidations = [];
        const command = createVocabularyCommand({api: {post: async () => {throw error;}}, onInvalidate: event => invalidations.push(event)});
        await command.createTag('sunset', EPOCH);
        assert.equal(command.state.phase, phase, `${error.status} ${error.code}`);
        assert.equal(command.state.error, error);
        assert.equal(invalidations.length, invalidates ? 1 : 0);
        command.dispose();
    }
    const invalid = createVocabularyCommand({api: {post: async () => tagReceipt({library_epoch: OTHER_EPOCH})}});
    await invalid.createTag('sunset', EPOCH);
    assert.equal(invalid.state.phase, 'unknown-outcome');
    const retryable = createVocabularyCommand({api: {post: async () => {throw new ApiError(400, 'bad');}}});
    await retryable.createTag('a,b', EPOCH);
    await retryable.createTag('ab', EPOCH);
    assert.equal(retryable.state.phase, 'write-error');
});

test('a network TypeError is an unknown outcome and is never retried automatically', async () => {
    const {createVocabularyCommand} = await source;
    let calls = 0;
    const command = createVocabularyCommand({api: {patch: async () => {calls += 1; throw new TypeError('Failed to fetch');}}});
    await command.renameTag({id: 3, name: 'dawn'}, 'dusk', EPOCH);
    assert.equal(command.state.phase, 'unknown-outcome');
    assert.equal(calls, 1);
    await command.renameTag({id: 3, name: 'dawn'}, 'dusk', EPOCH);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 1);
    command.reset();
    assert.equal(command.state.phase, 'idle');
    assert.equal(command.state.error, null);
});

test('a second Apply while saving is ignored', async () => {
    const {createVocabularyCommand} = await source;
    const posts = [];
    let release;
    const command = createVocabularyCommand({api: {post: (url, body) => {
        posts.push({url, body});
        if (url.endsWith('/preview')) return Promise.resolve(preview());
        return new Promise(resolve => {release = resolve;});
    }}});
    await command.previewCategory('Landscapes', 'Scenery', EPOCH);
    const shown = command.state.preview;
    const first = command.applyCategory(shown);
    assert.equal(command.state.phase, 'saving');
    await command.applyCategory(shown);
    await command.createTag('x', EPOCH);
    await command.previewCategory('Landscapes', 'Other', EPOCH);
    command.edit();
    command.reset();
    assert.equal(command.state.phase, 'saving');
    assert.equal(posts.length, 2);
    release({success: true, library_epoch: EPOCH, source: 'Landscapes', target: 'Scenery', consolidated: false, impact});
    await first;
    assert.equal(command.state.phase, 'done');
    assert.deepEqual(posts[1], {url: '/prompt_manager/vocabulary/categories/apply',
        body: {expected_epoch: EPOCH, source: 'Landscapes', target: 'Scenery', impact_token: TOKEN}});
});

test('category preview shows real impact; editing its inputs returns to idle and a stale preview cannot apply', async () => {
    const {createVocabularyCommand} = await source;
    const posts = [];
    const invalidations = [];
    const responses = [preview(), preview({target: 'Scenery', target_exists: true})];
    const command = createVocabularyCommand({
        api: {post: async (url, body) => {posts.push({url, body}); return responses.shift();}},
        onInvalidate: event => invalidations.push(event),
    });
    await command.previewCategory('Landscapes', ' Scenery ', EPOCH);
    assert.deepEqual(posts[0], {url: '/prompt_manager/vocabulary/categories/preview',
        body: {expected_epoch: EPOCH, source: 'Landscapes', target: ' Scenery '}});
    assert.equal(command.state.phase, 'previewed');
    assert.equal(command.state.kind, 'preview-category');
    const stale = command.state.preview;
    assert.deepEqual(stale.impact, impact);
    assert.equal(invalidations.length, 0, 'a preview writes nothing');
    command.edit();
    assert.equal(command.state.phase, 'idle');
    assert.equal(command.state.preview, null);
    await command.applyCategory(stale);
    assert.equal(posts.length, 1);
    await command.previewCategory('Landscapes', 'Scenery', EPOCH);
    assert.equal(command.state.preview.target_exists, true);
    await command.applyCategory(stale);
    assert.equal(posts.length, 2, 'only the preview on screen can be applied');
    command.edit();
    command.edit();
    assert.equal(command.state.phase, 'idle');
    for (const malformed of [preview({source: 'Portraits'}), preview({impact_token: 'short'}),
        preview({impact: {...impact, positive_prompts: 1.5}}), preview({library_epoch: OTHER_EPOCH})]) {
        const check = createVocabularyCommand({api: {post: async () => malformed}});
        await check.previewCategory('Landscapes', 'Scenery', EPOCH);
        assert.equal(check.state.phase, 'write-error', JSON.stringify(malformed));
        assert.equal(check.state.preview, null);
    }
    const lost = createVocabularyCommand({api: {post: async () => {throw new TypeError('Failed to fetch');}}});
    await lost.previewCategory('Landscapes', 'Scenery', EPOCH);
    assert.equal(lost.state.phase, 'write-error', 'a lost preview read wrote nothing');
    const conflicted = createVocabularyCommand({api: {post: async () => {throw new ApiError(409, 'gone', 'vocabulary-conflict');}}});
    await conflicted.previewCategory('Landscapes', 'Scenery', EPOCH);
    assert.equal(conflicted.state.phase, 'conflict');
    assert.throws(() => command.previewCategory(3, 'Scenery', EPOCH), TypeError);
});

test('an edit while a preview is pending discards its late result so Apply cannot send the old values', async () => {
    const {createVocabularyCommand} = await source;
    const posts = [];
    const pending = [];
    const command = createVocabularyCommand({api: {post: (url, body) => {
        posts.push({url, body});
        return new Promise((resolve, reject) => pending.push({resolve, reject}));
    }}});
    const first = command.previewCategory('Landscapes', 'Scenery', EPOCH);
    assert.equal(command.state.phase, 'saving');
    command.edit();
    assert.equal(command.state.phase, 'saving', 'the request is still in flight');
    pending.shift().resolve(preview());
    await first;
    assert.equal(command.state.phase, 'idle', 'the late preview describes values no longer on screen');
    assert.equal(command.state.preview, null);
    await command.applyCategory(preview());
    assert.equal(posts.length, 1, 'nothing is applied from a discarded preview');
    // A fresh preview of the edited values is applicable as usual.
    const second = command.previewCategory('Landscapes', 'Vistas', EPOCH);
    pending.shift().resolve(preview({target: 'Vistas'}));
    await second;
    assert.equal(command.state.phase, 'previewed');
    assert.equal(command.state.preview.target, 'Vistas');
    // A discarded preview's plain failure is not shown either; a recovery outcome still is, since it describes storage.
    command.edit();
    const failed = command.previewCategory('Landscapes', 'Vistas', EPOCH);
    command.edit();
    pending.shift().reject(new TypeError('Failed to fetch'));
    await failed;
    assert.equal(command.state.phase, 'idle');
    assert.equal(command.state.error, null);
    const restored = command.previewCategory('Landscapes', 'Vistas', EPOCH);
    command.edit();
    pending.shift().reject(new ApiError(409, 'restored', 'storage-changed'));
    await restored;
    assert.equal(command.state.phase, 'storage-changed');
});

test('a token conflict on apply requires a new preview', async () => {
    const {createVocabularyCommand} = await source;
    const responses = [() => preview(), () => {throw new ApiError(409, 'changed', 'vocabulary-conflict');}, () => preview()];
    const command = createVocabularyCommand({api: {post: async () => responses.shift()()}});
    await command.previewCategory('Landscapes', 'Scenery', EPOCH);
    await command.applyCategory(command.state.preview);
    assert.equal(command.state.phase, 'conflict');
    assert.equal(command.state.preview, null);
    await command.previewCategory('Landscapes', 'Scenery', EPOCH);
    assert.equal(command.state.phase, 'previewed');
});

test('dispose ignores late command results but a landed write still invalidates once', async () => {
    const {createVocabularyCommand} = await source;
    let release;
    const states = [];
    const invalidations = [];
    const command = createVocabularyCommand({api: {post: () => new Promise(resolve => {release = resolve;})},
        onChange: state => states.push(state), onInvalidate: event => invalidations.push(event)});
    const pending = command.createTag('sunset', EPOCH);
    command.dispose();
    const count = states.length;
    const receipt = tagReceipt();
    release(receipt);
    await pending;
    assert.equal(states.length, count, 'a disposed dialog is never rendered again');
    // The write committed after its dialog went away: cached Library views must still be dropped.
    assert.equal(invalidations.length, 1);
    assert.equal(invalidations[0].phase, 'done');
    assert.equal(invalidations[0].kind, 'create-tag');
    assert.deepEqual(invalidations[0].receipt, receipt);
    assert.ok(Object.isFrozen(invalidations[0]));
    assert.equal(command.state.phase, 'disposed');
    await command.createTag('x', EPOCH);
    assert.equal(invalidations.length, 1);
});

test('after dispose an unconfirmed write invalidates and a failed write does not', async () => {
    const {createVocabularyCommand} = await source;
    const cases = [
        [new TypeError('Failed to fetch'), 'unknown-outcome', 1],
        [new ApiError(409, 'changed', 'vocabulary-conflict'), 'conflict', 1],
        [new ApiError(400, 'Tag names cannot contain commas'), null, 0],
    ];
    for (const [error, phase, expected] of cases) {
        let fail;
        const invalidations = [];
        const command = createVocabularyCommand({api: {patch: () => new Promise((resolve, reject) => {fail = reject;})},
            onInvalidate: event => invalidations.push(event)});
        const pending = command.renameTag({id: 3, name: 'dawn'}, 'dusk', EPOCH);
        command.dispose();
        fail(error);
        await pending;
        assert.equal(invalidations.length, expected, error.message);
        if (expected) assert.deepEqual({...invalidations[0]}, {phase, kind: 'rename-tag', receipt: null});
        assert.equal(command.state.phase, 'disposed');
    }
});

test('browseFilters hands Library one exact constraint', async () => {
    const {browseFilters} = await source;
    assert.deepEqual(browseFilters('tags', 'a'), {text: '', category: '', tags: ['a'], sort: 'newest'});
    assert.deepEqual(browseFilters('categories', ' A '), {text: '', category: ' A ', tags: [], sort: 'newest'});
    assert.throws(() => browseFilters('images', 'a'), TypeError);
    assert.throws(() => browseFilters('tags', 2), TypeError);
    assert.throws(() => browseFilters('categories', ''), TypeError);
});

const helpers = imported.then(module => {
    // Count wording comes from the shared plural.mjs helper (#243); this module no longer exports its own.
    assert.equal(module.noun, undefined);
    for (const name of ['readTagImpact', 'categoryPreviewText', 'deleteTagText']) {
        assert.equal(typeof module[name], 'function', `vocabulary-state.mjs exports ${name}`);
    }
    return module;
});
const zero = {positive_prompts: 0, other_prompts: 0, negative_variants: 0, archived_negative_variants: 0};

test('readTagImpact reads one fresh, validated impact for the exact tag id', async () => {
    const {readTagImpact} = await helpers;
    const calls = [];
    const signal = new AbortController().signal;
    const api = {get: async (url, options) => {calls.push({url, options}); return {success: true, library_epoch: EPOCH, impact};}};
    const value = await readTagImpact(api, {id: 4, name: 'mist'}, {signal});
    assert.deepEqual(calls, [{url: '/prompt_manager/vocabulary/tags/4/impact', options: {signal, cache: 'no-store'}}]);
    assert.deepEqual(value, {library_epoch: EPOCH, impact});
    assert.ok(Object.isFrozen(value) && Object.isFrozen(value.impact));
    for (const malformed of [null, {success: false}, {success: true, library_epoch: 'x', impact},
        {success: true, library_epoch: EPOCH, impact: {...impact, other_prompts: -1}}, {success: true, library_epoch: EPOCH}]) {
        await assert.rejects(readTagImpact({get: async () => malformed}, {id: 4, name: 'mist'}), TypeError, JSON.stringify(malformed));
    }
    assert.throws(() => readTagImpact(api, {id: 0, name: 'mist'}), TypeError);
});

test('category preview text states the real impact and whether it renames or consolidates', async () => {
    const {categoryPreviewText} = await helpers;
    assert.equal(categoryPreviewText(preview()),
        '“Landscapes” is used by 3 positive prompts, 3 negative variants (1 archived) and 1 other saved prompt. Apply renames it to “Scenery”.');
    assert.equal(categoryPreviewText(preview({target: 'Architecture', target_exists: true,
        impact: {...zero, positive_prompts: 1, negative_variants: 1}})),
    '“Landscapes” is used by 1 positive prompt and 1 negative variant. Apply consolidates it into the existing “Architecture”.');
    assert.equal(categoryPreviewText(preview({impact: {...zero, other_prompts: 2}})),
        '“Landscapes” is used by 2 other saved prompts. Apply renames it to “Scenery”.');
});

test('accepts image_count and names images in the delete text', async () => {
    const {createVocabularyStream, createVocabularyCommand, readTagImpact, deleteTagText} = await helpers;
    const rows = [{...tag(1, 'mist', 2), image_count: 3}, tag(2, 'dusk', 0)];
    const stream = createVocabularyStream({api: queued([tagPage(rows)]).api, kind: 'tags'});
    assert.equal(await stream.loadMore(), 2);
    assert.deepEqual(stream.state.items.map(row => row.image_count), [3, undefined]);
    for (const bad of [{...tag(1), image_count: -1}, {...tag(1), image_count: '3'}, {...tag(1), image_count: 1.5}]) {
        const rejected = createVocabularyStream({api: queued([tagPage([bad])]).api, kind: 'tags'});
        await rejected.loadMore();
        assert.ok(rejected.state.error instanceof TypeError, JSON.stringify(bad));
        assert.deepEqual(rejected.state.items, []);
    }
    const withImages = {...impact, images: 2};
    const api = {get: async () => ({success: true, library_epoch: EPOCH, impact: withImages})};
    assert.deepEqual((await readTagImpact(api, {id: 4, name: 'mist'})).impact, withImages);
    await assert.rejects(readTagImpact({get: async () => ({success: true, library_epoch: EPOCH, impact: {...impact, images: -2}})},
        {id: 4, name: 'mist'}), TypeError);
    const command = createVocabularyCommand({api: {
        patch: async () => renameReceipt({tag: {...tag(1, 'dusk', 2), image_count: 1}, images_changed: 1}),
        post: async () => ({success: true, library_epoch: EPOCH, deleted: true, components_changed: 0, pairs_changed: 0, images_changed: 2}),
    }});
    await command.renameTag({id: 1, name: 'dawn', prompt_count: 2}, 'dusk', EPOCH);
    assert.equal(command.state.phase, 'done');
    assert.equal(command.state.receipt.images_changed, 1);
    command.reset();
    await command.deleteTag({id: 1, name: 'dusk'}, EPOCH);
    assert.equal(command.state.phase, 'done');
    const malformed = createVocabularyCommand({api: {post: async () => ({success: true, library_epoch: EPOCH, deleted: true,
        components_changed: 0, pairs_changed: 0, images_changed: 'two'})}});
    await malformed.deleteTag({id: 1, name: 'dusk'}, EPOCH);
    assert.equal(malformed.state.phase, 'unknown-outcome');
    assert.equal(deleteTagText('mist', withImages),
        'Deleting “mist” removes the name and its links from 3 positive prompts, 3 negative variants (1 archived), 1 other saved prompt and 2 images. The prompts, negative variants, images and history themselves are kept.');
    assert.equal(deleteTagText('mist', {...zero, images: 1}),
        'Deleting “mist” removes the name and its links from 1 image. The prompts, negative variants, images and history themselves are kept.');
    assert.equal(deleteTagText('unused', {...zero, images: 0}),
        '“unused” is not linked to any saved prompt or image. Deleting it removes only the name.');
});

test('delete text names the tag, its real links and what stays unchanged', async () => {
    const {deleteTagText} = await helpers;
    assert.equal(deleteTagText('mist', impact),
        'Deleting “mist” removes the name and its links from 3 positive prompts, 3 negative variants (1 archived) and 1 other saved prompt. The prompts, negative variants, images and history themselves are kept.');
    assert.equal(deleteTagText('unused', zero),
        '“unused” is not linked to any saved prompt or image. Deleting it removes only the name.');
});

test('a category row counts prompts and images, and an image-only row browses Images (#332 D143)', async () => {
    const {categoryRowText, categoryBrowseTarget, categoryPreviewText} = await import('../../web/js/workspace/vocabulary-state.mjs');
    assert.equal(categoryRowText({value: 'L', prompt_count: 3}), '3 positive prompts');
    assert.equal(categoryRowText({value: 'L', prompt_count: 1, image_count: 0}), '1 positive prompt');
    assert.equal(categoryRowText({value: 'L', prompt_count: 3, image_count: 2}), '3 positive prompts · 2 images');
    assert.equal(categoryRowText({value: 'S', prompt_count: 0, image_count: 1}), '1 image');
    assert.equal(categoryBrowseTarget({value: 'L', prompt_count: 3, image_count: 2}), 'library');
    assert.equal(categoryBrowseTarget({value: 'S', prompt_count: 0, image_count: 2}), 'images');
    const impact = {positive_prompts: 3, other_prompts: 0, negative_variants: 0, archived_negative_variants: 0, images: 2};
    assert.equal(categoryPreviewText({source: 'Landscapes', target: 'Landscape', target_exists: false, impact}),
        '“Landscapes” is used by 3 positive prompts and 2 images. Apply renames it to “Landscape”.');
});
