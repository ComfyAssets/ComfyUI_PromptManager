/**
 * System › Maintenance (#249 System S7 Task 19): the concept's `renderSystem` "maintenance" body (app.js 688–690).
 *
 * - The notice "Check first. Review changes. Then apply." with "Run health checks" (D123), then the surfaces
 *   "Database" and "Images & cache" with the concept's eight operations, in its order, labels and danger marks.
 * - Integrity & relationship checks → Check runs the same read-only health check and opens the Health report (D95);
 *   its "Review missing links" reviews a fresh Missing-file links plan, never a cleanup.
 * - Duplicate prompt text, Optimize database, Unused prompt components and Missing-file links → Review plan open the
 *   D100 plan review (maintenance-review.mjs), which applies only what it showed, as an Activity job.
 * - Duplicate image files → Find duplicates starts the duplicate search as an Activity job and opens it; its view
 *   offers Review groups (D101) once it found some.
 * - Thumbnail cache → Review options opens the S5 "Thumbnail maintenance" dialog (D99).
 * - D102 (Q5): Metadata cache clears only the browser entries Prompt Manager owns as a cache (cache-ownership.js,
 *   #222). The inventory has none, so the row says "Nothing is cached in this browser." and its Review plan is
 *   aria-disabled; with owned entries it reviews and clears exactly those.
 */
import '../cache-ownership.js';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { operationRow } from './system-controls.mjs';
import { healthNotice, runHealthAction } from './system-overview.mjs';
import { openThumbnailReview } from './thumbnail-review.mjs';
import { busyToast } from './activity-scan.mjs';
import { openReviewDialog } from './workspace-dialogs.mjs';
import { reviewPlan } from './maintenance-review.mjs';
import { noun } from './plural.mjs';

export const DUPLICATES_KIND = 'duplicates';
export const FIND_FAILED = 'The duplicate search could not start. Try again.';
export const NOTHING_CACHED_REASON = 'Nothing is cached in this browser.';
export const STORAGE_UNCHECKED_REASON = 'This browser did not allow checking its storage.';
export const METADATA_CACHE_DESCRIPTION = 'Clear only Prompt Manager metadata cached by this browser.';

/** The concept's two surfaces and their rows: `[title, description, label, action, danger]`. */
export const MAINTENANCE_SURFACES = Object.freeze([
    ['Database', [
        ['Integrity & relationship checks', 'Validate foreign keys, text hashes, pair uniqueness and image associations.', 'Check', 'health'],
        ['Duplicate prompt text', 'Review canonical text reuse and dependency-safe merging.', 'Review plan', 'duplicate_prompts'],
        ['Optimize database', 'Reclaim unused SQLite space. This does not delete prompts or media.', 'Review plan', 'optimize'],
        ['Unused prompt components', 'Preview unreferenced components. Pair references and history remain protected.', 'Review plan',
            'unused_components', true],
    ]],
    ['Images & cache', [
        ['Missing-file links', 'Remove stale database links only. Files on disk are never deleted by this operation.', 'Review plan',
            'missing_links', true],
        ['Duplicate image files', 'Find matching file content, review groups and choose what to keep. Separate from duplicate prompt text.',
            'Find duplicates', 'duplicates'],
        ['Thumbnail cache', 'Generate missing thumbnails or explicitly rebuild all.', 'Review options', 'thumbnails'],
        ['Metadata cache', METADATA_CACHE_DESCRIPTION, 'Review plan', 'metadata_cache'],
    ]],
]);

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

const ownership = () => window.CacheOwnership;
const browserStorage = () => window.localStorage;
const cacheStorage = () => ('caches' in window ? window.caches : null);

/** Start the duplicate search and open its job; a refused or failed start says why and stays. */
export async function findDuplicates({ store, scope, notify = showWorkspaceToast, navigate = navigateTo }) {
    try {
        const result = await store.start(DUPLICATES_KIND);
        if (scope.signal.aborted) return;
        if (result.busy !== undefined) {
            notify(busyToast(result.busy));
            return;
        }
        navigate(`#/activity/${result.job.id}`);
    } catch {
        if (!scope.signal.aborted) notify(FIND_FAILED);
    }
}

/**
 * D102: the Metadata cache row's description: the concept's, plus the reason its review is unavailable. A browser that
 * refused to list its storage is never claimed empty.
 */
export function metadataCacheDescription(entries) {
    if (entries.keys.length + entries.caches.length > 0) return METADATA_CACHE_DESCRIPTION;
    return `${METADATA_CACHE_DESCRIPTION} ${entries.denied ? STORAGE_UNCHECKED_REASON : NOTHING_CACHED_REASON}`;
}

