export function workspaceImageMetrics(viewportWidth, density = 'comfortable', availableWidth = Infinity) {
    if (!['comfortable', 'large', 'compact'].includes(density)) throw new TypeError('Unknown thumbnail density');
    const columns = viewportWidth <= 700 || density === 'large' ? 2 :
        density === 'compact' ? (viewportWidth >= 1700 ? 5 : viewportWidth <= 1250 ? 3 : 4) :
            viewportWidth >= 1700 ? 4 : viewportWidth <= 1250 ? 2 : 3;
    const gap = 13, minimumCardWidth = 120;
    const fittingColumns = Math.max(1, Math.floor((availableWidth + gap) / (minimumCardWidth + gap)));
    return {columns: Math.min(columns, fittingColumns), gap, footerHeight:54, listHeight:97};
}

export function layoutImages(images, width, mode, minWidth = 240, metrics = {}) {
    const {gap = 13, footerHeight = 54, listHeight = 97} = metrics;
    const columns = mode === 'list' ? 1 : metrics.columns ?? Math.max(1, Math.floor((width + gap) / (minWidth + gap)));
    const cardWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
    const bottoms = Array(columns).fill(0);
    let maxHeight = 0;
    const positions = images.map((image, index) => {
        const column = mode === 'masonry' ? bottoms.indexOf(Math.min(...bottoms)) : index % columns;
        const ratio = Number.isFinite(image.width) && Number.isFinite(image.height) && image.width > 0 && image.height > 0 ? image.width / image.height : 4 / 3;
        const mediaWidth = Math.max(1, cardWidth - 2);
        const mediaHeight = mode === 'masonry' ? mediaWidth / ratio : mediaWidth * .75;
        const height = mode === 'list' ? listHeight : (Number.isFinite(mediaHeight) ? mediaHeight : mediaWidth * .75) + footerHeight + 2;
        maxHeight = Math.max(maxHeight, height);
        const y = mode === 'grid' ? Math.floor(index / columns) * (height + gap) : bottoms[column];
        bottoms[column] = y + height + gap;
        return { x: column * (cardWidth + gap), y, width: cardWidth, height };
    });
    return { positions, height: Math.max(0, ...bottoms) - (images.length ? gap : 0), maxHeight };
}

export function visibleImages(positions, top, viewportHeight, focusedIndex = -1, maxHeight = 1024) {
    const buffer = Math.max(600, viewportHeight);
    const firstTop = top - buffer - maxHeight;
    const lastTop = top + viewportHeight + buffer;
    let low = 0, high = positions.length;
    // Every layout emits monotonically increasing tops, including shortest-column masonry.
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (positions[middle].y < firstTop) low = middle + 1;
        else high = middle;
    }
    const indexes = [];
    for (let index = low; index < positions.length; index++) {
        const position = positions[index];
        if (position.y > lastTop) break;
        if (index === focusedIndex || position.y + position.height >= top - buffer) indexes.push(index);
    }
    if (focusedIndex >= 0 && focusedIndex < positions.length && !indexes.includes(focusedIndex)) {
        indexes.push(focusedIndex);
        indexes.sort((a, b) => a - b);
    }
    return indexes;
}
