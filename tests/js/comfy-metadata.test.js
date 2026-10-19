// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { extractGenerationParams, extractGenerationFacts } = require("../../web/js/comfy-metadata.js");

const parsedFactGraph = value => ({ status: "parsed", value });
const absentFactGraph = () => ({ status: "absent", value: null });
const factFields = ["seed", "steps", "cfg", "sampler", "scheduler", "model"];
function factsFor(graph, extra = {}) {
    assert.equal(typeof extractGenerationFacts, "function", "the conservative facts contract is exported");
    return extractGenerationFacts({ prompt_graph: parsedFactGraph(graph), workflow_graph: absentFactGraph(), metadataStatus: "available", ...extra });
}
function factGraph(inputs = {}) {
    return {
        s: { class_type: "KSampler", inputs: { seed: "9007199254740993", steps: 28, cfg: "6.5000000000000000001", sampler_name: "dpmpp_2m", scheduler: "karras", model: ["model", 0], ...inputs } },
        model: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "model.safetensors" } },
    };
}

test("facts keep unsafe seed and decimal strings exact", () => {
    const facts = factsFor(factGraph());
    assert.deepEqual(Object.keys(facts), factFields);
    assert.deepEqual(factFields.map(field => facts[field].value), ["9007199254740993", "28", "6.5000000000000000001", "dpmpp_2m", "karras", "model.safetensors"]);
    for (const fact of Object.values(facts)) assert.deepEqual(fact, {
        status: "known", value: fact.value, source: "api-graph", evidence: "configuration", reason: null, sampler_id: "s",
    });
    const zero = factsFor(factGraph({ seed: 0, cfg: "0", steps: "1" }));
    assert.equal(zero.seed.value, "0"); assert.equal(zero.cfg.value, "0");
    for (const inputs of [{ seed: 9007199254740992 }, { seed: "1.5" }, { steps: "0" }, { cfg: "NaN" }, { cfg: {} }, { cfg: true }]) {
        const key = Object.keys(inputs)[0];
        assert.equal(factsFor(factGraph(inputs))[key].status, "unresolved");
    }
});

test("facts do not choose equal sampler branches or unrelated model", () => {
    const graph = factGraph(); graph.other = structuredClone(graph.s);
    const ambiguous = factsFor(graph);
    assert.ok(Object.values(ambiguous).every(fact => fact.status === "ambiguous" && fact.value === null));
    const selected = factsFor(graph, { resolution: { state: "resolved", sampler_id: "other" } });
    assert.equal(selected.seed.sampler_id, "other");
    const unrelated = factsFor(factGraph({ model: ["missing", 0] }));
    assert.equal(unrelated.model.status, "unresolved");
    assert.equal(unrelated.model.value, null);
    assert.equal(factsFor({ only: graph.model }).seed.status, "unresolved");
});

test("facts respect named scalar outputs and custom scheduler paths", () => {
    const graph = factGraph({ seed: ["primitive", 0], steps: ["steps", 0], cfg: ["cfg", 0], sampler_name: ["sampler", 0], scheduler: ["scheduler", 0], model: ["lora", 0] });
    graph.primitive = { class_type: "PrimitiveInt", inputs: { value: "0" } };
    graph.steps = { class_type: "PrimitiveInt", inputs: { value: "50" } };
    graph.cfg = { class_type: "PrimitiveFloat", inputs: { value: "0" } };
    graph.sampler = { class_type: "PrimitiveString", inputs: { value: "euler" } };
    graph.scheduler = { class_type: "PrimitiveStringMultiline", inputs: { value: "normal" } };
    graph.lora = { class_type: "LoraLoader", inputs: { lora_name: "style", model: ["model", 0] } };
    assert.deepEqual(factFields.map(field => factsFor(graph)[field].value), ["0", "50", "0", "euler", "normal", "model.safetensors"]);
    graph.settings = { class_type: "UnknownSettings", inputs: { first: 20, second: 30 } };
    graph.s.inputs.steps = ["settings", 0];
    assert.equal(factsFor(graph).steps.status, "unresolved");
    const custom = {
        s: { class_type: "SamplerCustomAdvanced", inputs: { guider: ["g", 0], noise: ["n", 0], sampler: ["k", 0], sigmas: ["sigmas", 0] } },
        g: { class_type: "CFGGuider", inputs: { model: ["m", 0], cfg: "4.0000000000000001" } },
        n: { class_type: "RandomNoise", inputs: { noise_seed: "9007199254740993" } },
        k: { class_type: "KSamplerSelect", inputs: { sampler_name: "euler" } },
        sigmas: { class_type: "BasicScheduler", inputs: { steps: 22, scheduler: "karras" } },
        m: { class_type: "UNETLoader", inputs: { unet_name: "flux.safetensors" } },
    };
    assert.deepEqual(factFields.map(field => factsFor(custom)[field].value), ["9007199254740993", "22", "4.0000000000000001", "euler", "karras", "flux.safetensors"]);
    custom.s = { class_type: "SamplerCustom", inputs: { model: ["m", 0], cfg: 0, noise_seed: 0, sampler: ["k", 0], sigmas: ["sigmas", 0] } };
    assert.deepEqual([factsFor(custom).seed.value, factsFor(custom).steps.value, factsFor(custom).cfg.value], ["0", "22", "0"]);
    custom.sigmas.inputs = { steps: 22 };
    assert.equal(factsFor(custom).scheduler.status, "not-recorded");
    assert.equal(factsFor(custom).sampler.value, "euler");
});

test("facts require supported scalar producer types and output slots", () => {
    const cases = [
        ["PrimitiveInt", "seed", "42", true], ["PrimitiveFloat", "cfg", "6.5000000000000000001", true],
        ["PrimitiveString", "sampler_name", "euler", true], ["PrimitiveStringMultiline", "scheduler", "normal", true],
        ["PrimitiveString", "seed", "42", false], ["PrimitiveStringMultiline", "steps", "42", false],
        ["PrimitiveInt", "sampler_name", "euler", false], ["PrimitiveFloat", "scheduler", "normal", false],
        ["PrimitiveBoolean", "seed", "42", false], ["PrimitiveNode", "seed", "42", false],
        ["UnknownSettings", "seed", "42", false], ["SamplerCombo", "steps", "42", false],
    ];
    for (const [class_type, input, value, supported] of cases) {
        for (const slot of [0, 1, 999, -1, 0.5]) {
            const graph = factGraph({ [input]: ["settings", slot] });
            graph.settings = { class_type, inputs: { value, [input]: value } };
            const fact = factsFor(graph)[input === "sampler_name" ? "sampler" : input];
            assert.equal(fact.status, supported && slot === 0 ? "known" : "unresolved", `${class_type} output${slot} as ${input}`);
            assert.equal(fact.value, supported && slot === 0 ? value : null);
        }
    }
    const graph = factGraph({ seed: ["primitive", 0] });
    graph.primitive = { class_type: "PrimitiveInt", inputs: { seed: "987" } };
    assert.equal(factsFor(graph).seed.status, "unresolved", "a matching input is not the output's value");
    graph.primitive.inputs = { value: "42", seed: "987" };
    assert.equal(factsFor(graph).seed.value, "42");
    for (const [input, class_type, inputs] of [
        ["seed", "RandomNoise", { noise_seed: "42", seed: "42" }],
        ["sampler_name", "KSamplerSelect", { sampler_name: "euler" }],
        ["steps", "BasicScheduler", { steps: "42" }],
        ["cfg", "CFGGuider", { cfg: "7" }],
    ]) {
        const graph = factGraph({ [input]: ["provider", 0] }); graph.provider = { class_type, inputs };
        assert.equal(factsFor(graph)[input === "sampler_name" ? "sampler" : input].status, "unresolved", `${class_type} is not a generic scalar output`);
    }
});

