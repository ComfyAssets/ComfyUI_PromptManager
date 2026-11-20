"""Graphs of PromptManager runs the monitor asks the ladder for (#659).

``sweep_graph`` is the maintainer's model-compare sweep with placeholder
text: rgthree's Any Switch picks between a PromptManagerText and the same
text joined to a LoRA stack. ``joined_prompt_manager_graph`` is resolved only
by the inspection profile; ``prompt_manager_shared_prefix`` only by the
recorded saver.
"""

try:
    from tests import saver_anchor_graphs as shapes
except ImportError:  # discovered with tests/ as the top-level directory
    import saver_anchor_graphs as shapes

PM_TEXT = "a quiet harbor"
JOINED_TEXT = "a quiet harbor, golden hour"


def joined_prompt_manager_graph(save=True):
    """A PromptManagerText joined to a string node the default profile does
    not know: only the inspection profile resolves the positive."""
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "1": {
            "class_type": "PromptManagerText",
            "inputs": {"text": PM_TEXT, "save_to_library": save},
        },
        "5": {
            "class_type": "StringConstantMultiline",
            "inputs": {"string": "golden hour", "strip_newlines": True},
        },
        "7": {
            "class_type": "JoinStringMulti",
            "inputs": {
                "inputcount": 2,
                "delimiter": ", ",
                "return_list": False,
                "string_1": ["1", 0],
                "string_2": ["5", 0],
            },
        },
        "8": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["7", 0], "clip": ["4", 1]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["4", 1]},
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["8", 0],
                "negative": ["2", 0],
                "latent_image": ["6", 0],
            },
        },
    }


def prompt_manager_shared_prefix():
    """The dataset run with a PromptManagerText feeding branch 1: a
    PromptManager run only its recorded saver decides."""
    graph = shapes.shared_prefix()
    graph["50"] = {
        "class_type": "PromptManagerText",
        "inputs": {"text": shapes.BRANCH_TEXTS[0], "save_to_library": True},
    }
    graph["10"]["inputs"]["text"] = ["50", 0]
    return graph


def sweep_graph(positive="a lighthouse on a cliff at dusk", negative="soft, grainy"):
    """The maintainer's model-compare sweep (#657) with placeholder text.

    The default profile calls it unresolved ("Unsupported latent
    ancestry"); without a table entry for rgthree's Any Switch, the
    inspection profile finds two resolved candidates for the sampler.
    """
    return {
        "8": {"class_type": "CLIPLoader", "inputs": {"clip_name": "c.safetensors"}},
        "9": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
        "10": {
            "class_type": "ModelResolutionPicker",
            "inputs": {"model": "m", "width": 64, "height": 64, "batch_size": 1},
        },
        "11": {
            "class_type": "Lora Stacker (LoraManager)",
            "inputs": {"text": "<lora:style:1.00>", "loras": []},
        },
        "12": {
            "class_type": "PromptManagerText",
            "inputs": {
                "text": positive,
                "prepend_text": "",
                "append_text": "",
                "save_to_library": True,
            },
        },
        "13": {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["11", 1], "string_b": ["12", 0], "delimiter": ", "},
        },
        "14": {
            "class_type": "Any Switch (rgthree)",
            "inputs": {"any_01": ["12", 0], "any_02": ["13", 0]},
        },
        "15": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["14", 0], "clip": ["8", 0]},
        },
        "16": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["8", 0]},
        },
        "21": {"class_type": "CheckpointFolderIterator", "inputs": {"folder": "f"}},
        "23": {
            "class_type": "LoRA Text Loader (LoraManager)",
            "inputs": {"model": ["21", 0], "lora_syntax": ["11", 2]},
        },
        "26": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "steps": 20,
                "cfg": 2.5,
                "model": ["23", 0],
                "positive": ["15", 0],
                "negative": ["16", 0],
                "latent_image": ["10", 0],
            },
        },
        "27": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["26", 0], "vae": ["9", 0]},
        },
        "28": {
            "class_type": "ModelCompareSheet",
            "inputs": {"run_name": "sweep", "images": ["27", 0]},
        },
    }
