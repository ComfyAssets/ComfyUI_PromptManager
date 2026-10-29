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
    noneArchived: 'None of the selected prompts can be deleted.',
    restoreStorage: 'The library was restored or moved since this page loaded. No prompt was returned to the Library.',
    restoreUnconfirmed: 'Returning the prompts could not be confirmed. The Library was reloaded.',
    restoreFailed: 'The prompts could not be returned to the Library. Try again.',
    archivedText: 'This text is an archived prompt. Restore it to show it in the Prompt library again.',
});

const count = value => (Number.isSafeInteger(value) && value >= 0 ? value : 0);
const ids = value => (Array.isArray(value) ? value.filter(id => Number.isSafeInteger(id) && id > 0) : []);
const were = (value, singular, plural) => `${value.toLocaleString('en-US')} ${value === 1 ? singular : plural}`;
const DIGEST = /^[0-9a-f]{64}$/;

/**
 * The preview's classes as counts and id lists, whatever else the answer holds. On the archived `shelf` (#492) the
 * review is the permanent delete only: the choice is fixed to `delete`.
 */
export function reviewOf(preview, shelf = 'active') {
    const archivedShelf = shelf === 'archived';
    return {
        shelf: archivedShelf ? 'archived' : 'active', notArchived: count(preview?.not_archived),
        epoch: typeof preview?.library_epoch === 'string' ? preview.library_epoch : null,
        delete: ids(preview?.delete), archive: ids(preview?.archive), protected: ids(preview?.protected),
        alreadyArchived: count(preview?.already_archived), notFound: count(preview?.not_found),
        imageLinks: count(preview?.image_links), backup: preview?.backup === 'off' ? 'off' : 'on',
        disk: preview?.disk && typeof preview.disk === 'object' ? preview.disk : null,
        ...pairedOf(preview), ...(archivedShelf ? { choice: 'delete' } : {}),
    };
}
const onArchivedShelf = review => review.shelf === 'archived';

/** #482: the archive ids that may be deleted permanently (each with its review digest), and the pair counts. */
function pairedOf(preview) {
    const archive = new Set(ids(preview?.archive)), given = preview?.review_digests ?? {};
    const deletable = ids(preview?.deletable).filter(id => archive.has(id) && DIGEST.test(String(given?.[id] ?? '')));
    const counts = preview?.paired_counts ?? {};
    return {
        deletable, digests: Object.fromEntries(deletable.map(id => [id, given[id]])),
        pairedCounts: { pairs: count(counts.pairs), archivedPairs: count(counts.archived_pairs),
            variants: count(counts.variants), keptNegatives: count(counts.kept_negatives) },
        pairedImageLinks: count(preview?.paired_image_links), choice: 'archive',
    };
}

/** #482: whether the review asks what to do with prompts in a saved pair. */
export const hasPairedChoice = review => !onArchivedShelf(review) && review.deletable.length > 0;

/** #482: the review with its saved-pair choice (`archive`, the default, or `delete`); the original is unchanged. */
export function withChoice(review, choice) {
    if (onArchivedShelf(review)) return { ...review, choice: 'delete' };
    return { ...review, choice: choice === 'delete' && hasPairedChoice(review) ? 'delete' : 'archive' };
}

const permanent = review => review.choice === 'delete';
/** What is deleted and archived: Delete permanently moves the deletable ids from archive to delete. */
function plan(review) {
    if (!permanent(review)) return { plain: review.delete, paired: [], archive: review.archive };
    const chosen = new Set(review.deletable);
    return { plain: review.delete, paired: review.deletable, archive: review.archive.filter(id => !chosen.has(id)) };
}

export function reviewTitle(review) {
    const { plain, paired, archive } = plan(review), deletes = plain.length + paired.length;
    if (onArchivedShelf(review)) return 'Delete archived prompts';
    if (!archive.length) return 'Delete selected prompts';
    if (!deletes) return 'Archive selected prompts';
    return 'Delete or archive selected prompts';
}

/** "Title A, Title B, Title C and 2 more": the protected prompts the page has loaded, in selection order. */
export function protectedNames(review, titleOf) {
    const names = review.protected.map(titleOf).filter(name => typeof name === 'string' && name);
    const shown = names.slice(0, 3).join(', ');
    const more = review.protected.length - Math.min(3, names.length);
    return more > 0 ? `${shown}${shown ? ' and ' : ''}${more.toLocaleString('en-US')} more` : shown;
}