function customFactGraph() {
    return {
        s: { class_type: "SamplerCustomAdvanced", inputs: { guider: ["g", 0], noise: ["n", 0], sampler: ["k", 0], sigmas: ["sigmas", 0] } },
        g: { class_type: "CFGGuider", inputs: { model: ["m", 0], cfg: ["cfg", 0] } },
        n: { class_type: "RandomNoise", inputs: { noise_seed: ["seed", 0] } },
        k: { class_type: "KSamplerSelect", inputs: { sampler_name: ["sampler", 0] } },
        sigmas: { class_type: "BasicScheduler", inputs: { steps: ["steps", 0], scheduler: ["scheduler", 0] } },
        seed: { class_type: "PrimitiveInt", inputs: { value: "9007199254740993" } },
        cfg: { class_type: "PrimitiveFloat", inputs: { value: "4.0000000000000001" } },
        steps: { class_type: "PrimitiveInt", inputs: { value: 22 } },
        sampler: { class_type: "PrimitiveString", inputs: { value: "euler" } },
        scheduler: { class_type: "PrimitiveString", inputs: { value: "karras" } },
        m: { class_type: "UNETLoader", inputs: { unet_name: "flux.safetensors" } },
    };
}

test("facts require supported custom providers and guider outputs", () => {
    assert.deepEqual(factFields.map(field => factsFor(customFactGraph())[field].value), ["9007199254740993", "22", "4.0000000000000001", "euler", "karras", "flux.safetensors"]);
    for (const [input, nodeId, fields] of [["noise", "n", ["seed"]], ["sampler", "k", ["sampler"]], ["sigmas", "sigmas", ["steps", "scheduler"]], ["guider", "g", ["cfg", "model"]]]) {
        for (const slot of [1, 999, -1, 0.5]) {
            const graph = customFactGraph(); graph.s.inputs[input] = [nodeId, slot];
            for (const field of fields) assert.equal(factsFor(graph)[field].status, "unresolved", `${input} output${slot} ${field}`);
        }
        for (const class_type of ["UnknownProvider", "PrimitiveInt", "PrimitiveString", "BasicGuider"]) {
            if (input === "guider" && class_type === "BasicGuider") continue;
            const graph = customFactGraph(); graph[nodeId].class_type = class_type;
            for (const field of fields) assert.equal(factsFor(graph)[field].status, "unresolved", `${class_type} cannot provide ${input}`);
        }
    }
    const basic = customFactGraph(); basic.g = { class_type: "BasicGuider", inputs: { model: ["m", 0] } };
    assert.equal(factsFor(basic).model.value, "flux.safetensors");
    assert.equal(factsFor(basic).cfg.status, "not-recorded");
});

test("facts require model output ancestry with supported loaders and passthroughs", () => {
    for (const [class_type, key] of [["CheckpointLoader", "ckpt_name"], ["CheckpointLoaderSimple", "ckpt_name"], ["unCLIPCheckpointLoader", "ckpt_name"], ["UNETLoader", "unet_name"]]) {
        for (const slot of [0, 1, 2, 999, -1, 0.5]) {
            const graph = factGraph({ model: ["loader", slot] });
            graph.loader = { class_type, inputs: { [key]: "selected.safetensors" } };
            assert.equal(factsFor(graph).model.value, slot === 0 ? "selected.safetensors" : null, `${class_type} output${slot}`);
            assert.equal(factsFor(graph).model.status, slot === 0 ? "known" : "unresolved");
        }
    }
    for (const class_type of ["LoraLoader", "LoraLoaderModelOnly", "UnknownModelPassThrough"]) {
        for (const slot of [0, 1, 2, 999]) {
            const graph = factGraph({ model: ["lora", slot] }); graph.lora = { class_type, inputs: { model: ["model", 0] } };
            assert.equal(factsFor(graph).model.status, class_type !== "UnknownModelPassThrough" && slot === 0 ? "known" : "unresolved", `${class_type} output${slot}`);
        }
    }
    for (const [class_type, inputs] of [["UnknownLoader", { ckpt_name: "fake" }], ["PrimitiveString", { model_name: "fake", value: "fake" }], ["__proto__", { "[object Object]": "fake" }]]) {
        const graph = factGraph({ model: ["unknown", 0] }); graph.unknown = { class_type, inputs };
        assert.equal(factsFor(graph).model.status, "unresolved");
    }
    for (const [input, field] of [["sampler_name", "sampler"], ["scheduler", "scheduler"]]) assert.equal(factsFor(factGraph({ [input]: 42 }))[field].status, "unresolved");
    const numericModel = factGraph(); numericModel.model.inputs.ckpt_name = 42;
    assert.equal(factsFor(numericModel).model.status, "unresolved", "model names require string evidence");
});

// D19: a FaceDetailer re-samples the KSampler's decoded image with its own settings.
function detailerFactGraph(detailer = {}) {
    const graph = factGraph({ seed: "42", steps: 25, cfg: "6.5", sampler_name: "euler", scheduler: "normal" });
    graph.decode = { class_type: "VAEDecode", inputs: { samples: ["s", 0], vae: ["model", 2] } };
    graph.d = { class_type: "FaceDetailer", inputs: { image: ["decode", 0], model: ["model", 0], seed: "7", steps: 12, cfg: "6.5", sampler_name: "euler", scheduler: "normal", denoise: "0.4", ...detailer } };
    return graph;
}
const statusValues = facts => Object.fromEntries(factFields.map(field => [field, [facts[field].status, facts[field].value]]));

test("facts use resolution.sampler_id for detailer-stage nodes", () => {
    // Resolved (the stages' prompts agree): the final stage the server named supplies every fact.
    const resolved = factsFor(detailerFactGraph(), { resolution: { state: "resolved", sampler_id: "d" } });
    assert.deepEqual(statusValues(resolved), {
        seed: ["known", "7"], steps: ["known", "12"], cfg: ["known", "6.5"],
        sampler: ["known", "euler"], scheduler: ["known", "normal"], model: ["known", "model.safetensors"],
    });
    assert.ok(factFields.every(field => resolved[field].sampler_id === "d" && resolved[field].evidence === "configuration"));
    // The upstream KSampler is also accepted when it is the one resolved.
    assert.equal(factsFor(detailerFactGraph(), { resolution: { state: "resolved", sampler_id: "s" } }).seed.value, "42");
    // Ambiguous stages: merged per field, never the detailer's or the upstream value alone.
    const ambiguous = factsFor(detailerFactGraph(), { resolution: { state: "ambiguous", sampler_id: "d", reason_code: "multistage-differs" } });
    assert.deepEqual(statusValues(ambiguous), {
        seed: ["ambiguous", null], steps: ["ambiguous", null], cfg: ["known", "6.5"],
        sampler: ["known", "euler"], scheduler: ["known", "normal"], model: ["known", "model.safetensors"],
    });
    assert.ok(factFields.every(field => ambiguous[field].sampler_id === null), "a merged fact names no single sampler");
    // A field one stage cannot read and the others agree on stays unresolved; not-recorded is not agreement.
    const unreadable = factsFor(detailerFactGraph({ cfg: ["missing", 0] }), { resolution: { state: "ambiguous", sampler_id: "d" } });
    assert.deepEqual([unreadable.cfg.status, unreadable.cfg.value], ["unresolved", null]);
    const unrecorded = detailerFactGraph(); delete unrecorded.d.inputs.scheduler;
    assert.equal(factsFor(unrecorded, { resolution: { state: "ambiguous", sampler_id: "d" } }).scheduler.status, "unresolved");
    // Two detailer stages over one KSampler: three stages merge.
    const chained = detailerFactGraph({ seed: "42" });
    chained.d2 = { class_type: "FaceDetailer", inputs: { image: ["d", 0], model: ["model", 0], seed: "42", steps: 25, cfg: "3", sampler_name: "euler", scheduler: "normal" } };
    assert.deepEqual(statusValues(factsFor(chained, { resolution: { state: "ambiguous", sampler_id: "d2" } })), {
        seed: ["known", "42"], steps: ["ambiguous", null], cfg: ["ambiguous", null],
        sampler: ["known", "euler"], scheduler: ["known", "normal"], model: ["known", "model.safetensors"],
    });
    // Without a named sampler, two samplers stay ambiguous (today's rules, now over the samplers table).
    for (const resolution of [undefined, { state: "ambiguous", sampler_id: null }, { state: "unresolved", sampler_id: "d" }]) {
        assert.ok(Object.values(factsFor(detailerFactGraph(), { resolution })).every(fact => fact.status === "ambiguous"), JSON.stringify(resolution));
    }
    // A sampler_id the graph or the samplers table does not hold never falls back to a guess.
    for (const sampler_id of ["missing", "decode", "model", "__proto__"]) {
        for (const state of ["resolved", "ambiguous"]) {
            const facts = factsFor(detailerFactGraph(), { resolution: { state, sampler_id } });
            assert.ok(Object.values(facts).every(fact => fact.status === "unresolved" && fact.value === null), `${state} ${sampler_id}`);
        }
    }
    // Stage ancestry that cannot be walked is unresolved, not a partial merge.
    const cyclic = detailerFactGraph(); cyclic.decode.inputs.samples = ["d", 0];
    const unlinked = detailerFactGraph({ image: "not a link" });
    for (const graph of [cyclic, unlinked]) {
        assert.ok(Object.values(factsFor(graph, { resolution: { state: "ambiguous", sampler_id: "d" } })).every(fact => fact.status === "unresolved"));
    }
    const wide = detailerFactGraph();
    let previous = ["s", 0];
    for (let index = 0; index < 600; index++) { wide[`hop${index}`] = { class_type: "ImageScale", inputs: { image: previous } }; previous = [`hop${index}`, 0]; }
    wide.decode.inputs.samples = ["s", 0]; wide.d.inputs.image = previous;
    assert.ok(Object.values(factsFor(wide, { resolution: { state: "ambiguous", sampler_id: "d" } })).every(fact => fact.status === "unresolved"), "the stage walk is bounded by MAX_VISITS");
});

