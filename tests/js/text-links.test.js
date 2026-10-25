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

test('only exact STRING links are steered; wildcards keep the frontend choice (#343 review M2)', async () => {
    const {preferredTextSlot} = await source;
    const node = pairNode();
    for (const type of ['STRING','STRING,INT',' STRING ']) assert.equal(preferredTextSlot(node, type, ['text'])?.index, 3, type);
    for (const type of ['*','','CLIP','INT','IMAGE',undefined,null,0]) assert.equal(preferredTextSlot(node, type, ['text']), undefined, String(type));
    assert.equal(preferredTextSlot({}, 'STRING', ['text']), undefined);
});

test('a link being moved off a text input leaves it free, as the frontend counts it (#343 review I2)', async () => {
    const {preferredTextSlot} = await source;
    const links = new Map([[7,{id:7,_dragging:true}],[8,{id:8}]]);
    const node = Object.assign(pairNode(), {graph:{getLink:id => links.get(id)}});
    node.inputs[3].link = 7;
    assert.equal(preferredTextSlot(node, 'STRING', ['text','negative_text']).slot.name, 'text', 'the dragged link does not block text');
    links.get(7)._dragging = false;
    assert.equal(preferredTextSlot(node, 'STRING', ['text','negative_text']).slot.name, 'negative_text');
    node.inputs[4].link = 8;
    assert.equal(preferredTextSlot(node, 'STRING', ['text','negative_text']), undefined);
    node.inputs[4].link = 99;
    assert.equal(preferredTextSlot(node, 'STRING', ['text','negative_text']).slot.name, 'negative_text', 'a link the graph no longer has is free');
    const legacy = Object.assign(pairNode(), {graph:{links:{5:{id:5,_dragging:true}}}});
    legacy.inputs[3].link = 5;
    assert.equal(preferredTextSlot(legacy, 'STRING', ['text']).slot.name, 'text', 'object-keyed link tables too');
});

test('a legacy frontend Primitive feeding a text input supplies its value (#343 review I1)', async () => {
    const {primitiveTextValue} = await source;
    const nodes = new Map();
    const links = new Map();
    const graph = {getNodeById:id => nodes.get(id), getLink:id => links.get(id)};
    const add = (id, node) => { nodes.set(id, {id, graph, ...node}); return nodes.get(id); };
    const target = add(1, {inputs:[{name:'text',link:10},{name:'negative_text',link:11},{name:'prepend_text',link:12},{name:'category',link:null}]});
    add(2, {type:'PrimitiveNode', isVirtualNode:true, widgets:[{value:'From the primitive'}]});
    add(3, {type:'Reroute', isVirtualNode:true, inputs:[{link:13}]});
    add(4, {type:'PrimitiveNode', isVirtualNode:true, widgets:[{value:42}]});
    add(5, {type:'PrimitiveStringMultiline', widgets:[{value:'a real node'}]});
    links.set(10, {origin_id:2}); links.set(11, {origin_id:3}); links.set(13, {origin_id:4}); links.set(12, {origin_id:5});
    assert.equal(primitiveTextValue(target, 'text'), 'From the primitive');
    assert.equal(primitiveTextValue(target, 'negative_text'), '42', 'through a virtual reroute');
    assert.equal(primitiveTextValue(target, 'prepend_text'), undefined, 'a real node is serialized as a link');
    assert.equal(primitiveTextValue(target, 'category'), undefined, 'not linked');
    assert.equal(primitiveTextValue(target, 'missing'), undefined);
    nodes.get(2).widgets = [];
    assert.equal(primitiveTextValue(target, 'text'), undefined, 'a primitive without its widget yet');
    links.set(13, {origin_id:3});
    assert.equal(primitiveTextValue(target, 'negative_text'), undefined, 'a reroute cycle ends');
    assert.equal(primitiveTextValue({inputs:[{name:'text',link:1}]}, 'text'), undefined, 'no graph');
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

test('a drop point over a visible prompt box or its note picks that box input (#343 review M1)', async () => {
    const {textInputAtPoint} = await source;
    const box = (left, top, width, height) => ({getBoundingClientRect:() => ({left, top, width, height, right:left + width, bottom:top + height})});
    const node = pairNode();
    const targets = [{name:'text',element:box(0,0,100,100)},{name:'negative_text',element:box(120,0,100,100)},{name:'text',element:box(0,0,0,0)},{name:'missing',element:box(300,0,50,50)}];
    assert.equal(textInputAtPoint(node, [150,50], targets).name, 'negative_text');
    assert.equal(textInputAtPoint(node, [10,10], targets).name, 'text');
    assert.equal(textInputAtPoint(node, [110,50], targets), undefined, 'between the boxes');
    assert.equal(textInputAtPoint(node, [320,10], targets), undefined, 'a target whose input the node lacks');
    assert.equal(textInputAtPoint(node, [0,0], [{name:'text',element:box(0,0,0,0)}]), undefined, 'a hidden target');
});

test('a linked Classic text box gives its row back and gets its size back on disconnect (#343 review M5)', async () => {
    const {collapseLinkedWidget} = await source;
    const own = () => 120;
    const widget = {options:{getMinHeight:own}};
    collapseLinkedWidget(widget, true);
    assert.equal(widget.options.getMinHeight(), 0);
    assert.equal(widget.options.getMaxHeight(), 0);
    collapseLinkedWidget(widget, true);
    collapseLinkedWidget(widget, false);
    assert.equal(widget.options.getMinHeight, own);
    assert.equal('getMaxHeight' in widget.options, false, 'an option it did not have is removed again');
    collapseLinkedWidget(widget, false);
    assert.equal(widget.options.getMinHeight, own);
    collapseLinkedWidget(undefined, true);
    collapseLinkedWidget({}, true);
});
