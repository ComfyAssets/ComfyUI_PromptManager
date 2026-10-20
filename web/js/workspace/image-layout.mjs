// The concept card (the concept stylesheet's `.media-card`): a 1px border around `aspect-ratio: 1.33` media and the measured
// `.media-info` row; a list row is the concept's 95px image plus that border.
const MEDIA_RATIO = 1.33, BORDER = 2, FOOTER_HEIGHT = 54, LIST_HEIGHT = 97;
// Bounded rendering keeps a card readable when zoom shrinks the grid below the concept's columns (D3, D31).
export const MINIMUM_CARD_WIDTH = 120;
const AUTOMATIC_CARD_WIDTH = 240;

/** The concept grid's own columns and gap, read from its computed style as the concept's mountMasonry does (D3). */
export function gridMetrics(grid) {
    const style = getComputedStyle(grid);
    const tracks = String(style.gridTemplateColumns || '').trim();
    const gap = Number.parseFloat(style.columnGap);
    return {columns: tracks && tracks !== 'none' ? tracks.split(/\s+/).length : 1, gap: Number.isFinite(gap) ? gap : 0};
}

export function layoutImages(images, width, mode, metrics = {}) {
    const {gap = 13, footerHeight = FOOTER_HEIGHT, listHeight = LIST_HEIGHT} = metrics;
    const fitting = Math.max(1, Math.floor((width + gap) / (MINIMUM_CARD_WIDTH + gap)));
    const asked = metrics.columns ?? Math.max(1, Math.floor((width + gap) / (AUTOMATIC_CARD_WIDTH + gap)));
    const columns = mode === 'list' ? 1 : Math.min(asked, fitting);
    const cardWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
    const bottoms = Array(columns).fill(0);
    let maxHeight = 0;
    const positions = images.map((image, index) => {
        const column = mode === 'masonry' ? bottoms.indexOf(Math.min(...bottoms)) : index % columns;
        const ratio = Number.isFinite(image.width) && Number.isFinite(image.height) && image.width > 0 && image.height > 0 ? image.width / image.height : 4 / 3;
        const mediaWidth = Math.max(1, cardWidth - BORDER);
        const mediaHeight = mode === 'masonry' ? mediaWidth / ratio : mediaWidth / MEDIA_RATIO;
        const height = mode === 'list' ? listHeight : (Number.isFinite(mediaHeight) ? mediaHeight : mediaWidth / MEDIA_RATIO) + footerHeight + BORDER;
        maxHeight = Math.max(maxHeight, height);
        const y = mode === 'grid' ? Math.floor(index / columns) * (height + gap) : bottoms[column];
        bottoms[column] = y + height + gap;
        return { x: column * (cardWidth + gap), y, width: cardWidth, height };
    });
    return { positions, columns, height: Math.max(0, ...bottoms) - (images.length ? gap : 0), maxHeight };
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
