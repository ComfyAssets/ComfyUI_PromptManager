"""Owned, deterministic Needs review items for the workspace page (#249 PR F).

Each file is a PNG whose workflow has two samplers with their own positive
encoders and one shared negative, so the resolver finds it ``ambiguous``; it
is recorded through the real recorder (``record_review_file``), exactly as the
live monitor records a new output. No path or SQL comes from a request.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image
from PIL.PngImagePlugin import PngInfo

if TYPE_CHECKING:
    from py.api import PromptManagerAPI

POSITIVES = ("Golden dunes at dusk", "Harbor lights at night")
NEGATIVE = "Soft negative, blur"
# More than one queue page (40), so Load more has a second page.
MAX_ITEMS = 45
_TABLES = (
    "saved_views",
    "image_favorites",
    "media_review_decisions",
    "media_review_items",
    "media_image_tags",
    "media_annotation_bytes",
    "media_annotation_files",
    "image_prompt_pairs",
    "prompt_pair_tags",
    "prompt_pairs",
    "generated_images",
    "prompt_tags",
    "tags",
    "prompt_library_state",
    "prompts",
)


def _graph() -> dict:
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE}},
    }
    for index, text in enumerate(POSITIVES):
        encoder, sampler = str(10 + 2 * index), str(11 + 2 * index)
        graph[encoder] = {"class_type": "CLIPTextEncode", "inputs": {"text": text}}
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": ["2", 0],
            },
        }
    return graph


def _png(path: Path, colour: tuple[int, int, int]) -> None:
    info = PngInfo()
    info.add_text("prompt", json.dumps(_graph()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (96, 64), colour) as image:
        image.save(path, pnginfo=info)


def seed_review(api: PromptManagerAPI, root: Path, items: int) -> dict:
    """Replace this server's disposable rows and files with ``items`` queued files."""
    with api.db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    api.close_annotation_captures()
    for name in ("output", "input"):
        shutil.rmtree(root / name)
        (root / name).mkdir()
    output = root / "output"
    now = time.time()
    files = []
    for number in range(items):
        relative = f"review/review_{number:04}.png"
        _png(output / relative, (40 + number * 4, 90, 140))
        stamp = now - 60 - number
        os.utime(output / relative, (stamp, stamp))
        outcome = api.record_review_file(str(output / relative))
        if outcome != "recorded":
            raise RuntimeError(f"{relative} was not recorded: {outcome}")
        files.append({"path": relative, "filename": Path(relative).name})
    api.invalidate_gallery_cache()
    return {"files": files, "positives": list(POSITIVES), "negative": NEGATIVE}


def register_review_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> None:
    """``POST /__test__/review/reset {items}``: the fixed queue, 0 to MAX_ITEMS."""

    @routes.post("/__test__/review/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if request.query or not isinstance(data, dict) or set(data) - {"items"}:
            raise web.HTTPBadRequest()
        items = data.get("items", 3)
        if isinstance(items, bool) or not isinstance(items, int):
            raise web.HTTPBadRequest()
        if not 0 <= items <= MAX_ITEMS:
            raise web.HTTPBadRequest()
        manifest = await api._run_in_executor(seed_review, api, root, items)
        return web.json_response({"success": True, **manifest})