/** D102: review the owned browser entries, then clear exactly those and say what happened. */
export function openMetadataCacheReview({ scope, entries, trigger = null, notify = showWorkspaceToast,
    cache = ownership(), onCleared = () => {} }) {
    const count = entries.keys.length + entries.caches.length;
    openReviewDialog({ scope, trigger, title: 'Review maintenance plan', heading: 'Metadata cache',
        grid: [['Scope', `${count.toLocaleString('en-US')} cached ${noun(count, 'entry', 'entries')} in this browser`],
            ['Protected', 'Preferences and other tools’ data in this browser'],
            ['Backup', 'Not needed: the database is not changed'],
            ['Media on disk', 'No files are removed']],
        sentence: [...entries.keys, ...entries.caches].join(' · '),
        cancelLabel: 'Cancel', primary: { label: 'Clear browser cache' },
        onStart: async () => {
            const result = await cache.clearOwned({ storage: browserStorage, cacheStorage: cacheStorage() });
            if (scope.signal.aborted) return;
            notify(cache.clearMessage(result));
            onCleared();
        } });
}

/** The Metadata cache row: aria-disabled with its reason while nothing is owned (D102). */
function metadataCacheRow({ scope, notify, cache }) {
    const row = operationRow({ scope, title: 'Metadata cache', description: METADATA_CACHE_DESCRIPTION, label: 'Review plan',
        onClick: async button => {
            if (button.getAttribute('aria-disabled') === 'true') return;
            const entries = await cache.ownedEntries({ storage: browserStorage, cacheStorage: cacheStorage() });
            if (scope.signal.aborted) return;
            if (entries.keys.length + entries.caches.length === 0) {
                paint(entries);
                return;
            }
            openMetadataCacheReview({ scope, entries, trigger: button, notify, cache, onCleared: refresh });
        } });
    const button = row.querySelector('.row-actions > button');
    const description = row.querySelector('p');
    function paint(entries) {
        description.textContent = metadataCacheDescription(entries);
        if (entries.keys.length + entries.caches.length > 0) button.removeAttribute('aria-disabled');
        else button.setAttribute('aria-disabled', 'true');
    }
    function refresh() {
        cache.ownedEntries({ storage: browserStorage, cacheStorage: cacheStorage() }).then(entries => {
            if (!scope.signal.aborted) paint(entries);
        });
    }
    paint({ keys: [], caches: [] });
    refresh();
    return row;
}

/**
 * The Health report's "Review missing links" (D95): a fresh Missing-file links plan, never a cleanup, holding the pressed
 * button while it is read. Maintenance and Diagnostics & logs (S12) pass it to `runHealthAction`.
 */
export function reviewMissingLinks({ api, store, scope, notify = showWorkspaceToast, navigate = navigateTo }) {
    return (trigger, pressed = trigger) => reviewPlan({ api, store, scope, operation: 'missing_links', trigger, hold: pressed,
        notify, navigate });
}

/**
 * The Maintenance section's mount (`mountSystem`'s `sections.maintenance`): the notice and the two surfaces. `activity`
 * is the page's Activity store.
 */
export function mountMaintenance(container, { api, activity, scope, navigate = navigateTo, notify = showWorkspaceToast,
    cache = ownership() }) {
    const reviewMissing = reviewMissingLinks({ api, store: activity, scope, notify, navigate });
    const actions = {
        health: button => runHealthAction({ api, activity, scope, button, onReviewMissing: reviewMissing, notify }),
        duplicates: () => findDuplicates({ store: activity, scope, notify, navigate }),
        thumbnails: button => openThumbnailReview({ api, store: activity, scope, trigger: button, navigate }),
    };
    const surfaces = MAINTENANCE_SURFACES.map(([heading, rows]) => {
        const surface = el('section', undefined, 'surface');
        surface.append(el('h2', heading), ...rows.map(([title, description, label, action, danger = false]) => {
            if (action === 'metadata_cache') return metadataCacheRow({ scope, notify, cache });
            const onClick = actions[action]
                ?? (button => reviewPlan({ api, store: activity, scope, operation: action, trigger: button, notify, navigate }));
            return operationRow({ scope, title, description, label, danger, onClick });
        }));
        return surface;
    });
    return [healthNotice({ api, activity, scope, navigate, notify, onReviewMissing: reviewMissing }), ...surfaces];
}
