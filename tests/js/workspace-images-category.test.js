const test = require('node:test');
const assert = require('node:assert/strict');

// #332 D140: Set category writes each selected image's own category; Use prompt category removes it.
const source = import('../../web/js/workspace/images-category.mjs');

test('the dialog keeps the concept\'s simpleInput words and names the routes', async () => {
    const {CATEGORY_DIALOG, USE_PROMPT_CATEGORY, REMOVE_DIALOG, CATEGORY_URL, IMAGE_CATEGORIES_URL, CATEGORY_FAILED,
        CATEGORY_MAX_LENGTH, MAX_CATEGORY_IMAGES} = await source;
    assert.deepEqual({...CATEGORY_DIALOG}, {title:'Categorize selected images', label:'Category', action:'Apply'});
    assert.equal(USE_PROMPT_CATEGORY, 'Use prompt category');
    assert.deepEqual({...REMOVE_DIALOG}, {title:'Use prompt category', action:'Remove'});
    assert.equal(CATEGORY_URL, '/prompt_manager/images/category');
    assert.equal(IMAGE_CATEGORIES_URL, '/prompt_manager/images/categories');
    assert.equal(CATEGORY_FAILED, 'Category could not be set. Try again.');
    assert.equal(CATEGORY_MAX_LENGTH, 100);
    assert.equal(MAX_CATEGORY_IMAGES, 500);
});

test('a set counts unchanged files as set and names files that could not be found', async () => {
    const {setOutcome} = await source;
    assert.equal(setOutcome({updated:1, unchanged:0, not_found:0}, 'Portraits'), 'Category “Portraits” set on 1 image.');
    assert.equal(setOutcome({updated:1, unchanged:2, not_found:0}, 'Scans'), 'Category “Scans” set on 3 images.');
    assert.equal(setOutcome({updated:2, unchanged:0, not_found:1}, 'Scans'), 'Category “Scans” set on 2 images. 1 could not be found.');
    assert.equal(setOutcome({updated:0, unchanged:1, not_found:3}, 'Scans'), 'Category “Scans” set on 1 image. 3 could not be found.');
    assert.equal(setOutcome({}, 'Scans'), 'Category “Scans” set on 0 images.');
});

test('a removal says how many lost their own category, or that none had one', async () => {
    const {clearOutcome, NONE_HAD_OWN} = await source;
    assert.equal(clearOutcome({cleared:1}), 'Removed the image category from 1 image.');
    assert.equal(clearOutcome({cleared:4}), 'Removed the image category from 4 images.');
    assert.equal(clearOutcome({cleared:0}), NONE_HAD_OWN);
    assert.equal(NONE_HAD_OWN, 'None of the selected images had its own category.');
});

test('the confirmation states the count', async () => {
    const {removeQuestion} = await source;
    assert.equal(removeQuestion(1), "Remove the image category from the 1 selected image? Each one then shows its prompt's " +
        'category, or none if it has no linked prompt. This cannot be undone.');
    assert.match(removeQuestion(1200), /^Remove the image category from the 1,200 selected images\? /);
});

test('coded failures have their own sentences, anything else the fixed one', async () => {
    const {failureMessage, NONE_FOUND, SOURCES_CHANGED, CATEGORY_FAILED} = await source;
    assert.equal(failureMessage({code:'files-not-found'}), NONE_FOUND);
    assert.equal(NONE_FOUND, 'None of the selected images could be found. Refresh the images and try again.');
    assert.equal(failureMessage({code:'source-changed'}), SOURCES_CHANGED);
    assert.equal(SOURCES_CHANGED, 'The sources changed. Refresh the images and try again.');
    assert.equal(failureMessage({code:'too-many-images', message:'Select at most 500 images.'}), 'Select at most 500 images.');
    for (const error of [null, undefined, new Error('x'), {code:'other', message:'secret'}]) {
        assert.equal(failureMessage(error), CATEGORY_FAILED);
    }
});

test('the toolbar list merges prompt and image categories once each', async () => {
    const {mergeCategories} = await source;
    assert.deepEqual(mergeCategories(['Landscapes', 'Portraits'], ['Scans', 'Landscapes']), ['Landscapes', 'Portraits', 'Scans']);
    assert.deepEqual(mergeCategories(['A', '', '  ', 7], null), ['A']);
    assert.deepEqual(mergeCategories(undefined, ['landscapes', 'Landscapes']), ['landscapes', 'Landscapes']);
});
