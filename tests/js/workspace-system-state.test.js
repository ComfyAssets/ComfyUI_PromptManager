// System draft store (#249 System S2 Task 5): one Save row for browser, host and runtime fields (concept app.js
// renderSystem `.save-row` 705, `save-settings` 1320). It saves browser, then host with the GET revision, then runtime;
// a domain that fails keeps its fields dirty, and the status copy (D92) says what really happened.
const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../../web/js/workspace/system-state.mjs');

/** Adapters that record every call; `host` answers like POST /prompt_manager/settings. */
function adapters({ browser = {}, host = {}, runtime = {}, revision = 'sha256:one' } = {}) {
    const calls = [];
    const outcome = { browser: null, host: null, runtime: null };
    return {
        calls,
        outcome,
        readBrowser: () => { calls.push(['readBrowser']); return { ...browser }; },
        readHost: async () => { calls.push(['readHost']); return { values: { ...host }, revision }; },
        readRuntime: async () => { calls.push(['readRuntime']); return { ...runtime }; },
        writeBrowser: async changes => {
            calls.push(['writeBrowser', changes]);
            if (outcome.browser) return outcome.browser(changes);
            return { persisted: true };
        },
        writeHost: async (changes, options) => {
            calls.push(['writeHost', changes, options]);
            if (outcome.host) return outcome.host(changes, options);
            return { success: true, persisted: true, revision: 'sha256:two', runtime_applied: true };
        },
        writeRuntime: async changes => {
            calls.push(['writeRuntime', changes]);
            if (outcome.runtime) return outcome.runtime(changes);
            return { success: true };
        },
    };
}

async function loaded(domains) {
    const { createSystemDrafts } = await source;
    const drafts = createSystemDrafts(domains);
    await Promise.all(['browser', 'host', 'runtime'].map(domain => drafts.load(domain)));
    domains.calls.length = 0;
    return drafts;
}

test('sections follow the concept order and the production hashes', async () => {
    const { SYSTEM_SECTIONS, isSystemSection } = await source;
    assert.deepEqual(SYSTEM_SECTIONS.map(([id, label]) => [id, label]), [
        ['overview', 'Overview'], ['appearance', 'Appearance'], ['sources', 'Sources & scans'],
        ['integrations', 'Integrations'], ['tagging', 'Auto-tagging'], ['maintenance', 'Maintenance'],
        ['backups', 'Backup & export'], ['diagnostics', 'Diagnostics & logs'],
    ]);
    assert.equal(isSystemSection('backups'), true);
    for (const value of ['general', '', 'constructor', '__proto__', undefined]) assert.equal(isSystemSection(value), false, value);
});

test('drafts read baselines and report dirty only for changed values', async () => {
    const domains = adapters({ browser: { layout: 'grid' }, host: { result_timeout: 30 } });
    const drafts = await loaded(domains);
    assert.equal(drafts.get('browser', 'layout'), 'grid');
    assert.equal(drafts.get('host', 'result_timeout'), 30);
    assert.equal(drafts.dirty(), false);
    drafts.set('host', 'result_timeout', 45);
    assert.equal(drafts.get('host', 'result_timeout'), 45);
    assert.equal(drafts.dirty(), true);
    assert.equal(drafts.dirty('host'), true);
    assert.equal(drafts.dirty('browser'), false);
    // Setting the saved value back is clean again.
    drafts.set('host', 'result_timeout', 30);
    assert.equal(drafts.dirty(), false);
    assert.throws(() => drafts.set('elsewhere', 'x', 1), TypeError);
    assert.throws(() => drafts.get('elsewhere', 'x'), TypeError);
});

test('save commits browser then host then runtime', async () => {
    const domains = adapters({ browser: { layout: 'grid' }, host: { result_timeout: 30 }, runtime: { level: 'INFO' } });
    const drafts = await loaded(domains);
    drafts.set('runtime', 'level', 'DEBUG');
    drafts.set('host', 'result_timeout', 45);
    drafts.set('browser', 'layout', 'list');
    const outcome = await drafts.save();
    assert.deepEqual(outcome, { browser: 'saved', host: 'saved', runtime: 'applied' });
    assert.deepEqual(domains.calls, [
        ['writeBrowser', { layout: 'list' }],
        ['writeHost', { result_timeout: 45 }, { expected_revision: 'sha256:one' }],
        ['writeRuntime', { level: 'DEBUG' }],
    ]);
    assert.equal(drafts.dirty(), false);
    // The next host save sends the revision the last one returned.
    drafts.set('host', 'result_timeout', 60);
    await drafts.save();
    assert.deepEqual(domains.calls.at(-1), ['writeHost', { result_timeout: 60 }, { expected_revision: 'sha256:two' }]);
});

