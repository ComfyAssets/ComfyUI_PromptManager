"""Graphs of PromptManager runs the monitor asks the ladder for (#659).

``ambiguous_sweep_graph`` is the maintainer's model-compare sweep with its
rgthree Any Switch replaced by a class no table lists: still ambiguous for
the inspection profile after #657. ``joined_prompt_manager_graph`` is resolved only
by the inspection profile; ``prompt_manager_shared_prefix`` only by the
recorded saver.
"""

try:
    from tests import saver_anchor_graphs as shapes
    from tests import unknown_node_graphs as switch_graphs
except ImportError:  # discovered with tests/ as the top-level directory
    import saver_anchor_graphs as shapes
    import unknown_node_graphs as switch_graphs

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


# The never-listed stand-in class of ``tests/unknown_node_graphs.py``
UNKNOWN_SWITCH = "PMTest Unknown Multi"
# The inspection profile's two candidate texts of ``ambiguous_sweep_graph``
SWEEP_TEXTS = sorted(
    [switch_graphs.POSITIVE, f"{switch_graphs.SWEEP_PREFIX}, {switch_graphs.POSITIVE}"]
)


def ambiguous_sweep_graph(switch_class=UNKNOWN_SWITCH):
    """The maintainer's model-compare sweep (``any_switch_sweep_graph``) with
    its switch's class replaced.

    With the stand-in class no table lists, the default profile calls it
    unresolved and the inspection profile finds two resolved candidates
    (``SWEEP_TEXTS``): the shape the sweep had before rgthree's Any Switch
    got its ``first-present`` entry (#657). The real class resolves it.
    """
    graph = switch_graphs.any_switch_sweep_graph()
    graph["14"]["class_type"] = switch_class
    return graph
