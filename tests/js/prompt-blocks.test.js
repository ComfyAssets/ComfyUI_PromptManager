import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import * as module from '../../web/js/workspace/prompt-inspector.mjs';

function dom() {
    class Element {
        constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this.classList={add:()=>{}};}
        append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
        replaceChildren(...children){this.children=[];this.append(...children);}
        setAttribute(key,value){this.attributes[key]=value;}
        addEventListener(type,listener,{signal}={}){this.listeners.set(type,listener);signal?.addEventListener('abort',()=>this.listeners.delete(type),{once:true});}
        closest(selector){if(selector==='section'&&this.tagName==='section')return this;if(selector==='[data-send-role]'&&Object.hasOwn(this.dataset,'sendRole'))return this;if(selector==='[data-copy-role]'&&Object.hasOwn(this.dataset,'copyRole'))return this;if(selector==='[data-retry-prompts]'&&Object.hasOwn(this.dataset,'retryPrompts'))return this;return this.parent?.closest(selector);}
        click(target){this.listeners.get('click')?.({target});}
    }
    const previous=global.document;global.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
    const walk=(root,predicate)=>[...(predicate(root)?[root]:[]),...root.children.flatMap(child=>walk(child,predicate))];
    return {Element,walk,restore:()=>{global.document=previous;}};
}
const record=text=>({status:'resolved',text,source:'library'});
test('shared prompt blocks send complete selected library values and explicit empty negatives',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:record('<literal>\nfull positive'),negative:{status:'unresolved',message:'Choose a saved negative.'}}});
        assert.ok(f.walk(container,node=>node.textContent==='<literal>\nfull positive').length);
        assert.equal(f.walk(container,node=>node.dataset.sendRole==='pair').length,0);
        view.update({positive:record('Complete positive'),negative:record('')});
        const pair=f.walk(container,node=>node.dataset.sendRole==='pair')[0];container.click(pair);
        assert.deepEqual(sent,[{positive:'Complete positive',negative:''}]);
        assert.equal(f.walk(container,node=>node.textContent==='Saved library prompt').length,2);
        view.dispose();container.click(pair);assert.equal(sent.length,1);
    }finally{scope.dispose();f.restore();}
});
test('positive-only cards never display or send a hidden negative or pair',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        module.mountPromptBlocks(container,{parentScope:scope,roles:['positive'],prompts:{positive:record('positive'),negative:record('hidden')},sender:{open:roles=>sent.push(roles)}});
        assert.equal(f.walk(container,node=>node.attributes['aria-label']==='Negative prompt').length,0);
        assert.equal(f.walk(container,node=>node.dataset.sendRole==='pair').length,0);
        const button=f.walk(container,node=>node.dataset.sendRole==='positive')[0];container.click(button);assert.deepEqual(sent,[{positive:'positive'}]);
        scope.dispose();container.click(button);assert.equal(sent.length,1);
    }finally{scope.dispose();f.restore();}
});

test('disposed blocks ignore updates and aborted parents never mount content',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:record('original')}});
        view.dispose();view.update({positive:record('late')});assert.equal(f.walk(container,node=>node.textContent==='late').length,0);
        scope.dispose();const other=new f.Element('div');module.mountPromptBlocks(other,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:record('late')}});assert.equal(other.children.length,0);
    }finally{scope.dispose();f.restore();}
});

test('frozen fields disclose their exact source and keep null, empty and whitespace actions distinct',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    const field=(text,source)=>({status:text===null?'unresolved':'resolved',text,source:'execution',evidence:{provenance:'recorded',source,reason:null}});
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:field('positive','runtime_return'),negative:field(null,'unknown')}});
        assert.equal(f.walk(container,node=>node.dataset.sendRole==='negative'||node.dataset.sendRole==='pair'||node.dataset.copyRole==='negative').length,0);
        assert.ok(f.walk(container,node=>node.textContent?.includes('runtime_return')).length);
        view.update({positive:field('positive','runtime_return'),negative:field('','host_cache_output')});
        assert.ok(f.walk(container,node=>node.textContent?.includes('host_cache_output')).length);
        const pair=f.walk(container,node=>node.dataset.sendRole==='pair')[0];container.click(pair);
        assert.deepEqual(sent,[{positive:'positive',negative:''}]);
        view.update({positive:field('positive','runtime_return'),negative:field('  \n\t ','host_cache_output')});
        assert.equal(f.walk(container,node=>node.tagName==='pre'&&node.textContent==='  \n\t ').length,1);
        container.click(f.walk(container,node=>node.dataset.sendRole==='negative')[0]);
        assert.deepEqual(sent[1],{negative:'  \n\t '});
        view.setState({prompts:{positive:field('positive','runtime_return'),negative:field('old','host_cache_output')},actionsEnabled:false});
        container.click(f.walk(container,node=>node.dataset.sendRole==='pair')[0]);assert.equal(sent.length,2);
    }finally{scope.dispose();f.restore();}
});

test('a zeroed conditioning negative shows as an explicit empty negative with its own source',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:{status:'resolved',text:'positive',source:'graph-inferred'},negative:{status:'resolved',text:'',source:'zeroed-conditioning'}}});
        assert.equal(f.walk(container,node=>node.textContent==='Zeroed conditioning (no negative text)').length,1);
        assert.equal(f.walk(container,node=>node.textContent==='Empty negative prompt').length,1);
        container.click(f.walk(container,node=>node.dataset.sendRole==='pair')[0]);
        assert.deepEqual(sent,[{positive:'positive',negative:''}]);
    }finally{scope.dispose();f.restore();}
});