test("Flux2Scheduler provides steps and scheduler is its node type", () => {
    const graph = customFactGraph();
    graph.sigmas = { class_type: "Flux2Scheduler", inputs: { steps: 4, width: 1024, height: 1024 } };
    const facts = factsFor(graph);
    assert.deepEqual([facts.steps.status, facts.steps.value], ["known", "4"]);
    assert.deepEqual([facts.scheduler.status, facts.scheduler.value, facts.scheduler.evidence], ["known", "Flux2Scheduler", "configuration"]);
    for (const slot of [1, -1]) {
        const linked = customFactGraph(); linked.sigmas = graph.sigmas; linked.s.inputs.sigmas = ["sigmas", slot];
        assert.equal(factsFor(linked).scheduler.status, "unresolved", `output${slot}`);
    }
    // Its own inputs cannot replace the node type, and steps keep exact-integer rules.
    const named = customFactGraph(); named.sigmas = { class_type: "Flux2Scheduler", inputs: { steps: 0, scheduler: "karras" } };
    assert.equal(factsFor(named).scheduler.value, "Flux2Scheduler");
    assert.equal(factsFor(named).steps.status, "unresolved");
});

test("ModelSampling passthroughs reach UNETLoader", () => {
    const graph = customFactGraph();
    graph.g.inputs.model = ["flux", 0];
    graph.flux = { class_type: "ModelSamplingFlux", inputs: { model: ["aura", 0], max_shift: "1.15", base_shift: "0.5" } };
    graph.aura = { class_type: "ModelSamplingAuraFlow", inputs: { model: ["sd3", 0], shift: "1.73" } };
    graph.sd3 = { class_type: "ModelSamplingSD3", inputs: { model: ["lora", 0], shift: "3" } };
    graph.lora = { class_type: "LoraLoaderModelOnly", inputs: { model: ["m", 0], lora_name: "style", strength_model: "1" } };
    assert.deepEqual([factsFor(graph).model.status, factsFor(graph).model.value], ["known", "flux.safetensors"]);
    for (const node of ["flux", "aura", "sd3"]) {
        const wrongSlot = structuredClone(graph);
        const parent = { flux: "g", aura: "flux", sd3: "aura" }[node];
        wrongSlot[parent].inputs.model = [node, 1];
        assert.equal(factsFor(wrongSlot).model.status, "unresolved", `${node} output1`);
        const unlisted = structuredClone(graph); unlisted[node].class_type = "ModelSamplingUnknown";
        assert.equal(factsFor(unlisted).model.status, "unresolved", `${node} unlisted`);
    }
});

test("vectors.json pins full facts for every inspection fixture", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "fixtures", "inspection_parity", "vectors.json"), "utf8"));
    const inspection = vectors.filter(vector => vector.profile === "inspection");
    assert.ok(inspection.length > 0);
    const statuses = new Set(["known", "ambiguous", "unresolved", "not-recorded"]);
    for (const vector of inspection) {
        const facts = vector.expect.facts;
        assert.deepEqual(Object.keys(facts), factFields, vector.name);
        for (const field of factFields) {
            assert.ok(statuses.has(facts[field].status), `${vector.name} ${field}`);
            assert.equal(facts[field].value === null, facts[field].status !== "known", `${vector.name} ${field} value`);
        }
    }
    // f19: KSampler seed 42 / steps 25 / cfg 6.5 then FaceDetailer seed 7 / steps 12 / cfg 4.5, different prompts.
    const f19 = inspection.find(vector => vector.fixture === "f19");
    assert.equal(f19.expect.state, "ambiguous");
    assert.deepEqual(Object.fromEntries(factFields.map(field => [field, f19.expect.facts[field].status])), {
        seed: "ambiguous", steps: "ambiguous", cfg: "ambiguous", sampler: "known", scheduler: "known", model: "known",
    });
});

test("wide workflow lookups retain selected core sampler facts", () => {
    const nodes = [{ id: "text", type: "PrimitiveString", widgets_values_named: { value: "unrelated text" } }], links = [];
    let linkId = 1;
    for (let index = 0; index < 20; index++) {
        nodes.push({ id: `r${index}`, type: "Reroute", inputs: [{ name: "value", link: linkId }] });
        links.push([linkId++, index ? `r${index - 1}` : "text", 0, `r${index}`, 0, "STRING"]);
    }
    for (let index = 0; index < 40; index++) {
        nodes.push({ id: `encoder${index}`, type: "CLIPTextEncode", inputs: [{ name: "text", link: linkId }] });
        links.push([linkId++, "r19", 0, `encoder${index}`, 0, "STRING"]);
    }
    nodes.push({ id: "model", type: "CheckpointLoaderSimple", widgets_values_named: { ckpt_name: "selected.safetensors" } },
        { id: "steps", type: "PrimitiveInt", widgets_values_named: { value: 28 } },
        { id: "s", type: "KSampler", widgets_values: ["9007199254740993", "fixed", 28, "6.5000000000000000001", "euler", "normal"], inputs: [{ name: "model", link: linkId }, { name: "steps", link: linkId + 1 }] });
    links.push([linkId, "model", 0, "s", 0, "MODEL"], [linkId + 1, "steps", 0, "s", 1, "INT"]);
    const facts = factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph({ nodes, links }) });
    assert.deepEqual(factFields.map(field => facts[field].value), ["9007199254740993", "28", "6.5000000000000000001", "euler", "normal", "selected.safetensors"]);
});

