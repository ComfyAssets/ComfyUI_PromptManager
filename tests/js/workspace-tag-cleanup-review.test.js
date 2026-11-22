// Tag cleanup review (#618, D210): the dialog draws whatever rules the findings route returns; its selection is one
// set of tag ids per rule, nothing ticked at first, and Review cleanup sends exactly the ticked ids per rule.
const test = require('node:test');
const assert = require('node:assert/strict');

const review = import('../../web/js/workspace/tag-cleanup-review.mjs');

const RULES = Object.freeze([
    { id: 'comma', title: 'Tags containing a comma', explanation: 'A whole list saved as one tag.',
        action_label: 'Removes the tag and deletes it.', total: 3,
        findings: [{ tag_id: 4, name: 'a, b', detail: '3 prompts · 1 pair' }, { tag_id: 9, name: 'c, d', detail: 'no links' },
            { tag_id: 2, name: 'x，y', detail: '1 prompt' }] },
    { id: 'unused', title: 'Tags with no links', explanation: 'No prompt, saved pair or image carries the tag.',
        action_label: 'Deletes the tag.', total: 2501, findings: [{ tag_id: 7, name: 'ghost', detail: 'no links' }] },
    { id: 'later', title: 'A later rule', explanation: 'x', action_label: 'y', total: 0, findings: [] },
]);

test('nothing is ticked at first; only rules with findings are drawn', async () => {
    const { createSelection, selectedCount, statusText, shownRules } = await review;
    const selection = createSelection(RULES);
    assert.equal(selectedCount(selection), 0);
    assert.equal(statusText(0), '0 tags selected');
    assert.equal(statusText(1), '1 tag selected');
    assert.equal(statusText(1204), '1,204 tags selected');
    assert.deepEqual(shownRules(RULES).map(rule => rule.id), ['comma', 'unused']);
    assert.deepEqual(shownRules(null), []);
});

test('a rule tick ticks every listed tag of the rule and clears them; single ticks make it indeterminate', async () => {
    const { createSelection, ruleState, toggleRule, toggleTag, selectedCount, isTicked } = await review;
    const selection = createSelection(RULES);
    assert.equal(ruleState(selection, 'comma'), 'none');
    toggleRule(selection, 'comma', true);
    assert.equal(ruleState(selection, 'comma'), 'all');
    assert.equal(selectedCount(selection), 3);
    assert.equal(ruleState(selection, 'unused'), 'none');
    toggleTag(selection, 'comma', 9, false);
    assert.equal(ruleState(selection, 'comma'), 'some');
    assert.equal(isTicked(selection, 'comma', 9), false);
    assert.equal(isTicked(selection, 'comma', 4), true);
    toggleRule(selection, 'comma', false);
    assert.equal(ruleState(selection, 'comma'), 'none');
    assert.equal(selectedCount(selection), 0);
    toggleTag(selection, 'unused', 7, true);
    assert.equal(ruleState(selection, 'unused'), 'all');
    // An id the rule did not list is never ticked.
    toggleTag(selection, 'unused', 999, true);
    assert.equal(selectedCount(selection), 1);
});

test('the select payload names each ticked rule once, in registry order, with its ticked ids', async () => {
    const { createSelection, toggleRule, toggleTag, selectPayload } = await review;
    const selection = createSelection(RULES);
    assert.deepEqual(selectPayload(selection), []);
    toggleTag(selection, 'unused', 7, true);
    toggleTag(selection, 'comma', 2, true);
    toggleTag(selection, 'comma', 4, true);
    assert.deepEqual(selectPayload(selection), [{ rule: 'comma', tag_ids: [4, 2] }, { rule: 'unused', tag_ids: [7] }]);
    toggleRule(selection, 'comma', false);
    assert.deepEqual(selectPayload(selection), [{ rule: 'unused', tag_ids: [7] }]);
});

test('labels: the rule with its total, a finding with its detail, the cap note only when the cap cut the list', async () => {
    const { ruleLabel, findingLabel, ruleText, capNote, CAP_NOTE } = await review;
    assert.equal(ruleLabel(RULES[1]), 'Tags with no links (2,501)');
    assert.equal(findingLabel(RULES[0].findings[0]), 'a, b · 3 prompts · 1 pair');
    assert.equal(findingLabel({ name: 'ghost' }), 'ghost');
    assert.equal(ruleText(RULES[1]), 'No prompt, saved pair or image carries the tag. Deletes the tag.');
    assert.equal(capNote(RULES[0]), null);
    assert.equal(capNote(RULES[1]), CAP_NOTE);
    assert.equal(CAP_NOTE, 'Only the first 2,000 are listed. Clean these up, then search again.');
});

test('only a finished tag search that found tags offers Review tags', async () => {
    const { hasTagFindings } = await review;
    const job = (state, found, kind = 'tag_cleanup') => ({ kind, state, counters: { found } });
    assert.equal(hasTagFindings(job('finished', 3)), true);
    assert.equal(hasTagFindings(job('finished', 0)), false);
    assert.equal(hasTagFindings(job('running', 3)), false);
    assert.equal(hasTagFindings(job('finished', 3, 'duplicates')), false);
    assert.equal(hasTagFindings({ kind: 'tag_cleanup', state: 'finished' }), false);
});
