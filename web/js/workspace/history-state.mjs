import {occurrenceKey, reportedLocationAdapter} from './history-media.mjs';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const id = value => Number.isSafeInteger(value) && value > 0;
const count = value => Number.isSafeInteger(value) && value >= 0;
const fields = ['raw', 'effective', 'prepend', 'append'];
const times = ['queue_item_id', 'observed_dequeue_at', 'observed_callback_at', 'executor_started_at', 'terminal_event_at'];
function requireValue(valid, message = 'Invalid recorded history response') {if (!valid) throw new TypeError(message);}
function text(value, maxBytes, maxChars = Infinity) {
    return typeof value === 'string' && [...value].length <= maxChars && !/[\ud800-\udfff]/u.test(value) && new TextEncoder().encode(value).length <= maxBytes;
}
function node(value) {return text(value, 512, 128) && value.length > 0 && !value.includes('\0');}
function decimal(value) {return typeof value === 'string' && /^(?:0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= 9223372036854775807n;}
function freeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {Object.values(value).forEach(freeze); Object.freeze(value);}
    return value;
}
function envelope(value, epoch = null) {
    requireValue(object(value) && value.success === true && value.history_version === 1 && uuid(value.history_epoch));
    if (epoch !== null && value.history_epoch !== epoch) throw Object.assign(new Error('Recorded history changed'), {status:409, code:'history-changed'});
}
function pagination(value, previousCursor) {
    requireValue(typeof value.has_more === 'boolean' && (value.next_cursor === null || (text(value.next_cursor, 4096) && value.next_cursor.length > 0)) &&
        value.has_more === (value.next_cursor !== null) && (!value.has_more || value.next_cursor !== previousCursor));
}
function capture(value, output = false) {
    requireValue(object(value) && ['complete', 'partial', 'ambiguous', 'unavailable', 'conflicting', 'not_captured'].includes(value.state) &&
        (value.reason === null || text(value.reason, 128)) && (value.state === 'not_captured' ? value.format_version === null : value.format_version === 1));
    if (output) requireValue(!['ambiguous'].includes(value.state) && (value.state === 'not_captured' ? value.source === null : value.source === 'host_final_history'));
}
function summary(value) {
    capture(value, true);
    requireValue((value.retained_occurrence_count === null || (count(value.retained_occurrence_count) && value.retained_occurrence_count <= 256)) &&
        value.list_status === (value.state === 'not_captured' ? 'not_captured' : value.state === 'complete' ? 'complete' : 'incomplete') &&
        (value.state === 'not_captured') === (value.retained_occurrence_count === null));
}
function evidence(value) {
    requireValue(object(value) && ['recorded', 'inferred', 'unknown'].includes(value.provenance) &&
        ({recorded:['runtime_return', 'host_cache_output'], inferred:['graph_literal', 'graph_semantics'], unknown:['unknown']})[value.provenance].includes(value.source) &&
        (value.provenance === 'unknown' ? ['unavailable', 'unsupported', 'ambiguous', 'limit_reached', 'missing'].includes(value.reason) : value.reason === null));
}
function anchor(value) {
    requireValue(object(value) && node(value.anchor_id) && digest(value.context_id) &&
        (value.positive_snapshot_id === null || digest(value.positive_snapshot_id)) && (value.negative_snapshot_id === null || digest(value.negative_snapshot_id)));
}
function validateRun(value, window = false) {
    requireValue(object(value) && ['run_id', 'session_id', 'job_id', 'storage_epoch'].every(key => uuid(value[key])) && value.record_version === 1);
    for (const key of times) requireValue((!['queue_item_id', 'observed_dequeue_at'].includes(key) && value[key] === null) || decimal(value[key]));
    requireValue(['observed', 'succeeded', 'failed', 'interrupted', 'unknown'].includes(value.outcome) &&
        (value.reason === null || text(value.reason, 128)) && typeof value.diagnostics_omitted === 'boolean');
    const reasons = {succeeded:['terminal_success'], failed:['terminal_error'], interrupted:['terminal_interrupted'], unknown:['missing_terminal', 'conflicting_terminal', 'invalid_evidence', 'evidence_limit']};
    requireValue(value.outcome === 'observed' ? value.reason === null && value.observed_callback_at === null : value.observed_callback_at !== null && reasons[value.outcome].includes(value.reason));
    if (window) {
        requireValue(typeof value.diagnostics_available === 'boolean' && Array.isArray(value.matching_anchors) && value.matching_anchors.length <= 16);
        value.matching_anchors.forEach(anchor); capture(value.context_capture); summary(value.output_summary);
        requireValue(new Set(value.matching_anchors.map(a => a.anchor_id)).size === value.matching_anchors.length);
    }
}
function compareRun(a, b) {
    for (const key of ['observed_dequeue_at', 'session_id', 'queue_item_id', 'run_id']) {
        const av = key === 'observed_dequeue_at' || key === 'queue_item_id' ? BigInt(a[key]) : a[key];
        const bv = key === 'observed_dequeue_at' || key === 'queue_item_id' ? BigInt(b[key]) : b[key];
        if (av !== bv) return av < bv ? -1 : 1;
    }
    return 0;
}
function validateWindow(value, positiveId, cursor, epoch) {
    envelope(value, epoch); pagination(value, cursor);
    requireValue(object(value.group) && value.group.positive_id === positiveId && (value.group.raw_text_id === null || digest(value.group.raw_text_id)) &&
        value.group.relation === 'exact_frozen_raw_text' && ['uncapturable_text', 'no_matches', 'matched'].includes(value.group.scope_status) &&
        count(value.recorded_run_count) && Array.isArray(value.items) && value.items.length <= 100 && value.recorded_run_count >= value.items.length && Array.isArray(value.context_summaries));
    value.items.forEach(run => validateRun(run, true));
    requireValue(new Set(value.items.map(r => r.run_id)).size === value.items.length && value.items.every((r, i) => i === 0 || compareRun(value.items[i - 1], r) < 0));
    const branches = new Set();
    for (const s of value.context_summaries) {
        requireValue(object(s) && digest(s.context_id) && !branches.has(s.context_id) && digest(s.positive_snapshot_id) && (s.negative_snapshot_id === null || digest(s.negative_snapshot_id)));
        branches.add(s.context_id);
        const raw = s.negative_raw;
        requireValue(object(raw) && ['unknown', 'explicit_empty', 'known'].includes(raw.state) && typeof raw.truncated === 'boolean' && Array.isArray(raw.evidence) &&
            (raw.state === 'unknown' ? raw.preview === null && !raw.truncated : text(raw.preview, 512, 100) && (raw.state === 'explicit_empty' ? raw.preview === '' && !raw.truncated : raw.preview !== '')));
        for (const e of raw.evidence) {requireValue(uuid(e.run_id) && node(e.anchor_id)); evidence(e);}
    }
    requireValue((!value.has_more || value.items.length > 0) && value.items.every(r => r.matching_anchors.every(a => branches.has(a.context_id))));
    for (const s of value.context_summaries) {
        const matching = value.items.flatMap(r => r.matching_anchors.filter(a => a.context_id === s.context_id).map(a => ({...a, run_id:r.run_id})));
        requireValue(matching.length > 0 && matching.every(a => a.positive_snapshot_id === s.positive_snapshot_id && a.negative_snapshot_id === s.negative_snapshot_id) &&
            s.negative_raw.evidence.length === matching.length && new Set(s.negative_raw.evidence.map(e => JSON.stringify([e.run_id, e.anchor_id]))).size === matching.length &&
            s.negative_raw.evidence.every(e => matching.some(a => a.run_id === e.run_id && a.anchor_id === e.anchor_id)));
    }
}
function validateContext(value) {
    anchor(value);
    requireValue(uuid(value.reference_epoch) && object(value.evidence) && value.evidence.format_version === 1 && value.evidence.role_provenance === 'inferred');
    for (const role of ['positive', 'negative']) {
        const side = value[role], proof = value.evidence[role];
        requireValue((side === null) === (value[`${role}_snapshot_id`] === null));
        if (side === null) {requireValue(proof === null); continue;}
        requireValue(object(side) && object(proof));
        for (const field of fields) {
            requireValue(side[field] === null || text(side[field], 65536)); evidence(proof[field]);
            requireValue((side[field] === null) === (proof[field].provenance === 'unknown'));
        }
    }
    requireValue(['positive_prompt_id', 'negative_prompt_id', 'pair_id'].every(key => value[key] === null || id(value[key])) && object(value.navigation) &&
        ['positive_available', 'negative_available', 'pair_available'].every(key => typeof value.navigation[key] === 'boolean'));
}
function validateDetail(value, runId, epoch) {
    envelope(value, epoch); validateRun(value.run); capture(value.context_capture); summary(value.output_summary);
    requireValue(value.run.run_id === runId && Array.isArray(value.contexts) && value.contexts.length <= 16);
    value.contexts.forEach(validateContext);
    requireValue(new Set(value.contexts.map(c => c.anchor_id)).size === value.contexts.length);
}
export function frozenPromptRoles(context, mode) {
    requireValue(['raw', 'effective'].includes(mode), 'Invalid frozen prompt mode'); validateContext(context);
    return Object.fromEntries(['positive', 'negative'].map(role => {
        const value = context[role]?.[mode] ?? null, proof = context.evidence[role]?.[mode] ?? null;
        return [role, {status:value === null ? 'unresolved' : 'resolved', text:value, source:proof?.provenance === 'recorded' ? 'execution' : proof?.provenance === 'inferred' ? 'graph-inferred' : null, evidence:proof,
            message:value === null ? 'This frozen field was not recorded.' : undefined}];
    }));
}
function validateReference(r) {
    requireValue(object(r) && ['output', 'temp'].includes(r.folder_type) && ['posix', 'windows'].includes(r.path_style) && (r.root_key === null || digest(r.root_key)) &&
        text(r.filename, 1024) && r.filename && !['.', '..'].includes(r.filename) && text(r.subfolder, 2048) && !r.filename.includes('\0') && !r.subfolder.includes('\0'));
    const split = r.path_style === 'windows' ? /[/\\]/ : /\//;
    requireValue(!split.test(r.filename) && !r.subfolder.startsWith('/') && (!r.subfolder || r.subfolder.split(split).every(p => p && p !== '.' && p !== '..')) &&
        (r.path_style !== 'windows' || (!r.subfolder.startsWith('\\') && !/:/.test(r.filename + r.subfolder))));
}
function compareCoordinate(a, b) {
    const aa = new TextEncoder().encode(a.output_node_id), bb = new TextEncoder().encode(b.output_node_id);
    for (let i = 0; i < Math.min(aa.length, bb.length); i++) if (aa[i] !== bb[i]) return aa[i] - bb[i];
    return aa.length - bb.length || a.image_ordinal - b.image_ordinal;
}
function validateOutputs(value, runId, epoch, cursor, previous) {
    envelope(value, epoch); pagination(value, cursor); capture(value.capture, true);
    summary({...value.capture, retained_occurrence_count:value.retained_occurrence_count, list_status:value.list_status});
    requireValue(digest(value.capture_fingerprint) && Array.isArray(value.items) && value.items.length <= 100 && (value.retained_occurrence_count ?? 0) >= value.items.length);
    if (cursor !== null && previous.capture_fingerprint !== value.capture_fingerprint) throw Object.assign(new Error('Output capture changed'), {status:409, code:'capture-changed'});
    for (const r of value.items) {
        occurrenceKey(r.occurrence); validateReference(r.reference);
        requireValue(r.occurrence.run_id === runId && r.file_identity === 'unverified' && r.newly_generated === 'unknown' && r.context_membership === 'unknown' && r.transient === (r.reference.folder_type === 'temp'));
        const c = r.current_candidate;
        requireValue(object(c) && ['available', 'unavailable', 'error'].includes(c.status) && c.evidence_class === 'locator_candidate');
        if (c.status === 'available') {
            reportedLocationAdapter.validate(c.media);
            requireValue(c.reason === null && c.current_scope === c.media.current_scope && c.location_key === c.media.location_key && c.media.history_epoch === epoch &&
                occurrenceKey(c.media.occurrence) === occurrenceKey(r.occurrence) && ['folder_type', 'subfolder', 'filename', 'path_style', 'root_key'].every(key => c.media.reference[key] === r.reference[key]) && text(c.relative_path, 4096) && text(c.source_label, 4096) &&
                ['none', 'unique', 'ambiguous'].includes(c.gallery_match) && (c.gallery_image_id === null || id(c.gallery_image_id)));
        } else requireValue(text(c.reason, 128) && c.reason.length > 0 && !['media', 'url', 'thumbnail_url', 'inspection_url'].some(key => Object.hasOwn(c, key)));
    }
    requireValue(value.items.every((r, i) => i === 0 || compareCoordinate(value.items[i - 1].occurrence, r.occurrence) < 0));
    if (cursor !== null && previous.items.length && value.items.length) requireValue(compareCoordinate(previous.items.at(-1).occurrence, value.items[0].occurrence) < 0);
}
const emptyWindow = () => ({items:[], context_summaries:[], group:null, recorded_run_count:0, next_cursor:null, has_more:false, history_epoch:null, loading:false, error:null});
const emptyRun = () => ({detail:null, loading:false, error:null});
const emptyOutputs = () => ({items:[], capture:null, capture_fingerprint:null, retained_occurrence_count:null, list_status:null, next_cursor:null, has_more:false, loading:false, error:null});
const emptySelection = mode => ({run_id:null, anchor_id:null, context_id:null, mode, receipt:null});

