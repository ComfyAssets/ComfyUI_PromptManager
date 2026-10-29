const test = require('node:test');
const assert = require('node:assert/strict');

// #437 D148: the Prompt library's selection bar sets one category on the selected prompts, fenced by the storage epoch.
const source = import('../../web/js/workspace/library-selection.mjs');

test('the dialog is the shared simpleInput with the prompt title, and the route and cap are the legacy ones', async () => {
    const {PROMPT_CATEGORY_DIALOG, CATEGORY_URL, MAX_CATEGORY_PROMPTS, TOO_MANY_PROMPTS} = await source;
    assert.deepEqual({...PROMPT_CATEGORY_DIALOG}, {title:'Categorize selected prompts', label:'Category', action:'Apply'});
    assert.equal(CATEGORY_URL, '/prompt_manager/bulk/category');
    assert.equal(MAX_CATEGORY_PROMPTS, 1000);
    assert.equal(TOO_MANY_PROMPTS, 'Select at most 1,000 prompts.');
});

test('a confirmed write toasts the shared outcome with "prompt", invalidates and reloads', async () => {
    const {writeOutcome} = await source;
    assert.deepEqual(writeOutcome({success:true, category:'Studio', updated:2, unchanged:1, not_found:0}, null),
        {toast:'Category “Studio” set on 3 prompts.', committed:true, reload:true});
    assert.deepEqual(writeOutcome({success:true, category:'Studio', updated:1, unchanged:0, not_found:2}, null),
        {toast:'Category “Studio” set on 1 prompt. 2 could not be found.', committed:true, reload:true});
});

test('none found, a restored storage, an unconfirmed answer and a refused write each say what happened', async () => {
    const {writeOutcome, NONE_FOUND, STORAGE_CHANGED, UNCONFIRMED, CATEGORY_FAILED} = await source;
    assert.deepEqual(writeOutcome({success:true, category:'X', updated:0, unchanged:0, not_found:3}, null),
        {toast:NONE_FOUND, committed:false, reload:true});
    assert.deepEqual(writeOutcome(null, {status:409, code:'storage-changed'}), {toast:STORAGE_CHANGED, committed:false, reload:true});
    for (const failure of [new TypeError('Failed to fetch'), {status:502}, {status:503}, {status:504}]) {
        assert.deepEqual(writeOutcome(null, failure), {toast:UNCONFIRMED, committed:true, reload:true});
    }
    assert.deepEqual(writeOutcome(null, null), {toast:UNCONFIRMED, committed:true, reload:true});
    for (const status of [400, 500]) {
        assert.deepEqual(writeOutcome(null, {status}), {toast:CATEGORY_FAILED, committed:false, reload:false});
    }
    assert.equal(STORAGE_CHANGED, 'Prompt storage was restored since this page loaded. No category was changed.');
});

test('the field refuses control characters as validate_category does', async () => {
    const {categoryProblem, CONTROL_CHARACTERS} = await source;
    assert.equal(categoryProblem('Studio portraits'), null);
    assert.equal(categoryProblem('tab\there'), CONTROL_CHARACTERS);
    assert.equal(categoryProblem('nul\u0000'), CONTROL_CHARACTERS);
    assert.equal(categoryProblem('unicode ✓ é'), null);
});
