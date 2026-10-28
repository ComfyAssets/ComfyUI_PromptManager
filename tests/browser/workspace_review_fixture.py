"""Owned, deterministic Needs review items for the workspace page (#249 PR F).

Each file is a PNG whose workflow has two samplers with their own positive
encoders and one shared negative, so the resolver finds it ``ambiguous``. Its
loader node carries ComfyUI's ``Infinity`` stamp, which the legacy pages'
parser (the scan's fallback) cannot read, so nothing links the file and it is
a Needs review case (#284: only ambiguous, unlinked files are queued). It is
recorded through the scan's recorder (``_record_review_sync`` with
``inspection=True``; only scans add items). No path or SQL comes from a
request.

``POST /__test__/review/reset`` also takes the special items F2 needs, queued
after the plain ones in this order:

- ``conflict``: an item whose file was linked to another prompt ("Misty
  forest path") after it was queued (the next scan or monitor pass would
  remove the item); applying a sampler path then conflicts with that link.
- ``limited``: a graph of 17 samplers, each with its own prompt, which the
  recorder stores ``limited`` (more than 16 candidates, so none are stored).
  An A1111 block with two negative lines is not queued any more: it offers
  no candidate to choose (#284).
- ``unavailable``: a file recorded under the ``temp`` directory while it was a
  gallery root; the root is then removed again, so no current root lists it.

``concept: true`` (F3, the concept comparison) names and sizes the plain
files as the concept's first three images (app.js ``images``) and gives
their first sampler the concept's first prompt (``alpine``: its positive and
its first negative, the one the concept's panel shows), so the panel's text
wraps as the concept's does.

``POST /__test__/review/add {kind}`` adds one file while a page is open:
``scan`` records a new ambiguous, unlinked output from a worker thread
through the scan's recorder (as a running scan's batch does; the monitor
never adds items), ``file`` only writes one (a metadata scan records it). ``GET
/__test__/review/links`` answers each review file's prompt and pair links, and
``GET /__test__/review/streams`` the open change streams and captures.
``POST /__test__/review/notify`` sends one queue change notice without
changing the queue: each open stream writes a ``change``, and a stream whose
page closed is dropped at such a write.
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
from tests.browser.folder_trash import replace_folder

if TYPE_CHECKING:
    from py.api import PromptManagerAPI

POSITIVES = ("Golden dunes at dusk", "Harbor lights at night")
NEGATIVE = "Soft negative, blur"
# The prompt the conflict item's file is linked to after it was queued.
FALLBACK = "Misty forest path"
SPECIAL = ("conflict", "limited", "unavailable")
# The concept's first three images (concept app.js `images`) and first prompt.
CONCEPT_IMAGES = (
    ("alpine_00001.png", (1536, 1152)),
    ("dunes_00001.png", (1024, 1536)),
    ("forest_00001.png", (1280, 1280)),
)
CONCEPT_POSITIVE = (
    "A remote alpine observatory at blue hour, warm light through glass, dramatic "
    "mountain peaks, cinematic composition, atmospheric mist, fine detail"
)
CONCEPT_NEGATIVE = "blurry, low quality, low resolution"
# More candidates than an item stores (16): the ``limited`` item.
LIMITED_SAMPLERS = 17
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


def _graph(
    encoder_class: str = "CLIPTextEncode",
    positives: tuple = POSITIVES,
    negative: str = NEGATIVE,
) -> dict:
    graph = {
        # ``Infinity`` (as ComfyUI writes it): the legacy parser cannot read the
        # graph, so the scan's fallback links nothing and the file is queued.
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "m"},
            "is_changed": [float("inf")],
        },
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
    }
    for index, text in enumerate(positives):
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
    size: tuple[int, int] = (96, 64),
) -> None:
    info = PngInfo()
    if parameters is not None:
        info.add_text("parameters", parameters)
    else:
        info.add_text("prompt", json.dumps(graph or _graph()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, colour) as image:
        image.save(path, pnginfo=info)


def _scan_record(api: PromptManagerAPI, path: Path) -> str:
    """Record ``path`` as the scan's recorder does."""
    from py.api.admin import _scan_roots

    roots = _scan_roots(api._get_all_output_dirs())
    return api._record_review_sync(Path(str(path)), roots, inspection=True)


def _record(api: PromptManagerAPI, path: Path, expected: str) -> None:
    outcome = _scan_record(api, path)
    if outcome != expected:
        raise RuntimeError(f"{path.name} was {outcome}, not {expected}")


def _stamp(path: Path, age: float) -> None:
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


