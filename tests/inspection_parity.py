"""Deterministic synthetic ComfyUI-shaped fixtures for inspection parity (#240).

Every graph here is original synthetic content (CC0): neutral texts, generic model
file names and invented filename prefixes. Nothing is copied from user outputs.
The shapes reproduce the parity audit's fixture numbering (``f01``..``f17``).

``build(name)`` returns ``{"prompt", "workflow", "extra_chunks"}``. ``write_png``
writes the two records the way core ``SaveImage`` does (Pillow ``PngInfo.add_text``,
``compress_level=4``, a 64x64 constant colour image), then splices any extra raw
``tEXt`` chunks, with real CRC32, before ``IEND``.

Regenerate the committed files with::

    python3 -m tests.inspection_parity --write tests/fixtures/inspection_parity
"""

import argparse
import io
import json
import struct
import tempfile
import zlib
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

POS = "a red fox in snow, detailed fur"
NEG = "blurry, lowres"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "inspection_parity"
VECTORS_PATH = FIXTURE_DIR / "vectors.json"
MAX_FIXTURE_BYTES = 32 * 1024
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ABSENT = "<absent>"
FACT_FIELDS = ("seed", "steps", "cfg", "sampler", "scheduler", "model")
EXPECT_KEYS = (
    "metadata_status",
    "state",
    "reason",
    "reason_code",
    "selection",
    "positive",
    "negative",
    "candidate_count",
    "facts",
)

# Small enough to commit (each file stays under MAX_FIXTURE_BYTES).
COMMITTED = (
    "f01b",
    "f02",
    "f03",
    "f06a",
    "f06b",
    "f06c",
    "f06d",
    "f06e",
    "f07a",
    "f07b",
    "f08",
    "f09",
    "f10",
    "f11",
    "f12",
    "f14",
    "f15",
    "f16",
    "f17",
)
# Built in a temporary directory at test time (too large to commit).
TEST_TIME = ("f01", "f04", "f05")


def _ui(prompt, extra_nodes=0, subgraphs=0, big_widget=None):
    """A UI workflow: one node per prompt node plus inert reroutes/notes/primitives."""
    nodes = [
        {"id": index + 1, "type": node["class_type"], "mode": 0}
        for index, node in enumerate(prompt.values())
    ]
    base = len(nodes)
    for offset in range(extra_nodes):
        kind = ("Reroute", "Note", "PrimitiveNode")[offset % 3]
        nodes.append({"id": base + offset + 1, "type": kind, "mode": 0})
    if big_widget is not None:
        nodes.append(
            {"id": 999999, "type": "Note", "mode": 0, "widgets_values": [big_widget]}
        )
    workflow = {"last_node_id": len(nodes), "nodes": nodes, "links": [], "version": 0.4}
    if subgraphs:
        workflow["definitions"] = {
            "subgraphs": [
                {"id": f"sg-{index}", "nodes": [], "links": []}
                for index in range(subgraphs)
            ]
        }
    return workflow


def _enc(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["4", 1]}}


def _ksampler_graph(positive, negative, sampler="KSampler", latent=None, extra=None):
    graph = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base.safetensors"},
        },
        "5": latent
        or {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": positive,
        "7": negative,
        "3": {
            "class_type": sampler,
            "inputs": {
                "seed": 42,
                "steps": 25,
                "cfg": 6.5,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]},
        },
    }
    if sampler == "KSamplerAdvanced":
        inputs = graph["3"]["inputs"]
        inputs["noise_seed"] = inputs.pop("seed")
        inputs.pop("denoise")
        inputs.update(
            add_noise="enable",
            start_at_step=0,
            end_at_step=10000,
            return_with_leftover_noise="disable",
        )
    graph.update(extra or {})
    return graph


def _basic(prompt, workflow=None, extra_chunks=()):
    return {
        "prompt": prompt,
        "workflow": workflow,
        "extra_chunks": [tuple(chunk) for chunk in extra_chunks],
    }


