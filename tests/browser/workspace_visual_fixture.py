"""Deterministic original artwork and saved prompts for workspace visual checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from utils.hashing import generate_prompt_hash

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


_POSITIVES = (
    (
        "Amber geometry\nLayered sandstone arches, warm afternoon light, "
        "precise silhouettes."
    ),
    (
        "Tidal observatory\nCobalt circles over a quiet sea, "
        "a pale horizon and measured reflections."
    ),
    (
        "Forest atlas\nOverlapping emerald triangles, paper textures "
        "and a soft misty background."
    ),
    (
        "Rose pavilion\nCoral blocks and floating windows, "
        "balanced shadows on a terracotta plaza."
    ),
    "Violet orbit\nConcentric rings, lavender haze and a tiny silver moon.",
    "A study in very long creative context\n"
    + "Preserve every geometric detail, layered color relationship and quiet edge. "
    * 18
    + "unbroken-"
    + "context" * 40,
)
_NEGATIVES = (
    ("blurred edges, watermark", "flat lighting, clipped highlights", ""),
    ("low contrast, noise", "distorted horizon"),
    ("oversaturated foliage", "soft geometry"),
    ("unbalanced composition",),
    ("banding, harsh gradients",),
    ("cropped details, illegible marks",),
)


def _write_art(path: Path, index: int, positive: str, negative: str) -> None:
    sizes = ((720, 480), (480, 720), (560, 560), (900, 400))
    palettes = (
        ("#252c46", "#e0a75e", "#b76445"),
        ("#153b57", "#64bfc6", "#dfdba8"),
        ("#1d4947", "#71ac83", "#d5d8aa"),
        ("#523547", "#ef9c87", "#bc6764"),
        ("#342b59", "#a79bd6", "#e5c0bf"),
        ("#303d4d", "#b1c4c5", "#d6a05f"),
    )
    width, height = sizes[index % len(sizes)]
    background, accent, highlight = palettes[index // 4]
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    info = PngInfo()
    info.add_text("prompt", json.dumps(graph))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (width, height), background) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, height * 3 // 4, width, height), fill=highlight)
        radius = min(width, height) // 3
        center_x, center_y = width // 2 + (index % 3 - 1) * 25, height // 2
        draw.ellipse(
            (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            ),
            fill=accent,
        )
        draw.polygon(
            (
                (width // 8, height * 7 // 8),
                (width // 2, height // 4),
                (width * 7 // 8, height * 7 // 8),
            ),
            outline=highlight,
            width=8,
        )
        draw.line(
            (width // 6, height // 3, width * 5 // 6, height // 3),
            fill=highlight,
            width=4,
        )
        image.save(path, pnginfo=info)
    stamp = 1_760_000_000 - index * 60
    os.utime(path, (stamp, stamp))


def seed_workspace_visual(api: PromptManagerAPI, root: Path) -> None:
    """Replace only the owned browser harness records and media roots."""
    with api.db.model.get_connection() as conn:
        for table in (
            "image_prompt_pairs",
            "prompt_pair_tags",
            "prompt_pairs",
            "generated_images",
            "prompt_tags",
            "tags",
            "prompts",
        ):
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        shutil.rmtree(root / name)
        (root / name).mkdir()
    for parent_index, (positive, negatives) in enumerate(zip(_POSITIVES, _NEGATIVES)):
        category = ("Architecture", "Landscape", "Abstract")[parent_index % 3]
        tags = ["geometric", ("warm", "cool")[parent_index % 2]]
        if parent_index == 5:
            tags.append("long-context-" + "detail" * 28)
        prompt_id = api.db.save_prompt(
            positive,
            category=category,
            tags=tags,
            prompt_hash=generate_prompt_hash(positive),
            rating=parent_index if parent_index else None,
        )
        for variant_index, negative in enumerate(negatives):
            api.db.save_prompt_pair(
                positive,
                negative,
                title=f"Composition {variant_index + 1}",
                category=category,
                tags=tags,
            )
        for offset in range(4):
            index = parent_index * 4 + offset
            directory = ("output", "input")[offset % 2]
            filename = f"study-{index + 1:02}.png"
            if index == 23:
                filename = (
                    "a-very-long-geometric-composition-"
                    + "layered-context-" * 9
                    + ".png"
                )
            path = root / directory / category.lower() / filename
            _write_art(path, index, positive, negatives[offset % len(negatives)])
            api.db.link_image_to_prompt(prompt_id, str(path))
    api._generate_gallery_thumbnails_sync(api._get_all_output_dirs(), (150, 150))
    api.invalidate_gallery_cache()
