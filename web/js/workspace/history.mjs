import {createScope, createButton} from '../shared/toolkit/core.js';
import {createHistoryController, visibleHistoryColumns} from './history-state.mjs';
import {mountRecordedRun} from './recorded-run.mjs';
import {countOf} from './plural.mjs';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function preview(summary) {
    const raw = summary.negative_raw;
    return raw.state === 'unknown' ? 'Negative unknown' : raw.state === 'explicit_empty' ? 'Explicitly empty negative' : `${raw.preview}${raw.truncated ? '…' : ''}`;
}
function distinction(summary) {
    const sources = [...new Set(summary.negative_raw.evidence.map(e => e.source))];
    return `Frozen composition · ${sources.join(', ') || 'unknown evidence'}`;
}
function observedLabel(value) {
    const time = Number(value);
    return `Observed dequeue ${value} ms (${Number.isSafeInteger(time) && time <= 8640000000000000 ? new Date(time).toISOString() : 'outside display range'})`;
}

// New nodes use ordinary insertion; connected moves preserve browser state where supported.
function place(parent, node, before) {
    if (node === before || (node.parentNode === parent && node.nextSibling === before)) return;
    if (typeof parent.moveBefore === 'function' && parent.isConnected && node.isConnected && parent.ownerDocument === node.ownerDocument) parent.moveBefore(node, before);
    else parent.insertBefore(node, before);
}

