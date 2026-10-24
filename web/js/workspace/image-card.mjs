/**
 * The concept media card (docs/concepts/workspace/app.js `mediaCard` a396–399), shared by Images, every
 * Library-embedded gallery and the Needs review queue: `article.media-card` > `.media-art` (the `.image-open` button
 * holding the media, then `.image-overlays`) and `.media-info` (title, "W × H · EXT · Seed N", then
 * `.image-card-actions` with Info and Tag). #249 D7–D11, D19.
 */
import { createButton } from '../shared/toolkit/core.js';
import { cardTitle, cardSubtitle, imageKey, mediaRatio } from './images-state.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { createTagButton } from './image-tagging.mjs';
import { paintStar } from './images-favorites.mjs';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/** A video's own element (its first frame), for a video without a usable preview. */
function videoMedia(image) {
    const node = el('video');
    node.src = window.ApiClient.resolveUrl(image.url);
    node.muted = true; node.playsInline = true; node.preload = 'metadata';
    return node;
}

/**
 * The card's media: the thumbnail (a video's is its preview frame), else the image, else a video's own element.
 * #249: a video whose preview cannot be loaded swaps back to its own element.
 */
export function cardMedia(image, lazy = false) {
    const { resolveUrl } = window.ApiClient;
    if (image.is_video && !image.thumbnail_url) return videoMedia(image);
    const node = el('img');
    node.src = resolveUrl(image.thumbnail_url || image.url);
    if (image.is_video) node.addEventListener('error', () => { if (node.isConnected) node.replaceWith(videoMedia(image)); }, { once: true });
    // The concept card image: alt is the card title, with the listing's intrinsic size when known.
    node.alt = cardTitle(image); node.decoding = 'async'; if (lazy) node.loading = 'lazy';
    if ([image.width, image.height].every(value => Number.isFinite(value) && value > 0)) {
        node.width = image.width; node.height = image.height;
    }
    return node;
}

/**
 * One card for `image`. `checked` (a boolean) adds the D8 selection checkbox, `favoriteBusy` paints a pending star
 * (D7, shown wherever the image reports `favorite`), `media: false` leaves the open button empty for a file that
 * cannot be served, and `showInfo: false` hides the title and file line (D97).
 */
export function createImageCard(image, { checked = null, favoriteBusy = false, media = true, showInfo = true } = {}) {
    const card = el('article', undefined, 'media-card');
    card.dataset.imageKey = imageKey(image); card.setAttribute('role', 'listitem');
    card.style.setProperty('--media-ratio', mediaRatio(image));
    const art = el('div', undefined, 'media-art');
    const open = createButton('', 'image-open');
    open.setAttribute('aria-label', `Open ${image.filename}`);
    if (media) open.append(cardMedia(image, true));
    // D8: the selection checkbox (Images only: Library-embedded media has no selection bar), then D7: the star,
    // wherever the listing reports favorites (Images and Library-embedded media).
    const overlays = el('div', undefined, 'image-overlays');
    if (typeof checked === 'boolean') {
        const label = el('label'), box = el('input');
        box.type = 'checkbox'; box.checked = checked;
        box.setAttribute('aria-label', `Select ${image.filename}`);
        label.append(box);
        overlays.append(label);
    }
    if (typeof image.favorite === 'boolean') {
        const star = createButton('', 'star-button');
        star.setAttribute('aria-label', `Favorite ${image.filename}`);
        paintStar(star, image.favorite, favoriteBusy);
        overlays.append(star);
    }
    art.append(open, overlays);
    const info = el('div', undefined, 'media-info');
    const text = el('div');
    const title = el('h3', cardTitle(image)); if (image.path) title.title = image.path;
    text.append(title, el('p', cardSubtitle(image)));
    // D97: `showInfo` false (System › Appearance) hides the title and file line; Info and Tag stay.
    if (!showInfo) text.hidden = true;
    const actions = el('div', undefined, 'image-card-actions');
    const inspectButton = createButton('', 'icon-btn');
    inspectButton.append(createWorkspaceIcon('info'));
    inspectButton.setAttribute('aria-label', `Inspect ${image.filename}`);
    actions.append(inspectButton, createTagButton(image));
    info.append(text, actions);
    card.append(art, info);
    return card;
}