test("workflow facts require supported passthrough output slots and bypass ancestry", () => {
    function workflow(type, slot, mode) {
        const pass = { id: "pass", type, ...(mode === undefined ? {} : { mode }), widgets_values: ["selected"], inputs: [{ name: type === "Reroute" ? "value" : "model", type: "MODEL", link: 1 }] };
        const nodes = [{ id: "m", type: "CheckpointLoaderSimple", widgets_values_named: { ckpt_name: "selected.safetensors" } }, pass,
            { id: "s", type: "KSampler", widgets_values: [0, "fixed", 28, "0", "euler", "normal"], inputs: [{ name: "model", link: 2 }] }];
        if (type === "GetNode") {
            nodes.push({ id: "set", type: "SetNode", widgets_values: ["selected"], inputs: [{ name: "model", link: 1 }] });
            pass.inputs = [];
        }
        return { nodes, links: [[1, "m", 0, "pass", 0, "MODEL"], [2, "pass", slot, "s", 0, "MODEL"]] };
    }
    const model = value => factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(value) }).model;
    for (const [type, mode] of [["Reroute", undefined], ["GetNode", undefined], ["SetNode", undefined], ["LoraLoader", 4], ["LoraLoaderModelOnly", 4]]) {
        for (const slot of [0, 1, 999, -1, 0.5]) {
            assert.equal(model(workflow(type, slot, mode)).status, slot === 0 ? "known" : "unresolved", `${type} output${slot}`);
        }
    }
    assert.equal(model(workflow("UnknownBypass", 0, 4)).status, "unresolved");
    const duplicate = workflow("LoraLoader", 0, 4);
    duplicate.nodes[1].inputs.push({ name: "model", type: "MODEL", link: 1 });
    assert.equal(model(duplicate).status, "unresolved", "bypass model input is uniquely named");
    const wrongOrigin = workflow("Reroute", 0); wrongOrigin.links[0][2] = 1;
    assert.equal(model(wrongOrigin).status, "unresolved", "passthrough cannot turn CLIP into MODEL");
    const cycle = workflow("Reroute", 0); cycle.links[0][1] = "pass";
    assert.equal(model(cycle).status, "unresolved");
    const unnamed = workflow("GetNode", 0);
    delete unnamed.nodes.find(node => node.id === "pass").widgets_values;
    delete unnamed.nodes.find(node => node.id === "set").widgets_values;
    assert.equal(model(unnamed).status, "unresolved", "missing Get/Set names are not a source association");
    const scalar = factGraph({ seed: ["bypass", 0] });
    scalar.bypass = { class_type: "LoraLoader", inputs: { model: ["primitive", 0] } };
    scalar.primitive = { class_type: "PrimitiveInt", inputs: { value: 42 } };
    const scalarWorkflow = factWorkflowFromGraph(scalar); scalarWorkflow.nodes.find(node => node.id === "bypass").mode = 4;
    assert.equal(factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(scalarWorkflow) }).seed.status, "unresolved", "bypassed MODEL output cannot become a scalar");
    for (const [input, field] of [["noise", "seed"], ["sampler", "sampler"], ["sigmas", "steps"], ["guider", "model"]]) {
        const graph = customFactGraph(), target = graph.s.inputs[input];
        graph.s.inputs[input] = ["bypass", 0]; graph.bypass = { class_type: "LoraLoaderModelOnly", inputs: { model: target } };
        const workflow = factWorkflowFromGraph(graph); workflow.nodes.find(node => node.id === "bypass").mode = 4;
        assert.equal(factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) })[field].status, "unresolved", `MODEL output cannot become ${input}`);
    }
});

function factWorkflowFromGraph(graph) {
    const nodes = [], links = [];
    for (const [id, node] of Object.entries(graph)) {
        const inputs = [], widgets_values_named = {};
        for (const [name, value] of Object.entries(node.inputs)) {
            if (Array.isArray(value)) {
                const link = links.length + 1;
                links.push([link, value[0], value[1], id, inputs.length, "*"]);
                inputs.push({ name, link });
            } else widgets_values_named[name] = value;
        }
        nodes.push({ id, type: node.class_type, inputs, widgets_values_named });
    }
    return { nodes, links };
}

test("workflow facts count combined original ancestry once across passthroughs and providers", () => {
    const inspect = graph => factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(factWorkflowFromGraph(graph)) });
    function chain(graph, reroutes, primitives, model = false) {
        let link = model ? ["model", 0] : null;
        for (let index = 0; index < primitives; index++) {
            graph[`p${index}`] = { class_type: model ? "LoraLoader" : "PrimitiveInt", inputs: model ? { model: link } : { value: link || "42" } };
            link = [`p${index}`, 0];
        }
        for (let index = 0; index < reroutes; index++) {
            graph[`r${index}`] = { class_type: "Reroute", inputs: { value: link } };
            link = [`r${index}`, 0];
        }
        return link;
    }
    for (const [reroutes, known] of [[32, true], [33, false]]) {
        const scalar = factGraph(); scalar.s.inputs.seed = chain(scalar, reroutes, 32);
        assert.equal(inspect(scalar).seed.status, known ? "known" : "unresolved", `${reroutes}+32 scalar links`);
        const model = factGraph(); model.s.inputs.model = chain(model, reroutes, 31, true);
        assert.equal(inspect(model).model.status, known ? "known" : "unresolved", `${reroutes}+31 LoRA+loader links`);
        const noise = customFactGraph();
        noise.n.inputs.noise_seed = chain(noise, reroutes - 1, 31);
        noise.noiseAlias = { class_type: "Reroute", inputs: { value: ["n", 0] } };
        noise.s.inputs.noise = ["noiseAlias", 0];
        assert.equal(inspect(noise).seed.status, known ? "known" : "unresolved", `noise alias+provider+${reroutes - 1}+31 links`);
        const guider = customFactGraph();
        guider.model = { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "model.safetensors" } };
        guider.g.inputs.model = chain(guider, reroutes - 1, 30, true);
        guider.guiderAlias = { class_type: "Reroute", inputs: { value: ["g", 0] } };
        guider.s.inputs.guider = ["guiderAlias", 0];
        assert.equal(inspect(guider).model.status, known ? "known" : "unresolved", `guider alias+provider+${reroutes - 1}+30 LoRA+loader links`);
    }
    const cycle = factGraph();
    cycle.s.inputs.seed = ["r", 0]; cycle.r = { class_type: "Reroute", inputs: { value: ["p", 0] } }; cycle.p = { class_type: "PrimitiveInt", inputs: { value: ["r", 0] } };
    assert.equal(inspect(cycle).seed.status, "unresolved");
    cycle.s.inputs.model = ["r", 0]; cycle.p = { class_type: "LoraLoader", inputs: { model: ["r", 0] } };
    assert.equal(inspect(cycle).model.status, "unresolved");
    for (const [reroutes, known] of [[32, true], [33, false]]) {
        const graph = factGraph(), value = chain(graph, reroutes, 31);
        const workflow = factWorkflowFromGraph(graph);
        const sourceLink = workflow.links.length + 1;
        workflow.nodes.push({ id: "set", type: "SetNode", widgets_values: ["seed"], inputs: [{ name: "value", link: sourceLink }] }, { id: "get", type: "GetNode", widgets_values: ["seed"], inputs: [] });
        workflow.links.push([sourceLink, value[0], 0, "set", 0, "INT"], [sourceLink + 1, "get", 0, "s", 0, "INT"]);
        workflow.nodes.find(node => node.id === "s").inputs.push({ name: "seed", link: sourceLink + 1 });
        const fact = factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) }).seed;
        assert.equal(fact.status, known ? "known" : "unresolved", `Get+${reroutes}+31 links, no extra setter expansion`);
    }
});

test("workflow sampler selection retains prototype-named node identities", () => {
    const workflow = { nodes: [
        { id: "__proto__", type: "KSampler", widgets_values: [1, "fixed", 20, "7", "euler", "normal"] },
        { id: "other", type: "KSampler", widgets_values: [2, "fixed", 20, "7", "euler", "normal"] },
    ], links: [] };
    assert.ok(Object.values(factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) })).every(fact => fact.status === "ambiguous"));
});

