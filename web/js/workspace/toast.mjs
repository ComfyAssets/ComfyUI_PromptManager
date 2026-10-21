/** The shell's shared toast (`#workspace-toast`, role="status"), used by every workspace page. */
export const TOAST_MS = 6500;
/** A toast with a link stays longer, so a keyboard user can reach it (D57, WCAG 2.2.1). */
export const LINK_TOAST_MS = 20000;
let toastTimer;
// Stops waiting for the pointer or focus to leave a toast that a newer one replaced.
let releaseWait = null;

/** Whether the pointer or keyboard focus rests on the toast. */
const held = toast => typeof toast.matches === 'function' && toast.matches(':hover, :focus-within');

/** Clear the toast when its time is up, unless the pointer or focus is on it: then wait until they leave. */
function scheduleClear(toast, delay) {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        if (held(toast)) {
            const release = () => {
                toast.removeEventListener('pointerleave', leave);
                toast.removeEventListener('focusout', later);
                releaseWait = null;
            };
            const leave = () => {
                if (held(toast)) return;
                release();
                scheduleClear(toast, TOAST_MS);
            };
            // focusout fires before the next element takes focus, so it checks once focus has moved.
            const later = () => setTimeout(leave);
            toast.addEventListener('pointerleave', leave);
            toast.addEventListener('focusout', later);
            releaseWait = release;
            return;
        }
        toast.textContent = '';
    }, delay);
}

/**
 * Show one outcome in the shared workspace toast; it clears itself like the concept's. A message given as parts
 * (`['Another job is running: ', {text, href}, '.']`) renders each `{text, href}` as a link (D57); such a toast stays
 * for LINK_TOAST_MS and while the pointer or focus is on it.
 */
export function showWorkspaceToast(message) {
    const toast = document.getElementById('workspace-toast');
    if (!toast) return;
    releaseWait?.();
    if (Array.isArray(message)) {
        toast.replaceChildren(...message.map(part => {
            if (typeof part === 'string') return document.createTextNode(part);
            const link = document.createElement('a');
            link.href = part.href;
            link.textContent = part.text;
            return link;
        }));
    } else {
        toast.textContent = message;
    }
    const linked = Array.isArray(message) && message.some(part => typeof part !== 'string');
    scheduleClear(toast, linked ? LINK_TOAST_MS : TOAST_MS);
}
