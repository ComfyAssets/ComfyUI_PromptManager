// Candidate selection for local-resolver.mjs (#249 local Inspect, PR L3 Task 9): ports of
// utils/prompt_pair_resolver.py `_GraphResolver` `stage`, `earlier_stages`, `added_conditioning`, `composite_of`,
// `upstream_stages`, `final_stages`, `continued_stages`, `file_stages`, `non_file_outputs`, `producers_of`,
// `consumers`, `reaches_output`, `lineage_predecessor`, `latent_stages` and `part`, plus `_left_out`, `_outcome_key`,
// `_recipe_key`, `_combined_positive` and `_shared_pair` (#270). Pass-through lineage: a sampler whose latent continues
// another's through `lineage` nodes is compared with it. Each function takes the resolver whose budgets and caches it
// spends, so the order of work, and with it every limit, is the server's. No DOM.
import { isDict, pyGet, pyStr } from './py-json.mjs';
import {
    ADDED_CONDITIONING_REASON, ADDED_CONDITIONING_SOURCE, DETAILER, IDENTICAL_BRANCHES_REASON,
    MULTISTAGE_UNATTRIBUTED_REASON, RUNTIME_AND_ADDED_SOURCE, RUNTIME_PARTS_SOURCE, Unresolved, isLink, rerouteValue, result,
    slotKey,
} from './local-resolver-support.mjs';

const roleText = side => side !== null && typeof side === 'object' ? side.text : null;
const storageText = side => side !== null && typeof side === 'object' ? side.storage_text : null;
/** `_left_out`: the omitting PromptManagers and the added-by composite, so a partial text never equals a complete one. */
function leftOut(side) {
    const provenance = side !== null && typeof side === 'object' ? side.provenance : null;
    if (provenance === null || typeof provenance !== 'object') return [];
    return [provenance.omitted_parts ?? [], provenance.added_by ?? null];
}
/** `_outcome_key`: what a reader sees of a result, the state, the two role texts and what each leaves out. */
const outcomeParts = item => [item.state, roleText(item.positive), roleText(item.negative), leftOut(item.positive), leftOut(item.negative)];
export const outcomeKey = item => JSON.stringify(outcomeParts(item));
/** `_recipe_key`: the outcome plus each side's stored recipe. */
const recipeKey = item => JSON.stringify([...outcomeParts(item), storageText(item.positive), storageText(item.negative)]);

/** `_shared_pair`: the one pair every sampler branch resolved to, or null (#270). */
export function sharedPair(candidates) {
    const first = candidates[0];
    if (first.state !== 'resolved') return null;
    if (new Set(candidates.map(recipeKey)).size !== 1) return null;
    return result('resolved', { positive: first.positive, negative: first.negative, reason: IDENTICAL_BRANCHES_REASON, candidates });
}

/** `_combined_positive`: whether a stage failed only because its positive combines texts. */
const combinedPositive = item => item.reason_code === 'composite' && item.positive === null;

/** `stage`: one sampler's result, compared with the stages it continues (detailer image, latent lineage). */
export function stage(resolver, samplerId, later) {
    if (later.size >= resolver.maxDepth) return result('unresolved', { samplerId, reason: 'Resolution depth limit reached', reasonCode: 'depth-limit' });
    const own = resolver.sides(samplerId);
    const combined = own.state === 'unresolved' && combinedPositive(own);
    if (own.state === 'unresolved' && !combined) return own;
    let upstream;
    try {
        upstream = earlierStages(resolver, samplerId);
        if (upstream.some(nodeId => later.has(nodeId))) throw new Unresolved('Cyclic sampler stage ancestry', 'malformed');
    } catch (error) {
        if (!(error instanceof Unresolved)) throw error;
        if (combined) return own;
        return result('unresolved', { samplerId, reason: error.message, reasonCode: error.code, node: error.node });
    }
    if (!upstream.length) return own;
    const deeper = new Set([...later, samplerId]);
    const stages = upstream.map(nodeId => stage(resolver, nodeId, deeper));
    const blocked = stages.find(item => item.state === 'unresolved');
    if (combined) return blocked !== undefined ? own : addedConditioning(resolver, samplerId, stages) ?? own;
    // A stage that cannot be read cannot be compared: never pick one.
    if (blocked !== undefined) return result('unresolved', { samplerId, reason: blocked.reason, reasonCode: blocked.reason_code, node: blocked.node });
    const flat = [];
    for (const item of [own, ...stages]) flat.push(...(item.state === 'ambiguous' ? item.candidates : [item]));
    if (new Set(flat.map(outcomeKey)).size === 1) return own;
    return result('ambiguous', { samplerId, reason: 'Sampler stages have different prompts', candidates: flat, reasonCode: 'multistage-differs' });
}

