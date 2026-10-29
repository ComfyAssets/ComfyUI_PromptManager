/**
 * The shared inspector's "Collections…" dialog (#331 D139, design §8), beyond the concept: one listed file's
 * collections as a checklist in the sidebar's order, a New collection field, then Cancel and Save.
 *
 * - The file's membership is read when the dialog opens ("Loading collections…"); a failed read says "Collections
 *   could not be loaded." with Retry, and Save waits for a read that worked.
 * - Each row is the concept's checkbox row (`label.tag-suggestion`, app.js a1128). With no collection the list says
 *   "No collections yet. Name one below."
 * - Save sends one request per change, in order: the new name, each newly checked collection, each unchecked one.
 *   Nothing changed sends nothing and closes. All sent: the dialog closes with "Collections updated.". A failure keeps
 *   it open with "The collections could not be updated. Try again." and the membership read again; the changes that
 *   went through still reach `onChanged({added, removed})` (collection ids), as a full success does, but only once the
 *   dialog closes: a page that lists the file again (a collection page) would otherwise remove the dialog before its
 *   failure is read. Save waits for the membership read again.
 * - A New collection name equal to a listed collection ignoring case is that collection's checkbox, checked. A name
 *   that cannot be a collection name is refused on the field; nothing is sent and the checkbox edits stay.
 * - Focus starts on the field and moves to the first checkbox once they are listed; closing returns it to the
 *   trigger, or to `focusFallback()` when an inspector re-render removed the trigger.
 */
import { createButton } from '../shared/toolkit/core.js';
import { closeWorkspaceDialog, field, openContentDialog } from './workspace-dialogs.mjs';
import { showWorkspaceToast } from './toast.mjs';
import { COLLECTIONS_UPDATED_TOAST, MAX_COLLECTION_NAME_LENGTH, MEMBERSHIP_FAILED_TEXT, MEMBERSHIP_LOADING_TEXT,
    NO_COLLECTIONS_TEXT, UPDATE_FAILED_TEXT, collectionNameKey, normalizeCollectionName } from './collections.mjs';

export const COLLECTIONS_DIALOG_TITLE = 'Collections';
export const NEW_COLLECTION_LABEL = 'New collection';

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

/**
 * Open the dialog for `target` (`{root_index, root_scope, path}`) with the client store `collections`. `openDialog`
 * and `closeDialog` are the dialog seams; `notify` the toast.
 */
