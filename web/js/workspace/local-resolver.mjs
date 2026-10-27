// The server's inspection resolver in this browser (#249 local Inspect, PR L3 Task 8, decision LI-1): a port of
// utils/prompt_inspector.py `inspect_prompt_evidence` with utils/prompt_pair_resolver.py (INSPECTION_PROFILE) and
// utils/pair_execution.py `read_pair_execution`, driven by web/js/inspection-semantics.json. Python is the oracle:
// tests/test_local_inspect_parity.py compares every answer with the server's on the same records. Values follow
// Python's model (py-json.mjs): dict order, int versus float, code-point lengths, `str.strip()`.
//
// Task 8 ported execution evidence and graph tracing for one sampler; Task 9 adds candidate selection among several
// samplers and detailer stages (local-resolver-stages.mjs) and A1111 `parameters` (local-parameters.mjs). No DOM.
import { PyValueError, isDict, isList, isInt, pyJsonLoads, pyLen, pySlice, pyStrip, pyStr, pyGet } from './py-json.mjs';
import {
    ADAPTERS,
    ADAPTER_SLOTS,
    Budget,
    CANDIDATE_STRATEGIES,
    CLASS_NAME_LENGTH,
    Diverging,
    EMPTY_POSITIVE,
    EMPTY_POSITIVE_REASON,
    GUIDER_ABSENT_REASON,
    INCOMPATIBLE_SLOT,
    MALFORMED_EVIDENCE_REASON,
    MAX_BRANCH_CANDIDATES,
    MAX_JOIN_INPUTS,
    MAX_SUBGRAPH_DEPTH,
    MAX_WORKFLOW_NODES,
    CODED_CLASSES,
    NO_SAMPLER_NAMES,
    OWN_CLASSES,
    PASS_LIMIT_CODES,
    PASS_THROUGH,
    SELECTOR_BRANCHES_REASON,
    SEVERAL_SOURCES_REASON,
    PASSED_UNVERIFIABLE,
    OVERWRITTEN_PROMPT_REASON,
    PROFILE,
    ROLE_METADATA_KEYS,
    RUNTIME_REASON,
    SAMPLER_LIKE_INPUTS,
    SEMANTICS_UNAVAILABLE_REASON,
    UNREADABLE_METADATA_REASON,
    UNVERIFIABLE_CODES,
    Unresolved,
    ZEROED_SOURCE,
    compileSemantics,
    explained,
    isLink,
    nodeRef,
    RUNTIME_PARTS_REASON,
    RUNTIME_PARTS_SOURCE,
    readPairExecution,
    result,
    sideOf,
    slotKey,
} from './local-resolver-support.mjs';
import { finalStages, latentStages, outcomeKey, promptPart, sharedPair, stage, stagesFrom } from './local-resolver-stages.mjs';
import { parameters } from './local-parameters.mjs';

export { SemanticsError, compileSemantics, readPairExecution, outcomeMessage, isLink } from './local-resolver-support.mjs';

class GraphResolver {
    constructor(graph, metadata, semantics, overwritten) {
        this.graph = graph;
        this.overwritten = overwritten;
        this.perCandidateVisits = PROFILE.perCandidateVisits;
        this.budget = new Budget(PROFILE.totalVisits);
        this.budget.reset(PROFILE.perCandidateVisits);
        this.pathEntries = PROFILE.maxPathEntries;
        this.builtChars = PROFILE.maxBuiltChars;
        this.scanned = PROFILE.maxScannedUnits;
        this.checked = new Map();
        this.costs = new Map();
        this.heights = new Map();
        this.deepest = 0;
        this.maxDepth = PROFILE.maxDepth;
        this.maxTextLength = PROFILE.maxTextLength;
        this.memo = new Map();
        this.active = new Set();
        this.metadata = metadata ?? new Map();
        this.textSources = new Map();
        this.omitting = new Set();
        this.choices = new Map();
        this.tainted = false;
        this.latentWalks = new Map();
        this.latentCharged = new Set();
        this.semantics = semantics;
        // Nesting depth of execution-record checks (`passed`).
        this.verifying = 0;
        this.samplerClasses = new Set([...semantics.samplers].filter(([, entry]) => CANDIDATE_STRATEGIES.has(entry.strategy)).map(([name]) => name));
        this.stageClasses = new Set(semantics.samplers.keys());
        // `described`: an unlisted output of one of these stays `unsupported-node`; any other class passes through (#381).
        this.described = new Set([...semantics.allClasses, ...[...semantics.lineage.keys()].map(key => key.split('\u0000')[0]),
            ...[...ADAPTERS.keys()].map(key => key.split('\u0000')[0]), ...CODED_CLASSES, ...semantics.factClasses]);
    }

    node(nodeId) {
        if (!this.checked.has(nodeId)) {
            try {
                this.checked.set(nodeId, this.checkedNode(nodeId));
            } catch (error) {
                if (!(error instanceof Unresolved) || error.code === 'work-limit') throw error;
                this.checked.set(nodeId, error);
            }
        }
        const checked = this.checked.get(nodeId);
        if (checked instanceof Unresolved) throw new Unresolved(checked.message, checked.code, checked.node);
        return checked;
    }
    checkedNode(nodeId) {
        const node = pyGet(this.graph, nodeId);
        if (!isDict(node) || !isDict(pyGet(node, 'inputs'))) throw new Unresolved('Missing or malformed graph node', 'malformed');
        const inputs = node.get('inputs');
        this.scan(inputs.size);
        return [pyStr(pyGet(node, 'class_type', '')), inputs];
    }
    scan(units) {
        if (units > this.scanned) {
            this.scanned = -1;
            throw new Unresolved('Resolution work limit reached', 'work-limit');
        }
        this.scanned -= units;
    }
    unsupported(message, nodeId) { return this.named(message, 'unsupported-node', nodeId); }
    named(message, code, nodeId) {
        const node = pyGet(this.graph, nodeId);
        const classType = isDict(node) ? pyGet(node, 'class_type') : null;
        return new Unresolved(message, code, nodeRef(nodeId, classType));
    }
    bounded(text) {
        if (pyLen(text) > this.maxTextLength) throw new Unresolved('Resolved text exceeds the length limit', 'text-limit');
        return text;
    }
    built(text) {
        this.builtChars -= pyLen(text);
        if (this.builtChars < 0) throw new Unresolved('Resolution work limit reached', 'work-limit');
        return text;
    }

