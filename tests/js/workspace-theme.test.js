import test from 'node:test';
import assert from 'node:assert/strict';
import { createScope } from '../../web/js/shared/toolkit/core.js';
import { mountWorkspaceTheme } from '../../web/js/workspace/theme.mjs';

function fixture({ dark = false, preference, fetchImpl, storageDenied = false, pathname = '/prompt_manager/workspace' } = {}) {
    const media = new EventTarget(); media.matches = dark;
    const win = new EventTarget(); win.location = { pathname }; win.matchMedia = () => media;
    const documentElement = { style: { colorScheme: 'light dark' } };
    const root = { dataset: {}, ownerDocument: { documentElement } };
    const saved = new Map(preference === undefined ? [] : [['pm_workspace_theme', preference]]);
    const storage = {
        getItem(key) { if (storageDenied) throw new Error('denied'); return saved.get(key); },
        setItem(key, value) { if (storageDenied) throw new Error('denied'); saved.set(key, value); },
    };
    const calls = [];
    const scope = createScope();
    const theme = mountWorkspaceTheme({ root, scope, win, storage, fetchImpl: fetchImpl || (async (url, init) => {
        calls.push({ url, init }); return { ok: true, json: async () => 'dark' };
    }) });
    return { root, documentElement, saved, media, win, scope, theme, calls };
}
const response = value => ({ ok: true, json: async () => value });
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('system uses host palette over OS and preserves reverse proxy prefix', async () => {
    const f = fixture({ pathname: '/proxy/comfy/prompt_manager/workspace' });
    await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, 'dark');
    assert.equal(f.documentElement.style.colorScheme, 'dark');
    assert.equal(f.calls[0].url, '/proxy/comfy/api/settings/Comfy.ColorPalette');
    f.scope.dispose();
});

test('supported light palettes accept string and wrapped values', async () => {
    for (const palette of ['light', 'solarized', 'github', 'arc']) {
        const f = fixture({ dark: true, fetchImpl: async () => response({ value: palette }) });
        await f.theme.ready;
        assert.equal(f.root.dataset.caTheme, 'light', palette);
        f.scope.dispose();
    }
});

test('explicit overrides persist and system restores host choice', async () => {
    const f = fixture({ preference: 'light' }); await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, 'light');
    f.theme.setPreference('dark'); assert.equal(f.root.dataset.caTheme, 'dark');
    assert.equal(f.saved.get('pm_workspace_theme'), 'dark');
    f.theme.setPreference('system'); assert.equal(f.root.dataset.caTheme, 'dark');
    assert.equal(f.saved.get('pm_workspace_theme'), 'system');
    assert.throws(() => f.theme.setPreference('follow'), /preference/i);
    assert.throws(() => f.theme.setPreference('orange'), /preference/i);
    f.scope.dispose();
});

test('unknown host palettes and invalid stored preferences follow changing OS', async () => {
    const f = fixture({ preference: 'orange', fetchImpl: async () => response('custom') });
    await f.theme.ready; assert.equal(f.root.dataset.caTheme, 'light');
    f.media.matches = true; f.media.dispatchEvent(new Event('change'));
    assert.equal(f.root.dataset.caTheme, 'dark'); f.scope.dispose();
});

test('network, HTTP, JSON and storage failures recover using system theme', async () => {
    for (const fetchImpl of [async () => { throw new Error('offline'); }, async () => ({ ok: false }), async () => ({ ok: true, json: async () => { throw new Error('invalid'); } })]) {
        const f = fixture({ dark: true, storageDenied: true, fetchImpl });
        await f.theme.ready; assert.equal(f.root.dataset.caTheme, 'dark');
        assert.doesNotThrow(() => f.theme.setPreference('light'));
        assert.equal(f.root.dataset.caTheme, 'light'); f.scope.dispose();
    }
});

