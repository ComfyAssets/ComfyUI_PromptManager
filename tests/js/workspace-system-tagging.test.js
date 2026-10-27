// System › Auto-tagging (#328, System S11 Tasks 6–7): the concept's rows with real data (D132 model options and live
// status, D133 suggestions per image), host drafts saved by the one Save row, and the actions: Download (an Activity
// job), Unload (direct), Preview tagging review (D115) and the reviewed Tag output images batch (D134).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const tagging = import('../../web/js/workspace/system-tagging.mjs');
const stateModule = import('../../web/js/workspace/system-state.mjs');
const coreModule = import('../../web/js/shared/toolkit/core.js');

const CONCEPT = fs.readFileSync(path.join(__dirname, '../../docs/concepts/workspace/app.js'), 'utf8');
const SOURCE = fs.readFileSync(path.join(__dirname, '../../web/js/workspace/system-tagging.mjs'), 'utf8');

/** A minimal DOM whose elements count their live listeners (a listener's signal aborts it, as in a browser). */
function fakeDOM() {
    const previous = global.document;
    const elements = [];
    class Element {
        constructor(tag) {
            this.tagName = tag.toUpperCase();
            this.children = [];
            this.attributes = {};
            this.listeners = [];
            this.dataset = {};
            this.className = '';
            this.id = '';
            this.value = '';
            this.disabled = false;
            this._text = '';
            elements.push(this);
        }
        get localName() { return this.tagName.toLowerCase(); }
        get options() { return this.children.filter(child => child.tagName === 'OPTION'); }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(text) { this.children = []; this._text = String(text); }
        append(...nodes) {
            for (const node of nodes) {
                node.parentNode?.children && (node.parentNode.children = node.parentNode.children.filter(child => child !== node));
                node.parentNode = this;
                this.children.push(node);
            }
        }
        remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(type, callback, options = {}) {
            if (options.signal?.aborted) return;
            const entry = { type, callback };
            this.listeners.push(entry);
            options.signal?.addEventListener('abort', () => { this.listeners = this.listeners.filter(item => item !== entry); });
        }
        removeEventListener(type, callback) { this.listeners = this.listeners.filter(item => item.type !== type || item.callback !== callback); }
        dispatch(type) { return Promise.all(this.listeners.filter(item => item.type === type).map(({ callback }) => callback({ type, target: this }))); }
    }
    global.document = { createElement: tag => new Element(tag), activeElement: null };
    const walk = (node, found = []) => { found.push(node); node.children.forEach(child => walk(child, found)); return found; };
    return {
        listenerCount: () => elements.reduce((sum, element) => sum + element.listeners.length, 0),
        find: (root, predicate) => walk(root).find(predicate),
        restore() { global.document = previous; },
    };
}

const HOST = { autotag_model_type: 'wd14-swinv2', autotag_existing: 'skip', autotag_general_threshold: 0.35,
    autotag_character_threshold: 0.85, autotag_suggestion_limit: 10, autotag_template: 'Describe the image as tags.' };
