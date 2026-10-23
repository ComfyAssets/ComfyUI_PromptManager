"""Candidate-selection record sets for the local resolver parity test (PR L3 Task 9).

``local_resolver_cases`` covers one sampler's tracing. These cases reach the
rest of ``_resolve``: several sampler candidates and the identical-branch rule
(#270), stages that only reach previews, detailer stages and their upstream
samplers (multistage), and A1111 ``parameters`` with every footer shape. The
server's ``inspect_prompt_evidence`` is the oracle; nothing here states an
expected answer. All content is synthetic.
"""

import json
import random

try:
    from tests import passthrough_graphs as shapes
    from tests.local_resolver_cases import EXECUTION_KEY, SPACES, WORDS, _Builder
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import passthrough_graphs as shapes
    from local_resolver_cases import EXECUTION_KEY, SPACES, WORDS, _Builder


def _encode(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["4", 1]}}


def _sampler(positive, negative, latent=None, name="KSampler"):
    inputs = {"seed": 1, "model": ["4", 0], "positive": positive}
    if negative is not None:
        inputs["negative"] = negative
    inputs["latent_image"] = latent or ["5", 0]
    return {"class_type": name, "inputs": inputs}


def _decode(sampler_id):
    return {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0]}}


def _save(image, name="SaveImage"):
    return {"class_type": name, "inputs": {"images": image}}


def _detailer(image, positive, negative, name="FaceDetailer", **extra):
    inputs = {"image": image, "model": ["4", 0], "positive": positive}
    inputs["negative"] = negative
    inputs.update(extra)
    return {"class_type": name, "inputs": inputs}


def _base(**nodes):
    graph = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
        "6": _encode("a fox"),
        "7": _encode("blurry"),
        "8": _encode("a cat"),
    }
    graph.update(nodes)
    return graph


def _samplers():
    """Several unanchored samplers: identical, differing and mixed branches."""
    same = _sampler(["6", 0], ["7", 0])
    cases = {
        "two-identical": _base(**{"3": same, "9": same}),
        "two-differ": _base(**{"3": same, "9": _sampler(["8", 0], ["7", 0])}),
        "two-negative-differs": _base(**{"3": same, "9": _sampler(["6", 0], ["8", 0])}),
        "identical-without-negative": _base(
            **{"3": _sampler(["6", 0], None), "9": _sampler(["6", 0], None)}
        ),
        "one-unresolved": _base(**{"3": same, "9": _sampler(["missing", 0], ["7", 0])}),
        "both-unresolved": _base(
            **{"3": _sampler(["x", 0], ["7", 0]), "9": _sampler(["y", 0], ["7", 0])}
        ),
        "three-identical-one-advanced": _base(
            **{
                "3": same,
                "9": same,
                "10": _sampler(["6", 0], ["7", 0], name="KSamplerAdvanced"),
            }
        ),
        # Equal texts, different recipes: a PromptManager pair against an encoder.
        "same-text-other-recipe": _base(
            **{
                "3": same,
                "9": _sampler(["11", 0], ["7", 0]),
                "11": {
                    "class_type": "PromptManager",
                    "inputs": {
                        "text": "fox",
                        "prepend_text": "a",
                        "append_text": "",
                        "clip": ["4", 1],
                    },
                },
            }
        ),
        "multistage-latent": _base(
            **{"3": same, "9": _sampler(["6", 0], ["7", 0], latent=["3", 0])}
        ),
    }
    selector = {
        "class_type": "easy ifElse",
        "inputs": {"boolean": ["4", 0], "on_true": ["6", 0], "on_false": ["8", 0]},
    }
    cases["selector-and-plain"] = _base(
        **{"3": same, "9": _sampler(["12", 0], ["7", 0]), "12": selector}
    )
    return cases


