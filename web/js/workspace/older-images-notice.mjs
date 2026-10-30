/**
 * The one-time upgrade notice "Bring older images into your prompt history" (#527, D166).
 *
 * Its dismissal is the legacy admin page's own `pm_update_notice_dismissed`, so dismissing it anywhere dismisses it
 * everywhere in this browser. The shell shows it above every page; System > Sources keeps the concept's copy in the
 * page, so the shell hides on that section and the two never show together. Storage that cannot be written still
 * dismisses it for this page.
 */
import { createButton } from '../shared/toolkit/core.js';
import { openMetadataScan } from './activity-scan.mjs';
import { showWorkspaceToast } from './toast.mjs';

export const NOTICE_KEY = 'pm_update_notice_dismissed';
export const NOTICE_VERSION = '3.2.0';
export const SOURCES_UNREADABLE = 'Could not read the image sources. Try again.';

const SETTINGS_URL = '/prompt_manager/settings';
const defaultStorage = () => window.localStorage;
const changes = new EventTarget();
// The storages this page dismissed the notice in without being able to write it.
const dismissedHere = new WeakSet();

/** Whether this browser dismissed the 3.2.0 notice; storage that cannot be read shows it. */
export function noticeDismissed(getStorage = defaultStorage) {
    if (dismissedHere.has(getStorage)) return true;
    try {
        return getStorage().getItem(NOTICE_KEY) === NOTICE_VERSION;
    } catch {
        return false;
    }
}

/** Dismiss the notice everywhere on this page. Returns whether it was saved for the next visit. */
export function dismissNotice(getStorage = defaultStorage) {
    let saved = true;
    try {
        getStorage().setItem(NOTICE_KEY, NOTICE_VERSION);
    } catch {
        dismissedHere.add(getStorage);
        saved = false;
    }
    changes.dispatchEvent(new Event('dismissed'));
    return saved;
}

/** Calls `onDismissed` when the notice is dismissed on this page or in another tab, until `scope` ends. */
export function watchNoticeDismissal({ scope, storage = defaultStorage, onDismissed }) {
    changes.addEventListener('dismissed', onDismissed);
    scope.cleanup(() => changes.removeEventListener('dismissed', onDismissed));
    scope.listen(window, 'storage', event => {
        if (event.key === NOTICE_KEY && noticeDismissed(storage)) onDismissed();
    });
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** The concept's `.notice` with Later and Review scan. */
export function noticeNode({ scope, onLater, onReview }) {
    const notice = el('div', undefined, 'notice');
    const text = el('div');
    text.append(el('h2', 'Bring older images into your prompt history'),
        el('p', 'The schema upgrades automatically. An optional metadata scan links existing positives and negatives without regenerating all thumbnails.'));
    const actions = el('div', undefined, 'row-actions');
    const later = createButton('Later');
    const review = createButton('Review scan', 'ca-primary');
    scope.listen(later, 'click', onLater);
    scope.listen(review, 'click', () => onReview(review));
    actions.append(later, review);
    notice.append(text, actions);
    return notice;
}

/** How many output folders a scan reads, from GET /settings; null when the answer does not say. */
export function sourceCount(settings) {
    const count = settings?.sources?.effective?.length;
    return Number.isSafeInteger(count) ? count : null;
}

/**
 * Mount the shell's notice in `container`, hidden until the first `showFor(route)`. The pressed button leaves with the
 * notice, so focus moves to `main`. If the sources cannot be read, Review scan says so and the notice stays.
 */
export function mountOlderImagesNotice(container, { api, activity, scope, main, storage = defaultStorage, notify = showWorkspaceToast }) {
    let onSources = true;
    let reading = false;
    const render = () => {
        const show = !onSources && !noticeDismissed(storage);
        container.hidden = !show;
        if (!show) notice.remove();
        else if (!notice.isConnected) container.append(notice);
    };
    const notice = noticeNode({ scope,
        onLater: () => {
            dismissNotice(storage);
            main.focus({ preventScroll: true });
        },
        onReview: async button => {
            if (reading) return;
            reading = true;
            button.setAttribute('aria-disabled', 'true');
            let sources = null;
            try {
                sources = sourceCount(await api.get(SETTINGS_URL, { signal: scope.signal, cache: 'no-store' }));
            } catch {
                if (scope.signal.aborted) return;
            }
            reading = false;
            button.removeAttribute('aria-disabled');
            if (sources === null) {
                notify(SOURCES_UNREADABLE);
                return;
            }
            dismissNotice(storage);
            main.focus({ preventScroll: true });
            openMetadataScan({ scope, store: activity, sources, trigger: main });
        } });
    watchNoticeDismissal({ scope, storage, onDismissed: render });
    return {
        showFor(route) {
            onSources = route.id === 'system' && route.section === 'sources';
            render();
        },
    };
}
