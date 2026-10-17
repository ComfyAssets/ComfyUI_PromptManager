import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import {mountImages} from '../../web/js/workspace/images.mjs';

test('Images mounted after its parent is aborted creates no DOM, requests or observers',()=>{
    const parent=createScope();parent.dispose();
    const originals=new Map(['document','window','ResizeObserver','IntersectionObserver'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
    const touches=[];
    const forbidden=name=>new Proxy({}, {get(target,key){touches.push(`${name}.${String(key)}`);throw Error(`Disposed mount accessed ${name}.${String(key)}`);}});
    Object.defineProperty(globalThis,'document',{configurable:true,value:forbidden('document')});
    Object.defineProperty(globalThis,'window',{configurable:true,value:forbidden('window')});
    for(const key of ['ResizeObserver','IntersectionObserver']) Object.defineProperty(globalThis,key,{configurable:true,value:class {constructor(){touches.push(key);throw Error('Observer created after parent disposal');}}});
    const api={get(){touches.push('request');assert.fail('Request started after parent disposal');}};
    try {
        let mounted;
        assert.doesNotThrow(()=>{mounted=mountImages(forbidden('container'),api,parent,{embedded:true,fetchPage(){touches.push('page');assert.fail('Page requested after parent disposal');}});});
        assert.equal(typeof mounted.dispose,'function');mounted.dispose();mounted.dispose();
        assert.deepEqual(touches,[]);
    } finally {
        for(const [key,descriptor] of originals) {if(descriptor) Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
    }
});
