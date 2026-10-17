const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ImageHelpers = require('../../web/js/image-helpers.js');
const {extractGenerationParams} = require('../../web/js/comfy-metadata.js');

function graph(positive = 0, negative = 1) {
    return {
        1:{class_type:'PromptManagerPair',inputs:{text:'cat',negative_text:'blur',prepend_text:'trigger',append_text:'detail'}},
        2:{class_type:'KSampler',inputs:{positive:['1',positive],negative:['1',negative],model:['3',0]}},
        3:{class_type:'CheckpointLoaderSimple',inputs:{ckpt_name:'m.safetensors'}},
    };
}

test('paired conditioning uses actual output roles and composes only positive', () => {
    const metadata = extractGenerationParams({prompt:graph()});
    assert.equal(metadata.positivePrompt,'trigger cat detail');
    assert.equal(metadata.negativePrompt,'blur');
    const swapped = extractGenerationParams({prompt:graph(1,0)});
    assert.equal(swapped.positivePrompt,'blur');
    assert.equal(swapped.negativePrompt,'trigger cat detail');
});

test('paired strings passed through encoders retain the connected output slot', () => {
    const prompt=graph();
    prompt[4]={class_type:'CLIPTextEncode',inputs:{text:['1',2]}};
    prompt[5]={class_type:'CLIPTextEncode',inputs:{text:['1',3]}};
    prompt[2].inputs.positive=['4',0];prompt[2].inputs.negative=['5',0];
    const metadata=extractGenerationParams({prompt});
    assert.equal(metadata.positivePrompt,'trigger cat detail');
    assert.equal(metadata.negativePrompt,'blur');
});

test('recorded image pair overrides unrelated PNG fallback, including an empty negative', async () => {
    for (const negative of ['blur','']) {
        const context=vm.createContext({document:{addEventListener(){}},window:{},console,ImageHelpers,
            ApiClient:{fetchUrl:async()=>({ok:true,json:async()=>({success:true,prompt:{text:'raw cat',pair:{negative_text:negative},pair_context:{positive_text:'trigger cat',negative_text:negative}}})})},
        });
        const Manager=vm.runInContext(fs.readFileSync(path.join(__dirname,'../../web/js/gallery.js'),'utf8')+'\nGalleryManager;',context);
        const manager=Object.create(Manager.prototype);
        manager.extractImageMetadata=async()=>({positivePrompt:'wrong',negativePrompt:'wrong'});
        manager.displayMetadata=data=>{manager.last=data;};
        await manager.loadImageMetadata('/prompt_manager/images/serve/example.png',{});
        assert.equal(manager.last.positivePrompt,'trigger cat');
        assert.equal(manager.last.negativePrompt,negative);
    }
});

test('gallery labels graph-inferred text separately from recorded execution text', () => {
    const context=vm.createContext({document:{addEventListener(){}},window:{},console,escapeHtml:value=>String(value)});
    const Manager=vm.runInContext(fs.readFileSync(path.join(__dirname,'../../web/js/gallery.js'),'utf8')+'\nGalleryManager;',context);
    const manager=Object.create(Manager.prototype);
    manager.formatPromptText=value=>String(value||'');manager.setupMetadataActions=()=>{};
    for (const [source,label] of [['graph-inferred','Recovered from workflow metadata'],['execution','Recorded during execution'],['embedded-parameters','Read from embedded parameters'],['mixed','Execution and workflow metadata']]) {
        const panel={};
        manager.displayMetadata({positivePrompt:'cat',negativePrompt:'blur',textSource:source},panel);
        assert.ok(panel.innerHTML.includes(label),source);
    }
});
