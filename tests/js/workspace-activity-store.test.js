// Activity (#249 Activity-1): the session job store behind the top-bar button and pill and the Activity page.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/activity-store.mjs');

const ID_A = 'a'.repeat(32);
const ID_B = 'b'.repeat(32);
const ID_C = 'c'.repeat(32);

function summary(overrides = {}) {
    return { id: ID_A, kind: 'scan', title: 'Scan for metadata', state: 'running', started_at: 1000, finished_at: null,
        progress: 0, status_line: '', processed: 0, total: 0,
        counters: { added: 0, known: 0, review: 0, no_metadata: 0, errors: 0 }, scope: { files: 'new', output_folders: 2 },
        ...overrides };
}

function apiError(status, message, code) {
    return Object.assign(new Error(message), { status, code });
}

/** A fake world: the API's answers, every opened stream, the pending timers and the toasts. */
function harness() {
    const world = { list: [], listError: null, posts: [], postResult: null, postError: null, streams: [], timers: [],
        toasts: [], gets: 0 };
    const cleanups = [];
    const controller = new AbortController();
    const scope = { signal: controller.signal, cleanup: fn => cleanups.push(fn) };
    const api = {
        async get(url, options) {
            assert.equal(url, '/prompt_manager/jobs');
            assert.equal(options.cache, 'no-store');
            world.gets += 1;
            if (world.listError) throw world.listError;
            return { success: true, jobs: world.list };
        },
        async post(url, body) {
            world.posts.push([url, body]);
            if (world.postError) throw world.postError;
            return world.postResult;
        },
    };
    const connect = url => {
        const stream = { url, closed: false, onmessage: null, onerror: null, close() { this.closed = true; } };
        world.streams.push(stream);
        return stream;
    };
    const timers = {
        setTimeout(fn, delay) { const timer = { fn, delay, cleared: false }; world.timers.push(timer); return timer; },
        clearTimeout(timer) { timer.cleared = true; },
    };
    world.dispose = () => { controller.abort(); cleanups.forEach(fn => fn()); };
    world.fire = async () => {
        const timer = world.timers.find(item => !item.cleared && !item.fired);
        timer.fired = true;
        timer.fn();
        await new Promise(resolve => setImmediate(resolve));
        return timer.delay;
    };
    world.open = () => world.streams.filter(stream => !stream.closed);
    return { world, api, scope, connect, timers, pollMs: 0, notify: message => world.toasts.push(message) };
}

const send = (stream, job) => stream.onmessage({ data: JSON.stringify(job) });

