export function layoutImages(images, width, mode, minWidth = 240) {
    const gap = 16;
    const columns = mode === 'list' ? 1 : Math.max(1, Math.floor((width + gap) / (minWidth + gap)));
    const cardWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
    const bottoms = Array(columns).fill(0);
    let maxHeight = 0;
    const positions = images.map((image, index) => {
        const column = mode === 'masonry' ? bottoms.indexOf(Math.min(...bottoms)) : index % columns;
        const ratio = image.width > 0 && image.height > 0 ? image.width / image.height : 4 / 3;
        const height = mode === 'list' ? 106 :
            (mode === 'masonry' ? Math.max(130, Math.min(440, cardWidth / ratio)) : cardWidth * .75) + 72;
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
