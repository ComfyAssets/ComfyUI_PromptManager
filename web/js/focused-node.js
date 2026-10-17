import {createScope, createButton, createField, mountTheme} from './shared/toolkit/core.js';
import {createPromptLibrary} from './prompt-library.js';
import {attachMetadataAutocomplete} from './metadata-autocomplete.js';

const names = ['text', 'category', 'tags', 'search_text', 'prepend_text', 'append_text'];
let nextEditorId = 0;

function loadStyles() {
    const href = new URL('./prompt-editor.css', import.meta.url).href;
    if ([...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === href)) return;
    const link = document.createElement('link'); Object.assign(link, {rel:'stylesheet', href}); document.head.append(link);
}

export function installFocusedNode(nodeType, app, api, parseQuery) {
    const prototype = nodeType.prototype;
    const created = prototype.onNodeCreated;
    const removed = prototype.onRemoved;
    const configure = prototype.configure;
    const serialize = prototype.serialize;
    const connections = prototype.onConnectionsChange;
    prototype.onNodeCreated = function (...args) {
        created?.apply(this, args);
        const scope = createScope(); this._focusedScope = scope;
        const editorId = `pm-focused-${++nextEditorId}`;
        this.properties ||= {};
        const widgets = new Map();
        for (const name of names) {
            let widget = this.widgets?.find(item => item.name === name);
            if (!widget) widget = this.addWidget('text', name, '', () => {}, {});
            widget.hidden = true; widget.type = 'hidden'; widget.computeSize = () => [0, -4];
            widgets.set(name, widget);
        }
        const element = document.createElement('div'); element.className = 'ca-tools pm-focused';
        element.setAttribute('aria-label','Focused prompt editor');
        loadStyles(); mountTheme(element, app.ui?.settings, scope);
        const toolbar = document.createElement('div'); toolbar.className = 'pm-editor-row pm-editor-toolbar';
        const title = document.createElement('strong'); title.textContent = 'Prompt Manager';
        const browse = createButton('Browse library','ca-primary'); browse.setAttribute('aria-expanded','false');
        const web = createButton('Web UI ↗'); toolbar.append(title, browse, web);
        let webDisplayMode = 'popup';
        this._focusedSettingsReady = fetch(api.fileURL('/prompt_manager/settings'),{signal:scope.signal})
            .then(response => response.ok ? response.json() : null)
            .then(data => {
                const mode = data?.settings?.webui_display_mode;
                if (!scope.signal.aborted && data?.success && ['popup','newtab'].includes(mode)) webDisplayMode = mode;
            }).catch(() => {});
        const textWidget = widgets.get('text');
        const nativeInput = textWidget.inputEl;
        const inputDescriptor = Object.getOwnPropertyDescriptor(textWidget,'inputEl');
        const main = document.createElement('textarea'); main.rows = 6; main.spellcheck = nativeInput?.spellcheck ?? false;
        // ComfyUI owns widget.element; inputEl exposes our visible editor to extensions.
        Object.defineProperty(textWidget,'inputEl',{configurable:true,writable:true,value:main});
        scope.cleanup(() => {
            if (inputDescriptor) Object.defineProperty(textWidget,'inputEl',inputDescriptor);
            else delete textWidget.inputEl;
        });
        const mainField = createField('Main prompt', main, `${editorId}-text`);
        const connected = document.createElement('p'); connected.className = 'pm-editor-status'; connected.textContent = 'Text is supplied by an upstream connection.';
        const controls = new Map([['text', main]]);
        const details = new Map();
        const completions = new Map();
        element.append(toolbar, mainField, connected);
        function disclosure(key, label) {
            const section = document.createElement('details'); section.className = 'pm-editor-details';
            const summary = document.createElement('summary'); summary.textContent = label; section.append(summary); element.append(section); details.set(key,section);
            scope.listen(section, 'toggle', () => {
                if (!section.open) completions.forEach(completion => completion.close());
                if (!scope.signal.aborted) thisNode.properties.focusedUI = snapshot();
            });
            return section;
        }
        const thisNode = this;
        const composition = disclosure('composition','Composition · prepend / append');
        const metadata = disclosure('metadata','Metadata · category / tags');
        for (const [section, fields] of [[composition,[['prepend_text','Prepend'],['append_text','Append']]],[metadata,[['category','Category'],['tags','Tags']]]]) {
            const grid = document.createElement('div'); grid.className = 'pm-editor-fields';
            for (const [name,label] of fields) {
                const input = document.createElement('input'); input.type = 'text'; input.setAttribute('aria-label',label); controls.set(name,input);
                const field = createField(label,input,`${editorId}-${name}`);
                const note = document.createElement('span'); note.className = 'pm-editor-connection'; note.textContent = 'Supplied by an upstream connection.'; note.hidden = true;
                field.append(note); input._connectionNote = note; grid.append(field);
                if (name === 'category' || name === 'tags') completions.set(name, attachMetadataAutocomplete(input, {
                    scope,api,settings:app.ui?.settings,multiple:name === 'tags',available:() => !isConnected(name) && input.getClientRects().length > 0,
                }));
            }
            section.append(grid);
        }
        const composed = disclosure('preview','Composed text preview');
        const preview = document.createElement('p'); preview.className = 'pm-editor-preview'; preview.dataset.composedPreview = ''; composed.append(preview);
        const footer = document.createElement('p'); footer.className = 'pm-editor-status'; footer.setAttribute('role','status'); footer.textContent = 'Main prompt is saved when this node runs.';
        function isConnected(name) { return thisNode.inputs?.some(input => input.name === name && input.link != null); }
        function value(name) { return String(widgets.get(name)?.value ?? ''); }
        function write(name, text) {
            if (isConnected(name)) return;
            const widget = widgets.get(name); widget.value = text;
            if (widget.inputEl) widget.inputEl.value = text;
            const index = thisNode.widgets.indexOf(widget);
            if (thisNode.widgets_values) thisNode.widgets_values[index] = text;
            widget.callback?.(text, app.canvas, thisNode);
            thisNode.setDirtyCanvas(true,true); app.graph?.setDirtyCanvas(true,true);
        }
        function render() {
            mainField.hidden = Boolean(isConnected('text')); connected.hidden = !isConnected('text');
            controls.forEach((input,name) => {
                input.value = value(name);
                if (name !== 'text') {
                    if (isConnected(name)) completions.get(name)?.close();
                    input.hidden = Boolean(isConnected(name));
                    input._connectionNote.hidden = !isConnected(name);
                }
            });
            preview.textContent = ['prepend_text','text','append_text'].map(name => isConnected(name) ? `[${name} supplied by connection]` : name === 'text' ? value(name) : value(name).trim()).filter(Boolean).join(' ');
            library.refreshAvailability();
        }
        const library = createPromptLibrary({scope,api,parseQuery,isConnected:() => isConnected('text'), onQuery:text => write('search_text',text), onUse:(prompt,includeMetadata) => {
            if (isConnected('text')) return;
            write('text',prompt.text);
            if (includeMetadata) {
                write('category',prompt.category || '');
                write('tags',Array.isArray(prompt.tags) ? prompt.tags.join(', ') : prompt.tags || '');
            }
            render(); footer.setAttribute('role','status'); footer.textContent = 'Loaded prompt. Saved when this node runs.'; main.focus();
        }});
        element.append(library.element,footer);
        function snapshot() {
            return {libraryOpen:!library.element.hidden,...Object.fromEntries([...details].map(([name,section]) => [name,section.open]))};
        }
        function restore() {
            const state = thisNode.properties.focusedUI || {};
            details.forEach((section,name) => {section.open = Boolean(state[name]);});
            library.element.hidden = !state.libraryOpen; browse.textContent = state.libraryOpen ? 'Hide library' : 'Browse library'; browse.setAttribute('aria-expanded', String(Boolean(state.libraryOpen)));
            library.setQuery(value('search_text')); render();
        }
        controls.forEach((input,name) => scope.listen(input,'input',() => {write(name,input.value);render();footer.setAttribute('role','status');footer.textContent='Main prompt is saved when this node runs.';}));
        scope.listen(browse,'click',() => {
            library.element.hidden = !library.element.hidden;
            browse.textContent = library.element.hidden ? 'Browse library' : 'Hide library'; browse.setAttribute('aria-expanded',String(!library.element.hidden));
            this.properties.focusedUI = snapshot(); if (!library.element.hidden) library.focus();
        });
        scope.listen(web,'click',() => {
            const url = api.fileURL('/prompt_manager/web');
            const opened = webDisplayMode === 'newtab' ? window.open(url,'_blank') : window.open(url,'_blank','width=1200,height=800,scrollbars=yes,resizable=yes');
            footer.setAttribute('role',opened ? 'status' : 'alert');
            footer.textContent = opened ? 'Web UI opened.' : 'Allow popups to open Web UI, then try again.';
        });
        this._focusedView = {element,render,restore,snapshot};
        this.addDOMWidget('prompt_manager_focused_ui','div',element,{serialize:false, hideOnZoom:false, getMinHeight:() => 360});
        this.size = [550,650]; this.setSize(this.size); restore();
    };
    prototype.configure = function (...args) { const result = configure?.apply(this,args); this._focusedView?.restore(); return result; };
    prototype.serialize = function (...args) {
        if (this._focusedView) this.properties.focusedUI = this._focusedView.snapshot();
        return serialize?.apply(this,args);
    };
    prototype.onConnectionsChange = function (...args) { const result = connections?.apply(this,args); this._focusedView?.render(); return result; };
    prototype.onRemoved = function (...args) { this._focusedScope?.dispose(); return removed?.apply(this,args); };
}