test('store merges list and SSE updates and caps backoff', async () => {
    const { createActivityStore, BACKOFF_MAX_MS, LOST_DETAIL } = await source;
    const { world, ...deps } = harness();
    world.list = [summary(), summary({ id: ID_B, state: 'finished', finished_at: 900, counters: { added: 3, errors: 0 } })];
    const store = createActivityStore(deps);
    let changes = 0;
    store.subscribe(() => { changes += 1; });
    await store.load();
    assert.equal(store.status, 'ready');
    assert.deepEqual(store.jobs().map(job => [job.id, job.state]), [[ID_A, 'running'], [ID_B, 'finished']]);
    assert.deepEqual(store.running().map(job => job.id), [ID_A]);
    // One stream per running job only; a finished job is not followed.
    assert.deepEqual(world.streams.map(stream => stream.url), [`/prompt_manager/jobs/${ID_A}/events`]);
    assert.ok(changes >= 1);

    // An SSE summary replaces the listed one; garbage and another job's summary are ignored.
    const [stream] = world.streams;
    send(stream, summary({ progress: 42, processed: 8240, total: 19622 }));
    stream.onmessage({ data: 'not json' });
    send(stream, summary({ id: ID_B, progress: 99 }));
    assert.equal(store.find(ID_A).progress, 42);
    assert.equal(store.find(ID_B).state, 'finished');
    assert.deepEqual(world.toasts, []);

    // The end of the job: the finish toast, and the stream is closed.
    send(stream, summary({ state: 'finished', progress: 100, finished_at: 2000, counters: { added: 1420, errors: 18 } }));
    assert.deepEqual(world.toasts, ['Scan finished · 1,420 added · 18 errors']);
    assert.equal(stream.closed, true);
    assert.deepEqual(store.running(), []);

    // A new running job from another tab, then the server goes away: every re-read waits longer, up to 30 s.
    world.list = [summary({ id: ID_C, state: 'running' })];
    await store.load();
    const second = world.open()[0];
    assert.equal(second.url, `/prompt_manager/jobs/${ID_C}/events`);
    world.listError = new Error('connection refused');
    second.onerror();
    assert.equal(second.closed, true);
    const delays = [];
    for (let index = 0; index < 8; index += 1) delays.push(await world.fire());
    assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
    assert.ok(delays.every(delay => delay <= BACKOFF_MAX_MS));
    // While the server cannot be read the running job shows as ended, so no pill stays.
    assert.equal(store.status, 'error');
    assert.equal(store.find(ID_C).state, 'lost');
    assert.deepEqual(store.running(), []);
    assert.equal(typeof LOST_DETAIL, 'string');

    // The server is back without the job (a restart): it stays ended; finished jobs the server no longer lists are
    // dropped, so the list never outgrows the server's (review M3).
    world.listError = null;
    world.list = [];
    await world.fire();
    assert.equal(store.status, 'ready');
    assert.deepEqual(store.jobs().map(job => [job.id, job.state]), [[ID_C, 'lost']]);
    assert.equal(world.timers.filter(timer => !timer.cleared && !timer.fired).length, 0, 'no retry after a good read');
    assert.equal(world.toasts.length, 1, 'an ended job has no finish toast');
    world.dispose();
});

test('a dropped stream of a still running job reattaches after the backoff, then resets it', async () => {
    const { createActivityStore } = await source;
    const { world, ...deps } = harness();
    world.list = [summary()];
    const store = createActivityStore(deps);
    await store.load();
    world.streams[0].onerror();
    assert.equal(await world.fire(), 1000);
    const again = world.open();
    assert.equal(again.length, 1);
    assert.equal(store.find(ID_A).state, 'running');
    again[0].onerror();
    assert.equal(await world.fire(), 2000);
    send(world.open()[0], summary({ progress: 5 }));
    world.open()[0].onerror();
    assert.equal(await world.fire(), 1000, 'a received message resets the backoff');
    // A second drop while a re-read is already scheduled does not schedule another.
    world.open()[0].onerror();
    store.load();
    assert.equal(world.timers.filter(timer => !timer.cleared && !timer.fired).length, 1);
    await new Promise(resolve => setImmediate(resolve));
    world.dispose();
    assert.ok(world.streams.every(stream => stream.closed), 'dispose closes every stream');
    assert.ok(world.timers.every(timer => timer.cleared || timer.fired), 'dispose clears the pending re-read');
});

test('start returns busy with the running job', async () => {
    const { createActivityStore } = await source;
    const { world, ...deps } = harness();
    const store = createActivityStore(deps);
    world.postResult = { success: true, job: summary() };
    const started = await store.start('scan');
    assert.equal(started.job.id, ID_A);
    assert.deepEqual(world.posts, [['/prompt_manager/jobs/scan', {}]]);
    assert.deepEqual(world.toasts, ['Scan started.']);
    assert.equal(world.open().length, 1, 'the started job is followed');

    world.postError = apiError(409, 'Scan for metadata is already running', 'busy');
    world.list = [summary()];
    const busy = await store.start('scan');
    assert.equal(busy.busy.id, ID_A);
    assert.equal(busy.busy.title, 'Scan for metadata');
    assert.equal(busy.message, 'Scan for metadata is already running');
    assert.equal(world.toasts.length, 1, 'a refused start has no start toast');

    world.postError = apiError(500, 'boom', null);
    await assert.rejects(store.start('scan'), /boom/);
    world.postError = null;
    world.postResult = { success: true, job: { id: 'nope' } };
    await assert.rejects(store.start('scan'), /Invalid job/);

    world.postResult = { success: true, cancellation_requested: true };
    assert.equal(await store.cancel(ID_A), true);
    assert.deepEqual(world.posts.at(-1), [`/prompt_manager/jobs/${ID_A}/cancel`, {}]);
    world.postResult = { success: true, cancellation_requested: false };
    assert.equal(await store.cancel(ID_A), false);
    world.dispose();
});

