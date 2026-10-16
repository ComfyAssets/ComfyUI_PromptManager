const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function fixture() {
    const context = vm.createContext({ document: { addEventListener() {}, getElementById() {} },
        window: {}, console: { error() {} }, escapeHtml: value => value });
    const Manager = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/gallery.js'), 'utf8') + '\nGalleryManager;', context);
    const manager = Object.create(Manager.prototype);
    const requests = new Map();
    manager.extractImageMetadata = url => {
        const pending = deferred();
        requests.set(url, pending);
        return pending.promise;
    };
    manager.displayMetadata = (metadata, panel) => {
        panel.innerHTML = metadata?.positivePrompt || 'No metadata';
        manager.currentMetadata = metadata;
    };
    manager.stopViewerSlideshow = () => {};
    return { manager, requests, panel: { innerHTML: '', isConnected: true } };
}

test('a late response cannot overwrite the selected image or copy state', async () => {
    const { manager, requests, panel } = fixture();
    const first = manager.loadImageMetadata('old.png', panel);
    const second = manager.loadImageMetadata('new.png', panel);
    requests.get('new.png').resolve({ positivePrompt: 'new prompt' });
    await second;
    requests.get('old.png').resolve({ positivePrompt: 'old prompt' });
    await first;
    assert.equal(panel.innerHTML, 'new prompt');
    assert.equal(manager.currentMetadata.positivePrompt, 'new prompt');
});

test('navigation clears old action data while the new request is pending', async () => {
    const { manager, requests, panel } = fixture();
    manager.currentMetadata = { positivePrompt: 'old prompt' };
    const pending = manager.loadImageMetadata('new.png', panel);
    assert.equal(manager.currentMetadata, null);
    requests.get('new.png').resolve(null);
    await pending;
    assert.equal(manager.currentMetadata, null);
});

test('a stale error cannot replace newer metadata', async () => {
    const { manager, requests, panel } = fixture();
    const first = manager.loadImageMetadata('old.png', panel);
    const second = manager.loadImageMetadata('new.png', panel);
    requests.get('new.png').resolve({ positivePrompt: 'new prompt' });
    await second;
    requests.get('old.png').reject(new Error('old request failed'));
    await first;
    assert.equal(panel.innerHTML, 'new prompt');
});

for (const close of ['destroy viewer', 'remove modal']) {
    test(`closing via ${close} prevents pending metadata from restoring action data`, async () => {
        const { manager, requests, panel } = fixture();
        const pending = manager.loadImageMetadata('old.png', panel);
        if (close === 'destroy viewer') manager.destroyViewer();
        else panel.isConnected = false;
        requests.get('old.png').resolve({ positivePrompt: 'closed prompt' });
        await pending;
        assert.equal(manager.currentMetadata, null);
        assert.notEqual(panel.innerHTML, 'closed prompt');
    });
}
