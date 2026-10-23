"""Owned, deterministic Needs review items for the workspace page (#249 PR F).

Each file is a PNG whose workflow has two samplers with their own positive
encoders and one shared negative, so the resolver finds it ``ambiguous``; it
is recorded through the real recorder (``record_review_file``), exactly as the
live monitor records a new output. No path or SQL comes from a request.

``POST /__test__/review/reset`` also takes the special items F2 needs, queued
after the plain ones in this order:

- ``conflict``: a new output handled by the real monitor pipeline
  (``ImageGenerationHandler.process_new_image``). Its positive encoders are
  PromptManager nodes whose texts are not saved, so the monitor falls back to
  the most recent prompt ("Misty forest path"), links the file to it and
  records the item; applying a sampler path then conflicts with that link.
- ``limited``: an A1111 ``parameters`` block with two negative lines, which
  the recorder stores ``limited`` (no candidates).
- ``unavailable``: a file recorded under the ``temp`` directory while it was a
  gallery root; the root is then removed again, so no current root lists it.

``POST /__test__/review/add {kind}`` adds one file while a page is open:
``monitor`` runs the monitor pipeline on a new ambiguous output (recorded),
``file`` only writes one (a metadata scan records it). ``GET
/__test__/review/links`` answers each review file's prompt and pair links, and
``GET /__test__/review/streams`` the open change streams and captures.
"""

from __future__ import annotations

import json
import os
import threading
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
# The monitor's fallback: the most recent saved prompt.
FALLBACK = "Misty forest path"
SPECIAL = ("conflict", "limited", "unavailable")
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


def _graph(encoder_class: str = "CLIPTextEncode") -> dict:
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE}},
    }
    for index, text in enumerate(POSITIVES):
        encoder, sampler = str(10 + 2 * index), str(11 + 2 * index)
        graph[encoder] = {"class_type": encoder_class, "inputs": {"text": text}}
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": ["2", 0],
            },
        }
    return graph


