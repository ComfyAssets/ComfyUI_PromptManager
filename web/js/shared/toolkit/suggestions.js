import { createScope, mountTheme } from './core.js';

let nextList = 0;

export function suggestionPosition(field, popup, viewport) {
    const left = Math.max(8, Math.min(field.left, viewport.width - popup.width - 8));
    const below = field.bottom + 4;
    const top = below + popup.height <= viewport.height - 8 ? below : field.top - popup.height - 4;
    return { left, top: Math.max(8, Math.min(top, viewport.height - popup.height - 8)) };
}

export function createSuggestionView(scope, settings, { label = 'Suggestions' } = {}) {
    const element = document.createElement('div');
    element.className = 'ca-tools ca-completions';
    do { element.id = `ca-completions-${++nextList}`; }
    while (document.getElementById(element.id));
    element.role = 'listbox';
    element.setAttribute('aria-label', label);
    element.hidden = true;
    document.body.append(element);
    mountTheme(element, settings, scope);
    let optionsScope;
    scope.cleanup(() => { optionsScope?.dispose(); element.remove(); });
    return {
        element,
        hide() {
            optionsScope?.dispose();
            element.hidden = true;
            element.replaceChildren();
        },
        render(suggestions, onSelect, onInsert) {
            optionsScope?.dispose();
            optionsScope = createScope();
            element.replaceChildren();
            suggestions.forEach((item, index) => {
                const option = document.createElement('div');
                option.className = 'ca-completion-option';
                option.id = `${element.id}-${index}`;
                option.role = 'option';
                option.setAttribute('aria-selected', 'false');
                const badge = document.createElement('span');
                badge.className = 'ca-completion-type';
                badge.textContent = item.type === 'embedding' ? '[EMB]' : item.type === 'lora' ? '[LORA]' : '[TAG]';
                const name = document.createElement('span');
                name.className = 'ca-completion-name';
                const path = item.type === 'embedding' ? item.fullPath : null;
                if (path?.includes('/')) {
                    const directory = document.createElement('span');
                    directory.className = 'ca-completion-directory';
                    directory.textContent = path.slice(0, path.lastIndexOf('/') + 1);
                    name.append(directory, document.createTextNode(path.slice(path.lastIndexOf('/') + 1)));
                } else name.textContent = item.name;
                if (item.type) option.append(badge);
                option.append(name);
                optionsScope.listen(option, 'pointerdown', event => event.preventDefault());
                optionsScope.listen(option, 'pointermove', () => onSelect(index));
                optionsScope.listen(option, 'click', () => onInsert(item));
                element.append(option);
            });
            element.hidden = false;
        },
        select(index) {
            const options = [...element.children];
            options.forEach((option, position) => option.setAttribute('aria-selected', String(position === index)));
            options[index]?.scrollIntoView({ block: 'nearest' });
            return options[index]?.id;
        },
        position(field) {
            const popup = element.getBoundingClientRect();
            const { left, top } = suggestionPosition(field, popup, { width: innerWidth, height: innerHeight });
            element.style.left = `${left}px`;
            element.style.top = `${top}px`;
        },
    };
}
