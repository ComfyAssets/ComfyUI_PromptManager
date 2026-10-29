import test from 'node:test';
import assert from 'node:assert/strict';

// #480 (D159): Images › Delete…: the review's rows, sentences, labels and outcome toasts.
const loaded = await import('../../web/js/workspace/images-delete.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'images-delete.mjs is not implemented'); return loaded; };
const pageScope = () => {
    const controller = new AbortController();
    return { signal: controller.signal, cleanup: () => {}, abort: () => controller.abort() };
};
const plan = (over = {}) => ({
    token: 't', library: { count: 3 }, trash: { count: 2 },
    refused: { count: 1, counts: { 'not-listed': 1 }, rows: [{ path: 'sub/gone.png', code: 'not-listed', detail: 'No longer listed in Images.' }] },
    trash_refused: { count: 1, counts: { 'no-trash': 1 }, rows: [{ path: 'b.png', code: 'no-trash', detail: 'This drive has no trash.' }] },
    records: { prompt_links: 2, pair_links: 0, image_tags: 1, auto_tagged: 0, categories: 1, collections: 2, favorites: 0, review_items: 0 },
    orphaned_prompts: { count: 1, titles: ['Red fox'] },
    backup: { policy: 'on' }, disk: null, ...over,
});

test('the words of the review and its routes', () => {
    const { PREVIEW_URL, MAX_DELETE, TOO_MANY, QUESTION, MODES } = module();
    assert.equal(PREVIEW_URL, '/prompt_manager/images/delete/preview');
    assert.equal(MAX_DELETE, 500);
    assert.equal(TOO_MANY, 'Select at most 500 images.');
    assert.equal(QUESTION, 'What should happen to the files?');
    assert.deepEqual(MODES.map(mode => mode.label), ['Remove from the library only', 'Also move the files to the trash']);
});

test('library rows: scope, refusals, links, details, backup and media', () => {
    const { deleteRows } = module();
    const rows = Object.fromEntries(deleteRows(plan(), 'library'));
    assert.equal(rows.Scope, '3 images');
    assert.equal(rows['Not changed'], '1: gone.png. No longer listed in Images.');
    assert.equal(rows['Prompt links'], '2 links. 1 prompt loses its last image: Red fox. The prompt stays on the Library page.');
    assert.equal(rows['Image details'], '1 with image tags, 1 category, 2 collection memberships');
    assert.equal(rows.Backup, 'Create and verify before changes (the database, not the files)');
    assert.equal(rows['Media on disk'], 'Files stay on disk, hidden from Images.');
});

test('trash rows add the trash refusals and the trash media line', () => {
    const { deleteRows } = module();
    const rows = Object.fromEntries(deleteRows(plan({ backup: { policy: 'off' } }), 'trash'));
    assert.equal(rows.Scope, '2 images');
    assert.match(rows['Not changed'], /^2: gone\.png, b\.png\. No longer listed in Images\. This drive has no trash\.$/);
    assert.equal(rows.Backup, 'Off: no safety backup (System › Backup & export › Recovery policy)');
    assert.match(rows['Media on disk'], /Nothing is deleted permanently/);
});

test('rows leave out what is zero', () => {
    const { deleteRows } = module();
    const bare = plan({ refused: { count: 0, rows: [] }, trash_refused: { count: 0, rows: [] },
        records: { prompt_links: 0 }, orphaned_prompts: { count: 0, titles: [] } });
    assert.deepEqual(deleteRows(bare, 'library').map(([term]) => term), ['Scope', 'Backup', 'Media on disk']);
    const many = plan({ orphaned_prompts: { count: 5, titles: ['A', 'B', 'C'] } });
    assert.match(Object.fromEntries(deleteRows(many, 'library'))['Prompt links'],
        /5 prompts lose their last image: A, B, C and 2 more\. The prompts stay on the Library page\./);
    assert.equal(module().refusedValue({ count: 5, rows: [{ path: 'a.png' }] }), '5: a.png and 4 more');
    assert.equal(module().refusedValue({ count: 0 }), null);
});

test('sentences follow the mode and the backup policy', () => {
    const { deleteSentences, SENTENCES, USE_LIBRARY_ONLY, NO_TRASH_HERE } = module();
    assert.deepEqual(deleteSentences(plan(), 'library'), [SENTENCES.libraryBackup]);
    assert.deepEqual(deleteSentences(plan({ backup: { policy: 'off' } }), 'library'), [SENTENCES.libraryNoBackup]);
    assert.deepEqual(deleteSentences(plan(), 'trash'), [SENTENCES.trash, USE_LIBRARY_ONLY]);
    const nowhere = plan({ trash: { count: 0 }, trash_refused: { count: 3, rows: [] } });
    assert.deepEqual(deleteSentences(nowhere, 'trash'), [SENTENCES.trash, NO_TRASH_HERE, USE_LIBRARY_ONLY]);
    assert.deepEqual(deleteSentences(plan({ trash_refused: { count: 0 } }), 'trash'), [SENTENCES.trash]);
});

test('primary labels and the close-only reasons', () => {
    const { primaryLabel, closeOnlyReason, NONE_DELETABLE } = module();
    assert.equal(primaryLabel('library', 1), 'Remove 1 image from the library');
    assert.equal(primaryLabel('trash', 4), 'Move 4 images to the trash');
    assert.equal(closeOnlyReason(plan()), null);
    assert.equal(closeOnlyReason(plan({ library: { count: 0 }, trash: { count: 0 } })), NONE_DELETABLE);
    assert.equal(closeOnlyReason(plan({ disk: { sufficient: false, required_bytes: 2048, free_bytes: 1024 } })),
        'There is not enough free space for the safety backup (needs 2,048 bytes, has 1,024).');
});

test('outcome toasts per mode and failure', () => {
    const { outcomeToast } = module();
    assert.equal(outcomeToast({ scope: { mode: 'library' }, counters: { removed: 3, not_changed: 0 }, total: 3 }),
        'Removed 3 images from the library.');
    assert.equal(outcomeToast({ scope: { mode: 'trash' }, counters: { trashed: 2, not_changed: 1 }, total: 3 }),
        'Moved 2 images to the trash. 1 was not changed (see Activity).');
    assert.equal(outcomeToast({ scope: { mode: 'trash' }, counters: { trashed: 0, not_changed: 2 }, total: 2 }),
        'Moved 0 images to the trash. 2 were not changed (see Activity).');
    assert.equal(outcomeToast({ scope: { mode: 'library', failure: 'records-failed' }, counters: { removed: 1 }, total: 4 }),
        'Stopped after 1 of 4 images; the rest were not changed. See Activity.');
    assert.equal(outcomeToast({ scope: { failure: 'backup-failed' } }), 'The safety backup failed; nothing was changed.');
    assert.equal(outcomeToast({ scope: { failure: 'sources-changed' } }), 'The sources changed; nothing was changed.');
});

test('review failures name the cause', () => {
    const { reviewFailure, SOURCE_CHANGED, STORAGE_CHANGED, TOO_MANY, REVIEW_FAILED } = module();
    assert.equal(reviewFailure({ code: 'source-changed' }), SOURCE_CHANGED);
    assert.equal(reviewFailure({ code: 'storage-changed' }), STORAGE_CHANGED);
    assert.equal(reviewFailure({ code: 'too-many-images' }), TOO_MANY);
    assert.equal(reviewFailure({ code: 'busy', message: 'Scan running' }), 'Scan running');
    assert.equal(reviewFailure(new Error('boom')), REVIEW_FAILED);
});

test('over 500 refuses with the toast before any request', async () => {
    const { openDeleteImages, TOO_MANY } = module();
    const toasts = [];
    const api = { post: () => assert.fail('no request') };
    await openDeleteImages({ api, scope: pageScope(), images: [], filter: {}, count: 501, notify: text => toasts.push(text) });
    assert.deepEqual(toasts, [TOO_MANY]);
    await openDeleteImages({ api, scope: pageScope(), images: [], notify: text => toasts.push(text) });
    assert.deepEqual(toasts, [TOO_MANY]);
});

test('a failed review toasts and releases its trigger', async () => {
    const { openDeleteImages, SOURCE_CHANGED } = module();
    const toasts = [], sent = [];
    const attributes = new Map();
    const trigger = { textContent: 'Delete…', getAttribute: name => attributes.get(name) ?? null,
        setAttribute: (name, value) => attributes.set(name, value), removeAttribute: name => attributes.delete(name) };
    const api = { post: async (url, body) => { sent.push([url, body]); throw Object.assign(new Error('x'), { code: 'source-changed' }); } };
    await openDeleteImages({ api, scope: pageScope(), trigger, notify: text => toasts.push(text),
        images: [{ root_index: 0, root_scope: 'a'.repeat(64), path: 'a.png', filename: 'a.png' }] });
    assert.deepEqual(sent, [['/prompt_manager/images/delete/preview', { items: [{ root_index: 0, root_scope: 'a'.repeat(64), path: 'a.png' }] }]]);
    assert.deepEqual(toasts, [SOURCE_CHANGED]);
    assert.equal(trigger.textContent, 'Delete…');
    assert.equal(trigger.getAttribute('aria-disabled'), null);
});

test('apply sends the token and the mode, and toasts that the job started', async () => {
    const { applyDelete } = module();
    const sent = [], toasts = [];
    const api = { post: async (url, body) => { sent.push([url, body]); return { job: { id: 'j', title: 'Delete images (move to the trash)' } }; } };
    const store = { track: job => job, load: () => {} };
    const job = await applyDelete({ api, store, plan: plan(), mode: 'trash', notify: text => toasts.push(text) });
    assert.equal(job.id, 'j');
    assert.deepEqual(sent, [['/prompt_manager/maintenance/apply', { token: 't', mode: 'trash' }]]);
    assert.deepEqual(toasts, ['Delete images (move to the trash) started.']);
    const refused = await applyDelete({ api: { post: async () => { throw Object.assign(new Error('Busy'), { code: 'busy', running: null }); } },
        store, plan: plan(), mode: 'library', notify: text => toasts.push(text) });
    assert.equal(refused, null);
});