/** An archived prompt's page notice: why it is archived, and its negative variants only when it has any. */
export function archivedNoticeText(variantCount) {
    const hidden = Number.isSafeInteger(variantCount) && variantCount > 0
        ? `It and its ${countOf(variantCount, 'negative variant')} are hidden`
        : 'It is hidden';
    return `This prompt is archived because it is in a saved pair. ${hidden} from the Prompt library; nothing was removed.`;
}

/** The review's `[term, value]` rows (design §7.2); rows that do not apply are left out. */
export function reviewGrid(review, titleOf = () => null) {
    if (permanent(review)) return permanentGrid(review, titleOf);
    const rows = [
        ['Delete permanently', `${countOf(review.delete.length, 'prompt')} not in a saved pair`],
        // The archive reason is the saved pair (a pair's positive or negative), with or without negative variants.
        ['Archive', `${countOf(review.archive.length, 'prompt')} in a saved pair: hidden, restorable`],
    ];
    keptRows(rows, review, titleOf);
    if (review.delete.length) deleteRows(rows, review, review.imageLinks);
    return rows;
}

function keptRows(rows, review, titleOf) {
    if (review.protected.length) {
        const names = protectedNames(review, titleOf);
        rows.push(['Protected (kept)', `${review.protected.length.toLocaleString('en-US')}${names ? `: ${names}` : ''}`]);
    }
    if (review.alreadyArchived) rows.push(['Already archived', review.alreadyArchived.toLocaleString('en-US')]);
    if (review.notArchived) rows.push(['No longer archived (kept)', review.notArchived.toLocaleString('en-US')]);
    if (review.notFound) rows.push(['No longer exist', review.notFound.toLocaleString('en-US')]);
}

function deleteRows(rows, review, images) {
    rows.push(['Image links removed', `${countOf(images, 'image')} will no longer show these prompts (files stay on disk)`]);
    rows.push(['Backup', review.backup === 'on' ? 'Create and verify before deleting'
        : 'Off: no safety backup (System › Backup & export › Recovery policy)']);
}

/** #482: the grid when Delete permanently is chosen. */
function permanentGrid(review, titleOf) {
    const { plain, paired, archive } = plan(review), counts = review.pairedCounts;
    const parts = [plain.length && `${plain.length.toLocaleString('en-US')} not in a saved pair`,
        `${paired.length.toLocaleString('en-US')} in a saved pair`].filter(Boolean);
    const total = plain.length + paired.length;
    const deleted = plain.length ? `${countOf(total, 'prompt')}: ${parts.join(', ')}` : `${countOf(total, 'prompt')} in a saved pair`;
    const details = [counts.variants && `${counts.variants.toLocaleString('en-US')} with a negative prompt`,
        counts.archivedPairs && `${counts.archivedPairs.toLocaleString('en-US')} archived`].filter(Boolean);
    const rows = [['Delete permanently', paired.length ? deleted : `${countOf(total, 'prompt')} not in a saved pair`]];
    // #492: on the archived shelf a selection may hold no prompt in a saved pair.
    if (paired.length) {
        rows.push(['Saved pairs removed', `${countOf(counts.pairs, 'saved pair')}${details.length ? ` (${details.join(', ')})` : ''}`]);
        if (counts.keptNegatives) rows.push(['Negatives kept', `${countOf(counts.keptNegatives, 'negative prompt')} ${counts.keptNegatives === 1 ? 'is' : 'are'} kept`]);
    }
    if (archive.length && onArchivedShelf(review)) rows.push(['Stays archived', `${countOf(archive.length, 'prompt')} used as another prompt's negative`]);
    else if (archive.length) rows.push(['Archive', `${countOf(archive.length, 'prompt')} used as another prompt's negative: hidden, restorable`]);
    keptRows(rows, review, titleOf);
    deleteRows(rows, review, review.imageLinks + review.pairedImageLinks);
    return rows;
}

/** The review's sentences, in order, each when it applies. */
export function reviewSentences(review) {
    if (permanent(review)) return permanentSentences(review);
    const sentences = [];
    if (review.archive.length) {
        sentences.push('Prompts in a saved pair, as its positive or its negative, are archived instead of deleted, so the ' +
            'pair and its runs stay intact. To delete them later or bring them back, press Show archived prompts, select them ' +
            'and press Delete… or Restore.');
    }
    if (review.delete.length) sentences.push(removeSentence(review));
    if (review.protected.length) sentences.push(PROTECTED_SENTENCE);
    return sentences;
}

