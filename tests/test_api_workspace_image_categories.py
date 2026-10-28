"""Images filters by the effective category (#332 MC4, MC5, DC6).

An image's effective category is its own category when it has one, else the
category its link supplies (the recipe's when set, else the positive's), else
none. The category filter, "select all N matching" and saved views match it.
"""

import os
import sqlite3
from unittest.mock import patch

try:
    import test_api_workspace_images as workspace
    from test_image_categories import category_checksum
except ImportError:
    from tests import test_api_workspace_images as workspace
    from tests.test_image_categories import category_checksum

from py.api.images import image_category_key, parse_listing_filter


class WorkspaceCategoryCase(workspace.ImageAPITestCase):
    """Listing helpers plus own-category writes keyed as the routes key them."""

    media = workspace.TestWorkspaceImages.media
    get = workspace.TestWorkspaceImages.get
    tag = workspace.TestWorkspaceImageTags.tag
    names = workspace.TestWorkspaceImageTags.names

    async def tearDownAsync(self):
        self.api.db.close_all()
        workspace.assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def own(self, path, value, sources=None):
        sources = sources or self.api._get_all_output_dirs()
        self.api.db.set_image_categories([image_category_key(sources, path)], value)

    async def listed(self, **params):
        response, body = await self.get(sort="name", **params)
        self.assertEqual(response.status, 200, body)
        return self.names(body), body


