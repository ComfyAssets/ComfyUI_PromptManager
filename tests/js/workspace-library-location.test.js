// Where the library lives (#249 System S6b, D129): the Overview row's copy, the Move library review and its start,
// "Keep it here" and the Move library job's end.
const test = require('node:test');
const assert = require('node:assert/strict');

const locationModule = import('../../web/js/workspace/library-location.mjs');

const NODE_FOLDER = Object.freeze({
    path: 'custom_nodes/ComfyUI_PromptManager/prompts.db', source: 'node_folder', movable: true, keep: false, offer: true,
    target: 'user/__promptmanager', problem: null, size_bytes: 1_700_000_000,
    target_source: 'default', config_moves: true,
});

function scope() {
    const controller = new AbortController();
    return { signal: controller.signal, abort: () => controller.abort(), listen() {}, cleanup() {} };
}

function activityStore({ running = [] } = {}) {
    const listeners = new Set();
    let jobs = [];
    return {
        tracked: [], loads: 0,
        track(job) { this.tracked.push(job); jobs = [...jobs, job]; return job; },
        async load() { this.loads += 1; },
        running() { return running; },
        jobs() { return jobs; },
        set(next) { jobs = next; for (const listener of listeners) listener(); },
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    };
}

class ApiError extends Error {
    constructor(status, message, code) { super(message); this.status = status; this.code = code; }
}

test('the row says where the library is and why, the choice and any problem', async () => {
    const { locationDescription, SOURCE_TEXT } = await locationModule;
    assert.equal(locationDescription(NODE_FOLDER),
        'custom_nodes/ComfyUI_PromptManager/prompts.db · The node folder, where an update or reinstall can delete it.');
    assert.equal(locationDescription({ ...NODE_FOLDER, keep: true }),
        `${NODE_FOLDER.path} · ${SOURCE_TEXT.node_folder}. You chose to keep it here.`);
    assert.equal(locationDescription({ path: 'user/__promptmanager/prompts.db', source: 'default', movable: false, keep: true }),
        'user/__promptmanager/prompts.db · ComfyUI\'s user folder, which updates and reinstalls of the node leave alone.');
    assert.match(locationDescription({ ...NODE_FOLDER, problem: 'data-dir-empty' }), /PROMPT_MANAGER_DATA_DIR has no library yet/);
    assert.match(locationDescription({ path: 'prompts.db', source: 'environment', movable: false }), /PROMPT_MANAGER_DATA_DIR names/);
    assert.match(locationDescription({ path: 'prompts.db', source: 'config', movable: false }), /config\.json/);
});

