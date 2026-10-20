const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/images-category.mjs');

const linked = (id, extra = {}) => ({path:`p${id}.png`, linked:{status:'linked', positive_id:id, ...extra}});

test('targets are the distinct linked positives, in selection order, and the rest count as skipped', async () => {
    const {categoryTargets} = await source;
    const images = [
        linked(7), linked(3), linked(7),
        {path:'u.png', linked:{status:'unlinked', positive_id:null}},
        {path:'a.png', linked:{status:'ambiguous', positive_id:4}},
        {path:'v.png', linked:{status:'unavailable', positive_id:null}},
        {path:'none.png'},
        {path:'bad.png', linked:{status:'linked', positive_id:'9'}},
        {path:'zero.png', linked:{status:'linked', positive_id:0}},
        {path:'frac.png', linked:{status:'linked', positive_id:1.5}},
        null,
    ];
    assert.deepEqual(categoryTargets(images), {promptIds:[7, 3], skipped:8});
    assert.deepEqual(categoryTargets([]), {promptIds:[], skipped:0});
    assert.deepEqual(categoryTargets([linked(2)]), {promptIds:[2], skipped:0});
});

test('the outcome toast counts the prompts and the skipped images', async () => {
    const {categoryOutcome} = await source;
    assert.equal(categoryOutcome(3, 0), 'Category set on 3 prompts.');
    assert.equal(categoryOutcome(1, 0), 'Category set on 1 prompt.');
    assert.equal(categoryOutcome(2, 4), 'Category set on 2 prompts. 4 selected images had no linked prompt and were skipped.');
    assert.equal(categoryOutcome(1, 1), 'Category set on 1 prompt. 1 selected image had no linked prompt and was skipped.');
});

test('the dialog and failure copy is the plan\'s', async () => {
    const {CATEGORY_DIALOG, CATEGORY_NONE_LINKED, CATEGORY_FAILED} = await source;
    assert.deepEqual(CATEGORY_DIALOG, {title:'Categorize selected images', label:'Category', action:'Apply'});
    assert.equal(CATEGORY_NONE_LINKED, 'None of the selected images has a linked prompt.');
    assert.equal(CATEGORY_FAILED, 'Category could not be set. Try again.');
    assert.ok(Object.isFrozen(CATEGORY_DIALOG));
});

test('the updated count is the server\'s when it is a count, else the number of prompts posted', async () => {
    const {updatedCount} = await source;
    assert.equal(updatedCount({updated_count:0}, 3), 0);
    assert.equal(updatedCount({updated_count:2}, 3), 2);
    for (const result of [{}, {updated_count:-1}, {updated_count:1.5}, {updated_count:'2'}, null, undefined]) {
        assert.equal(updatedCount(result, 3), 3, JSON.stringify(result));
    }
});

test('the category field is limited to the server\'s 100 characters', async () => {
    const {CATEGORY_MAX_LENGTH} = await source;
    assert.equal(CATEGORY_MAX_LENGTH, 100);
});
