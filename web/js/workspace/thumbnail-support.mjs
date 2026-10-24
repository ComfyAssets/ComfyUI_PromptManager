/**
 * Thumbnails this browser can show (#317). A thumbnail is only a grid or cover placeholder; every other view uses the
 * original.
 *
 * - `detectJxlSupport()` decodes a tiny embedded JPEG XL image once per page (`JXL_PROBE`) and remembers the answer
 *   (`jxlSupport()`: undefined until it answered, then true or false).
 * - `thumbnailSource(image, supported)`: the URL a card or the inspector cover loads: the listing's thumbnail, or the
 *   original when the thumbnail is JPEG XL and this browser cannot decode it. While the answer is unknown the
 *   thumbnail is tried; `fallBackToOriginal` then catches a failure.
 * - `fallBackToOriginal(img, url)`: a thumbnail `<img>` that fails to load shows the original instead (once).
 * - `showsThumbnail(image)`: false for a video whose thumbnail is missing or one this browser cannot show (its URL
 *   would be the video itself): the card keeps the video's own element or label, never a broken image.
 * - `replaceOnError(img, next)`: a video thumbnail `<img>` that fails to load is replaced by `next()` (once).
 */

// A 1×1 JPEG XL image (cjxl -d 1), decoded once to learn whether this browser shows JPEG XL.
export const JXL_PROBE = 'data:image/jxl;base64,/woAkAEAE4gCALQAtZ8gAAAVKqOMG7yc6/nyQ4fFtI3rDG21bWEJY7O9MEhIOGwY4s3xwRCTShIA';

let probe = null;
let answer;

/** Whether `url` names a JPEG XL file (its path, not its query or fragment). */
export function isJxlUrl(url) {
    if (typeof url !== 'string' || !url) return false;
    const path = url.split(/[?#]/, 1)[0];
    return path.toLowerCase().endsWith('.jxl');
}

/** The remembered answer: undefined until the probe decoded or failed. */
export const jxlSupport = () => answer;

/** Forget the answer (tests). */
export function resetJxlSupport() {
    probe = null;
    answer = undefined;
}

/** Decode the embedded JPEG XL once; resolves true when this browser shows it. */
export function detectJxlSupport({ createImage = () => new Image() } = {}) {
    probe ??= new Promise(resolve => {
        let image;
        try {
            image = createImage();
        } catch {
            resolve(false);
            return;
        }
        image.onload = () => resolve(true);
        image.onerror = () => resolve(false);
        image.src = JXL_PROBE;
    }).then(supported => {
        answer = supported;
        return supported;
    });
    return probe;
}

/** The URL a card or cover loads for `image` (`{url, thumbnail_url}`), given the JPEG XL answer. */
export function thumbnailSource(image, supported = answer) {
    const thumbnail = image?.thumbnail_url;
    if (typeof thumbnail !== 'string' || !thumbnail) return image?.url;
    if (supported === false && isJxlUrl(thumbnail)) return image.url;
    return thumbnail;
}

/** Whether a card shows `image`'s thumbnail (always for an image: its original at worst). */
export function showsThumbnail(image, supported = answer) {
    if (!image?.is_video) return true;
    const source = thumbnailSource(image, supported);
    return Boolean(source) && source !== image.url;
}

/** A video thumbnail `<img>` that fails to load is replaced by `next()`, once (`listen` as in fallBackToOriginal). */
export function replaceOnError(node, next, listen = null) {
    const swap = () => node.replaceWith(next());
    if (listen) listen(node, 'error', swap, { once: true });
    else node.addEventListener('error', swap, { once: true });
}

/**
 * A thumbnail `<img>` that fails to load shows `original` instead, once. `listen(target, type, handler, options)` is the
 * caller's scoped listener (a scope's `listen`); without one a plain once-listener is used.
 */
export function fallBackToOriginal(node, original, listen = null) {
    if (!original || node.src === original) return;
    const swap = () => { if (node.src !== original) node.src = original; };
    if (listen) listen(node, 'error', swap, { once: true });
    else node.addEventListener('error', swap, { once: true });
}
