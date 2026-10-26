"""Named workflow cases shared by the generation-parameter parser tests.

Each case is the raw metadata records a file would carry, as text, so the cases
exercise ``parse_js_json`` as well as the tracing. The JS parity test runs every
case through the legacy browser parser too. Prompt texts and seeds are invented.
"""

import json


def _text(value):
    return None if value is None else json.dumps(value)


def case(prompt=None, workflow=None):
    return {"prompt": _text(prompt), "workflow": _text(workflow)}


def encoder(text, clip=("4", 1)):
    return {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": text, "clip": list(clip)},
    }


def ksampler(positive, negative, model=("4", 0), **settings):
    inputs = {
        "seed": 5,
        "steps": 20,
        "cfg": 7,
        "sampler_name": "euler",
        "scheduler": "normal",
        "denoise": 1.0,
        "model": list(model),
        "positive": list(positive),
        "negative": list(negative),
        "latent_image": ["2", 0],
    }
    inputs.update(settings)
    return {"class_type": "KSampler", "inputs": inputs}


CHECKPOINT = {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "example.safetensors"},
}
LATENT = {
    "class_type": "EmptyLatentImage",
    "inputs": {"width": 512, "height": 512, "batch_size": 1},
}


def _basic():
    return {
        "4": CHECKPOINT,
        "2": LATENT,
        "6": encoder("a cat on a windowsill"),
        "7": encoder("blurry, watermark"),
        "3": ksampler(("6", 0), ("7", 0)),
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }


def _prompt_manager(text, prepend="", append=""):
    return {
        "class_type": "PromptManager",
        "inputs": {
            "text": text,
            "category": "",
            "tags": "",
            "search_text": "",
            "prepend_text": prepend,
            "append_text": append,
            "clip": ["4", 1],
        },
    }


def _shared_prompt_pipeline():
    """One PromptManager pair feeds a base pass, an upscale pass and a detailer."""
    return {
        "1": CHECKPOINT,
        "5": ksampler(
            ("25", 0),
            ("26", 0),
            ("1", 0),
            seed=111,
            steps=4,
            cfg=1.0,
            sampler_name="lcm",
            latent_image=["20", 0],
        ),
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "11": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["6", 0], "vae": ["1", 2]},
        },
        "12": ksampler(
            ("25", 0),
            ("26", 0),
            ("1", 0),
            seed=222,
            steps=6,
            cfg=1.0,
            sampler_name="lcm",
            denoise=0.45,
            latent_image=["11", 0],
        ),
        "13": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["12", 0], "vae": ["1", 2]},
        },
        "17": {
            "class_type": "FaceDetailer",
            "inputs": {
                "seed": 333,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "lcm",
                "image": ["13", 0],
                "model": ["1", 0],
                "clip": ["1", 1],
                "vae": ["1", 2],
                "positive": ["25", 0],
                "negative": ["26", 0],
            },
        },
        "20": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 640, "height": 1536, "batch_size": 1},
        },
        "25": _prompt_manager("a lighthouse at dusk, oil painting"),
        "26": _prompt_manager("blurry, watermark"),
        "48": {
            "class_type": "KikoSaveImage",
            "inputs": {"filename_prefix": ["49", 0], "images": ["17", 0]},
        },
        "49": {
            "class_type": "KikoProjectFilePath",
            "inputs": {"project_name": "example"},
        },
    }


def _custom_sampler(guider):
    return {
        "13": CHECKPOINT,
        "20": encoder("a red fox in snow", clip=("13", 1)),
        "21": encoder("lowres", clip=("13", 1)),
        "22": guider,
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "dpmpp_2m"}},
        "17": {
            "class_type": "BasicScheduler",
            "inputs": {
                "scheduler": "karras",
                "steps": 8,
                "denoise": 1.0,
                "model": ["13", 0],
            },
        },
        "1300:8": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["25", 0],
                "guider": ["22", 0],
                "sampler": ["16", 0],
                "sigmas": ["17", 0],
                "latent_image": ["5", 0],
            },
        },
    }