def _png(
    path: Path,
    colour: tuple[int, int, int],
    *,
    graph: dict | None = None,
    parameters: str | None = None,
) -> None:
    info = PngInfo()
    if parameters is not None:
        info.add_text("parameters", parameters)
    else:
        info.add_text("prompt", json.dumps(graph or _graph()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (96, 64), colour) as image:
        image.save(path, pnginfo=info)


def _record(api: PromptManagerAPI, path: Path, expected: str) -> None:
    outcome = api.record_review_file(str(path))
    if outcome != expected:
        raise RuntimeError(f"{path.name} was {outcome}, not {expected}")


def _stamp(path: Path, age: float) -> None:
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


class _Monitor:
    """The real monitor pipeline over this server's database (one per server)."""

    def __init__(self, api: PromptManagerAPI) -> None:
        from utils.image_monitor import ImageGenerationHandler
        from utils.prompt_tracker import PromptTracker

        self.handler = ImageGenerationHandler(api.db, PromptTracker(api.db))
        self.handler.review_recorder = api.record_review_file
        self.lock = threading.Lock()

    def new_output(self, path: Path) -> None:
        """What the monitor does once a new output has settled."""
        with self.lock:
            self.handler.process_new_image(str(path))


def _monitored_output(api: PromptManagerAPI, monitor: _Monitor, path: Path) -> None:
    from utils.hashing import generate_prompt_hash

    if api.db.get_prompt_by_hash(generate_prompt_hash(FALLBACK)) is None:
        api.db.save_prompt(FALLBACK, prompt_hash=generate_prompt_hash(FALLBACK))
    _png(path, (150, 60, 40), graph=_graph("PromptManager"))
    before = len(api.db.review_items_for(_locator(path)))
    monitor.new_output(path)
    if len(api.db.review_items_for(_locator(path))) != before + 1:
        raise RuntimeError(f"the monitor did not record {path.name}")


def _locator(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _unavailable(api: PromptManagerAPI, root: Path, path: Path) -> None:
    from py.config import GalleryConfig

    roots = list(GalleryConfig.MONITORING_DIRECTORIES)
    GalleryConfig.MONITORING_DIRECTORIES = [*roots, str(root / "temp")]
    try:
        _record(api, path, "recorded")
    finally:
        GalleryConfig.MONITORING_DIRECTORIES = roots


def seed_review(
    api: PromptManagerAPI,
    root: Path,
    items: int,
    special: tuple = (),
    monitor: _Monitor | None = None,
) -> dict:
    """Replace this server's disposable rows and files with ``items`` queued files."""
    with api.db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    api.close_annotation_captures()
    for name in ("output", "input"):
        shutil.rmtree(root / name)
        (root / name).mkdir()
    shutil.rmtree(root / "temp" / "review", ignore_errors=True)
    output = root / "output"
    files = []
    for number in range(items):
        relative = f"review/review_{number:04}.png"
        _png(output / relative, (40 + number * 4, 90, 140))
        _stamp(output / relative, 60 + number)
        _record(api, output / relative, "recorded")
        files.append({"path": relative, "filename": Path(relative).name})
    for kind in SPECIAL:
        if kind not in special:
            continue
        relative = f"review/{kind}.png"
        path = (root / "temp" if kind == "unavailable" else output) / relative
        if kind == "conflict":
            _monitored_output(api, monitor, path)
        elif kind == "limited":
            _png(
                path,
                (90, 90, 90),
                parameters=(
                    f"{POSITIVES[0]}\nNegative prompt: blur\nNegative prompt: noise\n"
                    "Steps: 20, Sampler: Euler"
                ),
            )
            _record(api, path, "limited")
        else:
            _png(path, (20, 140, 90))
            _unavailable(api, root, path)
        files.append({"path": relative, "filename": path.name, "kind": kind})
    api.invalidate_gallery_cache()
    return {
        "files": files,
        "positives": list(POSITIVES),
        "negative": NEGATIVE,
        "fallback": FALLBACK,
    }


def add_review_file(
    api: PromptManagerAPI, root: Path, kind: str, monitor: _Monitor
) -> dict:
    """One more ambiguous output: through the monitor, or only written."""
    output = root / "output"
    number = (
        len(list((output / "added").glob("*.png")))
        if (output / "added").exists()
        else 0
    )
    relative = f"added/added_{number:04}.png"
    if kind == "monitor":
        _monitored_output(api, monitor, output / relative)
    else:
        _png(output / relative, (200, 200, 40))
    api.invalidate_gallery_cache()
    return {"path": relative, "filename": Path(relative).name}


_LINKS = (
    "SELECT gi.filename, p.text AS prompt, pp.id AS pair_id, pos.text AS positive, "
    "neg.text AS negative FROM generated_images gi "
    "JOIN prompts p ON p.id = gi.prompt_id "
    "LEFT JOIN image_prompt_pairs ipp ON ipp.image_id = gi.id "
    "LEFT JOIN prompt_pairs pp ON pp.id = ipp.pair_id "
    "LEFT JOIN prompts pos ON pos.id = pp.positive_prompt_id "
    "LEFT JOIN prompts neg ON neg.id = pp.negative_prompt_id "
    "ORDER BY gi.filename, gi.id, pp.id"
)


def review_links(api: PromptManagerAPI) -> list:
    """Every file's prompt link and pair links, as plain rows."""
    conn = api.db.model.get_connection()
    return [dict(row) for row in conn.execute(_LINKS)]


def register_review_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> None:
    """Register the review fixture's commands; no path or SQL comes from a request."""
    monitors = []

    def monitor() -> _Monitor:
        if not monitors:
            monitors.append(_Monitor(api))
        return monitors[0]

    @routes.post("/__test__/review/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) - {"items", *SPECIAL}
        ):
            raise web.HTTPBadRequest()
        items = data.get("items", 3)
        if isinstance(items, bool) or not isinstance(items, int):
            raise web.HTTPBadRequest()
        if not 0 <= items <= MAX_ITEMS:
            raise web.HTTPBadRequest()
        if any(not isinstance(data.get(kind, False), bool) for kind in SPECIAL):
            raise web.HTTPBadRequest()
        special = tuple(kind for kind in SPECIAL if data.get(kind))
        manifest = await api._run_in_executor(
            seed_review, api, root, items, special, monitor()
        )
        return web.json_response({"success": True, **manifest})

    @routes.post("/__test__/review/add")
    async def add(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        if request.query or not isinstance(data, dict) or set(data) != {"kind"}:
            raise web.HTTPBadRequest()
        if data["kind"] not in ("monitor", "file"):
            raise web.HTTPBadRequest()
        added = await api._run_in_executor(
            add_review_file, api, root, data["kind"], monitor()
        )
        return web.json_response({"success": True, **added})

    @routes.get("/__test__/review/links")
    async def links(request: web.Request) -> web.Response:
        if request.query:
            raise web.HTTPBadRequest()
        return web.json_response(
            {"links": await api._run_in_executor(review_links, api)}
        )

    @routes.get("/__test__/review/streams")
    async def streams(request: web.Request) -> web.Response:
        """Open review change streams and held captures (teardown checks)."""
        if request.query:
            raise web.HTTPBadRequest()
        store = api._captures
        captures = store.stats()["captures"] if store is not None else 0
        return web.json_response(
            {"streams": api.review_stream_count(), "captures": captures}
        )
