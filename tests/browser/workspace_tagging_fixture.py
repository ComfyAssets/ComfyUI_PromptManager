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
import threading
import time
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from tests.browser.folder_trash import replace_folder

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


def _valid_missing(value) -> bool:
    """``{"<model>": ["<pip name>", ...]}`` with known models and string names."""
    return isinstance(value, dict) and all(
        name in MODELS
        and isinstance(names, list)
        and all(isinstance(pip, str) and pip for pip in names)
        for name, names in value.items()
    )


# Unlinked copies enough to virtualize the Images grid (it renders all up to 120).
MAX_EXTRA_IMAGES = 160
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
    "image_categories",
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
        replace_folder(root / name)
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
    from py.api.thumbnail_settings import ThumbnailSettings

    # 150 px previews in the default format (#317 thumbnail settings).
    api._generate_gallery_thumbnails_sync(
        api._get_all_output_dirs(), ThumbnailSettings("webp", 80, 150)
    )
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
    routes: web.RouteTableDef,
    api: PromptManagerAPI,
    root: Path,
    model,
    on_reset=lambda: None,
) -> None:
    """Register fixed seed, snapshot and fake-model commands; no paths or SQL input.

    ``on_reset`` runs first on every tagging reset (the source-change undo).
    """
    # The fake download (``/__test__/tagging/download``): waits on ``event``,
    # then answers ``result``. Every reset sets the event and puts back the
    # model's raising default, so no test can leave a worker hanging.
    # ``generation`` grows on every reset: a download a reset released (or one
    # that timed out) fails without touching the next test's model state.
    download = {"event": threading.Event(), "result": True, "generation": 0}

    def restore_download() -> None:
        download["generation"] += 1
        download["event"].set()
        model.__dict__.pop("download_model", None)

    def fake_download(model_type: str, progress_callback=None) -> bool:
        event, generation = download["event"], download["generation"]
        released = event.wait(DOWNLOAD_HOLD_LIMIT_S)
        if not released or download["generation"] != generation:
            return False
        if download["result"]:
            model.unavailable = model.unavailable - {model_type}
        return download["result"]

    _set_defaults(model, DEFAULTS)  # the server starts on the standard ones

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
        on_reset()
        model.unavailable = set()
        model.missing_packages = {}
        model.suggestions = None
        model.loaded = None
        restore_download()
        _set_defaults(model, DEFAULTS)
        # A test that ends with the dialog open leaves its capture behind; drop
        # every capture so the next test starts below the store's limits.
        await api._run_in_executor(api.close_annotation_captures)
        manifest = await api._run_in_executor(seed_tagging, api, root, extra)
        return web.json_response({"success": True, **manifest})

    @routes.get("/__test__/tagging/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        if request.query:
            raise web.HTTPBadRequest()
        return web.json_response(await api._run_in_executor(tagging_snapshot, api))

    @routes.post("/__test__/tagging/models")
    async def models(request: web.Request) -> web.Response:
        """Mark fake models as not downloaded (``{"unavailable": [...]}``) and,
        optionally, as missing backend packages (``"missing_packages":
        {"<model>": ["<pip name>", ...]}``, #424)."""
        data = await request.json() if request.can_read_body else None
        if (
            request.query
            or not isinstance(data, dict)
            or "unavailable" not in data
            or set(data) - {"unavailable", "missing_packages"}
            or not isinstance(data["unavailable"], list)
            or any(name not in MODELS for name in data["unavailable"])
            or not _valid_missing(data.get("missing_packages", {}))
        ):
            raise web.HTTPBadRequest()
        model.unavailable = set(data["unavailable"])
        model.missing_packages = {
            name: list(names)
            for name, names in data.get("missing_packages", {}).items()
        }
        return web.json_response({"success": True})

    @routes.post("/__test__/tagging/loaded")
    async def loaded(request: web.Request) -> web.Response:
        """Mark one fake model loaded (``{"model": "<key>"}``), as a batch that
        kept it in memory leaves it; ``/autotag/models`` then reports
        ``model_loaded``. Reset unloads it."""
        data = await request.json() if request.can_read_body else None
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) != {"model"}
            or data["model"] not in MODELS
        ):
            raise web.HTTPBadRequest()
        model.loaded = data["model"]
        return web.json_response({"success": True})

    @routes.post("/__test__/tagging/download")
    async def download_mode(request: web.Request) -> web.Response:
        """Switch the model's download to a fake (never a real download).

        ``{"mode": "hold"}``: the next download waits until a later
        ``succeed`` or ``fail`` (or a reset) releases it. ``succeed`` and
        ``fail`` release a held download with that result and answer every
        later one at once. A successful fake marks the model downloaded.
        """
        data = await request.json() if request.can_read_body else None
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) != {"mode"}
            or data["mode"] not in ("hold", "succeed", "fail")
        ):
            raise web.HTTPBadRequest()
        if data["mode"] == "hold":
            download["event"].set()
            download["event"] = threading.Event()
            download["result"] = True
        else:
            download["result"] = data["mode"] == "succeed"
            download["event"].set()
        model.download_model = fake_download
        return web.json_response({"success": True})

    @routes.post("/__test__/tagging/suggestions")
    async def suggestions(request: web.Request) -> web.Response:
        """Answer Generate with fixed scored names (the concept's own sample).

        ``{"scored": [[name, confidence], ...]}`` (at most 100, confidence in
        [0, 1]) or ``{"scored": null}`` for the default list. Every capture is
        released too, so a concept comparison starts below the store's limits
        whatever the previous test left open.
        """
        data = await request.json() if request.can_read_body else None
        scored = data.get("scored") if isinstance(data, dict) else None
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) != {"scored"}
            or not (scored is None or _valid_scored(scored))
        ):
            raise web.HTTPBadRequest()
        model.suggestions = (
            None if scored is None else [(name, float(value)) for name, value in scored]
        )
        model.unavailable = set()
        await api._run_in_executor(api.close_annotation_captures)
        return web.json_response({"success": True})

    @routes.post("/__test__/tagging/defaults")
    async def defaults(request: web.Request) -> web.Response:
        """Serve other tagging defaults from ``GET /autotag/models``.

        ``{"general": g, "character": c, "limit": n}``: the WD14 thresholds
        (in [0, 1]) and the persisted suggestion count (1..100), so a test can
        tell the server's values from the dialog's built-in ones. Reset
        restores the standard 0.35 / 0.85 / 10.
        """
        data = await request.json() if request.can_read_body else None
        if request.query or not _valid_defaults(data):
            raise web.HTTPBadRequest()
        _set_defaults(model, (data["general"], data["character"], data["limit"]))
        return web.json_response({"success": True})

    @routes.get("/__test__/tagging/captures")
    async def captures(request: web.Request) -> web.Response:
        """How many captures the store holds (a released capture is gone)."""
        if request.query:
            raise web.HTTPBadRequest()
        store = api._captures
        count = store.stats()["captures"] if store is not None else 0
        return web.json_response({"captures": count})


