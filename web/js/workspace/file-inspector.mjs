import { createScope, createButton } from '../shared/toolkit/core.js';
import { mountPromptBlocks, promptRoles } from './prompt-blocks.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createSendButton } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';

const RAW_KEYS = ['prompt', 'workflow', 'parameters', 'comfyassets_prompt_pairs'];
const RAW_LIMIT = 'Raw metadata exceeds the response limit. Download the original file to inspect it externally.';
const CONTAINER_WORKFLOW = 'Original workflow JSON text is unavailable for this container record. Download metadata or the original file.';
const REPRESENTATIONS = {
    'original-text': 'Original embedded text',
    'reserialized-json': 'Reserialized container JSON; numeric spelling/precision may differ',
    invalid: 'Invalid container record; text is unavailable',
};
const FACTS = [['seed', 'Seed'], ['steps', 'Steps'], ['cfg', 'CFG'], ['sampler', 'Sampler'], ['scheduler', 'Scheduler'], ['model', 'Model']];
const FACT_SOURCES = {'api-graph': 'Saved API graph', workflow: 'Saved workflow', 'embedded-parameters': 'Embedded generation parameters'};

function safeFilename(value) {
    return String(value || 'file').replace(/[\\/\x00-\x1f\x7f<>:"|?*]/g, '_').replace(/^\.+|[. ]+$/g, '') || 'file';
}

/** Export only the bounded public evidence, never normalized graphs or container carriers. */
export function inspectionDownloads(record) {
    const name = safeFilename(record?.download?.filename || record?.identity?.path?.split('/').at(-1));
    const stem = name.replace(/\.[^.]+$/, '') || 'file';
    const unavailable = (suffix, reason) => ({available:false, reason, filename:`${stem}.${suffix}.json`, bodyText:null, mime:'application/json'});
    const metadata = record?.metadata;
    const reason = metadata?.status === 'limited' ? RAW_LIMIT : 'Full metadata is unavailable for this inspection response.';
    const result = {metadata:unavailable('metadata', reason), workflow:unavailable('workflow', reason)};
    if (!metadata || metadata.status === 'limited') return result;
    const raw = {}, record_formats = {}, representations = {};
    for (const key of RAW_KEYS) {
        if (Object.hasOwn(REPRESENTATIONS, metadata.record_formats?.[key])) {
            record_formats[key] = metadata.record_formats[key];
            representations[key] = REPRESENTATIONS[record_formats[key]];
            if (record_formats[key] !== 'invalid' && typeof metadata.raw?.[key] === 'string') raw[key] = metadata.raw[key];
        }
    }
    const envelope = {inspection_version:1, identity:record.identity, file:record.file, prompts:record.prompts,
        generation:record.generation, library:record.library,
        metadata:{status:metadata.status, omitted_reason:metadata.omitted_reason, raw, record_formats, representations,
            scope:'Supported embedded records only; one selected container carrier for video. Other metadata and private carrier text are excluded.'}};
    result.metadata = {...result.metadata, available:true, reason:null, bodyText:JSON.stringify(envelope, null, 2)};
    if (record_formats.workflow === 'reserialized-json') result.workflow.reason = CONTAINER_WORKFLOW;
    else if (record_formats.workflow === 'original-text' && metadata.workflow_graph?.status === 'parsed' && typeof raw.workflow === 'string') {
        result.workflow = {...result.workflow, available:true, reason:null, bodyText:raw.workflow};
    } else result.workflow.reason = 'A valid original workflow JSON record is not available in this file.';
    return result;
}

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function childScope(parent) {
    const scope = createScope(), dispose = () => scope.dispose();
    parent.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parent.signal.removeEventListener('abort', dispose));
    if (parent.signal.aborted) scope.dispose();
    return scope;
}
function disclosure(label) {
    const node = el('details'); node.append(el('summary', label)); return node;
}
function definitionList(entries, className) {
    const list = el('dl', undefined, className);
    for (const [label, value] of entries) {
        const row = el('div'); row.append(el('dt', label), el('dd', value ?? 'Not available')); list.append(row);
    }
    return list;
}
function factText(fact) {
    if (fact?.status === 'known' && typeof fact.value === 'string') return fact.value;
    if (fact?.status === 'ambiguous') return 'Ambiguous';
    if (fact?.status === 'absent') return 'Not recorded';
    return fact?.reason === 'projection-limited' ? 'Unavailable within inspection limits' : 'Unresolved';
}
function formatBytes(value) {
    return value === null || value === undefined ? null : value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
}

export function mountFileInspector(container, {sender, parentScope, inline = false, onPreview, onRetry, onRefresh, onInteraction = () => {}}) {
    if (parentScope.signal.aborted) return {setState() {}, dispose() {}};
    const scope = childScope(parentScope);
    let contentScope;
    container.classList.add('file-inspector');
    scope.listen(container, 'pointerdown', onInteraction);
    scope.listen(container, 'keydown', onInteraction);
    scope.cleanup(() => {contentScope?.dispose(); container.replaceChildren(); container.classList.remove('file-inspector');});

    function setState(state) {
        if (scope.signal.aborted) return;
        contentScope?.dispose(); contentScope = childScope(scope);
        container.replaceChildren();
        if (state.loading || state.error) {
            const status = el('p', state.loading ? 'Loading file details…' : state.refreshRequired ? state.message : 'Could not read prompts from this file.', 'file-status muted');
            status.setAttribute('role', state.error ? 'alert' : 'status'); container.append(status);
            if (state.error && state.refreshRequired) {
                if (onRefresh) {
                    const refresh = createButton('Refresh image collection');
                    contentScope.listen(refresh, 'click', onRefresh); container.append(refresh);
                } else container.append(el('p', 'Reopen this file from a refreshed image collection.', 'muted'));
            } else if (state.error && onRetry) {
                const retry = createButton('Retry prompt details'); contentScope.listen(retry, 'click', onRetry); container.append(retry);
            }
            return;
        }
        const record = state.record;
        if (!record) return;
        const {file, library, metadata} = record;
        const filename = record.identity.path.split('/').at(-1);
        if (inline && record.download) {
            const cover = createButton('', 'file-cover'); cover.setAttribute('aria-label', 'Open full preview');
            const video = file?.media_type === 'video';
            const media = el(video ? 'video' : 'img'); media.src = record.download.url;
            if (video) {
                media.muted = true; media.playsInline = true; media.preload = 'metadata';
                contentScope.cleanup(() => {media.pause(); media.removeAttribute('src'); media.load();});
            } else {media.alt = filename; media.decoding = 'async'; contentScope.cleanup(() => media.removeAttribute('src'));}
            cover.append(media); if (onPreview) contentScope.listen(cover, 'click', onPreview);
            else {cover.setAttribute('aria-disabled', 'true'); cover.title = 'Full preview is unavailable in this view.';}
            container.append(cover);
        }
        const inner = el('div', undefined, 'file-inspector-inner'); container.append(inner);
        const header = el('div', undefined, 'file-title');
        header.append(el('h2', library?.status === 'linked' && library.title ? library.title : filename));
        const extension = file?.extension || filename.split('.').at(-1);
        header.append(el('span', file?.format || `${extension.toUpperCase()} extension`, 'file-format file-chip'));
        inner.append(header, el('p', filename, 'file-name muted'));
        const chips = el('div', undefined, 'file-chips');
        const sources = new Set(Object.values(record.prompts).filter(role => role?.status === 'resolved').map(role => role.source));
        const evidence = sources.has('execution') ? 'Execution prompt evidence' : sources.has('graph-inferred') ? 'Workflow prompt evidence' : sources.has('embedded-parameters') ? 'Embedded parameter evidence' : null;
        if (evidence) chips.append(el('span', evidence, 'file-chip file-evidence'));
        if (library?.status === 'linked' && library.category) {
            const label = library.metadata_scope === 'pair' ? 'Recipe category' : 'Positive prompt category';
            chips.append(el('span', `${label}: ${library.category}`, 'file-chip'));
        }
        if (chips.childNodes.length) inner.append(chips);
        const prompts = el('div'); inner.append(prompts);
        const blocks = mountPromptBlocks(prompts, {sender, parentScope:contentScope, includePairAction:false});
        blocks.setState({prompts:record.prompts}); contentScope.cleanup(blocks.dispose);
        if (library?.status === 'linked') {
            const vocabulary = el('div', undefined, 'file-vocabulary');
            vocabulary.append(el('p', library.metadata_scope === 'pair' ? 'Recipe tags' : 'Positive prompt tags', 'muted'));
            const tags = el('div', undefined, 'file-chips');
            for (const tag of library.tags) tags.append(el('span', tag, 'file-chip'));
            vocabulary.append(library.tags.length ? tags : el('p', 'No saved tags', 'muted')); inner.append(vocabulary);
        } else if (!library) inner.append(el('p', 'Saved vocabulary is unavailable for this inspection response.', 'muted'));
        const facts = definitionList(FACTS.map(([key, label]) => [label, factText(record.generation[key])]), 'generation-facts');
        inner.append(facts);
        const provenance = disclosure('File & provenance');
        provenance.append(el('p', 'Current file at this source; historical bytes are not verified.', 'muted'));
        if (file) provenance.append(definitionList([
            ['Source', file.source_label], ['Path', record.identity.path], ['Size', formatBytes(file.size)],
            ['Dimensions', file.width && file.height ? `${file.width} × ${file.height}` : null],
            ['Format', file.format || `${file.extension.toUpperCase()} extension`], ['Media type', file.media_type],
            ['Modified', file.modified_time !== null ? new Date(file.modified_time * 1000).toLocaleString() : null],
        ], 'file-facts'));
        else provenance.append(el('p', 'Current file observations are unavailable for this inspection response.', 'muted'));
        for (const [key, label] of FACTS) {
            const fact = record.generation[key];
            if (fact?.evidence) provenance.append(el('p', `${label}: ${fact.evidence === 'configuration' ? 'Configuration evidence' : 'Embedded parameter evidence'} · ${FACT_SOURCES[fact.source] || 'Source not recorded'}`, 'muted'));
        }
        provenance.append(el('p', record.historyTarget ? 'Saved association; not verified against current file' : library?.status === 'ambiguous' ? 'Saved association is ambiguous; no unique saved positive prompt is available.' : 'No unique saved positive prompt association is available.', 'muted'));
        if (record.download) {
            const original = el('a', 'Download original', 'workspace-link'); original.href = record.download.url;
            original.download = safeFilename(record.download.filename); provenance.append(original);
        }
        inner.append(provenance);
        const raw = disclosure('Workflow & raw metadata');
        raw.append(el('p', 'Supported embedded records only, not all EXIF or container metadata. Video records come from one selected container carrier; alternate carriers are not merged.', 'muted'));
        if (!metadata) raw.append(el('p', 'Full metadata is unavailable for this inspection response.', 'muted'));
        else {
            if (metadata.status !== 'available') raw.append(el('p', {limited:RAW_LIMIT, absent:'No supported embedded metadata was recorded in this file.', unreadable:'Embedded metadata could not be read.'}[metadata.status], 'muted'));
            for (const key of RAW_KEYS) {
                const format = metadata.record_formats[key]; if (!format) continue;
                raw.append(el('p', key, 'raw-record-name'), el('p', REPRESENTATIONS[format], 'muted'));
                if (Object.hasOwn(metadata.raw, key)) {
                    const text = el('pre'); text.tabIndex = 0; text.append(el('code', metadata.raw[key])); raw.append(text);
                }
            }
        }
        const downloads = inspectionDownloads(record);
        for (const [key, label] of [['metadata', 'Download metadata JSON'], ['workflow', 'Download workflow JSON']]) {
            const item = downloads[key], action = createButton(label);
            const group = el('div', undefined, 'file-download'); group.append(action);
            if (!item.available) {
                action.setAttribute('aria-disabled', 'true');
                const explanation = el('p', item.reason, 'muted');
                // IDs need only be unique within the current document, including its two inspectors.
                explanation.id = `file-download-${++downloadReasonId}`; action.setAttribute('aria-describedby', explanation.id); group.append(explanation);
            }
            contentScope.listen(action, 'click', () => {
                if (!item.available || contentScope.signal.aborted) return;
                const url = URL.createObjectURL(new Blob([item.bodyText], {type:item.mime}));
                let timer, released = false;
                const release = () => {if (released) return; released = true; clearTimeout(timer); URL.revokeObjectURL(url);};
                contentScope.cleanup(release);
                const anchor = el('a'); anchor.href = url; anchor.download = item.filename;
                container.append(anchor); anchor.click(); anchor.remove(); timer = setTimeout(release, 1000);
            });
            raw.append(group);
        }
        inner.append(raw);
        const footer = el('footer', undefined, 'file-inspector-footer'), roles = promptRoles(record.prompts);
        if (Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative')) {
            const pair = createSendButton('Send prompt pair'); pair.dataset.sendRole = 'pair';
            contentScope.listen(pair, 'click', () => sender.open(roles, pair)); footer.append(pair);
        }
        if (record.historyTarget) {
            const history = el('a', 'Prompt history', 'workspace-link'); history.href = record.historyTarget;
            history.prepend(createWorkspaceIcon('branch')); footer.append(history);
        }
        if (footer.childNodes.length) container.append(footer);
    }
    return {setState, dispose:() => scope.dispose()};
}
let downloadReasonId = 0;

/** Compatible standalone controller; Images owns a single shared controller instead. */
export function mountPromptInspector(container, {api, image, sender, parentScope}) {
    if (parentScope.signal.aborted) return {dispose() {}};
    const scope = childScope(parentScope), controller = createInspectionController({api});
    const view = mountFileInspector(container, {sender, parentScope:scope, onRetry:() => controller.retry()});
    scope.cleanup(controller.subscribe(view.setState)); scope.cleanup(() => controller.dispose()); scope.cleanup(view.dispose);
    controller.select(image);
    return {dispose:() => scope.dispose()};
}