def _flux2_branches(branches, gates=4):
    """Subgraph-expanded flux2 shape: N SaveImage branches plus a preview-only detailer.

    Each branch is SamplerCustomAdvanced <- BasicGuider <- (easy ifElse /
    ReferenceLatent) x gates <- FluxGuidance <- CLIPTextEncode <- JoinStringMulti.
    Every SaveImage shares one JoinStringMulti ``filename_prefix``. A FaceDetailer
    with a different prompt feeds only a PreviewImage.
    """
    graph = {
        "11": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "vae.safetensors"},
        },
        "12": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "text_encoder.safetensors", "type": "flux2"},
        },
        "13": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "flux-2-klein-9b-fp8.safetensors",
                "weight_dtype": "default",
            },
        },
        "14": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
        "21": {
            "class_type": "StringConstantMultiline",
            "inputs": {"string": POS + ",", "strip_newlines": False},
        },
        "22": {
            "class_type": "StringConstantMultiline",
            "inputs": {"string": "soft winter light,", "strip_newlines": False},
        },
        "31": {"class_type": "PrimitiveString", "inputs": {"value": "Project"}},
        "32": {"class_type": "PrimitiveString", "inputs": {"value": "fixture"}},
        "33": {"class_type": "PrimitiveString", "inputs": {"value": "IMG"}},
        "34": {
            "class_type": "JoinStringMulti",
            "inputs": {
                "inputcount": 2,
                "delimiter": "_",
                "return_list": False,
                "string_1": ["32", 0],
                "string_2": ["33", 0],
            },
        },
        "35": {
            "class_type": "JoinStringMulti",
            "inputs": {
                "inputcount": 4,
                "delimiter": "/",
                "return_list": False,
                "string_1": ["31", 0],
                "string_2": ["32", 0],
                "string_3": ["34", 0],
                "string_4": ["34", 0],
            },
        },
        "41": {
            "class_type": "PrimitiveStringMultiline",
            "inputs": {"value": "Detailer pass: refine the face"},
        },
        "42": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["41", 0], "clip": ["12", 0]},
        },
        "43": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["42", 0]},
        },
        "44": {
            "class_type": "FaceDetailer",
            "inputs": {
                "image": ["14", 0],
                "model": ["13", 0],
                "clip": ["12", 0],
                "vae": ["11", 0],
                "positive": ["42", 0],
                "negative": ["43", 0],
                "seed": 271828182845,
                "steps": 20,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
            },
        },
        "45": {"class_type": "PreviewImage", "inputs": {"images": ["44", 0]}},
    }
    for branch in range(branches):
        scope = f"{1300 + branch}"

        def sub(local, scope=scope):
            return f"{scope}:{local}"

        graph[f"{1100 + branch}"] = {
            "class_type": "StringConstantMultiline",
            "inputs": {
                "string": f"Scene {branch}: unique shot description",
                "strip_newlines": False,
            },
        }
        graph[f"{1200 + branch}"] = {
            "class_type": "JoinStringMulti",
            "inputs": {
                "inputcount": 3,
                "delimiter": " ",
                "return_list": False,
                "string_1": ["21", 0],
                "string_2": [f"{1100 + branch}", 0],
                "string_3": ["22", 0],
            },
        }
        graph[sub(1)] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": [f"{1200 + branch}", 0], "clip": ["12", 0]},
        }
        graph[sub(2)] = {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": 3.5, "conditioning": [sub(1), 0]},
        }
        previous = sub(2)
        for gate in range(gates):
            graph[sub(10 + gate)] = {
                "class_type": "ImageExists",
                "inputs": {"image": ["14", 0]},
            }
            graph[sub(20 + gate)] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": [sub(10 + gate), 0], "vae": ["11", 0]},
            }
            graph[sub(30 + gate)] = {
                "class_type": "ReferenceLatent",
                "inputs": {
                    "conditioning": [previous, 0],
                    "latent": [sub(20 + gate), 0],
                },
            }
            graph[sub(40 + gate)] = {
                "class_type": "easy ifElse",
                "inputs": {
                    "boolean": [sub(10 + gate), 1],
                    "on_true": [sub(30 + gate), 0],
                    "on_false": [previous, 0],
                },
            }
            previous = sub(40 + gate)
        graph[sub(3)] = {
            "class_type": "BasicGuider",
            "inputs": {"model": ["13", 0], "conditioning": [previous, 0]},
        }
        graph[sub(4)] = {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": 1000 + branch},
        }
        graph[sub(5)] = {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        }
        graph[sub(6)] = {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": 20, "width": 1024, "height": 1024},
        }
        graph[sub(7)] = {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        }
        graph[sub(8)] = {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": [sub(4), 0],
                "guider": [sub(3), 0],
                "sampler": [sub(5), 0],
                "sigmas": [sub(6), 0],
                "latent_image": [sub(7), 0],
            },
        }
        graph[sub(9)] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sub(8), 0], "vae": ["11", 0]},
        }
        graph[f"{1400 + branch}"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": ["35", 0], "images": [sub(9), 0]},
        }
    return graph


