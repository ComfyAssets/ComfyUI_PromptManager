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
    from tests.local_resolver_cases import EXECUTION_KEY, SPACES, WORDS, _Builder
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
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


def selection_cases():
    """Named record sets for every Task 9 path, each a ``read_inspection_file`` dict."""
    cases = {}
    for group in (_samplers(), _previews(), _detailers()):
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
