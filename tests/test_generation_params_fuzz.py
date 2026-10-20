"""Randomized differential test: the Python parser equals the browser parser.

Generates seeded random API graphs and UI workflows, from realistic to
malformed, and requires utils.generation_params to return exactly what
web/js/comfy-metadata.js extractGenerationParams returns, or to fail where it
throws. Runs only where Node is available. Set PM_PARAMS_FUZZ_CASES to run more.
"""

import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_generation_params_js_parity import (
        NODE,
        js_equal,
        run_oracle,
        run_python,
    )  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_generation_params_js_parity import (
        NODE,
        js_equal,
        run_oracle,
        run_python,
    )  # noqa: E402

CASE_COUNT = int(os.environ.get("PM_PARAMS_FUZZ_CASES", "3000"))
SEEDS = (20261020, 7, 424242)

NODE_IDS = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "9",
    "10",
    "12",
    "25",
    "26",
    "100",
    "1300:5",
    "1300:8",
    "x",
    "05",
    "4294967295",
    "-1",
    "constructor",
]
CLASS_TYPES = [
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustomAdvanced",
    "CFGGuider",
    "BasicGuider",
    "CLIPTextEncode",
    "PromptManager",
    "PromptManagerPair",
    "PromptManagerText",
    "StringConcatenate",
    "PrimitiveString",
    "ControlNetApplyAdvanced",
    "ConditioningCombine",
    "CheckpointLoaderSimple",
    "UNETLoader",
    "LoraLoader",
    "RandomNoise",
    "KSamplerSelect",
    "BasicScheduler",
    "Reroute",
    "MyKSamplerThing",
    "SaveImage",
    "FaceDetailer",
    "toString",
    "",
]
INPUT_KEYS = [
    "text",
    "positive",
    "negative",
    "model",
    "conditioning",
    "conditioning_1",
    "conditioning_to",
    "guider",
    "sigmas",
    "noise",
    "sampler",
    "seed",
    "noise_seed",
    "steps",
    "cfg",
    "sampler_name",
    "ckpt_name",
    "unet_name",
    "model_name",
    "prepend_text",
    "append_text",
    "negative_text",
    "delimiter",
    "string_a",
    "string_b",
    "value",
    "text_g",
    "prompt",
    "0",
    "10",
    "clip",
]
TEXTS = [
    "a cat",
    "  padded text  ",
    "",
    "   ",
    "﻿bom text ",
    "line one\nline two",
    "masterpiece",
    "masterpiece, a dog",
    "4k",
    "a dog 4k",
    "ünïcödé ✓",
    ", ",
]
WORKFLOW_TYPES = [
    "KSampler",
    "KSamplerAdvanced",
    "CLIPTextEncode",
    "PromptManager",
    "PromptManagerPair",
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "UNETLoader",
    "MyUnetLoader",
    "Reroute",
    "GetNode",
    "SetNode",
    "Note",
    "toString",
    "valueOf",
]