def _previews():
    """Stages whose outputs reach only previews are not the saved file's."""
    same, other = _sampler(["6", 0], ["7", 0]), _sampler(["8", 0], ["7", 0])
    return {
        "preview-dropped": _base(
            **{
                "3": same,
                "9": other,
                "13": _decode("3"),
                "14": _decode("9"),
                "15": _save(["13", 0]),
                "16": _save(["14", 0], "PreviewImage"),
            }
        ),
        "all-previews-kept": _base(
            **{
                "3": same,
                "9": other,
                "13": _decode("3"),
                "14": _decode("9"),
                "15": _save(["13", 0], "PreviewImage"),
                "16": _save(["14", 0], "Image Comparer (rgthree)"),
            }
        ),
        "preview-and-file": _base(
            **{
                "3": same,
                "9": other,
                "13": _decode("3"),
                "14": _decode("9"),
                "15": _save(["13", 0]),
                "16": _save(["14", 0], "PreviewImage"),
                "17": _save(["14", 0]),
            }
        ),
        "unconsumed-stage-kept": _base(
            **{
                "3": same,
                "9": other,
                "14": _decode("9"),
                "16": _save(["14", 0], "PreviewImage"),
            }
        ),
        # A list or object class_type is never a preview class (#286).
        "preview-unhashable-class": _base(
            **{
                "3": same,
                "9": other,
                "14": _decode("9"),
                "16": {"class_type": ["PreviewImage"], "inputs": {"images": ["14", 0]}},
                "17": {"class_type": {"a": 1}, "inputs": {"images": ["3", 0]}},
            }
        ),
        "preview-through-bad-node": _base(
            **{
                "3": same,
                "9": other,
                "13": _decode("3"),
                "15": _save(["13", 0]),
                "16": _save(["9", 0], "PreviewImage"),
                "18": "not a node",
                "19": {"class_type": "Note", "inputs": {"x": ["18", 0]}},
            }
        ),
    }