test('host failure keeps host fields dirty and browser saved', async () => {
    const domains = adapters({ browser: { layout: 'grid' }, host: { result_timeout: 30 } });
    const drafts = await loaded(domains);
    domains.outcome.host = () => { throw Object.assign(new Error('Settings could not be written to config.json.'), { code: 'config-write-failed' }); };
    drafts.set('browser', 'layout', 'list');
    drafts.set('host', 'result_timeout', 45);
    const outcome = await drafts.save();
    assert.deepEqual(outcome, { browser: 'saved', host: 'failed', runtime: 'clean' });
    assert.equal(drafts.dirty('browser'), false);
    assert.equal(drafts.dirty('host'), true);
    assert.equal(drafts.get('host', 'result_timeout'), 45);
    assert.deepEqual([...drafts.failed('host')], ['result_timeout']);
    assert.deepEqual([...drafts.failed('browser')], []);
    assert.equal(drafts.statusText(), 'Some changes were not saved. Review the highlighted settings.');
    // A save answered `persisted: false` without an error is not saved either.
    domains.outcome.host = () => ({ success: false, persisted: false });
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'failed', runtime: 'clean' });
    // Editing the failed field clears its highlight and reads as unsaved again.
    drafts.set('host', 'result_timeout', 50);
    assert.deepEqual([...drafts.failed('host')], []);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
});

test('stale revision reports conflict and keeps the draft', async () => {
    const domains = adapters({ host: { result_timeout: 30 } });
    const drafts = await loaded(domains);
    domains.outcome.host = () => { throw Object.assign(new Error('Settings changed since they were loaded.'), { code: 'settings-changed' }); };
    drafts.set('host', 'result_timeout', 45);
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'conflict', runtime: 'clean' });
    assert.equal(drafts.dirty('host'), true);
    assert.equal(drafts.get('host', 'result_timeout'), 45);
    assert.equal(drafts.statusText(), 'Settings changed elsewhere. Reload this section to see them.');
    // The conflict read the host values again (their new revision), keeping the draft.
    assert.deepEqual(domains.calls.slice(-2).map(([name]) => name), ['writeHost', 'readHost']);
    // Reloading the section reads the new revision and keeps the draft (Q10).
    domains.outcome.host = null;
    await drafts.load('host');
    assert.equal(drafts.get('host', 'result_timeout'), 45);
    await drafts.save();
    assert.deepEqual(domains.calls.at(-1)[2], { expected_revision: 'sha256:one' });
});

test('host save without a read revision never posts', async () => {
    const { createSystemDrafts } = await source;
    // Never read: an edit made before the host values (and their revision) arrived must not merge blindly.
    const unread = adapters({ host: { result_timeout: 30 } });
    const drafts = createSystemDrafts(unread);
    drafts.set('host', 'result_timeout', 45);
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'failed', runtime: 'clean' });
    assert.deepEqual(unread.calls, []);
    assert.equal(drafts.dirty('host'), true);
    assert.deepEqual([...drafts.failed('host')], ['result_timeout']);
    // A read of an unreadable configuration has no revision either.
    const unreadable = adapters({ host: { result_timeout: 30 }, revision: null });
    const blind = createSystemDrafts(unreadable);
    await blind.load('host');
    blind.set('host', 'result_timeout', 45);
    assert.equal((await blind.save()).host, 'failed');
    assert.equal(unreadable.calls.filter(([name]) => name === 'writeHost').length, 0);
    // Once read, the same draft saves with the revision.
    await drafts.load('host');
    assert.equal((await drafts.save()).host, 'saved');
    assert.deepEqual(unread.calls.at(-1), ['writeHost', { result_timeout: 45 }, { expected_revision: 'sha256:one' }]);
});

test('reloadHost reads the host values and says whether it could', async () => {
    const { createSystemDrafts } = await source;
    const domains = adapters({ host: { result_timeout: 30 } });
    const drafts = createSystemDrafts(domains);
    assert.equal(await drafts.reloadHost(), true);
    assert.equal(drafts.get('host', 'result_timeout'), 30);
    const broken = createSystemDrafts({ ...domains, readHost: async () => { throw new Error('offline'); } });
    assert.equal(await broken.reloadHost(), false);
    broken.set('host', 'result_timeout', 45);
    assert.equal((await broken.save()).host, 'failed');
});

