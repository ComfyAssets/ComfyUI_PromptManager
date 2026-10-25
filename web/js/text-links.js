// #343: let a link drive the editor nodes' prompt text, the way a link drives any core widget.
//
// The Focused, Split and Pair editors hide their widgets to draw their own editor. ComfyUI's frontend draws
// and hit-tests a widget's input socket only while the widget itself is visible, so `text` (and the Pair's
// `negative_text`) had no socket, and a STRING link dropped on the editor fell through to the first free
// STRING input, `prepend_text` (#342). Detaching those inputs from their hidden widgets makes them ordinary
// sockets in both renderers; the schema, the widgets and their saved values are unchanged.

/** What an editor shows in place of a text field whose value comes from a link. */
export function linkNoteText(label) {
    return `${label} comes from a link. Your typed text is kept for when you disconnect it.`;
}

export function isLinked(node, name) {
    return Boolean(node?.inputs?.some(input => input.name === name && input.link != null));
}

/** Detach the named inputs from their hidden widgets so the frontend shows them as sockets. Returns the names changed. */
export function exposeTextSockets(node, names) {
    const exposed = [];
    for (const input of node?.inputs || []) {
        if (!names.includes(input.name) || !('widget' in input || input._widget)) continue;
        delete input.widget;
        if ('_widget' in input) input._widget = undefined;
        exposed.push(input.name);
    }
    return exposed;
}

function acceptsString(type) {
    if (type == null) return false;
    return String(type).split(',').some(part => ['STRING','*',''].includes(part.trim()));
}

/** The first free text input, in editor order, for a link of `type`; undefined leaves the choice to the frontend. */
export function preferredTextSlot(node, type, names) {
    if (!acceptsString(type)) return undefined;
    const inputs = node?.inputs || [];
    for (const name of names) {
        const index = inputs.findIndex(input => input.name === name);
        if (index >= 0 && inputs[index].link == null) return {index, slot:inputs[index]};
    }
    return undefined;
}

/** A link dropped on the editor body (not on a socket) fills the main prompt first, then the negative. */
export function preferTextInputs(prototype, names) {
    const original = prototype.findInputByType;
    if (typeof original !== 'function') return;
    prototype.findInputByType = function (type, ...rest) {
        return preferredTextSlot(this, type, names) ?? original.call(this, type, ...rest);
    };
}

/**
 * The value a workflow stores for a widget. A linked widget keeps what the user typed (its value), so the text
 * returns when the link is disconnected; an unlinked one stores its live editor text.
 */
export function storedWidgetValue(node, widget) {
    if (isLinked(node, widget.name)) return widget.value;
    return widget.inputEl ? widget.inputEl.value : widget.value;
}
