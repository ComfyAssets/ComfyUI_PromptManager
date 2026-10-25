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
    // Only exact STRING links: a wildcard (a legacy Primitive before it is typed, an untyped reroute) keeps the
    // frontend's own choice, and is aimed at a prompt by dropping it on the prompt box or its socket.
    return type != null && String(type).split(',').some(part => part.trim() === 'STRING');
}

function linkOf(graph, id) {
    if (id == null || !graph) return undefined;
    return graph.getLink?.(id) ?? graph.links?.get?.(id) ?? graph.links?.[id];
}

// The frontend's own free test: an input whose link is being moved (`_dragging`), or whose link the graph no
// longer has, takes a drop. Without a graph only the input's own link field is known.
function isFree(node, input) {
    if (input.link == null) return true;
    if (!node.graph) return false;
    const link = linkOf(node.graph, input.link);
    return link == null || Boolean(link._dragging);
}

/** The first free text input, in editor order, for a link of `type`; undefined leaves the choice to the frontend. */
export function preferredTextSlot(node, type, names) {
    if (!acceptsString(type)) return undefined;
    const inputs = node?.inputs || [];
    for (const name of names) {
        const index = inputs.findIndex(input => input.name === name);
        if (index >= 0 && isFree(node, inputs[index])) return {index, slot:inputs[index]};
    }
    return undefined;
}

const MAX_VIRTUAL_HOPS = 32;

/**
 * The value a legacy frontend Primitive (a virtual node) supplies to a linked text input, following virtual
 * reroutes; undefined when the input is unlinked or its source is a real node (serialized as a link).
 *
 * A Primitive writes its value into the target's widget before the workflow is queued. The editor text inputs
 * are no longer bound to their hidden widgets, so the queue path fills the value in instead (#343 review I1).
 */
export function primitiveTextValue(node, name) {
    const input = node?.inputs?.find(item => item.name === name);
    const graph = node?.graph;
    if (input?.link == null || !graph) return undefined;
    let link = linkOf(graph, input.link);
    for (let hop = 0; link && hop < MAX_VIRTUAL_HOPS; hop++) {
        const origin = graph.getNodeById?.(link.origin_id);
        if (!origin?.isVirtualNode) return undefined;
        if (origin.type === 'PrimitiveNode') {
            const value = origin.widgets?.[0]?.value;
            return value == null ? undefined : String(value);
        }
        link = linkOf(graph, origin.inputs?.[0]?.link);
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

/** The text input whose visible drop target (a prompt box or its link note) contains a viewport point. */
export function textInputAtPoint(node, [x, y], targets) {
    for (const {name, element} of targets) {
        const box = element.getBoundingClientRect();
        if (!box.width || !box.height || x < box.left || x > box.right || y < box.top || y > box.bottom) continue;
        const input = node?.inputs?.find(item => item.name === name);
        if (input) return input;
    }
    return undefined;
}

const LAYOUT_OPTIONS = ['getMinHeight','getMaxHeight'];

/**
 * While its input is linked, the frontend hides a core text box but keeps its row, leaving an empty gap in the
 * Classic renderer. Give the row back while linked and restore the widget's own sizing on disconnect.
 */
export function collapseLinkedWidget(widget, linked) {
    const options = widget?.options;
    if (!options) return;
    if (linked && !widget._pmLinkedLayout) {
        widget._pmLinkedLayout = Object.fromEntries(LAYOUT_OPTIONS.filter(name => name in options).map(name => [name, options[name]]));
        for (const name of LAYOUT_OPTIONS) options[name] = () => 0;
    } else if (!linked && widget._pmLinkedLayout) {
        for (const name of LAYOUT_OPTIONS) {
            if (name in widget._pmLinkedLayout) options[name] = widget._pmLinkedLayout[name];
            else delete options[name];
        }
        delete widget._pmLinkedLayout;
    }
}