test('no domain called when clean', async () => {
    const domains = adapters({ browser: { layout: 'grid' } });
    const drafts = await loaded(domains);
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'clean', runtime: 'clean' });
    assert.deepEqual(domains.calls, []);
    drafts.set('browser', 'layout', 'list');
    drafts.discard();
    assert.equal(drafts.dirty(), false);
    assert.equal(drafts.get('browser', 'layout'), 'grid');
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'clean', runtime: 'clean' });
    assert.deepEqual(domains.calls, []);
});

test('status text per outcome', async () => {
    const { statusText, SETTINGS_STATUS } = await source;
    assert.deepEqual(SETTINGS_STATUS, {
        clean: 'No unsaved changes.',
        dirty: 'Unsaved changes.',
        browser: 'Saved in this browser.',
        host: 'Saved to the server configuration.',
        runtime: 'Applied until the server restarts.',
        partial: 'Some changes were not saved. Review the highlighted settings.',
        tabOnly: 'Saved for this tab only; browser storage is unavailable.',
        conflict: 'Settings changed elsewhere. Reload this section to see them.',
    });
    const clean = { browser: 'clean', host: 'clean', runtime: 'clean' };
    assert.equal(statusText(clean), 'No unsaved changes.');
    assert.equal(statusText({ ...clean, browser: 'saved' }), 'Saved in this browser.');
    assert.equal(statusText({ ...clean, host: 'saved' }), 'Saved to the server configuration.');
    assert.equal(statusText({ ...clean, runtime: 'applied' }), 'Applied until the server restarts.');
    assert.equal(statusText({ ...clean, browser: 'tab-only' }), 'Saved for this tab only; browser storage is unavailable.');
    assert.equal(statusText({ browser: 'saved', host: 'saved', runtime: 'applied' }),
        'Saved in this browser. Saved to the server configuration. Applied until the server restarts.');
    assert.equal(statusText({ ...clean, browser: 'saved', runtime: 'failed' }),
        'Some changes were not saved. Review the highlighted settings.');
    assert.equal(statusText({ browser: 'failed', host: 'conflict', runtime: 'clean' }),
        'Settings changed elsewhere. Reload this section to see them.');
});

test('the store says unsaved, then the save outcome, and notifies subscribers', async () => {
    const domains = adapters({ browser: { layout: 'grid' } });
    const drafts = await loaded(domains);
    const seen = [];
    const unsubscribe = drafts.subscribe(() => seen.push(drafts.statusText()));
    assert.equal(drafts.statusText(), 'No unsaved changes.');
    drafts.set('browser', 'layout', 'list');
    domains.outcome.browser = () => ({ persisted: false });
    await drafts.save();
    assert.deepEqual(seen, ['Unsaved changes.', 'Saved for this tab only; browser storage is unavailable.']);
    unsubscribe();
    drafts.set('browser', 'layout', 'masonry');
    assert.equal(seen.length, 2);
    // The tab-only value is this tab's baseline now.
    drafts.set('browser', 'layout', 'list');
    assert.equal(drafts.dirty(), false);
});

test('an edit made while a save runs stays dirty', async () => {
    const domains = adapters({ browser: { layout: 'grid' } });
    const drafts = await loaded(domains);
    let release;
    domains.outcome.browser = () => new Promise(resolve => { release = () => resolve({ persisted: true }); });
    drafts.set('browser', 'layout', 'list');
    const saving = drafts.save();
    // A second Save while one runs joins it instead of writing twice.
    assert.equal(drafts.save(), saving);
    assert.equal(drafts.saving(), true);
    await new Promise(resolve => setImmediate(resolve));
    drafts.set('browser', 'layout', 'masonry');
    release();
    assert.deepEqual(await saving, { browser: 'saved', host: 'clean', runtime: 'clean' });
    assert.equal(drafts.saving(), false);
    assert.equal(drafts.dirty('browser'), true);
    assert.equal(drafts.get('browser', 'layout'), 'masonry');
    assert.equal(domains.calls.filter(([name]) => name === 'writeBrowser').length, 1);
});

test('a failed read keeps the store usable and drafts survive a reload (Q10)', async () => {
    const { createSystemDrafts } = await source;
    const domains = adapters({ browser: { layout: 'grid' } });
    const drafts = createSystemDrafts(domains);
    await drafts.load('browser');
    drafts.set('browser', 'layout', 'list');
    await drafts.load('browser');
    assert.equal(drafts.get('browser', 'layout'), 'list');
    assert.equal(drafts.dirty(), true);
    const failing = createSystemDrafts({ ...domains, readHost: async () => { throw new Error('offline'); } });
    await assert.rejects(failing.load('host'), /offline/);
    assert.equal(failing.dirty(), false);
    assert.throws(() => createSystemDrafts({ ...domains, writeHost: null }), TypeError);
});
