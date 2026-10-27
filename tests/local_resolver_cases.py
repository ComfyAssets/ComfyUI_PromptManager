"""Record sets for the local resolver parity test (#249 local Inspect, PR L3 Task 8).

Every case is a dict of record texts as ``read_inspection_file`` returns them
(``prompt``, ``workflow``, ``comfyassets_prompt_pairs``), so the server's
``inspect_prompt_evidence`` and the browser's ``resolveLocalEvidence`` read the
same bytes. ``golden_cases`` names one case per traced strategy, bound and
malformed shape; ``fuzz_cases`` builds coherent single-sampler graphs from the
same node vocabulary, then corrupts some of them. All content is synthetic.
``single_chain`` says, from the server's own sampler table, whether a record
set resolves without candidate selection (the Task 8 subset);
``local_resolver_selection_cases`` holds the candidate-selection cases.
"""

import argparse
import json
import random
from pathlib import Path

from utils.inspection_semantics import get_semantics
from utils.prompt_inspector import inspect_prompt_evidence
from utils.prompt_pair_resolver import INSPECTION_PROFILE

VECTORS_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "local-inspect"
    / "resolver-vectors.json"
)
# A case may name the inspected file (#381 saver anchoring); it is no record.
FILE_NAME = "__file_name"
# Committed record sets stay small; the large bound cases are built at test time.
MAX_VECTOR_CHARS = 20_000
COMMITTED_FUZZ = (0, 60)
COMMITTED_PASSTHROUGH = (0, 40)

SPACES = ["", " ", "\t", "\n", "\x1c", "\x85", "　", "﻿", "  "]
WORDS = ["fox", "snow", "café", "猫", "\U0001f600", "a,", "detailed fur", ""]


def _dump(graph, ensure_ascii=False):
    return json.dumps(graph, ensure_ascii=ensure_ascii)


def _encode(text, name="CLIPTextEncode"):
    return {"class_type": name, "inputs": {"text": text, "clip": ["4", 1]}}


def _graph(positive, negative, extra=None, sampler=None):
    graph = {
        "3": sampler
        or {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "model": ["4", 0],
                "positive": positive,
                "negative": negative,
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
    }
    graph.update(extra or {})
    return graph


def _pair(text="fox", negative="blur", prepend="", append=""):
    return {
        "class_type": "PromptManagerPair",
        "inputs": {
            "text": text,
            "negative_text": negative,
            "prepend_text": prepend,
            "append_text": append,
        },
    }


def _execution(node_id, **changes):
    record = {
        "text": "fox",
        "negative_text": "blur",
        "positive_text": "fox executed",
        "negative_effective_text": "blur",
        "prepend_text": "",
        "append_text": "",
    }
    record.update(changes)
    return json.dumps({"version": 1, "nodes": {node_id: {"records": [record]}}})


def _chain(length, start="6"):
    nodes = {start: _encode("deep")}
    previous = start
    for index in range(length):
        node_id = f"r{index}"
        nodes[node_id] = {"class_type": "Reroute", "inputs": {"value": [previous, 0]}}
        previous = node_id
    return nodes, previous


def _combine_tree(levels):
    """A full ConditioningCombine tree over equal encoders: many distinct visits."""
    nodes, layer = {}, []
    for index in range(2**levels):
        nodes[f"e{index}"] = _encode("same")
        layer.append(f"e{index}")
    level = 0
    while len(layer) > 1:
        nxt = []
        for index in range(0, len(layer), 2):
            node_id = f"c{level}_{index}"
            nodes[node_id] = {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": [layer[index], 0],
                    "conditioning_2": [layer[index + 1], 0],
                },
            }
            nxt.append(node_id)
        layer, level = nxt, level + 1
    return nodes, layer[0]


