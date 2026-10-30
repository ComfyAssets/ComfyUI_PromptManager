import { createButton } from '../shared/toolkit/core.js';

/**
 * A "⋯" (More) menu button for a page heading (D163, #512; the concept has no menu, so it uses the concept's button
 * and surface styles). The button carries `aria-haspopup="menu"` and `aria-expanded`; Enter, Space or ArrowDown opens
 * the menu on its first item, ArrowUp on its last; ArrowUp/ArrowDown/Home/End move between items; Escape closes it and
 * returns focus to the button; Tab, an outside pointer press or choosing an item closes it. A held button
 * (`aria-disabled`) or a disabled one opens nothing. Listeners live on `scope`.
 *
 * `items`: `[{ label, danger, onSelect }]`. Returns `{ element, button, setDisabled(disabled) }`.
 */
let menus = 0;
export function createMoreMenu({ scope, label, items }) {
    const id = `more-menu-${++menus}`;
    const element = document.createElement('div');
    element.className = 'more-menu';
    const button = createButton('⋯');
    button.classList.add('more-menu-button');
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', id);
    const menu = document.createElement('div');
    menu.className = 'more-menu-list';
    menu.id = id;
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', label);
    menu.hidden = true;
    const entries = items.map(item => {
        const entry = createButton(item.label, item.danger ? 'danger' : '');
        entry.setAttribute('role', 'menuitem');
        entry.tabIndex = -1;
        scope.listen(entry, 'click', () => { close(false); item.onSelect(); });
        return entry;
    });
    menu.append(...entries);
    element.append(button, menu);

    const isOpen = () => !menu.hidden;
    const held = () => button.disabled || button.getAttribute('aria-disabled') === 'true';
    function open(index) {
        if (held()) return;
        menu.hidden = false;
        // Right-aligned under the button; where the heading wraps it to the left edge (390px), it opens rightwards.
        menu.style.left = menu.style.right = '';
        if (menu.getBoundingClientRect().left < 0) Object.assign(menu.style, { left: '0', right: 'auto' });
        button.setAttribute('aria-expanded', 'true');
        entries.at(index)?.focus();
    }
    function close(refocus) {
        if (!isOpen()) return;
        menu.hidden = true;
        button.setAttribute('aria-expanded', 'false');
        if (refocus) button.focus();
    }
    function move(step) {
        const at = entries.indexOf(document.activeElement);
        entries[(at + step + entries.length) % entries.length].focus();
    }
    scope.listen(button, 'click', () => (isOpen() ? close(true) : open(0)));
    scope.listen(button, 'keydown', event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        open(event.key === 'ArrowDown' ? 0 : -1);
    });
    scope.listen(menu, 'keydown', event => {
        const keys = { ArrowDown: () => move(1), ArrowUp: () => move(-1), Home: () => entries[0].focus(),
            End: () => entries.at(-1).focus(), Escape: () => close(true) };
        if (event.key === 'Tab') { close(false); return; }
        if (!keys[event.key]) return;
        event.preventDefault();
        keys[event.key]();
    });
    // Focus leaving the menu (a screen reader's cursor, a script) closes it too.
    scope.listen(element, 'focusout', event => { if (event.relatedTarget && !element.contains(event.relatedTarget)) close(false); });
    // An outside press closes it; capture, so a press that a page control stops still counts.
    const outside = event => { if (isOpen() && !element.contains(event.target)) close(false); };
    document.addEventListener('pointerdown', outside, true);
    scope.cleanup(() => document.removeEventListener('pointerdown', outside, true));
    return {
        element,
        button,
        setDisabled(disabled) { button.disabled = disabled; if (disabled) close(false); },
    };
}
