import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import gallerySettings from '../../web/js/gallery-settings.js';

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
        view.open(forbidden); view.stopSlideshow?.(); view.dispose(); view.dispose(); parentScope.dispose();
    }
});

test('preferences handle denied malformed storage without mutation', () => {
    assert.equal(typeof module.readViewerPreferences,'function');
    const normalize=gallerySettings.normalizeGallerySettings,defaults={videoAutoplay:false,videoMute:true,videoLoop:true};
    for(const getStorage of [() => {throw new Error('Denied');},() => ({getItem:() => {throw new Error('Denied');}}),
        ...['{broken','null','[]','42'].map(value=>() => ({getItem:key=>{assert.equal(key,'gallerySettings');return value;},setItem:()=>assert.fail('Read-only preferences')}))]) {
        assert.deepEqual(module.readViewerPreferences(getStorage,normalize),defaults);
    }
    let raw=JSON.stringify({videoAutoplay:true,videoMute:false,videoLoop:false,infiniteScroll:true});
    const storage={getItem:key=>{assert.equal(key,'gallerySettings');return raw;},setItem:()=>assert.fail('Read-only preferences')};
    assert.deepEqual(module.readViewerPreferences(()=>storage,normalize),{videoAutoplay:true,videoMute:false,videoLoop:false});
    raw=JSON.stringify({videoAutoplay:'true',videoMute:0,videoLoop:null});
    assert.deepEqual(module.readViewerPreferences(()=>storage,normalize),defaults);
});
