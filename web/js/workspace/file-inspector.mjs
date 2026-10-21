import { createScope, createButton } from '../shared/toolkit/core.js';
import { mountPromptBlocks, promptRoles } from './prompt-blocks.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createSendButton } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';

const RAW_KEYS = ['prompt', 'workflow', 'parameters', 'comfyassets_prompt_pairs'];
const RAW_LIMIT = 'Raw metadata exceeds the response limit. Download the original file to inspect it externally.';
const CONTAINER_WORKFLOW = 'Original workflow JSON text is unavailable for this container record. Download metadata or the original file.';
const LIMITED_WORKFLOW = 'Workflow exceeds inspection limits. Download the original file to inspect it externally.';
const REPRESENTATIONS = {
    'original-text': 'Original embedded text',
    'reserialized-json': 'Reserialized container JSON; numeric spelling/precision may differ',
    invalid: 'Invalid container record; text is unavailable',
    limited: 'Record exceeds inspection limits; text is unavailable',
};
const TEXTLESS = new Set(['invalid', 'limited']);
const FACTS = [['seed', 'Seed'], ['steps', 'Steps'], ['cfg', 'CFG'], ['sampler', 'Sampler'], ['scheduler', 'Scheduler'], ['model', 'Model']];
const FACT_SOURCES = {'api-graph': 'Embedded API configuration', workflow: 'Saved workflow configuration', 'embedded-parameters': 'Embedded generation parameters'};

