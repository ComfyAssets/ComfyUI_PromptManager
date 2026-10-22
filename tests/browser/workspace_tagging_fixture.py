"""Owned, deterministic files for the Tag image dialog workflows (#249 PR C).

Two files share one positive prompt and one saved pair, an equal-byte copy of
the first sits at another path, and a video and an unlinked file follow. The
injected fake model (``tests/browser/server.py`` ``TestModel``) answers
Generate; no model file exists and nothing is ever downloaded.
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

POSITIVE = (
    "Monolithic sandstone pavilion in a vast desert, sweeping dunes, warm "
    "terracotta, late afternoon sun, architectural photography, quiet geometry"
)
NEGATIVE = "plastic, harsh shadows, noise, watermark"
TITLE = "Quiet geometry"
PROMPT_TAGS = ["minimal", "warm_light"]
PAIR_TAGS = ["editorial"]
# Newest first, so the Images order (Recently added) is the manifest's.
FILES = (
    ("tagging/dunes_00001.png", "linked"),
    ("tagging/dunes_00002.png", "linked"),
    ("copies/dunes_00001.png", "copy"),
    ("tagging/clip.webm", "video"),
    ("loose/unlinked.png", "unlinked"),
)
MODELS = ("wd14-swinv2", "wd14-vit", "gguf", "hf")
# Unlinked copies enough to virtualize the Images grid (it renders all up to 120).
MAX_EXTRA_IMAGES = 160
_TABLES = (
    "saved_views",
    "image_favorites",
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
# Every prompt-side owner row a Tag save could touch, for before/after
# comparisons: tagging one image never changes a prompt, a pair or a link.
_SNAPSHOT = {
    "prompts": "SELECT id, text, category, tags, updated_at FROM prompts ORDER BY id",
    "prompt_tags": "SELECT prompt_id, tag_id FROM prompt_tags ORDER BY 1, 2",
    "prompt_pairs": "SELECT id, positive_prompt_id, negative_prompt_id, category,"
    " archived, updated_at FROM prompt_pairs ORDER BY id",
    "prompt_pair_tags": "SELECT pair_id, tag_id FROM prompt_pair_tags ORDER BY 1, 2",
    "generated_images": "SELECT id, prompt_id, image_path FROM generated_images"
    " ORDER BY id",
    "image_prompt_pairs": "SELECT id, image_id, pair_id FROM image_prompt_pairs"
    " ORDER BY id",
}


def _png(path: Path, colour: tuple[int, int, int], size=(96, 64)) -> None:
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": POSITIVE}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0], "seed": 7},
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    info = PngInfo()
    info.add_text("prompt", json.dumps(graph))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, colour) as image:
        image.save(path, pnginfo=info)


def seed_tagging(api: PromptManagerAPI, root: Path, extra_images: int = 0) -> dict:
    """Replace this server's disposable rows and files with the tagging set."""
    db = api.db
    with db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        shutil.rmtree(root / name)
        (root / name).mkdir()
    output = root / "output"
    _png(output / FILES[0][0], (196, 140, 92))
    _png(output / FILES[1][0], (150, 110, 80))
    (output / FILES[2][0]).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output / FILES[0][0], output / FILES[2][0])
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "fixtures" / "tiny.webm",
        output / FILES[3][0],
    )
    _png(output / FILES[4][0], (60, 70, 90), size=(64, 64))
    now = time.time()
    for index, (relative, _) in enumerate(FILES):
        stamp = now - 60 - index * 10
        os.utime(output / relative, (stamp, stamp))
    for number in range(extra_images):
        extra = output / "bulk" / f"bulk_{number:04}.png"
        extra.parent.mkdir(exist_ok=True)
        shutil.copyfile(output / FILES[4][0], extra)
        stamp = now - 600 - number
        os.utime(extra, (stamp, stamp))

    receipt = db.create_library_recipe(
        POSITIVE,
        NEGATIVE,
        expected_epoch=db.get_execution_epoch(),
        parent_metadata={
            "title": TITLE,
            "category": "Architecture",
            "tags": list(PROMPT_TAGS),
        },
    )
    positive_id = receipt["positive"]["id"]
    pair_id = receipt["pair"]["id"]
    with db.model.get_connection() as conn:
        tag_ids = []
        for name in PAIR_TAGS:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_ids.append(
                conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()[0]
            )
        conn.executemany(
            "INSERT INTO prompt_pair_tags (pair_id, tag_id) VALUES (?, ?)",
            [(pair_id, tag_id) for tag_id in tag_ids],
        )
    for relative, kind in FILES:
        if kind in {"linked", "video"}:
            image_id = db.link_image_to_prompt(positive_id, str(output / relative))
            if kind == "linked":
                db.link_image_to_pair(pair_id, image_id, {"source": "fixture"})
    api._generate_gallery_thumbnails_sync(api._get_all_output_dirs(), (150, 150))
    api.invalidate_gallery_cache()
    return {
        "positive_id": positive_id,
        "pair_id": pair_id,
        "title": TITLE,
        "prompt_tags": list(PROMPT_TAGS),
        "pair_tags": list(PAIR_TAGS),
        "files": [
            {"path": relative, "filename": Path(relative).name, "kind": kind}
            for relative, kind in FILES
        ],
    }


def tagging_snapshot(api: PromptManagerAPI) -> dict:
    """Plain rows of every prompt, pair and link table."""
    with api.db.model.get_connection() as conn:
        return {
            name: [list(row) for row in conn.execute(query)]
            for name, query in _SNAPSHOT.items()
        }


def register_tagging_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path, model
) -> None:
    """Register fixed seed, snapshot and fake-model commands; no paths or SQL input."""

    @routes.post("/__test__/tagging/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if request.query or not isinstance(data, dict) or set(data) - {"extra_images"}:
            raise web.HTTPBadRequest()
        extra = data.get("extra_images", 0)
        if isinstance(extra, bool) or not isinstance(extra, int):
            raise web.HTTPBadRequest()
        if not 0 <= extra <= MAX_EXTRA_IMAGES:
            raise web.HTTPBadRequest()
        model.unavailable = set()
        model.loaded = None
        manifest = await api._run_in_executor(seed_tagging, api, root, extra)
        return web.json_response({"success": True, **manifest})

    @routes.get("/__test__/tagging/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        if request.query:
            raise web.HTTPBadRequest()
        return web.json_response(await api._run_in_executor(tagging_snapshot, api))

    @routes.post("/__test__/tagging/models")
    async def models(request: web.Request) -> web.Response:
        """Mark fake models as not downloaded (``{"unavailable": [...]}``)."""
        data = await request.json() if request.can_read_body else None
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) != {"unavailable"}
            or not isinstance(data["unavailable"], list)
            or any(name not in MODELS for name in data["unavailable"])
        ):
            raise web.HTTPBadRequest()
        model.unavailable = set(data["unavailable"])
        return web.json_response({"success": True})
