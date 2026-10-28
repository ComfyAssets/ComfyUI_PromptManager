// #90: a PromptManager node's `save_to_library` switch. Off: PromptManager ignores the node's runs (nothing saved,
// counted or linked); the node's output is unchanged.
//
// The input is declared last, so it takes the widget index an older workflow used for the editor's own
// (non-serialized) DOM widget, where it stored `''` or nothing. ComfyUI coerces a BOOLEAN with `bool(value)`, so that
// `''` would turn saving off: after a workflow loads, anything that is not a boolean is the default, on.

export const SAVE_WIDGET = 'save_to_library';

export function saveWidget(node) {
    return node?.widgets?.find(widget => widget?.name === SAVE_WIDGET);
}

/** Whether the node saves its runs (a missing widget, as on an older backend, is on). */
export function savesToLibrary(node) {
    return saveWidget(node)?.value !== false;
}

/** Set a non-boolean switch value (from an older workflow) to on. Returns whether it changed. */
export function normalizeSaveWidget(node) {
    const widget = saveWidget(node);
    if (!widget || typeof widget.value === 'boolean') return false;
    widget.value = true;
    const index = node.widgets.indexOf(widget);
    if (Array.isArray(node.widgets_values) && index >= 0) node.widgets_values[index] = true;
    return true;
}

/** Move the switch after every other widget the node has now, so its index is the same on every frontend. */
export function placeSaveWidgetLast(node) {
    const widget = saveWidget(node);
    const widgets = node?.widgets;
    if (!widget || widgets.at(-1) === widget) return false;
    widgets.splice(widgets.indexOf(widget), 1);
    widgets.push(widget);
    return true;
}

/** Turn the switch on or off the way a native widget edit does. */
export function setSaveToLibrary(node, value) {
    const widget = saveWidget(node);
    if (!widget) return;
    widget.value = Boolean(value);
    const index = node.widgets.indexOf(widget);
    if (Array.isArray(node.widgets_values) && index >= 0) node.widgets_values[index] = widget.value;
    widget.callback?.(widget.value, undefined, node);
}
