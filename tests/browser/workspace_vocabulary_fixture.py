"""Owned, deterministic vocabulary data for the native Tags & categories workflows.

The six recipes are the approved concept's (docs/concepts/workspace/app.js:71-165)
in concept order, so tag creation order and category first use match the mock.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from tests.browser.folder_trash import replace_folder

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


CONCEPT_RECIPES = (
    {
        "title": "Alpine observatory",
        "category": "Landscapes",
        "tags": ["cinematic", "blue_hour", "architecture"],
        "positive": "A remote alpine observatory at blue hour, warm light through "
        "glass, dramatic mountain peaks, cinematic composition, atmospheric mist, "
        "fine detail",
        "negative": "blurry, low quality, low resolution",
    },
    {
        "title": "Quiet geometry",
        "category": "Architecture",
        "tags": ["minimal", "warm_light", "editorial"],
        "positive": "Monolithic sandstone pavilion in a vast desert, sweeping dunes, "
        "warm terracotta, late afternoon sun, architectural photography, quiet "
        "geometry",
        "negative": "plastic, harsh shadows, noise, watermark",
    },
    {
        "title": "Somewhere in the pines",
        "category": "Landscapes",
        "tags": ["forest", "mist", "peaceful"],
        "positive": "A quiet cedar cabin above a still forest lake, towering "
        "evergreens, soft morning mist, muted jade and moss, a small amber window, "
        "cinematic still",
        "negative": "oversaturated, blurry, low quality, text",
    },
    {
        "title": "After the rain",
        "category": "Environments",
        "tags": ["neon", "night", "cinematic"],
        "positive": "Rain-soaked city street at night, blue neon and rose "
        "reflections, deep perspective, cinematic science fiction, solitary "
        "architecture, detailed atmosphere",
        "negative": "blurry, low resolution, watermark, oversaturated",
    },
    {
        "title": "The long way home",
        "category": "Illustration",
        "tags": ["space", "minimal", "concept_art"],
        "positive": "An isolated lunar research outpost, a luminous planet above the "
        "horizon, sweeping pale terrain, quiet science fiction, finely detailed "
        "concept art",
        "negative": "blurry, low quality, watermark",
    },
    {
        "title": "Between sea and sky",
        "category": "Landscapes",
        "tags": ["coast", "soft_light", "peaceful"],
        "positive": "A solitary lighthouse on a rugged coastal cliff, sea mist, "
        "glints of morning light on turquoise water, atmospheric perspective, fine "
        "art landscape",
        "negative": "oversaturated, flat lighting, watermark, text",
    },
)
PAIR_ONLY_TAG = "pair_only_detail"
ARCHIVED_ONLY_TAG = "archived_pair_detail"
NEGATIVE_ONLY_TAG = "legacy_negative_detail"
UNUSED_TAG = "unused_vocabulary"
MAX_EXTRA_TAGS = 300
MAX_EXTRA_CATEGORIES = 200
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
# Every owner row a vocabulary command could touch, in a stable order, for
# before/after comparisons; prompt text, pairs and images must never change.
_SNAPSHOT = {
    "tags": "SELECT id, name FROM tags ORDER BY id",
    "prompt_tags": "SELECT prompt_id, tag_id FROM prompt_tags ORDER BY 1, 2",
    "prompt_pair_tags": "SELECT pair_id, tag_id FROM prompt_pair_tags ORDER BY 1, 2",
    "prompts": "SELECT id, text, category, tags, updated_at FROM prompts ORDER BY id",
    "prompt_pairs": "SELECT id, positive_prompt_id, negative_prompt_id, category,"
    " archived, updated_at FROM prompt_pairs ORDER BY id",
    "generated_images": "SELECT id, prompt_id, image_path FROM generated_images"
    " ORDER BY id",
    "image_prompt_pairs": "SELECT id, image_id, pair_id FROM image_prompt_pairs"
    " ORDER BY id",
}


def seed_vocabulary(
    api: PromptManagerAPI, root: Path, extra_tags: int = 0, extra_categories: int = 0
) -> dict:
    """Replace this server's disposable rows with the concept vocabulary."""
    db = api.db
    with db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        replace_folder(root / name)
    epoch = db.get_execution_epoch()
    recipes = []
    for recipe in CONCEPT_RECIPES:
        receipt = db.create_library_recipe(
            recipe["positive"],
            recipe["negative"],
            expected_epoch=epoch,
            parent_metadata={
                "title": recipe["title"],
                "category": recipe["category"],
                "tags": list(recipe["tags"]),
            },
        )
        recipes.append(
            {"positive_id": receipt["positive"]["id"], "pair_id": receipt["pair"]["id"]}
        )
    first = CONCEPT_RECIPES[0]["positive"]
    # A tag carried only by an active saved pair counts its Library parent.
    db.save_prompt_pair(first, "grain, banding", tags=[PAIR_ONLY_TAG])
    # A tag carried only by an archived pair is hidden from Library Browse.
    archived = db.save_prompt_pair(
        CONCEPT_RECIPES[1]["positive"], "lens flare", tags=[ARCHIVED_ONLY_TAG]
    )
    db.archive_prompt_pair(archived, True)
    # A legacy tag on a negative-only component is never a Library parent.
    with db.model.get_connection() as conn:
        negative_id = conn.execute(
            "SELECT negative_prompt_id FROM prompt_pairs WHERE id=?",
            (recipes[2]["pair_id"],),
        ).fetchone()[0]
    db.update_prompt_metadata(negative_id, tags=[NEGATIVE_ONLY_TAG])
    db.create_vocabulary_tag(UNUSED_TAG, expected_epoch=epoch)
    for number in range(extra_tags):
        db.create_vocabulary_tag(f"bulk_tag_{number:04}", expected_epoch=epoch)
    # Each extra category is carried by one minimal Library recipe, after the concept's.
    for number in range(extra_categories):
        db.create_library_recipe(
            f"Bulk category recipe {number:04}",
            "",
            expected_epoch=epoch,
            parent_metadata={
                "title": f"Bulk {number:04}",
                "category": f"bulk_category_{number:04}",
            },
        )
    with db.model.get_connection() as conn:
        tags = [row[0] for row in conn.execute("SELECT name FROM tags ORDER BY id")]
    api.invalidate_gallery_cache()
    return {
        "epoch": epoch,
        "recipes": recipes,
        "archived_pair_id": archived,
        "negative_only_id": negative_id,
        "tags": tags,
        "pair_only_tag": PAIR_ONLY_TAG,
        "archived_only_tag": ARCHIVED_ONLY_TAG,
        "negative_only_tag": NEGATIVE_ONLY_TAG,
        "unused_tag": UNUSED_TAG,
    }


