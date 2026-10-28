import {createScope, createButton, createField, mountTheme} from './shared/toolkit/core.js';
import {createPromptLibrary} from './prompt-library.js';
import {attachMetadataAutocomplete} from './metadata-autocomplete.js';
import {exposeTextSockets, linkNoteText, preferTextInputs, textInputAtPoint} from './text-links.js';
import {SAVE_WIDGET, normalizeSaveWidget, placeSaveWidgetLast, saveWidget, savesToLibrary, setSaveToLibrary} from './save-to-library.js';

const names = ['text', 'category', 'tags', 'search_text', 'prepend_text', 'append_text'];
let nextEditorId = 0;

function loadStyles() {
    const href = new URL('./prompt-editor.css', import.meta.url).href;
    if ([...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === href)) return;
    const link = document.createElement('link'); Object.assign(link, {rel:'stylesheet', href}); document.head.append(link);
}

export function installPairNode(nodeType, app, api, parseQuery) {
    installFocusedNode(nodeType, app, api, parseQuery, {layout:'pair'});
}

export function installSplitNode(nodeType, app, api, parseQuery) {
    installFocusedNode(nodeType, app, api, parseQuery, {layout:'split'});
}

export function installFocusedNode(nodeType, app, api, parseQuery, {layout = 'focused'} = {}) {
    const pair = layout === 'pair';
    const split = layout !== 'focused';
    const uiKey = pair ? 'pairUI' : split ? 'splitUI' : 'focusedUI';
    const textInputs = pair ? ['text','negative_text'] : ['text'];
    const prototype = nodeType.prototype;
    preferTextInputs(prototype, textInputs);
    const created = prototype.onNodeCreated;
    const removed = prototype.onRemoved;
    const configure = prototype.configure;
    const serialize = prototype.serialize;
    const connections = prototype.onConnectionsChange;
    const inputOnPos = prototype.getInputOnPos;
    const added = prototype.onAdded;
    prototype.onNodeCreated = function (...args) {
        created?.apply(this, args);
        const scope = createScope(); this._focusedScope = scope;
        const editorId = `pm-focused-${++nextEditorId}`;
        this.properties ||= {};
        const widgets = new Map();
        for (const name of pair ? [...names,'negative_text'] : names) {
            let widget = this.widgets?.find(item => item.name === name);
            if (!widget) widget = this.addWidget('text', name, '', () => {}, {});
            widget.hidden = true; widget.type = 'hidden'; widget.computeSize = () => [0, -4];
            widgets.set(name, widget);
        }
        exposeTextSockets(this, textInputs);
        // #90: the switch follows the hidden fallbacks above (one widget order on every frontend) and is drawn in the
        // toolbar, like the other hidden widgets are drawn by the editor.
        placeSaveWidgetLast(this);
        const nativeSave = saveWidget(this);
        if (nativeSave) { nativeSave.hidden = true; nativeSave.type = 'hidden'; nativeSave.computeSize = () => [0, -4]; }
        const element = document.createElement('div'); element.className = `ca-tools pm-focused${split ? ' pm-split' : ''}${pair ? ' pm-pair' : ''}`;
        element.setAttribute('aria-label',pair ? 'Paired prompt editor' : split ? 'Split prompt editor' : 'Focused prompt editor');
        loadStyles(); mountTheme(element, app.ui?.settings, scope);
        const toolbar = document.createElement('div'); toolbar.className = 'pm-editor-row pm-editor-toolbar';
        const title = document.createElement('strong'); title.textContent = 'Prompt Manager';
        const browse = createButton('Browse library','ca-primary'); browse.setAttribute('aria-expanded','false');
        const web = createButton('Web UI ↗'); toolbar.append(title);
        if (!split) toolbar.append(browse);
        toolbar.append(web);
        const save = pair ? createButton('Save pair','ca-primary') : null;
        if (save) toolbar.append(save);
        const saveSwitch = document.createElement('input'); saveSwitch.type = 'checkbox'; saveSwitch.dataset.saveToLibrary = '';
        const saveLabel = document.createElement('label'); saveLabel.className = 'pm-editor-save';
        saveLabel.title = 'Off: runs of this node are not saved, counted or linked to their images. The output is unchanged.';
        saveLabel.append(saveSwitch, document.createTextNode('Save to library'));
        if (nativeSave) toolbar.append(saveLabel);
        let saving = false;
        const workspace = document.createElement('div'); workspace.className = 'pm-editor-workspace';
        const editor = document.createElement('div'); editor.className = 'pm-editor-compose';
        workspace.append(editor);
        let webDisplayMode = 'popup';
        this._focusedSettingsReady = fetch(api.fileURL('/prompt_manager/settings'),{signal:scope.signal})
            .then(response => response.ok ? response.json() : null)
            .then(data => {
                const mode = data?.settings?.webui_display_mode;
                if (!scope.signal.aborted && data?.success && ['popup','newtab'].includes(mode)) webDisplayMode = mode;
            }).catch(() => {});
        const dropTargets = [];
        function textControl(name, label) {
            const widget = widgets.get(name);
            const nativeInput = widget.inputEl;
            const descriptor = Object.getOwnPropertyDescriptor(widget,'inputEl');
            const input = document.createElement('textarea'); input.rows = 6;
            input.spellcheck = nativeInput?.spellcheck ?? false;
            // The host retains widget.element; extensions receive the visible editor.
            Object.defineProperty(widget,'inputEl',{configurable:true,writable:true,value:input});
            scope.cleanup(() => {
                if (descriptor) Object.defineProperty(widget,'inputEl',descriptor);
                else delete widget.inputEl;
            });
            const field = createField(label,input,`${editorId}-${name}`);
            field.classList.add('pm-editor-main');
            const note = document.createElement('p'); note.className = 'pm-editor-status';
            note.textContent = linkNoteText(label);
            // The prompt box and its link note are drop targets for this input (#343): Nodes 2.0 resolves a drop
            // through the nearest `.lg-node-widget` and its `[data-slot-key]`; Classic asks getInputOnPos.
            for (const target of [field,note]) {
                const key = document.createElement('span'); key.hidden = true; key.dataset.slotKey = '';
                target.classList.add('lg-node-widget'); target.append(key);
                dropTargets.push({name,element:target,key});
            }
            return {input,field,note};
        }
        const positive = textControl('text',pair ? 'Positive prompt' : 'Main prompt');
        const main = positive.input, mainField = positive.field, connected = positive.note;
        const negative = pair ? textControl('negative_text','Negative prompt') : null;
        const controls = new Map([['text',main]]);
        if (negative) controls.set('negative_text',negative.input);
        const details = new Map();
        const completions = new Map();
        element.append(toolbar, workspace);
        if (pair) {
            const fields = document.createElement('div'); fields.className = 'pm-pair-texts';
            for (const control of [positive,negative]) {
                const slot = document.createElement('div'); slot.className = 'pm-pair-text-slot';
                slot.append(control.field,control.note); fields.append(slot);
            }
            editor.append(fields);
        } else editor.append(mainField, connected);
        function disclosure(key, label) {
            const section = document.createElement('details'); section.className = 'pm-editor-details';
            const summary = document.createElement('summary'); summary.textContent = label; section.append(summary); editor.append(section); details.set(key,section);
            scope.listen(section, 'toggle', () => {
                if (!section.open) completions.forEach(completion => completion.close());
                if (!scope.signal.aborted) thisNode.properties[uiKey] = snapshot();
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
        const footer = document.createElement('p'); footer.className = 'pm-editor-status'; footer.setAttribute('role','status');
        const SAVE_OFF = 'Save to library is off: runs of this node are not added to the library.';
        function runNote(saved) { return savesToLibrary(thisNode) ? saved : SAVE_OFF; }
        footer.textContent = runNote(pair ? 'The prompt pair is saved when this node runs, or use Save pair.' : 'Main prompt is saved when this node runs.');
        const saveConnectionNotice = pair ? document.createElement('p') : null;
        if (saveConnectionNotice) {
            saveConnectionNotice.className = 'pm-editor-status'; saveConnectionNotice.setAttribute('role','status');
            saveConnectionNotice.textContent = 'Run the node to save connected values.'; element.append(saveConnectionNotice);
        }
        function hasConnectedPairValues() { return ['text','negative_text','category','tags'].some(isConnected); }
        function pairPayload() { return {positive_text:value('text'),negative_text:value('negative_text'),category:value('category').trim(),tags:value('tags').split(',').map(tag => tag.trim()).filter(Boolean)}; }
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
        // The node id is assigned when the node joins a graph (and changes on paste), so keys follow it.
        function syncSlotKeys() {
            for (const target of dropTargets) target.key.dataset.slotKey = `${thisNode.id}-in-${thisNode.inputs?.findIndex(input => input.name === target.name)}`;
        }
        function render() {
            syncSlotKeys();
            mainField.hidden = Boolean(isConnected('text')); connected.hidden = !isConnected('text');
            if (negative) { negative.field.hidden = Boolean(isConnected('negative_text')); negative.note.hidden = !isConnected('negative_text'); }
            if (save) { save.disabled = saving || hasConnectedPairValues(); saveConnectionNotice.hidden = !hasConnectedPairValues(); }
            saveSwitch.checked = savesToLibrary(thisNode); saveSwitch.disabled = isConnected(SAVE_WIDGET);
            controls.forEach((input,name) => {
                input.value = value(name);
                if (name !== 'text' && name !== 'negative_text') {
                    if (isConnected(name)) completions.get(name)?.close();
                    input.hidden = Boolean(isConnected(name));
                    input._connectionNote.hidden = !isConnected(name);
                }
            });
            preview.textContent = ['prepend_text','text','append_text'].map(name => isConnected(name) ? `[${name} supplied by connection]` : name === 'text' ? value(name) : value(name).trim()).filter(Boolean).join(' ');
            library.refreshAvailability();
        }
        const library = createPromptLibrary({scope,api,mode:pair ? 'pairs' : 'prompts',settings:app.ui?.settings,parseQuery,isConnected:() => isConnected('text') || (pair && isConnected('negative_text')), onQuery:text => write('search_text',text), onFilters:() => {this.properties[uiKey] = snapshot();}, onUse:(prompt,includeMetadata) => {
            if (isConnected('text') || (pair && isConnected('negative_text'))) return;
            write('text',pair ? prompt.positive_text : prompt.text);
            if (pair) write('negative_text',prompt.negative_text || '');
            if (includeMetadata) {
                write('category',prompt.category || '');
                write('tags',Array.isArray(prompt.tags) ? prompt.tags.join(', ') : prompt.tags || '');
            }
            render(); footer.setAttribute('role','status'); footer.textContent = runNote(pair ? 'Loaded pair. Saved when this node runs.' : 'Loaded prompt. Saved when this node runs.'); main.focus();
        }});
        workspace.append(library.element); element.append(footer);
        function snapshot() {
            return {libraryOpen:!library.element.hidden,libraryFilters:library.filters(),...Object.fromEntries([...details].map(([name,section]) => [name,section.open]))};
        }
        function restore() {
            const state = thisNode.properties[uiKey] || {};
            details.forEach((section,name) => {section.open = Boolean(state[name]);});
            library.element.hidden = !split && !state.libraryOpen; browse.textContent = state.libraryOpen ? 'Hide library' : 'Browse library'; browse.setAttribute('aria-expanded', String(Boolean(state.libraryOpen)));
            library.setQuery(value('search_text')); library.restoreFilters(state.libraryFilters); render();
        }
        controls.forEach((input,name) => scope.listen(input,'input',() => {write(name,input.value);render();footer.setAttribute('role','status');footer.textContent=runNote(pair?'The pair is saved when this node runs.':'Main prompt is saved when this node runs.');}));
        scope.listen(saveSwitch,'change',() => {
            if (isConnected(SAVE_WIDGET)) return;
            setSaveToLibrary(thisNode, saveSwitch.checked); render();
            footer.setAttribute('role','status'); footer.textContent = runNote(pair ? 'The pair is saved when this node runs.' : 'Main prompt is saved when this node runs.');
            thisNode.setDirtyCanvas(true,true); app.graph?.setDirtyCanvas(true,true);
        });
        scope.listen(browse,'click',() => {
            library.element.hidden = !library.element.hidden;
            library.closeCompletions();
            browse.textContent = library.element.hidden ? 'Browse library' : 'Hide library'; browse.setAttribute('aria-expanded',String(!library.element.hidden));
            this.properties[uiKey] = snapshot(); if (!library.element.hidden) library.focus();
        });
        if (save) scope.listen(save,'click',async () => {
            if (saving || hasConnectedPairValues()) return;
            if (!value('text').trim()) {
                footer.setAttribute('role','alert'); footer.textContent = 'Enter a positive prompt before saving the pair.';
                main.focus(); return;
            }
            const savedDraft = JSON.stringify(pairPayload());
            saving = true; render(); footer.setAttribute('role','status'); footer.textContent = 'Saving pair…';
            try {
                const response = await fetch(api.fileURL('/prompt_manager/pairs'), {
                    method:'POST',headers:{'Content-Type':'application/json'},signal:scope.signal,
                    body:savedDraft,
                });
                const data = await response.json();
                if (scope.signal.aborted) return;
                if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
                footer.setAttribute('role','status'); footer.textContent = !hasConnectedPairValues() && JSON.stringify(pairPayload()) === savedDraft ? 'Pair saved.' : 'Earlier draft saved. Current changes are not saved yet.';
            } catch (error) {
                if (!scope.signal.aborted) { footer.setAttribute('role','alert'); footer.textContent = `Could not save pair: ${error.message}`; }
            } finally {
                saving = false; if (!scope.signal.aborted) render();
            }
        });
        scope.listen(web,'click',() => {
            const url = api.fileURL('/prompt_manager/web');
            const opened = webDisplayMode === 'newtab' ? window.open(url,'_blank') : window.open(url,'_blank','width=1200,height=800,scrollbars=yes,resizable=yes');
            footer.setAttribute('role',opened ? 'status' : 'alert');
            footer.textContent = opened ? 'Web UI opened.' : 'Allow popups to open Web UI, then try again.';
        });
        function inputAt(position) {
            const canvas = app.canvas;
            if (canvas?.linkConnector?.state?.connectingTo !== 'input' || !canvas.ds || !canvas.canvas) return undefined;
            const [x,y] = canvas.ds.convertOffsetToCanvas(position);
            const box = canvas.canvas.getBoundingClientRect();
            return textInputAtPoint(thisNode, [box.left + x, box.top + y], dropTargets);
        }
        this._focusedView = {element,render,restore,snapshot,inputAt,syncSlotKeys};
        this.addDOMWidget('prompt_manager_focused_ui','div',element,{serialize:false, hideOnZoom:false, getMinHeight:() => 360});
        this.size = [pair ? 1100 : split ? 900 : 550,pair ? 700 : 650]; this.setSize(this.size); restore();
    };
    prototype.configure = function (...args) {
        const result = configure?.apply(this,args);
        // A saved workflow restores its inputs with their widget bindings; keep the text inputs as sockets.
        exposeTextSockets(this, textInputs);
        // #90: an older workflow loads with Save to library on
        normalizeSaveWidget(this);
        this._focusedView?.restore();
        return result;
    };
    prototype.serialize = function (...args) {
        if (this._focusedView) this.properties[uiKey] = this._focusedView.snapshot();
        return serialize?.apply(this,args);
    };
    prototype.onConnectionsChange = function (...args) { const result = connections?.apply(this,args); this._focusedView?.render(); return result; };
    prototype.onRemoved = function (...args) { this._focusedScope?.dispose(); return removed?.apply(this,args); };
    prototype.onAdded = function (...args) { const result = added?.apply(this,args); this._focusedView?.syncSlotKeys(); return result; };
    // Classic: a link dropped on a prompt box (or its link note) fills that box's input.
    prototype.getInputOnPos = function (...args) {
        return inputOnPos?.apply(this,args) ?? this._focusedView?.inputAt(args[0]);
    };
}
