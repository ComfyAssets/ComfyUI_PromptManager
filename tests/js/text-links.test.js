// #343: a link can drive the editor nodes' text inputs (web/js/text-links.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/text-links.js');

// The frontend's own slot order for the Focused/Split/Pair schemas: forceInput sockets first, then the widget sockets.
function pairNode() {
    const inputs = [
        {name:'clip',type:'CLIP',link:null},
        {name:'prepend_text',type:'STRING',link:null},
        {name:'append_text',type:'STRING',link:null},
        {name:'text',type:'STRING',link:null,widget:{name:'text'},_widget:{name:'text'}},
        {name:'negative_text',type:'STRING',link:null,widget:{name:'negative_text'}},
        {name:'category',type:'STRING',link:null,widget:{name:'category'}},
    ];
    return {inputs};
}

test('the editor note says the value comes from a link and that typed text is kept', async () => {
    const {linkNoteText} = await source;
    assert.equal(linkNoteText('Negative prompt'), 'Negative prompt comes from a link. Your typed text is kept for when you disconnect it.');
});

test('a linked widget stores its typed value; an unlinked one its live editor text', async () => {
    const {storedWidgetValue} = await source;
    const widget = {name:'text', value:'typed', inputEl:{value:'live'}};
    assert.equal(storedWidgetValue({inputs:[{name:'text',link:3}]}, widget), 'typed');
    assert.equal(storedWidgetValue({inputs:[{name:'text',link:null}]}, widget), 'live');
    assert.equal(storedWidgetValue({}, {name:'tags', value:'a, b'}), 'a, b');
});

test('exposing a socket detaches only the named inputs from their hidden widgets', async () => {
    const {exposeTextSockets} = await source;
    const node = pairNode();
    assert.deepEqual(exposeTextSockets(node, ['text','negative_text']), ['text','negative_text']);
    const byName = Object.fromEntries(node.inputs.map(input => [input.name,input]));
    for (const name of ['text','negative_text']) {
        assert.equal('widget' in byName[name], false);
        assert.equal(byName[name]._widget, undefined);
        assert.equal(byName[name].type, 'STRING');
    }
    assert.deepEqual(byName.category.widget, {name:'category'});
    assert.deepEqual(exposeTextSockets(node, ['text','negative_text']), [], 'idempotent');
    assert.deepEqual(exposeTextSockets({}, ['text']), [], 'a node without inputs is left alone');
});

test('exposing a socket keeps an existing link and the widget value', async () => {
    const {exposeTextSockets,isLinked} = await source;
    const node = pairNode();
    node.inputs[3].link = 12;
    exposeTextSockets(node, ['text']);
    assert.equal(node.inputs[3].link, 12);
    assert.equal(isLinked(node,'text'), true);
    assert.equal(isLinked(node,'negative_text'), false);
    assert.equal(isLinked({},'text'), false);
});

test('a STRING link dropped on the editor prefers the first free text input over prepend', async () => {
    const {preferredTextSlot} = await source;
    const node = pairNode();
    assert.deepEqual(preferredTextSlot(node, 'STRING', ['text','negative_text']), {index:3,slot:node.inputs[3]});
    node.inputs[3].link = 4;
    assert.deepEqual(preferredTextSlot(node, 'STRING', ['text','negative_text']), {index:4,slot:node.inputs[4]});
    node.inputs[4].link = 5;
    assert.equal(preferredTextSlot(node, 'STRING', ['text','negative_text']), undefined, 'both linked: the frontend chooses');
});

test('only STRING-compatible links are steered to a text input', async () => {
    const {preferredTextSlot} = await source;
    const node = pairNode();
    for (const type of ['STRING','STRING,INT','*','']) assert.equal(preferredTextSlot(node, type, ['text'])?.index, 3, type);
    for (const type of ['CLIP','INT','IMAGE',undefined,null]) assert.equal(preferredTextSlot(node, type, ['text']), undefined, String(type));
    assert.equal(preferredTextSlot({}, 'STRING', ['text']), undefined);
});

test('installing the preference wraps findInputByType and restores nothing it did not own', async () => {
    const {preferTextInputs} = await source;
    const calls = [];
    const prototype = {findInputByType(type) { calls.push(type); return {index:1,slot:this.inputs[1]}; }};
    preferTextInputs(prototype, ['text']);
    const node = Object.assign(Object.create(prototype), pairNode());
    assert.equal(node.findInputByType('STRING').index, 3);
    assert.deepEqual(calls, []);
    assert.equal(node.findInputByType('CLIP').index, 1);
    node.inputs[3].link = 9;
    assert.equal(node.findInputByType('STRING').index, 1, 'the main prompt is taken: fall back to the frontend');
    assert.deepEqual(calls, ['CLIP','STRING']);
    const bare = {};
    preferTextInputs(bare, ['text']);
    assert.equal(bare.findInputByType, undefined, 'an older frontend without findInputByType is left unchanged');
});