def _detailers():
    """Detailer stages judged with the samplers in their image ancestry."""
    same = _sampler(["6", 0], ["7", 0])
    cases = {
        "detailer-same-prompt": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-differs": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["8", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-and-own-output": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["8", 0], ["7", 0]),
                "12": _save(["11", 0]),
                "13": _save(["10", 0]),
            }
        ),
        "detailer-image-literal": _base(
            **{
                "11": _detailer("not a link", ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-cycle": _base(
            **{
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "10": {"class_type": "ImageScale", "inputs": {"image": ["11", 0]}},
                "12": _save(["11", 0]),
            }
        ),
        "detailer-chain": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "21": _detailer(
                    ["11", 0], ["8", 0], ["7", 0], name="UltimateSDUpscale"
                ),
                "12": _save(["21", 0]),
            }
        ),
        "detailer-chain-same": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "21": _detailer(["11", 0], ["6", 0], ["7", 0], name="DetailerForEach"),
                "12": _save(["21", 0]),
            }
        ),
        "detailer-two-upstream": _base(
            **{
                "3": same,
                "9": _sampler(["8", 0], ["7", 0]),
                "10": {
                    "class_type": "ImageBlend",
                    "inputs": {"image1": ["13", 0], "image2": ["14", 0]},
                },
                "13": _decode("3"),
                "14": _decode("9"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-unsupported-stage": _base(
            **{
                "10": {
                    "class_type": "MysterySampler",
                    "inputs": {"positive": ["6", 0], "model": ["4", 0]},
                },
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-upstream-unresolved": _base(
            **{
                "3": _sampler(["gone", 0], ["7", 0]),
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-upstream-selector": _base(
            **{
                "3": _sampler(["15", 0], ["7", 0]),
                "15": {
                    "class_type": "easy ifElse",
                    "inputs": {
                        "boolean": ["4", 0],
                        "on_true": ["6", 0],
                        "on_false": ["8", 0],
                    },
                },
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-wildcard": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(["10", 0], ["6", 0], ["7", 0], wildcard="[x|y]"),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-no-upscaled-image": _base(
            **{
                "3": same,
                "10": _decode("3"),
                "11": _detailer(
                    ["10", 0], ["6", 0], ["7", 0], name="UltimateSDUpscaleNoUpscale"
                ),
                "12": _save(["11", 0]),
            }
        ),
        "detailer-no-sampler-upstream": _base(
            **{
                "10": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                "11": _detailer(["10", 0], ["6", 0], ["7", 0]),
                "12": _save(["11", 0]),
            }
        ),
    }
    deep = {"3": _sampler(["6", 0], ["7", 0]), "d0": _decode("3")}
    for index in range(1, 70):
        deep[f"d{index}"] = {
            "class_type": "ImageScale",
            "inputs": {"image": [f"d{index - 1}", 0]},
        }
    deep["11"] = _detailer(["d69", 0], ["6", 0], ["7", 0])
    deep["12"] = _save(["11", 0])
    cases["detailer-deep-ancestry"] = _base(**deep)
    # More nested detailer stages than the depth bound.
    stacked = {"3": _sampler(["6", 0], ["7", 0]), "s0": _decode("3")}
    previous = "s0"
    for index in range(1, 67):
        stacked[f"s{index}"] = _detailer([previous, 0], ["6", 0], ["7", 0])
        previous = f"s{index}"
    stacked["12"] = _save([previous, 0])
    cases["detailer-stacked-past-depth"] = _base(**stacked)
    # Each detailer walks a wide shared image ancestry: the total visit budget
    # runs out across stages before any candidate is read.
    wide = {
        f"l{index}": {"class_type": "LoadImage", "inputs": {}} for index in range(1500)
    }
    wide["hub"] = {
        "class_type": "ImageBatchMany",
        "inputs": {f"i{index}": [f"l{index}", 0] for index in range(1500)},
    }
    for index in range(15):
        wide[f"w{index}"] = _detailer(["hub", 0], ["6", 0], ["7", 0])
        wide[f"o{index}"] = _save([f"w{index}", 0])
    cases["detailers-exhaust-total-visits"] = _base(**wide)
    cases["detailer-mutual-cycle"] = _base(
        **{
            "11": _detailer(["21", 0], ["6", 0], ["7", 0]),
            "21": _detailer(["11", 0], ["6", 0], ["7", 0]),
            "12": _save(["21", 0]),
        }
    )
    # The sampler's output reaches a preview through more nodes than one
    # detailer's allowance: the walk runs out, so the sampler is not absorbed.
    long = {"3": _sampler(["6", 0], ["7", 0]), "10": _decode("3")}
    previous = "10"
    for index in range(2100):
        long[f"c{index}"] = {
            "class_type": "ImageScale",
            "inputs": {"image": [previous, 0]},
        }
        previous = f"c{index}"
    long["p"] = _save([previous, 0], "PreviewImage")
    long["11"] = _detailer(["10", 0], ["6", 0], ["7", 0])
    long["12"] = _save(["11", 0])
    cases["detailer-output-walk-exhausted"] = _base(**long)
    # Every detailer's ancestry walk has its own allowance: the first one is
    # wide, and the second still absorbs the first.
    chained = {"3": _sampler(["6", 0], ["7", 0]), "10": _decode("3")}
    chained["h1"] = {
        "class_type": "ImageBatchMany",
        "inputs": {
            "i": ["10", 0],
            **{f"i{index}": [f"a{index}", 0] for index in range(1500)},
        },
    }
    chained["h2"] = {
        "class_type": "ImageBatchMany",
        "inputs": {
            "i": ["11", 0],
            **{f"i{index}": [f"b{index}", 0] for index in range(600)},
        },
    }
    for index in range(1500):
        chained[f"a{index}"] = {"class_type": "LoadImage", "inputs": {}}
    for index in range(600):
        chained[f"b{index}"] = {"class_type": "LoadImage", "inputs": {}}
    chained["11"] = _detailer(["h1", 0], ["6", 0], ["7", 0])
    chained["21"] = _detailer(["h2", 0], ["6", 0], ["7", 0])
    chained["12"] = _save(["21", 0])
    cases["detailers-each-reset"] = _base(**chained)
    return cases


_FOOTER = "Steps: 20, Sampler: euler, CFG scale: 7"


def _parameters():
    """A1111 ``parameters`` texts: every footer shape and delimiter rule."""
    texts = {
        "basic": f"a fox\nNegative prompt: blur\n{_FOOTER}",
        "no-negative": f"a fox\n{_FOOTER}",
        "two-delimiters": f"a\nNegative prompt: b\nNegative prompt: c\n{_FOOTER}",
        "leading-delimiter": f"Negative prompt: blur\n{_FOOTER}",
        "blank-positive": f" \x85\nNegative prompt: blur\n{_FOOTER}",
        "footer-not-last": f"a fox\n{_FOOTER}\nmore text",
        "two-footers": f"a\n{_FOOTER}\n{_FOOTER}",
        "bare-footer": "a fox\nNegative prompt: blur\nSteps: 20",
        "bare-and-full": f"a fox\nSteps: 5\n{_FOOTER}",
        "two-bare": "a\nSteps: 1\nSteps: 2",
        "template": f"a fox\n{_FOOTER}\nTemplate: {{a|b}}",
        "both-templates": f"a\n{_FOOTER}\nTemplate: x\nNegative Template: y",
        "templates-reversed": f"a\n{_FOOTER}\nNegative Template: y\nTemplate: x",
        "two-templates": f"a\n{_FOOTER}\nTemplate: x\nTemplate: y",
        "template-multiline": f"a\n{_FOOTER}\nTemplate: x\nmore",
        "crlf": f"a fox\r\nNegative prompt: blur\r\n{_FOOTER}\r\n",
        "cr-only": f"a fox\rNegative prompt: blur\r{_FOOTER}",
        "cr-template": f"a\n{_FOOTER}\rTemplate: x",
        "line-separator": f"a fox {_FOOTER}",
        "next-line": f"a fox\x85{_FOOTER}",
        "spaced-steps": "a fox\nSteps:\n 20 \n, x",
        "steps-newline-bare": "a fox\nSteps:\n20\n",
        "arabic-digits": "a fox\nSteps: ٢٠, Sampler: euler",
        "fullwidth-digits": "a fox\nSteps: ２, x",
        "no-digits": "a fox\nSteps: , x",
        "trailing-spaces": f"a fox  \nNegative prompt:   blur  \n{_FOOTER}   \n  \n",
        "empty-negative": f"a fox\nNegative prompt:\n{_FOOTER}",
        "astral-within-limit": "\U0001f600" * 60_000 + f"\n{_FOOTER}",
        "over-limit": "x" * 100_001,
        "at-limit": "x" * (100_000 - len(_FOOTER) - 1) + "\n" + _FOOTER,
        "empty": "",
        "only-footer": _FOOTER,
        "lone-surrogate": f"\ud800 fox\nNegative prompt: \udfff\n{_FOOTER}",
    }
    cases = {f"parameters-{name}": {"parameters": text} for name, text in texts.items()}
    cases["parameters-with-execution"] = {
        "parameters": texts["basic"],
        EXECUTION_KEY: json.dumps({"version": 1, "nodes": {}}),
    }
    return cases


def _passthrough():
    """Chains joined through lineage nodes, and the floor for added conditioning."""
    cases = {
        "hires-fix": shapes.hires_fix(),
        "hires-fix-differs": shapes.hires_fix(second_positive=shapes.OTHER),
        "hires-fix-unlisted": shapes.hires_fix(filter_class="MysteryImageEffect"),
        "hires-fix-generated": shapes.generated_text_chain(),
        "flux-three-stage": shapes.flux_three_stage(),
        "flux-concat": shapes.flux_three_stage(composite="ConditioningConcat"),
        "flux-average": shapes.flux_three_stage(composite="ConditioningAverage"),
        "edit-batch": shapes.edit_batch(3),
    }
    graph = shapes.flux_three_stage(combined=(("37", 0), ("31", 0)))
    graph["37"] = shapes.encode(shapes.OTHER)
    cases["flux-without-base"] = graph
    graph = shapes.flux_three_stage()
    graph["38"] = shapes.encode("watermark")
    graph["60"]["inputs"]["negative"] = ["38", 0]
    cases["flux-other-negative"] = graph
    graph = shapes.hires_fix()
    graph["20"]["inputs"]["latent_image"] = ["10", 0]
    graph["10"]["inputs"]["latent_image"] = ["20", 0]
    cases["hires-fix-cycle"] = graph
    graph = shapes.hires_fix()
    graph["17"]["inputs"]["pixels"] = ["18", 0]
    graph["18"] = {"class_type": "Reroute", "inputs": {"value": ["16", 0]}}
    cases["hires-fix-reroute"] = graph
    graph = shapes.hires_fix()
    graph["24"] = {"class_type": "SaveImage", "inputs": {"images": ["11", 0]}}
    cases["hires-fix-both-saved"] = graph
    graph = shapes.hires_fix()
    graph["20"]["inputs"]["latent_image"] = ["99", 0]
    cases["hires-fix-missing-latent"] = graph
    graph = shapes.flux_three_stage()
    graph["38"] = {"class_type": "MysteryNegative", "inputs": {"seed": 1}}
    graph["60"]["inputs"]["negative"] = ["38", 0]
    cases["flux-negative-unsupported"] = graph
    graph = shapes.flux_three_stage()
    graph["39"] = {"class_type": "Reroute", "inputs": {"input": ["35", 0]}}
    graph["60"]["inputs"]["positive"] = ["39", 0]
    cases["flux-reroute-input"] = graph
    graph = shapes.flux_three_stage()
    graph["39"] = {
        "class_type": "CLIPTextEncodeSDXL",
        "inputs": {"text_g": shapes.POSITIVE, "text_l": shapes.STYLE},
    }
    graph["60"]["inputs"]["positive"] = ["39", 0]
    cases["flux-encoder-parts"] = graph
    cases.update(_passthrough_edges())
    return cases


def _passthrough_edges():
    """Lineage and floor edges: branching refiners, other slots, cycles, guiders."""
    cases = {}
    graph = shapes.hires_fix()
    graph["30"] = shapes.ksampler(["2", 0], ["3", 0], ["17", 0], denoise=0.4)
    graph["31"] = {"class_type": "SaveImage", "inputs": {"images": ["30", 0]}}
    cases["hires-fix-two-refiners"] = graph
    graph = shapes.hires_fix()
    graph["10"]["class_type"] = "SamplerCustom"
    graph["11"]["inputs"]["samples"] = ["10", 1]
    cases["hires-fix-denoised-slot"] = graph
    graph = shapes.hires_fix()
    graph["30"] = shapes.ksampler(["2", 0], ["3", 0], ["4", 0])
    graph["31"] = {"class_type": "VAEDecode", "inputs": {"samples": ["30", 0]}}
    graph["32"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["31", 0]}}
    graph["30"]["inputs"]["latent_image"] = ["32", 0]
    cases["hires-fix-self-latent"] = graph
    graph = shapes.hires_fix()
    graph["30"] = shapes.ksampler(["2", 0], ["3", 0], ["32", 0])
    graph["31"] = {"class_type": "VAEDecode", "inputs": {"samples": ["32", 0]}}
    graph["32"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["31", 0]}}
    graph["33"] = {"class_type": "SaveImage", "inputs": {"images": ["30", 0]}}
    cases["hires-fix-lineage-cycle"] = graph
    graph = shapes.flux_three_stage()
    graph["39"] = shapes.encode(shapes.OTHER)
    graph["40"]["inputs"]["positive"] = ["39", 0]
    cases["flux-base-stages-differ"] = graph
    graph = shapes.flux_three_stage()
    del graph["60"]["inputs"]["negative"]
    cases["flux-no-last-negative"] = graph
    graph = shapes.flux_three_stage()
    graph["39"] = {"class_type": "MysteryPrompt", "inputs": {"seed": 1}}
    graph["40"]["inputs"]["positive"] = ["39", 0]
    graph["50"]["inputs"]["positive"] = ["39", 0]
    cases["flux-unreadable-base"] = graph
    graph = shapes.flux_three_stage()
    graph["39"] = {
        "class_type": "MysteryUpscaler",
        "inputs": {"image": ["51", 0], "model": ["1", 0], "positive": ["34", 0]},
    }
    graph["52"]["inputs"]["images"] = ["39", 0]
    cases["flux-unsupported-ancestry"] = graph
    graph = shapes.flux_three_stage()
    graph["60"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["62", 0],
            "guider": ["63", 0],
            "sampler": ["64", 0],
            "sigmas": ["65", 0],
            "latent_image": ["66", 0],
        },
    }
    graph["62"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}}
    graph["63"] = {
        "class_type": "CFGGuider",
        "inputs": {"model": ["1", 0], "positive": ["35", 0], "negative": ["33", 0]},
    }
    graph["64"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    graph["65"] = {"class_type": "BasicScheduler", "inputs": {"steps": 4}}
    graph["66"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["52", 0]}}
    graph["67"] = {"class_type": "VAEDecode", "inputs": {"samples": ["60", 0]}}
    graph["61"]["inputs"]["images"] = ["67", 0]
    cases["flux-custom-advanced-last"] = graph
    return {f"edge-{name}": graph for name, graph in cases.items()}


def selection_cases():
    """Named record sets for every Task 9 path, each a ``read_inspection_file`` dict."""
    cases = {}
    for group in (_samplers(), _previews(), _detailers(), _passthrough()):
        for name, graph in group.items():
            cases[f"select-{name}"] = {"prompt": json.dumps(graph, ensure_ascii=False)}
    cases.update(_parameters())
    return cases


class _Selection(_Builder):
    """Random graphs with several samplers, outputs, previews and detailers."""

    def sampler(self):
        rng = self.rng
        positive = self.conditioning(0)
        negative = self.conditioning(0) if rng.random() < 0.85 else None
        name = rng.choice(["KSampler", "KSampler", "KSamplerAdvanced", "SamplerCustom"])
        node_id = self.new_id()
        self.nodes[node_id] = _sampler(positive, negative, name=name)
        return node_id

    def shared(self, ids):
        """A copy of an earlier sampler: an identical branch."""
        node_id = self.new_id()
        self.nodes[node_id] = json.loads(json.dumps(self.nodes[self.rng.choice(ids)]))
        return node_id

    def output(self, image):
        rng = self.rng
        choice = rng.randrange(5)
        if choice == 4:
            return
        name = ["SaveImage", "SaveImage", "PreviewImage", "PreviewAny"][choice]
        self.nodes[self.new_id()] = _save(image, name)

    def detailer(self, image):
        rng = self.rng
        node_id = self.new_id()
        positive = self.conditioning(1) if rng.random() < 0.5 else None
        name = rng.choice(list(_DETAILER_NAMES))
        node = _detailer(image, positive, self.conditioning(1), name=name)
        if positive is None:
            node["inputs"]["positive"] = self.reuse_positive()
        if name == "UltimateSDUpscaleNoUpscale":
            node["inputs"]["upscaled_image"] = node["inputs"].pop("image")
        if name in ("FaceDetailer", "DetailerForEach") and rng.random() < 0.15:
            node["inputs"]["wildcard"] = rng.choice(["", "x", ["4", 0]])
        self.nodes[node_id] = node
        return node_id

    def reuse_positive(self):
        samplers = [
            node
            for node in self.nodes.values()
            if node.get("class_type")
            in ("KSampler", "KSamplerAdvanced", "SamplerCustom")
        ]
        if samplers:
            return self.rng.choice(samplers)["inputs"]["positive"]
        return self.conditioning(1)

    def build(self):
        rng = self.rng
        ids = [self.sampler()]
        for _ in range(rng.randrange(3)):
            ids.append(self.shared(ids) if rng.random() < 0.4 else self.sampler())
        for sampler_id in list(ids):
            decoded = self.new_id()
            self.nodes[decoded] = _decode(sampler_id)
            image = [decoded, 0]
            for _ in range(rng.randrange(3)):
                if rng.random() < 0.5:
                    image = [self.detailer(image), 0]
                else:
                    self.output(image)
            self.output(image)
            if rng.random() < 0.1:
                self.nodes[sampler_id]["inputs"]["latent_image"] = [rng.choice(ids), 0]
        graph = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
        }
        graph.update(self.nodes)
        return graph


