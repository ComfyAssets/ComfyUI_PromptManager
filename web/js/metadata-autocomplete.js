import {createScope} from './shared/toolkit/core.js';
import {createSuggestionView} from './shared/toolkit/suggestions.js';

export function attachMetadataAutocomplete(input, {scope: owner, api, settings, multiple = false, available = () => true}) {
    const scope = createScope();
    owner.cleanup(() => scope.dispose());
    const view = createSuggestionView(scope, settings, {label: multiple ? 'Saved tags' : 'Saved categories'});
    const attributes = ['role','aria-autocomplete','aria-controls','aria-expanded','aria-activedescendant','autocomplete'];
    const original = new Map(attributes.map(name => [name,input.getAttribute(name)]));
    input.setAttribute('role','combobox');
    input.setAttribute('aria-autocomplete','list');
    input.setAttribute('aria-controls',view.element.id);
    input.setAttribute('aria-expanded','false');
    input.autocomplete = 'off';
    let timer, request, sequence = 0, items = [], selected = -1;
    function close() {
        clearTimeout(timer); request?.abort(); sequence++;
        items = []; selected = -1; view.hide();
        input.setAttribute('aria-expanded','false');
        input.removeAttribute('aria-activedescendant');
    }
    scope.cleanup(() => {
        close();
        original.forEach((value,name) => value === null ? input.removeAttribute(name) : input.setAttribute(name,value));
    });
    function choose(index) {
        selected = index;
        input.setAttribute('aria-activedescendant',view.select(index));
    }
    function insert(item) {
        if (!available()) return close();
        const parts = input.value.split(',');
        parts.pop();
        input.value = multiple ? [...parts.map(part => part.trim()).filter(Boolean),item.name].join(', ') + ', ' : item.name;
        input.dispatchEvent(new Event('input',{bubbles:true}));
        close(); input.focus();
    }
    async function load() {
        const current = ++sequence;
        request = new AbortController();
        const parts = input.value.split(',');
        const query = (multiple ? parts.pop() : input.value).trim();
        const committed = new Set(parts.map(part => part.trim().toLowerCase()));
        const url = multiple ? `/prompt_manager/tags/suggest?${new URLSearchParams({q:query,limit:'20'})}` : '/prompt_manager/categories';
        try {
            const response = await fetch(api.fileURL(url),{signal:request.signal});
            if (scope.signal.aborted || current !== sequence) return;
            if (!response.ok) return close();
            const data = await response.json();
            if (scope.signal.aborted || current !== sequence || document.activeElement !== input || !available()) return;
            if (!data.success) return close();
            items = (multiple ? data.suggestions : data.categories).map(item => ({name:typeof item === 'string' ? item : item.name}))
                .filter(item => item.name.toLowerCase().startsWith(query.toLowerCase()) && (!multiple || !committed.has(item.name.toLowerCase()))).slice(0,15);
            if (!items.length) return close();
            selected = -1;
            view.render(items,choose,insert); view.position(input.getBoundingClientRect());
            input.setAttribute('aria-expanded','true');
        } catch {
            if (current === sequence && !scope.signal.aborted) close();
        }
    }
    function schedule() {
        close();
        if (available()) timer = setTimeout(load,150);
    }
    scope.listen(input,'input',schedule);
    scope.listen(input,'focus',schedule);
    scope.listen(input,'blur',close);
    scope.listen(input,'keydown',event => {
        if (event.isComposing) return;
        if (event.key === 'Escape' || event.key === 'Tab') {close();return;}
        if (!items.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); event.stopPropagation();
            choose(selected < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1) : (selected + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length);
        } else if (event.key === 'Enter' && selected >= 0) {
            event.preventDefault(); event.stopPropagation(); insert(items[selected]);
        }
    });
    scope.listen(window,'resize',close);
    document.addEventListener('scroll',event => {
        if (!view.element.hidden && !view.element.contains(event.target)) close();
    },{capture:true,signal:scope.signal});
    return {close};
}
