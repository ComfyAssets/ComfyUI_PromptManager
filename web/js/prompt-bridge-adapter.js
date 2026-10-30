import {validateRoles} from './shared/toolkit/prompt-bridge.js';

const schemas = new Map([
    ['PromptManager', {positive:'text'}],
    ['PromptManagerText', {positive:'text'}],
    ['PromptManagerFocused', {positive:'text'}],
    ['PromptManagerSplit', {positive:'text'}],
    ['PromptManagerPair', {positive:'text',negative:'negative_text'}],
]);
const compositionNames = ['prepend_text','append_text'];

function nodes(graph) { return graph?._nodes || []; }
function target(node) { return {id:String(node.id),label:String(node.title || node.type),type:node.type}; }
function connected(node,name) { return Boolean(node.inputs?.some(input => input.name === name && input.link != null)); }
function linkedRoles(node) { return Object.entries(schemas.get(node.type)).filter(([,name]) => connected(node,name)).map(([role]) => role); }
function links(node) { return JSON.stringify((node.inputs || []).map(input => [input.name,input.link ?? null])); }
function capture(node,name) {
    const widget = node.widgets?.find(item => item.name === name);
    if (!widget) return {name,widget:null};
    const index = node.widgets.indexOf(widget);
    return {name,widget,index,value:widget.value,input:widget.inputEl,visible:widget.inputEl?.value,serialized:node.widgets_values?.[index],hasSerialization:Array.isArray(node.widgets_values)};
}
function sameField(node,field) {
    const now = capture(node,field.name);
    const unchanged = ['widget','index','value','input','visible'].every(key => now[key] === field[key]);
    // Host serialization may materialize or synchronize its cache from live values.
    const serialized = now.hasSerialization === field.hasSerialization && now.serialized === field.serialized;
    const normalized = now.hasSerialization && now.serialized === field.value;
    return unchanged && (serialized || normalized);
}
function composition(node,fields,roles) {
    const value = name => String(fields.find(field => field.name === name)?.value ?? '');
    const prependConnected = connected(node,'prepend_text'), appendConnected = connected(node,'append_text');
    const mainConnected = connected(node,'text');
    const combine = main => [value('prepend_text').trim(),main,value('append_text').trim()].filter(Boolean).join(' ');
    return {prepend:value('prepend_text'),append:value('append_text'),prependConnected,appendConnected,
        before:prependConnected || appendConnected || mainConnected ? null : combine(value('text')),
        after:prependConnected || appendConnected || (mainConnected && !Object.hasOwn(roles,'positive')) ? null : combine(roles.positive ?? value('text'))};
}
function write(node,field,value) {
    field.widget.value = value;
    if (field.widget.inputEl) field.widget.inputEl.value = value;
    if (node.widgets_values) node.widgets_values[field.index] = value;
}
function redraw(app,node) {
    node._focusedView?.render();
    node.setDirtyCanvas?.(true,true);
    app.graph?.setDirtyCanvas?.(true,true);
}

// The renderer this window draws nodes with (System › Integrations, S10): Nodes 2.0 while LiteGraph's Vue node mode is
// on, else Classic; unknown (omitted) without LiteGraph.
function renderer(runtime) {
    const graph = runtime?.LiteGraph;
    if (!graph) return {};
    return {renderer:graph.vueNodesMode === true ? 'nodes2' : 'classic'};
}

