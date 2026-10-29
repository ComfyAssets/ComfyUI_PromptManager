const test = require('node:test');
const assert = require('node:assert/strict');

// #443 D152: the Prompt library's Delete… review (delete unpaired, archive paired, keep protected) and Restore.
const source = import('../../web/js/workspace/library-delete.mjs');

const preview = (patch = {}) => ({success: true, library_epoch: 'e', delete: [], archive: [], protected: [],
    already_archived: 0, not_found: 0, image_links: 0, backup: 'on', disk: null, ...patch});

test('the routes are the workspace ones, never the legacy bulk delete', async () => {
    const {PREVIEW_URL, DELETE_URL, RESTORE_URL} = await source;
    assert.equal(PREVIEW_URL, '/prompt_manager/library/delete/preview');
    assert.equal(DELETE_URL, '/prompt_manager/library/delete');
    assert.equal(RESTORE_URL, '/prompt_manager/library/restore');
});

test('the title, primary and body follow what the review will do', async () => {
    const {reviewOf, reviewTitle, primaryLabel, deleteBody} = await source;
    const both = reviewOf(preview({delete: [1, 2], archive: [3]}));
    assert.equal(reviewTitle(both), 'Delete or archive selected prompts');
    assert.equal(primaryLabel(both), 'Delete 2 and archive 1 prompt');
    assert.deepEqual(deleteBody(both), {archive_ids: [3], delete_ids: [1, 2], expected_epoch: 'e', backup: 'on'});
    const onlyDelete = reviewOf(preview({delete: [1]}));
    assert.equal(reviewTitle(onlyDelete), 'Delete selected prompts');
    assert.equal(primaryLabel(onlyDelete), 'Delete 1 prompt');
    const onlyArchive = reviewOf(preview({archive: [4, 5]}));
    assert.equal(reviewTitle(onlyArchive), 'Archive selected prompts');
    assert.equal(primaryLabel(onlyArchive), 'Archive 2 prompts');
});

test('the grid shows each row only when it applies, with protected titles in selection order', async () => {
    const {reviewOf, reviewGrid} = await source;
    const names = {7: 'Seven', 8: 'Eight', 9: 'Nine', 10: 'Ten'};
    const review = reviewOf(preview({delete: [1], archive: [2], protected: [7, 8, 9, 10], already_archived: 1,
        not_found: 2, image_links: 3}));
    assert.deepEqual(reviewGrid(review, id => names[id]), [
        ['Delete permanently', '1 prompt not in a saved pair'],
        ['Archive', '1 prompt in a saved pair: hidden, restorable'],
        ['Protected (kept)', '4: Seven, Eight, Nine and 1 more'],
        ['Already archived', '1'],
        ['No longer exist', '2'],
        ['Image links removed', '3 images will no longer show these prompts (files stay on disk)'],
        ['Backup', 'Create and verify before deleting'],
    ]);
    const archiveOnly = reviewOf(preview({archive: [2]}));
    assert.deepEqual(reviewGrid(archiveOnly).map(([term]) => term), ['Delete permanently', 'Archive']);
    const off = reviewOf(preview({delete: [1], backup: 'off'}));
    assert.equal(reviewGrid(off).at(-1)[1], 'Off: no safety backup (System › Backup & export › Recovery policy)');
});

test('the Archive row names the saved pair, never negative variants, for one or many prompts', async () => {
    const {reviewOf, reviewGrid} = await source;
    // A saved-pair prompt with 0 negative variants (live prompt 7901) is archive-only for its pair, not for variants.
    const one = reviewGrid(reviewOf(preview({archive: [2]}))).find(([term]) => term === 'Archive');
    assert.deepEqual(one, ['Archive', '1 prompt in a saved pair: hidden, restorable']);
    const many = reviewGrid(reviewOf(preview({archive: [2, 3, 4]}))).find(([term]) => term === 'Archive');
    assert.deepEqual(many, ['Archive', '3 prompts in a saved pair: hidden, restorable']);
    for (const [, value] of [one, many]) assert.doesNotMatch(value, /variant/);
});