def golden_cases():
    """Named record sets, one per strategy, bound and malformed shape."""
    cases = {}

    def add(name, graph=None, prompt=None, **records):
        cases[name] = {"prompt": prompt if prompt is not None else _dump(graph)}
        cases[name].update(records)

    neg = ["7", 0]
    base = {"6": _encode("a fox"), "7": _encode("blurry")}
    add("encode", _graph(["6", 0], neg, base))
    add("empty-positive", _graph(["6", 0], neg, {**base, "6": _encode(" \x85")}))
    add("feff-positive", _graph(["6", 0], neg, {**base, "6": _encode("﻿")}))
    add("no-negative", _graph(["6", 0], None, base))
    add(
        "negative-missing-input",
        None,
        prompt=_dump(_graph(["6", 0], neg, base)).replace('"negative": ["7", 0], ', ""),
    )
    add(
        "float-slot",
        None,
        prompt=_dump(_graph(["6", 0], neg, base)).replace('["6", 0]', '["6", 0.0]'),
    )
    add(
        "bool-slot",
        None,
        prompt=_dump(_graph(["6", 0], neg, base)).replace('["6", 0]', '["6", true]'),
    )
    add("int-id", None, prompt=_dump(_graph([6, 0], neg, base)))
    add("dangling", _graph(["60", 0], neg, base))
    add("wrong-slot", _graph(["6", 3], neg, base))
    add(
        "cycle",
        _graph(
            ["8", 0],
            neg,
            {**base, "8": {"class_type": "Reroute", "inputs": {"value": ["8", 0]}}},
        ),
    )
    add(
        "reroute-ambiguous",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {
                    "class_type": "Reroute",
                    "inputs": {"value": ["6", 0], "in": ["6", 0]},
                },
            },
        ),
    )
    add(
        "duplicate-key",
        None,
        prompt=_dump(_graph(["6", 0], neg, base))[:-1]
        + ', "6": '
        + _dump(_encode("last wins"))
        + "}",
    )
    add(
        "nan-input",
        None,
        prompt=_dump(_graph(["6", 0], neg, base)).replace('"seed": 1', '"seed": NaN'),
    )
    add(
        "class-type-number",
        _graph(
            ["6", 0], neg, {**base, "6": {"class_type": 1.5, "inputs": {"text": "x"}}}
        ),
    )
    add(
        "class-type-missing",
        _graph(["6", 0], neg, {**base, "6": {"inputs": {"text": "x"}}}),
    )
    add(
        "inputs-list",
        _graph(
            ["6", 0],
            neg,
            {**base, "6": {"class_type": "CLIPTextEncode", "inputs": ["x"]}},
        ),
    )
    add("prompt-list", None, prompt="[1, 2]")
    add("prompt-empty", None, prompt="{}")
    add("prompt-bom", None, prompt="﻿" + _dump(_graph(["6", 0], neg, base)))
    add(
        "huge-int",
        None,
        prompt=_dump(_graph(["6", 0], neg, base)).replace(
            '"seed": 1', '"seed": ' + "9" * 4301
        ),
    )
    add("only-execution", None, prompt=None)
    cases["only-execution"] = {"comfyassets_prompt_pairs": _execution("8")}
    add(
        "no-sampler",
        {
            "1": _encode("x"),
            "2": {"class_type": "XSampler", "inputs": {"positive": ["1", 0]}},
            "9": {"class_type": "YGuider", "inputs": {"guider": ["1", 0]}},
        },
    )
    add("no-sampler-plain", {"1": _encode("x")})
    sdxl = {
        "class_type": "CLIPTextEncodeSDXL",
        "inputs": {"text_g": "a", "text_l": "a"},
    }
    add("sdxl-equal", _graph(["8", 0], neg, {**base, "8": sdxl}))
    add(
        "sdxl-one",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {
                    "class_type": "CLIPTextEncodeSDXL",
                    "inputs": {"text_g": "a", "text_l": ""},
                },
            },
        ),
    )
    add(
        "sdxl-differ",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {
                    "class_type": "CLIPTextEncodeSDXL",
                    "inputs": {"text_g": "a", "text_l": "b"},
                },
            },
        ),
    )
    for flag in (False, True, 1, ["9", 0]):
        smz = {
            "class_type": "smZ CLIPTextEncode",
            "inputs": {"text": "t", "text_g": "g", "text_l": "g", "with_SDXL": flag},
        }
        add(f"smz-{json.dumps(flag)}", _graph(["8", 0], neg, {**base, "8": smz}))
    add(
        "zeroed",
        _graph(
            ["6", 0],
            ["8", 0],
            {
                **base,
                "8": {
                    "class_type": "ConditioningZeroOut",
                    "inputs": {"conditioning": ["6", 0]},
                },
            },
        ),
    )
    add(
        "passthrough",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {
                    "class_type": "FluxGuidance",
                    "inputs": {"conditioning": ["6", 0], "guidance": 3.5},
                },
            },
        ),
    )
    add(
        "controlnet-slot1",
        _graph(
            ["8", 0],
            ["8", 1],
            {
                **base,
                "8": {
                    "class_type": "ControlNetApplyAdvanced",
                    "inputs": {"positive": ["6", 0], "negative": ["7", 0]},
                },
            },
        ),
    )
    add(
        "combine-equal",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "9": _encode("a fox"),
                "8": {
                    "class_type": "ConditioningCombine",
                    "inputs": {"conditioning_1": ["6", 0], "conditioning_2": ["9", 0]},
                },
            },
        ),
    )
    add(
        "combine-differ",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {
                    "class_type": "ConditioningCombine",
                    "inputs": {"conditioning_1": ["6", 0], "conditioning_2": ["7", 0]},
                },
            },
        ),
    )
    for condition in (True, False, ["9", 0], 1, "true"):
        select = {
            "class_type": "easy ifElse",
            "inputs": {"boolean": condition, "on_true": ["6", 0], "on_false": ["7", 0]},
        }
        add(
            f"select-{json.dumps(condition)}",
            _graph(
                ["8", 0],
                ["10", 0],
                {
                    **base,
                    "8": select,
                    "10": _encode("neg"),
                    "9": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
                },
            ),
        )
    same = {
        "class_type": "easy ifElse",
        "inputs": {"boolean": ["9", 0], "on_true": ["6", 0], "on_false": ["11", 0]},
    }
    add(
        "select-converge",
        _graph(["8", 0], neg, {**base, "8": same, "11": _encode("a fox")}),
    )
    # Two selectors on one condition: forced together, so two candidates, not four.
    add(
        "select-shared",
        _graph(
            ["8", 0],
            ["12", 0],
            {
                **base,
                "8": {
                    "class_type": "easy ifElse",
                    "inputs": {
                        "boolean": ["9", 0],
                        "on_true": ["6", 0],
                        "on_false": ["7", 0],
                    },
                },
                "12": {
                    "class_type": "Switch conditioning [Crystools]",
                    "inputs": {
                        "boolean": ["9", 0],
                        "on_true": ["7", 0],
                        "on_false": ["6", 0],
                    },
                },
            },
        ),
    )
    add(
        "runtime",
        _graph(
            ["8", 0],
            neg,
            {**base, "8": {"class_type": "PromptSearchList", "inputs": {}}},
        ),
    )
    add(
        "unknown-encoder",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {"class_type": "FancyEncoder" + "x" * 80, "inputs": {"text": "x"}},
            },
        ),
    )
    add(
        "primitive",
        _graph(
            ["6", 0],
            neg,
            {
                **base,
                "6": _encode(["8", 0]),
                "8": {
                    "class_type": "PrimitiveStringMultiline",
                    "inputs": {"value": "  multi\nline "},
                },
            },
        ),
    )
    add(
        "string-kind-mismatch",
        _graph(
            ["8", 0],
            neg,
            {**base, "8": {"class_type": "PrimitiveString", "inputs": {"value": "x"}}},
        ),
    )
    concat = {
        "class_type": "StringConcatenate",
        "inputs": {"string_a": " a ", "string_b": "", "delimiter": ", "},
    }
    add(
        "string-concatenate",
        _graph(["6", 0], neg, {**base, "6": _encode(["8", 0]), "8": concat}),
    )
    for clean in ("true", "False", True, "TRUE", 0):
        text_concat = {
            "class_type": "Text Concatenate",
            "inputs": {
                "text_b": " b ",
                "text_a": "　a",
                "text_aa": "x",
                "text_c": "",
                "clean_whitespace": clean,
            },
        }
        add(
            f"text-concatenate-{json.dumps(clean)}",
            _graph(["6", 0], neg, {**base, "6": _encode(["8", 0]), "8": text_concat}),
        )
    for strip in (True, False, 1):
        multiline = {
            "class_type": "StringConstantMultiline",
            "inputs": {"string": "\n a\nb \n", "strip_newlines": strip},
        }
        add(
            f"strip-newlines-{json.dumps(strip)}",
            _graph(["6", 0], neg, {**base, "6": _encode(["8", 0]), "8": multiline}),
        )
    for count, as_list in (
        (3, False),
        (1, False),
        (0, False),
        (1001, False),
        (2, True),
        (2.0, False),
        (True, False),
    ):
        join = {
            "class_type": "JoinStringMulti",
            "inputs": {
                "string_1": "",
                "string_2": "b",
                "string_3": "",
                "string_4": "d",
                "inputcount": count,
                "delimiter": " | ",
                "return_list": as_list,
            },
        }
        add(
            f"join-multi-{json.dumps(count)}-{json.dumps(as_list)}",
            _graph(["6", 0], neg, {**base, "6": _encode(["8", 0]), "8": join}),
        )
    manager = {
        "class_type": "PromptManager",
        "inputs": {
            "text": "main",
            "prepend_text": " before ",
            "append_text": "",
            "clip": ["4", 1],
        },
    }
    add("prompt-manager", _graph(["8", 0], neg, {**base, "8": manager}))
    add(
        "prompt-manager-null-prepend",
        _graph(
            ["8", 0],
            neg,
            {
                **base,
                "8": {**manager, "inputs": {**manager["inputs"], "prepend_text": None}},
            },
        ),
    )
    for name, slot in (
        ("PromptManagerV2", 0),
        ("PromptManagerV2", 1),
        ("PromptManagerPositive", 0),
        ("PromptManagerNegative", 0),
    ):
        historical = {
            "class_type": name,
            "inputs": {
                "text": " main ",
                "negative_text": " neg ",
                "prepend_text": "lead,",
                "append_text": None,
            },
        }
        add(
            f"historical-{name}-{slot}",
            _graph(["8", slot], neg, {**base, "8": historical}),
        )
    pair_graph = _graph(["8", 0], ["8", 1], {**base, "8": _pair()})
    add("pair-graph", pair_graph)
    add("pair-execution", pair_graph, comfyassets_prompt_pairs=_execution("8"))
    add(
        "pair-execution-other-node",
        pair_graph,
        comfyassets_prompt_pairs=_execution("99"),
    )
    add(
        "pair-execution-mismatch",
        pair_graph,
        comfyassets_prompt_pairs=_execution("8", text="other"),
    )
    add(
        "pair-execution-negative-changed",
        pair_graph,
        comfyassets_prompt_pairs=_execution("8", negative_effective_text="x"),
    )
    add(
        "pair-execution-version-true",
        pair_graph,
        comfyassets_prompt_pairs=_execution("8").replace(
            '"version": 1', '"version": true'
        ),
    )
    add(
        "pair-execution-version-float",
        pair_graph,
        comfyassets_prompt_pairs=_execution("8").replace(
            '"version": 1', '"version": 1.0'
        ),
    )
    add(
        "pair-execution-truncated",
        pair_graph,
        comfyassets_prompt_pairs=_execution("8").replace(
            '"version": 1', '"version": 1, "truncated": []'
        ),
    )
    add("pair-execution-bad-json", pair_graph, comfyassets_prompt_pairs="{nope")
    two = json.loads(_execution("8"))
    two["nodes"]["8"]["records"].append(
        dict(two["nodes"]["8"]["records"][0], positive_text="other")
    )
    add(
        "pair-execution-two-records",
        pair_graph,
        comfyassets_prompt_pairs=json.dumps(two),
    )
    linked_pair = _graph(
        ["8", 0],
        ["8", 1],
        {
            **base,
            "8": {**_pair(), "inputs": {**_pair()["inputs"], "text": ["9", 0]}},
            "9": {"class_type": "PromptSearchList", "inputs": {}},
        },
    )
    add(
        "pair-execution-runtime-input",
        linked_pair,
        comfyassets_prompt_pairs=_execution("8"),
    )
    add(
        "pair-execution-runtime-wrong-slot",
        _graph(
            ["8", 0],
            ["8", 1],
            {
                **base,
                "8": {**_pair(), "inputs": {**_pair()["inputs"], "text": ["9", 2]}},
                "9": {"class_type": "PromptManagerText", "inputs": {"text": "x"}},
            },
        ),
        comfyassets_prompt_pairs=_execution("8"),
    )
    guided = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["20", 0],
            "guider": ["21", 0],
            "sampler": ["22", 0],
            "sigmas": ["23", 0],
            "latent_image": ["5", 0],
        },
    }
    add(
        "custom-cfg",
        _graph(
            None,
            None,
            {
                **base,
                "21": {
                    "class_type": "CFGGuider",
                    "inputs": {
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "model": ["4", 0],
                        "cfg": 5,
                    },
                },
            },
            sampler=guided,
        ),
    )
    add(
        "custom-basic",
        _graph(
            None,
            None,
            {
                **base,
                "21": {
                    "class_type": "BasicGuider",
                    "inputs": {"conditioning": ["6", 0], "model": ["4", 0]},
                },
            },
            sampler=guided,
        ),
    )
    add(
        "custom-reroute-guider",
        _graph(
            None,
            None,
            {
                **base,
                "21": {"class_type": "Reroute", "inputs": {"value": ["24", 0]}},
                "24": {
                    "class_type": "BasicGuider",
                    "inputs": {"conditioning": ["6", 0]},
                },
            },
            sampler=guided,
        ),
    )
    add(
        "custom-unknown-guider",
        _graph(
            None,
            None,
            {
                **base,
                "21": {"class_type": "DualCFGGuider", "inputs": {"cond1": ["6", 0]}},
            },
            sampler=guided,
        ),
    )
    add(
        "latent-stage",
        _graph(
            ["6", 0],
            neg,
            {
                **base,
                "5": {"class_type": "LatentUpscale", "inputs": {"samples": ["30", 0]}},
                "30": {
                    "class_type": "KSamplerPreview",
                    "inputs": {"model": ["4", 0], "positive": ["6", 0]},
                },
            },
        ),
    )
    add(
        "latent-encode",
        _graph(
            ["6", 0],
            neg,
            {
                **base,
                "5": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["31", 0], "vae": ["4", 2]},
                },
                "31": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
            },
        ),
    )
    add(
        "latent-cycle",
        _graph(
            ["6", 0],
            neg,
            {
                **base,
                "5": {"class_type": "LatentUpscale", "inputs": {"samples": ["3", 0]}},
            },
        ),
    )
    for override in ("", "wild", ["6", 0]):
        detail = {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["6", 0],
                "negative": neg,
                "latent_image": ["5", 0],
                "wildcard": override,
            },
        }
        add(
            f"override-ignored-{json.dumps(override)}",
            _graph(None, None, base, sampler=detail),
        )
    chain, end = _chain(70)
    add("depth-limit", _graph([end, 0], neg, {**base, **chain}))
    chain, end = _chain(61)
    add("depth-near", _graph([end, 0], neg, {**base, **chain}))
    tree, top = _combine_tree(11)
    add("work-limit-visits", _graph([top, 0], neg, {**base, **tree}))
    tree, top = _combine_tree(9)
    add("diamond-memo", _graph([top, 0], [top, 0], {**base, **tree}))
    add("text-limit", _graph(["6", 0], neg, {**base, "6": _encode("x" * 100_001)}))
    add(
        "text-at-limit",
        _graph(["6", 0], neg, {**base, "6": _encode("\U0001f600" * 100_000)}),
    )
    big = {
        f"s{index}": {
            "class_type": "PrimitiveString",
            "inputs": {"value": "y" * 99_000},
        }
        for index in range(20)
    }
    joined = {
        "class_type": "JoinStringMulti",
        "inputs": {
            "string_1": ["s0", 0],
            "inputcount": 20,
            "delimiter": "",
            "return_list": False,
        },
    }
    joined["inputs"].update(
        {f"string_{index + 1}": [f"s{index}", 0] for index in range(1, 20)}
    )
    add(
        "join-over-length",
        _graph(["6", 0], neg, {**base, **big, "6": _encode(["8", 0]), "8": joined}),
    )
    concats = {
        f"k{index}": {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": "z" * 90_000, "string_b": "", "delimiter": ""},
        }
        for index in range(20)
    }
    combine = {f"q{index}": _encode([f"k{index}", 0]) for index in range(20)}
    add("built-chars", _graph(["q0", 0], ["q1", 0], {**base, **concats, **combine}))
    ceiling = {
        str(index): {"class_type": "Note", "inputs": {}} for index in range(100, 20_102)
    }
    add("graph-ceiling", _graph(["6", 0], neg, {**base, **ceiling}))
    workflow = {
        "nodes": [
            {"id": 6, "type": "PromptManager", "widgets_values": ["own text"]},
            {"id": 7, "type": "PromptManager", "widgets_values": ["other text"]},
        ]
    }
    overwritten = {**base, "6": _encode("shared"), "7": _encode("shared")}
    overwritten["6"]["inputs"]["search_text"] = ""
    add(
        "overwritten",
        _graph(["6", 0], ["7", 0], overwritten),
        workflow=json.dumps(workflow),
    )
    nested = {
        "nodes": [{"id": 1, "type": "sub-a", "widgets_values": []}],
        "definitions": {
            "subgraphs": [
                {
                    "id": "sub-a",
                    "nodes": [
                        {
                            "id": 6,
                            "type": "PromptManagerFocused",
                            "widgets_values": {"text": "a"},
                        },
                        {
                            "id": 7,
                            "type": "PromptManagerFocused",
                            "widgets_values": ["b"],
                        },
                    ],
                }
            ]
        },
    }
    nested_graph = {
        **base,
        "1:6": {
            "class_type": "PromptManager",
            "inputs": {"text": "same", "clip": ["4", 1]},
        },
        "1:7": {"class_type": "PromptManager", "inputs": {"text": "same"}},
    }
    add(
        "overwritten-subgraph",
        _graph(["1:6", 0], ["1:7", 0], nested_graph),
        workflow=json.dumps(nested),
    )
    add(
        "overwritten-bad-workflow",
        _graph(["6", 0], ["7", 0], overwritten),
        workflow="{bad",
    )
    # A list or object node ``type`` is skipped like a non-dict node (#286), also
    # inside a subgraph; a skipped node never hides the rest of the walk.
    for label, kind in (("list", ["PromptManager"]), ("dict", {"a": 1})):
        add(
            f"workflow-type-{label}",
            _graph(["6", 0], ["7", 0], overwritten),
            workflow=json.dumps(
                {"nodes": [{"id": 6, "type": "PromptManager"}, {"id": 8, "type": kind}]}
            ),
        )
    add(
        "workflow-type-in-subgraph",
        _graph(["1:6", 0], ["1:7", 0], nested_graph),
        workflow=json.dumps(
            {
                **nested,
                "definitions": {
                    "subgraphs": [{"id": "sub-a", "nodes": [{"id": 2, "type": [1]}]}]
                },
            }
        ),
    )
    add(
        "workflow-type-skipped-keeps-overwritten",
        _graph(["6", 0], ["7", 0], overwritten),
        workflow=json.dumps(
            {
                "nodes": [
                    workflow["nodes"][0],
                    {"id": 8, "type": ["PromptManager"]},
                    workflow["nodes"][1],
                ]
            }
        ),
    )
    add(
        "workflow-type-without-id",
        _graph(["6", 0], ["7", 0], overwritten),
        workflow=json.dumps({"nodes": [{"id": True, "type": ["x"]}, "skipped"]}),
    )
    add(
        "several-samplers",
        _graph(
            ["6", 0],
            neg,
            {
                **base,
                "9": {
                    "class_type": "KSampler",
                    "inputs": {"positive": ["6", 0], "negative": neg},
                },
            },
        ),
    )
    add(
        "detailer",
        _graph(
            None,
            None,
            base,
            sampler={
                "class_type": "FaceDetailer",
                "inputs": {
                    "image": ["5", 0],
                    "positive": ["6", 0],
                    "negative": neg,
                    "wildcard": "",
                },
            },
        ),
    )
    # #381: unknown nodes between the prompt and the sampler pass through.
    try:
        from tests import unknown_node_graphs
    except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
        import unknown_node_graphs
    for name, graph in unknown_node_graphs.shapes().items():
        add(f"pass-through-{name}", graph)
    for name, records in {
        **unknown_node_graphs.recorded_shapes(),
        **unknown_node_graphs.review_shapes(),
    }.items():
        cases[f"pass-through-{name}"] = records
    try:
        from tests import saver_anchor_graphs as anchor
    except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
        import saver_anchor_graphs as anchor
    for name, graph, file_name in (
        ("branch-3", anchor.branch_bench(), "s45_branch_3_00002_.png"),
        ("branch-unanchored", anchor.branch_bench(), None),
        ("branch-no-match", anchor.branch_bench(), "photo_00001_.png"),
        ("branch-linked", anchor.linked_prefix_bench(), "s45_branch_2_00007_.png"),
        (
            "branch-unknown-saver",
            anchor.branch_bench(saver="VendorSaveImage"),
            "s45_branch_4_00001.webp",
        ),
        ("shared-prefix", anchor.shared_prefix(), "IMG_GEN_00371_.png"),
        ("same-links", anchor.two_stage_same_conditioning(), "final_00003_.png"),
        ("same-links-unanchored", anchor.two_stage_same_conditioning(), None),
        (
            "other-links",
            anchor.two_stage_same_conditioning(["91", 0]),
            "final_00003_.png",
        ),
    ):
        cases[f"anchor-{name}"] = {"prompt": _dump(graph)}
        if file_name is not None:
            cases[f"anchor-{name}"][FILE_NAME] = file_name
    cases["parameters-only"] = {
        "parameters": "a fox\nNegative prompt: blur\nSteps: 20, Sampler: euler"
    }
    cases["prompt-and-parameters"] = {
        "prompt": _dump(_graph(["6", 0], neg, base)),
        "parameters": "ignored",
    }
    return cases


