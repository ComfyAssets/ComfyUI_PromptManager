"""Owned, deterministic collections for the workspace collection workflows (#331).

Twenty-one PNGs under the output source: twelve listed members of
"Landscapes", eight of "Experiments" and one loose file in neither. Landscapes
also holds one member whose file does not exist (MC3: hidden, counted as
missing). One saved view reopens "Experiments" (D140). Members are added
through ``ImageCollectionOperations`` with favorites' keys
(``_favorite_root_key``, ``_favorite_relative_key``), as the routes add them.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image
from tests.browser.folder_trash import replace_folder

if TYPE_CHECKING:
    from py.api import PromptManagerAPI

FOLDER = "collections"
LANDSCAPES = "Landscapes"
EXPERIMENTS = "Experiments"
LANDSCAPE_FILES = tuple(f"landscape_{index:02d}.png" for index in range(1, 13))
EXPERIMENT_FILES = tuple(f"experiment_{index:02d}.png" for index in range(1, 9))
LOOSE_FILE = "loose.png"
MISSING_FILE = "gone.png"
SAVED_VIEW = "Experiments view"
_TABLES = (
    "image_collection_items",
    "image_collections",
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
_IMAGES_STATE = {
    "q": "",
    "category": "",
    "sort": "newest",
    "source": "all",
    "folder": "",
    "media": "all",
    "tags": [],
    "favorites": False,
}


def _write_png(path: Path, shade: int, stamp: float) -> None:
    with Image.new("RGB", (64, 48), (shade, 120, 255 - shade)) as image:
        image.save(path)
    os.utime(path, (stamp, stamp))


def seed_collections(api: PromptManagerAPI, root: Path) -> dict:
    """Replace this server's disposable rows and files with the two collections."""
    # The API package needs the host stubs the server installs before importing it.
    from py.api.images import _favorite_relative_key, _favorite_root_key

    db = api.db
    with db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        replace_folder(root / name)
    folder = root / "output" / FOLDER
    folder.mkdir()
    # Newest first in this order: Landscapes, then Experiments, then the loose file.
    now = time.time()
    ordered = (*LANDSCAPE_FILES, *EXPERIMENT_FILES, LOOSE_FILE)
    for index, name in enumerate(ordered):
        _write_png(folder / name, (index * 11) % 256, now - 60 - index * 60)
    root_key = _favorite_root_key(list(api._get_all_output_dirs())[0])
    members = {
        LANDSCAPES: (*LANDSCAPE_FILES, MISSING_FILE),
        EXPERIMENTS: EXPERIMENT_FILES,
    }
    ids = {}
    for name, files in members.items():
        added = db.add_to_collection(
            name=name,
            items=[
                (root_key, _favorite_relative_key(f"{FOLDER}/{file}")) for file in files
            ],
        )
        ids[name] = added["collection"]["id"]
    view = db.create_saved_view(
        "images", SAVED_VIEW, {**_IMAGES_STATE, "collection": str(ids[EXPERIMENTS])}
    )
    api.invalidate_gallery_cache()
    return {
        "collections": {
            LANDSCAPES: {"id": ids[LANDSCAPES], "count": 12, "missing": 1},
            EXPERIMENTS: {"id": ids[EXPERIMENTS], "count": 8, "missing": 0},
        },
        "files": {
            LANDSCAPES: [f"{FOLDER}/{file}" for file in LANDSCAPE_FILES],
            EXPERIMENTS: [f"{FOLDER}/{file}" for file in EXPERIMENT_FILES],
            "loose": f"{FOLDER}/{LOOSE_FILE}",
        },
        "saved_view": {"id": view["id"], "name": SAVED_VIEW},
    }


def register_collections_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> None:
    """Register the fixed seed command; no path or SQL comes from a request."""

    @routes.post("/__test__/collections/seed")
    async def seed(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if data != {} or request.query:
            raise web.HTTPBadRequest()
        manifest = await api._run_in_executor(seed_collections, api, root)
        return web.json_response({"success": True, **manifest})
