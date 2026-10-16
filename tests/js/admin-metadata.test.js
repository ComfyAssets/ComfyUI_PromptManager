const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function fixture() {
    const panel = { innerHTML: '', isConnected: true };
    const requests = new Map();
    const context = vm.createContext({ document: { readyState: 'loading', addEventListener() {},
        getElementById: id => id === 'metadata-content' ? panel : null },
        window: {}, console: { error() {} },
    });
    const Admin = vm.runInContext(fs.readFileSync(path.join(__dirname, '../../web/js/admin.js'), 'utf8') + '\nPromptAdmin;', context, { filename: path.join(__dirname, '../../web/js/admin.js') });
    const manager = Object.create(Admin.prototype);
    manager.extractImageMetadata = url => new Promise((resolve, reject) => requests.set(url, { resolve, reject }));
    manager.updateMetadataSidebar = metadata => { panel.innerHTML = metadata?.imagePath || 'No metadata'; };
    manager.hideModal = () => {};
    const load = url => manager.loadMetadataForImage({ getAttribute: () => url });
    return { manager, panel, requests, load };
}
test('admin metadata and copy actions stay on the selected image', async () => {
    const { manager, panel, requests, load } = fixture();
    const old = load('old.png');
    const current = load('current.png');
    requests.get('current.png').resolve({ imagePath: 'current.png' }); await current;
    requests.get('old.png').resolve({ imagePath: 'old.png' }); await old;
    assert.equal(panel.innerHTML, 'current.png');
    assert.equal(manager.currentMetadata.imagePath, 'current.png');
});
test('navigation clears old action data and late errors cannot replace current metadata', async () => {
    const { manager, panel, requests, load } = fixture();
    manager.currentMetadata = { imagePath: 'previous.png' };
    const old = load('old.png');
    assert.equal(manager.currentMetadata, null);
    const current = load('current.png');
    requests.get('current.png').resolve({ imagePath: 'current.png' }); await current;
    requests.get('old.png').reject(new Error('old error')); await old;
    assert.equal(panel.innerHTML, 'current.png');
});
for (const close of ['close gallery', 'detach sidebar']) {
    test(`${close} prevents pending admin metadata from restoring copy state`, async () => {
        const { manager, panel, requests, load } = fixture();
        const pending = load('old.png');
        if (close === 'close gallery') manager.closeGallery();
        else panel.isConnected = false;
        requests.get('old.png').resolve({ imagePath: 'old.png' }); await pending;
        assert.equal(manager.currentMetadata, null);
        assert.notEqual(panel.innerHTML, 'old.png');
    });
}
