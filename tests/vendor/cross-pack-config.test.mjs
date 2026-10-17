import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { compatibilityPorts, validateKikoCheckout, launchPackHost } from '../../tools/vendor/pack-host.mjs';

test('cross-pack ports reserve three distinct unprivileged addresses', () => {
    assert.deepEqual(compatibilityPorts('8193'), { promptmanager: 8193, kiko: 8194, both: 8195 });
    assert.deepEqual(compatibilityPorts(65533), { promptmanager: 65533, kiko: 65534, both: 65535 });
    for (const value of [0, 1023, 65534, 'bad', 8193.5]) assert.throws(() => compatibilityPorts(value), /ports/);
});

test('cross-pack launcher refuses missing and unreviewed sibling sources before starting a host', async t => {
    await assert.rejects(validateKikoCheckout(), /PM_KIKO_ROOT/);
    await assert.rejects(launchPackHost({ mode: 'arbitrary' }), /Select/);
    const root = await mkdtemp(join(tmpdir(), 'pm-kiko-pin-'));
    t.after(() => rm(root, { recursive: true, force: true }));
    await writeFile(join(root, '__init__.py'), '');
    execFileSync('git', ['init', '--quiet', root]);
    execFileSync('git', ['-C', root, 'add', '__init__.py']);
    execFileSync('git', ['-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '--quiet', '-m', 'fixture']);
    await assert.rejects(validateKikoCheckout(root), /revision must be/);
});
