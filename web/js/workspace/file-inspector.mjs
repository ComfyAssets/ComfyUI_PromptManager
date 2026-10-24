import { createScope, createButton } from '../shared/toolkit/core.js';
import { mountPromptBlocks, promptRoles } from './prompt-blocks.mjs';
import { createInspectionController } from './inspection-state.mjs';
import { createSendButton } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { navigateTo } from './state.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { openCopyFallback } from './workspace-dialogs.mjs';
import { displayPreferences } from './preferences.mjs';

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
const REVEAL_URL = '/prompt_manager/images/reveal';
// D38 Show in folder: the route's answers, in the maintainer's words.
const REVEAL_OPENED = 'Opened the folder on this computer.';
const REVEAL_NOT_LOCAL = 'Show in folder works only in a browser on the computer running ComfyUI.';
const REVEAL_UNAVAILABLE = 'No file manager is available on this computer.';
const REVEAL_BUSY = 'The folder is already opening.';
const REVEAL_FAILED = 'The folder could not be opened. Try again.';
const FACTS = [['seed', 'Seed'], ['steps', 'Steps'], ['cfg', 'CFG'], ['sampler', 'Sampler'], ['scheduler', 'Scheduler'], ['model', 'Model']];

// D117 (LI-4): the legacy metadata page's COPY ALL (web/metadata.html copyAllData), its lines and placeholders.
const SUMMARY_LINES = [['Checkpoint', 'model'], ['Positive Prompt', 'positive'], ['Negative Prompt', 'negative'], ['Steps', 'steps'],
    ['CFG Scale', 'cfg'], ['Sampler', 'sampler'], ['Seed', 'seed']];
const SUMMARY_MISSING = {positive:'No prompt found', negative:'No negative prompt found'};

/**
 * The one-click settings summary (D117): the legacy COPY ALL text for what this inspector shows. A confirmed prompt
 * and a known fact are copied verbatim; anything else, an explicitly empty prompt included, takes the legacy page's
 * placeholder, as its parse does (`hasValue` treats '' as absent).
 */
export function settingsSummary(record) {
    const roles = promptRoles(record?.prompts);
    const value = key => {
        if (Object.hasOwn(SUMMARY_MISSING, key)) return roles[key] ? roles[key] : SUMMARY_MISSING[key];
        const fact = record?.generation?.[key];
        return fact?.status === 'known' && typeof fact.value === 'string' && fact.value !== '' ? fact.value : 'Unknown';
    };
    return SUMMARY_LINES.map(([label, key]) => `${label}: ${value(key)}`).join('\n');
}

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