/** `earlier_stages`: a detailer's upstream samplers, or the one sampler a latent continues (cached walk). */
function earlierStages(resolver, samplerId) {
    const [name, inputs] = resolver.node(samplerId);
    const entry = resolver.semantics.samplers.get(name);
    if (entry.strategy === DETAILER) return upstreamStages(resolver, samplerId, pyGet(inputs, entry.image_stage));
    const walked = resolver.latentWalks.get(samplerId);
    if (walked === undefined || walked[2] === null) return [];
    return [walked[2]];
}

/** `added_conditioning`: the earlier stages' shared pair when this stage's positive only adds to it. */
function addedConditioning(resolver, samplerId, stages) {
    const flat = [];
    for (const item of stages) flat.push(...(item.state === 'ambiguous' ? item.candidates : [item]));
    const base = flat[0];
    if (base.state !== 'resolved' || new Set(flat.map(outcomeKey)).size !== 1) return null;
    const [name, inputs] = resolver.node(samplerId);
    const entry = resolver.semantics.samplers.get(name);
    if (entry.strategy === 'custom-advanced') return null;
    const conditioning = entry.conditioning;
    let negative, composite;
    try {
        const found = compositeOf(resolver, pyGet(inputs, conditioning.positive));
        if (found === null) return null;
        let parts, depth;
        [parts, depth, composite] = found;
        const texts = parts.map(part => resolver.text(part, 'conditioning', depth)[0]);
        if (!texts.includes(base.positive.text)) return null;
        if (!inputs.has(conditioning.negative)) return null;
        negative = resolver.side(inputs.get(conditioning.negative), samplerId);
    } catch (error) {
        if (!(error instanceof Unresolved)) throw error;
        return null;
    }
    if (negative.text !== base.negative.text) return null;
    // D126: the positive does not include what the later stage added.
    const provenance = { ...base.positive.provenance };
    provenance.text_source = provenance.text_source === RUNTIME_PARTS_SOURCE ? RUNTIME_AND_ADDED_SOURCE : ADDED_CONDITIONING_SOURCE;
    provenance.added_by = composite;
    const positive = { ...base.positive, provenance };
    return result('resolved', { positive, negative: base.negative, samplerId: base.sampler_id, reason: ADDED_CONDITIONING_REASON });
}

/** `composite_of`: [part values, depth, node ID] of the `additive` composite a conditioning input reaches, or null. */
function compositeOf(resolver, value) {
    let depth = 0;
    while (isLink(value) && depth < resolver.maxDepth) {
        const nodeId = String(value[0]), slot = value[1];
        const [name, inputs] = resolver.node(nodeId);
        depth += 1;
        if (name === 'Reroute' && slot === 0) { value = rerouteValue(inputs); continue; }
        const forward = resolver.semantics.conditioning.get(slotKey(name, slot));
        if (forward !== undefined && forward.strategy === 'passthrough') { value = pyGet(inputs, forward.input); continue; }
        // `text` follows a composite only at output slot 0, so a failed `composite` positive reached it there.
        const composite = resolver.semantics.composites.get(name);
        if (composite === undefined || composite.additive !== true) return null;
        return [composite.inputs.map(part => pyGet(inputs, part)), depth, nodeId];
    }
    return null;
}

/** `upstream_stages`: samplers reached from a detailer's image input, breadth first; each node draws a visit. */
function upstreamStages(resolver, origin, value) {
    if (!isLink(value)) throw new Unresolved('Expected an image link', 'malformed');
    const found = [], start = String(value[0]), queue = [[start, 0]], queued = new Set([start]);
    for (let head = 0; head < queue.length; head++) {
        const [nodeId, depth] = queue[head];
        if (nodeId === origin) throw new Unresolved('Cyclic sampler stage ancestry', 'malformed');
        if (depth >= resolver.maxDepth) throw new Unresolved('Resolution depth limit reached', 'depth-limit');
        resolver.budget.spend();
        const [name, inputs] = resolver.node(nodeId);
        if (resolver.stageClasses.has(name)) { found.push(nodeId); continue; }
        if (resolver.stageShaped(name, inputs)) throw resolver.unsupported('Unsupported sampler stage ancestry', nodeId);
        resolver.enqueue(queue, queued, inputs, depth + 1);
    }
    return found;
}