# A held fake download gives up after this long, whatever the test does.
DOWNLOAD_HOLD_LIMIT_S = 60

# The standard tagging defaults: WD14 general and character thresholds, and
# the suggestion count (``AutotagConfig``).
DEFAULTS = (0.35, 0.85, 10)


def _set_defaults(model, values) -> None:
    """Set the saved tagging defaults (``AutotagConfig``, #328); a reset also
    puts back the saved model, batch policy and template (the fake model's
    short instructions, which the concept comparisons show)."""
    from py.config import AutotagConfig

    general, character, limit = values
    AutotagConfig.GENERAL_THRESHOLD = general
    AutotagConfig.CHARACTER_THRESHOLD = character
    AutotagConfig.SUGGESTION_LIMIT = limit
    if values == DEFAULTS:
        AutotagConfig.MODEL_TYPE = "wd14-swinv2"
        AutotagConfig.EXISTING = "skip"
        AutotagConfig.TEMPLATE = model.default_prompt


def _valid_defaults(data: object) -> bool:
    unit = (
        lambda value: isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )
    return (
        isinstance(data, dict)
        and set(data) == {"general", "character", "limit"}
        and unit(data["general"])
        and unit(data["character"])
        and type(data["limit"]) is int
        and 1 <= data["limit"] <= 100
    )


def _valid_scored(scored: object) -> bool:
    return (
        isinstance(scored, list)
        and len(scored) <= 100
        and all(
            isinstance(row, list)
            and len(row) == 2
            and isinstance(row[0], str)
            and 0 < len(row[0]) <= 50
            and isinstance(row[1], (int, float))
            and not isinstance(row[1], bool)
            and 0 <= row[1] <= 1
            for row in scored
        )
    )