test('the sentences explain archive, irreversibility per backup policy, re-creation and protection', async () => {
    const {reviewOf, reviewSentences} = await source;
    const [archive, remove, guarded] = reviewSentences(reviewOf(preview({delete: [1], archive: [2], protected: [3]})));
    // Live test (#470): the archive reason is the saved pair, which a prompt with 0 negative variants has too.
    assert.match(archive, /^Prompts in a saved pair, as its positive or its negative, are archived instead of deleted, so the pair and its runs stay intact\./);
    assert.doesNotMatch(archive, /variant/);
    assert.match(archive, /Archived prompts cannot be deleted\. To bring them back, press Show archived prompts/);
    assert.match(remove, /the only way back is to restore the safety backup taken first \(System › Backup & export\), which returns the whole library to that moment\. It is kept among the last 10 safety backups\./);
    assert.match(remove, /A later scan, a new image or a node run with the same text adds it again as a new prompt\.$/);
    assert.equal(guarded, 'Protected prompts are tagged __protected__; remove the tag to delete them.');
    const [off] = reviewSentences(reviewOf(preview({delete: [1], backup: 'off'})));
    assert.match(off, /Deleted prompts cannot be brought back\. Image files/);
    assert.doesNotMatch(off, /safety backup/);
});

test('a review that cannot start says why: nothing to change, or no room for the backup', async () => {
    const {reviewOf, blockedReason, COPY} = await source;
    assert.equal(blockedReason(reviewOf(preview({protected: [1]}))), COPY.none);
    assert.equal(blockedReason(reviewOf(preview({delete: [1], disk: {sufficient: false, message: 'Not enough. Nothing was changed.'}}))),
        'Not enough. Nothing was changed.');
    assert.match(blockedReason(reviewOf(preview({delete: [1], disk: {sufficient: false}}))), /not enough free space/);
    assert.equal(blockedReason(reviewOf(preview({delete: [1], disk: {sufficient: true}}))), null);
    assert.equal(blockedReason(reviewOf(preview({archive: [1]}))), null);
});

test('a delete outcome counts each part, singular and plural, and names the backup', async () => {
    const {reviewOf, deleteOutcome} = await source;
    const review = reviewOf(preview({delete: [1, 2], archive: [3], protected: [9]}));
    assert.deepEqual(deleteOutcome({success: true, deleted: 2, archived: 3, already_archived: 0, not_found: 0, changed: 0,
        backup: 'prompts.db.backup_x'}, null, review), {committed: true, reload: true,
        toast: 'Deleted 2 prompts. Archived 3 prompts. 1 protected prompt was kept. Safety backup saved: prompts.db.backup_x (System › Backup & export).'});
    assert.equal(deleteOutcome({success: true, deleted: 1, archived: 0, already_archived: 1, not_found: 1, changed: 1,
        backup: null}, null, reviewOf(preview())).toast,
    'Deleted 1 prompt. 1 was already archived. 1 no longer exists. 1 changed since the review and was kept.');
    assert.equal(deleteOutcome({success: true, deleted: 0, archived: 0, already_archived: 2, not_found: 3, changed: 2,
        backup: null}, null, reviewOf(preview({protected: [1, 2]}))).toast,
    'Nothing was deleted or archived. 2 were already archived. 3 no longer exist. 2 changed since the review and were kept. 2 protected prompts were kept. The Library was reloaded.');
    assert.equal(deleteOutcome({success: true, deleted: 0, archived: 0}, null, reviewOf(preview())).committed, false);
});

