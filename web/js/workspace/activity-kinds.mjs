/**
 * Per-kind Activity descriptors (#249 System S5, D111): one table that says, for each job kind, what Activity shows.
 *
 * `kindOf(kind)` → `{pillVerb, startToast(job), finishToast(job), line(job), kept(job), scope(job), tiles, outcomes,
 * openable, log, cancelLabel}`:
 * - `pillVerb`: the pill's verb ("Scanning"); null shows the job's title.
 * - `startToast` / `finishToast`: what a toast says when this tab starts the job and when it ends.
 * - `line`: the counters as one line ("N added · M errors"); `kept`: what a cancelled job kept ("N added"); `scope`:
 *   what the job covers ("2 output folders").
 * - `tiles` (`[group, label, tone]`), `outcomes` (`[group, label]`) and `openable` (rows that open the viewer, D57).
 * - `log` and `cancelLabel` (`cancel` is the same label under the job view's older name).
 *
 * `scan` is exactly the D56 view and `image-tags` the D89 one. `thumbnails` counts Generated (green), Skipped and
 * Errors (red). System S7: `maintenance` (a reviewed plan's apply) counts Changed, Protected and Errors and, when it
 * failed, ends with the server's reason; `duplicates` (the search) counts Groups found; `duplicate_cleanup` counts
 * Removed, Kept and Errors; S10: `lora_import` counts Imported, Linked to existing, Skipped, Images downloaded, Download
 * errors and Errors and, when it failed, ends with the server's reason (the previous imports were kept); S9: `restore`
 * ends with the server's line (what was restored, or why nothing changed). Any other kind (the health check, a held legacy-page operation, a kind this page does not know) gets
 * the generic descriptor: title-based copy and no tiles or outcomes, so it never shows another kind's zeros (S1 review
 * M6, S2 review M3). `viewOf(job)` keeps only the groups the job itself counts, so a hold (no groups) has none either.
 *
 * Progress is indeterminate while a job runs without a known total (`indeterminate`): the `<progress>` has no value
 * and the pill shows no percentage, never a made-up 0%. A job shows Cancel only while it runs and the server can stop
 * it (`canCancel`).
 */
import { noun, countOf } from './plural.mjs';

// Grouped as the server writes its status lines ("8,240 of 19,622"), whatever the browser's locale.
const number = value => value.toLocaleString('en-US');
const counted = (job, group, word) => `${number(job.counters?.[group] ?? 0)} ${word}`;
const errorsOf = job => `${number(job.counters?.errors ?? 0)} ${noun(job.counters?.errors ?? 0, 'error')}`;
const whole = value => Number.isSafeInteger(value) && value >= 0;
const tiles = rows => Object.freeze(rows.map(Object.freeze));
const groups = (...names) => Object.freeze(new Set(names));

/** "1,420 added". */
export const addedOf = job => counted(job, 'added', 'added');
/** "12 tagged" (D89). */
export const taggedOf = job => counted(job, 'tagged', 'tagged');
/** "3 generated" (D111). */
export const generatedOf = job => counted(job, 'generated', 'generated');
/** "3 changed" (System S7: a maintenance apply). */
export const changedOf = job => counted(job, 'changed', 'changed');
/** "12 groups found" (System S7: a duplicate search). */
export const groupsOf = job => {
    const groups = job.counters?.groups_found ?? 0;
    return `${number(groups)} ${noun(groups, 'group')} found`;
};
/** "3 removed" (System S7: a duplicate cleanup). */
export const removedOf = job => counted(job, 'removed', 'removed');
/** "12 imported" (System S10: the LoRA reference import). */
export const importedOf = job => counted(job, 'imported', 'imported');

/** #320 D198: the step a Move thumbnails job is in, and what it means meanwhile (under the progress bar). */
export function movePhase(job) {
    const from = job.scope?.from === 'folder' ? 'the old folder' : 'inside each source';
    if (job.state === 'running') {
        if (job.scope?.phase === 'remove') return 'Removing the old copies a move left. Thumbnails are read from the new location.';
        if (job.cancellable !== false) {
            return `Step 1 of 3: copying. Thumbnails are still read from ${from} until the switch. New thumbnails are not `
                + 'written until the move ends; originals are shown instead.';
        }
        return 'Step 2 and 3 of 3: switching, then removing the old copies. This can no longer be cancelled.';
    }
    if (job.state === 'finished') {
        const left = job.counters?.left ?? 0;
        return left ? `Thumbnails are now read from the new location. ${number(left)} ${noun(left, 'file')} that `
            + `${left === 1 ? 'is' : 'are'} not ${left === 1 ? 'a thumbnail was' : 'thumbnails were'} left in place.`
            : 'Thumbnails are now read from the new location.';
    }
    return 'Nothing changed: thumbnails are read from where they were.';
}