def _visits_graph(count):
    """``count`` PrimitiveString links joined under one encoder (``count`` visits)."""
    first, second = count // 2, count - count // 2
    nodes = {
        f"p{index}": {"class_type": "PrimitiveString", "inputs": {"value": "w"}}
        for index in range(count)
    }
    for name, start, size in (("j1", 0, first), ("j2", first, second)):
        inputs = {"inputcount": max(size, 1), "delimiter": "", "return_list": False}
        inputs.update(
            {f"string_{index + 1}": [f"p{start + index}", 0] for index in range(size)}
        )
        inputs.setdefault("string_1", "")
        nodes[name] = {"class_type": "JoinStringMulti", "inputs": inputs}
    nodes["k"] = {
        "class_type": "StringConcatenate",
        "inputs": {"string_a": ["j1", 0], "string_b": ["j2", 0], "delimiter": ""},
    }
    nodes["6"] = _encode(["k", 0])
    nodes["7"] = _encode("blurry")
    return {"prompt": _dump(_graph(["6", 0], ["7", 0], nodes))}


def _depth_graph(length):
    chain, end = _chain(length)
    return {
        "prompt": _dump(_graph([end, 0], ["7", 0], {**chain, "7": _encode("blurry")}))
    }


def _scan_graph(tail):
    """Whitespace-only Text Concatenate parts: scanned before stripping, never built."""
    nodes, parts = {}, []
    for index in range(4):
        if index < 3:
            inputs = {
                f"text_{letter}": " " * 99_000 for letter in "abcdefghijklmnopqrstu"
            }
        else:
            inputs, left = {}, tail
            for letter in "abcdefghijklmnopqrstu":
                inputs[f"text_{letter}"] = " " * min(99_000, left)
                left -= min(99_000, left)
        inputs["text_z"] = "kept" if index == 0 else ""
        nodes[f"t{index}"] = {"class_type": "Text Concatenate", "inputs": inputs}
        parts.append([f"t{index}", 0])
    nodes["k"] = {
        "class_type": "JoinStringMulti",
        "inputs": {
            "string_1": parts[0],
            "string_2": parts[1],
            "string_3": parts[2],
            "string_4": parts[3],
            "inputcount": 4,
            "delimiter": "",
            "return_list": False,
        },
    }
    nodes["6"] = _encode(["k", 0])
    nodes["7"] = _encode("blurry")
    return {"prompt": _dump(_graph(["6", 0], ["7", 0], nodes))}


