"""Synthetic graphs for saver anchoring and shared-conditioning stages (#381).

``branch_bench`` reproduces the maintainer's ``gpu_unlock_bench`` run: four
KSampler branches with different prompts, each saved by its own SaveImage whose
literal prefix names the branch (``bench/run/s45_branch_N``). ``shared_prefix``
is the dataset run's shape: every branch's saver uses one prefix, so the file
name cannot name the branch. ``two_stage_same_conditioning`` reproduces the
ANIMA run: a hires stage whose latent passes an unlisted node and reads the
same positive and negative links as the first stage. Texts are neutral.
"""

BRANCH_TEXTS = (
    "a knit beanie on a wooden table",
    "an old fishing boat at dawn",
    "a red vintage car in the rain",
    "a cozy library with tall shelves",
)
NEGATIVE = "blurry, lowres"


def _encode(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}}


def _ksampler(positive, negative, latent, denoise=1.0):
    return {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
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


def _base():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
        "3": _encode(NEGATIVE),
    }


def branch_bench(prefixes=None, saver="SaveImage"):
    """Four branches; ``prefixes`` replaces each saver's ``filename_prefix``."""
    prefixes = prefixes or [
        f"bench/run/s45_branch_{index}" for index in range(1, len(BRANCH_TEXTS) + 1)
    ]
    graph = _base()
    for index, text in enumerate(BRANCH_TEXTS, start=1):
        base = index * 10
        graph[str(base)] = _encode(text)
        graph[str(base + 1)] = _ksampler([str(base), 0], ["3", 0], ["2", 0])
        graph[str(base + 2)] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [str(base + 1), 0], "vae": ["1", 2]},
        }
        graph[str(base + 3)] = {
            "class_type": saver,
            "inputs": {
                "filename_prefix": prefixes[index - 1],
                "images": [str(base + 2), 0],
            },
        }
    return graph


def linked_prefix_bench():
    """The branch run with each prefix coming from a PrimitiveString."""
    graph = branch_bench(prefixes=[["90", 0], ["91", 0], ["92", 0], ["93", 0]])
    for index in range(4):
        graph[str(90 + index)] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": f"bench/run/s45_branch_{index + 1}"},
        }
    return graph


def shared_prefix():
    """Every branch saved with one prefix: the file name names no branch."""
    return branch_bench(prefixes=["dataset/IMG_GEN"] * len(BRANCH_TEXTS))


def two_stage_same_conditioning(second_positive=None):
    """Base and hires stage on the same links; the hires latent passes an
    unlisted node (``VendorGpuCleaner``), so the stages' lineage is unknown."""
    graph = _base()
    graph["90"] = _encode(BRANCH_TEXTS[0])
    graph["63"] = _ksampler(["90", 0], ["3", 0], ["2", 0])
    graph["30"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["63", 0], "vae": ["1", 2]},
    }
    graph["98"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "draft", "images": ["30", 0]},
    }
    graph["110"] = {"class_type": "VendorGpuCleaner", "inputs": {"anything": ["63", 0]}}
    graph["108"] = {
        "class_type": "LatentUpscaleBy",
        "inputs": {
            "upscale_method": "nearest-exact",
            "scale_by": 1.2,
            "samples": ["110", 0],
        },
    }
    graph["114"] = _ksampler(second_positive or ["90", 0], ["3", 0], ["108", 0], 0.5)
    graph["113"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["114", 0], "vae": ["1", 2]},
    }
    graph["115"] = {
        "class_type": "VendorGpuCleaner",
        "inputs": {"anything": ["113", 0]},
    }
    graph["107"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "final", "images": ["115", 0]},
    }
    if second_positive is not None:
        graph["91"] = _encode(BRANCH_TEXTS[1])
    return graph


def hidden_earlier_prompt(override=False):
    """Review shape: the skipped same-links stage continues a different prompt.

    10 (cat) -> 11 (dog) -> unlisted LatentBlend -> 13 (dog, same links as 11)
    -> VAEDecode -> SaveImage ``out``. With ``override``, 11 is a
    ``KSampler (Efficient)`` with a linked ``script``.
    """
    graph = _base()
    graph["5"] = _encode("cat X")
    graph["6"] = _encode("dog Y")
    graph["10"] = _ksampler(["5", 0], ["3", 0], ["2", 0])
    graph["11"] = _ksampler(["6", 0], ["3", 0], ["10", 0])
    if override:
        graph["11"] = _ksampler(["6", 0], ["3", 0], ["2", 0])
        graph["11"]["class_type"] = "KSampler (Efficient)"
        graph["11"]["inputs"]["script"] = ["40", 0]
        graph["40"] = {"class_type": "XY Plot", "inputs": {"grid_spacing": 0}}
    graph["12"] = {
        "class_type": "LatentBlend",
        "inputs": {"samples1": ["11", 0], "samples2": ["2", 0], "blend_factor": 0.5},
    }
    graph["13"] = _ksampler(["6", 0], ["3", 0], ["12", 0], 0.5)
    graph["14"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["13", 0], "vae": ["1", 2]},
    }
    graph["15"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "out", "images": ["14", 0]},
    }
    return graph


def deep_prefixes(chain):
    """Review shape: two branches whose savers both trace the prefix ``dup``.

    Saver A's prefix runs through ``chain`` DisplayText nodes; saver B's
    through one more, so a short visit allowance can finish only one trace.
    """
    graph = branch_bench(prefixes=[["p0", 0], ["q", 0], "x_1", "x_2"])
    graph["pz"] = {"class_type": "StringConstant", "inputs": {"string": "dup"}}
    previous = "pz"
    for index in range(chain - 1, -1, -1):
        graph[f"p{index}"] = {
            "class_type": "DisplayText",
            "inputs": {"text": [previous, 0]},
        }
        previous = f"p{index}"
    graph["q"] = {"class_type": "DisplayText", "inputs": {"text": ["p0", 0]}}
    return graph
