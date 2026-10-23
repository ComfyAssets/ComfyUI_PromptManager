"""Synthetic multi-stage graphs for the pass-through lineage rule.

Each builder reproduces one graph shape with neutral texts and generic names:
a hires-fix or refiner chain (sampler, decode, image filter, model upscale,
scale, encode, sampler), a three-stage Flux run whose last stage combines the
base prompt with a style trigger, an image-edit batch of samplers with distinct
prompts, and a chain whose prompt is generated at run time. They are shared by
the resolver tests and the browser-port parity cases; no user file is involved.
"""

import copy

POSITIVE = "a lighthouse on a cliff, golden hour"
NEGATIVE = "blurry, lowres"
OTHER = "a red bicycle in the rain"
STYLE = "film grain, analog photo"


def encode(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}}


def prompt_manager(text, prepend=""):
    return {
        "class_type": "PromptManager",
        "inputs": {
            "text": text,
            "category": "",
            "tags": "",
            "search_text": "",
            "prepend_text": prepend,
            "append_text": "",
            "clip": ["1", 1],
        },
    }


def ksampler(positive, negative, latent, denoise=1.0):
    return {
        "class_type": "KSampler",
        "inputs": {
            "seed": 7,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise,
            "model": ["1", 0],
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
        },
    }


def hires_fix(second_positive=None, filter_class="Image Filter"):
    """Two KSamplers joined in image space, like a hires-fix or refiner run.

    Sampler 10 runs on a custom batch latent; its output goes through a tiled
    decode, an interactive image filter, a model upscale and a scale (sized by a
    resolution node reading the filtered image), then is encoded again for
    sampler 20 (denoise 0.25), which is decoded and saved.
    """
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": prompt_manager(POSITIVE),
        "3": encode(NEGATIVE),
        "4": {
            "class_type": "EmptyLatentBatch",
            "inputs": {"width": 1024, "height": 512, "batch_size": 4},
        },
        "10": ksampler(["2", 0], ["3", 0], ["4", 0]),
        "11": {
            "class_type": "VAEDecodeTiled",
            "inputs": {"tile_size": 512, "samples": ["10", 0], "vae": ["1", 2]},
        },
        "12": {
            "class_type": filter_class,
            "inputs": {"timeout": 600, "ontimeout": "send none", "images": ["11", 0]},
        },
        "13": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "x4"}},
        "14": {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["13", 0], "image": ["12", 0]},
        },
        "15": {
            "class_type": "ResolutionCalculator",
            "inputs": {"scale_factor": 1.5, "image": ["12", 0]},
        },
        "16": {
            "class_type": "ImageScale",
            "inputs": {
                "upscale_method": "nearest-exact",
                "width": ["15", 0],
                "height": ["15", 1],
                "crop": "disabled",
                "image": ["14", 0],
            },
        },
        "17": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["16", 0], "vae": ["1", 2]},
        },
        "20": ksampler(["2", 0], ["3", 0], ["17", 0], denoise=0.25),
        "21": {
            "class_type": "VAEDecodeTiled",
            "inputs": {"tile_size": 512, "samples": ["20", 0], "vae": ["1", 2]},
        },
        "22": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "hires", "images": ["21", 0]},
        },
        "23": {"class_type": "PlaySound|pysssss", "inputs": {"any": ["21", 0]}},
    }
    if second_positive is not None:
        graph["5"] = encode(second_positive)
        graph["20"]["inputs"]["positive"] = ["5", 0]
    return graph


_COMPOSITE_INPUTS = {
    "ConditioningCombine": ("conditioning_1", "conditioning_2"),
    "ConditioningConcat": ("conditioning_to", "conditioning_from"),
    "ConditioningAverage": ("conditioning_to", "conditioning_from"),
}


