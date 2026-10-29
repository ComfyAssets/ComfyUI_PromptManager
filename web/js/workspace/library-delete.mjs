import { countOf } from './plural.mjs';
import { openCloseOnlyReview, openReviewDialog } from './workspace-dialogs.mjs';

/**
 * The Prompt library's Delete… review and outcomes (#443, D152, beyond the concept). The concept maps the legacy prompt
 * delete to a "reference-safe archive" reviewed in its `maintenancePlan` dialog; the workspace offers Delete… on the
 * D148 bar, whose review (the shared `openReviewDialog`) says what happens to each selected prompt: deleted (in no saved
 * pair), archived (paired: hidden, restorable) or kept (tagged `__protected__`). The server executes the reviewed
 * lists (`POST /prompt_manager/library/delete`), taking a verified safety backup before any delete when the policy is
 * on. Restore (`/library/restore`) puts archived prompts back on the shelf.
 */
export const PREVIEW_URL = '/prompt_manager/library/delete/preview';
export const DELETE_URL = '/prompt_manager/library/delete';
export const RESTORE_URL = '/prompt_manager/library/restore';
export const REVIEWING = 'Reviewing…';
export const DELETE_LABEL = 'Delete…';
export const RESTORE_LABEL = 'Restore';
// Answers that may follow a committed write: no status, or a gateway's.
const UNCONFIRMED_STATUSES = new Set([502, 503, 504]);
const NOTHING = 'Nothing was deleted or archived.';
export const COPY = Object.freeze({
    reviewFailed: 'The selected prompts could not be reviewed. Try again.',
    storage: `The library was restored or moved since this page loaded. ${NOTHING}`,
    unconfirmed: 'The server did not confirm the change. The Library was reloaded: check the shelf and Show archived prompts.',
    failed: 'The selected prompts could not be changed. Try again.',
    nothing: `${NOTHING} The Library was reloaded.`,
    none: 'None of the selected prompts can be deleted or archived.',
    restoreStorage: 'The library was restored or moved since this page loaded. No prompt was returned to the Library.',
    restoreUnconfirmed: 'Returning the prompts could not be confirmed. The Library was reloaded.',
    restoreFailed: 'The prompts could not be returned to the Library. Try again.',
    archivedNotice: 'This prompt and its negative variants are hidden from the Prompt library; nothing was removed.',
    archivedText: 'This text is an archived prompt. Restore it to show it in the Prompt library again.',
});

const count = value => (Number.isSafeInteger(value) && value >= 0 ? value : 0);
const ids = value => (Array.isArray(value) ? value.filter(id => Number.isSafeInteger(id) && id > 0) : []);
const were = (value, singular, plural) => `${value.toLocaleString('en-US')} ${value === 1 ? singular : plural}`;

/** The preview's classes as counts and id lists, whatever else the answer holds. */
export function reviewOf(preview) {
    return {
        epoch: typeof preview?.library_epoch === 'string' ? preview.library_epoch : null,
        delete: ids(preview?.delete), archive: ids(preview?.archive), protected: ids(preview?.protected),
        alreadyArchived: count(preview?.already_archived), notFound: count(preview?.not_found),
        imageLinks: count(preview?.image_links), backup: preview?.backup === 'off' ? 'off' : 'on',
        disk: preview?.disk && typeof preview.disk === 'object' ? preview.disk : null,
    };
}

export function reviewTitle(review) {
    if (!review.archive.length) return 'Delete selected prompts';
    if (!review.delete.length) return 'Archive selected prompts';
    return 'Delete or archive selected prompts';
}

/** "Title A, Title B, Title C and 2 more": the protected prompts the page has loaded, in selection order. */
export function protectedNames(review, titleOf) {
    const names = review.protected.map(titleOf).filter(name => typeof name === 'string' && name);
    const shown = names.slice(0, 3).join(', ');
    const more = review.protected.length - Math.min(3, names.length);
    return more > 0 ? `${shown}${shown ? ' and ' : ''}${more.toLocaleString('en-US')} more` : shown;
}

