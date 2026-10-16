const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ImageHelpers = require('../../web/js/image-helpers.js');

test('gallery metadata requests retain root identity without including cache parameters in filenames', async () => {
    const requests = [];
    const context = vm.createContext({ document: { addEventListener() {} }, window: {}, console,
        ImageHelpers, ApiClient: { fetchUrl: async url => {
            requests.push(url);
            return { ok: true, json: async () => ({ success: true, prompt: { text: url } }) };
        } },
    });
    const Manager = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/gallery.js'), 'utf8') + '\nGalleryManager;', context, { filename: path.join(__dirname, '../../web/js/gallery.js') });
    const manager = Object.create(Manager.prototype);
    manager.extractImageMetadata = async () => null;
    manager.displayMetadata = (metadata, panel) => { panel.prompt = metadata.positivePrompt; };
    for (const root of [0, 1]) {
        const panel = {};
        const expected = `/prompt_manager/images/prompt/sub%2Fsame.png?root=${root}`;
        await manager.loadImageMetadata(`/prompt_manager/images/serve/sub/same.png?v=123&root=${root}`, panel);
        assert.equal(panel.prompt, expected);
    }
    assert.deepEqual(requests, [
        '/prompt_manager/images/prompt/sub%2Fsame.png?root=0',
        '/prompt_manager/images/prompt/sub%2Fsame.png?root=1',
    ]);
});
