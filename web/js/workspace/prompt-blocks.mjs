import { createScope, createButton } from '../shared/toolkit/core.js';
import { createSendButton } from './prompt-sender.mjs';
import { createWorkspaceIcon } from './icons.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { openCopyFallback } from './workspace-dialogs.mjs';

export function promptRoles(prompts) {
    return Object.fromEntries(['positive', 'negative'].filter(role => prompts?.[role]?.status === 'resolved' && typeof prompts[role].text === 'string').map(role => [role, prompts[role].text]));
}

// #249 D22: the concept's prompt block (app.js a298–302) carries no source line; a file's evidence is its panel badge
// (D21) and its "File & provenance" line. Roles without a confirmed text keep these status strings.
const statuses = {
    absent: 'Not recorded in this file.',
    ambiguous: 'Multiple possible prompts. Source could not be determined.',
    unresolved: 'Prompt could not be resolved.',
};
const ROLE_LABELS = Object.freeze({positive: '+ Positive prompt', negative: '\u2212 Negative prompt'});
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

export function mountPromptBlocks(container, {prompts, sender, parentScope, roles:visibleRoles = ['positive', 'negative'], onRetry, includePairAction = true, contextLabel = '', title = '', copyFallback = openCopyFallback}) {
    // A nested set of blocks (the current file inside a recorded run) names its regions and actions with a
    // context so assistive technology never meets two identically named prompt regions in one dialog.
    const named = roleLabel => contextLabel ? `${contextLabel} ${roleLabel.toLowerCase()} prompt` : `${roleLabel} prompt`;
    const action = role => contextLabel ? `${contextLabel[0].toLowerCase()}${contextLabel.slice(1)} ${role} prompt` : `${role} prompt`;
    // A card among many names its actions after its prompt, as the concept's do (app.js a298–302: "Copy positive prompt: <title>").
    const actionName = (verb, role, rest = '') => `${verb} ${action(role)}${rest}${title ? `: ${title}` : ''}`;
    if (parentScope.signal.aborted) return {update() {}, setState() {}, dispose() {}};
    const scope = childScope(parentScope);
    container.classList.add('prompt-inspector');
    scope.listen(container, 'click', async event => {
        if (!actionsEnabled) return;
        const send = event.target.closest('[data-send-role]');
        if (send && (send.dataset.sendRole === 'pair' || Object.hasOwn(roles, send.dataset.sendRole))) {
            const role = send.dataset.sendRole;
            sender.open(role === 'pair' ? roles : {[role]:roles[role]}, send);
        }
        const copy = event.target.closest('[data-copy-role]');
        if (copy && Object.hasOwn(roles, copy.dataset.copyRole)) {
            // The concept's copy: the shared toast, or its "Copy prompt" select-to-copy dialog without a clipboard.
            const text = roles[copy.dataset.copyRole];
            try {
                if (!navigator.clipboard) throw new Error('Clipboard requires a secure context');
                await navigator.clipboard.writeText(text);
                if (!scope.signal.aborted) showWorkspaceToast('Prompt copied.');
            } catch {
                if (scope.signal.aborted) return;
                // A re-render during the pending write detaches the clicked button; Escape then returns to its successor.
                const trigger = copy.isConnected === false ? container.querySelector(`[data-copy-role="${copy.dataset.copyRole}"]`) : copy;
                copyFallback(text, {scope, trigger});
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
        for (const role of visibleRoles.filter(name => Object.hasOwn(ROLE_LABELS, name))) {
            const record = state.prompts?.[role];
            const block = el('section', undefined, `prompt-section ${role}`);
            block.setAttribute('aria-label', named(role[0].toUpperCase() + role.slice(1)));
            const heading = el('div', undefined, 'prompt-heading');
            heading.append(el('span', ROLE_LABELS[role], 'role-label'));
            block.append(heading);
            if (Object.hasOwn(roles, role)) {
                const actions = el('div', undefined, 'prompt-actions');
                const copy = createButton('', 'icon-btn');
                copy.setAttribute('aria-label', actionName('Copy', role));
                copy.append(createWorkspaceIcon('copy'));
                copy.dataset.copyRole = role;
                copy.disabled = !actionsEnabled;
                const send = createButton('', 'send icon-btn');
                send.setAttribute('aria-label', actionName('Send', role, ' to ComfyUI'));
                send.append(createWorkspaceIcon('send'));
                send.dataset.sendRole = role;
                send.disabled = !actionsEnabled;
                actions.append(copy, send);
                heading.append(actions);
                const text = el('p', roles[role] === '' ? `No ${role} prompt (explicitly empty)` : roles[role], 'prompt-text');
                // Production keeps a confirmed prompt keyboard-reachable for reading and selecting long text.
                text.tabIndex = 0;
                block.append(text);
            } else block.append(el('p', typeof record?.message === 'string' && record.message ? record.message : statuses[record?.status] || statuses.unresolved, 'prompt-text muted'));
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
