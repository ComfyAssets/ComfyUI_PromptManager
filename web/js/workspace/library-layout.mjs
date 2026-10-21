export function layoutLibraryRows(heights, columns, {gap = 18, estimate = 468} = {}) {
    if (!Number.isSafeInteger(columns) || columns < 1) throw new TypeError('Invalid Library columns');
    if (!Number.isFinite(gap) || gap < 0 || !Number.isFinite(estimate) || estimate <= 0) throw new TypeError('Invalid Library geometry');
    const rows = [];
    let top = 0;
    for (let first = 0; first < heights.length; first += columns) {
        const last = Math.min(first + columns, heights.length) - 1;
        let height = 0;
        for (let index = first; index <= last; index++) {
            const measured = heights[index];
            height = Math.max(height, Number.isFinite(measured) && measured > 0 ? measured : estimate);
        }
        rows.push({first, last, top, height});
        top += height + gap;
    }
    return {rows, height: rows.length ? top - gap : 0};
}

function lowerBound(rows, after) {
    let low = 0, high = rows.length;
    while (low < high) {
        const middle = low + Math.floor((high - low) / 2);
        if (after(rows[middle])) low = middle + 1;
        else high = middle;
    }
    return low;
}

export function visibleLibraryRows(rows, top, viewportHeight, focusedIndex = -1) {
    if (!rows.length) return [];
    top = Number.isFinite(top) ? Math.max(0, top) : 0;
    viewportHeight = Number.isFinite(viewportHeight) ? Math.max(0, viewportHeight) : 0;
    const first = Math.min(rows.length - 1, lowerBound(rows, row => row.top + row.height <= top));
    const end = Math.max(first + 1, lowerBound(rows, row => row.top < top + viewportHeight));
    const visible = new Set();
    for (let index = Math.max(0, first - 2); index < Math.min(rows.length, end + 2); index++) visible.add(index);
    if (Number.isSafeInteger(focusedIndex) && focusedIndex >= 0) {
        const focus = lowerBound(rows, row => row.last < focusedIndex);
        if (focus < rows.length && rows[focus].first <= focusedIndex) visible.add(focus);
    }
    return [...visible].sort((a, b) => a - b);
}

// #249 D23: the card footer's words. English month names as the concept's copy, independent of the browser locale.
const MONTHS = Object.freeze(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']);
const DAY_MS = 86400000;
/** The local calendar day as a UTC day number, so a DST change never makes a day 23 or 25 hours long. */
const localDay = date => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS;

/** "Used today", "Used yesterday", "Used N days ago" (under a week), "Used <Mon D>" (", <YYYY>" in another year), or "Not used yet". */
export function usedLabel(lastUsedAt, now = new Date()) {
    const used = typeof lastUsedAt === 'string' && lastUsedAt ? new Date(lastUsedAt) : null;
    if (!used || Number.isNaN(used.getTime())) return 'Not used yet';
    // A server clock ahead of this browser is not a future use.
    const days = Math.max(0, localDay(now) - localDay(used));
    if (days === 0) return 'Used today';
    if (days === 1) return 'Used yesterday';
    if (days < 7) return `Used ${days} days ago`;
    // A use in another calendar year names it ("Used Oct 21, 2025"), so it never reads as this year's date.
    const year = used.getFullYear() === now.getFullYear() ? '' : `, ${used.getFullYear()}`;
    return `Used ${MONTHS[used.getMonth()]} ${used.getDate()}${year}`;
}

/** The concept's stars (`★` × rating) with their visually hidden reading; an unrated parent shows neither. */
export function ratingLabel(rating) {
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) return {stars: '', hidden: ''};
    return {stars: '★'.repeat(rating), hidden: `Rated ${rating} out of 5`};
}