def _threshold(build, low, high):
    """The largest parameter in [low, high) the server still resolves."""

    def resolved(value):
        evidence = inspect_prompt_evidence(
            build(value), None, profile=INSPECTION_PROFILE
        )
        return evidence["resolution"]["state"] == "resolved"

    assert resolved(low) and not resolved(high)
    while high - low > 1:
        middle = (low + high) // 2
        if resolved(middle):
            low = middle
        else:
            high = middle
    return low


# The nesting the browser's CPython-JSON parsers accept (py-json.mjs JSON_MAX_DEPTH).
JSON_MAX_DEPTH = 1000
JSON_DEPTHS = (JSON_MAX_DEPTH - 1, JSON_MAX_DEPTH, JSON_MAX_DEPTH + 1)


def _nested(depth):
    """``depth`` nested arrays."""
    return "[" * depth + "]" * depth


def depth_cases():
    """``(name, kind, depth, records)``: a deep record at the JSON depth cap.

    A ``prompt`` holds a junk input nested to ``depth`` containers in all (the
    graph, its node and the inputs are three); parsed, both roles resolve.
    A ``workflow`` nested to ``depth`` makes two PromptManager nodes
    overwritten (R1) when it is read, and the prompt resolves when it is not.
    """
    cases = []
    base = {"6": _encode("a fox"), "7": _encode("blurry")}
    for depth in JSON_DEPTHS:
        graph = _dump(_graph(["6", 0], ["7", 0], {**base, "9": {"class_type": "Note"}}))
        junk = '"inputs": {"junk": ' + _nested(depth - 3) + "}"
        prompt = graph.replace('"class_type": "Note"', '"class_type": "Note", ' + junk)
        cases.append((f"prompt-depth-{depth}", "prompt", depth, {"prompt": prompt}))
        overwritten = {**base, "6": _encode("shared"), "7": _encode("shared")}
        overwritten["6"]["inputs"]["search_text"] = ""
        nodes = [
            {"id": 6, "type": "PromptManager", "widgets_values": ["own text"]},
            {"id": 7, "type": "PromptManager", "widgets_values": ["other text"]},
        ]
        workflow = json.dumps({"nodes": nodes, "extra": 0}).replace(
            '"extra": 0', '"extra": ' + _nested(depth - 1)
        )
        records = {
            "prompt": _dump(_graph(["6", 0], ["7", 0], overwritten)),
            "workflow": workflow,
        }
        cases.append((f"workflow-depth-{depth}", "workflow", depth, records))
    return cases