/** The review's `[term, value]` rows (design §7.2); rows that do not apply are left out. */
export function reviewGrid(review, titleOf = () => null) {
    const rows = [
        ['Delete permanently', `${countOf(review.delete.length, 'prompt')} not in a saved pair`],
        // The archive reason is the saved pair (a pair's positive or negative), with or without negative variants.
        ['Archive', `${countOf(review.archive.length, 'prompt')} in a saved pair: hidden, restorable`],
    ];
    if (review.protected.length) {
        const names = protectedNames(review, titleOf);
        rows.push(['Protected (kept)', `${review.protected.length.toLocaleString('en-US')}${names ? `: ${names}` : ''}`]);
    }
    if (review.alreadyArchived) rows.push(['Already archived', review.alreadyArchived.toLocaleString('en-US')]);
    if (review.notFound) rows.push(['No longer exist', review.notFound.toLocaleString('en-US')]);
    if (review.delete.length) {
        rows.push(['Image links removed', `${countOf(review.imageLinks, 'image')} will no longer show these prompts (files stay on disk)`]);
        rows.push(['Backup', review.backup === 'on' ? 'Create and verify before deleting'
            : 'Off: no safety backup (System › Backup & export › Recovery policy)']);
    }
    return rows;
}

/** The review's sentences, in order, each when it applies. */
export function reviewSentences(review) {
    const sentences = [];
    if (review.archive.length) {
        sentences.push('Prompts in a saved pair, as its positive or its negative, are archived instead of deleted, so the ' +
            'pair and its runs stay intact. Archived prompts cannot be deleted. To bring them back, press Show archived prompts, ' +
            'select them and press Restore.');
    }
    if (review.delete.length) {
        const undo = review.backup === 'on'
            ? ' Deleted prompts cannot be brought back one by one: the only way back is to restore the safety backup taken ' +
              'first (System › Backup & export), which returns the whole library to that moment. It is kept among the last ' +
              '10 safety backups.'
            : ' Deleted prompts cannot be brought back.';
        sentences.push("Deleting removes each prompt's text, title, category, tags, rating, notes, run count, last use and " +
            `image links.${undo} Image files, favorites, collections and recorded runs stay. A later scan, a new image or a ` +
            'node run with the same text adds it again as a new prompt.');
    }
    if (review.protected.length) sentences.push('Protected prompts are tagged __protected__; remove the tag to delete them.');
    return sentences;
}

export function primaryLabel(review) {
    const deletes = review.delete.length, archives = review.archive.length;
    if (deletes && archives) return `Delete ${deletes.toLocaleString('en-US')} and archive ${countOf(archives, 'prompt')}`;
    return deletes ? `Delete ${countOf(deletes, 'prompt')}` : `Archive ${countOf(archives, 'prompt')}`;
}

/** Why a review cannot start, or null: nothing to change, or no room for the promised backup. */
export function blockedReason(review) {
    if (!review.delete.length && !review.archive.length) return COPY.none;
    if (review.delete.length && review.disk && review.disk.sufficient === false) {
        return typeof review.disk.message === 'string' && review.disk.message
            ? review.disk.message : 'There is not enough free space for the safety backup. Nothing was changed.';
    }
    return null;
}

/**
 * Open the review for `review` (`reviewOf`). `titleOf(id)` names a loaded prompt. A review that cannot start is the
 * close-only review (`onDismiss` runs at once, as nothing is pending); otherwise Start calls `onStart()` and any other
 * close `onDismiss()`.
 */
export function openDeleteReview({ scope, trigger, review, titleOf, onStart, onDismiss }) {
    const title = reviewTitle(review), grid = reviewGrid(review, titleOf);
    const options = reviewSentences(review).map(text => Object.assign(document.createElement('p'), { className: 'small', textContent: text }));
    const blocked = blockedReason(review);
    if (blocked) {
        onDismiss();
        openCloseOnlyReview({ scope, trigger, title, grid, options, sentence: blocked });
        return;
    }
    openReviewDialog({ scope, trigger, title, grid, options, cancelLabel: 'Cancel',
        primary: { label: primaryLabel(review), danger: true }, onStart, onDismiss });
}

