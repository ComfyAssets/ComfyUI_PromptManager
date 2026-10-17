import {createScope, createButton, createSwitch} from './shared/toolkit/core.js';
let nextLibraryId = 0;

export function createPromptLibrary({scope, api, parseQuery, onUse, isConnected, onQuery}) {
    const element = document.createElement('section');
    element.className = 'pm-editor-library';
    element.setAttribute('aria-label', 'Prompt library');
    element.hidden = true;
    const heading = document.createElement('h3'); heading.textContent = 'Prompt library';
    const queryRow = document.createElement('div'); queryRow.className = 'pm-editor-query';
    const query = document.createElement('input'); query.type = 'search'; query.placeholder = 'Search text or tag:portrait'; query.setAttribute('aria-label', 'Search saved prompts');
    const search = createButton('Search', 'ca-primary');
    const recent = createButton('Recent');
    queryRow.append(query, search, recent);
    const status = document.createElement('p'); status.className = 'pm-editor-status'; status.setAttribute('role', 'status'); status.textContent = 'Search or browse recent prompts.';
    const results = document.createElement('div'); results.className = 'pm-editor-results';
    const preview = document.createElement('section'); preview.className = 'pm-editor-selection'; preview.hidden = true;
    const previewText = document.createElement('p'); previewText.className = 'pm-editor-preview';
    const actions = document.createElement('div'); actions.className = 'pm-editor-row';
    const includeMetadata = createSwitch('Load metadata', '', `pm-load-metadata-${++nextLibraryId}`);
    const use = createButton('Use prompt', 'ca-primary'); use.disabled = true;
    actions.append(includeMetadata, use); preview.append(previewText, actions);
    element.append(heading, queryRow, status, results, preview);
    let selected = null;
    let request = null;
    let sequence = 0;
    let rows = createScope();
    scope.cleanup(() => { sequence++; request?.abort(); rows.dispose(); });
    function refreshAvailability() { use.disabled = !selected || isConnected(); }
    async function load(recentOnly = false) {
        request?.abort(); request = new AbortController();
        const current = ++sequence;
        selected = null; preview.hidden = true; refreshAvailability();
        rows.dispose(); rows = createScope(); results.replaceChildren();
        const {text, tags} = parseQuery(query.value);
        status.classList.remove('pm-editor-error'); status.setAttribute('role', 'status');
        if (!recentOnly && !text && !tags.length) { status.textContent = 'Enter search text or tag:name.'; return; }
        const params = new URLSearchParams(recentOnly ? {limit:'20', sort:'last_used_desc'} : {limit:'50'});
        if (!recentOnly) {
            if (text) params.set('text', text);
            if (tags.length) params.set('tags', tags.join(','));
        }
        status.textContent = 'Loading prompts…';
        try {
            const response = await fetch(api.fileURL(`/prompt_manager/${recentOnly ? 'recent' : 'search'}?${params}`), {signal:request.signal});
            if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            const data = await response.json();
            if (scope.signal.aborted || current !== sequence) return;
            if (!data.success) throw new Error(data.error || 'Could not load prompts');
            const found = data.results || [];
            status.textContent = found.length ? `${found.length} ${found.length === 1 ? 'prompt' : 'prompts'}` : 'No matching prompts.';
            found.forEach(item => {
                const button = createButton('', 'pm-editor-result'); button.setAttribute('aria-pressed','false');
                const text = document.createElement('span'); text.textContent = item.text;
                const metadata = document.createElement('span'); metadata.className = 'pm-editor-result-meta';
                metadata.textContent = [item.category, Array.isArray(item.tags) ? item.tags.join(', ') : item.tags, item.rating ? `${item.rating}/5` : ''].filter(Boolean).join(' · ');
                button.append(text, metadata);
                rows.listen(button, 'click', () => {
                    selected = item; previewText.textContent = item.text; preview.hidden = false;
                    results.querySelectorAll('button').forEach(row => row.setAttribute('aria-pressed', String(row === button)));
                    status.textContent = 'Previewing selection. Editor unchanged.'; refreshAvailability();
                });
                results.append(button);
            });
        } catch (error) {
            if (scope.signal.aborted || current !== sequence || request.signal.aborted) return;
            status.classList.add('pm-editor-error'); status.setAttribute('role','alert'); status.textContent = `Could not load prompts: ${error.message}`;
        }
    }
    scope.listen(search, 'click', () => load());
    scope.listen(recent, 'click', () => load(true));
    scope.listen(query, 'input', () => onQuery(query.value));
    scope.listen(query, 'keydown', event => { if (event.key === 'Enter') { event.preventDefault(); load(); } });
    scope.listen(use, 'click', () => {
        if (!selected || isConnected()) return;
        onUse(selected, includeMetadata.querySelector('input').checked);
    });
    return {element, focus:() => query.focus(), refreshAvailability, setQuery(value) { query.value = value || ''; }};
}
