const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ImageHelpers = require('../../web/js/image-helpers.js');
function deferred() {
    let resolve;
    const promise = new Promise(yes => { resolve = yes; });
    return { promise, resolve };
}
function fixture() {
    const elements = new Map();
    const requests = [];
    const document = { addEventListener() {}, getElementById(id) {
        if (!elements.has(id)) elements.set(id, {});
        return elements.get(id);
    } };
    const context = vm.createContext({ document, window: {}, ImageHelpers,
        escapeHtml: value => value, console: { error() {} },
        ApiClient: { resolveUrl: value => value, fetchUrl: (url, options) => {
            const request = deferred();
            requests.push({ url, body: JSON.parse(options.body), ...request });
            return request.promise;
        } },
    });
    const Manager = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/gallery.js'), 'utf8') + '\nGalleryManager;', context, { filename: path.join(__dirname, '../../web/js/gallery.js') });
    const manager = Object.create(Manager.prototype);
    const notifications = [];
    Object.assign(manager, { autoTagState: { reviewImages: [
        { id: 'digest', path: 'same.png', root_index: 1, url: '/same.png' },
        { id: 'other', path: 'next.png', root_index: 0, url: '/next.png' },
    ], reviewIndex: 0, currentTags: [], modelType: 'gguf' },
    renderReviewTags() {}, showNotification: (...args) => notifications.push(args), hideModal() {}, loadImages() {},
    });
    const reply = (index, data) => requests[index].resolve({ json: async () => data });
    return { manager, requests, reply, notifications };
}
const SHA = 'a'.repeat(64);
test('gallery review retains root identity and applies the tags to the image by its sha256 (#379)', async () => {
    const { manager, requests, reply } = fixture();
    const load = manager.loadNextReviewImage();
    assert.equal(requests[0].body.root_index, 1);
    assert.equal(requests[0].body.existing, 'merge');
    reply(0, { success: true, prompt_id: 42, tags: ['smile'], sha256: SHA, image_tags: [] });
    await load;
    manager.loadNextReviewImage = () => {};
    const apply = manager.applyReviewTags();
    assert.equal(requests[1].url, '/prompt_manager/autotag/apply');
    assert.deepEqual(requests[1].body, ImageHelpers.autotagApplyBody(
        { id: 'digest', path: 'same.png', root_index: 1 }, SHA, ['smile']));
    assert.equal('prompt_id' in requests[1].body, false);
    reply(1, { success: true });
    await apply;
});
test('an unlinked image applies to the image like a linked one', async () => {
    const { manager, requests, reply } = fixture();
    const load = manager.loadNextReviewImage();
    reply(0, { success: true, prompt_id: null, tags: ['smile'], sha256: SHA, image_tags: [] });
    await load;
    manager.loadNextReviewImage = () => {};
    const apply = manager.applyReviewTags();
    assert.equal(requests[1].body.sha256, SHA);
    assert.deepEqual(requests[1].body.tags, ['smile']);
    reply(1, { success: true });
    await apply;
});
test('an answer without a captured sha256 never sends an apply', async () => {
    const { manager, requests, reply } = fixture();
    const load = manager.loadNextReviewImage();
    reply(0, { success: true, prompt_id: 42, tags: ['smile'] });
    await load;
    manager.loadNextReviewImage = () => {};
    await manager.applyReviewTags();
    assert.equal(requests.filter(r => r.url === '/prompt_manager/autotag/apply').length, 0);
});
test('a failed apply keeps the current image available for retry', async () => {
    const { manager, requests, reply } = fixture();
    const load = manager.loadNextReviewImage();
    reply(0, { success: true, prompt_id: 42, tags: ['smile'], sha256: SHA, image_tags: [] });
    await load;
    const apply = manager.applyReviewTags();
    reply(1, { success: false, error: 'Try again' });
    await apply;
    assert.equal(manager.autoTagState.reviewIndex, 0);
    assert.equal(requests.length, 2);
});
test('late generation responses cannot restore tags from a skipped or closed image', async () => {
    const { manager, reply } = fixture();
    const first = manager.loadNextReviewImage();
    manager.autoTagState.reviewIndex = 1;
    const second = manager.loadNextReviewImage();
    reply(1, { success: true, prompt_id: 2, tags: ['current'] });
    await second;
    reply(0, { success: true, prompt_id: 1, tags: ['stale'] });
    await first;
    assert.deepEqual(Array.from(manager.autoTagState.currentTags), ['current']);
    const third = manager.loadNextReviewImage();
    manager.cancelReview();
    reply(2, { success: true, prompt_id: 2, tags: ['closed'] });
    await third;
    assert.equal(manager.autoTagState.currentTags.length, 0);
});