test('the notice and the review name the actual target and what moves', async () => {
    const { bannerText, reviewGrid, locationDescription } = await locationModule;
    assert.match(bannerText(NODE_FOLDER), /Move it to ComfyUI's user folder: it is copied/);
    assert.match(bannerText({ ...NODE_FOLDER, target_source: 'environment' }),
        /Move it to the folder PROMPT_MANAGER_DATA_DIR names: it is copied/);
    assert.match(bannerText(null), /ComfyUI's user folder/);
    assert.equal(reviewGrid({ ...NODE_FOLDER, config_moves: false })[3][1],
        'The database backups (config.json stays where PROMPT_MANAGER_CONFIG_PATH names it)');
    assert.equal(reviewGrid({ ...NODE_FOLDER, config_moves: true })[3][1], 'config.json and the database backups');
    assert.match(locationDescription({ path: 'custom_nodes/pm/data/my.db', source: 'config', movable: false,
        problem: 'inside-node-folder' }), /inside the node folder, where an update or reinstall deletes it/);
});

test('only a well-formed location is shown', async () => {
    const { isLocation } = await locationModule;
    assert.equal(isLocation(NODE_FOLDER), true);
    for (const bad of [null, undefined, 'x', {}, { ...NODE_FOLDER, source: 'elsewhere' }, { ...NODE_FOLDER, path: 3 },
        { ...NODE_FOLDER, movable: 'yes' }]) assert.equal(isLocation(bad), false);
});

test('sizes read as the server would write them', async () => {
    const { sizeText } = await locationModule;
    assert.equal(sizeText(1_700_000_000), '1.6 GB');
    assert.equal(sizeText(120 * 2 ** 20), '120.0 MB');
    assert.equal(sizeText(1500), '2 KB');
    assert.equal(sizeText(-1), 'Unknown');
    assert.equal(sizeText(undefined), 'Unknown');
});

test('the review names both folders, the size and what happens to the old files', async () => {
    const { reviewGrid } = await locationModule;
    assert.deepEqual(reviewGrid(NODE_FOLDER), [
        ['From', 'custom_nodes/ComfyUI_PromptManager/prompts.db'],
        ['To', 'user/__promptmanager/prompts.db'],
        ['Size', '1.6 GB'],
        ['Moves with it', 'config.json and the database backups'],
        ['Old files', 'Renamed *.moved-to-…, never deleted'],
    ]);
});

test('Move library starts the job, tracks it and opens its view', async () => {
    const { startLibraryMove, MOVE_URL } = await locationModule;
    const posts = [];
    const api = { post: async (url, body) => { posts.push([url, body]); return { job: { id: 'j1', kind: 'library_move' } }; } };
    const activity = activityStore();
    const routes = [];
    const job = await startLibraryMove({ api, activity, scope: scope(), notify: () => assert.fail('no toast'),
        navigate: hash => routes.push(hash) });
    assert.equal(job.id, 'j1');
    assert.deepEqual(posts, [[MOVE_URL, {}]]);
    assert.deepEqual(activity.tracked.map(item => item.id), ['j1']);
    assert.deepEqual(routes, ['#/activity/j1']);
});

test('a busy server names the running job; another refusal says the server reason', async () => {
    const { startLibraryMove, START_FAILED } = await locationModule;
    const running = { id: 'scan1', title: 'Metadata scan' };
    const activity = activityStore({ running: [running] });
    const toasts = [];
    const busy = { post: async () => { throw new ApiError(409, 'busy', 'busy'); } };
    assert.equal(await startLibraryMove({ api: busy, activity, scope: scope(), notify: t => toasts.push(t),
        navigate: () => assert.fail('nothing opens') }), null);
    assert.equal(activity.loads, 1);
    assert.deepEqual(toasts.at(-1), ['Another job is running: ', { text: 'Metadata scan', href: '#/activity/scan1' }, '.']);
    const short = { post: async () => { throw new ApiError(507, 'There is not enough free space for the copy.', 'insufficient-space'); } };
    await startLibraryMove({ api: short, activity, scope: scope(), notify: t => toasts.push(t), navigate: () => {} });
    assert.equal(toasts.at(-1), 'There is not enough free space for the copy.');
    const broken = { post: async () => { throw new TypeError('network'); } };
    await startLibraryMove({ api: broken, activity, scope: scope(), notify: t => toasts.push(t), navigate: () => {} });
    assert.equal(toasts.at(-1), START_FAILED);
    const gone = scope();
    gone.abort();
    const before = toasts.length;
    await startLibraryMove({ api: broken, activity, scope: gone, notify: t => toasts.push(t), navigate: () => {} });
    assert.equal(toasts.length, before);
});

test('Keep it here posts the choice and resolves the new location, or null with a toast', async () => {
    const { keepLibrary, KEEP_URL, KEEP_FAILED } = await locationModule;
    const posts = [];
    const kept = { ...NODE_FOLDER, keep: true, offer: false };
    const api = { post: async (url, body) => { posts.push([url, body]); return { location: kept }; } };
    assert.deepEqual(await keepLibrary({ api, scope: scope() }), kept);
    assert.deepEqual(posts, [[KEEP_URL, { keep: true }]]);
    const toasts = [];
    const failing = { post: async () => { throw new ApiError(500, 'no', 'config-write-failed'); } };
    assert.equal(await keepLibrary({ api: failing, scope: scope(), notify: t => toasts.push(t) }), null);
    assert.deepEqual(toasts, [KEEP_FAILED]);
    const odd = { post: async () => ({ location: { path: 1 } }) };
    assert.equal(await keepLibrary({ api: odd, scope: scope() }), null);
});

test('the end of a Move library job is reported once; other jobs are ignored', async () => {
    const { onMoveEnded } = await locationModule;
    const activity = activityStore();
    let ended = 0;
    const unsubscribe = onMoveEnded(activity, () => { ended += 1; });
    activity.set([{ id: 'm', kind: 'library_move', state: 'running' }, { id: 's', kind: 'scan', state: 'running' }]);
    assert.equal(ended, 0);
    activity.set([{ id: 'm', kind: 'library_move', state: 'running' }, { id: 's', kind: 'scan', state: 'finished' }]);
    assert.equal(ended, 0);
    activity.set([{ id: 'm', kind: 'library_move', state: 'failed' }]);
    assert.equal(ended, 1);
    activity.set([{ id: 'm', kind: 'library_move', state: 'failed' }]);
    assert.equal(ended, 1);
    unsubscribe();
    activity.set([{ id: 'n', kind: 'library_move', state: 'running' }]);
    activity.set([{ id: 'n', kind: 'library_move', state: 'finished' }]);
    assert.equal(ended, 1);
});
