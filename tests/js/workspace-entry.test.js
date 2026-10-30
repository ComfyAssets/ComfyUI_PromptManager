// #525: where the canvas buttons open PromptManager (web/js/workspace-entry.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = import('../../web/js/workspace-entry.js');
const state = import('../../web/js/workspace/state.mjs');

test('the canvas entry route is the workspace Library, a native route', async () => {
    const { WORKSPACE_ENTRY_ROUTE } = await source;
    const { resolveRoute } = await state;
    assert.equal(WORKSPACE_ENTRY_ROUTE, '/prompt_manager/workspace#/library');
    const [pathname, hash] = WORKSPACE_ENTRY_ROUTE.split('#');
    assert.equal(pathname, '/prompt_manager/workspace');
    assert.equal(resolveRoute(`#${hash}`).id, 'library');
});

test('/prompt_manager/web redirects to the same route, under any mount prefix', async () => {
    const { WORKSPACE_ENTRY_ROUTE } = await source;
    const html = fs.readFileSync(path.join(__dirname, '../../web/index.html'), 'utf8');
    const [, pattern, flags, replacement] = html.match(/location\.replace\(\s*window\.location\.pathname\.replace\(\/(.+)\/([a-z]*), '([^']+)'\)/);
    const redirect = pathname => pathname.replace(new RegExp(pattern, flags), replacement);
    assert.equal(redirect('/prompt_manager/web'), WORKSPACE_ENTRY_ROUTE);
    assert.equal(redirect('/prompt_manager/web/'), WORKSPACE_ENTRY_ROUTE);
    assert.equal(redirect('/prompt_manager/index.html'), WORKSPACE_ENTRY_ROUTE);
    assert.equal(redirect('/sub/prompt_manager/web'), `/sub${WORKSPACE_ENTRY_ROUTE}`);
});

test('no canvas entry point still names a legacy page', () => {
    for (const file of ['web/comfy/prompt_manager.js', 'web/js/focused-node.js', 'web/js/classic-node.js']) {
        const code = fs.readFileSync(path.join(__dirname, '../..', file), 'utf8');
        assert.doesNotMatch(code, /\/prompt_manager\/(?:admin|gallery|web)\b/, file);
    }
});