def _top_level(graph):
    return {key: node for key, node in graph.items() if ":" not in key}


def _f01():
    graph = _flux2_branches(24)
    return _basic(graph, _ui(_top_level(graph), 450, subgraphs=24))


def _f01b():
    graph = _flux2_branches(1)
    return _basic(graph, _ui(_top_level(graph), subgraphs=1))


def _f02():
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    return _basic(graph, _ui(graph, 600))


def _f03():
    side = {
        f"{100 + index}": {"class_type": "PrimitiveInt", "inputs": {"value": 1}}
        for index in range(499)
    }
    return _basic(_ksampler_graph(_enc(POS), _enc(NEG), extra=side))


def _f04():
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    return _basic(graph, _ui(graph, big_widget="A" * 2_500_000))


def _f05():
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    return _basic(graph, _ui(graph, big_widget='"' * 900_000))


def _f06a():
    sdxl = {
        "class_type": "CLIPTextEncodeSDXL",
        "inputs": {
            "text_g": POS,
            "text_l": POS,
            "width": 1024,
            "height": 1024,
            "crop_w": 0,
            "crop_h": 0,
            "target_width": 1024,
            "target_height": 1024,
            "clip": ["4", 1],
        },
    }
    return _basic(_ksampler_graph(sdxl, _enc(NEG)))


def _f06b():
    flux = {
        "class_type": "CLIPTextEncodeFlux",
        "inputs": {"clip_l": POS, "t5xxl": POS, "guidance": 3.5, "clip": ["4", 1]},
    }
    return _basic(_ksampler_graph(flux, _enc(NEG)))


def _f06c():
    guidance = {
        "class_type": "FluxGuidance",
        "inputs": {"guidance": 3.5, "conditioning": ["10", 0]},
    }
    return _basic(_ksampler_graph(guidance, _enc(NEG), extra={"10": _enc(POS)}))


def _f06d():
    combine = {
        "class_type": "ConditioningCombine",
        "inputs": {"conditioning_1": ["10", 0], "conditioning_2": ["11", 0]},
    }
    extra = {"10": _enc(POS), "11": _enc("a second subject")}
    return _basic(_ksampler_graph(combine, _enc(NEG), extra=extra))


def _f06e():
    def apply():
        return {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["10", 0],
                "negative": ["11", 0],
                "control_net": ["12", 0],
                "image": ["13", 0],
                "strength": 1.0,
                "start_percent": 0,
                "end_percent": 1,
            },
        }

    extra = {
        "10": _enc(POS),
        "11": _enc(NEG),
        "12": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "control.safetensors"},
        },
        "13": {"class_type": "LoadImage", "inputs": {"image": "control.png"}},
    }
    graph = _ksampler_graph(apply(), apply(), extra=extra)
    graph["3"]["inputs"]["negative"] = ["7", 1]
    return _basic(graph)


def _f07a():
    return _basic(_ksampler_graph(_enc(POS), _enc(NEG), "KSamplerAdvanced"))