_DETAILER_NAMES = (
    "FaceDetailer",
    "DetailerForEach",
    "UltimateSDUpscale",
    "UltimateSDUpscaleNoUpscale",
)
_PARAMETER_LINES = [
    "Negative prompt:",
    "Negative prompt: blur",
    "Steps: 20, Sampler: euler",
    "Steps: 7",
    "Steps: x",
    "Template: t",
    "Negative Template: n",
    "Steps:٣, a",
]


def _parameter_text(rng):
    parts = []
    for _ in range(rng.randint(1, 5)):
        if rng.random() < 0.5:
            parts.append(rng.choice(_PARAMETER_LINES))
        else:
            parts.append(rng.choice(SPACES) + rng.choice(WORDS) + rng.choice(SPACES))
    joints = ["\n", "\n", "\r\n", "\r", " ", " "]
    return "".join(part + rng.choice(joints) for part in parts).rstrip(
        rng.choice(["", "\n", " "])
    )


def selection_fuzz_cases(seed, count):
    """Seeded several-sampler graphs (sometimes corrupted) and parameters texts."""
    rng = random.Random(seed)
    cases = {}
    for index in range(count):
        if rng.random() < 0.2:
            cases[f"select-fuzz-{seed}-{index}"] = {"parameters": _parameter_text(rng)}
            continue
        builder = _Selection(rng)
        graph = builder.build()
        if rng.random() < 0.2:
            builder.nodes = graph
            builder.corrupt()
        records = {"prompt": json.dumps(graph, ensure_ascii=rng.random() < 0.5)}
        records.update(builder.records)
        cases[f"select-fuzz-{seed}-{index}"] = records
    return cases


