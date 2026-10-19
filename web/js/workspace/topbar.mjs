// The concept topbar's "ComfyUI ↗" link (D6 of #249).

const PROMPT_MANAGER = /prompt_manager\/$/;

/** The ComfyUI canvas root under the page's mount: the shared resolver's Prompt Manager root minus its own segment. */
export function canvasRoot(resolve) {
    const root = resolve('/prompt_manager/');
    if (typeof root !== 'string' || !root.startsWith('/') || root.startsWith('//') || !PROMPT_MANAGER.test(root)) {
        throw new TypeError('Cannot resolve the ComfyUI canvas root');
    }
    return root.replace(PROMPT_MANAGER, '');
}

const PAGE_MOUNT = /^(.*\/)prompt_manager(?:\/|$)/;

/**
 * The canvas link's href. A resolver failure must never stop the shell from mounting, so it falls back to the
 * ComfyUI root read from the page's own path (the #164 proxy mount), and to the origin root when even that is not a
 * same-origin root-relative path.
 */
export function canvasHref(resolve, pathname) {
    try {
        return canvasRoot(resolve);
    } catch (error) {
        console.warn('Prompt Manager: the ComfyUI link falls back to the page mount.', error);
        const mount = typeof pathname === 'string' ? pathname.match(PAGE_MOUNT)?.[1] : undefined;
        return mount && !mount.startsWith('//') ? mount : '/';
    }
}

const plainClick = event => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
const isPair = roles => roles !== null && typeof roles === 'object' &&
    typeof roles.positive === 'string' && typeof roles.negative === 'string';

/**
 * Point the link at the canvas. A plain click while `getSelectedPair()` returns both resolved roles opens the
 * acknowledged pair send instead, as the concept does; otherwise the browser follows the link. Returns an unbind.
 */
export function bindCanvasLink(link, { getSelectedPair = () => null, sender, href }) {
    link.href = href;
    const onClick = event => {
        if (event.defaultPrevented || !plainClick(event) || !sender) return;
        const roles = getSelectedPair();
        if (!isPair(roles)) return;
        event.preventDefault();
        sender.open({ positive: roles.positive, negative: roles.negative }, link);
    };
    link.addEventListener('click', onClick);
    return () => link.removeEventListener('click', onClick);
}