const entry = (label, size, extra = {}) => ({ name: label, label, size_label: size, downloaded: true, loaded: false, ...extra });
const MODELS = {
    success: true,
    models: { 'wd14-swinv2': entry('WD14 SwinV2', '~400 MB'), 'wd14-vit': entry('WD14 ViT', '~400 MB', { downloaded: false }),
        gguf: entry('GGUF JoyCaption', '~4.5 GB', { downloaded: false, model_exists: true, mmproj_exists: false }),
        hf: entry('HuggingFace JoyCaption', '~16 GB', { downloaded: false }) },
    model_loaded: false, loaded_model_type: null,
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const IMAGE = { filename: 'dunes_00001.png' };

async function mounted({ host = HOST, models = MODELS, modelsFail = false, image = IMAGE, start, post, hostFails = false } = {}) {
    const { mountTagging } = await tagging;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const drafts = createSystemDrafts({
        readBrowser: () => ({}), readRuntime: () => ({}),
        readHost: async () => { if (hostFails) throw new Error('offline'); return { values: host, revision: 'sha256:one' }; },
        writeBrowser: async () => ({ persisted: true }), writeHost: async () => ({ success: true, persisted: true }),
        writeRuntime: async () => ({}),
    });
    const scope = createScope();
    const calls = { get: [], post: [], toasts: [], navigated: [], opened: [], shown: [], started: [] };
    let answer = models;
    const api = {
        get: async (url, options) => {
            calls.get.push(url);
            assert.equal(options.cache, 'no-store');
            if (modelsFail) throw new Error('offline');
            return answer;
        },
        post: async (url, body) => { calls.post.push([url, body]); return post ? post(url, body) : { success: true }; },
    };
    const activity = { start: async (kind, body) => { calls.started.push([kind, body]); return start ? start(kind, body) : { job: { id: 'job1' } }; } };
    const [surface] = mountTagging(null, { api, activity, scope, drafts, notify: text => calls.toasts.push(text),
        navigate: hash => calls.navigated.push(hash), selected: () => image, openTagging: options => calls.opened.push(options),
        show: (plan, options) => calls.shown.push([plan, options]) });
    const byId = id => dom.find(surface, node => node.id === id);
    const button = label => dom.find(surface, node => node.tagName === 'BUTTON' && node.textContent === label);
    const description = () => byId('tag-model').parentNode.children[0].children[1].textContent;
    await drafts.reloadHost();
    await tick();
    return { dom, drafts, scope, surface, byId, button, description, calls, setModels: value => { answer = value; } };
}

test('the section keeps the concept surface, rows, labels and actions in order', () => {
    const start = CONCEPT.indexOf('case "tagging":');
    const body = CONCEPT.slice(start, CONCEPT.indexOf('break;', start));
    const rows = [...body.matchAll(/setting\("([^"]+)", "([^"]+)", "([^"]+)"/g)].map(([, id, label, description]) => [id, label, description]);
    assert.deepEqual(rows.map(([id]) => id), ['tag-model', 'tag-existing', 'threshold-general', 'threshold-character']);
    for (const [id, label] of rows) assert.ok(SOURCE.includes(`id: '${id}', label: '${label}'`), `${id} keeps "${label}"`);
    // OQ6: the true descriptions are kept verbatim; the model's design note is replaced by its live status (D132).
    for (const [id, , description] of rows.slice(1)) assert.ok(SOURCE.includes(`'${description}'`), `${id} description`);
    assert.ok(!SOURCE.includes(rows[0][2]));
    for (const option of ['Skip already tagged', 'Merge with existing', 'Replace after review']) assert.ok(body.includes(option) && SOURCE.includes(`'${option}'`));
    assert.ok(body.includes('<h2>Auto-tagging</h2>') && SOURCE.includes("'h2', 'Auto-tagging'"));
    assert.ok(body.includes('One tagging configuration for images and prompt-library actions. Every result can be reviewed before applying.'));
    assert.ok(body.includes('LLM tag template') && SOURCE.includes("'LLM tag template'"));
    assert.ok(body.includes('"Preview tagging review", "tag-review", "", "ca-primary"'));
    // Prototype copy is never shipped.
    for (const sample of ['Simulate download', 'Simulate unload', 'Ollama']) assert.ok(!SOURCE.includes(sample), sample);
});

test('model options are the four real engines with their size, in the concept order (D132, OQ5)', async () => {
    const { modelOptions, MODEL_IDS, modelLabel, modelSize } = await tagging;
    assert.deepEqual(MODEL_IDS, ['wd14-swinv2', 'wd14-vit', 'gguf', 'hf']);
    assert.deepEqual(modelOptions(MODELS.models).map(([, label]) => label),
        ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB']);
    // Before the status answered (or when it cannot be read) the documented labels stand in.
    assert.deepEqual(modelOptions(null), modelOptions(MODELS.models));
    assert.equal(modelLabel('hf', { label: '' }), 'HuggingFace JoyCaption');
    assert.equal(modelLabel('other', null), 'other');
    assert.equal(modelSize('other', null), '');
});

test('the Tagging model description is the chosen model’s live status (D132)', async () => {
    const { modelDescription, readStatus, STATUS_COPY } = await tagging;
    const status = readStatus(MODELS);
    assert.equal(modelDescription({ phase: 'loading' }, 'wd14-vit'), 'Loading model status…');
    assert.equal(modelDescription({ phase: 'failed' }, 'wd14-vit'), 'Model status could not be read.');
    assert.equal(modelDescription(status, 'wd14-swinv2'), 'Downloaded. Not loaded.');
    assert.equal(modelDescription(status, 'wd14-vit'), 'Not downloaded. Download size ~400 MB.');
    assert.equal(modelDescription(status, 'hf'), 'Not downloaded. Download size ~16 GB.');
    assert.equal(modelDescription(status, 'gguf'), 'Partly downloaded. Download again to finish.');
    const both = readStatus({ ...MODELS, models: { ...MODELS.models, gguf: entry('GGUF JoyCaption', '~4.5 GB', { downloaded: false,
        model_exists: false, mmproj_exists: false }) } });
    assert.equal(modelDescription(both, 'gguf'), 'Not downloaded. Download size ~4.5 GB.');
    const loaded = readStatus({ ...MODELS, model_loaded: true, loaded_model_type: 'wd14-swinv2',
        models: { ...MODELS.models, 'wd14-swinv2': entry('WD14 SwinV2', '~400 MB', { loaded: true }) } });
    assert.equal(loaded.loadedType, 'wd14-swinv2');
    assert.equal(modelDescription(loaded, 'wd14-swinv2'), STATUS_COPY.loaded);
    assert.equal(modelDescription(status, 'unknown'), STATUS_COPY.failed);
    assert.deepEqual(readStatus(null), { phase: 'failed', models: null, loadedType: null });
    assert.equal(readStatus({ models: {}, model_loaded: true, loaded_model_type: '/abs/path' }).loadedType, null);
});

test('controls draft their host keys; numbers as numbers, other text as typed', async () => {
    const { SETTING_KEYS, coerceNumber, draftValue } = await tagging;
    assert.deepEqual(SETTING_KEYS, { 'tag-model': 'autotag_model_type', 'tag-existing': 'autotag_existing',
        'threshold-general': 'autotag_general_threshold', 'threshold-character': 'autotag_character_threshold',
        'tag-suggestions': 'autotag_suggestion_limit', 'tag-template': 'autotag_template' });
    assert.deepEqual(['0.4', ' 1.5 ', '15', '', 'abc', '1e-1'].map(coerceNumber), [0.4, 1.5, 15, '', 'abc', 0.1]);
    assert.equal(draftValue('autotag_suggestion_limit', '15'), 15);
    assert.equal(draftValue('autotag_template', ' 12 '), ' 12 ');
    assert.equal(draftValue('autotag_existing', undefined), '');
    const { dom, drafts, scope, byId } = await mounted();
    try {
        assert.equal(byId('tag-model').value, 'wd14-swinv2');
        assert.equal(byId('threshold-general').value, '0.35');
        assert.equal(byId('tag-template').value, HOST.autotag_template);
        assert.deepEqual([byId('threshold-general').min, byId('threshold-general').max, byId('threshold-general').step], ['0', '1', '0.05']);
        assert.deepEqual([byId('tag-suggestions').min, byId('tag-suggestions').max], ['1', '100']);
        byId('tag-existing').value = 'merge'; await byId('tag-existing').dispatch('change');
        byId('threshold-general').value = '0.4'; await byId('threshold-general').dispatch('change');
        byId('tag-suggestions').value = '15'; await byId('tag-suggestions').dispatch('change');
        byId('tag-template').value = 'Tags only.'; await byId('tag-template').dispatch('input');
        assert.deepEqual(drafts.changes('host'), { autotag_existing: 'merge', autotag_general_threshold: 0.4,
            autotag_suggestion_limit: 15, autotag_template: 'Tags only.' });
        // Every control names its stored key, so a refused save highlights it (D92).
        for (const id of Object.keys(SETTING_KEYS)) assert.equal(byId(id).dataset.key, SETTING_KEYS[id]);
        assert.equal(byId('tag-template').dataset.setting, 'tag-template');
    } finally { scope.dispose(); assert.equal(dom.listenerCount(), 0); dom.restore(); }
});

test('opening the section reads the model status once; the select waits for it', async () => {
    const { dom, scope, byId, description, calls, surface } = await mounted();
    try {
        assert.deepEqual(calls.get, ['/prompt_manager/autotag/models']);
        assert.deepEqual(calls.post, []);
        assert.equal(byId('tag-model').disabled, false);
        assert.equal(description(), 'Downloaded. Not loaded.');
        assert.deepEqual(byId('tag-model').options.map(option => option.textContent),
            ['WD14 SwinV2 · ~400 MB', 'WD14 ViT · ~400 MB', 'GGUF JoyCaption · ~4.5 GB', 'HuggingFace JoyCaption · ~16 GB']);
        assert.deepEqual(surface.children.map(node => node.className),
            ['', '', 'setting-row', 'setting-row', 'setting-row', 'setting-row', 'setting-row', 'field', 'row-actions', 'operation']);
    } finally { scope.dispose(); dom.restore(); }
    const failed = await mounted({ modelsFail: true });
    try {
        assert.equal(failed.description(), 'Model status could not be read.');
        assert.equal(failed.button('Download').getAttribute('aria-disabled'), 'true');
        assert.equal(failed.button('Unload').disabled, true);
        await failed.button('Download').dispatch('click');
        assert.deepEqual(failed.calls.toasts, ['Model status could not be read.']);
        assert.deepEqual(failed.calls.started, []);
    } finally { failed.scope.dispose(); failed.dom.restore(); }
});

test('server settings that cannot be read leave the rows disabled and say so', async () => {
    const { dom, scope, byId, button, description, calls } = await mounted({ hostFails: true });
    try {
        assert.equal(byId('tag-existing').disabled, true);
        assert.equal(byId('tag-existing').parentNode.children[0].children.at(-1).textContent, 'Server settings could not be read.');
        assert.equal(description(), 'Model status could not be read.');
        await button('Preview tagging review').dispatch('click');
        assert.deepEqual(calls.toasts, ['Server settings could not be read.']);
    } finally { scope.dispose(); dom.restore(); }
});

test('Download starts the model download job and opens it; the label follows the disk (OQ10)', async () => {
    const { dom, drafts, scope, button, calls, byId } = await mounted();
    try {
        assert.ok(button('Download again'));
        byId('tag-model').value = 'wd14-vit'; await byId('tag-model').dispatch('change');
        assert.equal(drafts.get('host', 'autotag_model_type'), 'wd14-vit');
        await button('Download').dispatch('click');
        assert.deepEqual(calls.started, [['model-download', { model_type: 'wd14-vit' }]]);
        assert.deepEqual(calls.navigated, ['#/activity/job1']);
    } finally { scope.dispose(); dom.restore(); }
});

test('startJob toasts by code and never navigates on a refusal', async () => {
    const { startJob, startRefusal } = await tagging;
    const { createScope } = await coreModule;
    const error = (code, message = 'Server says.', extra = {}) => Object.assign(new Error(message), { code, ...extra });
    assert.equal(startRefusal(error('model-busy', 'x', { operation: 'Tagging model download · WD14 ViT' }), 'X'),
        'The tagging model is busy: Tagging model download · WD14 ViT. Try again when it finishes.');
    assert.equal(startRefusal(error('model-busy', 'x'), 'X'),
        'The tagging model is busy: Another tagging operation. Try again when it finishes.');
    for (const code of ['model-unavailable', 'review-required', 'insufficient-space']) assert.equal(startRefusal(error(code), 'X'), 'Server says.');
    assert.equal(startRefusal(error('weird'), 'Tag output images'), 'Tag output images could not start. Try again.');
    assert.equal(startRefusal(new Error('offline'), 'Tag output images'), 'Tag output images could not start. Try again.');
    const scope = createScope();
    const toasts = [];
    const navigated = [];
    const run = start => startJob({ activity: { start }, scope, kind: 'autotag', body: {}, what: 'Tag output images',
        notify: text => toasts.push(text), navigate: hash => navigated.push(hash) });
    assert.equal(await run(async () => { throw error('model-busy', 'busy', { operation: 'Generate suggestions' }); }), null);
    assert.equal(await run(async () => ({ busy: { id: 'a1', title: 'Scan for metadata' } })), null);
    assert.equal(await run(async () => ({ busy: null })), null);
    assert.deepEqual(toasts, ['The tagging model is busy: Generate suggestions. Try again when it finishes.',
        ['Another job is running: ', { text: 'Scan for metadata', href: '#/activity/a1' }, '.'],
        'Another job is running. Try again when it ends.']);
    assert.deepEqual(await run(async () => ({ job: { id: 'j2' } })), { id: 'j2' });
    assert.deepEqual(navigated, ['#/activity/j2']);
    scope.dispose();
    assert.equal(await run(async () => { throw error('weird'); }), null);
    assert.deepEqual(await run(async () => ({ job: { id: 'j3' } })), { id: 'j3' });
    assert.deepEqual(navigated, ['#/activity/j2']);
    assert.equal(toasts.length, 3);
});

test('Unload is enabled only while a model is loaded; a busy refusal names the operation', async () => {
    const loadedModels = { ...MODELS, model_loaded: true, loaded_model_type: 'wd14-swinv2' };
    let refuse = null;
    const post = url => {
        if (refuse) throw refuse;
        return url.endsWith('/unload') ? { success: true, message: 'WD14SWINV2 model unloaded successfully', model_loaded: false } : {};
    };
    const { dom, scope, button, calls, setModels } = await mounted({ models: loadedModels, post });
    try {
        assert.equal(button('Unload').disabled, false);
        refuse = Object.assign(new Error('A tagging model is busy'), { code: 'model-busy', operation: 'Tagging model download · WD14 ViT' });
        await button('Unload').dispatch('click');
        assert.deepEqual(calls.toasts, ['The tagging model is busy: Tagging model download · WD14 ViT. Try again when it finishes.']);
        refuse = new Error('offline');
        await button('Unload').dispatch('click');
        assert.equal(calls.toasts.at(-1), 'The tagging model could not be unloaded. Try again.');
        refuse = null;
        setModels(MODELS);
        await button('Unload').dispatch('click');
        assert.equal(calls.toasts.at(-1), 'WD14 SwinV2 unloaded.');
        assert.deepEqual(calls.post.map(([url]) => url), Array(3).fill('/prompt_manager/autotag/unload'));
        // Each attempt reads the status again; nothing is loaded now.
        assert.equal(calls.get.length, 4);
        assert.equal(button('Unload').disabled, true);
    } finally { scope.dispose(); dom.restore(); }
});

test('Preview tagging review opens the Tag image dialog with the section’s values, else says why (D115)', async () => {
    const { NO_SELECTION, taggingPreset, draftProblem } = await tagging.then(async module => ({ ...module,
        NO_SELECTION: (await import('../../web/js/workspace/vocabulary.mjs')).NO_SELECTION }));
    assert.equal(NO_SELECTION, 'Select an image in Images first.');
    assert.deepEqual(taggingPreset(HOST), { model: 'wd14-swinv2', options: { general_threshold: 0.35, character_threshold: 0.85,
        suggestion_limit: 10 } });
    assert.deepEqual(taggingPreset({ ...HOST, autotag_model_type: 'gguf' }), { model: 'gguf',
        options: { instructions: HOST.autotag_template, suggestion_limit: 10 } });
    assert.equal(draftProblem(HOST), null);
    assert.deepEqual(draftProblem({ ...HOST, autotag_general_threshold: 1.5 }),
        { key: 'autotag_general_threshold', message: 'General-tag confidence must be between 0 and 1.' });
    assert.equal(draftProblem({ ...HOST, autotag_character_threshold: '' }).key, 'autotag_character_threshold');
    assert.equal(draftProblem({ ...HOST, autotag_suggestion_limit: 2.5 }).key, 'autotag_suggestion_limit');
    assert.equal(draftProblem({ ...HOST, autotag_suggestion_limit: 0 }, { limit: false }), null);
    assert.deepEqual(draftProblem({ ...HOST, autotag_model_type: 'hf', autotag_template: '  ' }),
        { key: 'autotag_template', message: 'LLM tag template must be non-empty text.' });
    assert.deepEqual(draftProblem({ ...HOST, autotag_model_type: 'hf', autotag_template: 'x'.repeat(4001) }),
        { key: 'autotag_template', message: 'LLM tag template must be at most 4000 characters.' });
    // Characters are code points, as Python's len counts them: 4000 emoji (8000 UTF-16 units) are allowed.
    assert.equal(draftProblem({ ...HOST, autotag_model_type: 'hf', autotag_template: '🏜'.repeat(4000) }), null);
    assert.equal(draftProblem({ ...HOST, autotag_model_type: 'hf', autotag_template: '🏜'.repeat(4001) }).key, 'autotag_template');
    // A WD14 run never reads the template, nor a language model the thresholds.
    assert.equal(draftProblem({ ...HOST, autotag_template: '' }), null);
    assert.equal(draftProblem({ ...HOST, autotag_model_type: 'gguf', autotag_general_threshold: 7 }), null);

    const none = await mounted({ image: null });
    try {
        const preview = none.button('Preview tagging review');
        assert.equal(preview.getAttribute('aria-disabled'), 'true');
        assert.equal(preview.className, 'ca-primary');
        await preview.dispatch('click');
        assert.deepEqual(none.calls.toasts, ['Select an image in Images first.']);
        assert.deepEqual(none.calls.opened, []);
    } finally { none.scope.dispose(); none.dom.restore(); }

    const { dom, scope, button, byId, calls } = await mounted();
    try {
        await button('Preview tagging review').dispatch('click');
        assert.equal(calls.opened.length, 1);
        assert.equal(calls.opened[0].image, IMAGE);
        assert.equal(calls.opened[0].source, 'tags');
        assert.deepEqual(calls.opened[0].preset, taggingPreset(HOST));
        byId('threshold-general').value = '1.5'; await byId('threshold-general').dispatch('change');
        assert.equal(button('Preview tagging review').getAttribute('aria-disabled'), 'true');
        await button('Preview tagging review').dispatch('click');
        assert.deepEqual(calls.toasts, ['General-tag confidence must be between 0 and 1.']);
        assert.equal(calls.opened.length, 1);
    } finally { scope.dispose(); dom.restore(); }
});

const PLAN = { images: 3, prompts: 2, already_tagged: 1, to_tag: 1,
    model: { type: 'wd14-swinv2', label: 'WD14 SwinV2', size_label: '~400 MB', downloaded: true }, existing: 'skip',
    review_required: false, backup: { policy: 'on' }, disk: { free_bytes: 10 * 2 ** 30, required_bytes: 2 ** 20, sufficient: true } };

test('the batch review grid, body and Cancel-only reasons (D134, CUJ 4–5)', async () => {
    const { batchGrid, batchBody, batchCloseNote, existingLabel, REPLACE_NOTE, NOTHING_TO_TAG, NO_LINKED_IMAGES } = await tagging;
    assert.deepEqual(batchGrid(PLAN, HOST), [['Model', 'WD14 SwinV2 · ~400 MB'], ['Existing tags', 'Skip already tagged'],
        ['Images linked', '3'], ['Prompts', '2'], ['Already tagged', '1'], ['Prompts to tag', 'up to 1'],
        ['Settings', 'General 0.35 · Character 0.85'], ['Backup', 'Create and verify before changes']]);
    const llm = { ...HOST, autotag_model_type: 'gguf' };
    assert.deepEqual(batchGrid({ ...PLAN, model: { type: 'gguf' }, backup: { policy: 'off' } }, llm).slice(-2),
        [['Settings', 'Template of 27 characters'], ['Backup', 'No safety backup (the policy is off)']]);
    assert.equal(batchGrid({}, HOST)[2][1], '0');
    assert.equal(existingLabel('odd'), 'odd');
    assert.deepEqual(batchBody(HOST), { model_type: 'wd14-swinv2', existing: 'skip',
        options: { general_threshold: 0.35, character_threshold: 0.85 } });
    assert.deepEqual(batchBody(llm), { model_type: 'gguf', existing: 'skip', options: { instructions: HOST.autotag_template } });
    assert.equal(batchCloseNote(PLAN), null);
    assert.equal(batchCloseNote({ ...PLAN, review_required: true }), REPLACE_NOTE);
    assert.equal(REPLACE_NOTE, 'Replacing tags needs a review of each image. Use Preview tagging review, or choose Skip or Merge for a batch run.');
    assert.equal(batchCloseNote({ ...PLAN, model: { ...PLAN.model, downloaded: false } }), 'WD14 SwinV2 is not downloaded. Download it first.');
    assert.equal(batchCloseNote({ ...PLAN, disk: { free_bytes: 2 ** 20, required_bytes: 2 ** 21, sufficient: false } }),
        'Not enough free disk space: the safety backup needs about 2.0 MB and 1.0 MB is free. Nothing is written until there is room.');
    assert.equal(batchCloseNote({ ...PLAN, prompts: 0, to_tag: 0 }), NO_LINKED_IMAGES);
    assert.equal(batchCloseNote({ ...PLAN, to_tag: 0 }), NOTHING_TO_TAG);
    assert.equal(NOTHING_TO_TAG, 'Every prompt with an output image is already tagged.');
});

test('showBatchPlan opens the review with the backup box, or the Cancel-only review', async () => {
    const { showBatchPlan, REVIEW_TITLE, BATCH_SENTENCE } = await tagging;
    const { createScope } = await coreModule;
    const scope = createScope();
    const reviews = [];
    const closed = [];
    const started = [];
    const options = { values: HOST, scope, activity: { start: async (kind, body) => { started.push([kind, body]); return { job: { id: 'b1' } }; } },
        notify: () => {}, navigate: () => {}, openReview: review => reviews.push(review), openCloseOnly: review => closed.push(review) };
    showBatchPlan(PLAN, options);
    assert.equal(reviews.length, 1);
    const [review] = reviews;
    assert.equal(review.title, REVIEW_TITLE);
    assert.equal(review.title, 'Review tag output images');
    assert.equal(review.heading, 'Tag output images');
    assert.equal(review.sentence, BATCH_SENTENCE);
    assert.equal(review.primary.label, 'Start tagging');
    assert.equal(review.cancelLabel, 'Cancel');
    assert.equal(review.backup.copy.heading, 'Back up the database first (recommended)');
    await review.onStart();
    assert.deepEqual(started, [['autotag', { model_type: 'wd14-swinv2', existing: 'skip',
        options: { general_threshold: 0.35, character_threshold: 0.85 } }]]);
    showBatchPlan({ ...PLAN, backup: { policy: 'off' } }, options);
    assert.equal(reviews[1].backup, null);
    showBatchPlan({ ...PLAN, review_required: true }, options);
    assert.equal(closed.length, 1);
    assert.equal(closed[0].note.startsWith('Replacing tags needs a review'), true);
    assert.equal(closed[0].grid[0][0], 'Model');
    scope.dispose();
});

test('Tag output images › Review previews the drafts, else says why; a refused preview is toasted', async () => {
    let refuse = null;
    const post = url => {
        if (refuse) throw refuse;
        assert.equal(url, '/prompt_manager/autotag/batch/preview');
        return PLAN;
    };
    const { dom, scope, button, byId, calls } = await mounted({ post });
    try {
        const review = button('Review');
        assert.equal(review.getAttribute('aria-disabled'), null);
        await review.dispatch('click');
        await tick();
        assert.deepEqual(calls.post, [['/prompt_manager/autotag/batch/preview', { model_type: 'wd14-swinv2', existing: 'skip' }]]);
        assert.equal(calls.shown.length, 1);
        assert.equal(calls.shown[0][0], PLAN);
        assert.deepEqual(calls.shown[0][1].values, HOST);
        assert.equal(review.textContent, 'Review');
        refuse = Object.assign(new Error('busy'), { code: 'busy', running: { id: 'r1', title: 'Optimize database' } });
        await review.dispatch('click');
        await tick();
        assert.deepEqual(calls.toasts.at(-1), ['Another job is running: ', { text: 'Optimize database', href: '#/activity/r1' }, '.']);
        refuse = new Error('offline');
        await review.dispatch('click');
        await tick();
        assert.equal(calls.toasts.at(-1), 'The plan could not be reviewed. Nothing was changed.');
        // A model that is not on disk cannot be reviewed: the button explains instead.
        byId('tag-model').value = 'wd14-vit'; await byId('tag-model').dispatch('change');
        assert.equal(review.getAttribute('aria-disabled'), 'true');
        await review.dispatch('click');
        assert.equal(calls.toasts.at(-1), 'WD14 ViT is not downloaded. Download it first.');
        assert.equal(calls.post.length, 3);
    } finally { scope.dispose(); dom.restore(); }
});

test('settingRefusal marks only an Auto-tagging save\'s plain 400 as invalid-setting (CUJ 2a)', async () => {
    const { settingRefusal } = await import('../../web/js/workspace/system.mjs');
    const refusal = (extra = {}) => Object.assign(new Error('General-tag confidence must be between 0 and 1.'),
        { status: 400, refused: true, code: null, ...extra });
    const tagging = { autotag_general_threshold: 1.5, expected_revision: 'sha256:one' };
    assert.equal(settingRefusal(refusal(), tagging).code, 'invalid-setting');
    // Another section's refusal keeps the generic copy, even with a plain 400.
    const other = refusal({ message: 'Result timeout must be between 0 and 300.' });
    assert.equal(settingRefusal(other, { result_timeout: 999, expected_revision: 'sha256:one' }).code, null);
    assert.equal(settingRefusal(refusal(), undefined).code, null);
    for (const extra of [{ status: 409 }, { refused: false }, { code: 'jxl-unavailable' }]) {
        assert.notEqual(settingRefusal(refusal(extra), tagging).code, 'invalid-setting');
    }
    assert.equal(settingRefusal(undefined, tagging), undefined);
});

test('a part-downloaded GGUF offers Download again', async () => {
    const { downloadLabel, partlyDownloaded } = await tagging;
    const partial = MODELS.models.gguf;
    assert.equal(partlyDownloaded('gguf', partial), true);
    assert.equal(downloadLabel('gguf', partial), 'Download again');
    assert.equal(downloadLabel('gguf', { ...partial, model_exists: false }), 'Download');
    assert.equal(downloadLabel('hf', MODELS.models.hf), 'Download');
    assert.equal(downloadLabel('wd14-swinv2', MODELS.models['wd14-swinv2']), 'Download again');
    assert.equal(partlyDownloaded('hf', { model_exists: true, mmproj_exists: false }), false);
    const { dom, scope, byId, button, description } = await mounted();
    try {
        byId('tag-model').value = 'gguf'; await byId('tag-model').dispatch('change');
        assert.equal(description(), 'Partly downloaded. Download again to finish.');
        assert.ok(button('Download again'));
    } finally { scope.dispose(); dom.restore(); }
});

test('the disk note says what is short in the server\'s words, and guesses no unknown byte counts', async () => {
    const { diskShortNote, DISK_SHORT_UNKNOWN } = await tagging;
    assert.equal(diskShortNote({ required_bytes: 3 * 2 ** 20, free_bytes: 2 ** 19, sufficient: false }),
        'Not enough free disk space: the safety backup needs about 3.0 MB and 0.5 MB is free. Nothing is written until there is room.');
    for (const disk of [{ required_bytes: NaN, free_bytes: 1 }, { required_bytes: 1, free_bytes: Infinity }, { required_bytes: -1, free_bytes: 1 },
        { required_bytes: '1', free_bytes: 1 }, null]) {
        assert.equal(diskShortNote(disk), DISK_SHORT_UNKNOWN);
    }
    assert.equal(DISK_SHORT_UNKNOWN, 'Not enough free disk space for the safety backup. Nothing is written until there is room.');
    assert.ok(!diskShortNote({ required_bytes: 1, free_bytes: 0 }).includes('apply'));
});

test('a drafts repaint never re-enables Download while its start is in flight', async () => {
    let answer;
    const start = () => new Promise(resolve => { answer = resolve; });
    const { dom, scope, byId, button, calls } = await mounted({ start });
    try {
        const download = button('Download again');
        const pressed = download.dispatch('click');
        await tick();
        assert.equal(download.getAttribute('aria-disabled'), 'true');
        byId('tag-existing').value = 'merge'; await byId('tag-existing').dispatch('change');
        assert.equal(download.getAttribute('aria-disabled'), 'true');
        // A second press while it starts is ignored.
        await download.dispatch('click');
        assert.equal(calls.started.length, 1);
        answer({ job: { id: 'd1' } });
        await pressed;
        assert.equal(download.getAttribute('aria-disabled'), null);
        assert.deepEqual(calls.navigated, ['#/activity/d1']);
    } finally { scope.dispose(); dom.restore(); }
});

test('an Unload that found nothing loaded says nothing and reads the status again', async () => {
    const { dom, scope, button, calls } = await mounted({ models: { ...MODELS, model_loaded: true, loaded_model_type: 'wd14-swinv2' },
        post: () => ({ success: true, message: 'No model was loaded' }) });
    try {
        await button('Unload').dispatch('click');
        assert.deepEqual(calls.toasts, []);
        assert.equal(calls.get.length, 2);
    } finally { scope.dispose(); dom.restore(); }
});

test('an unknown saved engine has its own reason', async () => {
    const { UNKNOWN_MODEL } = await tagging;
    const { dom, scope, button, calls } = await mounted({ host: { ...HOST, autotag_model_type: 'wd14-convnext' } });
    try {
        await button('Preview tagging review').dispatch('click');
        await button('Download').dispatch('click');
        await button('Review').dispatch('click');
        assert.deepEqual(calls.toasts, [UNKNOWN_MODEL, UNKNOWN_MODEL, UNKNOWN_MODEL]);
        assert.equal(UNKNOWN_MODEL, 'The saved tagging model is not one of these engines. Choose one and save preferences.');
        assert.deepEqual(calls.started, []);
    } finally { scope.dispose(); dom.restore(); }
});

test('a review whose dialog fails to open is toasted, not lost', async () => {
    const { dom, scope, button, calls } = await mounted({ post: () => PLAN });
    try {
        calls.shown.push = () => { throw new Error('dialog failed'); };
        await button('Review').dispatch('click');
        await tick();
        assert.equal(calls.toasts.at(-1), 'The plan could not be reviewed. Nothing was changed.');
        assert.equal(button('Review').getAttribute('aria-disabled'), null);
    } finally { scope.dispose(); dom.restore(); }
});

test('a status read, an unload and a preview that finish after the section left do nothing', async () => {
    const { mountTagging } = await tagging;
    const { createSystemDrafts } = await stateModule;
    const { createScope } = await coreModule;
    const dom = fakeDOM();
    const pending = [];
    const later = value => new Promise(resolve => pending.push(() => resolve(value)));
    const drafts = createSystemDrafts({ readBrowser: () => ({}), readRuntime: () => ({}),
        readHost: async () => ({ values: HOST, revision: 'sha256:one' }), writeBrowser: async () => ({}), writeHost: async () => ({}),
        writeRuntime: async () => ({}) });
    await drafts.load('host');
    const scope = createScope();
    const toasts = [];
    const shown = [];
    let gets = 0;
    const loaded = { ...MODELS, model_loaded: true, loaded_model_type: 'wd14-swinv2',
        models: { ...MODELS.models, 'wd14-swinv2': entry('WD14 SwinV2', '~400 MB', { loaded: true }) } };
    const api = { get: () => { gets += 1; return gets === 1 ? Promise.resolve(loaded) : later(MODELS); }, post: url => later(url.endsWith('/unload') ? { success: true, model_loaded: false } : PLAN) };
    const [surface] = mountTagging(null, { api, activity: {}, scope, drafts, notify: text => toasts.push(text), navigate: () => {},
        selected: () => IMAGE, openTagging: () => {}, show: plan => shown.push(plan) });
    try {
        await tick();
        const find = label => dom.find(surface, node => node.tagName === 'BUTTON' && node.textContent === label);
        const description = () => dom.find(surface, node => node.id === 'tag-model').parentNode.children[0].children[1].textContent;
        assert.equal(description(), 'Downloaded and loaded.');
        const unloading = find('Unload').dispatch('click');
        const reviewing = find('Review').dispatch('click');
        await tick();
        scope.dispose();
        for (const settle of pending.splice(0)) settle();
        await unloading;
        await reviewing;
        await tick();
        for (const settle of pending.splice(0)) settle();
        await tick();
        assert.deepEqual(toasts, []);
        assert.deepEqual(shown, []);
        // The status read after the unload never reached the page either.
        assert.equal(description(), 'Downloaded and loaded.');
        assert.equal(dom.listenerCount(), 0);
    } finally { dom.restore(); }
});