class TestEffectiveCategoryFilter(WorkspaceCategoryCase):
    def batch(self):
        """Four images of "castle at dusk" (Landscapes); one is overridden."""
        castle = self._save_prompt("castle at dusk", category="Landscapes", rating=4)
        files = [self.media(f"castle_{n}.png", mtime=100 + n) for n in range(4)]
        for path in files:
            self.api.db.link_image_to_prompt(castle, str(path))
        self.own(files[0], "Portraits")
        return castle, files

    async def test_category_filter_prefers_own_category(self):
        castle, _files = self.batch()
        conn = self.api.db.model.get_connection()
        before = category_checksum(conn)
        names, body = await self.listed(category="Portraits")
        self.assertEqual(names, ["castle_0.png"])
        # The caption still counts the prompt behind the image shown.
        self.assertEqual(body["positive_count"], 1)
        names, body = await self.listed(category="Landscapes")
        self.assertEqual(names, ["castle_1.png", "castle_2.png", "castle_3.png"])
        self.assertEqual(body["total"], 3)
        names, _ = await self.listed()
        self.assertEqual(len(names), 4)
        self.assertEqual(category_checksum(conn), before)
        self.assertEqual(self.api.db.get_prompt_by_id(castle)["category"], "Landscapes")

    async def test_category_filter_lists_unlinked_with_own(self):
        scans = [self.media(f"scan_{n}.png", mtime=100 + n) for n in range(2)]
        self.media("plain.png", mtime=50)
        for path in scans:
            self.own(path, "Scans")
        names, body = await self.listed(category="Scans")
        self.assertEqual(names, ["scan_0.png", "scan_1.png"])
        self.assertEqual(body["positive_count"], 0)

    async def test_recipe_category_is_inherited(self):
        """MC4: a recipe's own category, not the positive's, is inherited."""
        db = self.api.db
        self._save_prompt("Pair positive", category="Night")
        pair = db.save_prompt_pair("Pair positive", "Noise", category="Recipes")
        blank = db.save_prompt_pair("Pair positive", "Other noise", category="  ")
        recipe = self.media("recipe.png", mtime=103)
        blank_recipe = self.media("blank_recipe.png", mtime=102)
        positive = db.get_prompt_pair(pair)["positive_prompt_id"]
        for path, pair_id in ((recipe, pair), (blank_recipe, blank)):
            image = db.link_image_to_prompt(positive, str(path))
            db.link_image_to_pair(pair_id, image, {"source": "fixture"})
        self.assertEqual((await self.listed(category="Recipes"))[0], ["recipe.png"])
        # A blank recipe category falls back to the positive's.
        self.assertEqual((await self.listed(category="Night"))[0], ["blank_recipe.png"])
        # An own category still wins over the recipe's.
        self.own(recipe, "Mine")
        self.assertEqual((await self.listed(category="Recipes"))[0], [])
        self.assertEqual((await self.listed(category="Mine"))[0], ["recipe.png"])

    async def test_ambiguous_files_inherit_nothing_but_keep_their_own(self):
        first = self._save_prompt("Owner one", category="Night")
        second = self._save_prompt("Owner two", category="Night")
        ambiguous = self.media("ambiguous.png")
        for owner in (first, second):
            self.api.db.link_image_to_prompt(owner, str(ambiguous))
        self.assertEqual((await self.listed(category="Night"))[0], [])
        self.own(ambiguous, "Night")
        self.assertEqual((await self.listed(category="Night"))[0], ["ambiguous.png"])

    async def test_combines_with_tags_search_and_rating(self):
        stone = self._save_prompt(
            "castle at dusk", category="Landscapes", tags=["stone"], rating=4
        )
        files = [self.media(f"castle_{n}.png", mtime=100 + n) for n in range(2)]
        for path in files:
            self.api.db.link_image_to_prompt(stone, str(path))
        self.own(files[0], "Portraits")
        other = self._save_prompt("harbor at noon", category="Portraits", rating=2)
        harbor = self.media("harbor.png", mtime=90)
        self.api.db.link_image_to_prompt(other, str(harbor))
        names, _ = await self.listed(category="Portraits", tags="stone")
        self.assertEqual(names, ["castle_0.png"])
        names, _ = await self.listed(category="Portraits", q="harbor")
        self.assertEqual(names, ["harbor.png"])
        names, _ = await self.listed(category="Portraits", q="castle_0")
        self.assertEqual(names, ["castle_0.png"])
        response, body = await self.get(category="Portraits", sort="rating")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(self.names(body), ["castle_0.png", "harbor.png"])
        self.assertEqual(body["positive_count"], 2)

    async def test_image_tag_admitted_file_keeps_its_inherited_category(self):
        night = self._save_prompt("Night owl", category="Night", tags=["a"])
        tagged = self.media("tagged.png", mtime=103)
        unlinked = self.media("unlinked.png", mtime=102)
        self.api.db.link_image_to_prompt(night, str(tagged))
        self.tag(tagged, ["b"])
        self.tag(unlinked, ["a", "b"])
        names, _ = await self.listed(tags="a,b", category="Night")
        self.assertEqual(names, ["tagged.png"])
        self.own(unlinked, "Night")
        names, _ = await self.listed(tags="a,b", category="Night")
        self.assertEqual(names, ["tagged.png", "unlinked.png"])

    async def test_cursor_pages_walk_only_the_category(self):
        files = [self.media(f"img_{n:02}.png", mtime=100 + n) for n in range(7)]
        for path in files[::2]:
            self.own(path, "Even")
        seen, cursor = [], None
        while True:
            params = {"category": "Even", "limit": 2, "sort": "name"}
            if cursor:
                params["cursor"] = cursor
            response, body = await self.get(**params)
            self.assertEqual(response.status, 200, body)
            seen += self.names(body)
            self.assertEqual(body["total"], 4)
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, ["img_00.png", "img_02.png", "img_04.png", "img_06.png"])

    async def test_an_outer_source_lists_a_nested_sources_own_category(self):
        inner = self.output_dir / "nested"
        nested = self.media("nested/a.png")
        self.media("b.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, inner]
        self.own(nested, "Scans")
        names, _ = await self.listed(category="Scans", root="0")
        self.assertEqual(names, ["nested/a.png"])
        names, _ = await self.listed(category="Scans", root="1")
        self.assertEqual(names, ["a.png"])
        # The outer source's own key for that path is not the file's key.
        from py.api.images import _favorite_root_key

        self.api.db.clear_image_categories([image_category_key([inner], nested)])
        self.api.db.set_image_categories(
            [(_favorite_root_key(self.output_dir), "nested/a.png")], "Stale"
        )
        self.assertEqual((await self.listed(category="Stale"))[0], [])

    async def test_a_gone_files_category_is_not_listed(self):
        gone = self.media("gone.png")
        self.own(gone, "Scans")
        os.unlink(gone)
        self.api.invalidate_gallery_cache()
        self.assertEqual((await self.listed(category="Scans"))[0], [])

    async def test_a_failed_own_category_read_is_listing_failed(self):
        self.media("a.png")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("secret detail")

        with patch.object(self.api.db, "own_categories_of", side_effect=broken):
            with self.assertLogs("prompt_manager", level="ERROR"):
                response, body = await self.get(category="Scans")
            self.assertEqual(response.status, 500)
            self.assertNotIn("secret", str(body))
            # Without a category nothing new is read.
            response, body = await self.get()
            self.assertEqual(response.status, 200, body)

    async def test_select_all_matching_uses_the_effective_category(self):
        _castle, files = self.batch()
        resolved = await self.api.resolve_listing_filter(
            parse_listing_filter({"category": "Portraits"}), 100
        )
        self.assertEqual([t[2] for t in resolved.targets], ["castle_0.png"])
        resolved = await self.api.resolve_listing_filter(
            parse_listing_filter({"category": "Landscapes"}), 100
        )
        self.assertEqual(len(resolved.targets), 3)
        self.assertEqual(len(files), 4)

    async def test_saved_view_category_means_the_effective_category(self):
        """CUJ 8: a saved view's category is the listing's query value."""
        _castle, files = self.batch()
        self.own(files[1], "Portraits")
        names, _ = await self.listed(category="Portraits")
        self.assertEqual(names, ["castle_0.png", "castle_1.png"])


class TestFilterAndChipAgreeOnBlanks(WorkspaceCategoryCase):
    """The filter and the inspector use one inherited-category rule."""

    async def chip(self, path):
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{path}", params={"root": "0"}
        )
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        return body["library"]["category"]

    async def test_blank_recipe_values_agree(self):
        db = self.api.db
        positive = self._save_prompt("Pair positive", category="Night")
        blanks = {"spaces": "   ", "tab": "\t", "nbsp": " ", "set": "Recipes"}
        for index, (label, value) in enumerate(blanks.items()):
            pair = db.save_prompt_pair("Pair positive", f"noise {label}")
            path = self.media(f"{label}.png", mtime=100 + index)
            image = db.link_image_to_prompt(positive, str(path))
            db.link_image_to_pair(pair, image, {"source": "fixture"})
            with db.model.get_connection() as conn:
                conn.execute(
                    "UPDATE prompt_pairs SET category=? WHERE id=?", (value, pair)
                )
        blob = db.save_prompt_pair("Pair positive", "noise blob")
        path = self.media("blob.png", mtime=90)
        image = db.link_image_to_prompt(positive, str(path))
        db.link_image_to_pair(blob, image, {"source": "fixture"})
        with db.model.get_connection() as conn:
            conn.execute(
                "UPDATE prompt_pairs SET category=CAST(' ' AS BLOB) WHERE id=?",
                (blob,),
            )
        for name in ("spaces", "tab", "nbsp", "set", "blob"):
            with self.subTest(name=name):
                shown = await self.chip(f"{name}.png")
                for value in ("Night", "Recipes"):
                    listed = f"{name}.png" in (await self.listed(category=value))[0]
                    self.assertEqual(listed, shown == value, (name, value, shown))
