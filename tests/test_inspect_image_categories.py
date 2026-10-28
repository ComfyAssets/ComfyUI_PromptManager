"""The inspector and History show an image's own category (#332 §5, DC4, DC5)."""

import os
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_api_history_outputs as history
from tests.test_api_images import make_png
from py.api.images import _favorite_root_key, image_category_key
from utils.inspection_response import (
    OWN_CATEGORY_UNREAD,
    _library_projection,
    apply_own_category,
)


class InspectCase(history.ImageAPITestCase):
    async def tearDownAsync(self):
        self.api.db.close_all()
        await super().tearDownAsync()

    def own(self, path, value, sources=None):
        sources = sources or self.api._get_all_output_dirs()
        self.api.db.set_image_categories([image_category_key(sources, path)], value)

    async def library(self, path, root="0"):
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{path}", params={"root": root}
        )
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        return body["library"]


class TestInspectorOwnCategory(InspectCase):
    async def test_inspect_reports_image_scope(self):
        path = make_png(self.output_dir / "image.png")
        self.own(path, "Portraits")
        library = await self.library("image.png")
        self.assertEqual(library["status"], "none")
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Portraits", "image")
        )

    async def test_own_overrides_linked_recipe_and_ambiguous(self):
        db = self.api.db
        linked = make_png(self.output_dir / "linked.png")
        self._link_image(linked, "castle at dusk")
        prompt = db.get_prompt_id_for_image(str(linked))
        with db.model.get_connection() as conn:
            conn.execute(
                "UPDATE prompts SET category='Landscapes' WHERE id=?", (prompt,)
            )
        library = await self.library("linked.png")
        self.assertEqual(
            (library["category"], library["category_scope"]),
            ("Landscapes", "positive"),
        )
        self.own(linked, "Portraits")
        library = await self.library("linked.png")
        self.assertEqual(library["status"], "linked")
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Portraits", "image")
        )
        recipe = make_png(self.output_dir / "recipe.png")
        self._save_prompt("Pair positive", category="Night")
        pair = db.save_prompt_pair("Pair positive", "Noise", category="Recipes")
        positive = db.get_prompt_pair(pair)["positive_prompt_id"]
        image = db.link_image_to_prompt(positive, str(recipe))
        db.link_image_to_pair(pair, image, {"source": "fixture"})
        library = await self.library("recipe.png")
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Recipes", "pair")
        )
        self.own(recipe, "Mine")
        library = await self.library("recipe.png")
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Mine", "image")
        )
        ambiguous = make_png(self.output_dir / "ambiguous.png")
        self._link_image(ambiguous, "owner one")
        self._link_image(ambiguous, "owner two")
        self.own(ambiguous, "Scans")
        library = await self.library("ambiguous.png")
        self.assertEqual(library["status"], "ambiguous")
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Scans", "image")
        )

    async def test_a_failed_read_hides_the_category(self):
        linked = make_png(self.output_dir / "linked.png")
        self._link_image(linked, "castle at dusk")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("secret")

        with patch.object(self.api.db, "image_category", side_effect=broken):
            with self.assertLogs("prompt_manager", level="WARNING"):
                library = await self.library("linked.png")
        self.assertEqual(library["status"], "linked")
        self.assertIsNone(library["category"])
        self.assertIsNone(library["category_scope"])

    async def test_a_lora_root_reads_no_own_category(self):
        lora = self.comfy_dir / "loras"
        make_png(lora / "preview.png")
        roots = [self.output_dir, lora]
        reads = []
        real = self.api.db.image_category

        def recorded(*args):
            reads.append(args)
            return real(*args)

        with (
            patch.object(self.api, "_media_roots", lambda outputs: roots),
            patch.object(self.api, "_linked_only_error", self._no_error),
            patch.object(self.api.db, "image_category", side_effect=recorded),
        ):
            response = await self.client.get(
                "/prompt_manager/images/inspect/preview.png", params={"root": "1"}
            )
            body = await response.json()
        self.assertEqual(response.status, 200, body)
        self.assertEqual(reads, [])
        self.assertIsNone(body["library"]["category"])

    @staticmethod
    async def _no_error(*_args):
        return None