def _workflow_only():
    return {
        "nodes": [
            {
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "mode": 0,
                "inputs": [],
                "widgets_values": ["example.safetensors"],
            },
            {
                "id": 6,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [{"name": "clip", "link": 3}],
                "widgets_values": ["a tidepool at noon"],
            },
            {
                "id": 7,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [{"name": "clip", "link": 5}],
                "widgets_values": ["noisy"],
            },
            {
                "id": 10,
                "type": "Reroute",
                "mode": 0,
                "inputs": [{"name": "", "link": 7}],
            },
            {
                "id": 3,
                "type": "KSampler",
                "mode": 0,
                "inputs": [
                    {"name": "model", "link": 1},
                    {"name": "positive", "link": 8},
                    {"name": "negative", "link": 4},
                    {"name": "latent_image", "link": 2},
                ],
                "widgets_values": [314, "randomize", 25, 6.5, "dpmpp_2m", "karras", 1],
            },
            {
                "id": 11,
                "type": "CLIPTextEncode",
                "mode": 2,
                "inputs": [],
                "widgets_values": ["muted node text"],
            },
        ],
        "links": [
            [1, 4, 0, 3, 0, "MODEL"],
            [3, 4, 1, 6, 0, "CLIP"],
            [5, 4, 1, 7, 0, "CLIP"],
            [7, 6, 0, 10, 0, "CONDITIONING"],
            [8, 10, 0, 3, 1, "CONDITIONING"],
            [4, 7, 0, 3, 2, "CONDITIONING"],
            {
                "id": 2,
                "origin_id": 99,
                "origin_slot": 0,
                "target_id": 3,
                "target_slot": 3,
                "type": "LATENT",
            },
        ],
    }


def _detached_editor_text(pair):
    """#343: editor text inputs are sockets, not widget inputs; no named values."""
    names = ["text"] + (["negative_text"] if pair else [])
    sockets = [
        {"name": "clip", "link": 3},
        {"name": "prepend_text", "link": None},
        {"name": "append_text", "link": None},
    ]
    sockets += [{"name": name, "link": None} for name in names]
    sockets += [
        {"name": name, "widget": {"name": name}, "link": None}
        for name in ("category", "tags", "search_text")
    ]
    editor = {
        "id": 6,
        "type": "PromptManagerPair" if pair else "PromptManagerFocused",
        "mode": 0,
        "inputs": sockets,
        "widgets_values": ["a detached positive"]
        + (["a detached negative"] if pair else [])
        + ["a category", "a tag", "", "", ""],
    }
    negative = (
        []
        if pair
        else [
            {
                "id": 7,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [{"name": "clip", "link": 5}],
                "widgets_values": ["plain negative"],
            }
        ]
    )
    return {
        "nodes": [
            {
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "mode": 0,
                "inputs": [],
                "widgets_values": ["example.safetensors"],
            },
            editor,
            *negative,
            {
                "id": 3,
                "type": "KSampler",
                "mode": 0,
                "inputs": [
                    {"name": "model", "link": 1},
                    {"name": "positive", "link": 8},
                    {"name": "negative", "link": 4},
                ],
                "widgets_values": [7, "fixed", 12, 5, "euler", "normal", 1],
            },
        ],
        "links": [
            [1, 4, 0, 3, 0, "MODEL"],
            [3, 4, 1, 6, 0, "CLIP"],
            [5, 4, 1, 7, 0, "CLIP"],
            [8, 6, 0, 3, 1, "CONDITIONING"],
            [4, 6 if pair else 7, 1 if pair else 0, 3, 2, "CONDITIONING"],
        ],
    }


def _core_widget_input_out_of_order():
    """#343 review R4: a core KSampler whose only widget input is ``cfg``.

    Older frontends saved only converted or linked widget inputs, so the list
    is not a prefix of the schema and carries no named values; the positional
    values must still land on the schema's names.
    """
    workflow = _detached_editor_text(False)
    sampler = workflow["nodes"][-1]
    sampler["inputs"] = sampler["inputs"] + [
        {"name": "cfg", "widget": {"name": "cfg"}, "link": None}
    ]
    sampler["widgets_values"] = [9, "fixed", 30, 4.5, "dpmpp_2m", "karras", 1]
    return workflow


def _nested(depth):
    """A value nested ``depth`` arrays deep, as a crafted file can hold."""
    value = ["leaf", 7]
    for _ in range(depth):
        value = [value, None]
    return value


def _chain(length):
    graph = {"4": CHECKPOINT, "1": encoder("deep chain text")}
    previous = "1"
    for index in range(length):
        node_id = str(100 + index)
        graph[node_id] = {
            "class_type": "ConditioningPass",
            "inputs": {"conditioning": [previous, 0]},
        }
        previous = node_id
    graph["3"] = ksampler((previous, 0), ("1", 0))
    return graph


