const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function fixture() {
    const requests = [];
    const grid = { replaceChildren() {}, appendChild() {} };
    const context = vm.createContext({ window: {}, URLSearchParams, console: { error() {} },
        requestAnimationFrame() {}, document: { getElementById: id => id === 'tagPromptsGrid' ? grid : null,
            querySelector: () => null, createElement: () => ({ appendChild() {} }) },
    });
    const Manager = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/tags-page.js'), 'utf8') + '\nTagsPageManager;', context);
    const manager = Object.create(Manager.prototype);
    Object.assign(manager, { selectedTags: ['cats'], filterMode: 'and', promptLimit: 20,
        promptOffset: 0, isLoadingPrompts: false, prompts: [], hasMorePrompts: false,
        fetchJson(url) { return new Promise((resolve, reject) => requests.push({ url, resolve, reject })); },
        renderTagPrompts() {}, appendTagPrompts() {}, updatePromptCountBadge() {}, updateLoadMoreButton() {},
    });
    const reply = (index, text, more = false) => requests[index].resolve({ success: true,
        prompts: [{ text }], pagination: { has_more: more, total: more ? 40 : 1 } });
    return { manager, requests, reply };
}
test('changing a filter starts a new request and late results cannot overwrite it', async () => {
    const { manager, requests, reply } = fixture();
    const old = manager.loadTagPrompts();
    manager.selectedTags = ['dogs'];
    const current = manager.loadTagPrompts();
    assert.equal(requests.length, 2);
    assert.match(requests[1].url, /dogs/);
    reply(1, 'dog prompt'); await current;
    reply(0, 'cat prompt', true); await old;
    assert.equal(manager.prompts[0].text, 'dog prompt');
    assert.equal(manager.promptTotal, 1);
    assert.equal(manager.hasMorePrompts, false);
});
test('clearing selection invalidates pending results', async () => {
    const { manager, reply } = fixture();
    const old = manager.loadTagPrompts();
    manager.selectedTags = [];
    manager.clearPrompts();
    reply(0, 'cat prompt'); await old;
    assert.equal(manager.prompts.length, 0);
    assert.equal(manager.promptTotal, 0);
    assert.equal(manager.isLoadingPrompts, false);
});
test('a stale pagination response cannot append rows from the previous filter', async () => {
    const { manager, requests, reply } = fixture();
    Object.assign(manager, { prompts: [{ text: 'cat first page' }], hasMorePrompts: true, promptOffset: 20 });
    const old = manager.loadMoreTagPrompts();
    manager.selectedTags = ['dogs'];
    const current = manager.loadTagPrompts();
    assert.equal(requests.length, 2);
    reply(1, 'dog prompt'); await current;
    reply(0, 'cat second page', true); await old;
    assert.equal(manager.prompts.length, 1);
    assert.equal(manager.prompts[0].text, 'dog prompt');
    assert.equal(manager.promptOffset, 20);
});
test('a stale failure cannot finish the new request or clear its loading state', async () => {
    const { manager, requests, reply } = fixture();
    const old = manager.loadTagPrompts();
    manager.selectedTags = ['dogs'];
    const current = manager.loadTagPrompts();
    assert.equal(requests.length, 2);
    requests[0].reject(new Error('old failure')); await old;
    assert.equal(manager.isLoadingPrompts, true);
    reply(1, 'dog prompt'); await current;
    assert.equal(manager.isLoadingPrompts, false);
    assert.equal(manager.prompts[0].text, 'dog prompt');
});
