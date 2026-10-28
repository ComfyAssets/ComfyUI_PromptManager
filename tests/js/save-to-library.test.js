// #90: the node's save_to_library switch (web/js/save-to-library.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/save-to-library.js');

function classicNode(saved) {
    const names = ['text', 'category', 'tags', 'search_text', 'prepend_text', 'append_text', 'save_to_library', 'prompt_manager_search_ui'];
    const widgets = names.map(name => ({name, value: name === 'save_to_library' ? true : ''}));
    const node = {widgets, widgets_values: saved ? [...saved] : undefined};
    // What a host configure does: each saved value goes to the widget at its index.
    (saved || []).forEach((value, index) => { if (widgets[index]) widgets[index].value = value; });
    return node;
}

test('an older workflow stored the editor slot where the switch is now: it loads on', async () => {
    const {normalizeSaveWidget, savesToLibrary} = await source;
    const node = classicNode(['a cat', 'c', 't', '', '', '', '']);
    assert.equal(node.widgets[6].value, '', 'the host put the old DOM slot value into the switch');
    assert.equal(normalizeSaveWidget(node), true);
    assert.equal(node.widgets[6].value, true);
    assert.equal(node.widgets_values[6], true);
    assert.equal(savesToLibrary(node), true);
    assert.deepEqual(node.widgets_values.slice(0, 6), ['a cat', 'c', 't', '', '', ''], 'no earlier value moved');
});

test('null, undefined and missing slots load on; saved booleans are kept', async () => {
    const {normalizeSaveWidget, savesToLibrary} = await source;
    for (const value of [null, undefined, 0, 'false']) {
        const node = classicNode(['a', '', '', '', '', '', value]);
        normalizeSaveWidget(node);
        assert.equal(savesToLibrary(node), true, String(value));
    }
    const short = classicNode(['a', '', '', '', '', '']);
    assert.equal(normalizeSaveWidget(short), false);
    assert.equal(savesToLibrary(short), true);
    const off = classicNode(['a', '', '', '', '', '', false]);
    assert.equal(normalizeSaveWidget(off), false);
    assert.equal(savesToLibrary(off), false);
});

test('a node without the widget (an older backend) saves', async () => {
    const {normalizeSaveWidget, savesToLibrary} = await source;
    assert.equal(savesToLibrary({widgets: [{name: 'text', value: ''}]}), true);
    assert.equal(normalizeSaveWidget({widgets: []}), false);
    assert.equal(savesToLibrary(undefined), true);
});

test('the switch moves after the editor fallbacks so every frontend has one order', async () => {
    const {placeSaveWidgetLast} = await source;
    const widgets = ['text', 'category', 'tags', 'search_text', 'save_to_library', 'prepend_text', 'append_text'].map(name => ({name}));
    const node = {widgets};
    assert.equal(placeSaveWidgetLast(node), true);
    assert.deepEqual(node.widgets.map(widget => widget.name), ['text', 'category', 'tags', 'search_text', 'prepend_text', 'append_text', 'save_to_library']);
    assert.equal(placeSaveWidgetLast(node), false, 'already last');
    assert.equal(placeSaveWidgetLast({widgets: [{name: 'text'}]}), false);
});

test('setting the switch updates the widget, its saved value and notifies the host', async () => {
    const {setSaveToLibrary, savesToLibrary} = await source;
    const calls = [];
    const node = classicNode(['a', '', '', '', '', '', true]);
    node.widgets[6].callback = value => calls.push(value);
    setSaveToLibrary(node, false);
    assert.equal(savesToLibrary(node), false);
    assert.equal(node.widgets_values[6], false);
    assert.deepEqual(calls, [false]);
    setSaveToLibrary({widgets: []}, false);
});