    /** `text`: [text, path, storageText] for a string value or a typed link. */
    text(value, kind, depth = 0) {
        this.deepest = Math.max(this.deepest, depth);
        if (depth >= this.maxDepth) throw new Unresolved('Resolution depth limit reached', 'depth-limit');
        if (typeof value === 'string' && kind === 'string') return [this.bounded(value), [], value];
        if (!isLink(value)) throw new Unresolved('Expected a typed graph link or text input', 'malformed');
        const nodeId = String(value[0]), slot = value[1];
        const key = `${slotKey(nodeId, slot)}\u0000${kind}`;
        if (this.active.has(key)) throw new Unresolved('Cyclic prompt graph', 'malformed');
        if (this.memo.has(key)) {
            const height = this.heights.get(key);
            if (depth + height >= this.maxDepth) throw new Unresolved('Resolution depth limit reached', 'depth-limit');
            this.budget.charge(this.costs.get(key));
            this.deepest = Math.max(this.deepest, depth + height);
            return this.memo.get(key);
        }
        const allowance = this.budget.allowance, outerDeepest = this.deepest, outerTainted = this.tainted;
        this.tainted = false;
        this.deepest = depth;
        try {
            this.budget.spend();
            const [name, inputs] = this.node(nodeId);
            this.scan(inputs.size);
            if (this.overwritten.has(nodeId)) throw new Unresolved(OVERWRITTEN_PROMPT_REASON, 'malformed');
            this.active.add(key);
            let text, path, storageText;
            const adapted = ADAPTERS.get(slotKey(name, slot));
            if (name === 'Reroute' && slot === 0) {
                const keys = ['value', 'input', 'in'].filter(item => inputs.has(item));
                if (keys.length !== 1) throw new Unresolved('Reroute input is not uniquely identifiable', 'malformed');
                [text, path, storageText] = this.text(inputs.get(keys[0]), kind, depth + 1);
                this.inheritSource(nodeId, slot, [path]);
            } else if (adapted) {
                if (kind !== 'any' && adapted.kind !== kind) throw new Unresolved(INCOMPATIBLE_SLOT, 'malformed');
                const evidence = name === 'PromptManagerPair' ? this.executionRecord(nodeId, inputs) : null;
                if (evidence !== null) {
                    text = evidence[adapted.inputName === 'text' ? 'positive_text' : 'negative_effective_text'];
                    storageText = evidence[adapted.inputName];
                    path = [];
                    this.textSources.set(slotKey(nodeId, slot), 'execution');
                } else if (adapted.comma || adapted.strip) {
                    [text, path, storageText] = this.historical(adapted, inputs, depth);
                } else {
                    [text, path, storageText] = this.text(pyGet(inputs, adapted.inputName), 'string', depth + 1);
                    if (adapted.compose || adapted.storageInput) storageText = text;
                    if (adapted.compose) {
                        const omitted = [];
                        const [before, beforePath] = promptPart(this, pyGet(inputs, 'prepend_text', ''), depth, omitted);
                        const [after, afterPath] = promptPart(this, pyGet(inputs, 'append_text', ''), depth, omitted);
                        this.scan(pyLen(before) + pyLen(after) + beforePath.length + path.length + afterPath.length);
                        text = this.built(this.bounded([pyStrip(before), text, pyStrip(after)].filter(part => part).join(' ')));
                        path = [...beforePath, ...path, ...afterPath];
                        if (omitted.length) this.omitting.add(slotKey(nodeId, slot));
                    }
                }
            } else if ((name === 'StringConcatenate' || name === 'Text Concatenate') && slot === 0 && (kind === 'string' || kind === 'any')) {
                [text, path] = this.concatenate(name, inputs, depth);
                storageText = text;
            } else if (this.follows(name, slot, kind)) {
                [text, path, storageText] = this.semantic(nodeId, name, slot, kind, inputs, depth);
            } else if (!this.described.has(name)) {
                [text, path, storageText] = this.passed(nodeId, slot, kind, inputs, depth);
            } else {
                throw this.unsupported('Unsupported node semantics or output slot', nodeId);
            }
            this.scan(path.length + 1);
            path = [{ node_id: nodeId, output_slot: slot }, ...path];
            const pathLimit = this.maxDepth * Math.max(1, this.perCandidateVisits);
            this.pathEntries -= path.length;
            if (path.length > pathLimit || this.pathEntries < 0) throw new Unresolved('Provenance path limit reached', 'work-limit');
            const traced = [text, path, storageText];
            if (!this.tainted) {
                this.memo.set(key, traced);
                this.costs.set(key, allowance - this.budget.allowance);
                this.heights.set(key, this.deepest - depth);
            }
            return traced;
        } finally {
            this.deepest = Math.max(outerDeepest, this.deepest);
            this.tainted = outerTainted || this.tainted;
            this.active.delete(key);
        }
    }