/** "2 output folders": what a scan or a thumbnail job walks. */
function foldersOf(job) {
    const folders = job.scope?.output_folders;
    return whole(folders) ? countOf(folders, 'output folder') : '';
}

/** "4 images" (D89), grouped as the server writes its lines. */
function imagesOf(job) {
    const images = job.scope?.images;
    return whole(images) ? `${number(images)} ${noun(images, 'image')}` : '';
}

const titleStarted = job => `${job.title} started.`;

function titleFinished(job) {
    if (job.state === 'cancelled') return `${job.title} was cancelled.`;
    if (job.state === 'failed') return `${job.title} failed.`;
    return `${job.title} finished.`;
}

/** "<Noun> finished · <line>", "<Noun> cancelled · <kept>", "<Noun> failed · <line>". */
const namedFinish = (name, kept, line) => job => {
    if (job.state === 'cancelled') return `${name(job)} cancelled · ${kept(job)}`;
    return `${name(job)} ${job.state === 'failed' ? 'failed' : 'finished'} · ${line(job)}`;
};

const nothing = () => '';
const GENERIC = Object.freeze({
    pillVerb: null, startToast: titleStarted, finishToast: titleFinished, line: nothing, kept: nothing,
    scope: foldersOf, tiles: tiles([]), outcomes: tiles([]), openable: groups(), log: 'Job log', cancelLabel: 'Cancel job',
    cancel: 'Cancel job',
});

const scanLine = job => `${addedOf(job)} · ${errorsOf(job)}`;
const tagLine = job => `${taggedOf(job)} · ${errorsOf(job)}`;
const thumbnailLine = job => `${generatedOf(job)} · ${errorsOf(job)}`;
const changedLine = job => `${changedOf(job)} · ${errorsOf(job)}`;
const cleanupLine = job => `${removedOf(job)} · ${errorsOf(job)}`;
const importLine = job => `${importedOf(job)} · ${errorsOf(job)}`;

/** A job that ends with the server's reason when it failed (the LoRA import keeps the old collection and says so). */
const reasonFinish = (name, kept, line) => job => (job.state === 'failed' && job.status_line
    ? `${name} failed · ${job.status_line}` : namedFinish(() => name, kept, line)(job));

/** A maintenance apply ends with its counters, or, when it failed, the server's reason (plan-changed: review again). */
function maintenanceFinish(job) {
    if (job.state === 'cancelled') return `${job.title} cancelled · ${changedOf(job)}`;
    if (job.state === 'failed') return `${job.title} failed · ${job.status_line || errorsOf(job)}`;
    return `${job.title} finished · ${changedOf(job)}`;
}
// D85: an image tagging job says which of its two modes started.
const IMAGE_TAG_START_TOASTS = Object.freeze({ add: 'Tagging started.', auto: 'Auto-tagging started.' });

/** D89: the image tagging job's view, shared with Tag output images (#328). */
const IMAGE_TAGS = Object.freeze({
    ...GENERIC,
    pillVerb: 'Tagging',
    startToast: job => IMAGE_TAG_START_TOASTS[job.scope?.mode] ?? titleStarted(job),
    finishToast: namedFinish(job => job.title, taggedOf, tagLine),
    line: tagLine,
    kept: taggedOf,
    scope: imagesOf,
    tiles: tiles([['tagged', 'Tagged', 'added'], ['unchanged', 'Unchanged', ''], ['skipped', 'Skipped', 'warn'],
        ['errors', 'Errors', 'bad']]),
    outcomes: tiles([['tagged', 'Tagged'], ['errors', 'Errors'], ['skipped', 'Skipped'], ['unchanged', 'Unchanged']]),
    openable: groups('tagged', 'unchanged'),
    log: 'Tagging log',
    cancelLabel: 'Cancel tagging',
    cancel: 'Cancel tagging',
});