def _f07b():
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    graph["3"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["20", 0],
            "guider": ["21", 0],
            "sampler": ["22", 0],
            "sigmas": ["23", 0],
            "latent_image": ["5", 0],
        },
    }
    graph.update(
        {
            "20": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
            "21": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "cfg": 6.5,
                },
            },
            "22": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "23": {
                "class_type": "BasicScheduler",
                "inputs": {
                    "model": ["4", 0],
                    "scheduler": "normal",
                    "steps": 25,
                    "denoise": 1.0,
                },
            },
        }
    )
    return _basic(graph)


def _f08():
    extra = {
        "30": {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["31", 0], "string_b": ", winter", "delimiter": ""},
        },
        "31": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": POS}},
    }
    return _basic(_ksampler_graph(_enc(["30", 0]), _enc(NEG), extra=extra))


def _f09():
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    graph["40"] = {
        "class_type": "LatentUpscaleBy",
        "inputs": {
            "upscale_method": "nearest-exact",
            "scale_by": 1.5,
            "samples": ["3", 0],
        },
    }
    graph["41"] = {
        "class_type": "KSampler",
        "inputs": {**graph["3"]["inputs"], "latent_image": ["40", 0], "denoise": 0.5},
    }
    graph["8"]["inputs"]["samples"] = ["41", 0]
    return _basic(graph)


def _f10():
    latent = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["50", 0], "vae": ["4", 2]},
    }
    extra = {"50": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}}
    return _basic(_ksampler_graph(_enc(POS), _enc(NEG), latent=latent, extra=extra))


def _f11():
    latent = {
        "class_type": "EmptyHunyuanLatentVideo",
        "inputs": {"width": 640, "height": 384, "length": 17, "batch_size": 1},
    }
    return _basic(_ksampler_graph(_enc(POS), _enc(NEG), latent=latent))


def _f12():
    def link(name, link_id):
        return {"name": name, "link": link_id}

    workflow = {
        "nodes": [
            {
                "id": 3,
                "type": "KSampler",
                "mode": 0,
                "inputs": [
                    link("model", 1),
                    link("positive", 2),
                    link("negative", 3),
                    link("latent_image", 4),
                ],
                "widgets_values": [42, "fixed", 25, 6.5, "euler", "normal", 1.0],
            },
            {
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "mode": 0,
                "inputs": [],
                "widgets_values": ["sdxl_base.safetensors"],
            },
            {
                "id": 6,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [],
                "widgets_values": [POS],
            },
            {
                "id": 7,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [],
                "widgets_values": [NEG],
            },
            {
                "id": 5,
                "type": "EmptyLatentImage",
                "mode": 0,
                "inputs": [],
                "widgets_values": [1024, 1024, 1],
            },
        ],
        "links": [
            [1, 4, 0, 3, 0, "MODEL"],
            [2, 6, 0, 3, 1, "CONDITIONING"],
            [3, 7, 0, 3, 2, "CONDITIONING"],
            [4, 5, 0, 3, 3, "LATENT"],
        ],
    }
    return _basic(None, workflow)


def _f14():
    """ComfyUI writes ``_meta`` titles and ``"is_changed": [NaN]`` outside inputs."""
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    for node in graph.values():
        node["_meta"] = {"title": node["class_type"]}
    graph["4"]["is_changed"] = [float("nan")]
    return _basic(graph, _ui(graph))


def _f15():
    """Byte-identical duplicate ``prompt``/``workflow`` chunks (four copies each)."""
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    workflow = _ui(graph)
    prompt_text, workflow_text = json.dumps(graph), json.dumps(workflow)
    extra = [("prompt", prompt_text), ("workflow", workflow_text)] * 3
    return _basic(graph, workflow, extra)


def _f16():
    """A second ``prompt`` chunk that differs from the first."""
    graph = _ksampler_graph(_enc(POS), _enc(NEG))
    other = _ksampler_graph(_enc("a grey wolf in fog"), _enc(NEG))
    return _basic(graph, _ui(graph), [("prompt", json.dumps(other))])