test("workflow-only facts are configuration without creating sendable roles", () => {
    const workflow = { nodes: [
        { id: 1, type: "UNETLoader", widgets_values: ["flux.safetensors", "default"] },
        { id: 2, type: "SetNode", widgets_values: ["model"], inputs: [{ name: "MODEL", link: 1 }] },
        { id: 3, type: "GetNode", widgets_values: ["model"] },
        { id: 4, type: "KSamplerAdvanced", widgets_values: ["enable", "9007199254740993", "fixed", 28, "6.5000000000000000001", "euler", "normal", 0, 28, "disable"], inputs: [{ name: "model", link: 2 }] },
    ], links: [[1, 1, 0, 2, 0, "MODEL"], [2, 3, 0, 4, 0, "MODEL"]] };
    const facts = factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) });
    assert.deepEqual(factFields.map(field => facts[field].value), ["9007199254740993", "28", "6.5000000000000000001", "euler", "normal", "flux.safetensors"]);
    assert.ok(Object.values(facts).every(fact => fact.source === "workflow" && fact.evidence === "configuration"));
    assert.equal(facts.positive, undefined);
    workflow.nodes.push({ ...workflow.nodes[3] });
    assert.ok(Object.values(factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) })).every(fact => fact.status === "unresolved"));
});

test("limited projection stays unresolved", () => {
    for (const extra of [
        { prompt_graph: { status: "limited", value: null } },
        { prompt_graph: absentFactGraph(), workflow_graph: { status: "limited", value: null } },
        { prompt_graph: absentFactGraph(), metadataStatus: "limited", parameters: null },
    ]) {
        const facts = factsFor(null, extra);
        assert.ok(Object.values(facts).every(fact => fact.status === "unresolved" && fact.value === null && fact.reason === "projection-limited"));
    }
    const unreadable = factsFor(null, { prompt_graph: absentFactGraph(), metadataStatus: "unreadable" });
    assert.equal(unreadable.seed.reason, "metadata-unreadable");
    const absent = factsFor(null, { prompt_graph: absentFactGraph(), metadataStatus: "absent" });
    assert.equal(absent.seed.status, "not-recorded");
});

test("facts preserve exact video-projected decimals without reading reserialized raw", () => {
    const graph = factGraph();
    const facts = factsFor(graph, { recordFormats: { prompt: "reserialized-json" }, raw: { prompt: '{"s":{"inputs":{"seed":9007199254740992,"cfg":6.5}}}' } });
    assert.equal(facts.seed.value, "9007199254740993");
    assert.equal(facts.cfg.value, "6.5000000000000000001");
    assert.equal(factsFor(factGraph({ cfg: "1E+1000000000" })).cfg.value, "1E+1000000000");
});

test("nontext parameters remain unresolved", () => {
    const parameters = "A portrait\nNegative prompt: blur\nSteps: 28, Sampler: Euler, Schedule type: Karras, CFG scale: 6.5000000000000000001, Seed: 9007199254740993, Model: model";
    const base = { prompt_graph: absentFactGraph(), parameters };
    for (const format of [undefined, "reserialized-json", "invalid"]) {
        const facts = factsFor(null, { ...base, recordFormats: format ? { parameters: format } : {} });
        assert.ok(Object.values(facts).every(fact => fact.status === "unresolved" && fact.value === null));
    }
    const facts = factsFor(null, { ...base, recordFormats: { parameters: "original-text" } });
    assert.deepEqual(factFields.map(field => facts[field].value), ["9007199254740993", "28", "6.5000000000000000001", "Euler", "Karras", "model"]);
    assert.ok(Object.values(facts).every(fact => fact.source === "embedded-parameters" && fact.evidence === "embedded-parameters"));
    for (const invalid of [parameters + "\nSteps: 5, Seed: 2", parameters + ", Seed: 1", parameters + "\ntrailing", parameters.replace("Seed: 9007199254740993", "Seed: -1"), parameters.replace("Karras", "invented"), parameters.replace("Negative prompt: blur", "Negative prompt: blur\nNegative prompt: again")]) {
        assert.ok(Object.values(factsFor(null, { ...base, parameters: invalid, recordFormats: { parameters: "original-text" } })).some(fact => fact.status === "unresolved"));
    }
});

test("malformed or unsupported API evidence blocks plausible workflow and parameter fallback", () => {
    const workflow = { nodes: [{ id: 1, type: "KSampler", widgets_values: [42, "fixed", 20, 7, "euler", "normal"] }], links: [] };
    const extra = { workflow_graph: parsedFactGraph(workflow), parameters: "portrait\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 42", recordFormats: { parameters: "original-text" } };
    for (const prompt_graph of [{ status: "malformed", value: null }, parsedFactGraph({ s: { class_type: "UnknownSampler", inputs: { seed: 99 } } }), parsedFactGraph({ s: { class_type: "KSampler", inputs: [] } })]) {
        const facts = factsFor(null, { ...extra, prompt_graph });
        assert.ok(Object.values(facts).every(fact => fact.status === "unresolved" && fact.value === null));
        assert.ok(Object.values(facts).every(fact => fact.source !== "workflow" && fact.source !== "embedded-parameters"));
    }
});

test("conservative facts bound cycles fan-out visits and link depth", () => {
    const graph = factGraph({ seed: ["loop", 0], model: ["loop", 0] });
    graph.loop = { class_type: "Unknown", inputs: { seed: ["loop", 0], model: ["loop", 0], a: ["loop", 0], b: ["loop", 0] } };
    assert.equal(factsFor(graph).seed.status, "unresolved");
    assert.equal(factsFor(graph).model.status, "unresolved");
    graph.loop = { class_type: "PrimitiveInt", inputs: { value: ["loop", 0] } };
    assert.equal(factsFor(graph).seed.status, "unresolved", "registered scalar cycle is bounded");
    graph.loop = { class_type: "LoraLoader", inputs: { model: ["loop", 0] } };
    assert.equal(factsFor(graph).model.status, "unresolved", "registered model cycle is bounded");
    const chain = depth => {
        const value = factGraph({ seed: ["p0", 0] });
        for (let index = 0; index < depth; index++) value[`p${index}`] = { class_type: "PrimitiveInt", inputs: { value: index === depth - 1 ? "42" : [`p${index + 1}`, 0] } };
        return value;
    };
    assert.equal(factsFor(chain(64)).seed.value, "42");
    assert.equal(factsFor(chain(65)).seed.status, "unresolved");
    const custom = chain(64);
    custom.s = { class_type: "SamplerCustomAdvanced", inputs: { noise: ["noise", 0] } };
    custom.noise = { class_type: "RandomNoise", inputs: { noise_seed: ["p0", 0] } };
    assert.equal(factsFor(custom).seed.status, "unresolved", "the provider itself consumes one of the 64 allowed links");
});

// Spec section 10: graph size is not a facts bound. Only the per-lookup visit
// set (MAX_VISITS) and the projection's 20,000-node ceiling (`limited`) bound work.
test("facts resolve on a parsed 600-node graph with one KSampler", () => {
    const large = factGraph();
    for (let index = 0; index < 598; index++) large[`unused${index}`] = { class_type: "PrimitiveInt", inputs: { value: index } };
    assert.equal(Object.keys(large).length, 600);
    const facts = factsFor(large);
    for (const [field, value] of [["seed", "9007199254740993"], ["steps", "28"], ["cfg", "6.5000000000000000001"]]) {
        assert.equal(facts[field].status, "known", field);
        assert.equal(facts[field].value, value);
        assert.equal(facts[field].reason, null);
    }
});

