import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';

const module = await import('../../web/js/workspace/media-viewer.mjs').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    return {};
});
test('disposed media viewer touches no DOM observers or requests', () => {
    assert.equal(typeof module.createMediaViewer,'function','media viewer contract is exported');
    const forbidden = new Proxy({}, {get() {assert.fail('Disposed viewer touched DOM or inspection');}});
    for (const aborted of [false,true]) {
        const parentScope = createScope(); if (aborted) parentScope.dispose();
        const view = module.createMediaViewer({container:forbidden,parentScope,inspectionController:forbidden,sender:forbidden,
            onSelect:() => assert.fail('Disposed viewer selected an entry'),onRefresh:() => assert.fail('Disposed viewer refreshed')});
        if (!aborted) view.dispose();
        view.open(forbidden); view.dispose(); view.dispose(); parentScope.dispose();
    }
});