/** Every kind with a descriptor of its own. */
export const KINDS = Object.freeze({
    __proto__: null,
    // D56: the scan's tiles and outcome list in the mockup's orders; what got added and what went wrong first.
    scan: Object.freeze({
        ...GENERIC,
        pillVerb: 'Scanning',
        // #498: "Return images to the library" is a scan of the returned files; its toasts carry its own title.
        startToast: job => (job.scope?.files === 'returned' ? `${job.title} started.` : 'Scan started.'),
        finishToast: namedFinish(job => (job.scope?.files === 'returned' ? job.title : 'Scan'), addedOf, scanLine),
        line: scanLine,
        kept: addedOf,
        tiles: tiles([['added', 'Added to Library', 'added'], ['known', 'Already known', ''],
            ['review', 'Needs review', 'warn'], ['no_metadata', 'No usable metadata', ''], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['added', 'Added to Library'], ['review', 'Needs review'], ['errors', 'Errors'],
            ['no_metadata', 'No usable metadata'], ['known', 'Already known']]),
        openable: groups('added', 'known'),
        log: 'Scan log',
        cancelLabel: 'Cancel scan',
        cancel: 'Cancel scan',
    }),
    // D89: Tagged (green), Unchanged, Skipped (warning) and Errors (red); Tagged and Unchanged rows open the viewer.
    'image-tags': IMAGE_TAGS,
    // #328 (extends D111): Tag output images, the prompt-tag batch, counts its images the same way. Its legacy-page
    // hold counts nothing and is not cancellable, so `viewOf` gives it the generic held view.
    autotag: IMAGE_TAGS,
    // D111: Generated (green), Skipped (already fresh) and Errors (red); a row with a preview opens the viewer.
    thumbnails: Object.freeze({
        ...GENERIC,
        pillVerb: 'Generating thumbnails',
        finishToast: namedFinish(() => 'Thumbnails', generatedOf, thumbnailLine),
        line: thumbnailLine,
        kept: generatedOf,
        tiles: tiles([['generated', 'Generated', 'added'], ['skipped', 'Skipped', ''], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['generated', 'Generated'], ['errors', 'Errors'], ['skipped', 'Skipped']]),
        openable: groups('generated', 'skipped'),
    }),
    // #320 D198: Move thumbnails, a `thumbnails` job with `scope.mode` "move": Moved (green), Already there, Left in
    // place and Errors (red); Cancel only until the switch (the server says so through `cancellable`).
    'thumbnails:move': Object.freeze({
        ...GENERIC,
        pillVerb: 'Moving thumbnails',
        finishToast: job => (job.status_line && job.state !== 'running' ? job.status_line : titleFinished(job)),
        line: job => (job.state === 'running' ? '' : job.status_line || ''),
        kept: job => counted(job, 'moved', 'moved'),
        tiles: tiles([['moved', 'Moved', 'added'], ['skipped', 'Already there', ''], ['left', 'Left in place', ''],
            ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['moved', 'Moved'], ['errors', 'Errors'], ['skipped', 'Already there'], ['left', 'Left in place']]),
        log: 'Move log',
        phase: movePhase,
        cancelLabel: 'Cancel move',
        cancel: 'Cancel move',
    }),
    // #320 D200: Remove thumbnails of removed sources (`scope.mode` "prune"): Removed, Left in place and Errors.
    'thumbnails:prune': Object.freeze({
        ...GENERIC,
        pillVerb: 'Removing thumbnails',
        finishToast: job => (job.status_line && job.state !== 'running' ? job.status_line : titleFinished(job)),
        line: job => (job.state === 'running' ? '' : job.status_line || ''),
        kept: removedOf,
        tiles: tiles([['removed', 'Removed', 'added'], ['left', 'Left in place', ''], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['removed', 'Removed'], ['errors', 'Errors'], ['left', 'Left in place']]),
    }),
    // System S7: a reviewed maintenance plan's apply, titled after its operation; it runs without a known total.
    maintenance: Object.freeze({
        ...GENERIC,
        finishToast: maintenanceFinish,
        line: changedLine,
        kept: changedOf,
        tiles: tiles([['changed', 'Changed', 'added'], ['protected', 'Protected', ''], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['changed', 'Changed'], ['errors', 'Errors'], ['protected', 'Protected']]),
        log: 'Maintenance log',
    }),
    // System S7: the duplicate image search; each found group's row opens its first copy.
    duplicates: Object.freeze({
        ...GENERIC,
        pillVerb: 'Finding duplicates',
        startToast: () => 'Duplicate search started.',
        finishToast: namedFinish(() => 'Duplicate search', groupsOf, groupsOf),
        line: groupsOf,
        kept: groupsOf,
        tiles: tiles([['groups_found', 'Groups found', '']]),
        outcomes: tiles([['groups_found', 'Groups found']]),
        openable: groups('groups_found'),
        log: 'Search log',
        cancelLabel: 'Cancel search',
        cancel: 'Cancel search',
    }),
    // System S7: the reviewed duplicate cleanup; the kept copies open the viewer, the removed ones are gone.
    duplicate_cleanup: Object.freeze({
        ...GENERIC,
        pillVerb: 'Removing duplicate files',
        // A failed cleanup says the server's reason (sources changed, no safety backup), as a maintenance apply does.
        finishToast: job => (job.state === 'failed' && job.status_line ? `Duplicate cleanup failed · ${job.status_line}`
            : namedFinish(() => 'Duplicate cleanup', removedOf, cleanupLine)(job)),
        line: cleanupLine,
        kept: removedOf,
        tiles: tiles([['removed', 'Removed', 'added'], ['kept', 'Kept', ''], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['removed', 'Removed'], ['errors', 'Errors'], ['kept', 'Kept']]),
        openable: groups('kept'),
        log: 'Cleanup log',
    }),
    // System S9: the reviewed database restore (exclusive, never cancellable, indeterminate); it ends with the server's
    // line: what was restored, or why nothing was changed.
    restore: Object.freeze({
        ...GENERIC,
        pillVerb: 'Restoring the database',
        finishToast: job => (job.status_line && job.state !== 'cancelled'
            ? `${job.title} ${job.state === 'failed' ? 'failed' : 'finished'} · ${job.status_line}` : titleFinished(job)),
        line: job => (job.state === 'running' ? '' : job.status_line || ''),
        log: 'Restore log',
    }),
    // System S10: the reviewed LoRA reference import; cancellable only while it downloads (the server says when).
    lora_import: Object.freeze({
        ...GENERIC,
        pillVerb: 'Importing LoRAs',
        finishToast: reasonFinish('LoRA import', importedOf, importLine),
        line: importLine,
        kept: importedOf,
        tiles: tiles([['imported', 'Imported', 'added'], ['linked', 'Linked to existing', ''], ['skipped', 'Skipped', 'warn'],
            ['downloaded', 'Images downloaded', ''], ['download_errors', 'Download errors', 'warn'], ['errors', 'Errors', 'bad']]),
        outcomes: tiles([['imported', 'Imported'], ['errors', 'Errors'], ['skipped', 'Skipped'], ['linked', 'Linked to existing']]),
        log: 'Import log',
        cancelLabel: 'Cancel import',
        cancel: 'Cancel import',
    }),
});

