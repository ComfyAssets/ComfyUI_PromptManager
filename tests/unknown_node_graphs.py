"""Synthetic graphs with unknown nodes between the prompt and the sampler (#381).

``any_switch_graph`` reproduces the shape of the maintainer's krea2 run
(PromptManagerPair -> Any Switch (rgthree) -> KSampler, a LoraManager loader
with its own lora ``text``, custom latent, filename and saver nodes) with
neutral texts. ``shapes()`` names one graph per pass-through rule; the resolver
tests and the browser-port parity vectors share them. No user file is involved.
"""

import json

POSITIVE = "a lighthouse on a cliff, golden hour"
OTHER = "a red bicycle in the rain"
NEGATIVE = "blurry, lowres"


def encode(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["7", 0]}}


def pair(text=POSITIVE, negative=""):
    return {
        "class_type": "PromptManagerPair",
        "inputs": {
            "text": text,
            "negative_text": negative,
            "category": "",
            "tags": "",
            "search_text": "",
            "prepend_text": "",
            "append_text": "",
            "clip": ["9", 1],
        },
    }


def any_switch_graph(**switch_inputs):
    """The shape of #381: PromptManagerPair -> Any Switch (rgthree) -> KSampler.

    Classes and links reproduce the maintainer's krea2 run (a LoraManager
    loader with its own lora ``text``, a project-path filename node, a custom
    latent and saver); the texts are neutral.
    """
    inputs = switch_inputs or {"any_02": ["10", 0]}
    return {
        "3": {"class_type": "SeedHistory", "inputs": {"seed": 1538595059}},
        "5": {
            "class_type": "KikoProjectFilePath",
            "inputs": {"project_name": "demo", "filename": "final"},
        },
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": "model.safetensors"}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip.safetensors"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
        "9": {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {
                "text": "<lora:style-demo:1>",
                "loras": [{"name": "style-demo", "strength": 1, "active": True}],
                "model": ["6", 0],
                "clip": ["7", 0],
            },
        },
        "10": pair(),
        "15": {"class_type": "Any Switch (rgthree)", "inputs": inputs},
        "16": {
            "class_type": "EmptyLatentBatch",
            "inputs": {"width": 1024, "height": 1536, "batch_size": 1},
        },
        "17": {
            "class_type": "KSampler",
            "inputs": {
                "seed": ["3", 0],
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": "er_sde",
                "scheduler": "beta",
                "denoise": 1.0,
                "model": ["9", 0],
                "positive": ["15", 0],
                "negative": ["10", 1],
                "latent_image": ["16", 0],
            },
        },
        "18": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["17", 0], "vae": ["8", 0]},
        },
        "19": {
            "class_type": "KikoSaveImage",
            "inputs": {"filename_prefix": ["5", 0], "images": ["18", 0]},
        },
    }


def with_nodes(graph, **nodes):
    return {**graph, **nodes}


GENERATED = "a generated caption of a lighthouse"


def generated_text_graph():
    """PromptManagerPair whose ``text`` is a language model's output.

    The generator (``sampling_mode.seed``, ``prompt``, ``system_prompt``)
    writes new text at run time: its inputs are not the prompt.
    """
    graph = any_switch_graph()
    graph["10"]["inputs"]["text"] = ["30", 0]
    graph["30"] = {
        "class_type": "VendorTextGenerate",
        "inputs": {
            "prompt": ["31", 0],
            "system_prompt": ["32", 0],
            "sampling_mode.seed": 0,
            "clip": ["7", 0],
        },
    }
    graph["31"] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": OTHER}}
    graph["32"] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "You"}}
    return graph


def transformed_text_graph():
    """PromptManagerPair whose ``text`` passes an unknown string transform."""
    graph = any_switch_graph()
    graph["10"]["inputs"]["text"] = ["30", 0]
    graph["30"] = {
        "class_type": "VendorRegexReplace",
        "inputs": {"text": OTHER, "pattern": "rain", "replace": "snow"},
    }
    return graph