test("workflow facts are not gated by UI node count", () => {
    const nodes = [
        { id: 1, type: "CheckpointLoaderSimple", widgets_values: ["wide.safetensors"] },
        { id: 2, type: "KSampler", widgets_values: [42, "fixed", 20, "7", "euler", "normal"], inputs: [{ name: "model", link: 1 }] },
    ];
    for (let index = 0; index < 598; index++) nodes.push({ id: `note${index}`, type: "Note", widgets_values: [`note ${index}`] });
    const workflow = { nodes, links: [[1, 1, 0, 2, 0, "MODEL"]] };
    const facts = factsFor(null, { prompt_graph: absentFactGraph(), workflow_graph: parsedFactGraph(workflow) });
    assert.deepEqual(factFields.map(field => facts[field].status), Array(6).fill("known"));
    assert.deepEqual(factFields.map(field => facts[field].value), ["42", "20", "7", "euler", "normal", "wide.safetensors"]);
    assert.ok(Object.values(facts).every(fact => fact.source === "workflow" && fact.reason === null));
});

// Same shape as the workflow attached to #75: PromptManager nodes are saved as
// CLIPTextEncode with extra inputs, and the checkpoint loader is pysssss'.
const issue75Prompt = {
    1: {
        class_type: "KSampler",
        inputs: {
            seed: 480655185510613, steps: 25, cfg: 5.5,
            sampler_name: "euler_ancestral", scheduler: "normal", denoise: 0.8,
            model: ["20", 0], positive: ["39", 0], negative: ["40", 0], latent_image: ["36", 0],
        },
    },
    8: { class_type: "CLIPSetLastLayer", inputs: { stop_at_clip_layer: -2, clip: ["20", 1] } },
    20: {
        class_type: "CheckpointLoader|pysssss",
        inputs: { ckpt_name: "Anime\\Model-XL.safetensors", prompt: "[none]", example: "[none]" },
    },
    39: {
        class_type: "CLIPTextEncode",
        inputs: {
            text: "1girl, cat_ears, smile", category: "", tags: "", search_text: "",
            prepend_text: "masterpiece, best quality, ", append_text: "", clip: ["8", 0],
        },
    },
    40: {
        class_type: "CLIPTextEncode",
        inputs: {
            text: "3d, crowd", category: "", tags: "", search_text: "",
            prepend_text: "bad quality, worst quality, ", append_text: "", clip: ["8", 0],
        },
    },
};

test("#75: traces PromptManager prompts and pysssss checkpoint from the sampler", () => {
    const r = extractGenerationParams({ prompt: issue75Prompt });
    assert.equal(r.positivePrompt, "masterpiece, best quality, 1girl, cat_ears, smile");
    assert.equal(r.negativePrompt, "bad quality, worst quality, 3d, crowd");
    assert.equal(r.checkpoint, "Anime\\Model-XL.safetensors");
    assert.deepEqual(
        [r.seed, r.steps, r.cfgScale, r.sampler],
        [480655185510613, 25, 5.5, "euler_ancestral"],
    );
});

test("#75: workflow blob does not override values from the prompt graph", () => {
    const workflow = { nodes: [{ type: "KSampler", widgets_values: [1, "randomize", 99, 7, "ddim"] }] };
    const r = extractGenerationParams({ prompt: issue75Prompt, workflow });
    assert.equal(r.steps, 25);
    assert.equal(r.cfgScale, 5.5);
});

test("#75: recovers per-node text when SaveImage patch overwrote every PromptManager node", () => {
    // Pre-fix images: both nodes carry the final positive text in the prompt graph,
    // but the workflow graph still has each node's real widget values.
    const positiveFinal = "masterpiece, best quality, 1girl, smile";
    const prompt = {
        1: { class_type: "KSampler", inputs: { seed: 1, steps: 20, cfg: 7, sampler_name: "euler", positive: ["39", 0], negative: ["40", 0], model: ["20", 0] } },
        20: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } },
        39: { class_type: "PromptManager", inputs: { text: positiveFinal, prepend_text: "masterpiece, best quality,", append_text: "" } },
        40: { class_type: "PromptManager", inputs: { text: positiveFinal, prepend_text: "bad quality,", append_text: "" } },
    };
    const workflow = {
        nodes: [
            { id: 39, type: "PromptManager", widgets_values: ["1girl, smile", "", "", "", "masterpiece, best quality,", ""] },
            { id: 40, type: "PromptManager", widgets_values: ["3d, crowd", "", "", "", "bad quality,", ""] },
        ],
    };
    const r = extractGenerationParams({ prompt, workflow });
    assert.equal(r.positivePrompt, "masterpiece, best quality, 1girl, smile");
    assert.equal(r.negativePrompt, "bad quality, 3d, crowd");
});

test("current metadata: API text wins over a stale or unexpanded workflow widget", () => {
    // Dynamic prompt expanded at queue time: API holds what ran, workflow holds the template
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["39", 0], negative: ["40", 0], model: ["20", 0] } },
        20: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } },
        39: { class_type: "PromptManager", inputs: { text: "a red cat", prepend_text: "", append_text: "" } },
        40: { class_type: "PromptManager", inputs: { text: "blurry", prepend_text: "", append_text: "" } },
    };
    const workflow = {
        nodes: [
            { id: 39, type: "PromptManager", widgets_values: ["a {red|blue} cat", "", "", "", "", ""] },
            { id: 40, type: "PromptManager", widgets_values: ["blurry", "", "", "", "", ""] },
        ],
    };
    const r = extractGenerationParams({ prompt, workflow });
    assert.equal(r.positivePrompt, "a red cat");
    assert.equal(r.negativePrompt, "blurry");
});

test("current metadata: linked text is followed, not replaced by the widget's stale value", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["39", 0], negative: ["40", 0], model: ["20", 0] } },
        20: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } },
        39: { class_type: "PromptManager", inputs: { text: ["50", 0], prepend_text: "", append_text: "" } },
        40: { class_type: "PromptManager", inputs: { text: ["50", 0], prepend_text: "bad,", append_text: "" } },
        50: { class_type: "PrimitiveString", inputs: { value: "shared text" } },
    };
    const workflow = {
        nodes: [
            { id: 39, type: "PromptManager", widgets_values: ["old value A", "", "", "", "", ""] },
            { id: 40, type: "PromptManager", widgets_values: ["old value B", "", "", "", "bad,", ""] },
        ],
    };
    const r = extractGenerationParams({ prompt, workflow });
    assert.equal(r.positivePrompt, "shared text");
});

test("prepend/append are not doubled when text is already the combined prompt", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManager", inputs: { text: "best quality, a cat, 8k", prepend_text: "best quality,", append_text: "8k" } },
        3: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
    };
    assert.equal(extractGenerationParams({ prompt }).positivePrompt, "best quality, a cat, 8k");
});

test("linked prepend/append inputs are resolved (no longer baked into text since 3.2.3)", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManager", inputs: { text: "a fox", prepend_text: ["5", 0], append_text: ["6", 0] } },
        3: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
        5: { class_type: "PrimitiveString", inputs: { value: "masterpiece," } },
        6: { class_type: "PrimitiveString", inputs: { value: "8k" } },
    };
    assert.equal(extractGenerationParams({ prompt }).positivePrompt, "masterpiece, a fox 8k");
});

test("negative prompt is identified by link, not by keywords", () => {
    const prompt = {
        3: { class_type: "KSampler", inputs: { seed: 1, steps: 20, cfg: 7, sampler_name: "euler", positive: ["6", 0], negative: ["7", 0], model: ["4", 0] } },
        4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sd15.safetensors" } },
        6: { class_type: "CLIPTextEncode", inputs: { text: "embedding:goodstyle, a lighthouse" } },
        7: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
    };
    const r = extractGenerationParams({ prompt });
    assert.equal(r.positivePrompt, "embedding:goodstyle, a lighthouse");
    assert.equal(r.negativePrompt, "blurry");
});

