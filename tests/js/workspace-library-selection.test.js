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

test('Add tags uses the Images dialog words for prompts, the tag routes and the editor limits (#442)', async () => {
    const {PROMPT_TAGS_DIALOG, TAGS_URL, TAG_OPTIONS_URL, MAX_PROMPT_TAGS} = await source;
    assert.deepEqual({...PROMPT_TAGS_DIALOG}, {title:'Tag selected prompts', label:'Tags, separated by commas', action:'Apply'});
    assert.equal(TAGS_URL, '/prompt_manager/bulk/tags');
    assert.equal(TAG_OPTIONS_URL, '/prompt_manager/tags');
    assert.equal(MAX_PROMPT_TAGS, 20);
});

test('the Add tags toast says what was added, already present, skipped and missing', async () => {
    const {tagsOutcome, writeOutcome, NONE_FOUND} = await source;
    assert.equal(tagsOutcome({tags:['a', 'b'], updated:2, unchanged:0, over_limit:0, not_found:0}), 'Added “a, b” to 2 prompts.');
    assert.equal(tagsOutcome({tags:['a'], updated:1, unchanged:2, over_limit:1, not_found:3}),
        'Added “a” to 1 prompt. 2 prompts already had them. 1 prompt would pass 20 tags and was skipped. 3 could not be found.');
    assert.equal(tagsOutcome({tags:['a'], updated:0, unchanged:2, over_limit:0, not_found:0}), '2 prompts already had “a”.');
    // Every prompt skipped: found, so the skip sentence, never "none found"; nothing committed.
    assert.deepEqual(writeOutcome({success:true, tags:['a'], updated:0, unchanged:0, over_limit:2, not_found:0}, null, 'tags'),
        {toast:'2 prompts would pass 20 tags and were skipped.', committed:false, reload:true});
    assert.deepEqual(writeOutcome({success:true, tags:['a'], updated:0, unchanged:0, over_limit:0, not_found:2}, null, 'tags'),
        {toast:NONE_FOUND, committed:false, reload:true});
});

test('Add tags failures name tags, not the category', async () => {
    const {writeOutcome, TAGS_STORAGE_CHANGED, TAGS_UNCONFIRMED, TAGS_FAILED} = await source;
    assert.equal(writeOutcome(null, {status:409, code:'storage-changed'}, 'tags').toast, TAGS_STORAGE_CHANGED);
    assert.equal(writeOutcome(null, {status:503}, 'tags').toast, TAGS_UNCONFIRMED);
    assert.equal(writeOutcome(null, {status:400}, 'tags').toast, TAGS_FAILED);
    assert.equal(TAGS_STORAGE_CHANGED, 'Prompt storage was restored since this page loaded. No tags were added.');
});

test('the Add tags field refuses what validate_tags refuses', async () => {
    const {tagsProblem, TAG_PROBLEMS} = await source;
    assert.equal(tagsProblem('red, blue'), null);
    assert.equal(tagsProblem(' , ,'), TAG_PROBLEMS.none);
    assert.equal(tagsProblem(Array.from({length:21}, (_, i) => `t${i}`).join(',')), TAG_PROBLEMS.many);
    assert.equal(tagsProblem(Array.from({length:21}, () => 'same').join(',')), null);
    assert.equal(tagsProblem('x'.repeat(51)), TAG_PROBLEMS.long);
    assert.equal(tagsProblem('bad\ttag'), TAG_PROBLEMS.control);
});

test('Select all N loaded is offered while some loaded prompts are unchecked (#444 ruling)', async () => {
    const {loadedOffer, offersLoaded} = await source;
    assert.equal(loadedOffer(3), 'Select all 3 loaded');
    assert.equal(loadedOffer(1203), 'Select all 1,203 loaded');
    assert.equal(offersLoaded(new Set([1]), [1, 2, 3]), true);
    assert.equal(offersLoaded(new Set([1, 2, 3]), [1, 2, 3]), false);
    assert.equal(offersLoaded(new Set(), [1, 2]), false);
    assert.equal(offersLoaded(new Set([9]), [9]), false);
    // More loaded than any action accepts: never offered (Ctrl/Cmd+A still selects them).
    assert.equal(offersLoaded(new Set([1]), Array.from({length:1001}, (_, i) => i + 1)), false);
    assert.equal(offersLoaded(new Set([1]), Array.from({length:1000}, (_, i) => i + 1)), true);
});
