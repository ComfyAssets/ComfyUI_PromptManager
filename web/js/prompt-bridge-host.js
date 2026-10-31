import { createBridgeHost } from './shared/toolkit/prompt-bridge.js';
import { createPromptAdapter } from './prompt-bridge-adapter.js';

const installations = new WeakMap();

export function installPromptBridge(app, { baseUrl, runtime = globalThis,
    hostFactory = createBridgeHost, adapterFactory = createPromptAdapter } = {}) {
    if (installations.has(app)) return installations.get(app);
    const url = new URL(baseUrl, runtime.location.href);
    if (url.origin !== runtime.location.origin || !url.pathname.endsWith('/prompt_manager/')) {
        throw new Error('Invalid PromptManager deployment origin or path');
    }
    const namespace = url.pathname.slice(0, -'prompt_manager/'.length);
    let host = null, disposed = false;
    function mount() {
        if (host || disposed) return;
        try {
            host = hostFactory({ namespace, origin: runtime.location.origin,
                // A getter: the Toolkit reads this on every discovery, and the tab title follows the open workflow.
                get label() { return runtime.document.title || 'ComfyUI'; }, adapter: adapterFactory(app) });
        } catch (error) {
            runtime.console.warn('[PromptManager] Prompt sending is unavailable:', error.message);
        }
    }
    function unmount() { host?.dispose(); host = null; }
    const controller = { dispose() {
        if (disposed) return;
        disposed = true;
        runtime.removeEventListener('pagehide', unmount);
        runtime.removeEventListener('pageshow', mount);
        unmount(); installations.delete(app);
    } };
    runtime.addEventListener('pagehide', unmount);
    runtime.addEventListener('pageshow', mount);
    installations.set(app, controller);
    mount();
    return controller;
}
