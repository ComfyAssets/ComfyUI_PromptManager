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
        partialSaved: 'Some changes were saved to the server configuration; the highlighted ones were not.',
        tabOnly: 'Saved for this tab only; browser storage is unavailable.',
        conflict: 'Settings changed elsewhere. Reload this section to see them.',
        restart: 'Restart ComfyUI to apply:',
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

test('a rejected value drops its draft and is highlighted; a conflict elsewhere keeps the drafts', async () => {
    const domains = adapters({ browser: { videoMute: true, videoLoop: true }, host: { result_timeout: 5 } });
    const drafts = await loaded(domains);
    assert.deepEqual(drafts.saved('browser'), { videoMute: true, videoLoop: true });
    drafts.set('host', 'result_timeout', 12);
    assert.equal(drafts.dirty('host'), true);
    drafts.reject('host', 'result_timeout');
    assert.equal(drafts.dirty('host'), false);
    assert.deepEqual([...drafts.failed('host')], ['result_timeout']);
    assert.equal(drafts.get('host', 'result_timeout'), 5);
    // A valid value clears the highlight.
    drafts.set('host', 'result_timeout', 7);
    assert.deepEqual([...drafts.failed('host')], []);
    drafts.discard();
    drafts.set('browser', 'videoMute', false);
    drafts.conflict('browser', []);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
    drafts.conflict('browser', ['videoMute']);
    assert.equal(drafts.statusText(), 'Settings changed elsewhere. Reload this section to see them.');
    assert.deepEqual([...drafts.failed('browser')], ['videoMute']);
    assert.equal(drafts.get('browser', 'videoMute'), false);
    assert.throws(() => drafts.reject('nowhere', 'x'), TypeError);
});

test('a rejected value says why in the status until the next edit', async () => {
    const drafts = await loaded(adapters({ host: { result_timeout: 5 } }));
    drafts.reject('host', 'result_timeout');
    assert.equal(drafts.statusText(), 'Some changes were not saved. Review the highlighted settings.');
    drafts.set('host', 'result_timeout', 9);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
});

test('a browser write refused as changed elsewhere keeps the draft and reads the browser values again', async () => {
    const domains = adapters({ browser: { workspaceDensity: 'comfortable' } });
    const drafts = await loaded(domains);
    drafts.set('browser', 'workspaceDensity', 'compact');
    domains.outcome.browser = () => { throw Object.assign(new Error('changed'), { code: 'settings-changed' }); };
    assert.deepEqual(await drafts.save(), { browser: 'conflict', host: 'clean', runtime: 'clean' });
    assert.deepEqual(domains.calls.map(([name]) => name), ['writeBrowser', 'readBrowser']);
    assert.equal(drafts.get('browser', 'workspaceDensity'), 'compact');
    assert.deepEqual([...drafts.failed('browser')], ['workspaceDensity']);
    assert.equal(drafts.statusText(), 'Settings changed elsewhere. Reload this section to see them.');
});

test('host read state says whether the server settings could be read', async () => {
    const { createSystemDrafts } = await source;
    const domains = adapters({ host: { result_timeout: 5 } });
    let fail = true;
    const drafts = createSystemDrafts({ ...domains, readHost: async () => { if (fail) throw new Error('offline'); return { values: {}, revision: 'r' }; } });
    assert.equal(drafts.hostState(), 'pending');
    assert.equal(await drafts.reloadHost(), false);
    assert.equal(drafts.hostState(), 'failed');
    fail = false;
    assert.equal(await drafts.reloadHost(), true);
    assert.equal(drafts.hostState(), 'loaded');
});

test('a host save names the settings that apply after a restart (D128)', async () => {
    const { statusText, RESTART_LABELS } = await source;
    assert.deepEqual(RESTART_LABELS, { monitoring_enabled: 'Watch for new outputs' });
    const saved = { browser: 'clean', host: 'saved', runtime: 'clean' };
    assert.equal(statusText({ ...saved, restart: ['monitoring_enabled'] }),
        'Saved to the server configuration. Restart ComfyUI to apply: Watch for new outputs.');
    // A field without a row label (the sources say "restart pending" per row) adds nothing.
    assert.equal(statusText({ ...saved, restart: ['gallery_root_paths'] }), 'Saved to the server configuration.');
    assert.equal(statusText({ ...saved, restart: [] }), 'Saved to the server configuration.');

    const domains = adapters({ host: { monitoring_enabled: true } });
    const drafts = await loaded(domains);
    domains.outcome.host = () => ({ success: true, persisted: true, revision: 'sha256:two',
        restart_required_fields: ['monitoring_enabled', 7] });
    drafts.set('host', 'monitoring_enabled', false);
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'saved', runtime: 'clean', restart: ['monitoring_enabled'] });
    assert.equal(drafts.statusText(), 'Saved to the server configuration. Restart ComfyUI to apply: Watch for new outputs.');
    // The next edit is unsaved again.
    drafts.set('host', 'monitoring_enabled', true);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
});