test("follows LoRA chains, ControlNet pass-through, concatenated text and primitive inputs", () => {
    const prompt = {
        1: { class_type: "KSamplerAdvanced", inputs: { noise_seed: 42, steps: ["9", 0], cfg: 6, sampler_name: "dpmpp_2m", positive: ["5", 0], negative: ["5", 1], model: ["3", 0] } },
        2: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "base.safetensors" } },
        3: { class_type: "LoraLoader", inputs: { lora_name: "style.safetensors", model: ["2", 0], clip: ["2", 1] } },
        5: { class_type: "ControlNetApplyAdvanced", inputs: { positive: ["6", 0], negative: ["7", 0], strength: 1 } },
        6: { class_type: "CLIPTextEncode", inputs: { text: ["8", 0] } },
        7: { class_type: "CLIPTextEncode", inputs: { text: "lowres" } },
        8: { class_type: "Text Concatenate", inputs: { delimiter: ", ", text_a: "a castle", text_b: "at dusk" } },
        9: { class_type: "PrimitiveInt", inputs: { value: 30 } },
    };
    const r = extractGenerationParams({ prompt });
    assert.equal(r.positivePrompt, "a castle, at dusk");
    assert.equal(r.negativePrompt, "lowres");
    assert.equal(r.checkpoint, "base.safetensors");
    assert.equal(r.seed, 42);
    assert.equal(r.steps, 30);
});

test("custom sampler graphs (Flux: SamplerCustomAdvanced + CFGGuider) resolve every field", () => {
    const prompt = {
        85: { class_type: "UNETLoader", inputs: { unet_name: "flux2-klein.safetensors" } },
        135: { class_type: "Lora Loader (LoraManager)", inputs: { model: ["85", 0], clip: ["90", 0] } },
        149: { class_type: "PromptManager", inputs: { text: "a red fox", prepend_text: ["133", 0], append_text: "" } },
        152: { class_type: "SeedHistory", inputs: { seed: 1725922648 } },
        // ControlNet-style node with positive/negative but no model: must not be taken as the sampler
        200: { class_type: "ControlNetApplyAdvanced", inputs: { positive: ["149", 0], negative: ["274", 0], strength: 1 } },
        265: { class_type: "SamplerCustomAdvanced", inputs: { noise: ["267", 0], guider: ["268", 0], sampler: ["269", 0], sigmas: ["266", 0] } },
        266: { class_type: "Flux2Scheduler", inputs: { steps: 50, width: ["153", 1] } },
        267: { class_type: "RandomNoise", inputs: { noise_seed: ["152", 0] } },
        268: { class_type: "CFGGuider", inputs: { cfg: 5.0, model: ["135", 0], positive: ["149", 0], negative: ["274", 0] } },
        269: { class_type: "KSamplerSelect", inputs: { sampler_name: "uni_pc" } },
        274: { class_type: "CLIPTextEncode", inputs: { text: "watermark" } },
    };
    const r = extractGenerationParams({ prompt });
    assert.deepEqual(
        [r.positivePrompt, r.negativePrompt, r.checkpoint, r.cfgScale, r.seed, r.steps, r.sampler],
        ["a red fox", "watermark", "flux2-klein.safetensors", 5.0, 1725922648, 50, "uni_pc"],
    );
});

test("multi-output settings nodes (SamplerCombo) map each field to its own value", () => {
    const prompt = {
        5: {
            class_type: "KSampler",
            inputs: {
                seed: ["162", 0], steps: ["165", 2], cfg: ["165", 3], sampler_name: ["165", 0],
                positive: ["6", 0], negative: ["7", 0], model: ["4", 0],
            },
        },
        4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "m.safetensors" } },
        6: { class_type: "CLIPTextEncode", inputs: { text: "a cat" } },
        7: { class_type: "CLIPTextEncode", inputs: { text: "blurry" } },
        162: { class_type: "SeedHistory", inputs: { seed: 1495646836, seed_history_ui: "" } },
        165: { class_type: "SamplerCombo", inputs: { sampler_name: "euler_ancestral", scheduler: "normal", steps: 50, cfg: 7 } },
    };
    const r = extractGenerationParams({ prompt });
    assert.deepEqual([r.seed, r.steps, r.cfgScale, r.sampler], [1495646836, 50, 7, "euler_ancestral"]);
});

test("workflow-only images account for the hidden control_after_generate widget", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "CheckpointLoader|pysssss", widgets_values: ["model.safetensors", "[none]"] },
            { id: 2, type: "KSampler", widgets_values: [123, "randomize", 25, 5.5, "euler", "normal", 1] },
        ],
    };
    const r = extractGenerationParams({ workflow });
    assert.deepEqual(
        [r.checkpoint, r.seed, r.steps, r.cfgScale, r.sampler],
        ["model.safetensors", 123, 25, 5.5, "euler"],
    );
});

test("cyclic, fan-out graphs from crafted PNGs resolve quickly", () => {
    // Every node links to itself and its neighbour through several inputs and has
    // no scalars: without a visited set this explores ~inputs^MAX_DEPTH paths.
    const loop = (id, next) => ({
        class_type: "Evil",
        inputs: {
            text_a: [id, 0], text_b: [next, 0], text_c: [id, 0], text_d: [next, 0],
            value_a: [id, 0], value_b: [next, 0], model: [next, 0],
        },
    });
    const prompt = {
        1: { class_type: "KSampler", inputs: { seed: ["2", 0], steps: ["3", 0], positive: ["2", 0], negative: ["3", 0], model: ["2", 0] } },
        2: loop("2", "3"),
        3: loop("3", "2"),
    };
    const started = Date.now();
    const r = extractGenerationParams({ prompt });
    assert.ok(Date.now() - started < 500, `took ${Date.now() - started}ms`);
    assert.equal(r.positivePrompt, "No prompt found");
    assert.equal(r.seed, "Unknown");
});

test("chained encoders whose prepend and append both link onward stay linear", () => {
    const prompt = { 1: { class_type: "KSampler", inputs: { positive: ["n0", 0], negative: ["n0", 0], model: ["n0", 0] } } };
    for (let i = 0; i < 60; i++) {
        prompt[`n${i}`] = {
            class_type: "PromptManager",
            inputs: { text: `t${i}`, prepend_text: [`n${i + 1}`, 0], append_text: [`n${i + 1}`, 0] },
        };
    }
    const started = Date.now();
    extractGenerationParams({ prompt });
    assert.ok(Date.now() - started < 500, `took ${Date.now() - started}ms`);
});

// UI-format link: [id, from_node, from_slot, to_node, to_slot, type]
test("workflow-only: traces prompts, model and KSamplerAdvanced settings through Set/Get and Reroute", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "UNETLoader", widgets_values: ["flux.safetensors", "default"], outputs: [{ links: [1] }] },
            { id: 2, type: "SetNode", widgets_values: ["model"], inputs: [{ name: "MODEL", link: 1 }] },
            { id: 3, type: "GetNode", widgets_values: ["model"], outputs: [{ links: [2] }] },
            { id: 4, type: "PromptManager", widgets_values: ["a castle", "", "", "", "masterpiece,", ""], inputs: [], outputs: [{ links: [3] }] },
            { id: 5, type: "Reroute", inputs: [{ name: "", link: 3 }], outputs: [{ links: [4] }] },
            { id: 6, type: "CLIPTextEncode", widgets_values: ["lowres"], outputs: [{ links: [5] }] },
            {
                id: 7, type: "KSamplerAdvanced",
                inputs: [{ name: "model", link: 2 }, { name: "positive", link: 4 }, { name: "negative", link: 5 }],
                widgets_values: ["enable", 77, "randomize", 30, 6.5, "dpmpp_2m", "karras", 0, 10000, "disable"],
            },
        ],
        links: [
            [1, 1, 0, 2, 0, "MODEL"], [2, 3, 0, 7, 0, "MODEL"], [3, 4, 0, 5, 0, "CONDITIONING"],
            [4, 5, 0, 7, 1, "CONDITIONING"], [5, 6, 0, 7, 2, "CONDITIONING"],
        ],
    };
    const r = extractGenerationParams({ workflow });
    assert.deepEqual(
        [r.positivePrompt, r.negativePrompt, r.checkpoint, r.seed, r.steps, r.cfgScale, r.sampler],
        ["masterpiece, a castle", "lowres", "flux.safetensors", 77, 30, 6.5, "dpmpp_2m"],
    );
});