    /** `StringConcatenate` / `Text Concatenate` (the `text` branch). */
    concatenate(name, inputs, depth) {
        let keys, delimiter, clean;
        if (name === 'StringConcatenate') {
            keys = ['string_a', 'string_b'];
            delimiter = pyGet(inputs, 'delimiter', '');
            clean = false;
        } else {
            keys = [...inputs.keys()].filter(item => /^text_[a-z]$/.test(item)).sort();
            delimiter = pyGet(inputs, 'delimiter', ' ');
            clean = pyStr(pyGet(inputs, 'clean_whitespace', 'true')).toLowerCase() === 'true';
        }
        if (!keys.length || typeof delimiter !== 'string') throw new Unresolved('Malformed concatenation', 'malformed');
        const parts = [], path = [];
        let size = 0;
        for (const inputName of keys) {
            let [part, followed] = this.text(pyGet(inputs, inputName, ''), 'string', depth + 1);
            this.scan(pyLen(part) + followed.length);
            if (clean) part = pyStrip(part);
            path.push(...followed);
            if (part || name === 'StringConcatenate') {
                size += pyLen(part) + (parts.length ? pyLen(delimiter) : 0);
                if (size > this.maxTextLength) throw new Unresolved('Resolved text exceeds the length limit', 'text-limit');
                parts.push(part);
            }
        }
        return [this.built(parts.join(delimiter)), path];
    }

    /** `prompt_manager_*.py` at 07a5af3: strip, then join with ", ". */
    historical(adapted, inputs, depth) {
        let [text, path] = this.text(pyGet(inputs, adapted.inputName, adapted.default), 'string', depth + 1);
        this.scan(pyLen(text));
        text = pyStrip(text);
        const storageText = text;
        if (adapted.comma) {
            let [before, beforePath] = this.text(pyGet(inputs, 'prepend_text') ?? '', 'string', depth + 1);
            let [after, afterPath] = this.text(pyGet(inputs, 'append_text') ?? '', 'string', depth + 1);
            this.scan(pyLen(before) + pyLen(after) + beforePath.length + afterPath.length);
            before = pyStrip(before); after = pyStrip(after);
            if (before) text = `${before}${text && !before.endsWith(',') ? ', ' : ''}${text}`;
            if (after) text = `${text}${text && !text.endsWith(',') ? ', ' : ''}${after}`;
            text = this.built(this.bounded(text));
            path = [...beforePath, ...path, ...afterPath];
        }
        return [text, path, storageText];
    }

    follows(name, slot, kind) {
        const semantics = this.semantics, key = slotKey(name, slot);
        if (semantics.runtime.has(name)) return true;
        if (semantics.encoders.has(key) || semantics.strings.has(key)) return true;
        if (slot === 0 && semantics.selectors.has(name)) return true;
        return (kind === 'conditioning' || kind === 'any') && (semantics.conditioning.has(key) || (slot === 0 && semantics.composites.has(name)));
    }

    semantic(nodeId, name, slot, kind, inputs, depth) {
        const semantics = this.semantics, key = slotKey(name, slot);
        if (semantics.runtime.has(name)) throw this.named(RUNTIME_REASON, 'runtime-value', nodeId);
        for (const [table, expected, build] of [[semantics.encoders, 'conditioning', 'encoded'], [semantics.strings, 'string', 'stringed']]) {
            const listed = table.get(key);
            if (listed !== undefined) {
                if (kind !== expected && kind !== 'any') throw new Unresolved(INCOMPATIBLE_SLOT, 'malformed');
                return this[build](nodeId, listed, inputs, depth);
            }
        }
        const entry = kind === 'conditioning' || kind === 'any' ? semantics.conditioning.get(key) : undefined;
        if (entry !== undefined && entry.strategy === 'zeroed') {
            this.textSources.set(slotKey(nodeId, slot), ZEROED_SOURCE);
            return ['', [], ''];
        }
        if (entry !== undefined) {
            const traced = this.text(pyGet(inputs, entry.input), kind, depth + 1);
            this.inheritSource(nodeId, slot, [traced[1]]);
            return traced;
        }
        if (semantics.selectors.has(name)) return this.select(nodeId, semantics.selectors.get(name), inputs, kind, depth);
        const parts = semantics.composites.get(name).inputs.map(inputName => this.text(pyGet(inputs, inputName), kind, depth + 1));
        if (new Set(parts.map(part => part[0])).size !== 1) throw this.named('Combined conditioning has different texts', 'composite', nodeId);
        return this.merged(nodeId, slot, parts);
    }

    select(nodeId, entry, inputs, kind, depth) {
        const condition = pyGet(inputs, entry.condition), branches = entry.branches;
        let choice;
        if (typeof condition === 'boolean') choice = condition;
        else if (!isLink(condition)) throw new Unresolved('Malformed selector condition', 'malformed');
        else if (this.choices.has(slotKey(String(condition[0]), condition[1]))) {
            choice = this.choices.get(slotKey(String(condition[0]), condition[1]));
            this.tainted = true;
        } else choice = null;
        if (choice !== null) {
            const traced = this.text(pyGet(inputs, branches[choice ? 'true' : 'false']), kind, depth + 1);
            this.inheritSource(nodeId, 0, [traced[1]]);
            return traced;
        }
        const parts = ['true', 'false'].map(label => this.text(pyGet(inputs, branches[label]), kind, depth + 1));
        if (parts[0][0] !== parts[1][0]) throw new Diverging(slotKey(String(condition[0]), condition[1]), [true, false]);
        return this.merged(nodeId, 0, parts);
    }

    /** `passed`: `readThrough`, except that while an execution record is checked any outcome but `work-limit` is unverifiable. */
    passed(nodeId, slot, kind, inputs, depth) {
        if (!this.verifying) return this.readThrough(nodeId, slot, kind, inputs, depth);
        try {
            return this.readThrough(nodeId, slot, kind, inputs, depth);
        } catch (error) {
            if (error instanceof Unresolved && error.code === 'work-limit') throw error;
            if (error instanceof Unresolved || error instanceof Diverging) throw this.unsupported(PASSED_UNVERIFIABLE, nodeId);
            throw error;
        }
    }