def _f17():
    """SamplerCustomAdvanced + BasicGuider only: no positive/negative inputs."""
    graph = {
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"},
        },
        "11": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": "clip_l.safetensors",
                "clip_name2": "t5xxl.safetensors",
                "type": "flux",
            },
        },
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": POS, "clip": ["11", 0]},
        },
        "26": {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": 3.5, "conditioning": ["6", 0]},
        },
        "22": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["10", 0], "conditioning": ["26", 0]},
        },
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "17": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["10", 0],
                "scheduler": "simple",
                "steps": 20,
                "denoise": 1.0,
            },
        },
        "27": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "13": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["25", 0],
                "guider": ["22", 0],
                "sampler": ["16", 0],
                "sigmas": ["17", 0],
                "latent_image": ["27", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["13", 0], "vae": ["12", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]},
        },
    }
    return _basic(graph, _ui(graph))


_BUILDERS = {
    "f01": _f01,
    "f01b": _f01b,
    "f02": _f02,
    "f03": _f03,
    "f04": _f04,
    "f05": _f05,
    "f06a": _f06a,
    "f06b": _f06b,
    "f06c": _f06c,
    "f06d": _f06d,
    "f06e": _f06e,
    "f07a": _f07a,
    "f07b": _f07b,
    "f08": _f08,
    "f09": _f09,
    "f10": _f10,
    "f11": _f11,
    "f12": _f12,
    "f14": _f14,
    "f15": _f15,
    "f16": _f16,
    "f17": _f17,
}


def build(name):
    """Return a fresh ``{"prompt", "workflow", "extra_chunks"}`` for one fixture."""
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise KeyError(f"Unknown inspection parity fixture: {name}") from None
    return builder()


def fixture_path(name, directory=FIXTURE_DIR):
    return Path(directory) / f"{name}.png"


def expected_text_chunks(built):
    """The decoded ``(keyword, text)`` chunks ``write_png`` emits, in file order."""
    chunks = []
    if built["prompt"] is not None:
        chunks.append(("prompt", json.dumps(built["prompt"])))
    if built["workflow"] is not None:
        chunks.append(("workflow", json.dumps(built["workflow"])))
    return chunks + [tuple(chunk) for chunk in built["extra_chunks"]]


def _raw_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path, built):
    """Write ``built`` as core SaveImage does, then splice extra raw text chunks."""
    info = PngInfo()
    if built["prompt"] is not None:
        info.add_text("prompt", json.dumps(built["prompt"]))
    if built["workflow"] is not None:
        info.add_text("workflow", json.dumps(built["workflow"]))
    buffer = io.BytesIO()
    with Image.new("RGB", (64, 64), (40, 80, 120)) as image:
        image.save(buffer, format="PNG", pnginfo=info, compress_level=4)
    data = buffer.getvalue()
    iend = data.rindex(b"IEND") - 4
    extra = b"".join(
        _raw_chunk(b"tEXt", key.encode("latin-1") + b"\0" + text.encode("latin-1"))
        for key, text in built["extra_chunks"]
    )
    with open(path, "wb") as stream:
        stream.write(data[:iend] + extra + data[iend:])


def write_all(directory, names=COMMITTED):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        write_png(fixture_path(name, directory), build(name))
    return [fixture_path(name, directory) for name in names]


def read_text_chunks(path):
    """Decode every tEXt/zTXt/iTXt chunk as ``(keyword, text)``, duplicates kept."""
    with open(path, "rb") as stream:
        data = stream.read()
    if not data.startswith(_SIGNATURE):
        raise ValueError(f"Not a PNG: {path}")
    chunks, offset = [], len(_SIGNATURE)
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        body = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"tEXt":
            key, _, text = body.partition(b"\0")
            chunks.append((key.decode("latin-1"), text.decode("latin-1")))
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            chunks.append(
                (key.decode("latin-1"), zlib.decompress(rest[1:]).decode("latin-1"))
            )
        elif kind == b"iTXt":
            key, _, rest = body.partition(b"\0")
            flag, fields = rest[0], rest[2:].split(b"\0", 2)
            text = zlib.decompress(fields[2]) if flag else fields[2]
            chunks.append((key.decode("latin-1"), text.decode("utf-8")))
        elif kind == b"IEND":
            break
    return chunks