class TestProjection(unittest.TestCase):
    def test_scope_image_is_kept_for_category_only(self):
        base = {"status": "none", "scope": "saved-library", "tags": []}
        projected = _library_projection(
            {**base, "category": "Scans", "category_scope": "image"}
        )
        self.assertEqual(projected["category_scope"], "image")
        projected = _library_projection({**base, "tags_scope": "image"})
        self.assertNotIn("tags_scope", projected)

    def test_apply_own_category_three_cases(self):
        library = {"status": "linked", "category": "L", "category_scope": "positive"}
        self.assertEqual(
            apply_own_category(library, "Mine"),
            {"status": "linked", "category": "Mine", "category_scope": "image"},
        )
        self.assertEqual(apply_own_category(library, None), library)
        self.assertEqual(
            apply_own_category(library, OWN_CATEGORY_UNREAD),
            {"status": "linked", "category": None, "category_scope": None},
        )
        self.assertEqual(library["category"], "L")  # never mutated


class TestHistoryOwnCategory(history.ImageAPITestCase):
    """Only the inspection request reads the own category, keyed as Images."""

    # The History fixture's helpers, without inheriting its tests.
    _base = history.HistoryOutputAPITests
    ref = _base.ref
    capture = _base.capture
    outputs = _base.outputs
    current = _base.current
    media = _base.media

    async def get_application(self):
        app = await super().get_application()
        self.temp_dir = self.comfy_dir / "temp"
        self.temp_dir.mkdir()
        self.host_output = str(self.output_dir)
        self.host_temp = str(self.temp_dir)
        self.patches = history.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in (
            ("folder_paths.get_output_directory", lambda: self.host_output),
            ("folder_paths.get_temp_directory", lambda: self.host_temp),
        ):
            self.patches.enter_context(patch(name, value, create=True))
        self.patches.enter_context(
            patch.object(
                history.GalleryConfig,
                "allowed_gallery_parents",
                lambda: [str(self.comfy_dir)],
            )
        )
        return app

    async def tearDownAsync(self):
        self.api.close_history_reads()
        self.patches.close()
        await super().tearDownAsync()

    async def inspect(self, start):
        descriptor = await self.media(start)
        response = await self.current(descriptor["inspection_url"])
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        return body["library"]

    async def test_history_inspection_shows_own_category(self):
        path = make_png(self.output_dir / "image.png")
        self.api.db.set_image_categories(
            [image_category_key([self.output_dir], path)], "Portraits"
        )
        start = self.capture([self.ref()])
        library = await self.inspect(start)
        self.assertEqual(
            (library["category"], library["category_scope"]), ("Portraits", "image")
        )

    async def test_nested_sources_give_the_images_key(self):
        inner = self.output_dir / "nested"
        path = make_png(inner / "image.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, inner]
        self.api.db.set_image_categories(
            [(_favorite_root_key(inner), "image.png")], "S"
        )
        start = self.capture([self.ref(subfolder="nested")])
        library = await self.inspect(start)
        self.assertEqual(library["category"], "S")
        self.assertEqual(
            image_category_key([self.output_dir, inner], path),
            (_favorite_root_key(inner), "image.png"),
        )

    async def test_a_symlinked_output_folder_still_shows_it(self):
        real = self.comfy_dir / "real-output"
        real.mkdir()
        link = self.comfy_dir / "linked-output"
        self._make_symlink(real, link, target_is_directory=True)
        make_png(real / "image.png")
        self.host_output = str(link)
        self.api._get_all_output_dirs = lambda: [real]
        self.api.db.set_image_categories([(_favorite_root_key(real), "image.png")], "S")
        start = self.capture([self.ref()])
        library = await self.inspect(start)
        self.assertEqual(library["category"], "S")

    async def test_outputs_and_media_read_no_own_category(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        reads = []
        real = self.api.db.image_category

        def recorded(*args):
            reads.append(args)
            return real(*args)

        with patch.object(self.api.db, "image_category", side_effect=recorded):
            descriptor = await self.media(start)
            for field in ("url", "thumbnail_url"):
                response = await self.current(descriptor[field])
                await response.read()
                self.assertEqual(response.status, 200)
            self.assertEqual(reads, [])
            await self.current(descriptor["inspection_url"])
            self.assertEqual(len(reads), 1)

    async def test_a_temp_output_has_none(self):
        reference = {"folder_type": "temp", "subfolder": "", "filename": "a.png"}
        self.assertIsNone(self.api._history_own_category(reference, None))

    async def test_a_failed_read_hides_it(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])

        def broken(*_args):
            raise sqlite3.OperationalError("secret")

        with patch.object(self.api.db, "image_category", side_effect=broken):
            with self.assertLogs("prompt_manager", level="WARNING"):
                library = await self.inspect(start)
        self.assertIsNone(library["category"])
        self.assertTrue(os.path.exists(self.output_dir / "image.png"))
