import { createScope, createButton } from '../shared/toolkit/core.js';
import { createSendButton } from './prompt-sender.mjs';

export function inspectPath(image) {
    return `/prompt_manager/images/inspect/${image.path.split('/').map(encodeURIComponent).join('/')}?root=${image.root_index}`;
}

export function promptRoles(prompts) {
    return Object.fromEntries(['positive', 'negative'].filter(role => prompts?.[role]?.status === 'resolved' && typeof prompts[role].text === 'string').map(role => [role, prompts[role].text]));
}

export function createInspectionLoader(api, onChange) {
    let request, disposed = false;
    return {
        async load(image) {
            if (disposed) return;
            request?.abort();
            const current = request = new AbortController();
            onChange({ loading: true });
            try {
                const result = await api.get(inspectPath(image), {signal:current.signal});
                if (!disposed && request === current) onChange({prompts:result.prompts});
            } catch {
                if (!disposed && request === current) onChange({error:true});
            }
        },
        dispose() { disposed = true; request?.abort(); },
    };
}

const sources = {
    execution: 'Recorded at execution',
    'graph-inferred': 'Inferred from the saved workflow',
    'embedded-parameters': 'Embedded generation parameters',
    'saved-association': 'Saved library association',
};
const statuses = {
    absent: 'Not recorded in this file.',
    ambiguous: 'Multiple possible prompts. Source could not be determined.',
    unresolved: 'Prompt could not be resolved.',
};
function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

export function mountPromptInspector(container, {api, image, sender, parentScope}) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    container.classList.add('prompt-inspector');
    const loader = createInspectionLoader(api, render);
    scope.cleanup(() => loader.dispose());
    scope.listen(container, 'click', async event => {
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
        if (event.target.closest('[data-retry-prompts]')) loader.load(image);
    });
    let roles = {};
    function render(state) {
        roles = {};
        container.replaceChildren();
        if (state.loading || state.error) {
            const status = el('p', state.loading ? 'Loading prompts…' : 'Could not read prompts from this file.', 'muted');
            status.setAttribute('role', state.error ? 'alert' : 'status');
            container.append(status);
            if (state.error) {
                const retry = createButton('Retry prompt details');
                retry.dataset.retryPrompts = '';
                container.append(retry);
            }
            return;
        }
        roles = promptRoles(state.prompts);
        for (const role of ['positive', 'negative']) {
            const title = `${role[0].toUpperCase() + role.slice(1)} prompt`;
            const record = state.prompts?.[role];
            const block = el('section', undefined, `prompt-block prompt-${role}`);
            block.setAttribute('aria-label', title);
            block.append(el('h3', title));
            if (Object.hasOwn(roles, role)) {
                block.append(el('p', sources[record.source] || 'Source not recorded', 'prompt-source muted'));
                if (roles[role] === '') block.append(el('p', `Empty ${role} prompt`, 'prompt-empty'));
                else {
                    const text = el('pre', roles[role], 'prompt-text');
                    text.tabIndex = 0;
                    block.append(text);
                }
                const actions = el('div', undefined, 'prompt-actions');
                const send = createSendButton(`Send ${role} prompt`);
                send.dataset.sendRole = role;
                const copy = createButton(`Copy ${role} prompt`);
                copy.dataset.copyRole = role;
                actions.append(send, copy);
                const status = el('p', '', 'prompt-copy-status muted'); status.setAttribute('role', 'status');
                block.append(actions, status);
            } else block.append(el('p', statuses[record?.status] || statuses.unresolved, 'muted'));
            container.append(block);
        }
        if (Object.hasOwn(roles, 'positive') && Object.hasOwn(roles, 'negative')) {
            const pair = createSendButton('Send prompt pair'); pair.dataset.sendRole = 'pair';
            container.append(pair);
        }
    }
    loader.load(image);
    return {dispose:() => scope.dispose()};
}
