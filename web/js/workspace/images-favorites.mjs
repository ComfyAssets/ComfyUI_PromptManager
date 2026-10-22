/**
 * The media card's star (#249 D7 = A: the concept's `actions.favorite` against the favorites store), for the Images
 * page and every Library-embedded gallery (PR E). Its toasts are the concept's without the demo's "in this tab"; the
 * concept has no failure copy, so a failure follows the workspace "could not be …" pattern.
 *
 * One favorite request per file at a time (TESTING.md): the requests outlive the page that sent them, so the keys in
 * flight and the pages that receive each answer live here. Extracted from images.mjs unchanged.
 */
import { imageKey } from './images-state.mjs';
import { showWorkspaceToast } from './toast.mjs';

const FAVORITE_URL = '/prompt_manager/images/favorite';
const FAVORITE_DONE = Object.freeze({true:'Added to favorites.', false:'Removed from favorites.'});
const FAVORITE_FAILED = Object.freeze({true:'Favorite could not be added. Try again.', false:'Favorite could not be removed. Try again.'});

const favoritePending = new Set();
let pageTarget = null;
// Library-embedded galleries showing media cards; keyed like the Images page, so each repaints its own copy.
const embeddedTargets = new Set();
let visitTarget = () => {};
// Told after every confirmed favorite change, wherever it was made (the shell's Favorites count, #249 D51).
const favoriteListeners = new Set();

/** Run `listener()` after each confirmed favorite change; returns the unsubscribe. */
export function onFavoriteChange(listener) {
    favoriteListeners.add(listener);
    return () => favoriteListeners.delete(listener);
}
/** Whether the favorite request of the file `key` is still in flight. */
export function favoriteBusy(key) {
    return favoritePending.has(key);
}
/** `target(key, favorite)` patches the saved Images visit while no Images page is mounted. */
export function setFavoriteVisitTarget(target) {
    visitTarget = target;
}
/** Receive favorite states with `setFavorite(key, favorite)` until `scope` ends: the Images page or an embedded gallery. */
export function receiveFavorites({scope, embedded, setFavorite}) {
    if (!embedded) {
        pageTarget = setFavorite;
        scope.cleanup(() => { if (pageTarget === setFavorite) pageTarget = null; });
    } else {
        embeddedTargets.add(setFavorite);
        scope.cleanup(() => embeddedTargets.delete(setFavorite));
    }
}
/** Give a favorite state to the mounted Images page, else the saved visit, and to every embedded gallery. */
function deliver(key, favorite) {
    if (pageTarget) pageTarget(key, favorite);
    else visitTarget(key, favorite);
    for (const target of embeddedTargets) target(key, favorite);
}

/** A star whose request is still in flight is marked busy: pressing it again does nothing until the answer. */
export function paintStar(button, favorite, pending) {
    button.textContent = favorite ? '★' : '☆';
    button.setAttribute('aria-pressed', String(favorite));
    if (pending) button.setAttribute('aria-disabled', 'true'); else button.removeAttribute('aria-disabled');
}

/** Optimistic toggle: the star changes at once and returns to its previous state if the store refuses. */
export async function toggleFavorite(api, image) {
    const key = imageKey(image);
    if (favoritePending.has(key)) return;
    const favorite = !image.favorite;
    favoritePending.add(key);
    deliver(key, favorite);
    const body = {root_index: image.root_index, path: image.path};
    let saved = false;
    try {
        await (favorite ? api.put(FAVORITE_URL, body) : api.del(FAVORITE_URL, {body}));
        saved = true;
    } catch {
        // Announced below; the star returns to its previous state.
    }
    favoritePending.delete(key);
    deliver(key, saved ? favorite : !favorite);
    showWorkspaceToast(saved ? FAVORITE_DONE[favorite] : FAVORITE_FAILED[favorite]);
    // On Favorites an unstarred card stays (with ☆) until the next load, so the star can be undone in place (D51).
    if (saved) for (const listener of favoriteListeners) listener();
}
