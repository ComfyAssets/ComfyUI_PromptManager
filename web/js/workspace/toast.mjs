/** The shell's shared toast (`#workspace-toast`, role="status"), used by every workspace page. */
const TOAST_MS = 6500;
let toastTimer;

/**
 * Show one outcome in the shared workspace toast; it clears itself like the concept's. A message given as parts
 * (`['Another job is running: ', {text, href}, '.']`) renders each `{text, href}` as a link (D57).
 */
export function showWorkspaceToast(message) {
    const toast = document.getElementById('workspace-toast');
    if (!toast) return;
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
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {toast.textContent = '';}, TOAST_MS);
}