def boundary_cases():
    """Each work bound at its edge: the last value that resolves, then the next."""
    cases = {}
    for name, build, low, high in (
        ("visits", _visits_graph, 10, 2000),
        ("depth", _depth_graph, 1, 80),
        ("scanned", _scan_graph, 0, 2_000_000),
    ):
        edge = _threshold(build, low, high)
        cases[f"{name}-edge"] = build(edge)
        cases[f"{name}-over"] = build(edge + 1)
    long_name = "\U0001f600" * 70
    cases["named-astral"] = {
        "prompt": _dump(
            _graph(
                ["\U0001f600" * 70, 0],
                ["7", 0],
                {
                    "\U0001f600"
                    # No text and no links: still named, not passed through (#381).
                    * 70: {"class_type": long_name, "inputs": {"seed": 1}},
                    "7": _encode("b"),
                },
            )
        )
    }
    return cases


class _Builder:
    """Coherent random graphs over the traced vocabulary, sometimes corrupted."""

    def __init__(self, rng):
        self.rng = rng
        self.nodes = {}
        self.records = {}
        self.counter = 100

    def new_id(self):
        self.counter += 1
        return str(self.counter)

    def word(self):
        rng = self.rng
        parts = [rng.choice(SPACES), rng.choice(WORDS), rng.choice(SPACES)]
        if rng.random() < 0.3:
            parts.append(rng.choice(WORDS))
        return "".join(parts)

    def string(self, depth):
        """A string input: a literal, or a link to a string node."""
        rng = self.rng
        if depth > 3 or rng.random() < 0.45:
            return self.word()
        node_id, choice = self.new_id(), rng.randrange(8)
        if choice == 0:
            node = {
                "class_type": rng.choice(
                    ["PrimitiveString", "PrimitiveStringMultiline"]
                ),
                "inputs": {"value": self.string(depth + 1)},
            }
        elif choice == 1:
            node = {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": self.string(depth + 1),
                    "string_b": self.string(depth + 1),
                    "delimiter": rng.choice(["", ", ", " "]),
                },
            }
        elif choice == 2:
            inputs = {
                f"text_{letter}": self.string(depth + 1)
                for letter in rng.sample("abcd", rng.randint(1, 3))
            }
            inputs["clean_whitespace"] = rng.choice(["true", "false", True])
            node = {"class_type": "Text Concatenate", "inputs": inputs}
        elif choice == 3:
            node = {
                "class_type": "StringConstantMultiline",
                "inputs": {
                    "string": self.string(depth + 1),
                    "strip_newlines": rng.choice([True, False]),
                },
            }
        elif choice == 4:
            count = rng.randint(1, 4)
            inputs = {
                f"string_{index}": self.string(depth + 1)
                for index in range(1, count + 1)
                if index == 1 or rng.random() < 0.8
            }
            inputs.update(
                {
                    "inputcount": count,
                    "delimiter": rng.choice([" ", ","]),
                    "return_list": rng.random() < 0.1,
                }
            )
            node = {"class_type": "JoinStringMulti", "inputs": inputs}
        elif choice == 5:
            node = {
                "class_type": "Reroute",
                "inputs": {"value": self.string_link(depth + 1)},
            }
        elif choice == 6:
            node = {
                "class_type": rng.choice(["StringConstant", "TextInput"]),
                "inputs": {rng.choice(["string", "text"]): self.string(depth + 1)},
            }
        else:
            node = {
                "class_type": "PromptManagerText",
                "inputs": {
                    "text": self.string(depth + 1),
                    "prepend_text": self.word(),
                    "append_text": self.word(),
                },
            }
        self.nodes[node_id] = node
        return [node_id, 0]

    def string_link(self, depth):
        value = self.string(depth)
        if isinstance(value, list):
            return value
        node_id = self.new_id()
        self.nodes[node_id] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": value},
        }
        return [node_id, 0]

    def conditioning(self, depth):
        rng = self.rng
        node_id = self.new_id()
        choice = rng.randrange(11) if depth < 4 else 0
        slot = 0
        if choice == 0:
            node = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self.string(depth + 1), "clip": ["4", 1]},
            }
        elif choice == 1:
            text = self.string(depth + 1)
            node = {
                "class_type": "CLIPTextEncodeSDXL",
                "inputs": {
                    "text_g": text,
                    "text_l": (
                        text if rng.random() < 0.6 else rng.choice(["", self.word()])
                    ),
                },
            }
        elif choice == 2:
            node = {
                "class_type": "FluxGuidance",
                "inputs": {"conditioning": self.conditioning(depth + 1)},
            }
        elif choice == 3:
            node = {
                "class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": self.conditioning(depth + 1)},
            }
        elif choice == 4:
            first = self.conditioning(depth + 1)
            node = {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": first,
                    "conditioning_2": (
                        first if rng.random() < 0.5 else self.conditioning(depth + 1)
                    ),
                },
            }
        elif choice == 5:
            condition = rng.choice([True, False, ["4", 0], ["4", 1]])
            node = {
                "class_type": "easy ifElse",
                "inputs": {
                    "boolean": condition,
                    "on_true": self.conditioning(depth + 1),
                    "on_false": self.conditioning(depth + 1),
                },
            }
        elif choice == 6:
            node = {
                "class_type": rng.choice(["PromptManager", "PromptManagerFocused"]),
                "inputs": {
                    "text": self.string(depth + 1),
                    "prepend_text": self.word(),
                    "append_text": self.word(),
                    "clip": ["4", 1],
                },
            }
        elif choice == 7:
            node = {
                "class_type": rng.choice(["PromptManagerV2", "PromptManagerPositive"]),
                "inputs": {
                    "text": self.string(depth + 1),
                    "prepend_text": self.word(),
                    "negative_text": self.word(),
                },
            }
        elif choice == 8:
            slot = rng.choice([0, 1])
            texts = {
                "text": self.word(),
                "negative_text": self.word(),
                "prepend_text": self.word(),
                "append_text": self.word(),
            }
            node = {"class_type": "PromptManagerPair", "inputs": dict(texts)}
            if rng.random() < 0.6:
                record = {
                    **texts,
                    "positive_text": self.word(),
                    "negative_effective_text": texts["negative_text"],
                }
                record["prepend_text"], record["append_text"] = (
                    record["prepend_text"].strip(),
                    record["append_text"].strip(),
                )
                if rng.random() < 0.2:
                    record["text"] = record["text"] + "!"
                self.records[EXECUTION_KEY] = json.dumps(
                    {"version": 1, "nodes": {node_id: {"records": [record]}}}
                )
        elif choice == 9:
            node = {
                "class_type": "Reroute",
                "inputs": {"value": self.conditioning(depth + 1)},
            }
        else:
            node = {
                "class_type": rng.choice(["PromptSearchList", "FancyEncoder"]),
                "inputs": {"text": self.word()},
            }
        self.nodes[node_id] = node
        return [node_id, slot]

    def corrupt(self):
        rng = self.rng
        node_id = rng.choice(sorted(self.nodes))
        node = self.nodes[node_id]
        links = [
            key
            for key, value in node.get("inputs", {}).items()
            if isinstance(value, list)
        ]
        choice = rng.randrange(6)
        if choice == 0 and links:
            node["inputs"][rng.choice(links)][1] = rng.choice([0.0, True, 7, -1])
        elif choice == 1 and links:
            node["inputs"][rng.choice(links)][0] = rng.choice(["missing", node_id])
        elif choice == 2:
            node["class_type"] = rng.choice([None, 3, "Unknown"])
        elif choice == 3:
            node["inputs"] = rng.choice([[], {}])
        elif choice == 4 and links:
            del node["inputs"][rng.choice(links)]
        else:
            node.pop("inputs", None)


