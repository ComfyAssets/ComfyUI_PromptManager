import {createScope, createButton, createSwitch, createField} from './shared/toolkit/core.js';
import {attachMetadataAutocomplete} from './metadata-autocomplete.js';
let nextLibraryId = 0;

export function createPromptLibrary({scope, api, settings, parseQuery, onUse, isConnected, onQuery, onFilters, mode = 'prompts'}) {
    const pairs = mode === 'pairs';
    const noun = pairs ? 'pair' : 'prompt';
    const element = document.createElement('section');
    element.className = 'pm-editor-library';
    element.setAttribute('aria-label', 'Prompt library');
    element.hidden = true;
    const heading = document.createElement('h3'); heading.textContent = 'Prompt library';
    const libraryId = `pm-library-${++nextLibraryId}`;
    const queryRow = document.createElement('div'); queryRow.className = 'pm-editor-query';
    const query = document.createElement('input'); query.type = 'search'; query.placeholder = 'Search text or tag:portrait'; query.setAttribute('aria-label', 'Search saved prompts');
    const search = createButton('Search', 'ca-primary');
    const recent = createButton('Recent');
    queryRow.append(query, search, recent);
    const filters = document.createElement('div'); filters.className = 'pm-editor-fields';
    const category = document.createElement('input'); category.type = 'text';
    const tags = document.createElement('input'); tags.type = 'text';
    filters.append(createField('Filter category',category,`${libraryId}-category`),createField('Filter tags',tags,`${libraryId}-tags`));
    const hint = document.createElement('p'); hint.className = 'pm-editor-status'; hint.textContent = `Search combines text, category and tags. Recent shows all recent ${noun}s.`;
    const completions = [category,tags].map((input,index) => attachMetadataAutocomplete(input, {
        scope,api,settings,multiple:index === 1,available:() => !element.hidden && input.getClientRects().length > 0,
    }));
    const status = document.createElement('p'); status.className = 'pm-editor-status'; status.setAttribute('role', 'status'); status.textContent = `Search or browse recent ${noun}s.`;
    const results = document.createElement('div'); results.className = 'pm-editor-results';
    const preview = document.createElement('section'); preview.className = 'pm-editor-selection'; preview.hidden = true;
    const previewText = document.createElement('p'); previewText.className = 'pm-editor-preview';
    const previewNegative = document.createElement('p'); previewNegative.className = 'pm-editor-preview';
    const actions = document.createElement('div'); actions.className = 'pm-editor-row';
    const includeMetadata = createSwitch('Load metadata', '', `${libraryId}-load-metadata`);
    const use = createButton(pairs ? 'Use pair' : 'Use prompt', 'ca-primary'); use.disabled = true;
    actions.append(includeMetadata, use); preview.append(previewText); if (pairs) preview.append(previewNegative); preview.append(actions);
    element.append(heading, queryRow, filters, hint, status, results, preview);
    let selected = null;
    let request = null;
    let sequence = 0;
    let rows = createScope();
    scope.cleanup(() => { sequence++; request?.abort(); rows.dispose(); });
    function refreshAvailability() { use.disabled = !selected || isConnected(); }
    function invalidate() {
        sequence++; request?.abort(); selected = null; preview.hidden = true; refreshAvailability();
        rows.dispose(); rows = createScope(); results.replaceChildren();
        status.classList.remove('pm-editor-error'); status.setAttribute('role','status'); status.textContent = 'Search to refresh results.';
    }
    async function load(recentOnly = false) {
        request?.abort(); request = new AbortController();
        const current = ++sequence;
        selected = null; preview.hidden = true; refreshAvailability();
        rows.dispose(); rows = createScope(); results.replaceChildren();
        const parsed = parseQuery(query.value);
        const text = parsed.text;
        const tagFilters = [...new Map([...parsed.tags,...tags.value.split(',')].map(tag => tag.trim()).filter(Boolean).map(tag => [tag.toLowerCase(),tag])).values()];
        const categoryFilter = category.value.trim();
        status.classList.remove('pm-editor-error'); status.setAttribute('role', 'status');
        if (!recentOnly && !text && !tagFilters.length && !categoryFilter) { status.textContent = 'Enter search text, a category or tags.'; return; }
        const params = new URLSearchParams(recentOnly ? {limit:'20', sort:'last_used_desc'} : {limit:'50'});
        if (!recentOnly) {
            if (text) params.set('text', text);
            if (tagFilters.length) { if (pairs) tagFilters.forEach(tag => params.append('tag',tag)); else params.set('tags', tagFilters.join(',')); }
            if (categoryFilter) params.set('category', categoryFilter);
        }
        status.textContent = `Loading ${noun}s…`;
        try {
            const response = await fetch(api.fileURL(`/prompt_manager/${pairs ? 'pairs' : recentOnly ? 'recent' : 'search'}?${params}`), {signal:request.signal});
            if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            const data = await response.json();
            if (scope.signal.aborted || current !== sequence) return;
            if (!data.success) throw new Error(data.error || `Could not load ${noun}s`);
            const found = (pairs ? data.pairs : data.results) || [];
            status.textContent = found.length ? `${found.length} ${noun}${found.length === 1 ? '' : 's'}` : `No matching ${noun}s.`;
            found.forEach(item => {
                const button = createButton('', 'pm-editor-result'); button.setAttribute('aria-pressed','false');
                const text = document.createElement('span'); text.textContent = pairs ? item.title || item.positive_text : item.text;
                const metadata = document.createElement('span'); metadata.className = 'pm-editor-result-meta';
                metadata.textContent = [item.category, Array.isArray(item.tags) ? item.tags.join(', ') : item.tags, item.rating ? `${item.rating}/5` : ''].filter(Boolean).join(' · ');
                button.append(text, metadata);
                rows.listen(button, 'click', () => {
                    selected = item; previewText.textContent = pairs ? `Positive: ${item.positive_text}` : item.text; if (pairs) previewNegative.textContent = `Negative: ${item.negative_text || '(empty)'}`; preview.hidden = false;
                    results.querySelectorAll('button').forEach(row => row.setAttribute('aria-pressed', String(row === button)));
                    status.textContent = 'Previewing selection. Editor unchanged.'; refreshAvailability();
                });
                results.append(button);
            });
        } catch (error) {
            if (scope.signal.aborted || current !== sequence || request.signal.aborted) return;
            status.classList.add('pm-editor-error'); status.setAttribute('role','alert'); status.textContent = `Could not load ${noun}s: ${error.message}`;
        }
    }
    scope.listen(search, 'click', () => load());
    scope.listen(recent, 'click', () => load(true));
    scope.listen(query, 'input', () => {invalidate();onQuery(query.value);});
    [query,category,tags].forEach(input => scope.listen(input, 'keydown', event => {
        if (event.key === 'Enter' && !event.defaultPrevented && !event.isComposing) { event.preventDefault(); load(); }
    }));
    [category,tags].forEach(input => scope.listen(input,'input',() => {
        invalidate(); status.textContent = 'Filters changed. Search to refresh results.'; onFilters();
    }));
    scope.listen(use, 'click', () => {
        if (!selected || isConnected()) return;
        onUse(selected, includeMetadata.querySelector('input').checked);
    });
    return {element, focus:() => query.focus(), refreshAvailability, setQuery(value) { query.value = value || ''; },
        filters:() => ({category:category.value,tags:tags.value}),
        restoreFilters(state = {}) { invalidate();category.value = state.category || '';tags.value = state.tags || '';completions.forEach(completion => completion.close()); },
        closeCompletions() { completions.forEach(completion => completion.close()); },
    };
}
