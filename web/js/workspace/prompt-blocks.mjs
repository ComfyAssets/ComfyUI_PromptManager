import { createScope, createButton } from '../shared/toolkit/core.js';
import { createSendButton } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';

export function promptRoles(prompts) {
    return Object.fromEntries(['positive', 'negative'].filter(role => prompts?.[role]?.status === 'resolved' && typeof prompts[role].text === 'string').map(role => [role, prompts[role].text]));
}

const sources = {
    library: 'Saved library prompt',
    execution: 'Recorded at execution',
    'graph-inferred': 'Inferred from the saved workflow',
    'embedded-parameters': 'Embedded generation parameters',
    'zeroed-conditioning': 'Zeroed conditioning (no negative text)',
    'saved-association': 'Saved library association',
};
const statuses = {
    absent: 'Not recorded in this file.',
    ambiguous: 'Multiple possible prompts. Source could not be determined.',
    unresolved: 'Prompt could not be resolved.',
};
const fieldSources = {
    runtime_return: 'Runtime return',
    host_cache_output: 'Host cache output',
    graph_literal: 'Inferred graph literal',
    graph_semantics: 'Inferred graph semantics',
};
function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function childScope(parentScope) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    if (parentScope.signal.aborted) scope.dispose();
    return scope;
}

export function mountPromptBlocks(container, {prompts, sender, parentScope, roles:visibleRoles = ['positive', 'negative'], onRetry, includePairAction = true}) {
    if (parentScope.signal.aborted) return {update() {}, setState() {}, dispose() {}};
    const scope = childScope(parentScope);
    container.classList.add('prompt-inspector');
    scope.listen(container, 'click', async event => {
        if (!actionsEnabled) return;
        const send = event.target.closest('[data-send-role]');
        if (send) {
            const role = send.dataset.sendRole;
            sender.open(role === 'pair' ? roles : {[role]:roles[role]}, send);
        }
        const copy = event.target.closest('[data-copy-role]');
        if (copy) {
            const role = copy.dataset.copyRole;
            const status = copy.closest('section').querySelector('.prompt-copy-status');
            try {
                await navigator.clipboard.writeText(roles[role]);
                if (!scope.signal.aborted) status.textContent = 'Copied.';
            } catch {
                if (!scope.signal.aborted) status.textContent = 'Copy unavailable. Select the prompt text to copy it.';
            }
        }
        if (event.target.closest('[data-retry-prompts]')) onRetry?.();
    });
    let roles = {}, actionsEnabled = true;
    function render(state) {
        if (scope.signal.aborted) return;
        roles = {};
        actionsEnabled = state.actionsEnabled !== false;
        container.replaceChildren();
        if (state.loading || state.error) {
            const status = el('p', state.loading ? 'Loading prompts…' : 'Could not read prompts from this file.', 'muted');
            status.setAttribute('role', state.error ? 'alert' : 'status');
            container.append(status);
            if (state.error && onRetry) {
                const retry = createButton('Retry prompt details');
                retry.dataset.retryPrompts = '';
                container.append(retry);
            }
            return;
        }
        roles = Object.fromEntries(Object.entries(promptRoles(state.prompts)).filter(([role]) => visibleRoles.includes(role)));
        for (const role of visibleRoles) {
            const title = `${role[0].toUpperCase() + role.slice(1)} prompt`;
            const record = state.prompts?.[role];
            const block = el('section', undefined, `prompt-block prompt-${role}`);
            block.setAttribute('aria-label', title);
            const heading = el('div', undefined, 'prompt-block-heading');
            heading.append(el('h3', title));
            block.append(heading);
            if (Object.hasOwn(roles, role)) {
                const proof = record.evidence;
                block.append(el('p', proof ? `${fieldSources[proof.source] || 'Source unknown'} · ${proof.source}` : sources[record.source] || 'Source not recorded', 'prompt-source muted'));
                if (roles[role] === '') block.append(el('p', `Empty ${role} prompt`, 'prompt-empty'));
                else {
                    const text = el('pre', roles[role], 'prompt-text');
                    text.tabIndex = 0;
                    block.append(text);
                }
                const actions = el('div', undefined, 'prompt-actions');
                const send = createSendButton(`Send ${role} prompt`);
                send.dataset.sendRole = role;
                send.disabled = !actionsEnabled;
                const copy = createButton('', 'ca-icon-button');
                copy.setAttribute('aria-label', `Copy ${role} prompt`);
                copy.title = `Copy ${role} prompt`;
                copy.append(createWorkspaceIcon('copy'));
                copy.dataset.copyRole = role;
                copy.disabled = !actionsEnabled;
                actions.append(copy, send);
                heading.append(actions);
                const status = el('p', '', 'prompt-copy-status muted'); status.setAttribute('role', 'status');
                block.append(status);
            } else block.append(el('p', typeof record?.message === 'string' ? record.message : statuses[record?.status] || statuses.unresolved, 'muted'));
            container.append(block);
        }
        if (includePairAction && Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative')) {
            const pair = createSendButton('Send prompt pair'); pair.dataset.sendRole = 'pair';
            pair.disabled = !actionsEnabled;
            container.append(pair);
        }
    }
    render({prompts});
    return {update:prompts => render({prompts}), setState:render, dispose:() => scope.dispose()};
}