def flux_three_stage(combined=(("30", 0), ("31", 0)), composite="ConditioningCombine"):
    """FluxGuidance base, a refiner through an image filter, then a tiled upscale.

    Samplers 40 and 50 share FluxGuidance(PromptManager 30) and a zeroed empty
    negative. Sampler 50 refines 40's filtered image (denoise 0.25). The final
    UltimateSDUpscale 60 reads 50's image, scaled down, and a positive that is
    ``composite`` of ``combined`` (by default the base PromptManager and a
    style-trigger encoder 31).
    """
    names = _COMPOSITE_INPUTS[composite]
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae"}},
        "30": prompt_manager(POSITIVE),
        "31": encode(STYLE),
        "32": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
        },
        "33": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["32", 0]},
        },
        "34": {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": 3.5, "conditioning": ["30", 0]},
        },
        "35": {
            "class_type": composite,
            "inputs": dict(zip(names, map(list, combined))),
        },
        "36": {
            "class_type": "EmptyLatentBatch",
            "inputs": {"width": 1080, "height": 1920, "batch_size": 4},
        },
        "40": ksampler(["34", 0], ["33", 0], ["36", 0]),
        "41": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["40", 0], "vae": ["3", 0]},
        },
        "42": {
            "class_type": "Image Filter",
            "inputs": {"timeout": 600, "images": ["41", 0]},
        },
        "43": {"class_type": "PreviewImage", "inputs": {"images": ["42", 0]}},
        "44": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["42", 0], "vae": ["3", 0]},
        },
        "50": ksampler(["34", 0], ["33", 0], ["44", 0], denoise=0.25),
        "51": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["50", 0], "vae": ["3", 0]},
        },
        "52": {
            "class_type": "ImageScaleDownBy",
            "inputs": {"scale_by": 0.75, "images": ["51", 0]},
        },
        "53": {"class_type": "PreviewImage", "inputs": {"images": ["51", 0]}},
        "54": {
            "class_type": "Image Comparer (rgthree)",
            "inputs": {"image_a": ["51", 0], "image_b": ["42", 0]},
        },
        "60": {
            "class_type": "UltimateSDUpscale",
            "inputs": {
                "upscale_by": 2.0,
                "seed": 7,
                "steps": 4,
                "denoise": 0.35,
                "image": ["52", 0],
                "model": ["1", 0],
                "positive": ["35", 0],
                "negative": ["33", 0],
                "vae": ["3", 0],
            },
        },
        "61": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "flux", "images": ["60", 0]},
        },
    }
    return graph


def edit_batch(count=3):
    """Image-edit samplers with distinct prompts, all saved with one prefix."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "3": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["2", 0], "vae": ["1", 2]},
        },
        "4": {"class_type": "PrimitiveString", "inputs": {"value": "edit"}},
    }
    for index in range(count):
        base = 100 + index * 10
        graph[str(base)] = {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {
                "prompt": f"edit number {index}: change the pose",
                "clip": ["1", 1],
            },
        }
        graph[str(base + 1)] = ksampler([str(base), 0], [str(base), 0], ["3", 0])
        graph[str(base + 2)] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [str(base + 1), 0], "vae": ["1", 2]},
        }
        graph[str(base + 3)] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": ["4", 0], "images": [str(base + 2), 0]},
        }
    return graph


def trigger_toggle(words="trig9word"):
    """LoraManager's TriggerWord Toggle: its output is decided at run time."""
    return {
        "class_type": "TriggerWord Toggle (LoraManager)",
        "inputs": {
            "group_mode": True,
            "default_active": True,
            "toggle_trigger_words": {"__value__": [{"text": words, "active": True}]},
            "orinalMessage": words,
            "trigger_words": ["7", 2],
        },
    }


def hires_fix_with_trigger_words():
    """``hires_fix`` whose PromptManager prepends LoRA trigger words (node 8)."""
    graph = hires_fix()
    graph["7"] = {
        "class_type": "Lora Loader (LoraManager)",
        "inputs": {"model": ["1", 0], "clip": ["1", 1], "text": "<lora:x:1>"},
    }
    graph["8"] = trigger_toggle()
    graph["2"]["inputs"]["prepend_text"] = ["8", 0]
    return graph


def generated_text_chain():
    """``hires_fix`` whose positive text comes from a run-time text generator."""
    graph = hires_fix()
    graph["2"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ["6", 0], "clip": ["1", 1]},
    }
    graph["6"] = {
        "class_type": "TextGenerate",
        "inputs": {"prompt": "write a prompt", "model": ["1", 0]},
    }
    return graph


def copied(graph):
    return copy.deepcopy(graph)