/** The descriptor of a job `kind`; any kind without one gets the generic descriptor. */
export function kindOf(kind) {
    return (typeof kind === 'string' && KINDS[kind]) || GENERIC;
}

const countsOf = job => job.counters && typeof job.counters === 'object' ? job.counters : {};

/**
 * A legacy-page operation held on the registry (D112): not cancellable and counting no outcome groups. Whatever its
 * kind (the gallery's thumbnail clear is a `thumbnails` hold), it reads as the generic job under its own title.
 */
export const held = job => job.cancellable === false && Object.keys(countsOf(job)).length === 0;

/**
 * The descriptor for one job: its kind's, keeping only the outcome groups the job counts; a held job's is the generic
 * one.
 */
export function viewOf(job) {
    if (held(job)) return GENERIC;
    // #320 D198: a thumbnails job chooses its tiles by its scope's mode (move, prune).
    const mode = job.kind === 'thumbnails' ? job.scope?.mode : null;
    const view = (mode === 'move' || mode === 'prune') ? KINDS[`thumbnails:${mode}`] : kindOf(job.kind);
    const counts = countsOf(job);
    const has = ([group]) => Object.hasOwn(counts, group);
    if (view.tiles.every(has) && view.outcomes.every(has)) return view;
    return Object.freeze({ ...view, tiles: tiles(view.tiles.filter(has)), outcomes: tiles(view.outcomes.filter(has)),
        openable: groups(...[...view.openable].filter(group => Object.hasOwn(counts, group))) });
}

/** A running job whose total the server has not reported (yet) has no percentage. */
export function indeterminate(job) {
    return job.state === 'running' && !(job.total > 0);
}

/** Set a `<progress>` to the job's: its value and "N%", or no value at all while indeterminate. */
export function showProgress(node, job) {
    if (indeterminate(job)) {
        node.removeAttribute('value');
        node.textContent = '';
        return;
    }
    node.value = job.progress;
    node.textContent = `${job.progress}%`;
}

/** Truth rule: Cancel only while the job runs and the server can stop it (never on a held legacy job). */
export function canCancel(job) {
    return job.state === 'running' && job.cancellable !== false;
}

/** The pill's text: "Scanning · 42% · 8,240 / 19,622"; the file counts once the total is known, no "%" before. */
export function pillParts(job) {
    const verb = viewOf(job).pillVerb ?? job.title;
    const label = indeterminate(job) ? verb : `${verb} · ${job.progress}%`;
    const files = job.total > 0 ? ` · ${number(job.processed)} / ${number(job.total)}` : '';
    return { label, files, text: label + files };
}

/** The toast after this tab starts a job. */
export const startToast = job => viewOf(job).startToast(job);
/** The toast when a job ends. */
export const finishToast = job => viewOf(job).finishToast(job);
/** The counters as one line; '' for a kind without one. */
export const outcomeLine = job => viewOf(job).line(job);