export function openCollectionsDialog({ scope, collections, target, trigger = null, notify = showWorkspaceToast, onChanged = null,
    focusFallback = null, openDialog = openContentDialog, closeDialog = closeWorkspaceDialog }) {
    const local = new AbortController();
    const active = () => !scope.signal.aborted && !local.signal.aborted;
    let member = null, saving = false, listed = false;
    // The confirmed changes, reported once when the dialog closes (review M2).
    const changes = { added: [], removed: [] };
    const status = el('p', '', 'small');
    const retry = createButton('Retry');
    const list = el('div');
    list.setAttribute('role', 'group');
    list.setAttribute('aria-label', COLLECTIONS_DIALOG_TITLE);
    const input = el('input');
    input.value = '';
    input.maxLength = MAX_COLLECTION_NAME_LENGTH;
    input.autocomplete = 'off';
    input.addEventListener('input', () => input.setCustomValidity?.(''));
    const problem = el('p', '', 'small');
    problem.setAttribute('role', 'alert');
    problem.hidden = true;
    const actions = el('div', undefined, 'dialog-actions');
    const cancel = createButton('Cancel');
    const save = createButton('Save', 'ca-primary');
    actions.append(cancel, save);
    const blocked = () => member === null || saving;
    const paintSave = () => {
        if (blocked()) save.setAttribute('aria-disabled', 'true');
        else save.removeAttribute('aria-disabled');
    };
    retry.addEventListener('click', () => load());
    cancel.addEventListener('click', () => { if (!saving) closeDialog(); });
    save.addEventListener('click', () => { if (!blocked()) submit(); });

    /** One checkbox row per collection, checked where the file is a member. */
    function renderList() {
        list.replaceChildren();
        if (!collections.collections.length) {
            list.append(el('p', NO_COLLECTIONS_TEXT, 'small'));
            return;
        }
        for (const item of collections.collections) {
            const row = el('label', undefined, 'tag-suggestion');
            const box = el('input');
            Object.assign(box, { type: 'checkbox', id: `collection-choice-${item.id}`, checked: member.has(item.id) });
            box.dataset.collectionId = String(item.id);
            row.htmlFor = box.id;
            row.append(box, el('span', item.name));
            list.append(row);
        }
    }
    function showStatus(text, failed) {
        status.textContent = text;
        status.setAttribute('role', failed ? 'alert' : 'status');
        status.hidden = !text;
        retry.hidden = !failed;
    }
    /** Save waits while the membership is read (again). */
    function loading() {
        member = null;
        paintSave();
        list.replaceChildren();
        showStatus(MEMBERSHIP_LOADING_TEXT, false);
    }
    async function load() {
        loading();
        try {
            const ids = await collections.membership(target);
            if (!active()) return;
            member = new Set(ids);
            showStatus('', false);
            renderList();
            // The first listing takes focus from the untouched field to the first checkbox (review L6).
            if (!listed && !input.value) firstBox().focus();
            listed = true;
        } catch {
            if (!active()) return;
            showStatus(MEMBERSHIP_FAILED_TEXT, true);
        }
        paintSave();
    }
    const boxesOf = () => [...list.children].map(row => row.children?.[0]).filter(box => box?.dataset?.collectionId);
    const firstBox = () => boxesOf()[0] ?? input;
    /**
     * The requests Save sends, in order; null when the new name cannot be a collection name. A name equal to a listed
     * collection's ignoring case is that collection's checkbox, checked (review L4).
     */
    function plan() {
        const typed = input.value.trim();
        const name = typed ? normalizeCollectionName(typed) : '';
        if (name === null) return null;
        const named = name ? collections.collections.find(item => collectionNameKey(item.name) === collectionNameKey(name)) : null;
        const steps = name && !named ? [{ add: { name } }] : [];
        const boxes = boxesOf().map(box => ({ id: Number(box.dataset.collectionId), checked: box.checked }))
            .map(box => (box.id === named?.id ? { ...box, checked: true } : box));
        for (const box of boxes) if (box.checked && !member.has(box.id)) steps.push({ add: { collectionId: box.id } });
        for (const box of boxes) if (!box.checked && member.has(box.id)) steps.push({ remove: box.id });
        return steps;
    }
    async function submit() {
        problem.textContent = '';
        problem.hidden = true;
        const steps = plan();
        if (steps === null) {
            // Review L5: refused on the field; nothing is sent and the checkbox edits stay.
            input.setCustomValidity?.(UPDATE_FAILED_TEXT);
            input.reportValidity?.();
            input.focus();
            return;
        }
        if (steps.length === 0) { closeDialog(); return; }
        saving = true;
        paintSave();
        let failed = false;
        for (const step of steps) {
            try {
                if (step.add) changes.added.push((await collections.add(step.add, [target])).collection.id);
                else changes.removed.push((await collections.remove(step.remove, [target])).collection.id);
            } catch {
                failed = true;
                break;
            }
            if (!active()) return;
            if (step.add?.name) input.value = '';
        }
        if (!active()) return;
        saving = false;
        if (!failed) {
            closeDialog();
            notify(COLLECTIONS_UPDATED_TOAST);
            return;
        }
        problem.textContent = UPDATE_FAILED_TEXT;
        problem.hidden = false;
        // Review M1: Save waits from here until the membership is read again. A collection deleted meanwhile is
        // re-read by the store; the list then shows what the server holds.
        loading();
        await collections.settled();
        if (active()) await load();
    }
    function closed() {
        local.abort();
        if (changes.added.length || changes.removed.length) onChanged?.({ added: [...changes.added], removed: [...changes.removed] });
        // The shell focuses a trigger still on the page; one an inspector re-render replaced falls back (review L6).
        if (trigger && !trigger.isConnected) focusFallback?.()?.focus();
    }
    showStatus('', false);
    openDialog({ scope, title: COLLECTIONS_DIALOG_TITLE, trigger, canClose: () => !saving, onClose: closed, focusTarget: input,
        nodes: [status, retry, list, field(NEW_COLLECTION_LABEL, input, 'collection-new'), problem, actions] });
    load();
}