def vocabulary_snapshot(api: PromptManagerAPI) -> dict:
    """Plain rows of every vocabulary owner table."""
    with api.db.model.get_connection() as conn:
        return {
            name: [list(row) for row in conn.execute(query)]
            for name, query in _SNAPSHOT.items()
        }


def register_vocabulary_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> None:
    """Register only fixed seed, snapshot and backup commands; no paths or SQL input."""

    @routes.post("/__test__/vocabulary/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        if not isinstance(data, dict) or set(data) - {"extra_tags", "extra_categories"}:
            raise web.HTTPBadRequest()
        extra = data.get("extra_tags", 0)
        categories = data.get("extra_categories", 0)
        for value, limit in (
            (extra, MAX_EXTRA_TAGS),
            (categories, MAX_EXTRA_CATEGORIES),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise web.HTTPBadRequest()
            if not 0 <= value <= limit:
                raise web.HTTPBadRequest()
        if request.query:
            raise web.HTTPBadRequest()
        manifest = await api._run_in_executor(
            seed_vocabulary, api, root, extra, categories
        )
        return web.json_response({"success": True, **manifest})

    @routes.get("/__test__/vocabulary/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        if request.query:
            raise web.HTTPBadRequest()
        rows = await api._run_in_executor(vocabulary_snapshot, api)
        return web.json_response(rows)

    @routes.get("/__test__/vocabulary/backup")
    async def backup(request: web.Request) -> web.Response:
        """A database copy for the real restore route, which rotates the epoch."""
        if request.query:
            raise web.HTTPBadRequest()
        path = root / "owned-vocabulary-backup.db"
        created = await api._run_in_executor(api.db.model.backup_database, str(path))
        if not created:
            raise web.HTTPInternalServerError()
        return web.Response(
            body=path.read_bytes(), content_type="application/octet-stream"
        )
