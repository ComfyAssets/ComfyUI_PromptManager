const test = require('node:test');
const assert = require('node:assert/strict');
const source = import('../../web/js/workspace/images-export.mjs');
const inspector = import('../../web/js/workspace/file-inspector.mjs');

function record(path, status = 'available') {
    return {identity:{kind:'rooted-file', key:`0:${path}`, root_index:0, path, root_scope:null}, file:{format:'PNG'},
        prompts:{positive:{status:'resolved', text:path}}, generation:{}, library:{status:'unlinked'},
        download:{filename:path.split('/').at(-1), url:`/prompt_manager/images/serve/${path}?root=0`},
        metadata:{status, omitted_reason:null, raw:{prompt:'{}'}, record_formats:{prompt:'original-text'}}};
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; };

test('the single-image download and the bundle share one envelope builder', async () => {
    const {metadataEnvelope, inspectionDownloads} = await inspector;
    const value = record('a/one.png');
    assert.deepEqual(JSON.parse(inspectionDownloads(value).metadata.bodyText), metadataEnvelope(value));
    assert.equal(metadataEnvelope(record('b.png', 'limited')), null);
    assert.equal(metadataEnvelope({}), null);
});

test('the bundle holds the format, version, export time and envelopes in the given order', async () => {
    const {metadataBundle, BUNDLE_FILENAME} = await source;
    const envelopes = [{inspection_version:1, n:1}, {inspection_version:1, n:2}];
    const bundle = metadataBundle(envelopes, '2026-10-20T00:00:00.000Z');
    assert.deepEqual(bundle, {format:'prompt-manager-images-metadata', version:1, exported_at:'2026-10-20T00:00:00.000Z', images:envelopes});
    assert.notEqual(bundle.images, envelopes);
    assert.equal(BUNDLE_FILENAME, 'prompt-manager-images-metadata.json');
});

test('collection keeps listing order, runs at most four reads at once and counts failures', async () => {
    const {collectImagesMetadata} = await source;
    const {metadataEnvelope} = await inspector;
    const images = Array.from({length:9}, (_, index) => ({path:`img-${index}.png`, root_index:0}));
    const waits = new Map(images.map(image => [image.path, deferred()]));
    let running = 0, peak = 0;
    const done = collectImagesMetadata(images, async image => {
        running += 1; peak = Math.max(peak, running);
        try { return await waits.get(image.path).promise; } finally { running -= 1; }
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(running, 4);
    // Settle out of order: later images finish first, one read fails and one file's metadata is limited.
    for (const image of [...images].reverse()) {
        const wait = waits.get(image.path);
        if (image.path === 'img-3.png') wait.reject(new Error('unreadable'));
        else wait.resolve(record(image.path, image.path === 'img-5.png' ? 'limited' : 'available'));
        await new Promise(resolve => setImmediate(resolve));
    }
    const {envelopes, failed} = await done;
    assert.equal(peak, 4);
    assert.equal(failed, 2);
    assert.deepEqual(envelopes.map(envelope => envelope.identity.path),
        ['img-0.png', 'img-1.png', 'img-2.png', 'img-4.png', 'img-6.png', 'img-7.png', 'img-8.png']);
    assert.deepEqual(envelopes[0], metadataEnvelope(record('img-0.png')));
});

test('a read that throws synchronously counts as a failure', async () => {
    const {collectImagesMetadata} = await source;
    const result = await collectImagesMetadata([{path:'a.png'}, {path:'b.png'}], image => {
        if (image.path === 'a.png') throw new TypeError('Invalid inspection identity');
        return record(image.path);
    });
    assert.equal(result.failed, 1);
    assert.deepEqual(result.envelopes.map(envelope => envelope.identity.path), ['b.png']);
    assert.deepEqual(await collectImagesMetadata([], () => assert.fail('no reads')), {envelopes:[], failed:0});
});

test('the outcome toasts name the exported and unread counts', async () => {
    const {exportOutcome} = await source;
    assert.equal(exportOutcome(1, 1), 'Exported metadata for 1 image.');
    assert.equal(exportOutcome(2, 2), 'Exported metadata for 2 images.');
    assert.equal(exportOutcome(1, 3), 'Exported metadata for 1 of 3 images. 2 could not be read.');
    assert.equal(exportOutcome(2, 3), 'Exported metadata for 2 of 3 images. 1 could not be read.');
    assert.equal(exportOutcome(0, 2), 'Metadata could not be read. Try again.');
});