CASES = {
    "basic": case(_basic()),
    "prompt-manager-prepend-append": case(
        {
            "4": CHECKPOINT,
            "6": _prompt_manager("a cat", prepend="masterpiece", append="4k"),
            "7": _prompt_manager(
                "masterpiece, a dog", prepend="masterpiece", append="4k"
            ),
            "3": ksampler(("6", 0), ("7", 0)),
        }
    ),
    "linked-text-concat": case(
        {
            "4": CHECKPOINT,
            "10": {"class_type": "PrimitiveString", "inputs": {"value": "first part"}},
            "11": {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": ["10", 0],
                    "string_b": "second part",
                    "delimiter": ", ",
                    "seed": 3,
                    "string_c": "   ",
                },
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ["11", 0], "clip": ["4", 1]},
            },
            "7": encoder(""),
            "3": ksampler(("6", 0), ("7", 0)),
        }
    ),
    "prompt-manager-pair": case(
        {
            "4": CHECKPOINT,
            "8": {
                "class_type": "PromptManagerPair",
                "inputs": {
                    "text": "pair positive",
                    "negative_text": "pair negative",
                    "prepend_text": "",
                    "append_text": "",
                    "clip": ["4", 1],
                },
            },
            "3": ksampler(("8", 0), ("8", 1)),
        }
    ),
    "conditioning-passthrough": case(
        {
            "4": CHECKPOINT,
            "6": encoder("through a controlnet"),
            "7": encoder("negative through it"),
            "12": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "control_net": ["13", 0],
                },
            },
            "3": ksampler(("12", 0), ("12", 1)),
        }
    ),
    "first-sampler-in-js-key-order": case(
        {
            "4": CHECKPOINT,
            "6": encoder("from sampler twelve"),
            "7": encoder("from sampler five"),
            "x": ksampler(("6", 0), ("6", 0)),
            "12": ksampler(("6", 0), ("6", 0), seed=12),
            "5": ksampler(("7", 0), ("7", 0), seed=55),
        }
    ),
    "custom-sampler-cfg-guider": case(
        _custom_sampler(
            {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["13", 0],
                    "positive": ["20", 0],
                    "negative": ["21", 0],
                    "cfg": 3.5,
                },
            }
        )
    ),
    "custom-sampler-basic-guider": case(
        _custom_sampler(
            {
                "class_type": "BasicGuider",
                "inputs": {"model": ["13", 0], "conditioning": ["20", 0]},
            }
        )
    ),
    "no-sampler-loose-settings": case(
        {
            "4": CHECKPOINT,
            "3": {
                "class_type": "MyKSamplerVariant",
                "inputs": {
                    "noise_seed": 9,
                    "steps": 12,
                    "cfg": 2,
                    "sampler_name": "ddim",
                },
            },
        }
    ),
    "primitive-seed": case(
        {
            "4": CHECKPOINT,
            "6": encoder("seeded"),
            "30": {
                "class_type": "Seed (rgthree)",
                "inputs": {"other": "x", "seed": 77},
            },
            "3": ksampler(("6", 0), ("6", 0), seed=["30", 0]),
        }
    ),
    "workflow-only": case(workflow=_workflow_only()),
    "workflow-only-detached-editor-text": case(workflow=_detached_editor_text(False)),
    "workflow-only-detached-pair-text": case(workflow=_detached_editor_text(True)),
    "workflow-only-core-widget-input-out-of-order": case(
        workflow=_core_widget_input_out_of_order()
    ),
    "overwritten-prompt-manager-text": case(
        {
            "4": CHECKPOINT,
            "25": _prompt_manager("same saved text"),
            "26": _prompt_manager("same saved text"),
            "3": ksampler(("25", 0), ("26", 0)),
        },
        {
            "nodes": [
                {
                    "id": 25,
                    "type": "PromptManager",
                    "widgets_values": ["the real positive"],
                },
                {
                    "id": 26,
                    "type": "PromptManager",
                    "widgets_values": ["the real negative"],
                },
            ],
            "links": [],
        },
    ),
    "conditioning-cycle": case(
        {
            "4": CHECKPOINT,
            "20": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["21", 0]},
            },
            "21": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["20", 0]},
            },
            "3": ksampler(("20", 0), ("21", 0)),
        }
    ),
    "chain-within-visit-limit": case(_chain(450)),
    "chain-beyond-visit-limit": case(_chain(600)),
    "chain-at-last-visit": case(_chain(499)),
    "chain-one-past-last-visit": case(_chain(500)),
    "blank-text-and-zero-seed": case(
        {
            "4": CHECKPOINT,
            "6": encoder("   "),
            "7": encoder("kept negative"),
            "3": ksampler(("6", 0), ("7", 0), seed=0),
        }
    ),
    "shared-prompt-pipeline": case(_shared_prompt_pipeline()),
    "null-workflow-node": case(_basic(), {"nodes": [None], "links": []}),
    "unparsable-records": {"prompt": '{"3": NaN}', "workflow": "not json"},
    "seed-beyond-double-precision": case(
        {
            "4": CHECKPOINT,
            "6": encoder("x"),
            "3": ksampler(("6", 0), ("6", 0), cfg=1.0, seed=4974052037430000001),
        }
    ),
    "deeply-nested-values": case(
        {
            "4": CHECKPOINT,
            "6": encoder("nested neighbours"),
            "3": ksampler(("6", 0), ("6", 0)),
            "8": {"class_type": _nested(900), "inputs": {"text": "x"}},
        },
        {"nodes": [{"id": _nested(900), "type": _nested(900)}], "links": []},
    ),
    "nothing": case(),
}