    /** `read_through` (#381): an output no rule describes passes its text through; PromptManager nodes are authoritative. */
    readThrough(nodeId, slot, kind, inputs, depth) {
        // A string output of a node with a seed input is generated at run time (a language model, a wildcard).
        // With a linked conditioning input it is a conditioning transform (noise) instead.
        if (kind === 'string' && [...inputs.keys()].some(name => name.endsWith('seed')) &&
            !['conditioning', 'positive', 'negative'].some(name => isLink(pyGet(inputs, name)))) throw this.named(RUNTIME_REASON, 'runtime-value', nodeId);
        if (inputs.has('text')) {
            const traced = this.text(inputs.get('text'), 'string', depth + 1);
            this.inheritSource(nodeId, slot, [traced[1]]);
            return traced;
        }
        let found = [], blocking = null;
        for (const value of this.passInputs(slot, inputs)) {
            try {
                found.push(this.text(value, 'any', depth + 1));
            } catch (error) {
                if (!(error instanceof Unresolved) || PASS_LIMIT_CODES.has(error.code)) throw error;
                if (error.code !== 'unsupported-node' && blocking === null) blocking = error;
            }
        }
        const owned = found.filter(traced => this.owned(traced[1]));
        if (owned.length) found = owned;
        else if (blocking !== null) throw blocking;
        if (!found.length) throw this.unsupported('Unsupported node semantics or output slot', nodeId);
        const texts = [...new Set(found.map(traced => traced[0]))];
        if (texts.length === 1) return this.merged(nodeId, slot, found);
        if (texts.length > MAX_BRANCH_CANDIDATES) throw new Unresolved('Too many selector branches', 'work-limit');
        // JSON escapes U+0000, so this never equals a selector's raw `slotKey` (Python: a 3-tuple vs a 2-tuple).
        const key = JSON.stringify([PASS_THROUGH, nodeId, slot]);
        if (!this.choices.has(key)) throw new Diverging(key, texts.map((_, index) => index), true);
        this.tainted = true;
        const chosen = texts[this.choices.get(key)];
        return this.merged(nodeId, slot, found.filter(traced => traced[0] === chosen));
    }

    /** `pass_inputs`: the role input by output slot, else `conditioning`, else every linked non-type input. */
    passInputs(slot, inputs) {
        if ((slot === 0 || slot === 1) && (inputs.has('positive') || inputs.has('negative'))) {
            const value = pyGet(inputs, slot === 0 ? 'positive' : 'negative');
            return isLink(value) ? [value] : [];
        }
        if (isLink(pyGet(inputs, 'conditioning'))) return [inputs.get('conditioning')];
        return [...inputs].filter(([name, value]) => isLink(value) && !this.semantics.skipInputs.has(name)).map(([, value]) => value);
    }

    /** `saver_stages` (#381): the candidate samplers of the one saver whose prefix names the file, or null (never guessed). */
    saverStages(fileName) {
        const stem = fileStem(fileName);
        if (stem === null) return null;
        this.budget.reset(this.perCandidateVisits);
        let found;
        try {
            const matches = [...this.graph].filter(([, node]) => this.writes(node, stem)).map(([id]) => id);
            if (matches.length !== 1) return null;
            const [, inputs] = this.node(matches[0]);
            const values = [...inputs].filter(([name, value]) => name !== 'filename_prefix' && isLink(value)).map(([, value]) => value);
            found = stagesFrom(this, matches[0], values);
        } catch (error) {
            if (error instanceof Unresolved || error instanceof Diverging) return null;
            throw error;
        }
        const candidates = found.filter(id => this.samplerClasses.has(this.graph.get(id).get('class_type')));
        return candidates.length ? candidates : null;
    }

    /** `writes`: whether a node's `filename_prefix` (literal or traced) names the file `stem`. */
    writes(node, stem) {
        if (!isDict(node) || !isDict(pyGet(node, 'inputs'))) return false;
        let prefix = pyGet(node.get('inputs'), 'filename_prefix');
        if (isLink(prefix)) {
            try { prefix = this.text(prefix, 'string')[0]; } catch (error) {
                if (error instanceof Unresolved || error instanceof Diverging) return false;
                throw error;
            }
        }
        if (typeof prefix !== 'string') return false;
        return counterOf(prefix.replaceAll('\\', '/').split('/').at(-1), stem);
    }

    /** `through_unknown`: whether a followed path passes through a node no rule describes. */
    throughUnknown(path) {
        this.scan(path.length);
        return path.some(step => !this.described.has(this.node(step.node_id)[0]));
    }

    /** `owned`: whether a followed path passes through one of this repository's nodes. */
    owned(path) {
        this.scan(path.length);
        return path.some(step => OWN_CLASSES.has(this.node(step.node_id)[0]));
    }

    merged(nodeId, slot, parts) {
        const traced = this.joinedPaths(parts, parts);
        this.inheritSource(nodeId, slot, parts.map(part => part[1]));
        return traced;
    }

    joinedPaths(chosen, parts) {
        const text = chosen[0][0];
        const storages = new Set(chosen.map(part => part[2]));
        const storageText = storages.size === 1 ? chosen[0][2] : text;
        const paths = parts.map(part => part[1]);
        this.scan(paths.reduce((sum, path) => sum + path.length, 0));
        const merged = [], seen = new Set();
        for (const path of paths) {
            for (const step of path) {
                const identity = slotKey(step.node_id, step.output_slot);
                if (!seen.has(identity)) { seen.add(identity); merged.push(step); }
            }
        }
        return [text, merged, storageText];
    }

    encoded(nodeId, entry, inputs, depth) {
        const names = entry.inputs;
        if (entry.strategy === 'single') return this.text(pyGet(inputs, names[0]), 'string', depth + 1);
        if (entry.strategy === 'flag-parts') return this.flagged(nodeId, entry, inputs, depth);
        if (entry.strategy !== 'parts-equal') throw this.unsupported('Unsupported encoder strategy', nodeId);
        const parts = names.map(name => this.text(pyGet(inputs, name), 'string', depth + 1));
        this.scan(parts.reduce((sum, part) => sum + pyLen(part[0]), 0));
        if (new Set(parts.map(part => part[0])).size === 1) return this.joinedPaths(parts, parts);
        const filled = parts.filter(part => part[0] !== '');
        if (filled.length === 1) return this.joinedPaths(filled, parts);
        throw this.named('Encoder parts have different texts', 'composite', nodeId);
    }

