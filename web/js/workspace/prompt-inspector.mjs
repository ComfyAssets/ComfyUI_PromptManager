import { createScope } from '../shared/toolkit/core.js';
import { mountPromptBlocks } from './prompt-blocks.mjs';
import { createInspectionLoader } from './inspection-state.mjs';

export { promptRoles, mountPromptBlocks } from './prompt-blocks.mjs';
export { inspectPath, createInspectionLoader } from './inspection-state.mjs';

function childScope(parentScope) {
    const scope = createScope();
    const dispose = () => scope.dispose();
    parentScope.signal.addEventListener('abort', dispose, {once:true});
    scope.cleanup(() => parentScope.signal.removeEventListener('abort', dispose));
    if (parentScope.signal.aborted) scope.dispose();
    return scope;
}

export function mountPromptInspector(container, {api, image, sender, parentScope}) {
    const scope = childScope(parentScope);
    let loader;
    const blocks = mountPromptBlocks(container, {sender, parentScope:scope, onRetry:() => loader.load(image)});
    loader = createInspectionLoader(api, blocks.setState);
    scope.cleanup(() => loader.dispose());
    if (!scope.signal.aborted) loader.load(image);
    return {dispose:() => scope.dispose()};
}