const PROTECTED_SENTENCE = 'Protected prompts are tagged __protected__; remove the tag to delete them.';
function removeSentence(review, pairs = false) {
    const undo = review.backup === 'on'
        ? ' Deleted prompts cannot be brought back one by one: the only way back is to restore the safety backup taken ' +
          'first (System › Backup & export), which returns the whole library to that moment. It is kept among the last ' +
          '10 safety backups.'
        : ' Deleted prompts cannot be brought back.';
    return "Deleting removes each prompt's text, title, category, tags, rating, notes, run count, last use and " +
        `image links.${undo} Image files, favorites, collections and recorded runs stay. A later scan, a new image or a ` +
        `node run with the same text adds it again as a new prompt.${pairs ? ' A pair node run with the same text saves a new pair.' : ''}`;
}

/** #482: the sentences when Delete permanently is chosen. */
function permanentSentences(review) {
    const { paired, archive } = plan(review);
    const sentences = paired.length ? ['Deleting a prompt in a saved pair also removes its saved pairs, with their titles, ' +
        'categories, ratings, notes, tags, run counts and image links. Their negative prompts are kept. Recorded runs stay ' +
        'in History and Activity, listed by their text.', removeSentence(review, true)] : [removeSentence(review)];
    if (archive.length && onArchivedShelf(review)) sentences.push("Prompts used as another prompt's negative stay archived, so that prompt's variant stays.");
    else if (archive.length) sentences.push("Prompts used as another prompt's negative are archived instead of deleted, " +
        "so that prompt's variant stays.");
    if (review.protected.length) sentences.push(PROTECTED_SENTENCE);
    return sentences;
}

export function primaryLabel(review) {
    const { plain, paired, archive } = plan(review);
    const deletes = plain.length + paired.length, archives = archive.length;
    // #492: on the archived shelf what cannot be deleted stays archived; the primary names only the deletes.
    if (onArchivedShelf(review)) return `Delete ${countOf(deletes, 'prompt')}`;
    if (deletes && archives) return `Delete ${deletes.toLocaleString('en-US')} and archive ${countOf(archives, 'prompt')}`;
    return deletes ? `Delete ${countOf(deletes, 'prompt')}` : `Archive ${countOf(archives, 'prompt')}`;
}