    flagged(nodeId, entry, inputs, depth) {
        const names = entry.inputs;
        if (!this.setting(nodeId, inputs, entry.params.flag, 'bool')) return this.text(pyGet(inputs, names[0]), 'string', depth + 1);
        const parts = names.map(name => this.text(pyGet(inputs, name), 'string', depth + 1));
        this.scan(parts.reduce((sum, part) => sum + pyLen(part[0]), 0));
        if (new Set(parts.map(part => part[0])).size === 1) return this.joinedPaths(parts, parts);
        throw this.named(RUNTIME_REASON, 'runtime-value', nodeId);
    }

    stringed(nodeId, entry, inputs, depth) {
        const strategy = entry.strategy, source = entry.inputs[0];
        if (strategy === 'literal') return this.text(pyGet(inputs, source), 'string', depth + 1);
        if (strategy === 'strip-newlines') {
            const flag = this.setting(nodeId, inputs, entry.params.flag, 'bool');
            const traced = this.text(pyGet(inputs, source), 'string', depth + 1);
            if (!flag) return traced;
            this.scan(pyLen(traced[0]));
            const text = this.built(this.bounded(pyStrip(traced[0].replaceAll('\n', ''))));
            return [text, traced[1], text];
        }
        if (strategy === 'join-multi') return this.joinMulti(nodeId, entry, inputs, depth);
        throw this.unsupported('Unsupported string strategy', nodeId);
    }

    /** A literal node setting of exactly `type(value) is kind`; linked is `unsupported-node`, else malformed. */
    setting(nodeId, inputs, name, kind) {
        if (!inputs.has(name)) throw new Unresolved('Missing string node setting', 'malformed');
        const value = inputs.get(name);
        if (isLink(value)) throw this.unsupported('Linked string node setting', nodeId);
        const typed = { bool: typeof value === 'boolean', int: isInt(value), str: typeof value === 'string' }[kind];
        if (!typed) throw new Unresolved('Malformed string node setting', 'malformed');
        return value;
    }

    joinMulti(nodeId, entry, inputs, depth) {
        const params = entry.params, flag = params.list_flag;
        if (!inputs.has(flag)) throw new Unresolved('Missing string node setting', 'malformed');
        if (isLink(inputs.get(flag)) || inputs.get(flag) === true) throw this.named(RUNTIME_REASON, 'runtime-value', nodeId);
        if (inputs.get(flag) !== false) throw new Unresolved('Malformed string node setting', 'malformed');
        const count = this.setting(nodeId, inputs, params.count, 'int');
        const delimiter = this.setting(nodeId, inputs, params.delimiter, 'str');
        if (!(count >= 1 && count <= MAX_JOIN_INPUTS)) throw new Unresolved('Malformed string node setting', 'malformed');
        const total = Number(count);
        this.scan(total);
        const names = [entry.inputs[0]];
        for (let index = 2; index <= total; index++) names.push(`${params.input_prefix}${index}`);
        const parts = [], path = [], seen = new Set();
        let size = 0;
        names.forEach((inputName, position) => {
            if (position && !inputs.has(inputName)) return;
            const [part, followed] = this.text(pyGet(inputs, inputName), 'string', depth + 1);
            this.scan(pyLen(part) + followed.length);
            for (const step of followed) {
                const identity = slotKey(step.node_id, step.output_slot);
                if (!seen.has(identity)) { seen.add(identity); path.push(step); }
            }
            if (position && !part) return;
            size += pyLen(part) + (parts.length ? pyLen(delimiter) : 0);
            if (size > this.maxTextLength) throw new Unresolved('Resolved text exceeds the length limit', 'text-limit');
            parts.push(part);
        });
        const text = this.built(parts.join(delimiter));
        return [text, path, text];
    }

    sourceOf(path) {
        if (!path.length) return 'graph-inferred';
        return this.textSources.get(slotKey(path[0].node_id, path[0].output_slot)) ?? 'graph-inferred';
    }
    inheritSource(nodeId, slot, paths) {
        const sources = new Set(paths.map(path => this.sourceOf(path)));
        if (sources.size === 1) this.textSources.set(slotKey(nodeId, slot), [...sources][0]);
        else this.textSources.delete(slotKey(nodeId, slot));
    }
    unverifiable(error) { return UNVERIFIABLE_CODES.has(error.code); }

    executionRecord(nodeId, inputs) {
        const record = readPairExecution(this.metadata, nodeId);
        if (record === null) return null;
        for (const name of ['text', 'negative_text', 'prepend_text', 'append_text']) {
            let expected, followed;
            this.verifying += 1;
            try {
                [expected, followed] = this.text(pyGet(inputs, name, ''), 'string');
            } catch (error) {
                if (error instanceof Unresolved && this.unverifiable(error)) {
                    const value = pyGet(inputs, name);
                    if (isLink(value)) {
                        const [sourceName] = this.node(String(value[0]));
                        const known = ADAPTER_SLOTS.get(sourceName);
                        if (known && !known.has(String(value[1]))) throw error;
                        if (['StringConcatenate', 'Text Concatenate', 'Reroute'].includes(sourceName) && value[1] !== 0) throw error;
                    }
                    continue;
                }
                throw error;
            } finally {
                this.verifying -= 1;
            }
            // A text passed through an unknown node may be transformed there.
            if (this.throughUnknown(followed)) continue;
            if (name === 'prepend_text' || name === 'append_text') expected = pyStrip(expected);
            if (expected !== record[name]) throw new Unresolved('Execution evidence does not match recorded node inputs', 'malformed');
        }
        return record;
    }