class _ScanRecorder:
    """The scan's recorder over this server's database (one per server)."""

    def __init__(self, api: PromptManagerAPI) -> None:
        self.api = api
        self.lock = threading.Lock()

    def new_output(self, path: Path) -> None:
        """The scan's Needs review recording of a new output, from a worker thread."""
        outcome = []
        with self.lock:
            worker = threading.Thread(
                target=lambda: outcome.append(_scan_record(self.api, path))
            )
            worker.start()
            worker.join()
        if outcome != ["recorded"]:
            raise RuntimeError(f"{path.name} was {outcome}, not recorded")


def _scanned_output(api: PromptManagerAPI, recorder: _ScanRecorder, path: Path) -> None:
    _png(path, (150, 60, 40))
    recorder.new_output(path)


def _linked_after_queueing(api: PromptManagerAPI, path: Path) -> None:
    from utils.hashing import generate_prompt_hash

    _png(path, (150, 60, 40))
    _record(api, path, "recorded")
    prompt = api.db.get_prompt_by_hash(generate_prompt_hash(FALLBACK))
    prompt_id = (
        prompt["id"]
        if prompt is not None
        else api.db.save_prompt(FALLBACK, prompt_hash=generate_prompt_hash(FALLBACK))
    )
    api.db.link_image_to_prompt(prompt_id, str(path))


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
    recorder: _ScanRecorder | None = None,
    concept: bool = False,
) -> dict:
    """Replace this server's disposable rows and files with ``items`` queued files."""
    with api.db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    api.close_annotation_captures()
    for name in ("output", "input"):
        replace_folder(root / name)
    shutil.rmtree(root / "temp" / "review", ignore_errors=True)
    output = root / "output"
    files = []
    for number in range(items):
        relative = f"review/review_{number:04}.png"
        options = {}
        if concept:
            name, size = CONCEPT_IMAGES[number % len(CONCEPT_IMAGES)]
            relative = f"review/{name}" if number < len(CONCEPT_IMAGES) else relative
            graph = _graph(
                positives=(CONCEPT_POSITIVE, POSITIVES[1]), negative=CONCEPT_NEGATIVE
            )
            options = {"graph": graph, "size": size}
        _png(output / relative, (40 + number * 4, 90, 140), **options)
        _stamp(output / relative, 60 + number)
        _record(api, output / relative, "recorded")
        files.append({"path": relative, "filename": Path(relative).name})
    for kind in SPECIAL:
        if kind not in special:
            continue
        relative = f"review/{kind}.png"
        path = (root / "temp" if kind == "unavailable" else output) / relative
        if kind == "conflict":
            _linked_after_queueing(api, path)
        elif kind == "limited":
            many = tuple(f"{POSITIVES[0]} {n}" for n in range(LIMITED_SAMPLERS))
            _png(path, (90, 90, 90), graph=_graph(positives=many))
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
    api: PromptManagerAPI, root: Path, kind: str, recorder: _ScanRecorder
) -> dict:
    """One more ambiguous output: recorded as a scan does, or only written."""
    output = root / "output"
    number = (
        len(list((output / "added").glob("*.png")))
        if (output / "added").exists()
        else 0
    )
    relative = f"added/added_{number:04}.png"
    if kind == "scan":
        _scanned_output(api, recorder, output / relative)
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
    recorders = []

    def recorder() -> _ScanRecorder:
        if not recorders:
            recorders.append(_ScanRecorder(api))
        return recorders[0]

    @routes.post("/__test__/review/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) - {"items", "concept", *SPECIAL}
        ):
            raise web.HTTPBadRequest()
        items = data.get("items", 3)
        if isinstance(items, bool) or not isinstance(items, int):
            raise web.HTTPBadRequest()
        if not 0 <= items <= MAX_ITEMS:
            raise web.HTTPBadRequest()
        if any(
            not isinstance(data.get(kind, False), bool)
            for kind in (*SPECIAL, "concept")
        ):
            raise web.HTTPBadRequest()
        special = tuple(kind for kind in SPECIAL if data.get(kind))
        manifest = await api._run_in_executor(
            seed_review,
            api,
            root,
            items,
            special,
            recorder(),
            data.get("concept", False),
        )
        return web.json_response({"success": True, **manifest})

    @routes.post("/__test__/review/add")
    async def add(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        if request.query or not isinstance(data, dict) or set(data) != {"kind"}:
            raise web.HTTPBadRequest()
        if data["kind"] not in ("scan", "file"):
            raise web.HTTPBadRequest()
        added = await api._run_in_executor(
            add_review_file, api, root, data["kind"], recorder()
        )
        return web.json_response({"success": True, **added})

    @routes.post("/__test__/review/notify")
    async def notify(request: web.Request) -> web.Response:
        if request.query or request.can_read_body:
            raise web.HTTPBadRequest()
        from database.review_changes import notify_review_change

        notify_review_change(api.db.model.db_path)
        return web.json_response({"success": True})

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