/** Why a review cannot start, or null: nothing to change, or no room for the promised backup. */
export function blockedReason(review) {
    if (onArchivedShelf(review) && !review.delete.length && !review.deletable.length) return COPY.noneArchived;
    if (!review.delete.length && !review.archive.length) return COPY.none;
    const deletes = review.delete.length + plan(review).paired.length;
    if (deletes && review.disk && review.disk.sufficient === false) {
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
    let current = withChoice(review, 'archive');
    const paragraphs = target => reviewSentences(target).map(text => paragraph(text));
    const title = reviewTitle(current), grid = reviewGrid(current, titleOf);
    let sentences = paragraphs(current);
    const blocked = blockedReason(current);
    if (blocked) {
        onDismiss();
        openCloseOnlyReview({ scope, trigger, title, grid, options: sentences, sentence: blocked });
        return;
    }
    // #482: prompts in a saved pair ask Archive (the default, as #443) or Delete permanently, with their pairs.
    const choice = hasPairedChoice(review) ? pairedChoice(value => update(value)) : null;
    const dialog = openReviewDialog({ scope, trigger, title, grid, options: choice ? [choice, ...sentences] : sentences,
        cancelLabel: 'Cancel', primary: { label: primaryLabel(current), danger: true },
        onStart: () => onStart(current), onDismiss });
    function update(value) {
        current = withChoice(review, value);
        dialog.setRows(reviewGrid(current, titleOf));
        const reason = blockedReason(current);
        const next = paragraphs(current);
        if (reason) next.push(paragraph(reason));
        for (const node of sentences) node.remove();
        choice.after(...next);
        sentences = next;
        const heading = choice.closest('dialog')?.querySelector('#modal-title');
        if (heading) heading.textContent = reviewTitle(current);
        dialog.setPrimary({ label: primaryLabel(current), disabled: Boolean(reason) });
    }
}

const paragraph = text => Object.assign(document.createElement('p'), { className: 'small', textContent: text });
export const PAIRED_CHOICES = Object.freeze([
    Object.freeze({ value: 'archive', label: 'Archive (hidden, restorable)' }),
    Object.freeze({ value: 'delete', label: 'Delete permanently, with their saved pairs' }),
]);

/** #482: the D90 radio row ("Prompts in a saved pair"), Archive checked; it is built fresh for every review. */
function pairedChoice(onChange) {
    const group = document.createElement('div');
    group.className = 'field';
    group.setAttribute('role', 'radiogroup');
    const caption = document.createElement('span');
    caption.textContent = 'Prompts in a saved pair';
    caption.id = 'library-delete-choice-label';
    group.setAttribute('aria-labelledby', caption.id);
    group.append(caption);
    for (const choice of PAIRED_CHOICES) {
        const label = document.createElement('label');
        const input = document.createElement('input');
        Object.assign(input, { type: 'radio', name: 'library-delete-choice', value: choice.value, checked: choice.value === 'archive' });
        input.addEventListener('change', () => { if (input.checked) onChange(choice.value); });
        label.append(input, ` ${choice.label}`);
        group.append(label);
    }
    return group;
}

/** The write's body: the reviewed lists, the epoch and the reviewed backup promise. */
export function deleteBody(review) {
    const body = { archive_ids: review.archive, delete_ids: review.delete, expected_epoch: review.epoch, backup: review.backup };
    // #492: the archived shelf sends only what it deletes (its prompts are archived already).
    if (onArchivedShelf(review)) {
        return { ...body, archive_ids: review.deletable, delete_paired_ids: review.deletable, paired_digests: review.digests,
            shelf: 'archived' };
    }
    // #482: the archive ids to delete permanently, each with the digest of what the review showed.
    if (permanent(review)) Object.assign(body, { delete_paired_ids: review.deletable, paired_digests: review.digests });
    return body;
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
    const parts = [], pairs = count(result.pairs_removed);
    if (deleted) parts.push(`Deleted ${countOf(deleted, 'prompt')}.`);
    if (pairs) parts.push(`Removed ${countOf(pairs, 'saved pair')}.`);
    if (archived) parts.push(`Archived ${countOf(archived, 'prompt')}.`);
    if (!parts.length) parts.push(NOTHING);
    const already = count(result.already_archived), missing = count(result.not_found), changed = count(result.changed);
    if (already) parts.push(`${were(already, 'was', 'were')} already archived.`);
    const restored = count(result.not_archived);
    if (restored) parts.push(`${were(restored, 'was', 'were')} restored since the review and kept.`);
    if (missing) parts.push(`${missing.toLocaleString('en-US')} no longer ${missing === 1 ? 'exists' : 'exist'}.`);
    if (changed) parts.push(`${changed.toLocaleString('en-US')} changed since the review and ${changed === 1 ? 'was' : 'were'} not deleted.`);
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

/**
 * #481: review and run a delete of `ids` outside the selection bar (a prompt's own page): the same preview, review
 * (#482's choice included), backup and write. `onSettled(outcome, result, failure)` runs once the write has answered
 * (or the preview failed: `result` null); a dismissed review calls `onDismiss()`. The toast is the caller's.
 */
export async function reviewPromptDelete({ api, scope, trigger, ids: promptIds, epoch, titleOf, onSettled, onDismiss = () => {} }) {
    const body = { prompt_ids: promptIds, expected_epoch: epoch };
    let preview = null, failure = null;
    try {
        preview = await api.post(PREVIEW_URL, body, { signal: scope.signal });
    } catch (error) {
        failure = error;
    }
    if (scope.signal.aborted) return;
    if (failure !== null || !preview?.success) { onSettled(previewOutcome(failure), null, failure); return; }
    const review = { ...reviewOf(preview), epoch };
    openDeleteReview({ scope, trigger, review, titleOf, onDismiss,
        onStart: async chosen => {
            let result = null, error = null;
            try {
                result = await api.post(DELETE_URL, deleteBody(chosen));
            } catch (caught) {
                error = caught;
            }
            onSettled(deleteOutcome(result, error, chosen), result?.success ? result : null, error);
        } });
}