    side(value, samplerId, guiderId = null, depth = 0) {
        const [text, path, storageText] = this.text(value, 'conditioning', depth);
        const origin = path[0];
        const provenance = {
            format: 'comfyui', sampler_id: samplerId, ...origin, path,
            text_source: this.textSources.get(slotKey(origin.node_id, origin.output_slot)) ?? 'graph-inferred',
        };
        if (guiderId !== null) provenance.guider_id = guiderId;
        const omitted = [];
        for (const step of path) if (this.omitting.has(slotKey(step.node_id, step.output_slot)) && !omitted.includes(step.node_id)) omitted.push(step.node_id);
        // Through encoders, concatenations and chained PromptManagers (D125).
        if (omitted.length) { provenance.text_source = RUNTIME_PARTS_SOURCE; provenance.omitted_parts = omitted; }
        return { text, storage_text: storageText, provenance };
    }

    guider(value) {
        const seen = new Set();
        let depth = 0;
        for (;;) {
            if (depth >= this.maxDepth) throw new Unresolved('Resolution depth limit reached', 'depth-limit');
            if (!isLink(value)) throw new Unresolved('Expected a guider link', 'malformed');
            const nodeId = String(value[0]), slot = value[1];
            if (seen.has(nodeId)) throw new Unresolved('Cyclic guider ancestry', 'malformed');
            seen.add(nodeId);
            this.budget.spend();
            const [name, inputs] = this.node(nodeId);
            this.scan(inputs.size);
            depth += 1;
            if (name === 'Reroute' && slot === 0) {
                const keys = ['value', 'input', 'in'].filter(item => inputs.has(item));
                if (keys.length !== 1) throw new Unresolved('Reroute input is not uniquely identifiable', 'malformed');
                value = inputs.get(keys[0]);
                continue;
            }
            const entry = this.semantics.guiders.get(name);
            if (entry === undefined || slot !== 0) throw this.unsupported('Unsupported guider', nodeId);
            return [nodeId, inputs, { positive: entry.positive, negative: entry.negative ?? null }, entry.absent_reason ?? GUIDER_ABSENT_REASON, depth];
        }
    }

    pair(samplerId) {
        this.budget.reset(this.perCandidateVisits);
        this.latentCharged = new Set();
        if (this.budget.total <= 0) return result('unresolved', { samplerId, reason: 'Resolution visit limit reached', reasonCode: 'work-limit' });
        return this.branches(samplerId);
    }

    branches(samplerId) {
        const pending = [new Map()];
        let results = [], several = false;
        try {
            while (pending.length) {
                this.choices = pending.shift();
                try {
                    results.push(stage(this, samplerId, new Set()));
                } catch (error) {
                    if (!(error instanceof Diverging)) throw error;
                    if (results.length + pending.length + error.choices.length > MAX_BRANCH_CANDIDATES) throw new Unresolved('Too many selector branches', 'work-limit');
                    several = several || error.sources;
                    for (const choice of error.choices) pending.push(new Map([...this.choices, [error.key, choice]]));
                }
            }
        } catch (error) {
            if (!(error instanceof Unresolved)) throw error;
            return result('unresolved', { samplerId, reason: error.message, reasonCode: error.code, node: error.node });
        } finally {
            this.choices = new Map();
        }
        if (results.every(item => item.state === 'unresolved')) return results[0];
        if (several) {
            // Chained unknown nodes can offer one text twice: list it once.
            const unique = new Map();
            for (const item of results) if (!unique.has(outcomeKey(item))) unique.set(outcomeKey(item), item);
            results = [...unique.values()];
        }
        if (new Set(results.map(outcomeKey)).size === 1) return results[0];
        return result('ambiguous', { samplerId, reason: several ? SEVERAL_SOURCES_REASON : SELECTOR_BRANCHES_REASON, candidates: results });
    }

    sides(samplerId) {
        let positive = null, negative = null;
        try {
            let [name, inputs] = this.node(samplerId);
            const entry = this.semantics.samplers.get(name);
            this.checkOverrides(samplerId, entry, inputs);
            const latent = entry.latent;
            if (latent !== undefined && latent !== null && inputs.has(latent)) this.latentChecked(samplerId, inputs.get(latent));
            let guiderId = null, depth = 0, conditioning = entry.conditioning, absentReason = 'Negative input is absent';
            if (entry.strategy === 'custom-advanced') {
                let guiderReason;
                [guiderId, inputs, conditioning, guiderReason, depth] = this.guider(pyGet(inputs, conditioning.guider));
                if (conditioning.negative === null) absentReason = guiderReason;
            }
            positive = this.side(pyGet(inputs, conditioning.positive), samplerId, guiderId, depth);
            if (!pyStrip(positive.text)) throw new Unresolved(EMPTY_POSITIVE_REASON);
            const negativeName = conditioning.negative;
            if (negativeName === null || negativeName === undefined || !inputs.has(negativeName)) {
                return result('absent', { positive, samplerId, reason: absentReason });
            }
            negative = this.side(inputs.get(negativeName), samplerId, guiderId, depth);
            const omitted = [positive, negative].some(side => side.provenance.text_source === RUNTIME_PARTS_SOURCE);
            return result('resolved', { positive, negative, samplerId, reason: omitted ? RUNTIME_PARTS_REASON : '' });
        } catch (error) {
            if (!(error instanceof Unresolved)) throw error;
            return result('unresolved', { positive, negative, samplerId, reason: error.message, reasonCode: error.code, node: error.node });
        }
    }

    checkOverrides(samplerId, entry, inputs) {
        for (const name of entry.prompt_overrides ?? []) {
            const value = pyGet(inputs, name);
            if (value === null || value === '') continue;
            if (isLink(value)) throw this.unsupported('Unsupported prompt override', String(value[0]));
            throw this.named(RUNTIME_REASON, 'runtime-value', samplerId);
        }
    }