test('track follows a job another route started, with no read of its own', async () => {
    const { createActivityStore } = await source;
    const { world, ...deps } = harness();
    const store = createActivityStore(deps);
    const seen = [];
    store.subscribe(() => seen.push(store.find(ID_A)?.state));
    const job = store.track(summary({ kind: 'health', title: 'Health check' }));
    assert.equal(job.id, ID_A);
    assert.equal(world.gets, 0, 'the started job is known without a list read');
    assert.deepEqual(world.open().map(stream => stream.url), [`/prompt_manager/jobs/${ID_A}/events`]);
    assert.deepEqual(world.toasts, [], 'the route that started it says so itself');
    send(world.open()[0], summary({ kind: 'health', title: 'Health check', state: 'finished', progress: 100 }));
    assert.deepEqual(seen, ['running', 'finished']);
    assert.equal(world.open().length, 0, 'an ended job is no longer followed');
    assert.throws(() => store.track({ id: 'nope' }), /Invalid job/);
    world.dispose();
});

test('a list that is not a job list counts as a failed read', async () => {
    const { createActivityStore } = await source;
    const { world, api, ...deps } = harness();
    const store = createActivityStore({ ...deps, api: { ...api, get: async () => ({ success: true }) } });
    await store.load();
    assert.equal(store.status, 'error');
    assert.equal(world.timers.length, 1);
    world.dispose();
    await store.load();
    assert.equal(store.status, 'error', 'a disposed store reads nothing');
});

test('a visible tab reads the list again every 15 s; a hidden one skips its turn', async () => {
    const { createActivityStore, POLL_MS } = await source;
    const { world, ...deps } = harness();
    let visible = true;
    const store = createActivityStore({ ...deps, pollMs: POLL_MS, isVisible: () => visible });
    await store.load();
    assert.equal(world.gets, 1);
    // A job started in another tab appears on the next read.
    world.list = [summary()];
    assert.equal(await world.fire(), 15000);
    assert.equal(world.gets, 2);
    assert.deepEqual(store.running().map(job => job.id), [ID_A]);
    visible = false;
    assert.equal(await world.fire(), 15000);
    assert.equal(world.gets, 2, 'a hidden tab does not read');
    visible = true;
    await world.fire();
    assert.equal(world.gets, 3);
    // A manual read replaces the pending poll rather than adding one.
    await store.load();
    assert.equal(world.timers.filter(timer => !timer.cleared && !timer.fired).length, 1);
    world.dispose();
    assert.ok(world.timers.every(timer => timer.cleared || timer.fired));
});

test('a late or older answer never brings an ended job back, so it is announced once', async () => {
    const { createActivityStore } = await source;
    const { world, api, ...deps } = harness();
    const answers = [];
    const slowApi = { ...api, get: () => new Promise(resolve => answers.push(resolve)) };
    const store = createActivityStore({ ...deps, api: slowApi });
    const first = store.load();
    const second = store.load();
    // The newer read answers first; the older one, answering later with stale data, is dropped.
    answers[1]({ success: true, jobs: [summary()] });
    await second;
    answers[0]({ success: true, jobs: [] });
    await first;
    assert.deepEqual(store.jobs().map(job => job.state), ['running']);
    send(world.open()[0], summary({ state: 'finished', counters: { added: 2, errors: 0 } }));
    assert.deepEqual(world.toasts, ['Scan finished · 2 added · 0 errors']);
    // A list read sent before the end still says running: the job stays finished and no stream reopens.
    const third = store.load();
    answers[2]({ success: true, jobs: [summary()] });
    await third;
    assert.equal(store.find(ID_A).state, 'finished');
    assert.equal(world.open().length, 0);
    assert.equal(world.toasts.length, 1);
    // A stale failure is dropped too.
    const failing = store.load();
    const newest = store.load();
    answers[4]({ success: true, jobs: [] });
    await newest;
    answers[3](Promise.reject(new Error('late failure')));
    await failing;
    assert.equal(store.status, 'ready');
    world.dispose();
});