function safeFilename(value) {
    return String(value || 'file').replace(/[\\/\x00-\x1f\x7f<>:"|?*]/g, '_').replace(/^\.+|[. ]+$/g, '') || 'file';
}

/**
 * One file's metadata envelope: only the bounded public evidence, never normalized graphs or container carriers.
 * Null when the inspection holds no full metadata. Shared by "Download metadata JSON" and the Images bulk export.
 */
export function metadataEnvelope(record) {
    const metadata = record?.metadata;
    if (!metadata || metadata.status === 'limited') return null;
    const raw = {}, record_formats = {}, representations = {};
    for (const key of RAW_KEYS) {
        if (Object.hasOwn(REPRESENTATIONS, metadata.record_formats?.[key])) {
            record_formats[key] = metadata.record_formats[key];
            representations[key] = REPRESENTATIONS[record_formats[key]];
            if (!TEXTLESS.has(record_formats[key]) && typeof metadata.raw?.[key] === 'string') raw[key] = metadata.raw[key];
        }
    }
    return {inspection_version:1, identity:record.identity, file:record.file, prompts:record.prompts,
        generation:record.generation, library:record.library,
        metadata:{status:metadata.status, omitted_reason:metadata.omitted_reason, raw, record_formats, representations,
            scope:'Supported embedded records only; one selected container carrier for video. Other metadata and private carrier text are excluded.'}};
}

/** The inspector's metadata and workflow downloads, each with its filename or the reason it is unavailable. */
export function inspectionDownloads(record) {
    const name = safeFilename(record?.download?.filename || record?.identity?.path?.split('/').at(-1));
    const stem = name.replace(/\.[^.]+$/, '') || 'file';
    const unavailable = (suffix, reason) => ({available:false, reason, filename:`${stem}.${suffix}.json`, bodyText:null, mime:'application/json'});
    const metadata = record?.metadata;
    const reason = metadata?.status === 'limited' ? RAW_LIMIT : 'Full metadata is unavailable for this inspection response.';
    const result = {metadata:unavailable('metadata', reason), workflow:unavailable('workflow', reason)};
    const envelope = metadataEnvelope(record);
    if (!envelope) return result;
    const {raw, record_formats} = envelope.metadata;
    result.metadata = {...result.metadata, available:true, reason:null, bodyText:JSON.stringify(envelope, null, 2)};
    if (record_formats.workflow === 'reserialized-json') result.workflow.reason = CONTAINER_WORKFLOW;
    else if (record_formats.workflow === 'limited') result.workflow.reason = LIMITED_WORKFLOW;
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
    if (fact?.status === 'not-recorded') return 'Not recorded';
    return fact?.reason === 'projection-limited' ? 'Unavailable within inspection limits' : 'Unresolved';
}
function formatBytes(value) {
    return value === null || value === undefined ? null : value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
}

/** The six facts, and the limit note their values point to when the configuration exceeded inspection limits. */
function generationFacts(record, listClass, noteClass) {
    const facts = definitionList(FACTS.map(([key, label]) => [label, factText(record.generation[key])]), listClass);
    if (!FACTS.some(([key]) => record.generation[key]?.reason === 'projection-limited')) return [facts];
    const explanation = el('p', 'Configuration exceeds inspection limits; download the original file to inspect it locally.', noteClass);
    explanation.id = `file-fact-limit-${++factReasonId}`;
    FACTS.forEach(([key], index) => {
        if (record.generation[key]?.reason === 'projection-limited') facts.children[index].querySelector('dd').setAttribute('aria-describedby', explanation.id);
    });
    return [facts, explanation];
}
// D21: the panel badge names the strongest resolved prompt source; any other source shows no badge.
const EVIDENCE_BADGES = [['execution', 'Execution recorded'], ['graph-inferred', 'Workflow inferred'], ['embedded-parameters', 'Embedded parameters']];
function evidenceBadge(prompts) {
    const sources = new Set(Object.values(prompts).filter(role => role?.status === 'resolved').map(role => role.source));
    return EVIDENCE_BADGES.find(([source]) => sources.has(source))?.[1] ?? null;
}
/** A concept chip whose visible text carries a visually hidden prefix naming what it is. */
function prefixedChip(prefix, text) {
    const chip = el('span', undefined, 'chip');
    chip.append(el('span', prefix, 'ca-visually-hidden'), text);
    return chip;
}

/**
 * One file's inspector. `variant: 'panel'` is the Images side panel in the concept's structure (app.js imageDetails
 * a400–414, D20): cover, heading, filename, D21 badges, prompt blocks, tag chips (`onTagFilter`), facts and footer.
 * `variant: 'full'` (the viewer) also shows provenance, raw records and downloads. `inline: true` means `panel`.
 */
export function mountFileInspector(container, {sender, parentScope, variant, inline = false, promptContext = '', onTagFilter, onRetry, onRefresh, onInteraction = () => {}}) {
    if (parentScope.signal.aborted) return {setState() {}, dispose() {}};
    const scope = childScope(parentScope);
    let contentScope;
    container.classList.add('file-inspector');
    scope.listen(container, 'pointerdown', onInteraction);
    scope.listen(container, 'keydown', onInteraction);
    scope.listen(container, 'click', onInteraction);
    scope.listen(container, 'wheel', onInteraction, {passive:true});
    scope.cleanup(() => {contentScope?.dispose(); container.replaceChildren(); container.classList.remove('file-inspector');});

    const panel = (variant ?? (inline ? 'panel' : 'full')) === 'panel';
    function statusView(state, parent) {
        const status = el('p', state.loading ? 'Loading file details…' : state.refreshRequired ? state.message : 'Could not read prompts from this file.', panel ? 'small' : 'file-status muted');
        status.setAttribute('role', state.error ? 'alert' : 'status'); parent.append(status);
        if (state.error && state.refreshRequired) {
            if (onRefresh) {
                const refresh = createButton(state.code === 'history-changed' ? 'Reload recorded history' : ['candidate-stale', 'capture-changed'].includes(state.code) ? 'Reload reported outputs' : 'Refresh image collection');
                contentScope.listen(refresh, 'click', onRefresh); parent.append(refresh);
            } else parent.append(el('p', 'Reopen this file from a refreshed image collection.', panel ? 'small' : 'muted'));
        } else if (state.error && onRetry) {
            const retry = createButton('Retry prompt details'); contentScope.listen(retry, 'click', onRetry); parent.append(retry);
        }
    }
    function setState(state) {
        if (scope.signal.aborted) return;
        contentScope?.dispose(); contentScope = childScope(scope);
        container.replaceChildren();
        if (panel) return renderPanel(state);
        if (state.loading || state.error) {
            statusView(state, container);
            return;
        }
        const record = state.record;
        if (!record) return;
        const {file, library, metadata} = record;
        const filename = record.identity.path.split('/').at(-1);
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
        const blocks = mountPromptBlocks(prompts, {sender, parentScope:contentScope, includePairAction:false, contextLabel:promptContext});
        blocks.setState({prompts:record.prompts}); contentScope.cleanup(blocks.dispose);
        if (library?.status === 'linked') {
            const vocabulary = el('div', undefined, 'file-vocabulary');
            vocabulary.append(el('p', library.metadata_scope === 'pair' ? 'Recipe tags' : 'Positive prompt tags', 'muted'));
            const tags = el('div', undefined, 'file-chips');
            for (const tag of library.tags) tags.append(el('span', tag, 'file-chip'));
            vocabulary.append(library.tags.length ? tags : el('p', 'No saved tags', 'muted')); inner.append(vocabulary);
        } else if (!library) inner.append(el('p', 'Saved vocabulary is unavailable for this inspection response.', 'muted'));
        inner.append(...generationFacts(record, 'generation-facts', 'generation-limit muted'));
        const provenance = disclosure('File & provenance');
        provenance.append(el('p', record.identity.kind === 'reported-location' ? 'Current file at the reported location · identity unverified' : 'Current file at this source; historical bytes are not verified.', 'muted'));
        if (record.identity.kind === 'reported-location') {
            const occurrence = record.identity.occurrence;
            provenance.append(definitionList([
                ['Recorded run', occurrence.run_id], ['Output node', occurrence.output_node_id], ['Image ordinal', String(occurrence.image_ordinal)],
            ], 'file-occurrence'));
        }
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
            const pair = createSendButton(promptContext ? `Send ${promptContext.toLowerCase()} prompt pair` : 'Send prompt pair'); pair.dataset.sendRole = 'pair';
            contentScope.listen(pair, 'click', () => sender.open(roles, pair)); footer.append(pair);
        }
        if (record.historyTarget) {
            const history = el('a', 'Prompt history', 'workspace-link'); history.href = record.historyTarget;
            history.prepend(createWorkspaceIcon('branch')); footer.append(history);
        }
        if (footer.childNodes.length) container.append(footer);
    }
    /** The concept side panel (app.js imageDetails a400–414 without `full`, D20). */
    function renderPanel(state) {
        const inner = el('div', undefined, 'detail-inner');
        if (state.loading || state.error) {container.append(inner); statusView(state, inner); return;}
        const record = state.record;
        if (!record) return;
        const {file, library} = record;
        const filename = record.identity.path.split('/').at(-1);
        const linked = library?.status === 'linked';
        const title = linked && library.title ? library.title : filename;
        if (record.download) {
            // The concept's plain cover; the card image opens the viewer (D19). Video keeps its own element.
            const video = file?.media_type === 'video';
            const cover = el(video ? 'video' : 'img', undefined, 'detail-cover'); cover.src = record.download.url;
            if (video) {
                cover.muted = true; cover.playsInline = true; cover.preload = 'metadata';
                contentScope.cleanup(() => {cover.pause(); cover.removeAttribute('src'); cover.load();});
            } else {
                cover.alt = ''; cover.decoding = 'async';
                if (file?.width && file?.height) {cover.width = file.width; cover.height = file.height;}
                contentScope.cleanup(() => cover.removeAttribute('src'));
            }
            container.append(cover);
        }
        container.append(inner);
        const heading = el('div', undefined, 'panel-heading');
        const extension = (file?.extension || filename.split('.').at(-1)).replace(/^\./, '').toUpperCase();
        heading.append(el('h2', title), file?.format ? el('span', file.format, 'chip') : prefixedChip('File extension ', extension));
        inner.append(heading, el('p', filename, 'small'));
        const badges = el('div', undefined, 'tags'), badge = evidenceBadge(record.prompts);
        if (badge) badges.append(el('span', badge, 'chip blue'));
        if (linked && library.category) badges.append(prefixedChip(library.metadata_scope === 'pair' ? 'Recipe category: ' : 'Positive prompt category: ', library.category));
        if (badges.childNodes.length) inner.append(badges);
        const prompts = el('div'); inner.append(prompts);
        const blocks = mountPromptBlocks(prompts, {sender, parentScope:contentScope, includePairAction:false, contextLabel:promptContext});
        blocks.setState({prompts:record.prompts}); contentScope.cleanup(blocks.dispose);
        if (linked && library.tags.length) {
            // D21: a chip sets the Images Tags filter, which matches the linked positive's tags; a recipe's own tags are
            // not what it matches, so they (and chips where no Tags filter exists) stay plain.
            const filterable = onTagFilter && library.metadata_scope === 'positive';
            const tags = el('div', undefined, 'tags');
            for (const tag of library.tags) {
                if (!filterable) {tags.append(el('span', tag, 'chip')); continue;}
                const chip = createButton(tag, 'chip'); contentScope.listen(chip, 'click', () => onTagFilter(tag)); tags.append(chip);
            }
            inner.append(tags);
        }
        inner.append(...generationFacts(record, 'meta-grid', 'small'));
        const footer = el('div', undefined, 'detail-footer'), roles = promptRoles(record.prompts);
        if (Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative')) {
            const pair = createButton('', 'send');
            pair.setAttribute('aria-label', `Send ${promptContext ? `${promptContext.toLowerCase()} ` : ''}prompt pair to ComfyUI: ${title}`);
            pair.append(createWorkspaceIcon('send'), ' Send to ComfyUI'); pair.dataset.sendRole = 'pair';
            contentScope.listen(pair, 'click', () => sender.open(roles, pair)); footer.append(pair);
        }
        if (record.historyTarget) {
            const history = createButton('');
            history.append(createWorkspaceIcon('branch'), ' Prompt history');
            contentScope.listen(history, 'click', () => {window.location.hash = record.historyTarget;}); footer.append(history);
        }
        if (footer.childNodes.length) container.append(footer);
    }
    return {setState, dispose:() => scope.dispose()};
}
let downloadReasonId = 0, factReasonId = 0;

/** Compatible standalone controller; Images owns a single shared controller instead. */
export function mountPromptInspector(container, {api, image, sender, parentScope}) {
    if (parentScope.signal.aborted) return {dispose() {}};
    const scope = childScope(parentScope), controller = createInspectionController({api});
    const view = mountFileInspector(container, {sender, parentScope:scope, onRetry:() => controller.retry()});
    scope.cleanup(controller.subscribe(view.setState)); scope.cleanup(() => controller.dispose()); scope.cleanup(view.dispose);
    controller.select(image);
    return {dispose:() => scope.dispose()};
}
