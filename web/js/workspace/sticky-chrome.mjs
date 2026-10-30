/**
 * The chrome that stays with you as a page scrolls (#531, beyond the concept; TESTING.md D186 and D187).
 *
 * - `keepingPlace`: repaint a sticky selection bar without moving the cards under the pointer.
 * - `publishBarHeight`: tell the root how much of the viewport's top a shown bar takes, for the Images panel's sticky
 *   offset and for `scroll-margin` (a focused card is not left under the bar).
 * - `mountBackToTop`: the shell's chip, which rests above the toast's spot and rises only for a taller toast.
 */

/** How far off the top of the page the chip appears, in CSS pixels. */
export const BACK_TO_TOP_AFTER = 200;
/** The gap between the stuck bar and whatever it clears (its `inset-block-start` above, and as much below). */
const BAR_GAP = 16;
const TOAST_GAP = 8;

/**
 * Run `paint`, which may change the height of `element`. While the element's place in the page's flow is above the
 * viewport (the bar is stuck), scroll by the difference, so the content below stays where it was. On screen, the bar
 * pushes the content as the concept's does. Native scroll anchoring would add its own correction in some browsers, so
 * it is off until the next frame. Returns the distance scrolled.
 */
export function keepingPlace(element, paint) {
    const next = element.nextElementSibling;
    if (!next || !element.isConnected) { paint(); return 0; }
    const before = next.getBoundingClientRect().top;
    const root = document.documentElement;
    const anchoring = root.style.overflowAnchor;
    root.style.overflowAnchor = 'none';
    paint();
    const shift = next.getBoundingClientRect().top - before;
    requestAnimationFrame(() => { root.style.overflowAnchor = anchoring; });
    if (!shift || before >= Math.max(0, element.getBoundingClientRect().bottom)) return 0;
    const from = window.scrollY;
    window.scrollBy({ top: shift, behavior: 'instant' });
    return window.scrollY - from;
}

/** Publish the height of the bar inside `element` as `--selection-bar-block` while it shows; cleared with `scope`. */
export function publishBarHeight(scope, element) {
    const root = document.documentElement;
    const clear = () => root.style.removeProperty('--selection-bar-block');
    const update = () => {
        const height = element.getBoundingClientRect().height;
        if (height > 0) root.style.setProperty('--selection-bar-block', `${height + BAR_GAP}px`);
        else clear();
    };
    const observer = new ResizeObserver(update);
    observer.observe(element);
    scope.cleanup(() => { observer.disconnect(); clear(); });
}

/** Go to the top of the page at once: the pages' own height corrections would cancel a smooth scroll part way. */
export function scrollToPageTop() {
    window.scrollTo({ top: 0, behavior: 'instant' });
}

/** One chip for the whole shell: shown off the top of any page; pressing it goes to the top at once. */
export function mountBackToTop({ scope, toast }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'back-to-top';
    button.hidden = true;
    const arrow = document.createElement('span');
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '↑';
    button.append(arrow, ' Back to top');
    toast.after(button);

    // Scroll events already come once a frame, and this only reads `scrollY`.
    const update = () => { button.hidden = window.scrollY <= BACK_TO_TOP_AFTER; };
    scope.listen(window, 'scroll', update, { passive: true });
    scope.listen(button, 'click', () => {
        // Never animated, so reduced motion holds. The chip hides under the focus, so focus moves to the page.
        scrollToPageTop();
        document.getElementById('workspace-main')?.focus({ preventScroll: true });
        update();
    });

    const root = document.documentElement;
    const followToast = () => {
        const height = toast.getBoundingClientRect().height;
        if (height > 0) root.style.setProperty('--toast-block', `${height + TOAST_GAP}px`);
        else root.style.removeProperty('--toast-block');
    };
    const toastObserver = new MutationObserver(followToast);
    toastObserver.observe(toast, { childList: true, characterData: true, subtree: true });
    scope.listen(window, 'resize', followToast);
    scope.cleanup(() => { toastObserver.disconnect(); root.style.removeProperty('--toast-block'); button.remove(); });
    update();
    return button;
}