test('summaries are validated and the copy follows the spec', async () => {
    const { parseJob, finishToast, startToast, pillParts, outcomeLine, backoffDelay } = await source;
    for (const raw of [null, [], { id: 'x', state: 'running' }, summary({ state: 'lost' }), summary({ id: ID_A.toUpperCase() })]) {
        assert.equal(parseJob(raw), null);
    }
    const loose = parseJob({ id: ID_A, state: 'running', progress: 140.4, processed: -1, total: 1.5, counters: { added: 'x', errors: 2 },
        title: '  ', started_at: 'x', scope: [] });
    assert.deepEqual(loose, { id: ID_A, kind: '', title: 'Background job', state: 'running', started_at: null, finished_at: null,
        progress: 100, status_line: '', processed: 0, total: 0, counters: { added: 0, errors: 2 }, scope: {}, listed: {}, log_length: 0,
        cancellable: true });
    assert.equal(parseJob(summary({ progress: -3 })).progress, 0);
    // A held legacy job cannot be cancelled; an older server sends no key, which keeps Cancel.
    assert.equal(parseJob(summary({ cancellable: false })).cancellable, false);
    assert.equal(parseJob(summary({ cancellable: true })).cancellable, true);
    assert.equal(parseJob(summary()).cancellable, true);

    const scan = parseJob(summary({ progress: 42, processed: 8240, total: 19622, counters: { added: 1, errors: 1 } }));
    assert.deepEqual(pillParts(scan), { label: 'Scanning · 42%', files: ' · 8,240 / 19,622', text: 'Scanning · 42% · 8,240 / 19,622' });
    // D111: without a total the progress is indeterminate, so the pill shows no percentage.
    assert.equal(pillParts({ ...scan, total: 0 }).text, 'Scanning');
    // A kind without a pill verb shows its title (D111: thumbnails have their own, see workspace-activity-kinds.test.js).
    assert.equal(pillParts({ ...scan, kind: 'backup', title: 'Backup' }).text, 'Backup · 42% · 8,240 / 19,622');
    assert.equal(finishToast({ ...scan, state: 'finished' }), 'Scan finished · 1 added · 1 error');
    assert.equal(finishToast({ ...scan, state: 'cancelled' }), 'Scan cancelled · 1 added');
    assert.equal(finishToast({ ...scan, state: 'failed' }), 'Scan failed · 1 added · 1 error');
    assert.equal(finishToast({ ...scan, counters: {}, state: 'finished' }), 'Scan finished · 0 added · 0 errors');
    const other = { ...scan, kind: 'backup', title: 'Backup' };
    assert.equal(finishToast({ ...other, state: 'finished' }), 'Backup finished.');
    assert.equal(finishToast({ ...other, state: 'cancelled' }), 'Backup was cancelled.');
    assert.equal(finishToast({ ...other, state: 'failed' }), 'Backup failed.');
    assert.equal(startToast(scan), 'Scan started.');
    assert.equal(startToast(other), 'Backup started.');
    assert.equal(outcomeLine(scan), '1 added · 1 error');
    assert.equal(outcomeLine(other), '');
    assert.deepEqual([-1, 0, 1, 4, 5, 6, 1e9, 0.5].map(backoffDelay), [1000, 1000, 2000, 16000, 30000, 30000, 30000, 1000]);
});