const strategyOf = (resolver, nodeId) => resolver.semantics.samplers.get(resolver.graph.get(nodeId).get('class_type')).strategy;

/** `final_stages`: drop samplers that only feed detailers (each is judged in that chain). */
export function finalStages(resolver, samplerIds) {
    samplerIds = fileStages(resolver, samplerIds);
    const detailers = samplerIds.filter(nodeId => strategyOf(resolver, nodeId) === DETAILER);
    const continued = samplerIds.length > 1 ? continuedStages(resolver, samplerIds) : [];
    if (!detailers.length && !continued.length) return samplerIds;
    let reverse;
    try { reverse = consumers(resolver); } catch (error) {
        if (!(error instanceof Unresolved)) throw error;
        return samplerIds;
    }
    const absorbed = new Set();
    for (const detailer of detailers) {
        resolver.budget.reset(resolver.perCandidateVisits);
        try {
            const [name, inputs] = resolver.node(detailer);
            const image = pyGet(inputs, resolver.semantics.samplers.get(name).image_stage);
            for (const nodeId of upstreamStages(resolver, detailer, image)) {
                if (!reachesOutput(resolver, nodeId, reverse)) absorbed.add(nodeId);
            }
        } catch (error) {
            if (!(error instanceof Unresolved)) throw error;
        }
    }
    for (const nodeId of continued) {
        resolver.budget.reset(resolver.perCandidateVisits);
        try {
            if (!reachesOutput(resolver, nodeId, reverse)) absorbed.add(nodeId);
        } catch (error) {
            if (!(error instanceof Unresolved)) throw error;
        }
    }
    const kept = samplerIds.filter(nodeId => !absorbed.has(nodeId));
    return kept.length ? kept : samplerIds;
}

/** `continued_stages`: lineage predecessors of the non-detailer candidates, in order, once. */
function continuedStages(resolver, samplerIds) {
    const found = [];
    for (const nodeId of samplerIds) {
        const entry = resolver.semantics.samplers.get(resolver.graph.get(nodeId).get('class_type'));
        if (entry.strategy === DETAILER || entry.latent === undefined || entry.latent === null) continue;
        let earlier;
        try {
            const [, inputs] = resolver.node(nodeId);
            earlier = lineagePredecessor(resolver, pyGet(inputs, entry.latent));
        } catch (error) {
            if (!(error instanceof Unresolved)) throw error;
            continue;
        }
        if (earlier !== null && earlier !== nodeId && !found.includes(earlier)) found.push(earlier);
    }
    return found;
}

/** `file_stages`: stages not proven preview-only; all of them when every one is. */
function fileStages(resolver, samplerIds) {
    if (samplerIds.length < 2) return samplerIds;
    let toFile, toPreview;
    try {
        const reverse = consumers(resolver), previews = nonFileOutputs(resolver);
        const terminals = [...resolver.graph].filter(([nodeId, node]) => isDict(node) && !previews.has(nodeId) && !reverse.get(nodeId)?.length).map(([nodeId]) => nodeId);
        toFile = producersOf(resolver, terminals, previews);
        toPreview = producersOf(resolver, [...previews], previews);
    } catch (error) {
        if (!(error instanceof Unresolved)) throw error;
        return samplerIds;
    }
    const kept = samplerIds.filter(nodeId => toFile.has(nodeId) || !toPreview.has(nodeId));
    return kept.length ? kept : samplerIds;
}

function nonFileOutputs(resolver) {
    const names = resolver.semantics.nonFileOutputs, found = new Set();
    for (const [nodeId, node] of resolver.graph) {
        // `node.get("class_type") in names`: only a string can be a listed class.
        if (isDict(node) && names.has(pyGet(node, 'class_type'))) found.add(nodeId);
    }
    return found;
}

/** `producers_of`: nodes whose output reaches one of `starts`, never through a preview. */
function producersOf(resolver, starts, previews) {
    const found = new Set(starts), queue = [...starts];
    for (let head = 0; head < queue.length; head++) {
        const inputs = pyGet(resolver.graph.get(queue[head]), 'inputs');
        if (!isDict(inputs)) continue;
        resolver.scan(inputs.size);
        for (const value of inputs.values()) {
            if (!isLink(value)) continue;
            const producer = String(value[0]);
            if (found.has(producer) || previews.has(producer) || !isDict(pyGet(resolver.graph, producer))) continue;
            found.add(producer);
            queue.push(producer);
        }
    }
    return found;
}

