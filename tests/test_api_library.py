"""Native library endpoints use bounded cursors and rooted media identities."""

import json
from unittest.mock import patch

try:
    from test_api_images import ImageAPITestCase, make_png
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png


class TestLibraryAPI(ImageAPITestCase):
    async def get(self, path="", **params):
        response = await self.client.get(
            "/prompt_manager/library" + path, params=params
        )
        return response, await response.json()

    async def test_list_detail_variant_images_and_safe_physical_previews(self):
        pair = self.api.db.save_prompt_pair(
            "Cat", "Noise", title="Original", tags=["studio"]
        )
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        path = make_png(self.output_dir / "same.png")
        image = self.api.db.link_image_to_prompt(positive, str(path))
        self.api.db.link_image_to_pair(pair, image, {"source": "one"})
        self.api.db.link_image_to_pair(pair, image, {"source": "two"})
        response, result = await self.get()
        self.assertEqual(response.status, 200)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["previews"][0]["root_index"], 0)
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))
        _, detail = await self.get(f"/{positive}")
        self.assertEqual(detail["variants"][0]["negative_text"], "Noise")
        self.assertEqual(detail["variants"][0]["id"], pair)
        _, media = await self.get(f"/{positive}/images", pair_id=pair)
        self.assertEqual(media["total"], 1)
        self.assertEqual(len(media["images"]), 1)
        self.assertEqual(media["images"][0]["path"], "same.png")

    async def test_scoped_cursor_tampering_filters_and_continuation(self):
        for text in ("one", "two", "three"):
            self._save_prompt(text)
        _, first = await self.get(limit=1)
        self._save_prompt("later")
        _, second = await self.get(limit=1, cursor=first["next_cursor"])
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        for params in (
            {"cursor": "tampered"},
            {"cursor": first["next_cursor"], "sort": "oldest"},
            {"cursor": first["next_cursor"], "text": "one"},
            {"limit": "no"},
            {"sort": "sql"},
        ):
            response, result = await self.get(**params)
            self.assertEqual(response.status, 400)
            self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_outside_missing_and_unsupported_media_do_not_leak_or_scan(self):
        positive = self._save_prompt("Cat")
        outside = make_png(self.comfy_dir / "outside.png")
        for path in (
            outside,
            self.output_dir / "missing.png",
            self.output_dir / "secret.txt",
        ):
            self.api.db.link_image_to_prompt(positive, str(path))
        with patch.object(
            self.api,
            "_scan_gallery_files_sync",
            side_effect=AssertionError("scan"),
        ):
            _, result = await self.get(f"/{positive}/images", limit=2)
        self.assertEqual(result["images"], [])
        self.assertEqual(result["total"], 3)
        self.assertTrue(result["has_more"])
        self.assertIsInstance(result["next_cursor"], str)
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))
        response, result = await self.get("/9999")
        self.assertEqual(response.status, 404)

    async def test_static_options_route_includes_pair_categories(self):
        self._save_prompt("Legacy", category="Legacy")
        self.api.db.save_prompt_pair("Cat", "Noise", category="Pair-only")
        response, result = await self.get("/options")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            result, {"success": True, "categories": ["Legacy", "Pair-only"]}
        )

    async def test_variant_cursor_is_positive_scoped_and_required_limits_are_bounded(
        self,
    ):
        for index in range(3):
            pair = self.api.db.save_prompt_pair("Cat", f"Neg {index}")
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        other = self._save_prompt("Other")
        _, first = await self.get(f"/{positive}", variant_limit=1)
        self.assertEqual(len(first["variants"]), 1)
        self.assertEqual(first["variant_total"], 3)
        self.assertTrue(first["variants_has_more"])
        _, second = await self.get(
            f"/{positive}",
            variant_limit=1,
            variant_cursor=first["variants_next_cursor"],
        )
        self.assertNotEqual(first["variants"][0]["id"], second["variants"][0]["id"])
        response, _ = await self.get(
            f"/{other}", variant_cursor=first["variants_next_cursor"]
        )
        self.assertEqual(response.status, 400)
        response, _ = await self.get(f"/{positive}", variant_limit=201)
        self.assertEqual(response.status, 400)

    async def test_image_cursors_scope_variants_and_roots_with_real_collisions(
        self,
    ):
        first = self.api.db.save_prompt_pair("Cat", "Blur")
        second = self.api.db.save_prompt_pair("Cat", "Noise")
        positive = self.api.db.get_prompt_pair(first)["positive_prompt_id"]
        other = self.comfy_dir / "input"
        other.mkdir()
        self.api._get_all_output_dirs = lambda: [self.output_dir, other]
        for root in (self.output_dir, other):
            path = make_png(root / "same.png")
            image = self.api.db.link_image_to_prompt(positive, str(path))
            self.api.db.link_image_to_pair(first, image, {"source": "test"})
        _, page = await self.get(f"/{positive}/images", pair_id=first, limit=1)
        self.assertEqual(page["images"][0]["root_index"], 1)
        self.assertTrue(page["has_more"])
        _, next_page = await self.get(
            f"/{positive}/images", pair_id=first, limit=1, cursor=page["next_cursor"]
        )
        self.assertEqual(next_page["images"][0]["root_index"], 0)
        self.assertNotIn(str(self.comfy_dir), json.dumps(next_page))
        response, _ = await self.get(
            f"/{positive}/images", pair_id=second, cursor=page["next_cursor"]
        )
        self.assertEqual(response.status, 400)
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        response, _ = await self.get(
            f"/{positive}/images", pair_id=first, cursor=page["next_cursor"]
        )
        self.assertEqual(response.status, 400)

    async def test_invalid_ids_limits_and_database_failures_are_safe(self):
        positive = self._save_prompt("Cat")
        for path, params in (
            ("/bad", {}),
            ("/-1", {}),
            ("/999999999999999999999999", {}),
            (f"/{positive}/images", {"pair_id": "-1"}),
            (f"/{positive}/images", {"pair_id": "999999999999999999999999"}),
            ("", {"limit": "201"}),
            ("", {"cursor": "x" * 4097}),
        ):
            response, _ = await self.get(path, **params)
            self.assertEqual(response.status, 400, (path, params))
        with patch.object(
            self.api.db, "list_library", side_effect=RuntimeError(str(self.comfy_dir))
        ):
            response, result = await self.get()
        self.assertEqual(response.status, 500)
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_bounded_unicode_name_cursor_and_wildcard_search(self):
        prefix = "猫" * 200
        self._save_prompt(prefix + "B")
        self._save_prompt(prefix + "A")
        self._save_prompt("literal % and _")
        _, first = await self.get(sort="name", limit=1, text=prefix)
        self.assertLess(len(first["next_cursor"]), 4096)
        _, second = await self.get(
            sort="name", limit=1, text=prefix, cursor=first["next_cursor"]
        )
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        _, literal = await self.get(text="%")
        self.assertEqual(
            [item["text"] for item in literal["items"]], ["literal % and _"]
        )