test("workflow-only: widgets_values_named and widget-backed inputs take precedence over positions", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "CheckpointLoaderSimple", widgets_values: ["m.safetensors"], outputs: [{ links: [1] }] },
            { id: 2, type: "CLIPTextEncode", widgets_values_named: { text: "named text" }, widgets_values: ["ignored"], outputs: [{ links: [2] }] },
            { id: 3, type: "CLIPTextEncode", widgets_values: ["neg"], outputs: [{ links: [3] }] },
            {
                id: 4, type: "KSampler",
                inputs: [
                    { name: "model", link: 1 }, { name: "positive", link: 2 }, { name: "negative", link: 3 },
                    { name: "seed", widget: { name: "seed" }, link: null },
                    { name: "steps", widget: { name: "steps" }, link: null },
                    { name: "cfg", widget: { name: "cfg" }, link: null },
                    { name: "sampler_name", widget: { name: "sampler_name" }, link: null },
                ],
                widgets_values: [5, "fixed", 12, 4, "euler", "normal", 1],
            },
        ],
        links: [[1, 1, 0, 4, 0, "MODEL"], [2, 2, 0, 4, 1, "CONDITIONING"], [3, 3, 0, 4, 2, "CONDITIONING"]],
    };
    const r = extractGenerationParams({ workflow });
    assert.deepEqual([r.positivePrompt, r.seed, r.steps, r.cfgScale, r.sampler], ["named text", 5, 12, 4, "euler"]);
});

test("workflow-only: bypassed nodes pass through, muted nodes are ignored", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "CheckpointLoaderSimple", widgets_values: ["m.safetensors"], outputs: [{ links: [1] }] },
            { id: 2, type: "LoraLoader", mode: 4, inputs: [{ name: "model", type: "MODEL", link: 1 }], outputs: [{ type: "MODEL", links: [2] }] },
            { id: 3, type: "CLIPTextEncode", widgets_values: ["pos"], outputs: [{ links: [3] }] },
            { id: 4, type: "CLIPTextEncode", widgets_values: ["neg"], outputs: [{ links: [4] }] },
            { id: 5, type: "KSampler", mode: 2, inputs: [{ name: "model", link: 2 }, { name: "positive", link: 3 }, { name: "negative", link: 4 }], widgets_values: [9, "fixed", 99, 9, "ddim"] },
            { id: 6, type: "KSampler", inputs: [{ name: "model", link: 2 }, { name: "positive", link: 3 }, { name: "negative", link: 4 }], widgets_values: [1, "fixed", 20, 7, "euler"] },
        ],
        links: [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 6, 0, "MODEL"], [3, 3, 0, 6, 1, "CONDITIONING"], [4, 4, 0, 6, 2, "CONDITIONING"]],
    };
    const r = extractGenerationParams({ workflow });
    assert.deepEqual([r.checkpoint, r.positivePrompt, r.steps], ["m.safetensors", "pos", 20]);
});

test("missing or empty metadata yields placeholders", () => {
    const r = extractGenerationParams({});
    assert.equal(r.positivePrompt, "No prompt found");
    assert.equal(r.negativePrompt, "No negative prompt found");
    assert.equal(r.checkpoint, "Unknown");
    assert.equal(extractGenerationParams(null).seed, "Unknown");
});


test("Focused API metadata composes linked positive text and preserves negative role", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManagerFocused", inputs: { text: "a fox", prepend_text: ["5", 0], append_text: "8k" } },
        3: { class_type: "PromptManagerFocused", inputs: { text: "blurry", prepend_text: "avoid" } },
        4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "model.safetensors" } },
        5: { class_type: "PrimitiveString", inputs: { value: "lora trigger" } },
    };
    const result = extractGenerationParams({ prompt });
    assert.equal(result.positivePrompt, "lora trigger a fox 8k");
    assert.equal(result.negativePrompt, "avoid blurry");
});

test("Focused workflow-only metadata recovers serialized fields and connected composition", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "KSampler", inputs: [{ name: "positive", link: 1 }, { name: "negative", link: 2 }, { name: "model", link: 3 }] },
            { id: 2, type: "PromptManagerFocused", widgets_values: ["a fox", "animals", "fox", "ignored search", "stale widget", "8k"], inputs: [{ name: "prepend_text", link: 4 }] },
            { id: 3, type: "PromptManagerFocused", widgets_values: ["blurry", "negative", "bad", "ignored search", "avoid", "watermark"] },
            { id: 4, type: "UNETLoader", widgets_values: ["model.safetensors", "default"] },
            { id: 5, type: "CLIPTextEncode", widgets_values: ["lora trigger"] },
        ],
        links: [
            [1, 2, 0, 1, 0, "CONDITIONING"], [2, 3, 0, 1, 1, "CONDITIONING"],
            [3, 4, 0, 1, 2, "MODEL"], [4, 5, 0, 2, 0, "STRING"],
        ],
    };
    const result = extractGenerationParams({ workflow });
    assert.equal(result.positivePrompt, "lora trigger a fox 8k");
    assert.equal(result.negativePrompt, "avoid blurry watermark");
    assert.equal(result.checkpoint, "model.safetensors");
});

test("Split API metadata composes linked positive text and preserves negative role", () => {
    const prompt = {
        1: { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["4", 0] } },
        2: { class_type: "PromptManagerSplit", inputs: { text: "a fox", prepend_text: ["5", 0], append_text: "8k" } },
        3: { class_type: "PromptManagerSplit", inputs: { text: "blurry", prepend_text: "avoid" } },
        4: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "model.safetensors" } },
        5: { class_type: "PrimitiveString", inputs: { value: "lora trigger" } },
    };
    const result = extractGenerationParams({ prompt });
    assert.equal(result.positivePrompt, "lora trigger a fox 8k");
    assert.equal(result.negativePrompt, "avoid blurry");
});

test("Split workflow-only metadata recovers serialized fields and connected composition", () => {
    const workflow = {
        nodes: [
            { id: 1, type: "KSampler", inputs: [{ name: "positive", link: 1 }, { name: "negative", link: 2 }, { name: "model", link: 3 }] },
            { id: 2, type: "PromptManagerSplit", widgets_values: ["a fox", "animals", "fox", "ignored search", "stale widget", "8k"], inputs: [{ name: "prepend_text", link: 4 }] },
            { id: 3, type: "PromptManagerSplit", widgets_values: ["blurry", "negative", "bad", "ignored search", "avoid", "watermark"] },
            { id: 4, type: "UNETLoader", widgets_values: ["model.safetensors", "default"] },
            { id: 5, type: "CLIPTextEncode", widgets_values: ["lora trigger"] },
        ],
        links: [
            [1, 2, 0, 1, 0, "CONDITIONING"], [2, 3, 0, 1, 1, "CONDITIONING"],
            [3, 4, 0, 1, 2, "MODEL"], [4, 5, 0, 2, 0, "STRING"],
        ],
    };
    const result = extractGenerationParams({ workflow });
    assert.equal(result.positivePrompt, "lora trigger a fox 8k");
    assert.equal(result.negativePrompt, "avoid blurry watermark");
    assert.equal(result.checkpoint, "model.safetensors");
});