    /** `latent_checked`: `latent_stages` once per sampler; returns the continued earlier stage, or null. */
    latentChecked(samplerId, value) {
        let error, earlier = null;
        if (this.latentWalks.has(samplerId)) {
            const [walked, cost, found] = this.latentWalks.get(samplerId);
            error = walked;
            earlier = found;
            if (!this.latentCharged.has(samplerId)) {
                this.budget.charge(cost);
                this.latentCharged.add(samplerId);
            }
        } else {
            const before = this.budget.allowance;
            try {
                earlier = latentStages(this, samplerId, value);
                error = null;
            } catch (raised) {
                if (!(raised instanceof Unresolved) || raised.code === 'work-limit') throw raised;
                error = raised;
            }
            this.latentWalks.set(samplerId, [error, before - this.budget.allowance, earlier]);
            this.latentCharged.add(samplerId);
        }
        if (error !== null) throw new Unresolved(error.message, error.code, error.node === null ? null : { ...error.node });
        return earlier;
    }

    enqueue(queue, queued, inputs, depth) {
        this.scan(inputs.size);
        for (const item of inputs.values()) {
            if (!isLink(item)) continue;
            const key = String(item[0]);
            if (!queued.has(key)) { queued.add(key); queue.push([key, depth]); }
        }
    }

    stageShaped(name, inputs) {
        if (this.semantics.allClasses.has(name)) return false;
        const { shaped, withModel } = this.semantics.stageInputs;
        if (shaped.some(key => isLink(pyGet(inputs, key)))) return true;
        return isLink(pyGet(inputs, 'model')) && withModel.some(key => isLink(pyGet(inputs, key)));
    }
}


/** `_sampler_like_classes`: distinct classes, in graph order, of nodes shaped like samplers or guiders. */
function samplerLikeClasses(graph) {
    const names = [];
    for (const node of graph.values()) {
        if (!isDict(node) || !isDict(pyGet(node, 'inputs'))) continue;
        const name = pyGet(node, 'class_type');
        if (typeof name !== 'string' || !name || names.includes(name)) continue;
        if (SAMPLER_LIKE_INPUTS.some(key => isLink(pyGet(node.get('inputs'), key)))) {
            names.push(pySlice(name, CLASS_NAME_LENGTH));
            if (names.length === NO_SAMPLER_NAMES) break;
        }
    }
    return names;
}

function noSampler(graph) {
    const seen = samplerLikeClasses(graph);
    const reason = `No supported sampler pair found${seen.length ? ` (saw ${seen.join(', ')})` : ''}`;
    return result('unresolved', { reason, reasonCode: 'no-sampler' });
}

/** `_prompt_manager_widgets`: API node ID to widget text for every PromptManager node that runs. */
function promptManagerWidgets(workflow) {
    const nodes = isDict(workflow) ? pyGet(workflow, 'nodes') : null;
    if (!isList(nodes)) return new Map();
    const definitions = pyGet(workflow, 'definitions');
    const listed = isDict(definitions) ? pyGet(definitions, 'subgraphs') : null;
    const subgraphs = new Map();
    for (const subgraph of isList(listed) ? listed : []) {
        if (isDict(subgraph) && typeof pyGet(subgraph, 'id') === 'string' && isList(pyGet(subgraph, 'nodes'))) subgraphs.set(subgraph.get('id'), subgraph.get('nodes'));
    }
    const widgets = new Map(), stack = [[nodes, '', 0]];
    let visited = 0;
    while (stack.length) {
        const [members, prefix, depth] = stack.pop();
        for (const node of members) {
            visited += 1;
            if (visited > MAX_WORKFLOW_NODES) return widgets;
            if (!isDict(node)) continue;
            const kind = pyGet(node, 'type'), nodeId = pyGet(node, 'id');
            if (!(typeof nodeId === 'string' || isInt(nodeId))) continue;
            const apiId = `${prefix}${String(nodeId)}`;
            // A list or object `type` is neither a subgraph instance nor a PromptManager node (#286).
            if (isList(kind) || isDict(kind)) continue;
            if (typeof kind === 'string' && subgraphs.has(kind) && depth < MAX_SUBGRAPH_DEPTH) {
                stack.push([subgraphs.get(kind), `${apiId}:`, depth + 1]);
                continue;
            }
            const values = pyGet(node, 'widgets_values');
            const text = isList(values) && values.length && typeof values[0] === 'string' ? values[0] :
                isDict(values) && typeof pyGet(values, 'text') === 'string' ? values.get('text') : null;
            if (typeof kind === 'string' && kind !== 'PromptManagerPair' && kind.toLowerCase().includes('promptmanager') && text !== null) widgets.set(apiId, text);
        }
    }
    return widgets;
}

/** `_overwritten_prompt_nodes` (R1). */
function overwrittenPromptNodes(workflow, graph) {
    const widgets = promptManagerWidgets(workflow), byText = new Map();
    for (const [nodeId] of widgets) {
        const node = pyGet(graph, nodeId);
        const inputs = isDict(node) ? pyGet(node, 'inputs') : null;
        const text = isDict(inputs) ? pyGet(inputs, 'text') : null;
        if (typeof text === 'string') {
            if (!byText.has(text)) byText.set(text, []);
            byText.get(text).push(nodeId);
        }
    }
    const found = new Set();
    for (const ids of byText.values()) {
        if (ids.length > 1 && new Set(ids.map(id => widgets.get(id))).size > 1) ids.forEach(id => found.add(id));
    }
    return found;
}

/** `_file_stem`: a file name's last path part without its extension, or null. */
function fileStem(fileName) {
    if (typeof fileName !== 'string' || !fileName) return null;
    const base = fileName.replaceAll('\\', '/').split('/').at(-1);
    const dot = base.lastIndexOf('.');
    const stem = dot > 0 ? base.slice(0, dot) : base;
    return stem || null;
}

