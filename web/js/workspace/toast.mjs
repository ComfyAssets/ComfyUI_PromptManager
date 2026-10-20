/** The shell's shared toast (`#workspace-toast`, role="status"), used by every workspace page. */
const TOAST_MS = 6500;
let toastTimer;

/** Show one outcome in the shared workspace toast; it clears itself like the concept's. */
export function showWorkspaceToast(message) {
    const toast = document.getElementById('workspace-toast');
    if (!toast) return;
    toast.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {toast.textContent = '';}, TOAST_MS);
}