/** `consumers`: reverse links, node ID to the IDs of nodes reading one of its outputs. */
function consumers(resolver) {
    const reverse = new Map();
    for (const [nodeId, node] of resolver.graph) {
        if (!isDict(node) || !isDict(pyGet(node, 'inputs'))) continue;
        const inputs = node.get('inputs');
        resolver.scan(inputs.size);
        for (const value of inputs.values()) {
            if (!isLink(value)) continue;
            const producer = String(value[0]);
            if (!reverse.has(producer)) reverse.set(producer, []);
            reverse.get(producer).push(nodeId);
        }
    }
    return reverse;
}

/** `reaches_output`: whether a sampler's output reaches a terminal node besides later stages. */
function reachesOutput(resolver, nodeId, reverse) {
    const seen = new Set([nodeId]), queue = [nodeId];
    for (let head = 0; head < queue.length; head++) {
        for (const consumer of reverse.get(queue[head]) ?? []) {
            if (seen.has(consumer)) continue;
            seen.add(consumer);
            resolver.budget.spend();
            const name = pyStr(pyGet(resolver.graph.get(consumer), 'class_type', ''));
            if (resolver.stageClasses.has(name) || resolver.semantics.nonFileOutputs.has(name)) continue;
            if (!reverse.get(consumer)?.length) return true;
            queue.push(consumer);
        }
    }
    return false;
}

/** `lineage_predecessor`: the sampler whose output a latent input continues through `lineage` nodes, or null. */
function lineagePredecessor(resolver, value) {
    const seen = new Set();
    while (isLink(value) && seen.size < resolver.maxDepth) {
        const nodeId = String(value[0]), slot = value[1];
        if (seen.has(nodeId)) return null;
        seen.add(nodeId);
        const [name, inputs] = resolver.node(nodeId);
        if (resolver.stageClasses.has(name)) return slot === 0 ? nodeId : null;
        if (name === 'Reroute' && slot === 0) { value = rerouteValue(inputs); continue; }
        const entry = resolver.semantics.lineage.get(slotKey(name, slot));
        if (entry === undefined) return null;
        value = pyGet(inputs, entry.input);
    }
    return null;
}

/** `latent_stages`: the one earlier sampler a latent continues, null for a single stage; else unattributed. */
export function latentStages(resolver, origin, value) {
    if (!isLink(value)) throw new Unresolved('Unknown latent ancestry', 'malformed');
    const start = String(value[0]), queue = [[start, 0]], queued = new Set([start]);
    let earlier = null, lineage = null, traced = false;
    for (let head = 0; head < queue.length; head++) {
        const [nodeId, depth] = queue[head];
        if (nodeId === origin) throw new Unresolved('Cyclic latent ancestry', 'malformed');
        if (depth >= resolver.maxDepth) throw new Unresolved('Resolution depth limit reached', 'depth-limit');
        resolver.budget.spend();
        const [name, inputs] = resolver.node(nodeId);
        if (resolver.stageClasses.has(name)) {
            if (!traced) { lineage = lineagePredecessor(resolver, value); traced = true; }
            if (nodeId !== lineage) throw new Unresolved(MULTISTAGE_UNATTRIBUTED_REASON);
            earlier = nodeId;
            continue;
        }
        if (resolver.stageShaped(name, inputs)) throw resolver.unsupported('Unsupported latent ancestry', nodeId);
        resolver.enqueue(queue, queued, inputs, depth + 1);
    }
    return earlier;
}

/** `part`: a PromptManager prepend or append, traced, or left out when a listed run-time node feeds it directly. */
export function promptPart(resolver, value, depth, omitted) {
    try {
        const [text, path] = resolver.text(value, 'string', depth + 1);
        return [text, path];
    } catch (error) {
        // A listed run-time node raises at itself, so a deeper run-time source never matches here.
        if (!(error instanceof Unresolved) || error.code !== 'runtime-value' || !isLink(value) ||
            !resolver.semantics.runtime.has(resolver.node(String(value[0]))[0])) throw error;
        omitted.push(String(value[0]));
        return ['', []];
    }
}
