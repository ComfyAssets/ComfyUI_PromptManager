import test from 'node:test';
import assert from 'node:assert/strict';
import {createScope} from '../../web/js/shared/toolkit/core.js';
import * as module from '../../web/js/workspace/prompt-inspector.mjs';

function dom() {
    class Element {
        constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this.classList={add:()=>{}};this.className='';}
        append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
        replaceChildren(...children){this.children=[];this.append(...children);}
        setAttribute(key,value){this.attributes[key]=value;}
        addEventListener(type,listener,{signal}={}){this.listeners.set(type,listener);signal?.addEventListener('abort',()=>this.listeners.delete(type),{once:true});}
        closest(selector){if(selector==='section'&&this.tagName==='section')return this;if(selector==='[data-send-role]'&&Object.hasOwn(this.dataset,'sendRole'))return this;if(selector==='[data-copy-role]'&&Object.hasOwn(this.dataset,'copyRole'))return this;if(selector==='[data-retry-prompts]'&&Object.hasOwn(this.dataset,'retryPrompts'))return this;return this.parent?.closest(selector);}
        click(target){this.listeners.get('click')?.({target});}
    }
    const previous=global.document,toast=new Element('div');global.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),getElementById:id=>id==='workspace-toast'?toast:null};
    const walk=(root,predicate)=>[...(predicate(root)?[root]:[]),...root.children.flatMap(child=>walk(child,predicate))];
    return {Element,walk,toast,restore:()=>{global.document=previous;}};
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
        assert.equal(f.walk(container,node=>node.textContent==='Saved library prompt').length,0,'no per-block source line');
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

test('frozen fields keep null, empty and whitespace actions distinct without a source line',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    const field=(text,source)=>({status:text===null?'unresolved':'resolved',text,source:'execution',evidence:{provenance:'recorded',source,reason:null}});
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:field('positive','runtime_return'),negative:field(null,'unknown')}});
        assert.equal(f.walk(container,node=>node.dataset.sendRole==='negative'||node.dataset.sendRole==='pair'||node.dataset.copyRole==='negative').length,0);
        assert.equal(f.walk(container,node=>node.textContent?.includes('runtime_return')).length,0,'no per-block source line');
        view.update({positive:field('positive','runtime_return'),negative:field('','host_cache_output')});
        assert.equal(f.walk(container,node=>node.textContent?.includes('host_cache_output')).length,0);
        const pair=f.walk(container,node=>node.dataset.sendRole==='pair')[0];container.click(pair);
        assert.deepEqual(sent,[{positive:'positive',negative:''}]);
        view.update({positive:field('positive','runtime_return'),negative:field('  \n\t ','host_cache_output')});
        assert.equal(f.walk(container,node=>node.tagName==='p'&&node.className==='prompt-text'&&node.textContent==='  \n\t ').length,1);
        container.click(f.walk(container,node=>node.dataset.sendRole==='negative')[0]);
        assert.deepEqual(sent[1],{negative:'  \n\t '});
        view.setState({prompts:{positive:field('positive','runtime_return'),negative:field('old','host_cache_output')},actionsEnabled:false});
        container.click(f.walk(container,node=>node.dataset.sendRole==='pair')[0]);assert.equal(sent.length,2);
    }finally{scope.dispose();f.restore();}
});

test('a zeroed conditioning negative shows as an explicit empty negative without a source line',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:{status:'resolved',text:'positive',source:'graph-inferred'},negative:{status:'resolved',text:'',source:'zeroed-conditioning'}}});
        assert.equal(f.walk(container,node=>node.textContent==='Zeroed conditioning (no negative text)').length,0);
        assert.equal(f.walk(container,node=>node.className==='prompt-text'&&node.textContent==='No negative prompt (explicitly empty)').length,1);
        container.click(f.walk(container,node=>node.dataset.sendRole==='pair')[0]);
        assert.deepEqual(sent,[{positive:'positive',negative:''}]);
    }finally{scope.dispose();f.restore();}
});

