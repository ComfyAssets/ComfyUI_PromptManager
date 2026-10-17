import test from 'node:test';
import assert from 'node:assert/strict';
import { createScope, themeFromColor } from '../../web/js/shared/toolkit/core.js';

test('host background luminance determines mode without assuming palette names', () => {
    for (const color of ['#fff', '#F4F4F6', 'rgb(240, 240, 240)']) assert.equal(themeFromColor(color), 'light');
    for (const color of ['#222', '#202020', 'rgb(30, 32, 34)']) assert.equal(themeFromColor(color), 'dark');
    assert.equal(themeFromColor(''), null);
    assert.equal(themeFromColor('transparent'), null);
});

test('scope cancels requests and callbacks, removes listeners and disposes once', () => {
    const scope = createScope();
    const target = new EventTarget();
    let events = 0;
    let cleanups = 0;
    scope.listen(target, 'change', () => events++);
    scope.cleanup(() => cleanups++);
    target.dispatchEvent(new Event('change'));
    assert.equal(events, 1);
    scope.dispose();
    scope.dispose();
    target.dispatchEvent(new Event('change'));
    assert.equal(events, 1);
    assert.equal(cleanups, 1);
    assert.equal(scope.signal.aborted, true);
});

test('debounced work runs once with the latest value and stops after disposal', async () => {
    const scope = createScope();
    const values = [];
    const change = scope.debounce(value => values.push(value), 1);
    change('old');
    change('new');
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(values, ['new']);
    change('cancelled');
    scope.dispose();
    change('after removal');
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(values, ['new']);
});
