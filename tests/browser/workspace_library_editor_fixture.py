"""Owned, deterministic Library data for the native recipe editor workflows."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image
from tests.browser.folder_trash import replace_folder

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


EDITOR_POSITIVE = "Editor portrait, soft window light"
FIRST_NEGATIVE = "blurry, watermark"
SELECTED_NEGATIVE = "low contrast, muddy shadows"
ARCHIVED_NEGATIVE = "oversaturated, harsh flash"
STANDALONE = "Standalone sketch without saved negatives"
EDITOR_METADATA = {
    "title": "Editor portrait",
    "category": "Portraits",
    "tags": ["studio", "window light"],
    "rating": 4,
    "notes": "Keep the soft light.",
}
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


def seed_library_editor(api: PromptManagerAPI, root: Path) -> dict:
    """Replace this server's disposable rows with one fixed editor Library."""
    db = api.db
    with db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        replace_folder(root / name)
    epoch = db.get_execution_epoch()
    receipt = db.create_library_recipe(
        EDITOR_POSITIVE,
        FIRST_NEGATIVE,
        expected_epoch=epoch,
        parent_metadata=dict(EDITOR_METADATA),
    )
    positive_id = receipt["positive"]["id"]
    first_pair_id = receipt["pair"]["id"]
    pairs = {}
    for name, text in (
        ("pair_id", SELECTED_NEGATIVE),
        ("empty_pair_id", ""),
        ("archived_pair_id", ARCHIVED_NEGATIVE),
    ):
        receipt = db.add_library_negative(
            positive_id,
            text,
            expected_epoch=epoch,
            if_match=receipt["positive"]["edit_token"],
        )
        pairs[name] = receipt["pair"]["id"]
    db.archive_prompt_pair(pairs["archived_pair_id"], True)
    image_path = root / "output" / "editor" / "portrait.png"
    image_path.parent.mkdir()
    with Image.new("RGB", (64, 64), (40, 90, 120)) as image:
        image.save(image_path)
    image_id = db.link_image_to_prompt(positive_id, str(image_path))
    db.link_image_to_pair(pairs["pair_id"], image_id, {"source": "editor fixture"})
    db.record_prompt_pair_use(pairs["pair_id"], times=2)
    standalone_id = db.save_prompt(STANDALONE, category="Sketches", tags=["draft"])
    with db.model.get_connection() as conn:
        negative_only_id = conn.execute(
            "SELECT negative_prompt_id FROM prompt_pairs WHERE id=?",
            (first_pair_id,),
        ).fetchone()[0]
    api.invalidate_gallery_cache()
    return {
        "positive_id": positive_id,
        "first_pair_id": first_pair_id,
        **pairs,
        "standalone_id": standalone_id,
        "negative_only_id": negative_only_id,
        "positive_text": EDITOR_POSITIVE,
        "first_negative": FIRST_NEGATIVE,
        "selected_negative": SELECTED_NEGATIVE,
        "archived_negative": ARCHIVED_NEGATIVE,
        "metadata": dict(EDITOR_METADATA),
    }


class _LibraryEditorCommands:
    """One disposable fixture lifetime; an ordinary reset expires it."""

    def __init__(self) -> None:
        self.manifest: dict | None = None

    def reset(self) -> None:
        self.manifest = None


def register_library_editor_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> _LibraryEditorCommands:
    """Register only fixed seed and snapshot commands; no paths or SQL input."""
    state = _LibraryEditorCommands()

    @routes.post("/__test__/library-editor/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if data != {} or request.query:
            raise web.HTTPBadRequest()
        state.manifest = await api._run_in_executor(seed_library_editor, api, root)
        return web.json_response({"success": True, **state.manifest})

    @routes.get("/__test__/library-editor/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        if state.manifest is None or request.query:
            raise web.HTTPConflict()
        path = root / "owned-library-editor-snapshot.db"
        created = await api._run_in_executor(api.db.model.backup_database, str(path))
        if not created:
            raise web.HTTPInternalServerError()
        return web.Response(
            body=path.read_bytes(), content_type="application/octet-stream"
        )

    return state