// D89 (plan D66): an image tagging job's view has its own tiles, outcomes, openable rows, log and cancel labels.
test('image-tags jobs get their own tiles outcomes and pill verb', async () => {
    const { parseJob, jobView, pillParts, outcomeLine, startToast } = await source;
    assert.equal(typeof jobView, 'function');
    const job = parseJob(summary({ kind: 'image-tags', title: 'Auto-tag images', progress: 40, processed: 2, total: 5,
        counters: { tagged: 1420, unchanged: 3, skipped: 2, errors: 1 }, scope: { mode: 'auto', images: 5, model_type: 'wd14-vit' } }));
    const view = jobView(job.kind);
    assert.deepEqual(view.tiles, [['tagged', 'Tagged', 'added'], ['unchanged', 'Unchanged', ''], ['skipped', 'Skipped', 'warn'],
        ['errors', 'Errors', 'bad']]);
    assert.deepEqual(view.outcomes, [['tagged', 'Tagged'], ['errors', 'Errors'], ['skipped', 'Skipped'], ['unchanged', 'Unchanged']]);
    assert.deepEqual([...view.openable].sort(), ['tagged', 'unchanged']);
    assert.equal(view.log, 'Tagging log');
    assert.equal(view.cancel, 'Cancel tagging');
    assert.deepEqual(pillParts(job), { label: 'Tagging · 40%', files: ' · 2 / 5', text: 'Tagging · 40% · 2 / 5' });
    assert.equal(outcomeLine(job), '1,420 tagged · 1 error');
    assert.equal(outcomeLine({ ...job, counters: {} }), '0 tagged · 0 errors');
    assert.equal(startToast(job), 'Auto-tagging started.');
    // The design's toast pattern, as the scan's: "<Job> finished · N tagged · M errors".
    const { finishToast } = await source;
    assert.equal(finishToast({ ...job, state: 'finished' }), 'Auto-tag images finished · 1,420 tagged · 1 error');
    assert.equal(finishToast({ ...job, state: 'cancelled' }), 'Auto-tag images cancelled · 1,420 tagged');
    assert.equal(finishToast({ ...job, state: 'failed' }), 'Auto-tag images failed · 1,420 tagged · 1 error');
    assert.equal(finishToast({ ...job, title: 'Add tags to images', counters: {}, state: 'finished' }),
        'Add tags to images finished · 0 tagged · 0 errors');
});

// D111 (S5): an unknown kind no longer borrows the scan's tiles (S1 review M6, S2 review M3); it has none.
test('unknown kinds get the generic view without the scan tiles', async () => {
    const { jobView } = await source;
    const scan = jobView('scan');
    assert.deepEqual(scan.tiles, [['added', 'Added to Library', 'added'], ['known', 'Already known', ''],
        ['review', 'Needs review', 'warn'], ['no_metadata', 'No usable metadata', ''], ['errors', 'Errors', 'bad']]);
    assert.deepEqual(scan.outcomes, [['added', 'Added to Library'], ['review', 'Needs review'], ['errors', 'Errors'],
        ['no_metadata', 'No usable metadata'], ['known', 'Already known']]);
    assert.deepEqual([...scan.openable].sort(), ['added', 'known']);
    assert.deepEqual([scan.log, scan.cancel], ['Scan log', 'Cancel scan']);
    for (const kind of ['restore', '', 'constructor', '__proto__']) {
        const view = jobView(kind);
        assert.deepEqual([view.tiles, view.outcomes, [...view.openable]], [[], [], []]);
        assert.deepEqual([view.log, view.cancel], ['Job log', 'Cancel job']);
    }
});

test('pagehide stops polling, closes streams and aborts the in-flight read; pageshow resumes', async () => {
    const { createActivityStore } = await source;
    const { world, api, ...deps } = harness();
    const lifecycle = new EventTarget();
    const signals = [];
    let answer = null;
    const pendingApi = { ...api, get: (url, options) => {
        signals.push(options.signal);
        world.gets += 1;
        if (world.gets === 1) return Promise.resolve({ success: true, jobs: [summary()] });
        return new Promise((resolve, reject) => {
            answer = resolve;
            options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
        });
    } };
    const store = createActivityStore({ ...deps, api: pendingApi, pollMs: 15000, lifecycle });
    await store.load();
    assert.equal(world.open().length, 1);
    const poll = world.timers.find(timer => !timer.cleared && timer.delay === 15000);
    assert.ok(poll, 'a visible tab polls');
    // A read in flight when the page goes away (a closed tab, a test teardown) is aborted, nothing is scheduled again.
    const read = store.load();
    lifecycle.dispatchEvent(new Event('pagehide'));
    await read;
    assert.equal(signals.at(-1).aborted, true);
    assert.equal(world.open().length, 0);
    assert.ok(world.timers.every(timer => timer.cleared || timer.fired));
    assert.equal(store.find(ID_A).state, 'running', 'a hidden page marks nothing lost');
    await store.load();
    assert.equal(world.gets, 2, 'a suspended store reads nothing');
    // Back from the cache, it reads the list again and follows the job.
    const resumed = Object.assign(new Event('pageshow'), { persisted: true });
    lifecycle.dispatchEvent(resumed);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(world.gets, 3);
    answer({ success: true, jobs: [summary()] });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(world.open().length, 1);
    world.dispose();
    assert.equal(signals.at(-1).aborted, true, 'disposing aborts the store reads');
    assert.equal(world.open().length, 0);
});

