const STORAGE_KEY = 'pm_workspace_theme';
// D4 (#249): the concept's Theme select. "system" follows the ComfyUI palette, then the OS.
const PREFERENCES = new Set(['dark', 'light', 'system']);
// The pre-#249 "Follow ComfyUI" value, read as System so a saved choice survives the rename.
const LEGACY = new Map([['follow', 'system']]);
const LIGHT_PALETTES = new Set(['light', 'solarized', 'github', 'arc']);
const DARK_PALETTES = new Set(['dark', 'obsidian', 'nord', 'dracula', 'gruvbox']);

/** Adapt the standalone page to ComfyUI without coupling the shared toolkit to HTTP. */
export function mountWorkspaceTheme({ root = document.body, scope, fetchImpl = globalThis.fetch, storage, win = window }) {
    const documentElement = root.ownerDocument.documentElement;
    const previousTheme = root.dataset.caTheme;
    const previousScheme = documentElement.style.colorScheme;
    const system = win.matchMedia('(prefers-color-scheme: dark)');
    let preference = 'system';
    let hostTheme = null;
    let request = null;
    let sequence = 0;
    try {
        storage ??= win.localStorage;
        const stored = storage?.getItem(STORAGE_KEY);
        const saved = LEGACY.get(stored) ?? stored;
        if (PREFERENCES.has(saved)) preference = saved;
    } catch { /* Storage can be disabled while theme controls remain usable. */ }

    function update() {
        if (scope.signal.aborted) return;
        const theme = preference === 'system' ? hostTheme || (system.matches ? 'dark' : 'light') : preference;
        root.dataset.caTheme = theme;
        documentElement.style.colorScheme = theme;
    }

    async function refreshHost() {
        if (scope.signal.aborted) return;
        request?.abort();
        const controller = new AbortController();
        request = controller;
        const current = ++sequence;
        let next = null;
        try {
            const prefix = win.location.pathname.match(/^(.*)\/prompt_manager(?:\/|$)/)?.[1] || '';
            const response = await fetchImpl(`${prefix}/api/settings/Comfy.ColorPalette`, { signal: controller.signal });
            if (response.ok) {
                const data = await response.json();
                const value = typeof data === 'string' ? data : data?.value;
                const name = typeof value === 'string' ? value.toLowerCase().trim() : '';
                next = LIGHT_PALETTES.has(name) ? 'light' : DARK_PALETTES.has(name) ? 'dark' : null;
            }
        } catch { /* A standalone or unavailable host follows the system preference. */ }
        if (scope.signal.aborted || current !== sequence) return;
        hostTheme = next;
        update();
    }

    scope.listen(system, 'change', update);
    scope.listen(win, 'focus', refreshHost);
    scope.cleanup(() => {
        sequence++;
        request?.abort();
        if (previousTheme === undefined) delete root.dataset.caTheme;
        else root.dataset.caTheme = previousTheme;
        documentElement.style.colorScheme = previousScheme;
    });
    update();
    const ready = refreshHost();
    return {
        ready,
        get preference() { return preference; },
        setPreference(value) {
            if (scope.signal.aborted) return;
            if (!PREFERENCES.has(value)) throw new TypeError('Unknown theme preference');
            preference = value;
            try { storage?.setItem(STORAGE_KEY, value); } catch { /* Keep the in-memory preference. */ }
            update();
        },
    };
}
