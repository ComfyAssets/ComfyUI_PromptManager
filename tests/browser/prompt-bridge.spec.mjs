import { test, expect } from './fixtures.mjs';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';

const moduleUrl = '/prompt_manager/js/shared/toolkit/prompt-bridge.js';

test.beforeEach(async ({ page, browserName }) => {
    await startCoverage(page, browserName);
    page.bridgeErrors = [];
    page.on('pageerror', error => page.bridgeErrors.push(error.message));
    await page.goto('/prompt_manager/workspace#/overview');
});

test.afterEach(async ({ page, browserName }) => {
    await page.evaluate(() => { window.client?.dispose(); window.host?.dispose(); });
    await saveCoverage(page, browserName);
    expect(page.bridgeErrors).toEqual([]);
});

async function host(page, namespace = '/', suffix = '') {
    await page.evaluate(async ({ moduleUrl, namespace, suffix }) => {
        const { createBridgeHost } = await import(moduleUrl + suffix);
        window.applied = [];
        window.host = createBridgeHost({ origin: location.origin, namespace, label: 'Independent host', adapter: {
            listTargets: () => [{ id: 'node-1', label: 'Pair', roles: ['positive', 'negative'] }],
            preview: (id, roles) => ({ preview: { target: { id }, fields: roles }, snapshot: structuredClone(roles) }),
            apply: roles => { window.applied.push(roles); return { targetId: 'node-1', values: roles }; },
        } });
    }, { moduleUrl, namespace, suffix });
}

async function client(page, namespace = '/') {
    await page.evaluate(async ({ moduleUrl, namespace }) => {
        const { createBridgeClient } = await import(moduleUrl);
        window.client = createBridgeClient({ origin: location.origin, namespace, timeoutMs: 1000 });
    }, { moduleUrl, namespace });
}

test('separately opened tabs preview and acknowledge exact roles through the shipped transport', async ({ page, context }) => {
    const destination = await context.newPage();
    try {
        await destination.goto('/prompt_manager/workspace#/overview');
        await host(destination);
        await client(page);
        const result = await page.evaluate(async () => {
            const [host] = await window.client.discover({ waitMs: 400 });
            const preview = await window.client.preview(host.hostId, 'node-1', { positive: 'A mountain', negative: '' });
            window.previewTicket = preview.ticket;
            return { host, preview };
        });
        expect(result.host.label).toBe('Independent host');
        expect(result.preview.fields).toEqual({ positive: 'A mountain', negative: '' });
        expect(await destination.evaluate(() => window.applied)).toEqual([]);
        const ack = await page.evaluate(hostId => window.client.apply(hostId, window.previewTicket), result.host.hostId);
        expect(ack).toEqual({ targetId: 'node-1', values: { positive: 'A mountain', negative: '' } });
        expect(await destination.evaluate(() => window.applied)).toEqual([ack.values]);
        expect(await page.evaluate(async hostId => {
            try { await window.client.apply(hostId, window.previewTicket); return false; } catch { return true; }
        }, result.host.hostId)).toBe(true);
    } finally {
        await destination.evaluate(() => window.host?.dispose()).catch(() => {});
        await destination.close();
    }
});

test('independent bundled hosts coexist and deployment namespaces isolate discovery', async ({ page, context }) => {
    const second = await context.newPage();
    try {
        await second.goto('/prompt_manager/workspace#/overview');
        await host(page, '/', '?bundle=first');
        await host(second, '/', '?bundle=second');
        await client(page);
        const hosts = await page.evaluate(() => window.client.discover({ waitMs: 400 }));
        expect(new Set(hosts.map(host => host.hostId)).size).toBe(2);
        await page.evaluate(() => window.client.dispose());
        await client(page, '/other-deployment/');
        expect(await page.evaluate(() => window.client.discover({ waitMs: 400 }))).toEqual([]);
    } finally {
        await second.evaluate(() => window.host?.dispose()).catch(() => {});
        await second.close();
    }
});

test('disconnected hosts, cancellation and disposal reject pending requests cleanly', async ({ page }) => {
    await host(page);
    await client(page);
    const errors = await page.evaluate(async () => {
        const [host] = await window.client.discover({ waitMs: 400 });
        window.host.dispose();
        const errors = [];
        try { await window.client.preview(host.hostId, 'node-1', { positive: 'No destination' }); } catch (error) { errors.push(error.message); }
        const controller = new AbortController();
        const aborted = window.client.preview(host.hostId, 'node-1', { negative: 'Cancelled' }, { signal: controller.signal });
        controller.abort();
        try { await aborted; } catch (error) { errors.push(error.message); }
        const pending = window.client.preview(host.hostId, 'node-1', { positive: 'Disposed' });
        window.client.dispose();
        try { await pending; } catch (error) { errors.push(error.message); }
        return errors;
    });
    expect(errors).toHaveLength(3);
    expect(errors[0]).toMatch(/timed out/);
    expect(errors[1]).toMatch(/abort/);
    expect(errors[2]).toMatch(/disposed/);
});
