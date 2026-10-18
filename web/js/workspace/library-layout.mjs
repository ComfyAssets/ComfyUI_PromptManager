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