test('one failed read is not a restart; two in a row are, and a good read resets the backoff', async () => {
    const { createActivityStore } = await source;
    const { world, ...deps } = harness();
    world.list = [summary(), summary({ id: ID_B, state: 'finished', finished_at: 900 })];
    const store = createActivityStore(deps);
    await store.load();
    // A single failed read (a proxy's 502) keeps the job running and its stream open (review M1).
    world.listError = apiError(502, 'Bad gateway', null);
    await store.load();
    assert.equal(store.status, 'error');
    assert.equal(store.find(ID_A).state, 'running');
    assert.equal(world.open().length, 1);
    assert.equal(await world.fire(), 1000);
    // The second failure in a row: the server is gone, so the running job ended with it.
    assert.equal(store.find(ID_A).state, 'lost');
    assert.equal(world.open().length, 0);
    for (let index = 0; index < 3; index += 1) await world.fire();
    // The server is back with a new running job: the backoff starts again at 1 s (review M2).
    world.listError = null;
    world.list = [summary({ id: ID_C })];
    await world.fire();
    assert.equal(store.status, 'ready');
    assert.deepEqual(store.jobs().map(job => [job.id, job.state]), [[ID_C, 'running'], [ID_A, 'lost']]);
    world.open()[0].onerror();
    assert.equal(await world.fire(), 1000);
    // A summary carries what its detail lists, for the job view's reads (review M12).
    send(world.open()[0], summary({ id: ID_C, listed: { added: 3, errors: -1 }, log_length: 7 }));
    assert.deepEqual([store.find(ID_C).listed, store.find(ID_C).log_length], [{ added: 3, errors: 0 }, 7]);
    world.dispose();
});

test('numbers group the way the server writes them, whatever the browser locale', async () => {
    const { pillParts, finishToast, parseJob } = await source;
    const original = Number.prototype.toLocaleString;
    Number.prototype.toLocaleString = function (locale, ...rest) { return original.call(this, locale ?? 'de-DE', ...rest); };
    try {
        const job = parseJob(summary({ progress: 41, processed: 8240, total: 19622, counters: { added: 1420, errors: 18 } }));
        assert.equal(pillParts(job).text, 'Scanning · 41% · 8,240 / 19,622');
        assert.equal(finishToast({ ...job, state: 'finished' }), 'Scan finished · 1,420 added · 18 errors');
    } finally {
        Number.prototype.toLocaleString = original;
    }
});

test('a busy start names the job the server named, not the first running one (S7 review M9)', async () => {
    const { createActivityStore } = await source;
    const { world, ...deps } = harness();
    const store = createActivityStore(deps);
    const holder = 'b'.repeat(32);
    // Two read jobs run side by side; the server says the second one holds the slot.
    world.list = [summary(), summary({ id: holder, kind: 'duplicates', title: 'Find duplicate image files' })];
    world.postError = Object.assign(apiError(409, 'busy', 'busy'),
        { running: { id: holder, title: 'Find duplicate image files' } });
    const busy = await store.start('duplicates');
    assert.deepEqual([busy.busy.id, busy.busy.title], [holder, 'Find duplicate image files']);
});
