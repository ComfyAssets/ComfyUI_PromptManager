import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installPromptBridge } from '../../web/js/prompt-bridge-host.js';

function fixture() {
    const runtime = new EventTarget();
    runtime.location = { origin: 'https://comfy.example', href: 'https://comfy.example/team/' };
    runtime.document = { title: 'My workflow' };
    const warnings = []; runtime.console = { warn: (...args) => warnings.push(args) };
    const hosts = [], adapter = {};
    const options = { runtime, baseUrl: '/team/prompt_manager/', adapterFactory: () => adapter,
        hostFactory: config => { const host = { config, disposed: 0, dispose() { this.disposed++; } }; hosts.push(host); return host; } };
    return { runtime, options, hosts, adapter, warnings };
}

test('host uses the exact deployment prefix and explicit pack label', () => {
    const f = fixture(), app = {};
    const controller = installPromptBridge(app, f.options);
    assert.equal(f.hosts.length, 1);
    assert.equal(f.hosts[0].config.namespace, '/team/');
    assert.equal(f.hosts[0].config.origin, 'https://comfy.example');
    assert.equal(f.hosts[0].config.label, 'PromptManager — My workflow');
    assert.equal(f.hosts[0].config.adapter, f.adapter);
    assert.equal(installPromptBridge(app, f.options), controller);
    controller.dispose(); controller.dispose();
    assert.equal(f.hosts[0].disposed, 1);
});

test('page hide disposes and cached restoration mounts exactly one fresh host', () => {
    const f = fixture(), controller = installPromptBridge({}, f.options);
    f.runtime.dispatchEvent(new Event('pagehide'));
    assert.equal(f.hosts[0].disposed, 1);
    f.runtime.dispatchEvent(new Event('pageshow'));
    f.runtime.dispatchEvent(new Event('pageshow'));
    assert.equal(f.hosts.length, 2);
    controller.dispose();
    f.runtime.dispatchEvent(new Event('pageshow'));
    assert.equal(f.hosts.length, 2);
    assert.equal(f.hosts[1].disposed, 1);
});

test('a disposed installation can be installed again', () => {
    const f = fixture(), app = {};
    installPromptBridge(app, f.options).dispose();
    const next = installPromptBridge(app, f.options);
    assert.equal(f.hosts.length, 2);
    next.dispose();
});

test('unavailable messaging leaves the existing host usable and can recover after restoration', () => {
    const f = fixture(); let unavailable = true;
    const factory = f.options.hostFactory;
    f.options.hostFactory = config => { if (unavailable) throw new Error('BroadcastChannel unavailable'); return factory(config); };
    const controller = installPromptBridge({}, f.options);
    assert.equal(f.warnings.length, 1);
    unavailable = false;
    f.runtime.dispatchEvent(new Event('pageshow'));
    assert.equal(f.hosts.length, 1);
    controller.dispose();
});

test('deployment namespace rejects external origins and unexpected paths', () => {
    for (const baseUrl of ['https://comfy.example.attacker.test/prompt_manager/', '/wrong/', 'data:text/plain,hello']) {
        const f = fixture();
        assert.throws(() => installPromptBridge({}, { ...f.options, baseUrl }), /deployment|origin|path/i);
        assert.equal(f.hosts.length, 0);
    }
});