const unresolved=message=>({status:'unresolved',text:null,source:null,...(message===undefined?{}:{message})});
const statusLines=(f,container)=>f.walk(container,node=>node.tagName==='p'&&node.className==='prompt-text muted').map(node=>node.textContent);
test('unsupported node status names the class',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    const line='Prompt uses a node the inspector does not support (DualCFGGuider).';
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:unresolved(line),negative:unresolved(line)}});
        assert.deepEqual(statusLines(f,container),[line,line]);
        assert.equal(f.walk(container,node=>node.dataset.sendRole!==undefined||node.dataset.copyRole!==undefined).length,0,'no action implies a prompt exists');
    }finally{scope.dispose();f.restore();}
});
test('runtime and composite statuses',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    const runtime='Prompt is chosen at run time (PromptSearchList).',composite='Prompt combines several texts (ConditioningCombine).';
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:unresolved(runtime),negative:unresolved(runtime)}});
        assert.deepEqual(statusLines(f,container),[runtime,runtime]);
        view.update({positive:unresolved(composite),negative:{status:'resolved',text:'kept',source:'execution'}});
        assert.deepEqual(statusLines(f,container),[composite]);
        assert.equal(f.walk(container,node=>node.tagName==='p'&&node.className==='prompt-text'&&node.textContent==='kept').length,1);
    }finally{scope.dispose();f.restore();}
});
test('limit status',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    const line='Prompt exceeds inspection limits.';
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:unresolved(line),negative:unresolved(line)}});
        assert.deepEqual(statusLines(f,container),[line,line]);
    }finally{scope.dispose();f.restore();}
});
test('roles without a usable line keep the status copy, and a candidate is never shown as a prompt',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:unresolved(''),negative:{status:'ambiguous',text:'candidate text',source:null}}});
        assert.deepEqual(statusLines(f,container),['Prompt could not be resolved.','Multiple possible prompts. Source could not be determined.']);
        assert.equal(f.walk(container,node=>node.textContent==='candidate text').length,0);
    }finally{scope.dispose();f.restore();}
});
test('class names in status lines render as text, never markup',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    const line='Prompt uses a node the inspector does not support (<img src=x onerror=alert(1)>).';
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:unresolved(line),negative:unresolved(line)}});
        assert.deepEqual(statusLines(f,container),[line,line]);
        assert.equal(f.walk(container,node=>node.tagName==='img').length,0);
        assert.equal(f.walk(container,node=>Object.hasOwn(node,'innerHTML')).length,0);
    }finally{scope.dispose();f.restore();}
});

test('a context label names nested prompt regions and actions apart from the frozen blocks while the visible heading stays',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},contextLabel:'Current file',prompts:{positive:record('p'),negative:record('n')}});
        const labels=f.walk(container,node=>node.attributes['aria-label']).map(node=>node.attributes['aria-label']);
        for(const name of ['Current file positive prompt','Current file negative prompt','Copy current file positive prompt','Send current file positive prompt to ComfyUI','Copy current file negative prompt','Send current file negative prompt to ComfyUI']) assert.ok(labels.includes(name),name);
        assert.equal(labels.filter(name=>/^(Positive|Negative) prompt$/.test(name)).length,0);
        assert.deepEqual(f.walk(container,node=>node.className==='role-label').map(node=>node.textContent),['+ Positive prompt','\u2212 Negative prompt']);
        const plain=new f.Element('div');module.mountPromptBlocks(plain,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:record('p')}});
        assert.ok(f.walk(plain,node=>node.attributes['aria-label']==='Positive prompt').length);
    }finally{scope.dispose();f.restore();}
});

test('a title names each action after its prompt (concept "Copy positive prompt: <title>") while regions keep their names',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},title:'Alpine observatory',roles:['positive'],prompts:{positive:record('p')}});
        const labels=f.walk(container,node=>node.attributes['aria-label']).map(node=>node.attributes['aria-label']);
        assert.deepEqual(labels,['Positive prompt','Copy positive prompt: Alpine observatory','Send positive prompt to ComfyUI: Alpine observatory']);
    }finally{scope.dispose();f.restore();}
});
const shape=node=>({tag:node.tagName,className:node.className,...(node.attributes['aria-label']?{label:node.attributes['aria-label']}:{}),
    ...(node.children.length?{children:node.children.filter(child=>child.tagName!=='svg').map(shape)}:{text:node.textContent})});