test('focus refreshes host palette and stale responses cannot replace newest palette', async () => {
    const pending = [];
    const f = fixture({ fetchImpl: (url, init) => new Promise(resolve => pending.push({ resolve, signal: init.signal })) });
    f.win.dispatchEvent(new Event('focus'));
    assert.equal(pending.length, 2); assert.equal(pending[0].signal.aborted, true);
    pending[1].resolve(response('light')); await tick();
    pending[0].resolve(response('dark')); await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, 'light'); f.scope.dispose();
});

test('cleanup aborts requests, restores attributes and native scheme and removes listeners', async () => {
    let resolve, fSignal;
    const f = fixture({ fetchImpl: (url, init) => new Promise(done => { resolve = done; fSignal = init.signal; }) });
    f.scope.dispose(); assert.equal(fSignal.aborted, true);
    assert.equal(f.root.dataset.caTheme, undefined);
    assert.equal(f.documentElement.style.colorScheme, 'light dark');
    f.win.dispatchEvent(new Event('focus')); f.media.dispatchEvent(new Event('change'));
    resolve(response('dark')); await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, undefined);
    f.theme.setPreference('dark'); assert.equal(f.root.dataset.caTheme, undefined);
});

test('cleanup restores an existing toolkit theme', async () => {
    const f = fixture(); await f.theme.ready; f.scope.dispose();
    f.root.dataset.caTheme = 'light';
    const scope = createScope();
    const theme = mountWorkspaceTheme({ root: f.root, scope, win: f.win, storage: f.saved, fetchImpl: async () => response('dark') });
    await theme.ready; scope.dispose(); assert.equal(f.root.dataset.caTheme, 'light');
});


test('malformed palette payloads fall back without losing native theme alignment', async () => {
    for (const payload of [null, {}, {value: 42}, 42]) {
        const f = fixture({ dark: true, fetchImpl: async () => response(payload) });
        await f.theme.ready;
        assert.equal(f.root.dataset.caTheme, 'dark');
        assert.equal(f.documentElement.style.colorScheme, 'dark');
        f.scope.dispose();
    }
});

test('focus failure clears obsolete host palette and resumes system following', async () => {
    let calls = 0;
    const f = fixture({ fetchImpl: async () => {
        if (++calls === 1) return response('dark');
        throw new Error('host offline');
    } });
    await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, 'dark');
    f.win.dispatchEvent(new Event('focus')); await tick();
    assert.equal(f.root.dataset.caTheme, 'light');
    f.scope.dispose();
    f.win.dispatchEvent(new Event('focus')); await tick();
    assert.equal(calls, 2);
});

test('blocked localStorage getter and missing proxy marker stay usable', async () => {
    const f = fixture(); await f.theme.ready; f.scope.dispose();
    Object.defineProperty(f.win, 'localStorage', { get() { throw new Error('denied'); } });
    f.win.location.pathname = '/standalone';
    const scope = createScope();
    let url;
    const theme = mountWorkspaceTheme({ root: f.root, win: f.win, scope,
        fetchImpl: async value => { url = value; return response(' Obsidian '); } });
    assert.equal(theme.preference, 'system');
    await theme.ready;
    assert.equal(url, '/api/settings/Comfy.ColorPalette');
    assert.equal(f.root.dataset.caTheme, 'dark');
    theme.setPreference('light');
    assert.equal(theme.preference, 'light');
    scope.dispose();
});

test('stored follow migrates to system and system follows the host palette', async () => {
    const f = fixture({ preference: 'follow', fetchImpl: async () => response('light') });
    assert.equal(f.theme.preference, 'system');
    await f.theme.ready;
    assert.equal(f.root.dataset.caTheme, 'light');
    f.media.matches = true; f.media.dispatchEvent(new Event('change'));
    assert.equal(f.root.dataset.caTheme, 'light', 'a known host palette wins over the OS');
    f.scope.dispose();
    const unknown = fixture({ preference: 'follow', dark: true, fetchImpl: async () => response('custom') });
    await unknown.theme.ready;
    assert.equal(unknown.theme.preference, 'system');
    assert.equal(unknown.root.dataset.caTheme, 'dark', 'an unknown host palette falls back to the OS');
    unknown.scope.dispose();
});