def load_vectors(path=VECTORS_PATH):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _role_text(status, text):
    if status == "resolved":
        return text
    return ABSENT if status == "absent" else None


def _observe_inspection(read):
    from utils.prompt_inspector import inspect_prompt_evidence
    from utils.prompt_pair_resolver import INSPECTION_PROFILE

    if read is None:  # inspect_current_file's unreadable-media evidence
        return "unresolved", "Media metadata could not be read", None, None, {}
    evidence = inspect_prompt_evidence(read.metadata, profile=INSPECTION_PROFILE)
    resolution, prompts = evidence["resolution"], evidence["prompts"]
    roles = [
        _role_text(prompts[role]["status"], prompts[role]["text"])
        for role in ("positive", "negative")
    ]
    return resolution["state"], resolution["reason"], *roles, resolution


def _observe_default(path):
    from utils.prompt_pair_resolver import DEFAULT_PROFILE, resolve_prompt_pair

    # Last chunk wins, as Pillow's ``image.text`` (the metadata extractor) does.
    records = dict(read_text_chunks(path))
    canonical = {
        key: value for key, value in records.items() if key in ("prompt", "parameters")
    }
    if isinstance(canonical.get("prompt"), str):
        canonical["prompt"] = json.loads(canonical["prompt"])
    resolved = resolve_prompt_pair(canonical, profile=DEFAULT_PROFILE)
    state = resolved["state"]
    roles = [
        (
            resolved[role]["text"]
            if state == "resolved" and isinstance(resolved[role], dict)
            else (ABSENT if state == "absent" else None)
        )
        for role in ("positive", "negative")
    ]
    resolved = {**resolved, "candidate_count": len(resolved.get("candidates") or [])}
    return "available", state, resolved.get("reason", ""), *roles, resolved


def _observe_path(vector, path):
    from utils.prompt_inspector import MetadataReadError, read_inspection_file

    if vector["profile"] != "inspection":
        return _observe_default(path)
    try:
        read, status = read_inspection_file(path), "available"
    except MetadataReadError:
        read, status = None, "unreadable"
    return (status, *_observe_inspection(read))


def observe(vector, directory=FIXTURE_DIR):
    """Today's outcome for one vector, shaped like ``vector["expect"]`` minus facts.

    ``inspection`` is the workspace inspector path (``read_inspection_file`` then
    ``inspect_prompt_evidence``) with ``INSPECTION_PROFILE``. ``default`` pins
    the resolver's ``DEFAULT_PROFILE`` only: ``resolve_prompt_pair`` on the role
    records decoded from the PNG text chunks. Ingestion's own readers are
    covered by their own suites.

    Committed fixtures are read from ``directory``, which must contain them.
    Test-time fixtures are built into a temporary directory; the file is
    written and closed before it is read, and closed again before cleanup.
    """
    name = vector["fixture"]
    if name in TEST_TIME:
        with tempfile.TemporaryDirectory() as temporary:
            path = fixture_path(name, temporary)
            write_png(path, build(name))
            observed = _observe_path(vector, path)
    else:
        path = fixture_path(name, directory)
        if not path.is_file():
            raise FileNotFoundError(path)
        observed = _observe_path(vector, path)
    status, state, reason, positive, negative, resolution = observed
    return {
        "metadata_status": status,
        "state": state,
        "reason": reason,
        "reason_code": resolution.get("reason_code"),
        "selection": resolution.get("selection"),
        "positive": positive,
        "negative": negative,
        "candidate_count": resolution.get("candidate_count", 0),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", required=True, help="output directory")
    args = parser.parse_args(argv)
    for written in write_all(args.write):
        print(written)


if __name__ == "__main__":
    main()