test('prompt block emits concept markup without a source line',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div');
    try{
        module.mountPromptBlocks(container,{parentScope:scope,includePairAction:false,sender:{open:()=>{}},prompts:{positive:{...record('a lighthouse'),evidence:{provenance:'recorded',source:'runtime_return'}},negative:record('blurry')}});
        const section=(role,label,text)=>({tag:'section',className:`prompt-section ${role}`,label:`${role[0].toUpperCase()}${role.slice(1)} prompt`,children:[
            {tag:'div',className:'prompt-heading',children:[{tag:'span',className:'role-label',text:label},{tag:'div',className:'prompt-actions',children:[
                {tag:'button',className:'icon-btn',label:`Copy ${role} prompt`,children:[]},{tag:'button',className:'send icon-btn',label:`Send ${role} prompt to ComfyUI`,children:[]}]}]},
            {tag:'p',className:'prompt-text',text}]});
        assert.deepEqual(container.children.map(shape),[section('positive','+ Positive prompt','a lighthouse'),section('negative','\u2212 Negative prompt','blurry')]);
        for(const button of f.walk(container,node=>node.tagName==='button')) assert.equal(button.children.filter(child=>child.tagName==='svg').length,1,'one icon per action');
    }finally{scope.dispose();f.restore();}
});
test('empty negative uses the concept copy and still sends an explicit clear',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:roles=>sent.push(roles)},prompts:{positive:record('positive'),negative:record('')}});
        const negative=container.children[1];
        assert.equal(negative.children[1].className,'prompt-text');assert.equal(negative.children[1].textContent,'No negative prompt (explicitly empty)');
        container.click(f.walk(negative,node=>node.dataset.sendRole==='negative')[0]);
        container.click(f.walk(container,node=>node.dataset.sendRole==='pair')[0]);
        assert.deepEqual(sent,[{negative:''},{positive:'positive',negative:''}]);
    }finally{scope.dispose();f.restore();}
});
test('unknown roles never offer copy or send',()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),sent=[];
    try{
        module.mountPromptBlocks(container,{parentScope:scope,roles:['positive','negative','style'],sender:{open:roles=>sent.push(roles)},
            prompts:{positive:{status:'absent'},negative:{status:'ambiguous',text:'candidate'},style:record('not a role')}});
        assert.deepEqual(container.children.map(node=>node.className),['prompt-section positive','prompt-section negative'],'an unknown role name renders nothing');
        assert.deepEqual(statusLines(f,container),['Not recorded in this file.','Multiple possible prompts. Source could not be determined.']);
        assert.equal(f.walk(container,node=>node.dataset.sendRole!==undefined||node.dataset.copyRole!==undefined||node.className==='prompt-actions').length,0);
        const forged=new f.Element('button');forged.dataset.sendRole='style';container.append(forged);container.click(forged);
        assert.equal(sent.length,0,'a forged action for an unknown role sends nothing');
    }finally{scope.dispose();f.restore();}
});
test('copy shows the shared toast "Prompt copied." with the exact role text',async()=>{
    const f=dom(),scope=createScope(),container=new f.Element('div'),copied=[];
    const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator');
    Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>{copied.push(text);}}}});
    try{
        module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:record('  exact\ntext '),negative:record('')}});
        await container.listeners.get('click')({target:f.walk(container,node=>node.dataset.copyRole==='positive')[0]});
        assert.deepEqual(copied,['  exact\ntext ']);assert.equal(f.toast.textContent,'Prompt copied.');
        assert.equal(f.walk(container,node=>node.attributes.role==='status').length,0,'no per-block copy status line');
    }finally{scope.dispose();f.restore();if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;}
});

/** Runs one copy click with `clipboard` as `navigator.clipboard`, recording what the fallback seam receives. */
async function copyWithClipboard(clipboard,{detach=false}={}) {
    const f=dom(),scope=createScope(),container=new f.Element('div'),fallbacks=[];
    const previous=Object.getOwnPropertyDescriptor(globalThis,'navigator');
    Object.defineProperty(globalThis,'navigator',{configurable:true,value:clipboard===undefined?{}:{clipboard}});
    try{
        const view=module.mountPromptBlocks(container,{parentScope:scope,sender:{open:()=>{}},prompts:{positive:record('exact text'),negative:record('')},
            copyFallback:(text,options)=>fallbacks.push({text,...options})});
        const copy=f.walk(container,node=>node.dataset.copyRole==='positive')[0];
        let current=copy;
        if(detach) {
            // The blocks re-render while the clipboard is pending: the clicked button is detached, its successor is not.
            copy.isConnected=false;container.querySelector=selector=>selector==='[data-copy-role="positive"]'?current:null;
        }
        const pending=container.listeners.get('click')({target:copy});
        if(detach) {view.update({positive:record('exact text'),negative:record('')});current=f.walk(container,node=>node.dataset.copyRole==='positive')[0];}
        await pending;
        return {fallbacks,toast:f.toast.textContent,copy,current,scope};
    }finally{scope.dispose();f.restore();if(previous)Object.defineProperty(globalThis,'navigator',previous);else delete globalThis.navigator;}
}
test('copy without a clipboard opens the select-to-copy fallback and shows no toast',async()=>{
    const {fallbacks,toast,copy,scope}=await copyWithClipboard(undefined);
    assert.equal(fallbacks.length,1);assert.equal(fallbacks[0].text,'exact text');assert.equal(fallbacks[0].trigger,copy);assert.ok(fallbacks[0].scope);
    assert.notEqual(toast,'Prompt copied.');assert.equal(scope.signal.aborted,true);
});
test('a refused clipboard write opens the fallback instead of the toast',async()=>{
    const {fallbacks,toast}=await copyWithClipboard({writeText:async()=>{throw new Error('Denied');}});
    assert.equal(fallbacks.length,1);assert.equal(fallbacks[0].text,'exact text');assert.notEqual(toast,'Prompt copied.');
});
test('a fallback after a re-render returns focus to the same-role copy button, not the detached one',async()=>{
    const {fallbacks,copy,current}=await copyWithClipboard({writeText:async()=>{throw new Error('Denied');}},{detach:true});
    assert.equal(fallbacks.length,1);assert.notEqual(current,copy);assert.equal(fallbacks[0].trigger,current);
});