def execution(node_id, text):
    """A ``comfyassets_prompt_pairs`` record of what the pair node ran."""
    record = {
        "text": text,
        "negative_text": "",
        "positive_text": text,
        "negative_effective_text": "",
        "prepend_text": "",
        "append_text": "",
    }
    return json.dumps({"version": 1, "nodes": {node_id: {"records": [record]}}})


def recorded_shapes():
    """Record sets whose PromptManager node recorded the text it ran."""
    return {
        "generated-recorded": {
            "prompt": json.dumps(generated_text_graph()),
            "comfyassets_prompt_pairs": execution("10", GENERATED),
        },
        "transformed-recorded": {
            "prompt": json.dumps(transformed_text_graph()),
            "comfyassets_prompt_pairs": execution("10", GENERATED),
        },
    }


def _sampler_inputs(graph, positive, negative):
    graph["17"]["inputs"]["positive"] = positive
    graph["17"]["inputs"]["negative"] = negative
    return graph


def shapes():
    """One named graph per rule (resolved, candidates, PromptManager, refusals)."""
    vendor_encode = {
        "class_type": "VendorPromptEncode",
        "inputs": {"prompt": ["21", 0], "clip": ["22", 1]},
    }
    control = {
        "class_type": "VendorControlApply",
        "inputs": {
            "positive": ["21", 0],
            "negative": ["22", 0],
            "control_net": ["8", 0],
            "image": ["8", 0],
        },
    }
    return {
        "any-switch-pair": any_switch_graph(),
        "any-switch-same-pair": any_switch_graph(any_01=["10", 0], any_02=["10", 0]),
        "any-switch-equal-texts": with_nodes(
            any_switch_graph(any_01=["20", 0], any_02=["21", 0]),
            **{"20": encode(OTHER), "21": encode(OTHER)},
        ),
        "any-switch-candidates": with_nodes(
            any_switch_graph(any_01=["20", 0], any_02=["21", 0]),
            **{"20": encode(POSITIVE), "21": encode(OTHER)},
        ),
        "any-switch-prompt-manager-wins": with_nodes(
            any_switch_graph(any_01=["20", 0], any_02=["10", 0]),
            **{"20": encode(OTHER)},
        ),
        "any-switch-runtime-blocks": with_nodes(
            any_switch_graph(any_01=["20", 0], any_02=["22", 0]),
            **{
                "20": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": ["21", 0], "clip": ["7", 0]},
                },
                "21": {"class_type": "PromptSearchList", "inputs": {"search": "x"}},
                "22": encode(OTHER),
            },
        ),
        "any-switch-no-text": any_switch_graph(any_01=["7", 0]),
        "unknown-chain": with_nodes(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "Router Node (vendor)",
                    "inputs": {"in": ["10", 0]},
                }
            },
        ),
        "unknown-text-input": with_nodes(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorTextEncode",
                    "inputs": {"text": OTHER, "clip": ["9", 1]},
                }
            },
        ),
        "unknown-role-inputs": _sampler_inputs(
            with_nodes(
                any_switch_graph(),
                **{"20": control, "21": encode(POSITIVE), "22": encode(NEGATIVE)},
            ),
            ["20", 0],
            ["20", 1],
        ),
        "unknown-conditioning-input": with_nodes(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorStyleApply",
                    "inputs": {"conditioning": ["10", 0], "style": ["21", 0]},
                },
                "21": encode(OTHER),
            },
        ),
        "generated-text-runtime": generated_text_graph(),
        "transformed-text": transformed_text_graph(),
        "unknown-skips-clip": with_nodes(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": vendor_encode,
                "21": {"class_type": "PrimitiveString", "inputs": {"value": OTHER}},
                "22": {
                    "class_type": "VendorLoraLoader",
                    "inputs": {"text": "<lora:x:1>", "clip": ["7", 0]},
                },
            },
        ),
    }