EXECUTION_KEY = "comfyassets_prompt_pairs"


def fuzz_cases(seed, count):
    rng = random.Random(seed)
    cases = {}
    for index in range(count):
        builder = _Builder(rng)
        positive = builder.conditioning(0)
        negative = builder.conditioning(0) if rng.random() < 0.9 else None
        graph = _graph(positive, negative, builder.nodes)
        if negative is None:
            del graph["3"]["inputs"]["negative"]
        if rng.random() < 0.25:
            builder.nodes = graph
            builder.corrupt()
        records = {"prompt": json.dumps(graph, ensure_ascii=rng.random() < 0.5)}
        records.update(builder.records)
        cases[f"fuzz-{seed}-{index}"] = records
    return cases


def single_chain(records):
    """Whether the server answers ``records`` without choosing among samplers."""
    roles = {key for key in records if key in ("prompt", "parameters", EXECUTION_KEY)}
    if "prompt" not in roles:
        return "parameters" not in roles
    try:
        graph = json.loads(records["prompt"])
    except (ValueError, RecursionError):
        return True
    if (
        not isinstance(graph, dict)
        or not graph
        or len(graph) > INSPECTION_PROFILE.node_ceiling
    ):
        return True
    samplers = get_semantics().samplers
    found = [
        node["class_type"]
        for node in graph.values()
        if isinstance(node, dict)
        and isinstance(node.get("class_type"), str)
        and node["class_type"] in samplers
        and samplers[node["class_type"]]["strategy"]
        in ("ksampler", "custom-advanced", "detailer-stage")
    ]
    return len(found) <= 1 and not any(
        samplers[name]["strategy"] == "detailer-stage" for name in found
    )