/** Fixed run positions; an off-screen focused column remains in semantic order. */
export function visibleHistoryColumns(count, left, width, focusedIndex = -1) {
    requireValue(Number.isSafeInteger(count) && count >= 0, 'Invalid history column count');
    if (count <= 120) return Array.from({length:count}, (_, index) => index);
    const start = Math.max(0, Math.floor((Number.isFinite(left) ? Math.max(0, left) : 0) / 64) - 3);
    const right = (Number.isFinite(left) ? Math.max(0, left) : 0) + (Number.isFinite(width) ? Math.max(1, width) : 1);
    const end = Math.min(count - 1, Math.ceil(right / 64) - 1 + 3);
    const result = [];
    for (let index = start; index <= end; index++) result.push(index);
    if (Number.isSafeInteger(focusedIndex) && focusedIndex >= 0 && focusedIndex < count && !result.includes(focusedIndex)) {
        result.push(focusedIndex);result.sort((a, b) => a - b);
    }
    return result;
}

export function createHistoryController({api, positiveId, onChange = () => {}, restore}) {
    requireValue(id(positiveId), 'Invalid saved positive ID');
    const intent = {search:typeof restore?.search === 'string' ? restore.search : '', scrollTop:typeof restore?.scrollTop === 'number' && Number.isFinite(restore.scrollTop) ? Math.max(0, restore.scrollTop) : 0};
    let state = freeze({window:emptyWindow(), run:emptyRun(), outputs:emptyOutputs(), selection:emptySelection(restore?.mode === 'effective' ? 'effective' : 'raw'),
        parent:{detail:null, loading:false, error:null}, intent, refreshRequired:null, refreshLabel:'', validating:false, actionsEnabled:false});
    let disposed = false, failedOutput = null, runOptions = null;
    let restoreHint = uuid(restore?.runId) && (restore.anchorId === undefined || restore.anchorId === null || node(restore.anchorId)) && (restore.contextId === undefined || restore.contextId === null || digest(restore.contextId)) ? {runId:restore.runId, anchorId:restore.anchorId ?? null, contextId:restore.contextId ?? null} : null;
    const requests = {window:null, run:null, outputs:null, parent:null, validation:null};
    const generations = {window:0, run:0, outputs:0, parent:0, validation:0};
    function cancel(slot) {generations[slot]++; requests[slot]?.abort(); requests[slot] = null;}
    function begin(slot) {cancel(slot); const request = requests[slot] = new AbortController(), generation = generations[slot]; return {signal:request.signal, current:() => !disposed && requests[slot] === request && generations[slot] === generation};}
    function emit(patch) {
        if (disposed) return;
        const next = {...state, ...patch};
        next.actionsEnabled = !!next.window.history_epoch && !next.refreshRequired && !next.validating && !next.window.loading && !next.window.error && !next.run.loading && !next.run.error;
        state = freeze(next); onChange(state);
    }
    function clearOutputs() {cancel('outputs'); failedOutput = null; return emptyOutputs();}
    function clearDependents() {cancel('run'); return {run:emptyRun(), outputs:clearOutputs(), selection:emptySelection(state.selection.mode)};}
    async function validateParent() {
        const request = begin('parent'); emit({parent:{detail:null, loading:true, error:null}});
        try {
            const detail = await api.get(`/prompt_manager/library/${positiveId}`, {signal:request.signal});
            if (!request.current()) return;
            requireValue(object(detail) && object(detail.positive) && detail.positive.id === positiveId && typeof detail.positive.text === 'string');
            emit({parent:{detail:structuredClone(detail), loading:false, error:null}});
        } catch (error) {if (request.current()) emit({parent:{detail:null, loading:false, error}});}
    }
    function recover(error) {
        if (error?.status !== 409 || !['history-changed', 'capture-changed', 'candidate-stale'].includes(error.code)) return false;
        if (error.code === 'history-changed') {
            restoreHint = null; runOptions = null;
            cancel('window'); cancel('validation');
            emit({...clearDependents(), window:emptyWindow(), validating:false, refreshRequired:error.code, refreshLabel:'Reload recorded history'});
            void validateParent();
        } else {
            if (state.refreshRequired === error.code) {emit({outputs:{...state.outputs, loading:false, error}}); return true;}
            emit({outputs:clearOutputs(), selection:{...state.selection, receipt:null}, refreshRequired:error.code, refreshLabel:'Reload reported outputs'});
            void fetchOutputs(null, 40);
        }
        return true;
    }
    function mergeSummaries(earlier, later, items) {
        const map = new Map();
        for (const s of [...earlier, ...later]) {
            const previous = map.get(s.context_id);
            const evidence = new Map([...(previous?.negative_raw.evidence || []), ...s.negative_raw.evidence].map(e => [JSON.stringify([e.run_id, e.anchor_id]), e]));
            map.set(s.context_id, {...s, negative_raw:{...s.negative_raw, evidence:[...evidence.values()]}});
        }
        const ordered = new Map(); for (const r of items) for (const a of r.matching_anchors) ordered.set(a.context_id, map.get(a.context_id));
        return [...ordered.values()];
    }
    async function fetchWindow(earlier = false) {
        if (disposed || (earlier && (state.window.loading || !state.window.has_more || state.refreshRequired))) return;
        const cursor = earlier ? state.window.next_cursor : null, epoch = earlier ? state.window.history_epoch : null;
        const previous = state.window, request = begin('window');
        if (!earlier) {cancel('validation'); emit({...clearDependents(), window:emptyWindow(), validating:false, refreshRequired:null, refreshLabel:''});}
        emit({window:{...state.window, loading:true, error:null}});
        const query = new URLSearchParams({limit:'40'}); if (cursor !== null) query.set('cursor', cursor);
        try {
            const value = await api.get(`/prompt_manager/library/${positiveId}/history?${query}`, {signal:request.signal});
            if (!request.current()) return;
            validateWindow(value, positiveId, cursor, epoch);
            const page = structuredClone(value), merged = new Map();
            for (const r of [...page.items, ...(earlier ? previous.items : [])]) merged.set(r.run_id, r);
            const items = [...merged.values()].sort(compareRun);
            emit({window:{...page, items, context_summaries:mergeSummaries(page.context_summaries, earlier ? previous.context_summaries : [], items), loading:false, error:null}});
            const hint = restoreHint; restoreHint = null;
            if (hint && items.some(r => r.run_id === hint.runId)) void selectRun(hint.runId, hint);
        } catch (error) {if (request.current() && !recover(error)) emit({window:{...state.window, loading:false, error}});}
    }
    async function selectRun(runId, {anchorId = null, contextId = null} = {}) {
        if (disposed) return;
        requireValue(uuid(runId) && (anchorId === null || node(anchorId)) && (contextId === null || digest(contextId)), 'Invalid run selection');
        requireValue(state.window.history_epoch !== null && !state.refreshRequired, 'Load recorded history before selecting a run');
        cancel('validation');
        runOptions = {runId, anchorId, contextId};
        const request = begin('run'), epoch = state.window.history_epoch;
        emit({validating:false, run:{...emptyRun(), loading:true}, outputs:clearOutputs(), selection:{...emptySelection(state.selection.mode), run_id:runId}});
        try {
            const value = await api.get(`/prompt_manager/history/runs/${runId}?${new URLSearchParams({epoch})}`, {signal:request.signal});
            if (!request.current()) return;
            validateDetail(value, runId, epoch);
            const detail = structuredClone(value);
            const selected = detail.contexts.find(c => (anchorId === null || c.anchor_id === anchorId) && (contextId === null || c.context_id === contextId));
            requireValue((anchorId === null && contextId === null) || !!selected, 'Recorded context is unavailable');
            emit({run:{detail, loading:false, error:null}, selection:{...state.selection, anchor_id:selected?.anchor_id ?? null, context_id:selected?.context_id ?? null}});
        } catch (error) {if (request.current() && !recover(error)) emit({run:{detail:null, loading:false, error}});}
    }
    function cancelDependentRead() {cancel('outputs'); emit({outputs:{...state.outputs, loading:false}});}
    async function fetchOutputs(cursor, limit) {
        if (disposed || !state.run.detail || state.refreshRequired === 'history-changed') return;
        const runId = state.selection.run_id, epoch = state.window.history_epoch, previous = state.outputs, request = begin('outputs');
        const attempt = {cursor, limit};
        emit({outputs:{...previous, loading:true, error:null}});
        const query = new URLSearchParams({epoch, limit:String(limit)}); if (cursor !== null) query.set('cursor', cursor);
        try {
            const value = await api.get(`/prompt_manager/history/runs/${runId}/outputs?${query}`, {signal:request.signal});
            if (!request.current()) return;
            validateOutputs(value, runId, epoch, cursor, previous);
            const page = structuredClone(value), items = cursor === null ? page.items : [...previous.items, ...page.items];
            requireValue(items.length <= 256 && (page.retained_occurrence_count ?? 0) >= items.length && (page.has_more ? page.items.length > 0 && (page.retained_occurrence_count ?? 0) > items.length : (page.retained_occurrence_count ?? 0) === items.length));
            failedOutput = null;
            emit({outputs:{...page, items, loading:false, error:null}, refreshRequired:null, refreshLabel:''});
        } catch (error) {
            if (!request.current() || recover(error)) return;
            failedOutput = attempt;
            if (error?.status === 422 && error.code === 'response-limit' && error.recovery?.action === 'retry-outputs' && Number.isSafeInteger(error.recovery.limit) && error.recovery.limit >= 1 && error.recovery.limit < limit) failedOutput = {cursor, limit:error.recovery.limit};
            emit({outputs:{...previous, loading:false, error}});
        }
    }
    async function revalidate() {
        if (disposed || !state.window.history_epoch || state.refreshRequired) return;
        const request = begin('validation'), epoch = state.window.history_epoch, runId = state.selection.run_id;
        cancel('window'); cancel('run'); cancel('outputs');
        emit({validating:true, window:{...state.window, loading:false}, run:{...state.run, loading:false}, outputs:{...state.outputs, loading:false}});
        try {
            const value = await api.get(runId ? `/prompt_manager/history/runs/${runId}?${new URLSearchParams({epoch})}` : `/prompt_manager/library/${positiveId}/history?limit=40`, {signal:request.signal});
            if (!request.current()) return;
            if (runId) validateDetail(value, runId, epoch); else validateWindow(value, positiveId, null, epoch);
            const hadOutputs = state.outputs.capture !== null;
            emit({validating:false, ...(runId ? {run:{detail:structuredClone(value), loading:false, error:null}} : {}),
                ...(hadOutputs ? {outputs:clearOutputs(), selection:{...state.selection, receipt:null}} : {})});
            if (hadOutputs) return fetchOutputs(null, 40);
        } catch (error) {if (request.current() && !recover(error)) emit({validating:false, refreshRequired:'revalidation-failed', refreshLabel:'Reload recorded history', window:{...state.window, error}});}
    }
    return {
        get state() {return state;}, load:() => fetchWindow(), refresh:() => fetchWindow(), loadEarlier:() => fetchWindow(true), selectRun,
        selectContext(anchorId) {
            if (disposed) return;
            requireValue(node(anchorId), 'Invalid context anchor'); const c = state.run.detail?.contexts.find(c => c.anchor_id === anchorId); requireValue(!!c, 'Recorded context is unavailable');
            cancelDependentRead(); emit({selection:{...state.selection, anchor_id:c.anchor_id, context_id:c.context_id}});
        },
        setMode(mode) {if (disposed) return; requireValue(['raw', 'effective'].includes(mode), 'Invalid frozen prompt mode'); if (mode === state.selection.mode) return; cancelDependentRead(); emit({selection:{...state.selection, mode}});},
        loadOutputs() {if (disposed || state.outputs.loading || (state.outputs.capture !== null && !state.outputs.has_more)) return; return fetchOutputs(state.outputs.next_cursor, 40);},
        selectReceipt(occurrence) {
            if (disposed) return;
            const key = occurrenceKey(occurrence); requireValue(!state.refreshRequired && !state.validating && state.outputs.items.some(r => occurrenceKey(r.occurrence) === key), 'Reported output is unavailable');
            cancelDependentRead(); emit({selection:{...state.selection, receipt:{...occurrence}}});
        },
        retryRun() {if (!disposed && !state.refreshRequired && runOptions) return selectRun(runOptions.runId, runOptions);},
        retryOutputs() {if (!disposed && !state.refreshRequired && failedOutput) return fetchOutputs(failedOutput.cursor, failedOutput.limit);},
        revalidate,
        dispose() {if (disposed) return; disposed = true; Object.keys(requests).forEach(cancel); failedOutput = null; runOptions = null; state = freeze({...state, ...clearDependents(), window:emptyWindow(), parent:{detail:null, loading:false, error:null}, actionsEnabled:false});},
    };
}