test('a failed delete says nothing changed, or that it may have, and keeps or reloads the selection', async () => {
    const {deleteOutcome, COPY} = await source;
    assert.deepEqual(deleteOutcome(null, {status: 409, code: 'storage-changed'}), {toast: COPY.storage, committed: false, reload: true});
    assert.equal(COPY.storage, 'The library was restored or moved since this page loaded. Nothing was deleted or archived.');
    assert.deepEqual(deleteOutcome(null, {status: 409, code: 'busy', running: {title: 'Scan'}}),
        {toast: 'Scan is running. Nothing was changed; try again when it finishes.', committed: false, reload: false});
    assert.deepEqual(deleteOutcome(null, {status: 500, code: 'backup-failed', message: 'The safety backup could not be taken and verified; nothing was changed.'}),
        {toast: 'The safety backup failed. The safety backup could not be taken and verified; nothing was changed.', committed: false, reload: false});
    assert.deepEqual(deleteOutcome(null, {status: 507, code: 'insufficient-space', message: 'Not enough free disk space. Nothing was changed.'}),
        {toast: 'Not enough free disk space. Nothing was changed.', committed: false, reload: false});
    for (const failure of [new TypeError('Failed to fetch'), {status: 502}, {status: 503}, {status: 504}]) {
        assert.deepEqual(deleteOutcome(null, failure), {toast: COPY.unconfirmed, committed: true, reload: true});
    }
    assert.deepEqual(deleteOutcome(null, {status: 400}), {toast: COPY.failed, committed: false, reload: false});
});

test('a failed preview reloads only after a restore; Restore outcomes have their own words', async () => {
    const {previewOutcome, restoreOutcome, COPY} = await source;
    assert.deepEqual(previewOutcome({status: 409, code: 'storage-changed'}), {toast: COPY.storage, committed: false, reload: true});
    assert.deepEqual(previewOutcome({status: 500}), {toast: COPY.reviewFailed, committed: false, reload: false});
    assert.equal(previewOutcome({status: 409, code: 'busy', running: {title: 'Optimize'}}).toast,
        'Optimize is running. Nothing was changed; try again when it finishes.');
    assert.deepEqual(restoreOutcome({success: true, restored: 2, unchanged: 1, not_found: 1}, null),
        {toast: 'Restored 2 prompts to the Prompt library. 1 was not archived. 1 no longer exists.', committed: true, reload: true});
    assert.deepEqual(restoreOutcome({success: true, restored: 0, unchanged: 2, not_found: 0}, null),
        {toast: 'No prompt was restored. 2 were not archived.', committed: false, reload: true});
    assert.deepEqual(restoreOutcome(null, {status: 409, code: 'storage-changed'}), {toast: COPY.restoreStorage, committed: false, reload: true});
    assert.deepEqual(restoreOutcome(null, {status: 503}), {toast: COPY.restoreUnconfirmed, committed: true, reload: true});
    assert.deepEqual(restoreOutcome(null, {status: 400}), {toast: COPY.restoreFailed, committed: false, reload: false});
});

test('reviewOf keeps only well-formed ids and counts from an answer', async () => {
    const {reviewOf} = await source;
    const review = reviewOf({library_epoch: 3, delete: [1, 'x', -2, 2], archive: null, protected: [1.5], already_archived: -1,
        not_found: 'x', image_links: 2, backup: 'maybe'});
    assert.deepEqual(review, {epoch: null, delete: [1, 2], archive: [], protected: [], alreadyArchived: 0, notFound: 0,
        imageLinks: 2, backup: 'on', disk: null});
});

test('the archived notice says why (the saved pair) and names negative variants only when there are any', async () => {
    const {archivedNoticeText} = await source;
    assert.equal(archivedNoticeText(0), 'This prompt is archived because it is in a saved pair. It is hidden from the Prompt ' +
        'library; nothing was removed.');
    assert.equal(archivedNoticeText(1), 'This prompt is archived because it is in a saved pair. It and its 1 negative variant ' +
        'are hidden from the Prompt library; nothing was removed.');
    assert.equal(archivedNoticeText(3), 'This prompt is archived because it is in a saved pair. It and its 3 negative variants ' +
        'are hidden from the Prompt library; nothing was removed.');
    // An unknown count (an older server) never claims variants.
    assert.equal(archivedNoticeText(undefined), archivedNoticeText(0));
});
