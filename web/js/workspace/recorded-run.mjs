import {createScope, createButton} from '../shared/toolkit/core.js';
import {frozenPromptRoles} from './history-state.mjs';
import {mountPromptBlocks} from './prompt-blocks.mjs';
import {createInspectionController} from './inspection-state.mjs';
import {mountFileInspector} from './file-inspector.mjs';
import {createMediaViewer} from './media-viewer.mjs';
import {distinctCurrentMedia, occurrenceKey, reportedLocationAdapter} from './history-media.mjs';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function facts(entries, values) {
    const list = el('dl', undefined, 'recorded-run-facts');
    for (const [label, value] of entries) {const row = el('div'), description = el('dd', value ?? 'Not recorded');row.append(el('dt', label), description);list.append(row);values.set(label, description);}
    return list;
}
function observed(value) {
    const time = Number(value);
    return Number.isSafeInteger(time) && time <= 8640000000000000 ? new Date(time).toISOString() : 'Recorded time outside display range';
}
function outputSummary(summary) {
    if (summary.state === 'not_captured') return 'Reported outputs not captured';
    const count = summary.retained_occurrence_count;
    return summary.list_status === 'complete' ? `${count} reported outputs · complete` : `${count} retained reported output${count === 1 ? '' : 's'} · incomplete · ${summary.state}`;
}