/** The reveal body for a listed file's identity (`root_index`, `root_scope`, `path`), else null (D38). */
export function revealTarget(identity) {
    if (identity?.kind !== 'rooted-file') return null;
    const {root_index, root_scope, path} = identity;
    if (!Number.isInteger(root_index) || root_index < 0 || typeof root_scope !== 'string' || !/^[0-9a-f]{64}$/.test(root_scope) ||
        typeof path !== 'string' || !path) return null;
    return {root_index, root_scope, path};
}
/** The toast for a reveal outcome: null is success, otherwise the request's error (D38). */
export function revealOutcome(error) {
    if (error === null) return REVEAL_OPENED;
    if (error?.status === 403 && error.code === 'not-local') return REVEAL_NOT_LOCAL;
    if (error?.status === 501) return REVEAL_UNAVAILABLE;
    if (error?.status === 429 && error.code === 'busy') return REVEAL_BUSY;
    return REVEAL_FAILED;
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
        // A value is its text, or a prepared `dd` (one a caller keeps to add to, such as D38's Path).
        const row = el('div'); row.append(el('dt', label), typeof value === 'object' && value !== null ? value : el('dd', value ?? 'Not available')); list.append(row);
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
// A prompt that left out a run-time PromptManager part (D125) or a later stage's added conditioning (D126) is still
// inferred from the workflow.
const EVIDENCE_BADGES = [['execution', 'Execution recorded'], ['graph-inferred', 'Workflow inferred'], ['runtime-parts-omitted', 'Workflow inferred'],
    ['added-conditioning', 'Workflow inferred'], ['runtime-parts-omitted-and-added-conditioning', 'Workflow inferred'], ['embedded-parameters', 'Embedded parameters']];
function evidenceBadge(prompts) {
    const sources = new Set(Object.values(prompts).filter(role => role?.status === 'resolved').map(role => role.source));
    return EVIDENCE_BADGES.find(([source]) => sources.has(source))?.[1] ?? null;
}
/** The file's extension without its leading dot, upper case ("PNG"). */
function extensionOf(record) {
    return (record.file?.extension || record.identity.path.split('.').at(-1)).replace(/^\./, '').toUpperCase();
}
/**
 * The chip row after the prompt blocks (D88): the file's current image tags first, then D21's linked tags not already
 * shown (case-insensitive). With `filterable` (a Tags filter exists, as in the Images panel) every image tag sets it, and
 * a linked tag only when it is the positive's own (D21); otherwise, as in the viewer, every chip is plain.
 */
export function inspectorTagChips(record, {filterable}) {
    const shown = new Set(), chips = [];
    const add = (names, filter) => {
        for (const name of names) {
            const key = name.toLowerCase();
            if (shown.has(key)) continue;
            shown.add(key); chips.push({name, filter});
        }
    };
    if (record.imageTags?.state === 'current') add(record.imageTags.tags, filterable);
    const {library} = record;
    if (library?.status === 'linked') add(library.tags, filterable && library.tags_scope === 'positive');
    return chips;
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
 * `variant: 'full'` (the viewer's `#viewer-details`, imageDetails(i, true)) leaves out the cover and adds "File &
 * provenance" and "Workflow & raw metadata" with the downloads. `inline: true` means `panel`; neither means `full`.
 */
/** D124: the inspector's line while "Load metadata on selection" is off. */
export const DEFERRED_TEXT = 'Metadata loads when you ask for it.';

export function mountFileInspector(container, {api, sender, parentScope, variant, inline = false, promptContext = '', onTagFilter, onRetry, onRefresh, onLoad, onInteraction = () => {}, copyFallback = openCopyFallback}) {
    if (parentScope.signal.aborted) return {setState() {}, dispose() {}};
    const scope = childScope(parentScope);
    let contentScope;
    container.classList.add('file-inspector');
    scope.listen(container, 'pointerdown', onInteraction);
    scope.listen(container, 'keydown', onInteraction);
    scope.listen(container, 'click', onInteraction);
    scope.listen(container, 'wheel', onInteraction, {passive:true});
    scope.cleanup(() => {contentScope?.dispose(); container.replaceChildren(); container.classList.remove('file-inspector'); container.removeAttribute('aria-busy');});

    const full = (variant ?? (inline ? 'panel' : 'full')) === 'full';
    function statusView(state, parent) {
        const status = el('p', state.loading ? 'Loading file details…' : state.refreshRequired ? state.message : 'Could not read prompts from this file.', 'small');
        status.setAttribute('role', state.error ? 'alert' : 'status'); parent.append(status);
        if (state.error && state.refreshRequired) {
            if (onRefresh) {
                const refresh = createButton(state.code === 'history-changed' ? 'Reload recorded history' : ['candidate-stale', 'capture-changed'].includes(state.code) ? 'Reload reported outputs' : 'Refresh image collection');
                contentScope.listen(refresh, 'click', onRefresh); parent.append(refresh);
            } else parent.append(el('p', 'Reopen this file from a refreshed image collection.', 'small'));
        } else if (state.error && onRetry) {
            const retry = createButton('Retry prompt details'); contentScope.listen(retry, 'click', onRetry); parent.append(retry);
        }
    }
    function setState(state) {
        if (scope.signal.aborted) return;
        contentScope?.dispose(); contentScope = childScope(scope);
        container.replaceChildren();
        // A local read resolves on this thread in bounded steps; the busy state stays until its answer lands.
        if (state.loading) container.setAttribute('aria-busy', 'true'); else container.removeAttribute('aria-busy');
        render(state);
    }
    /**
     * The concept's imageDetails (app.js a400–414). The side panel has the cover, `.detail-inner` and `.detail-footer`;
     * the viewer (`full`) has a class-less wrapper that adds the two disclosures, with the footer buttons after it (D20).
     */
    function render(state) {
        const inner = el('div', undefined, full ? undefined : 'detail-inner');
        if (state.loading || state.error) {container.append(inner); statusView(state, inner); return;}
        // D124: "Load metadata on selection" is off, so the file's details wait for an explicit request.
        if (state.deferred) {
            container.append(inner);
            const status = el('p', DEFERRED_TEXT, 'small'); status.setAttribute('role', 'status'); inner.append(status);
            if (onLoad) {const load = createButton('Load metadata'); contentScope.listen(load, 'click', onLoad); inner.append(load);}
            return;
        }
        const record = state.record;
        if (!record) return;
        const {file, library} = record;
        const filename = record.identity.path.split('/').at(-1);
        const linked = library?.status === 'linked';
        const title = linked && library.title ? library.title : filename;
        if (record.download && !full) container.append(cover(record));
        container.append(inner);
        const heading = el('div', undefined, 'panel-heading');
        heading.append(el('h2', title), file?.format ? el('span', file.format, 'chip') : prefixedChip('File extension ', extensionOf(record)));
        inner.append(heading, el('p', filename, 'small'));
        const badges = el('div', undefined, 'tags'), badge = evidenceBadge(record.prompts);
        if (badge) badges.append(el('span', badge, 'chip blue'));
        if (linked && library.category) badges.append(prefixedChip(library.category_scope === 'pair' ? 'Recipe category: ' : 'Positive prompt category: ', library.category));
        if (badges.childNodes.length) inner.append(badges);
        const prompts = el('div'); inner.append(prompts);
        const blocks = mountPromptBlocks(prompts, {sender, parentScope:contentScope, includePairAction:false, contextLabel:promptContext, title});
        blocks.setState({prompts:record.prompts}); contentScope.cleanup(blocks.dispose);
        // D21: a chip sets the Images Tags filter, which matches the linked positive's tags; a recipe's own tags are
        // not what it matches, so they (and chips where no Tags filter exists, as in the viewer) stay plain. With the
        // D39 fallback a recipe without tags shows its positive's, which are filter buttons again. D88: the file's own
        // image tags lead the row and, where a Tags filter exists, always set it (it matches them too).
        const chips = inspectorTagChips(record, {filterable:!!onTagFilter});
        if (chips.length) {
            const tags = el('div', undefined, 'tags');
            for (const {name, filter} of chips) {
                if (!filter) {tags.append(el('span', name, 'chip')); continue;}
                const chip = createButton(name, 'chip'); contentScope.listen(chip, 'click', () => onTagFilter(name)); tags.append(chip);
            }
            inner.append(tags);
        }
        inner.append(...generationFacts(record, 'meta-grid', 'small'));
        if (full) inner.append(summaryButton(record), provenance(record, badge), rawMetadata(record));
        const footer = full ? container : el('div', undefined, 'detail-footer'), roles = promptRoles(record.prompts);
        if (Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative')) {
            const pair = createButton('', 'send');
            pair.setAttribute('aria-label', `Send ${promptContext ? `${promptContext.toLowerCase()} ` : ''}prompt pair to ComfyUI: ${title}`);
            pair.append(createWorkspaceIcon('send'), ' Send to ComfyUI'); pair.dataset.sendRole = 'pair';
            contentScope.listen(pair, 'click', () => sender.open(roles, pair)); footer.append(pair);
        }
        if (record.historyTarget) {
            const history = createButton('');
            history.append(createWorkspaceIcon('branch'), ' Prompt history');
            // The concept's go(): the viewer closes, and a link to the route already shown re-renders it.
            contentScope.listen(history, 'click', () => {
                document.querySelector('dialog#viewer-dialog[open]')?.close();
                navigateTo(record.historyTarget);
            });
            footer.append(history);
        }
        if (!full && footer.childNodes.length) container.append(footer);
    }
    /**
     * D117 (LI-4): "Copy settings summary" after the six facts, the legacy COPY ALL. It copies as the concept's prompt
     * copy does: the shared toast, or the select-to-copy dialog where the clipboard is unavailable or refused.
     */
    function summaryButton(record) {
        const button = createButton('Copy settings summary', 'settings-summary'), text = settingsSummary(record);
        contentScope.listen(button, 'click', async () => {
            try {
                if (!navigator.clipboard) throw new Error('Clipboard requires a secure context');
                await navigator.clipboard.writeText(text);
                if (!contentScope.signal.aborted) showWorkspaceToast('Settings summary copied.');
            } catch {
                if (!contentScope.signal.aborted) copyFallback(text, {scope:contentScope, trigger:button, title:'Copy settings summary'});
            }
        });
        return button;
    }
    /**
     * The concept's plain cover; the card image opens the viewer (D19). #249: it is the file's preview (a video's
     * is a JPEG frame) whenever the listing gave one; without one the original, and a video keeps its own element.
     */
    function cover(record) {
        const {file} = record, preview = record.preview?.url, isVideo = file?.media_type === 'video';
        if (isVideo && !preview) return videoCover(record);
        const node = el('img', undefined, 'detail-cover'); node.src = preview || record.download.url;
        node.alt = ''; node.decoding = 'async';
        if (file?.width && file?.height) {node.width = file.width; node.height = file.height;}
        contentScope.cleanup(() => node.removeAttribute('src'));
        // #249: a video whose preview cannot be loaded swaps back to its own element.
        if (isVideo) contentScope.listen(node, 'error', () => { if (node.isConnected) node.replaceWith(videoCover(record)); }, {once:true});
        return node;
    }
    function videoCover(record) {
        const node = el('video', undefined, 'detail-cover'); node.src = record.download.url;
        node.muted = true; node.playsInline = true; node.preload = 'metadata';
        contentScope.cleanup(() => {node.pause(); node.removeAttribute('src'); node.load();});
        return node;
    }
    /** "File & provenance": the file facts, the D21 prompt evidence, the occurrence and the saved association (D66 for a local file). */
    function provenance(record, badge) {
        const {file, library} = record, section = disclosure('File & provenance');
        const reported = record.identity.kind === 'reported-location', local = record.identity.kind === 'local-file';
        // D66: a file chosen in this browser has no server path, no folder to show and no saved association.
        section.append(el('p', reported ? 'Current file at the reported location · identity unverified' : local ? 'Selected local file · read in this browser' :
            'Current file at this source; historical bytes are not verified.', 'small'));
        const path = el('dd', record.identity.path ?? 'Not available');
        // System › Appearance "Show file paths" off: the Path row is left out; nothing else changes.
        const showPath = !local && displayPreferences()?.showFilePaths !== false;
        const facts = file && definitionList([
            ['Source', file.source_label], ...(showPath ? [['Path', path]] : []), ['Size', formatBytes(file.size)],
            ['Dimensions', file.width && file.height ? `${file.width} × ${file.height}` : null],
            ['Format', file.format || `${extensionOf(record)} extension`], ['Media type', file.media_type],
            ['Modified', file.modified_time !== null ? new Date(file.modified_time * 1000).toLocaleString() : null],
        ], 'meta-grid');
        if (facts) {
            // D38: "Show in folder" follows the Path value.
            const target = api ? revealTarget(record.identity) : null;
            section.append(facts);
            if (target && showPath) path.append(' ', revealButton(target));
            else if (target) section.append(revealButton(target));
        } else section.append(el('p', 'Current file observations are unavailable for this inspection response.', 'small'));
        // The concept's "Prompt evidence: execution recorded" line, in the D21 badge's words; no badge, no line.
        if (badge) section.append(el('p', `Prompt evidence: ${badge.toLowerCase()}`, 'small'));
        if (reported) {
            const occurrence = record.identity.occurrence;
            section.append(definitionList([
                ['Recorded run', occurrence.run_id], ['Output node', occurrence.output_node_id], ['Image ordinal', String(occurrence.image_ordinal)],
            ], 'meta-grid'));
        }
        section.append(el('p', local ? 'Local file; no saved library association was checked.' : record.historyTarget ? 'Saved association; not verified against current file' : library?.status === 'ambiguous' ? 'Saved association is ambiguous; no unique saved positive prompt is available.' : 'No unique saved positive prompt association is available.', 'small'));
        if (record.download) {
            const original = el('a', 'Download original'); original.href = record.download.url;
            original.download = safeFilename(record.download.filename); section.append(original);
        }
        return section;
    }
    /** D38: "Show in folder" right after the Path value, for a listed file only; the server opens its file manager. */
    function revealButton(target) {
        const button = createButton('Show in folder');
        button.setAttribute('aria-label', `Show ${target.path.split('/').at(-1)} in folder`);
        let pending = false;
        contentScope.listen(button, 'click', async () => {
            if (pending) return;
            pending = true; button.setAttribute('aria-busy', 'true');
            let error = null;
            try {
                await api.post(REVEAL_URL, target, {signal:contentScope.signal});
            } catch (failure) {
                error = failure ?? {};
            }
            pending = false; button.removeAttribute('aria-busy');
            if (!contentScope.signal.aborted) showWorkspaceToast(revealOutcome(error));
        });
        return button;
    }
    /** "Workflow & raw metadata": each supported record (`pre[tabindex=0] > code`), then the two downloads. */
    function rawMetadata(record) {
        const {metadata} = record, section = disclosure('Workflow & raw metadata');
        section.append(el('p', 'Supported embedded records only, not all EXIF or container metadata. Video records come from one selected container carrier; alternate carriers are not merged.', 'small'));
        if (!metadata) section.append(el('p', 'Full metadata is unavailable for this inspection response.', 'small'));
        else {
            if (metadata.status !== 'available') section.append(el('p', {limited:RAW_LIMIT, absent:'No supported embedded metadata was recorded in this file.', unreadable:'Embedded metadata could not be read.'}[metadata.status], 'small'));
            for (const key of RAW_KEYS) {
                const format = metadata.record_formats[key]; if (!format) continue;
                const name = el('p', undefined, 'small'); name.append(el('strong', key));
                section.append(name, el('p', REPRESENTATIONS[format], 'small'));
                if (Object.hasOwn(metadata.raw, key)) {
                    const text = el('pre'); text.tabIndex = 0; text.append(el('code', metadata.raw[key])); section.append(text);
                }
            }
        }
        const downloads = inspectionDownloads(record);
        for (const [key, label] of [['metadata', 'Download metadata JSON'], ['workflow', 'Download workflow JSON']]) {
            const item = downloads[key], action = createButton(label);
            section.append(action);
            if (!item.available) {
                action.setAttribute('aria-disabled', 'true');
                const explanation = el('p', item.reason, 'small');
                // IDs need only be unique within the current document, including its two inspectors.
                explanation.id = `file-download-${++downloadReasonId}`; action.setAttribute('aria-describedby', explanation.id); section.append(explanation);
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
        }
        return section;
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
