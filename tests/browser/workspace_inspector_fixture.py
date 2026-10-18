"""Owned, deterministic physical-file evidence for the full inspector workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.browser.workspace_visual_fixture import seed_workspace_visual

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


INSPECTOR_POSITIVE = (
    "Inspector portrait 猫 😀\n" + "Full prompt context, fine geometry and light. " * 40
)
INSPECTOR_NEGATIVE = "blurred edges, watermark"


def _graph(positive: str, negative: str) -> dict:
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["1", 0],
                "negative": ["2", 0],
                "model": ["4", 0],
                "seed": 9007199254740993,
                "steps": 28,
                "cfg": 6.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "fixture-model.safetensors"},
        },
    }


def _image(path: Path, index: int, metadata: dict) -> None:
    info = PngInfo()
    for key, value in metadata.items():
        text = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        info.add_itxt(key, text, zip=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (720, 480), (30 + index * 9, 74, 98)) as image:
        image.save(path, pnginfo=info)
    stamp = 1_780_000_000 + index
    os.utime(path, (stamp, stamp))


def seed_workspace_inspector(
    api: PromptManagerAPI,
    root: Path,
    *,
    extra_images: int = 0,
    include_video: bool = False,
) -> None:
    """Seed only disposable harness media and exact saved associations."""
    seed_workspace_visual(api, root)
    complete = _graph(INSPECTOR_POSITIVE, INSPECTOR_NEGATIVE)
    workflow = {
        "nodes": [
            {
                "id": 3,
                "type": "KSampler",
                "widgets_values": [
                    9007199254740993,
                    "fixed",
                    28,
                    6.5,
                    "dpmpp_2m",
                    "karras",
                    1,
                ],
            }
        ],
        "links": [],
    }
    ambiguous = {**complete, "5": complete["3"]}
    examples = (
        ("complete.png", {"prompt": complete, "workflow": workflow}),
        ("empty.png", {}),
        ("explicit-empty.png", {"prompt": _graph("Explicit empty negative", "")}),
        ("ambiguous.png", {"prompt": ambiguous}),
        ("workflow-only.png", {"workflow": workflow}),
        ("malformed.png", {"prompt": "not-json", "workflow": "{broken"}),
        ("control-heavy.png", {"parameters": "\x01" * 1_450_000}),
    )
    for index, (name, metadata) in enumerate(examples):
        _image(root / "output" / "inspector" / name, index, metadata)
    _image(
        root / "input" / "inspector" / "complete.png",
        8,
        {"prompt": _graph("Input collision", "")},
    )
    pair = api.db.save_prompt_pair(
        INSPECTOR_POSITIVE,
        INSPECTOR_NEGATIVE,
        title="Inspector recipe",
        category="Inspection",
        tags=["fixture", "geometric"],
    )
    positive = api.db.get_prompt_pair(pair)["positive_prompt_id"]
    image = api.db.link_image_to_prompt(
        positive, str(root / "output" / "inspector" / "complete.png")
    )
    api.db.link_image_to_pair(pair, image, {"source": "fixture"})
    api.db.link_image_to_prompt(
        positive, str(root / "output" / "inspector" / "workflow-only.png")
    )
    for number in range(extra_images):
        _image(
            root / "output" / "inspector" / f"extra-{number:03}.png",
            9 + number % 12,
            {"prompt": complete},
        )
    if include_video:
        shutil.copyfile(
            Path(__file__).resolve().parents[1] / "fixtures" / "tiny.webm",
            root / "output" / "inspector" / "clip.webm",
        )
        os.utime(
            root / "output" / "inspector" / "clip.webm", (1_780_000_100, 1_780_000_100)
        )
    api.invalidate_gallery_cache()