export function mountRecordedRun(container, {api, controller, sender, parentScope, onClose, onRestoreFocus}) {
    const scope = createScope(), dispose = () => scope.dispose();
    let active = null, latest = controller.state;
    if (parentScope.signal.aborted) {dispose();return {open() {}, setState() {}, dispose};}
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    scope.cleanup(() => active?.close(false));

    function open({runId, anchorId = null, contextId = null, trigger = document.activeElement}) {
        if (scope.signal.aborted) return;
        active?.close(false);
        const local = createScope(), dialog = el('dialog', undefined, 'recorded-run-dialog');dialog.setAttribute('aria-label', 'Recorded run');
        const header = el('header', undefined, 'recorded-run-header');
        const close = createButton('×');close.setAttribute('aria-label', 'Close recorded run');close.autofocus = true;
        header.append(el('h2', 'Recorded run'), close);
        const message = el('p', '', 'recorded-run-status muted');message.setAttribute('role', 'status');
        const retry = createButton('Retry run details'), reload = createButton('Reload recorded history');
        const body = el('div', undefined, 'recorded-run-body');
        const metadata = el('section');metadata.setAttribute('aria-label', 'Run evidence');
        const anchors = el('div', undefined, 'recorded-run-anchors');anchors.setAttribute('role', 'group');anchors.setAttribute('aria-label', 'Recorded anchors');
        const mode = el('div', undefined, 'recorded-run-mode');mode.setAttribute('role', 'group');mode.setAttribute('aria-label', 'Frozen text field');
        const raw = createButton('Raw'), effective = createButton('Effective');mode.append(raw, effective);
        const promptSection = el('section');promptSection.setAttribute('aria-label', 'Frozen run prompt texts');
        promptSection.append(el('h3', 'Frozen prompt fields'), el('p', 'Only the selected stored field is shown. Role assignment: inferred. Field evidence is disclosed separately.', 'muted'));
        const prompts = el('div');promptSection.append(prompts);
        const provenance = el('details', undefined, 'recorded-provenance');provenance.append(el('summary', 'Frozen field provenance'));
        const proof = el('div');provenance.append(proof);promptSection.append(provenance);
        const outputs = el('section', undefined, 'recorded-outputs');outputs.setAttribute('aria-label', 'Reported outputs');
        const outputState = el('p', '', 'recorded-output-status muted');outputState.setAttribute('role', 'status');
        const receipts = el('ol', undefined, 'recorded-receipts');receipts.setAttribute('aria-label', 'Ordered reported output receipts');
        const more = createButton('Load more reported outputs'), retryOutputs = createButton('Retry reported outputs');
        outputs.append(el('h3', 'Reported outputs'), el('p', 'Host-reported locations · file identity unverified · newly generated unknown · Context membership unknown.', 'muted'), outputState, receipts, more, retryOutputs);
        const current = el('section', undefined, 'recorded-current-file');current.setAttribute('aria-label', 'Current reported file');
        current.append(el('h3', 'Current file at reported location'), el('p', 'Current file prompts and facts are separate from frozen run fields.', 'muted'));
        const inspectorContainer = el('div');current.append(inspectorContainer);current.hidden = true;
        body.append(metadata, anchors, mode, promptSection, outputs, current);dialog.append(header, message, retry, reload, body);container.append(dialog);
        const inspection = createInspectionController({api, extractFacts:window.ComfyMetadata.extractGenerationFacts, adapter:reportedLocationAdapter});
        let closed = false, renderedDetail = null, renderedRoles = '', initialOutputQueued = false, inspected = null, viewerOpen = false;
        const receiptNodes = new Map();
        const evidenceValues = new Map();
        const blocks = mountPromptBlocks(prompts, {sender, parentScope:local});
        const inspector = mountFileInspector(inspectorContainer, {sender, parentScope:local, inline:true, promptContext:'Current file', onRetry:() => inspection.retry(), onRefresh:refreshCurrent});
        function selectedReceipt() {
            const selected = latest.selection.receipt;
            return selected && latest.outputs.items.find(item => occurrenceKey(item.occurrence) === occurrenceKey(selected));
        }
        function restoreReceiptFocus() {
            const key = latest.selection.receipt && occurrenceKey(latest.selection.receipt);
            const row = receiptNodes.get(key);(row?.view.hidden ? row.select : row?.view)?.focus({preventScroll:true});
        }
        const makeViewer = () => createMediaViewer({container, parentScope:local, inspectionController:inspection, sender, adapter:reportedLocationAdapter,
            onSelect:entry => {
                if (closed || !latest.actionsEnabled) return;
                inspected = occurrenceKey(entry.occurrence);controller.selectReceipt(entry.occurrence);inspection.select(entry);
            }, onRefresh:refreshCurrent, onRestoreFocus:restoreReceiptFocus,
            onClose:() => {viewerOpen = false;restoreReceiptFocus();},
        });
        let viewer = makeViewer();
        function resetViewer() {viewer.dispose();viewerOpen = false;viewer = makeViewer();}
        function refreshCurrent() {
            if (closed) return;
            resetViewer();inspection.clear();inspected = null;
            if (latest.refreshRequired === 'history-changed') controller.refresh();
            else controller.revalidate();
        }
        function showViewer(triggerButton) {
            if (!latest.actionsEnabled || inspection.state.refreshRequired || closed) return;
            const receipt = selectedReceipt();if (receipt?.current_candidate.status !== 'available') return;
            const items = distinctCurrentMedia(latest.outputs.items, latest.selection.receipt);
            viewerOpen = true;viewer.open({items, selectedKey:reportedLocationAdapter.key(receipt.current_candidate.media), trigger:triggerButton || receiptNodes.get(occurrenceKey(receipt.occurrence))?.view});
        }
        function chooseReceipt(receipt, triggerButton) {
            if (!latest.actionsEnabled || closed) return;
            inspected = occurrenceKey(receipt.occurrence);controller.selectReceipt(receipt.occurrence);
            inspection.select(receipt.current_candidate.media);showViewer(triggerButton);
        }
        function selectReceipt(receipt) {
            if (!latest.actionsEnabled || closed) return;
            inspected = null;controller.selectReceipt(receipt.occurrence);inspection.clear();if (viewerOpen) resetViewer();
        }
        function closeRun(restore = true) {
            if (closed) return;closed = true;active = null;local.dispose();
            if (restore && !scope.signal.aborted) {
                if (trigger?.isConnected) trigger.focus({preventScroll:true});
                else onRestoreFocus?.({runId, anchorId, contextId});
                onClose?.();
            }
        }
        local.cleanup(() => {viewer.dispose();inspection.dispose();inspector.dispose();blocks.dispose();receiptNodes.clear();if (dialog.open) dialog.close();dialog.remove();});
        local.listen(close, 'click', () => closeRun());local.listen(dialog, 'close', () => closeRun());
        local.listen(dialog, 'cancel', event => {event.preventDefault();closeRun();});
        local.listen(retry, 'click', () => controller.retryRun());
        local.listen(reload, 'click', () => {
            if (['capture-changed', 'candidate-stale'].includes(latest.refreshRequired)) controller.loadOutputs();
            else {closeRun();controller.refresh();}
        });
        local.listen(raw, 'click', () => {if (latest.actionsEnabled) controller.setMode('raw');});
        local.listen(effective, 'click', () => {if (latest.actionsEnabled) controller.setMode('effective');});
        local.listen(anchors, 'click', event => {const button = event.target.closest('[data-select-anchor]');if (button && latest.actionsEnabled) controller.selectContext(button.dataset.selectAnchor);});
        local.listen(more, 'click', () => controller.loadOutputs());local.listen(retryOutputs, 'click', () => controller.retryOutputs());
        local.listen(receipts, 'click', event => {
            const button = event.target.closest('[data-view-receipt],[data-select-receipt],[data-retry-receipt]');if (!button || button.disabled) return;
            const key = button.dataset.viewReceipt ?? button.dataset.selectReceipt ?? button.dataset.retryReceipt;
            const receipt = latest.outputs.items.find(item => occurrenceKey(item.occurrence) === key);if (!receipt) return;
            if (button.hasAttribute('data-view-receipt')) chooseReceipt(receipt, button);
            else if (button.hasAttribute('data-select-receipt')) selectReceipt(receipt);
            else {selectReceipt(receipt);void controller.retryReceipt(receipt.occurrence);}
        });
        local.cleanup(inspection.subscribe(state => {
            if (closed) return;
            const target = selectedReceipt()?.current_candidate.media;
            if (target && inspected === occurrenceKey(target.occurrence) && state.error && state.refreshRequired) {
                controller.inspectionFailed(target, state);
                if (inspected === null || !latest.selection.receipt) return;
            }
            inspector.setState(state);
            for (const receipt of receiptNodes.values()) receipt.view.disabled = !latest.actionsEnabled || latest.outputs.loading || state.refreshRequired;
        }));
        function render(next) {
            if (closed || local.signal.aborted) return;
            const selectedRun = next.selection.run_id === runId, detail = selectedRun ? next.run.detail : null;
            const enabled = next.actionsEnabled && selectedRun;
            message.setAttribute('role', next.run.error || next.refreshRequired ? 'alert' : 'status');
            message.textContent = next.refreshRequired ? 'Recorded evidence changed. Reload before using these actions.' : next.validating ? 'Revalidating recorded run…' : next.run.error && selectedRun ? 'Recorded run details could not be loaded.' : !detail ? 'Loading recorded run…' : '';
            retry.hidden = !selectedRun || !next.run.error || !!next.refreshRequired;reload.hidden = !next.refreshRequired;reload.textContent = next.refreshLabel || 'Reload recorded history';
            body.hidden = !detail;raw.disabled = effective.disabled = !enabled;
            if (!detail) {
                if (renderedDetail) {
                    inspected = null;inspection.clear();resetViewer();blocks.setState({actionsEnabled:false});proof.replaceChildren();current.hidden = true;
                    renderedDetail = null;renderedRoles = '';metadata.replaceChildren();anchors.replaceChildren();receipts.replaceChildren();receiptNodes.clear();
                }
                return;
            }
            raw.setAttribute('aria-pressed', String(next.selection.mode === 'raw'));effective.setAttribute('aria-pressed', String(next.selection.mode === 'effective'));
            if (detail !== renderedDetail) {
                renderedDetail = detail;const run = detail.run;
                metadata.replaceChildren(facts([
                    ['Run ID', run.run_id], ['Session ID', run.session_id], ['Queue item ID', run.queue_item_id], ['Job / correlation ID', run.job_id],
                    ['Observed dequeue', observed(run.observed_dequeue_at)], ['Observed dequeue · exact decimal milliseconds', run.observed_dequeue_at],
                    ['Outcome', run.outcome], ['Outcome reason', run.reason], ['Context capture', `${detail.context_capture.state} · ${detail.context_capture.reason ?? 'not captured'}`],
                    ['Output capture', `${detail.output_summary.state} · ${detail.output_summary.reason ?? 'not captured'}`],
                    ['Safe diagnostic node', run.node_id], ['Safe diagnostic node type', run.node_type], ['Safe exception type', run.exception_type],
                    ['Diagnostics omitted', run.diagnostics_omitted ? 'Yes' : 'No'],
                ], evidenceValues));
                if (detail.context_capture.state === 'conflicting') metadata.append(el('p', 'First captured context · conflicting evidence', 'muted'));
                anchors.replaceChildren();
                for (const context of detail.contexts) {
                    const button = createButton(`Anchor ${context.anchor_id} · context ${context.context_id.slice(0, 8)}`);button.dataset.selectAnchor = context.anchor_id;anchors.append(button);
                }
            }
            for (const button of anchors.querySelectorAll('button')) {button.disabled = !enabled;button.setAttribute('aria-pressed', String(button.dataset.selectAnchor === next.selection.anchor_id));}
            const context = detail.contexts.find(item => item.anchor_id === next.selection.anchor_id);
            const roleKey = `${next.selection.anchor_id}:${next.selection.mode}:${enabled}:${detail.context_capture.state}`;
            if (roleKey !== renderedRoles) {
                renderedRoles = roleKey;
                blocks.setState({prompts:context ? frozenPromptRoles(context, next.selection.mode) : undefined, actionsEnabled:enabled});
                proof.replaceChildren(el('p', 'Role assignment: inferred. Sources describe each field, not blanket execution proof.', 'muted'));
                if (context) for (const role of ['positive', 'negative']) {
                    const section = el('section');section.append(el('h4', `${role[0].toUpperCase() + role.slice(1)} fields`));
                    for (const field of ['raw', 'effective', 'prepend', 'append']) {
                        const value = context[role]?.[field] ?? null, evidence = context.evidence[role]?.[field];
                        section.append(el('p', `${field[0].toUpperCase() + field.slice(1)} · ${evidence?.provenance || 'unknown'} · ${evidence?.source || 'unknown'}${evidence?.reason ? ` · reason: ${evidence.reason}` : ''}`, 'muted'));
                        section.append(el('pre', value === null ? 'Unknown · not recorded' : value === '' ? '(explicitly empty)' : value, 'prompt-text'));
                    }
                    proof.append(section);
                }
            }
            const output = next.outputs;
            const outputEvidence = output.capture ? {...output.capture, retained_occurrence_count:output.retained_occurrence_count, list_status:output.list_status} : detail.output_summary;
            evidenceValues.get('Output capture').textContent = `${outputEvidence.state} · ${outputEvidence.reason ?? 'not captured'}`;
            outputState.setAttribute('role', output.error ? 'alert' : 'status');
            outputState.textContent = `${outputSummary(outputEvidence)}${output.loading ? ' · Loading reported outputs…' : output.error ? ' · Reported outputs could not be loaded.' : output.capture && !output.has_more ? ' · All retained receipts loaded.' : ''}`;
            more.hidden = !output.has_more;more.disabled = output.loading || !enabled;
            retryOutputs.hidden = !output.error;retryOutputs.disabled = !!next.refreshRequired;
            const keep = new Set(output.items.map(item => occurrenceKey(item.occurrence)));
            for (const [key, record] of receiptNodes) if (!keep.has(key)) {record.node.remove();receiptNodes.delete(key);}
            for (const receipt of output.items) {
                const key = occurrenceKey(receipt.occurrence), candidate = receipt.current_candidate;
                let record = receiptNodes.get(key);
                if (!record) {
                    const node = el('li', undefined, 'recorded-receipt');node.dataset.receipt = key;node.dataset.outputNodeId = receipt.occurrence.output_node_id;node.dataset.imageOrdinal = String(receipt.occurrence.image_ordinal);
                    const description = el('div'), select = createButton('Select reported output'), view = createButton('View current file'), retry = createButton('Retry current availability');
                    select.dataset.selectReceipt = view.dataset.viewReceipt = retry.dataset.retryReceipt = key;
                    select.title = `Select output node ${receipt.occurrence.output_node_id} · image ordinal ${receipt.occurrence.image_ordinal}`;
                    node.append(description, select, view, retry);receipts.append(node);record = {node, description, select, view, retry};receiptNodes.set(key, record);
                }
                const reference = receipt.reference;
                record.description.textContent = `Output node ${receipt.occurrence.output_node_id} · image ordinal ${receipt.occurrence.image_ordinal} · ${reference.folder_type}/${[reference.subfolder, reference.filename].filter(Boolean).join('/')} · ${reference.path_style} · root namespace ${reference.root_key ?? 'not recorded'} · ${receipt.transient ? 'Transient' : 'Non-transient locator'} · capture ${output.capture?.state || detail.output_summary.state} · Current candidate: ${candidate.status}${candidate.reason ? ` · ${candidate.reason}` : ''}`;
                record.node.setAttribute('aria-current', String(next.selection.receipt !== null && key === occurrenceKey(next.selection.receipt)));
                record.select.setAttribute('aria-pressed', record.node.getAttribute('aria-current'));record.select.disabled = !enabled;
                record.retry.hidden = candidate.status === 'available';record.retry.disabled = !enabled || output.loading;
                record.view.hidden = candidate.status !== 'available';record.view.disabled = !enabled || output.loading || inspection.state.refreshRequired;
            }
            const selected = selectedReceipt(), selectedKey = selected && occurrenceKey(selected.occurrence);
            current.hidden = !selected || inspected !== selectedKey || selected.current_candidate.status !== 'available';
            if (inspected !== null && inspected !== selectedKey) {inspected = null;inspection.clear();if (viewerOpen) resetViewer();}
            if (!enabled && viewerOpen) resetViewer();
            if (enabled && !initialOutputQueued && !output.capture && !output.loading && !output.error) {
                initialOutputQueued = true;
                queueMicrotask(() => {
                    initialOutputQueued = false;
                    if (!closed && !local.signal.aborted && latest.selection.run_id === runId && latest.actionsEnabled && latest.run.detail && !latest.run.loading && !latest.outputs.capture && !latest.outputs.loading && !latest.outputs.error) controller.loadOutputs();
                });
            }
        }
        active = {close:closeRun, render};
        dialog.showModal();
        render(latest);
        const reusable = latest.actionsEnabled && latest.selection.run_id === runId && latest.run.detail && latest.outputs.capture && !latest.outputs.loading && !latest.outputs.error;
        if (reusable) {
            const context = latest.run.detail.contexts.find(context => (anchorId === null || context.anchor_id === anchorId) && (contextId === null || context.context_id === contextId));
            if (context && context.anchor_id !== latest.selection.anchor_id) controller.selectContext(context.anchor_id);
        } else void controller.selectRun(runId, {anchorId, contextId});
    }
    function setState(state) {if (scope.signal.aborted) return;latest = state;active?.render(state);}
    return {open, setState, dispose};
}