export function mountRecordedHistory(container, {api, positiveId, parentScope, sender, visit = {}, onParentRevalidate, onRecordedCount}) {
    const scope = createScope(), dispose = () => scope.dispose();
    if (parentScope.signal.aborted) {dispose();return {dispose};}
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    const section = el('section', undefined, 'library-surface recorded-history');
    section.setAttribute('aria-label', 'Recorded executions');
    section.append(el('h2', 'Recorded executions'), el('p', 'same positive raw text · frozen contexts can differ in composition and evidence.', 'muted'));
    const count = el('p', '', 'history-count muted');count.setAttribute('role', 'status');
    const disclosure = el('details', undefined, 'history-scope');disclosure.append(el('summary', 'Recorded history scope'));
    const scopeText = el('p', '', 'muted');disclosure.append(scopeText);
    const toolbar = el('div', undefined, 'history-toolbar');
    const earlier = createButton('Load earlier runs'), refresh = createButton('Refresh history'), retry = createButton('Retry recorded history');
    const reload = createButton('Reload recorded history'), selected = createButton('View selected run');selected.hidden = true;
    toolbar.append(earlier, refresh, retry, reload, selected);
    const status = el('p', '', 'history-status muted');status.setAttribute('role', 'status');
    const timeline = el('div', undefined, 'history-timeline');timeline.setAttribute('aria-label', 'Chronological branch timeline');
    const canvas = el('div', undefined, 'history-canvas');
    const edge = el('span', undefined, 'history-earlier-edge');edge.setAttribute('aria-hidden', 'true');
    const positive = el('div', undefined, 'history-positive-lane');positive.append(el('span', 'Positive raw text · unchanged'));positive.setAttribute('aria-hidden', 'true');
    const branches = el('div', undefined, 'history-context-branches');branches.setAttribute('aria-hidden', 'true');
    const positions = el('ol', undefined, 'history-run-list');positions.setAttribute('aria-label', 'Recorded run positions');
    canvas.append(edge, positive, branches, positions);timeline.append(canvas);
    section.append(count, disclosure, toolbar, status, timeline, el('p', 'Green + positive · red − negative · small dot repeated frozen context. Colors identify prompt roles.', 'history-legend muted'));
    container.append(section);
    let state, runView, frame = 0, observer, items = null, summaries = [], indexes = new Map(), contexts = new Map();
    let initialized = false, focusedRun = null, activeParent = null;
    let restorePosition = typeof visit.resumeCursor === 'string' && Number.isFinite(visit.scrollLeft) && visit.scrollLeft >= 0;
    const columns = new Map(), branchNodes = new Map();
    const controller = createHistoryController({api, positiveId, restore:visit, onChange:next => {
        render(next);runView?.setState(next);
        if (next.parent !== activeParent) {activeParent = next.parent;onParentRevalidate?.(next.parent);}
        // The history summary's "N tracked runs" (#249 D24): a completed window read's count; a read in flight keeps the last.
        const window = next.window;
        if (!window.loading) onRecordedCount?.(window.history_epoch && !window.error ? window.recorded_run_count : null);
    }});
    state = controller.state;
    runView = mountRecordedRun(container, {api, controller, sender, parentScope:scope, onRestoreFocus:restoreMarker});

    function restoreMarker(selection) {
        const index = indexes.get(selection?.runId || state.selection.run_id);
        if (index === undefined) {refresh.focus({preventScroll:true});return;}
        focusedRun = items[index].run_id;renderColumns();
        const column = columns.get(focusedRun);
        const button = [...column.node.querySelectorAll('button')].find(node => node.dataset.contextId === (selection?.contextId || state.selection.context_id)) || column.node.querySelector('button');
        button?.focus({preventScroll:true});
    }
    function captureAnchor() {
        const bounds = timeline.getBoundingClientRect(), active = document.activeElement;
        let button = timeline.contains(active) && active.matches('[data-run-id]') ? active : null;
        if (!button) for (const column of columns.values()) {
            const box = column.node.getBoundingClientRect();
            if (box.right > bounds.left + 1 && box.left < bounds.right) {button = column.node.querySelector('button');break;}
        }
        if (!button) return null;
        const box = button.getBoundingClientRect();
        return {run:button.dataset.runId, context:button.dataset.contextId, x:box.left - bounds.left, y:box.top - bounds.top};
    }
    function restoreAnchor(anchor) {
        const column = columns.get(anchor?.run);
        const button = column && [...column.buttons.values()].find(node => node.dataset.contextId === anchor.context);
        if (!button) return;
        const box = button.getBoundingClientRect(), bounds = timeline.getBoundingClientRect();
        timeline.scrollLeft += box.left - bounds.left - anchor.x;
        timeline.scrollTop += box.top - bounds.top - anchor.y;
    }
    function columnFor(run) {
        const node = el('li', undefined, 'history-run-column');node.dataset.historyRun = run.run_id;
        const position = el('span', '', 'history-position');position.setAttribute('aria-hidden', 'true');node.append(position);
        return {node, position, buttons:new Map()};
    }
    function renderColumns() {
        if (scope.signal.aborted || !items) return;
        const active = document.activeElement;
        const focused = indexes.get(timeline.contains(active) ? active.closest('[data-history-run]')?.dataset.historyRun : focusedRun || state.selection.run_id) ?? -1;
        const visible = visibleHistoryColumns(items.length, Math.max(0, timeline.scrollLeft - 38), timeline.clientWidth, focused);
        const keep = new Set(visible.map(index => items[index].run_id));
        for (const [id, column] of columns) if (!keep.has(id)) {column.node.remove();columns.delete(id);}
        let before = positions.firstChild;
        for (const index of visible) {
            const run = items[index];let column = columns.get(run.run_id);
            if (!column) {column = columnFor(run);columns.set(run.run_id, column);}
            const {node, buttons} = column;
            node.style.insetInlineStart = `${38 + index * 64}px`;node.value = index + 1;
            node.setAttribute('aria-posinset', String(index + 1));node.setAttribute('aria-setsize', String(items.length));
            column.position.textContent = String(index + 1);
            const anchors = new Map();for (const anchor of run.matching_anchors) if (!anchors.has(anchor.context_id)) anchors.set(anchor.context_id, anchor);
            for (const anchor of anchors.values()) {
                let button = buttons.get(anchor.context_id);
                if (!button) {
                    button = createButton('', 'history-run-marker');button.dataset.runId = run.run_id;button.dataset.anchorId = anchor.anchor_id;button.dataset.contextId = anchor.context_id;
                    buttons.set(anchor.context_id, button);node.append(button);
                }
                const context = contexts.get(anchor.context_id), first = context.first === index;
                button.classList.toggle('history-context-anchor', first);
                button.style.insetBlockStart = `${80 + context.row * 57}px`;
                button.textContent = first ? '−' : '';
                const label = `Recorded run ${index + 1} of ${items.length} loaded · ${observedLabel(run.observed_dequeue_at)} · Outcome ${run.outcome} · ${preview(context.summary)} · context ${anchor.context_id.slice(0, 8)} · anchor ${anchor.anchor_id}`;
                button.setAttribute('aria-label', label);button.title = label;
                button.setAttribute('aria-pressed', String(state.selection.run_id === run.run_id && state.selection.context_id === anchor.context_id));
                // Disabling a focused native button drops focus during a prepend read.
                button.setAttribute('aria-disabled', String(!state.actionsEnabled));
            }
            place(positions, node, before);before = node.nextSibling;
        }
        if (timeline.contains(active) && active.isConnected && document.activeElement !== active) active.focus({preventScroll:true});
    }
    function render(next) {
        if (scope.signal.aborted) return;
        const anchor = initialized && items !== next.window.items ? captureAnchor() : null;
        state = next;
        const window = next.window;
        if (items !== window.items) {
            items = window.items;indexes = new Map(items.map((run, index) => [run.run_id, index]));
            const firsts = new Map();
            for (const [index, run] of items.entries()) for (const anchor of run.matching_anchors) if (!firsts.has(anchor.context_id)) firsts.set(anchor.context_id, index);
            summaries = [...window.context_summaries].sort((a, b) => firsts.get(a.context_id) - firsts.get(b.context_id) || (a.context_id < b.context_id ? -1 : 1));
            contexts = new Map(summaries.map((summary, row) => [summary.context_id, {summary, row, first:firsts.get(summary.context_id)}]));
            canvas.style.inlineSize = `${Math.max(520, 76 + items.length * 64)}px`;
            canvas.style.blockSize = `${Math.max(150, 115 + summaries.length * 57)}px`;
            const keep = new Set(contexts.keys());
            for (const [id, node] of branchNodes) if (!keep.has(id)) {node.remove();branchNodes.delete(id);}
            let before = branches.firstChild;
            for (const [id, context] of contexts) {
                let branch = branchNodes.get(id);
                if (!branch) {branch = el('div', undefined, 'history-context-branch');branch.dataset.contextBranch = id;branch.append(el('span', '', 'history-context-label'));branchNodes.set(id, branch);}
                branch.style.insetBlockStart = `${80 + context.row * 57}px`;branch.style.insetInlineStart = `${38 + context.first * 64}px`;
                branch.firstChild.textContent = `${preview(context.summary)} · ${id.slice(0, 8)} · ${distinction(context.summary)}`;
                place(branches, branch, before);before = branch.nextSibling;
            }
            if (anchor) {
                const index = indexes.get(anchor.run);
                if (index !== undefined) timeline.scrollLeft = Math.max(0, 38 + index * 64 - anchor.x);
            }
        }
        count.textContent = window.group ? `${countOf(window.recorded_run_count, 'distinct recorded run')} as of this read · ${window.items.length} loaded positions` : '';
        const group = window.group;
        scopeText.textContent = group?.scope_status === 'uncapturable_text' ? 'This saved positive cannot be matched within frozen-text capture limits.' : 'Runs are grouped by exact frozen positive raw text, independently of saved pairs. Effective text, prepend/append composition and evidence can differ. Reported output locations do not establish context membership or historical file identity.';
        status.setAttribute('role', window.error || next.run.error ? 'alert' : 'status');
        status.textContent = next.validating ? 'Revalidating recorded history…' : window.loading ? 'Loading recorded history…' : window.error ? 'Recorded history could not be loaded.' : next.refreshRequired ? 'Recorded evidence changed. Reload before using these actions.' : !items.length ? 'No matching recorded executions.' : window.has_more ? 'Newest bounded window · displayed in chronological order.' : 'All matching recorded runs in this window are loaded.';
        if (next.run.error && !next.selection.run_id && !next.refreshRequired) status.textContent += ' The previous recorded selection could not be restored.';
        const outside = !!next.selection.run_id && !indexes.has(next.selection.run_id);
        if (outside && !next.validating && !next.refreshRequired) status.textContent += ` Selected run ${next.selection.run_id} is outside this loaded timeline window.`;
        selected.hidden = !outside;selected.disabled = next.validating || window.loading || !!next.refreshRequired;
        earlier.hidden = !window.has_more;earlier.disabled = window.loading || !!next.refreshRequired;earlier.setAttribute('aria-disabled', String(earlier.disabled));
        refresh.disabled = window.loading || next.validating;retry.hidden = !window.error || !!next.refreshRequired;
        reload.hidden = !next.refreshRequired;reload.textContent = next.refreshLabel || 'Reload recorded history';
        timeline.hidden = !items.length;
        let initialAnchor = null;
        if (!initialized && items.length && !window.loading && !next.validating && !next.refreshRequired) {
            timeline.scrollLeft = restorePosition ? state.intent.scrollLeft : canvas.offsetWidth;
            timeline.scrollTop = restorePosition ? state.intent.scrollTop : 0;
            if (restorePosition && state.intent.anchor) initialAnchor = {run:state.intent.anchor.runId, context:state.intent.anchor.contextId, x:state.intent.anchor.x, y:state.intent.anchor.y};
            initialized = true;restorePosition = false;
        }
        if (next.refreshRequired === 'history-changed') {initialized = false;restorePosition = false;}
        renderColumns();if (initialAnchor || anchor) restoreAnchor(initialAnchor || anchor);
    }
    const schedule = () => {if (!frame) frame = requestAnimationFrame(() => {frame = 0;renderColumns();});};
    scope.listen(timeline, 'scroll', schedule);scope.listen(window, 'resize', schedule);
    scope.listen(positions, 'focusin', event => {focusedRun = event.target.closest('[data-history-run]')?.dataset.historyRun || focusedRun;});
    scope.listen(positions, 'click', event => {
        const button = event.target.closest('button[data-run-id]');if (!button || !state.actionsEnabled) return;
        runView.open({runId:button.dataset.runId, anchorId:button.dataset.anchorId, contextId:button.dataset.contextId, trigger:button});
    });
    scope.listen(positions, 'keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !state.actionsEnabled) return;
        const current = event.target.closest('[data-history-run]');if (!current) return;
        const index = indexes.get(current.dataset.historyRun), next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : index + (event.key === 'ArrowLeft' ? -1 : 1);
        if (next < 0 || next >= items.length) return;
        event.preventDefault();focusedRun = items[next].run_id;renderColumns();
        const button = columns.get(focusedRun).node.querySelector('button');button.focus({preventScroll:true});button.scrollIntoView({block:'nearest', inline:'nearest'});renderColumns();
    });
    scope.listen(earlier, 'click', () => controller.loadEarlier());scope.listen(refresh, 'click', () => {initialized = false;restorePosition = false;controller.refresh();});
    scope.listen(selected, 'click', () => {
        if (!selected.disabled && state.selection.run_id) runView.open({runId:state.selection.run_id, anchorId:state.selection.anchor_id, contextId:state.selection.context_id, trigger:selected});
    });
    scope.listen(retry, 'click', () => controller.load());scope.listen(reload, 'click', () => state.refreshRequired === 'history-changed' || state.refreshRequired === 'revalidation-failed' ? controller.refresh() : controller.loadOutputs());
    if (typeof IntersectionObserver === 'function') {
        observer = new IntersectionObserver(entries => {if (entries.some(entry => entry.isIntersecting) && state.actionsEnabled && state.window.has_more) controller.loadEarlier();}, {root:timeline});observer.observe(edge);
    }
    scope.listen(window, 'pagehide', dispose);
    scope.cleanup(() => {
        const anchor = captureAnchor();
        Object.assign(visit, {mode:state.selection.mode, runId:state.selection.run_id, anchorId:state.selection.anchor_id, contextId:state.selection.context_id,
            historyEpoch:state.window.history_epoch, resumeCursor:state.window.resume_cursor, loadedCount:state.window.items.length,
            outputCount:state.outputs.items.length, outputsLoaded:state.outputs.capture !== null, receipt:state.selection.receipt && {...state.selection.receipt},
            scrollLeft:timeline.scrollLeft, scrollTop:timeline.scrollTop, anchor:anchor && {runId:anchor.run, contextId:anchor.context, x:anchor.x, y:anchor.y}});
        observer?.disconnect();cancelAnimationFrame(frame);runView.dispose();controller.dispose();columns.clear();branchNodes.clear();section.remove();
    });
    render(state);
    void controller.load();
    return {dispose};
}