_LINEAGE_HOPS = (
    ("VAEDecode", "samples"),
    ("VAEDecodeTiled", "samples"),
    ("Image Filter", "images"),
    ("ImageUpscaleWithModel", "image"),
    ("ImageScale", "image"),
    ("ImageScaleBy", "image"),
    ("VAEEncode", "pixels"),
    ("LatentUpscaleBy", "samples"),
    ("Reroute", "value"),
    ("MysteryImageEffect", "image"),
)


def passthrough_fuzz_cases(seed, count):
    """Seeded sampler chains joined by random lineage (and unlisted) hops.

    Each later stage reads the earlier one's output through zero to four hops,
    with a shared, a different, an added-to (combine, concat, average) or a
    run-time positive, and a shared or different negative, under a saver or a
    preview. The server is the oracle; nothing states the expected answer.
    """
    rng = random.Random(seed)
    cases = {}
    for index in range(count):
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
            "2": shapes.prompt_manager(rng.choice([shapes.POSITIVE, "fox"])),
            "3": shapes.encode(shapes.NEGATIVE),
            "4": {"class_type": "EmptyLatentBatch", "inputs": {"batch_size": 2}},
            "5": shapes.encode(shapes.STYLE),
            "6": shapes.encode(shapes.OTHER),
        }
        previous, next_id = ["4", 0], 10
        for stage in range(rng.randint(2, 3)):
            positive = ["2", 0]
            choice = rng.randrange(8)
            if stage and choice == 1:
                positive = ["6", 0]
            elif stage and choice in (2, 3, 4):
                name = (
                    "ConditioningCombine",
                    "ConditioningConcat",
                    "ConditioningAverage",
                )[choice - 2]
                names = shapes._COMPOSITE_INPUTS[name]
                parts = [["2", 0], rng.choice([["5", 0], ["6", 0]])]
                rng.shuffle(parts)
                graph[str(next_id)] = {
                    "class_type": name,
                    "inputs": dict(zip(names, parts)),
                }
                positive, next_id = [str(next_id), 0], next_id + 1
            elif stage and choice == 5:
                graph[str(next_id)] = {
                    "class_type": "FluxGuidance",
                    "inputs": {"guidance": 3.5, "conditioning": ["2", 0]},
                }
                positive, next_id = [str(next_id), 0], next_id + 1
            negative = ["6", 0] if stage and rng.random() < 0.15 else ["3", 0]
            latent = previous
            if stage:
                image = previous
                for _ in range(rng.randrange(5)):
                    name, key = rng.choice(_LINEAGE_HOPS)
                    graph[str(next_id)] = {"class_type": name, "inputs": {key: image}}
                    image, next_id = [str(next_id), 0], next_id + 1
                latent = image
            sampler = shapes.ksampler(positive, negative, latent)
            if stage and rng.random() < 0.2:
                sampler = {
                    "class_type": "UltimateSDUpscale",
                    "inputs": {
                        "image": latent,
                        "model": ["1", 0],
                        "positive": positive,
                        "negative": negative,
                    },
                }
            graph[str(next_id)] = sampler
            previous, next_id = [str(next_id), 0], next_id + 1
            if rng.random() < 0.3:
                saver = rng.choice(["SaveImage", "PreviewImage"])
                graph[str(next_id)] = {
                    "class_type": saver,
                    "inputs": {"images": previous},
                }
                next_id += 1
        graph[str(next_id)] = {
            "class_type": "SaveImage",
            "inputs": {"images": previous},
        }
        cases[f"select-passthrough-fuzz-{seed}-{index}"] = {
            "prompt": json.dumps(graph, ensure_ascii=False)
        }
    return cases