class Generator:
    def __init__(self, seed):
        self.random = random.Random(seed)

    def pick(self, items):
        return self.random.choice(items)

    def chance(self, probability):
        return self.random.random() < probability

    def number(self):
        return self.pick(
            [
                0,
                1,
                2,
                3,
                5,
                20,
                7,
                1.0,
                1.5,
                6.5,
                0.45,
                1.5e-7,
                2.5e21,
                -3,
                9007199254740991,
            ]
        )

    def link(self):
        return [
            self.pick(NODE_IDS + [3, 12, 5.0]),
            self.pick([0, 1, 2, 3, 1.0, 0, 0, 0, -1]),
        ]

    def value(self, key):
        roll = self.random.random()
        if (
            key
            in (
                "positive",
                "negative",
                "model",
                "conditioning",
                "conditioning_1",
                "conditioning_to",
                "guider",
                "sigmas",
                "noise",
                "sampler",
                "clip",
            )
            and roll < 0.8
        ):
            return self.link()
        if roll < 0.3:
            return self.link()
        if roll < 0.6:
            return self.pick(TEXTS)
        if roll < 0.85:
            return self.number()
        return self.pick(
            [True, False, None, [1, 2, 3], {"nested": "x"}, [], "randomize"]
        )

    def inputs(self):
        if self.chance(0.04):
            return self.pick([[], ["text"], "text", 3, None, True])
        keys = self.random.sample(INPUT_KEYS, self.random.randint(0, 9))
        return {key: self.value(key) for key in keys}

    def node(self):
        if self.chance(0.03):
            return self.pick([None, "node", 5, [1, 2]])
        node = {"class_type": self.pick(CLASS_TYPES), "inputs": self.inputs()}
        if self.chance(0.05):
            del node["class_type"]
        return node

    def graph(self):
        ids = self.random.sample(NODE_IDS, self.random.randint(1, 12))
        if self.chance(0.03):
            return [self.node() for _ in ids]
        return {node_id: self.node() for node_id in ids}

    def workflow_node(self, node_id):
        if self.chance(0.02):
            return self.pick([None, 3, "node"])
        node = {
            "id": node_id,
            "type": self.pick(WORKFLOW_TYPES),
            "mode": self.pick([0, 0, 0, 2, 4]),
        }
        inputs = []
        for _ in range(self.random.randint(0, 4)):
            item = {
                "name": self.pick(INPUT_KEYS),
                "link": self.pick([None, 1, 2, 3, 4, 5, 6, 7, 8, "2"]),
                "type": self.pick(["CONDITIONING", "MODEL", "CLIP", "*", "STRING"]),
            }
            if self.chance(0.2):
                item["widget"] = {"name": self.pick(INPUT_KEYS)}
            inputs.append(item)
        node["inputs"] = (
            inputs if not self.chance(0.03) else self.pick([{"a": 1}, "abc", None])
        )
        node["widgets_values"] = [
            self.value(self.pick(INPUT_KEYS)) for _ in range(self.random.randint(0, 6))
        ]
        if self.chance(0.1):
            node["widgets_values"].insert(1, "randomize")
        if self.chance(0.05):
            node["widgets_values_named"] = {
                "text": self.pick(TEXTS),
                "seed": self.number(),
            }
        return node

    def workflow(self):
        if self.chance(0.03):
            return self.pick([[], "workflow", {"nodes": "x"}, {"links": []}])
        ids = self.random.sample(
            [1, 2, 3, 4, 5, 6, 7, 8, "9", 10.0], self.random.randint(1, 8)
        )
        links = []
        for link_id in range(1, self.random.randint(1, 9)):
            origin, slot = self.pick(ids), self.pick([0, 1, 2])
            if self.chance(0.5):
                links.append(
                    [
                        link_id,
                        origin,
                        slot,
                        self.pick(ids),
                        0,
                        self.pick(["CONDITIONING", "MODEL", "*", "STRING"]),
                    ]
                )
            else:
                links.append(
                    {
                        "id": link_id,
                        "origin_id": origin,
                        "origin_slot": slot,
                        "type": self.pick(["CONDITIONING", "MODEL", "*"]),
                    }
                )
        return {
            "nodes": [self.workflow_node(node_id) for node_id in ids],
            "links": links,
        }

    # --- coherent pipelines, then corrupted ------------------------------------

    def text_source(self, graph, ids, depth=0):
        """A node id producing text: an encoder, PromptManager, pair or a text chain."""
        node_id = ids.pop()
        kind = self.pick(
            ["encoder", "manager", "pair", "concat", "primitive"]
            if depth < 2
            else ["encoder", "manager"]
        )
        if kind == "encoder":
            text = (
                self.pick(TEXTS)
                if self.chance(0.7)
                else [self.text_source(graph, ids, depth + 1), 0]
            )
            graph[node_id] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": text, "clip": ["4", 1]},
            }
        elif kind == "manager":
            graph[node_id] = {
                "class_type": self.pick(["PromptManager", "PromptManagerText"]),
                "inputs": {
                    "text": self.pick(TEXTS),
                    "prepend_text": self.pick(TEXTS + ["masterpiece"]),
                    "append_text": self.pick(TEXTS + ["4k"]),
                    "clip": ["4", 1],
                },
            }
        elif kind == "pair":
            graph[node_id] = {
                "class_type": "PromptManagerPair",
                "inputs": {
                    "text": self.pick(TEXTS),
                    "negative_text": self.pick(TEXTS),
                    "prepend_text": "",
                    "append_text": "",
                },
            }
        elif kind == "concat":
            graph[node_id] = {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": (
                        [self.text_source(graph, ids, depth + 1), 0] if ids else "x"
                    ),
                    "string_b": self.pick(TEXTS),
                    "delimiter": self.pick([", ", " ", 3, "|"]),
                },
            }
        else:
            graph[node_id] = {
                "class_type": "PrimitiveString",
                "inputs": {"value": self.pick(TEXTS)},
            }
        return node_id

    def conditioning(self, graph, ids, role):
        source = self.text_source(graph, ids)
        slot = (
            self.pick([0, 1, 2, 3])
            if graph[source]["class_type"] == "PromptManagerPair"
            else 0
        )
        link = [source, slot]
        for _ in range(self.pick([0, 0, 1, 2, 3])):
            if not ids:
                break
            node_id = ids.pop()
            key = self.pick([role, "conditioning", "conditioning_to", "conditioning_1"])
            graph[node_id] = {
                "class_type": self.pick(
                    [
                        "ControlNetApplyAdvanced",
                        "ConditioningCombine",
                        "ConditioningSetArea",
                    ]
                ),
                "inputs": {key: link, "strength": 1.0},
            }
            link = [node_id, 0]
        return link

    def pipeline(self):
        ids = [str(i) for i in self.random.sample(range(1, 400), 120)] + [
            "1300:7",
            "x",
            "constructor",
        ]
        self.random.shuffle(ids)
        graph = {
            "4": {
                "class_type": self.pick(["CheckpointLoaderSimple", "UNETLoader"]),
                "inputs": {
                    self.pick(
                        ["ckpt_name", "unet_name", "gguf_name"]
                    ): "model.safetensors"
                },
            }
        }
        for _ in range(self.pick([1, 1, 2, 3])):
            positive, negative = self.conditioning(
                graph, ids, "positive"
            ), self.conditioning(graph, ids, "negative")
            settings = {
                "seed": self.number() if self.chance(0.8) else [ids.pop(), 0],
                "steps": self.number(),
                "cfg": self.number(),
                "sampler_name": self.pick(["euler", "lcm", "dpmpp_2m"]),
            }
            if settings["seed"] and isinstance(settings["seed"], list):
                graph[settings["seed"][0]] = {
                    "class_type": "Seed",
                    "inputs": {"seed": self.number(), "other": "x"},
                }
            if self.chance(0.25):
                guider = ids.pop()
                graph[guider] = {
                    "class_type": "CFGGuider",
                    "inputs": {
                        "model": ["4", 0],
                        "positive": positive,
                        "negative": negative,
                        "cfg": self.number(),
                    },
                }
                noise, chooser, sigmas = ids.pop(), ids.pop(), ids.pop()
                graph[noise] = {
                    "class_type": "RandomNoise",
                    "inputs": {"noise_seed": self.number()},
                }
                graph[chooser] = {
                    "class_type": "KSamplerSelect",
                    "inputs": {"sampler_name": "euler"},
                }
                graph[sigmas] = {
                    "class_type": "BasicScheduler",
                    "inputs": {"steps": self.number()},
                }
                graph[ids.pop()] = {
                    "class_type": "SamplerCustomAdvanced",
                    "inputs": {
                        "noise": [noise, 0],
                        "guider": [guider, 0],
                        "sampler": [chooser, 0],
                        "sigmas": [sigmas, 0],
                    },
                }
            else:
                graph[ids.pop()] = {
                    "class_type": self.pick(
                        ["KSampler", "KSamplerAdvanced", "FaceDetailer"]
                    ),
                    "inputs": {
                        "model": ["4", 0],
                        "positive": positive,
                        "negative": negative,
                        **settings,
                    },
                }
        for _ in range(self.pick([0, 0, 1, 3])):  # corruption
            node_id = self.pick(list(graph))
            node = graph[node_id]
            if (
                self.chance(0.5)
                and isinstance(node, dict)
                and isinstance(node.get("inputs"), dict)
                and node["inputs"]
            ):
                key = self.pick(list(graph[node_id]["inputs"]))
                graph[node_id]["inputs"][key] = self.value(key)
            else:
                graph[node_id] = self.node()
        return graph

    def pipeline_workflow(self):
        """A connected workflow: loader, encoders, Reroute or Set/Get hops, sampler."""
        nodes, links = [], []

        def link(origin, slot, kind):
            links.append(
                [len(links) + 1, origin, slot, 0, 0, kind]
                if self.chance(0.6)
                else {
                    "id": len(links) + 1,
                    "origin_id": origin,
                    "origin_slot": slot,
                    "type": kind,
                }
            )
            return len(links)

        nodes.append(
            {
                "id": 4,
                "type": self.pick(
                    ["CheckpointLoaderSimple", "UNETLoader", "MyUnetLoader"]
                ),
                "mode": 0,
                "inputs": [],
                "widgets_values": ["model.safetensors"],
            }
        )
        refs = []
        for index, role in enumerate(("positive", "negative")):
            encoder = 10 + index
            nodes.append(
                {
                    "id": encoder,
                    "type": self.pick(
                        ["CLIPTextEncode", "PromptManager", "PromptManagerText"]
                    ),
                    "mode": self.pick([0, 0, 0, 4]),
                    "inputs": [
                        {"name": "clip", "link": link(4, 1, "CLIP"), "type": "CLIP"}
                    ],
                    "widgets_values": [
                        self.pick(TEXTS),
                        "",
                        "",
                        "",
                        self.pick(TEXTS),
                        self.pick(TEXTS),
                    ],
                }
            )
            ref = link(encoder, 0, "CONDITIONING")
            hop = self.pick(["none", "none", "reroute", "setget"])
            if hop == "reroute":
                nodes.append(
                    {
                        "id": 20 + index,
                        "type": "Reroute",
                        "mode": 0,
                        "inputs": [{"name": "", "link": ref}],
                    }
                )
                ref = link(20 + index, 0, "*")
            elif hop == "setget":
                name = self.pick(["cond_" + role, "shared"])
                nodes.append(
                    {
                        "id": 30 + index,
                        "type": "SetNode",
                        "mode": 0,
                        "inputs": [{"name": "c", "link": ref}],
                        "widgets_values": [name],
                    }
                )
                nodes.append(
                    {
                        "id": 40 + index,
                        "type": "GetNode",
                        "mode": 0,
                        "inputs": [],
                        "widgets_values": [name],
                    }
                )
                ref = link(40 + index, 0, "CONDITIONING")
            refs.append(ref)
        widgets = [
            self.number(),
            self.pick(["randomize", "fixed", "x"]),
            self.number(),
            self.number(),
            "euler",
            "normal",
            1,
        ]
        nodes.append(
            {
                "id": 3,
                "type": self.pick(["KSampler", "KSamplerAdvanced"]),
                "mode": 0,
                "inputs": [
                    {"name": "model", "link": link(4, 0, "MODEL"), "type": "MODEL"},
                    {"name": "positive", "link": refs[0], "type": "CONDITIONING"},
                    {"name": "negative", "link": refs[1], "type": "CONDITIONING"},
                ],
                "widgets_values": widgets,
            }
        )
        if self.chance(0.3):
            nodes[self.random.randrange(len(nodes))] = self.workflow_node(
                self.pick([1, 2, 99])
            )
        return {"nodes": nodes, "links": links}

    def case(self):
        roll = self.random.random()
        structured = self.chance(0.7)
        prompt = (
            json.dumps(self.pipeline() if structured else self.graph())
            if roll < 0.75
            else None
        )
        workflow = (
            json.dumps(self.pipeline_workflow() if structured else self.workflow())
            if roll > 0.45
            else None
        )
        if self.chance(0.02):
            prompt = self.pick(['{"1": NaN}', "{", "null", "[]", '"text"', "12"])
        return {"prompt": prompt, "workflow": workflow}


@unittest.skipUnless(NODE, "Node is required to run the browser parser")
class RandomGraphs(unittest.TestCase):
    def test_random_graphs_match_the_browser_parser(self):
        for seed in SEEDS:
            generator = Generator(seed)
            cases = [generator.case() for _ in range(CASE_COUNT)]
            expected = run_oracle(cases)
            self.assertEqual(len(expected), len(cases))
            mismatches = []
            for index, (case, result) in enumerate(zip(cases, expected)):
                actual = run_python(case)
                if not js_equal(actual, result):
                    mismatches.append((index, case, actual, result))
            with self.subTest(seed=seed):
                outcomes = {
                    "ok": sum(r["ok"] for r in expected),
                    "throws": sum(not r["ok"] for r in expected),
                }
                self.assertEqual(
                    mismatches[:3],
                    [],
                    f"{len(mismatches)} of {len(cases)} differ; outcomes {outcomes}",
                )


if __name__ == "__main__":
    unittest.main()