def _utf8(records):
    """Whether the record texts can be written as UTF-8 (no lone surrogate)."""
    try:
        json.dumps(records, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def committed_vectors():
    """The committed record sets with the server's evidence, in a stable order."""
    try:
        from tests import local_resolver_selection_cases as selection
    except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
        import local_resolver_selection_cases as selection

    named = {**golden_cases(), **selection.selection_cases()}
    cases = {
        name: records
        for name, records in named.items()
        if len(json.dumps(records)) <= MAX_VECTOR_CHARS and _utf8(records)
    }
    cases.update(fuzz_cases(*COMMITTED_FUZZ))
    cases.update(selection.selection_fuzz_cases(*COMMITTED_FUZZ))
    cases.update(selection.passthrough_fuzz_cases(*COMMITTED_PASSTHROUGH))
    vectors = [
        {
            "name": name,
            "records": records,
            "evidence": inspect_prompt_evidence(
                {key: value for key, value in records.items() if key != FILE_NAME},
                None,
                profile=INSPECTION_PROFILE,
                file_name=records.get(FILE_NAME),
            ),
        }
        for name, records in cases.items()
    ]
    return {"version": 2, "vectors": vectors}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the vectors")
    if parser.parse_args().write:
        text = json.dumps(committed_vectors(), ensure_ascii=False, sort_keys=True)
        VECTORS_PATH.write_text(text + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