/** The write's body: the reviewed lists, the epoch and the reviewed backup promise. */
export function deleteBody(review) {
    return { archive_ids: review.archive, delete_ids: review.delete, expected_epoch: review.epoch, backup: review.backup };
}

function failureOutcome(failure, copy) {
    if (failure?.code === 'storage-changed') return { toast: copy.storage, committed: false, reload: true };
    if (failure?.code === 'busy') {
        const name = failure.running?.title || 'Another job';
        return { toast: `${name} is running. Nothing was changed; try again when it finishes.`, committed: false, reload: false };
    }
    const status = failure?.status;
    if (!Number.isInteger(status) || UNCONFIRMED_STATUSES.has(status)) return { toast: copy.unconfirmed, committed: true, reload: true };
    // The server's own refusals that name their reason (backup-failed, insufficient-space) say "Nothing was changed".
    if (['backup-failed', 'insufficient-space'].includes(failure?.code) && typeof failure.message === 'string' && failure.message) {
        const lead = failure.code === 'backup-failed' ? 'The safety backup failed. ' : '';
        return { toast: `${lead}${failure.message}`, committed: false, reload: false };
    }
    return { toast: copy.failed, committed: false, reload: false };
}

/** What a settled delete means: the toast, whether it may have `committed` and whether the shelf must `reload`. */
export function deleteOutcome(result, failure, review) {
    if (failure !== null || !result?.success) {
        return failureOutcome(failure, { storage: COPY.storage, unconfirmed: COPY.unconfirmed, failed: COPY.failed });
    }
    const deleted = count(result.deleted), archived = count(result.archived);
    const parts = [];
    if (deleted) parts.push(`Deleted ${countOf(deleted, 'prompt')}.`);
    if (archived) parts.push(`Archived ${countOf(archived, 'prompt')}.`);
    if (!parts.length) parts.push(NOTHING);
    const already = count(result.already_archived), missing = count(result.not_found), changed = count(result.changed);
    if (already) parts.push(`${were(already, 'was', 'were')} already archived.`);
    if (missing) parts.push(`${missing.toLocaleString('en-US')} no longer ${missing === 1 ? 'exists' : 'exist'}.`);
    if (changed) parts.push(`${changed.toLocaleString('en-US')} changed since the review and ${changed === 1 ? 'was' : 'were'} kept.`);
    const kept = review?.protected?.length || 0;
    if (kept) parts.push(`${countOf(kept, 'protected prompt')} ${kept === 1 ? 'was' : 'were'} kept.`);
    if (typeof result.backup === 'string' && result.backup) parts.push(`Safety backup saved: ${result.backup} (System › Backup & export).`);
    if (!deleted && !archived) parts.push('The Library was reloaded.');
    return { toast: parts.join(' '), committed: deleted + archived > 0, reload: true };
}

/** What a settled restore means (the same shape as `deleteOutcome`). */
export function restoreOutcome(result, failure) {
    if (failure !== null || !result?.success) {
        return failureOutcome(failure, { storage: COPY.restoreStorage, unconfirmed: COPY.restoreUnconfirmed, failed: COPY.restoreFailed });
    }
    const restored = count(result.restored), unchanged = count(result.unchanged), missing = count(result.not_found);
    const parts = [restored ? `Restored ${countOf(restored, 'prompt')} to the Prompt library.` : 'No prompt was restored.'];
    if (unchanged) parts.push(`${unchanged.toLocaleString('en-US')} ${unchanged === 1 ? 'was' : 'were'} not archived.`);
    if (missing) parts.push(`${missing.toLocaleString('en-US')} no longer ${missing === 1 ? 'exists' : 'exist'}.`);
    return { toast: parts.join(' '), committed: restored > 0, reload: true };
}

/** A failed preview: a restored storage reloads the shelf; anything else keeps the selection. */
export function previewOutcome(failure) {
    if (failure?.code === 'storage-changed') return { toast: COPY.storage, committed: false, reload: true };
    if (failure?.code === 'busy') return failureOutcome(failure, {});
    return { toast: COPY.reviewFailed, committed: false, reload: false };
}