/** `_counter_of`: whether `stem` is `prefix` + `_` + ASCII digits [+ `_`]. */
function counterOf(prefix, stem) {
    if (!prefix || !stem.startsWith(`${prefix}_`)) return false;
    let rest = stem.slice(prefix.length + 1);
    if (rest.endsWith('_')) rest = rest.slice(0, -1);
    return rest !== '' && /^[0-9]+$/.test(rest);
}

/** `_resolve` for an unanchored inspection (output_node_id None); `fileName` anchors on its saver (#381). */
function resolve(metadata, semantics, fileName = null) {
    if (!isDict(metadata) || !metadata.size) return result('absent', { reason: 'No parsed prompt metadata' });
    let graph;
    if (metadata.has('prompt')) graph = metadata.get('prompt');
    else if (metadata.has('parameters')) return parameters(metadata.get('parameters'), PROFILE.maxTextLength);
    else graph = metadata;
    if (!isDict(graph) || !graph.size) return result('unresolved', { reason: 'API graph is missing or malformed', reasonCode: 'malformed' });
    if (graph.size > PROFILE.nodeCeiling) return result('unresolved', { reason: 'Prompt graph exceeds the inspection node ceiling', reasonCode: 'graph-ceiling' });
    if (semantics === null) return result('unresolved', { reason: SEMANTICS_UNAVAILABLE_REASON });
    let overwritten = new Set();
    if (metadata.has('prompt')) {
        overwritten = overwrittenPromptNodes(pyGet(metadata, 'workflow'), graph);
    }
    const resolver = new GraphResolver(graph, metadata.has('prompt') ? metadata : null, semantics, overwritten);
    const anchored = fileName === null ? null : resolver.saverStages(fileName);
    const samplerIds = anchored ?? finalStages(resolver, [...graph].filter(([, node]) => isDict(node) && typeof pyGet(node, 'class_type') === 'string' &&
        resolver.samplerClasses.has(node.get('class_type'))).map(([id]) => id));
    if (!samplerIds.length) return noSampler(graph);
    if (samplerIds.length > 1) {
        const candidates = samplerIds.map(id => resolver.pair(id));
        return sharedPair(candidates) ?? result('ambiguous', { reason: 'Multiple sampler branches', candidates });
    }
    return resolver.pair(samplerIds[0]);
}


/** `_may_hold_prompt_manager`. */
function mayHoldPromptManager(canonical) {
    const graph = pyGet(canonical, 'prompt');
    if (!isDict(graph)) return false;
    for (const node of graph.values()) {
        if (!isDict(node)) continue;
        const name = pyGet(node, 'class_type'), inputs = pyGet(node, 'inputs');
        if (typeof name === 'string' && name.toLowerCase().includes('promptmanager')) return true;
        if (isDict(inputs) && inputs.has('search_text')) return true;
    }
    return false;
}

/** `_parsed_workflow`: the UI workflow as a dict, or null. */
function parsedWorkflow(value) {
    if (typeof value === 'string') {
        try { value = pyJsonLoads(value); } catch (error) {
            if (error instanceof PyValueError) return null;
            throw error;
        }
    }
    return isDict(value) ? value : null;
}

/** `unreadable_evidence`: both roles when the file's metadata could not be read at all. */
export function unreadableEvidence() {
    const resolution = { state: 'unresolved', sampler_id: null, reason: UNREADABLE_METADATA_REASON, reason_code: null, node: null };
    return { prompts: explained({ positive: sideOf('unresolved'), negative: sideOf('unresolved') }, resolution), resolution };
}

/**
 * `inspect_prompt_evidence(records, None, profile=INSPECTION_PROFILE)`: `{prompts, resolution}` for the records a
 * local read found (`{prompt, workflow, parameters, comfyassets_prompt_pairs}` texts). `semantics` is
 * `compileSemantics(...)`'s table, or null when it could not be loaded.
 */
export function resolveLocalEvidence(records, semantics, fileName = null) {
    const source = records !== null && typeof records === 'object' ? records : {};
    const metadata = new Map(Object.entries(source).filter(([key]) => ROLE_METADATA_KEYS.has(key)));
    const malformedResolution = { state: 'unresolved', sampler_id: null, reason: MALFORMED_EVIDENCE_REASON, reason_code: 'malformed', node: null };
    if (!metadata.size) {
        return {
            prompts: { positive: sideOf('absent'), negative: sideOf('absent') },
            resolution: { state: 'absent', sampler_id: null, reason: 'No parsed prompt metadata', reason_code: null, node: null },
        };
    }
    const malformed = { prompts: explained({ positive: sideOf('unresolved'), negative: sideOf('unresolved') }, malformedResolution), resolution: malformedResolution };
    if (!metadata.has('prompt') && !metadata.has('parameters')) return malformed;
    if (typeof metadata.get('prompt') === 'string') {
        try { metadata.set('prompt', pyJsonLoads(metadata.get('prompt'))); } catch (error) {
            if (error instanceof PyValueError) return malformed;
            throw error;
        }
    }
    const workflow = mayHoldPromptManager(metadata) ? parsedWorkflow(source.workflow ?? null) : null;
    if (workflow !== null && metadata.has('prompt')) metadata.set('workflow', workflow);
    const resolved = resolve(metadata, semantics, fileName);
    const state = resolved.state, prompts = {};
    for (const role of ['positive', 'negative']) {
        const side = resolved[role];
        const present = side !== null && typeof side === 'object';
        if (state !== 'ambiguous' && present && (role === 'negative' || pyStrip(side.text))) prompts[role] = sideOf('resolved', side.text, side.provenance.text_source);
        else if (state === 'resolved' && present) prompts[role] = { ...sideOf('unresolved'), message: EMPTY_POSITIVE };
        else prompts[role] = sideOf(state !== 'resolved' ? state : 'unresolved');
    }
    const resolution = { state, sampler_id: resolved.sampler_id, reason: resolved.reason, reason_code: resolved.reason_code, node: resolved.node };
    return { prompts: explained(prompts, resolution), resolution };
}