export function createPromptAdapter(app, {runtime = globalThis} = {}) {
    const snapshots = new WeakMap();
    // Loading a workflow makes new node objects but can land on the same graph version, so a revision needs both.
    const nodeSerials = new WeakMap();
    let lastSerial = 0;
    function revision(node) {
        if (!nodeSerials.has(node)) nodeSerials.set(node,++lastSerial);
        return `${nodeSerials.get(node)}:${app.graph?._version}`;
    }
    function listTargets() {
        const drawn = renderer(runtime);
        return nodes(app.graph).filter(node => schemas.has(node.type)).map(node => ({...target(node),roles:Object.keys(schemas.get(node.type)),linkedRoles:linkedRoles(node),revision:revision(node),...drawn}));
    }
    function preview(targetId,rawRoles) {
        const roles = validateRoles(rawRoles);
        const graph = app.graph;
        const node = nodes(graph).find(item => String(item.id) === targetId);
        const schema = schemas.get(node?.type);
        if (!schema) throw new Error('Prompt destination is unavailable.');
        const fields = [...new Set([...Object.values(schema),...compositionNames,'category','tags'])].map(name => capture(node,name));
        const requested = Object.entries(roles).map(([role,after]) => {
            if (!schema[role]) throw new Error(`Destination does not support ${role}.`);
            const field = fields.find(item => item.name === schema[role]);
            if (!field.widget) throw new Error(`Destination widget ${schema[role]} is unavailable.`);
            if (connected(node,field.name)) throw new Error(`${role} is connected to an upstream input.`);
            return {role,after,field};
        });
        const snapshot = Object.freeze({});
        snapshots.set(snapshot,{graph,node,type:node.type,id:String(node.id),version:graph._version,widgets:node.widgets,widgetOrder:[...node.widgets],fields,links:links(node),requested});
        return {preview:{target:target(node),revision:revision(node),fields:requested.map(({role,after,field}) => ({role,before:String(field.value ?? ''),after})),composition:composition(node,fields,roles)},snapshot};
    }
    function apply(snapshot) {
        const state = snapshots.get(snapshot);
        if (!state) throw new Error('Invalid or expired prompt preview snapshot.');
        const {graph,node,fields,requested} = state;
        if (app.graph !== graph || !nodes(graph).includes(node) || String(node.id) !== state.id || node.widgets !== state.widgets || node.type !== state.type || graph._version !== state.version || links(node) !== state.links || fields.some(field => !sameField(node,field))) throw new Error('Workflow or prompt changed. Preview again.');
        snapshots.delete(snapshot);
        const serializedBefore = node.widgets_values;
        const rollbackFields = fields.map(field => capture(node,field.name));
        try {
            node.widgets_values ||= node.widgets.map(widget => widget.value);
            for (const {field,after} of requested) write(node,field,after);
            for (const {field,after} of requested) field.widget.callback?.(after,app.canvas,node,null,null);
            redraw(app,node);
            if (app.graph !== graph || !nodes(graph).includes(node) || String(node.id) !== state.id || node.widgets !== state.widgets || node.type !== state.type || links(node) !== state.links || fields.some(field => {
                const current = capture(node,field.name);
                return current.widget !== field.widget || current.index !== field.index || current.input !== field.input;
            })) throw new Error('Workflow changed during prompt update.');
            if (fields.some(field => field.widget && !requested.some(item => item.field === field) && !sameField(node,{...field,hasSerialization:true,serialized:field.hasSerialization ? field.serialized : field.value}))) throw new Error('Protected prompt fields changed during update.');
            for (const {field,after} of requested) {
                if (field.widget.value !== after || (field.widget.inputEl && field.widget.inputEl.value !== after) || (!Array.isArray(node.widgets_values) || node.widgets_values[field.index] !== after)) throw new Error('Prompt values do not match the approved preview.');
            }
        } catch (error) {
            // Widget callbacks are host hooks; restore our fields without repeating them.
            node.widgets = state.widgets;
            node.widgets.splice(0,node.widgets.length,...state.widgetOrder);
            node.widgets_values = serializedBefore;
            for (const field of rollbackFields.filter(item => item.widget)) {
                node.widgets[field.index] = field.widget;
                field.widget.value = field.value;
                if (field.widget.inputEl !== field.input) field.widget.inputEl = field.input;
                if (field.input) field.input.value = field.visible;
                if (field.hasSerialization) node.widgets_values[field.index] = field.serialized;
            }
            node.widgets_values = serializedBefore;
            try { redraw(app,node); } catch { /* Preserve the original callback failure. */ }
            throw new Error(`Prompt update failed: ${error.message}`);
        }
        return {targetId:state.id,values:Object.fromEntries(requested.map(({role,after}) => [role,after]))};
    }
    return {listTargets,preview,apply};
}
