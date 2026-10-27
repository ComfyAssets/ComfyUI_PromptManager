/**
 * File-manager selection on the Images page (#392, D135): pure helpers for the selection bar (images-selection.mjs).
 * Keys are image keys (`root_index:path`) in the listing's display order, so a range spans every loaded batch
 * whether or not its cards are mounted.
 */

/** The press that turns on touch selection mode, and how far it may drift before it counts as a scroll. */
export const LONG_PRESS_MS = 500;
export const LONG_PRESS_SLOP = 10;

/**
 * The keys from `from` to `to`, inclusive, in `keys` order (either direction). Without a listed `from` (no anchor
 * yet, or it left the listing) the range is `to` alone; an unlisted `to` gives none.
 */
export function rangeKeys(keys, from, to) {
    const end = keys.indexOf(to);
    if (end < 0) return [];
    const start = from === null || from === undefined ? -1 : keys.indexOf(from);
    if (start < 0) return [to];
    return keys.slice(Math.min(start, end), Math.max(start, end) + 1);
}

/** `selected` with `keys` added (`on`) or removed, frozen; the same set when nothing changes. */
export function applyKeys(selected, keys, on) {
    if (keys.every(key => selected.has(key) === on)) return selected;
    const next = new Set(selected);
    for (const key of keys) {
        if (on) next.add(key); else next.delete(key);
    }
    return Object.freeze(next);
}

/** Whether `keys` is non-empty and every one of them is in `selected`. */
export function everyChecked(selected, keys) {
    return keys.length > 0 && selected.size >= keys.length && keys.every(key => selected.has(key));
}

/** "Select all N matching" shows once every loaded image is checked and the listing holds more than is loaded. */
export function offersAllMatching(selected, keys, { total, hasMore }) {
    return Boolean(hasMore) && total > keys.length && everyChecked(selected, keys);
}

const grouped = count => count.toLocaleString('en-US');
export const matchingLabel = total => `All ${grouped(total)} matching selected`;
export const matchingOffer = total => `Select all ${grouped(total)} matching`;

/**
 * A touch long-press: `down` starts the timer, a `move` past the slop, `up` or `cancel` stops it, and
 * `onLongPress(target)` runs once it fires. `consumeClick()` answers whether the click the press ends in belongs to
 * the long press (so the page swallows it); it answers once. A new `down` forgets an unclaimed long press.
 */
export function createLongPress({ onLongPress, delay = LONG_PRESS_MS, slop = LONG_PRESS_SLOP,
    setTimer = setTimeout, clearTimer = clearTimeout }) {
    let timer = null, origin = null, fired = false;
    const stop = () => {
        if (timer !== null) clearTimer(timer);
        timer = null; origin = null;
    };
    return {
        down(x, y, target) {
            stop();
            fired = false;
            origin = { x, y };
            timer = setTimer(() => { timer = null; origin = null; fired = true; onLongPress(target); }, delay);
        },
        move(x, y) {
            if (origin && Math.hypot(x - origin.x, y - origin.y) > slop) stop();
        },
        up: stop,
        /** A press that ends without its click (a mouse press next, a scroll): its long press is forgotten too. */
        cancel() { stop(); fired = false; },
        /** Whether a touch press is held and has not yet fired (its context menu belongs to the long press). */
        pending: () => timer !== null,
        consumeClick() {
            const claimed = fired;
            fired = false;
            return claimed;
        },
    };
}