test('a host save refused for one setting says the server reason and names no source (S11 CUJ 2a)', async () => {
    const { INVALID_SETTING_CODE } = await source;
    const domains = adapters({ host: { autotag_general_threshold: 0.35 } });
    const drafts = await loaded(domains);
    const message = 'General-tag confidence must be between 0 and 1.';
    domains.outcome.host = () => { throw Object.assign(new Error(message), { code: INVALID_SETTING_CODE }); };
    drafts.set('host', 'autotag_general_threshold', 1.5);
    assert.deepEqual(await drafts.save(), { browser: 'clean', host: 'failed', runtime: 'clean', invalid: { source: null, message } });
    assert.equal(drafts.statusText(), message);
    assert.equal(drafts.invalidSource(), null);
    assert.deepEqual([...drafts.failed('host')], ['autotag_general_threshold']);
    assert.equal(drafts.get('host', 'autotag_general_threshold'), 1.5);
    drafts.set('host', 'autotag_general_threshold', 0.4);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
});

test('a host save refused for one source says the server reason and names it until the next edit', async () => {
    const domains = adapters({ host: { gallery_root_paths: ['output', 'output/gone'] } });
    const drafts = await loaded(domains);
    const message = "output/gone can't be used: Gallery root does not exist. Remove it or reconnect it, then save.";
    domains.outcome.host = () => { throw Object.assign(new Error(message), { code: 'invalid-source', source: 'output/gone' }); };
    drafts.set('host', 'gallery_root_paths', ['output/gone']);
    const outcome = await drafts.save();
    assert.deepEqual(outcome, { browser: 'clean', host: 'failed', runtime: 'clean',
        invalid: { source: 'output/gone', message } });
    assert.equal(drafts.statusText(), message);
    assert.equal(drafts.invalidSource(), 'output/gone');
    assert.deepEqual([...drafts.failed('host')], ['gallery_root_paths']);
    // Any other failure keeps the generic copy and names no source.
    domains.outcome.host = () => { throw Object.assign(new Error('busy'), { code: 'busy' }); };
    await drafts.save();
    assert.equal(drafts.statusText(), 'Some changes were not saved. Review the highlighted settings.');
    assert.equal(drafts.invalidSource(), null);
    domains.outcome.host = () => { throw Object.assign(new Error(message), { code: 'invalid-source' }); };
    await drafts.save();
    assert.equal(drafts.invalidSource(), null);
    drafts.set('host', 'gallery_root_paths', ['output']);
    assert.equal(drafts.invalidSource(), null);
    assert.equal(drafts.statusText(), 'Unsaved changes.');
});

test('a partial host save keeps what saved, adopts its revision and fails only the rest (S10 review I3)', async () => {
    const { SETTINGS_STATUS } = await source;
    const domains = adapters({ host: { lora_trigger_words_enabled: false, gallery_root_paths: ['a'] } });
    const drafts = await loaded(domains);
    // The LoRA part is written (config.json moves to sha256:lora), then /settings refuses one source.
    domains.outcome.host = () => {
        throw Object.assign(new Error('missing can\'t be used: not found. Remove it or reconnect it, then save.'), {
            code: 'invalid-source', source: 'missing', partial: { saved: ['lora_trigger_words_enabled'], revision: 'sha256:lora' } });
    };
    drafts.set('host', 'lora_trigger_words_enabled', true);
    drafts.set('host', 'gallery_root_paths', ['a', 'missing']);
    const first = await drafts.save();
    assert.equal(first.host, 'partial');
    assert.deepEqual([...drafts.failed('host')], ['gallery_root_paths']);
    assert.equal(drafts.saved('host').lora_trigger_words_enabled, true);
    assert.deepEqual(drafts.changes('host'), { gallery_root_paths: ['a', 'missing'] });
    assert.equal(drafts.invalidSource(), 'missing');
    assert.equal(drafts.statusText(), 'missing can\'t be used: not found. Remove it or reconnect it, then save.');
    // The next save carries the revision the saved part produced: no false "changed elsewhere".
    domains.outcome.host = null;
    drafts.set('host', 'gallery_root_paths', ['a']);
    const second = await drafts.save();
    assert.equal(second.host, 'saved');
    assert.equal(domains.calls.at(-1)[2].expected_revision, 'sha256:lora');
    assert.deepEqual(domains.calls.at(-1)[1], { gallery_root_paths: ['a'] });
    // Without a server reason, the status says some were saved and the highlighted ones were not.
    domains.outcome.host = () => { throw Object.assign(new Error('boom'), { partial: { saved: ['x'], revision: 'sha256:three' } }); };
    drafts.set('host', 'x', 1);
    drafts.set('host', 'y', 2);
    assert.equal((await drafts.save()).host, 'partial');
    assert.equal(drafts.statusText(), SETTINGS_STATUS.partialSaved);
    assert.deepEqual([...drafts.failed('host')], ['y']);
    // A partial result that saved nothing is a plain failure.
    domains.outcome.host = () => { throw Object.assign(new Error('boom'), { partial: { saved: [] } }); };
    drafts.set('host', 'z', 3);
    assert.equal((await drafts.save()).host, 'failed');
    assert.equal(drafts.statusText(), SETTINGS_STATUS.partial);
});

// S12 (#329): System's runtime domain is the running server's log level and console setting (`/logs/config`).
test('runtime adapter maps config.level to runtime_level and back', async () => {
    const { runtimeValues, runtimeBody } = await import('../../web/js/workspace/system-diagnostics.mjs');
    assert.deepEqual(runtimeValues({ level: 'WARNING', console_logging: false, backup_count: 5 }),
        { runtime_level: 'WARNING', console_logging: false });
    assert.deepEqual(runtimeBody({ runtime_level: 'WARNING' }), { level: 'WARNING' });
    assert.deepEqual(runtimeBody({ runtime_level: 'CRITICAL', console_logging: true }), { level: 'CRITICAL', console_logging: true });
});
