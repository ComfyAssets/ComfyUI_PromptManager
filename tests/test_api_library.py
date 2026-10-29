"""Native library endpoints use bounded cursors and rooted media identities."""

import json
import os
import unittest
from unittest.mock import patch

from utils.hashing import generate_prompt_hash

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

    async def test_untagged_tag_mode_lists_parents_without_tags(self):
        self._save_prompt("plain one")
        self._save_prompt("plain two")
        self._save_prompt("tagged", tags=["t"])
        self.api.db.save_prompt_pair("pair tagged", "noise", tags=["p"])
        _, first = await self.get(limit=1, tag_mode="untagged")
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["tag_mode"], "untagged")
        _, second = await self.get(
            limit=1, tag_mode="untagged", cursor=first["next_cursor"]
        )
        self.assertEqual(
            sorted([first["items"][0]["text"], second["items"][0]["text"]]),
            ["plain one", "plain two"],
        )
        _, default = await self.get(tag_mode="all")
        self.assertEqual(default["total"], 4)
        for params in (
            {"tag_mode": "untagged", "tag": "t"},
            {"tag_mode": "sql"},
            {"cursor": first["next_cursor"]},
        ):
            response, result = await self.get(limit=1, **params)
            self.assertEqual(response.status, 400, params)
            self.assertEqual(result["success"], False)

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
            result,
            {
                "success": True,
                "library_epoch": self.api.db.get_execution_epoch(),
                "categories": ["Legacy", "Pair-only"],
            },
        )

    async def test_folder_filter_lists_and_matches_prompts_by_output_folder(self):
        deep = self._save_prompt("deep")
        top = self._save_prompt("top")
        other = self._save_prompt("other")
        self._save_prompt("no images")
        for prompt, parts in ((deep, ("a", "b")), (top, ("a",)), (other, ("ab",))):
            path = make_png(self.output_dir.joinpath(*parts, f"{prompt}.png"))
            self.api.db.link_image_to_prompt(prompt, str(path))
        response, options = await self.get("/options", include="folders")
        self.assertEqual(response.status, 200)
        self.assertEqual(options["folders"], ["a", "a/b", "ab"])
        _, plain = await self.get("/options")
        self.assertNotIn("folders", plain)
        _, first = await self.get(folder="a", limit=1)
        self.assertEqual((first["total"], first["folder"]), (2, "a"))
        _, second = await self.get(folder="\\a\\", limit=1, cursor=first["next_cursor"])
        self.assertEqual(
            sorted([first["items"][0]["text"], second["items"][0]["text"]]),
            ["deep", "top"],
        )
        _, all_rows = await self.get()
        self.assertEqual((all_rows["total"], all_rows["folder"]), (4, ""))
        for params in (
            {"folder": "a/../ab"},
            {"folder": "C:/x"},
            {"folder": "b", "cursor": first["next_cursor"]},
            {"cursor": first["next_cursor"]},
        ):
            response, result = await self.get(limit=1, **params)
            self.assertEqual(response.status, 400, params)
            self.assertEqual(result["success"], False)
        response, _ = await self.get("/options", include="tags")
        self.assertEqual(response.status, 400)

    async def test_any_tag_mode_is_sent_through_and_echoed(self):
        self._save_prompt("a only", tags=["a"])
        self._save_prompt("b only", tags=["b"])
        self._save_prompt("neither")
        _, result = await self.get(tag=["a", "b"], tag_mode="any")
        self.assertEqual(result["tag_mode"], "any")
        self.assertEqual(
            sorted(i["text"] for i in result["items"]), ["a only", "b only"]
        )
        _, both = await self.get(tag=["a", "b"])
        self.assertEqual((both["total"], both["tag_mode"]), (0, "all"))
        _, first = await self.get(tag=["a", "b"], tag_mode="any", limit=1)
        response, _ = await self.get(
            tag=["a", "b"], limit=1, cursor=first["next_cursor"]
        )
        self.assertEqual(response.status, 400)
        response, _ = await self.get(tag_mode="or")
        self.assertEqual(response.status, 400)

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
            self.api.db, "_list_library", side_effect=RuntimeError(str(self.comfy_dir))
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

    async def test_maximum_parent_page_uses_bounded_sql_queries_for_previews(self):
        for index in range(200):
            positive = self._save_prompt(f"Parent {index}", tags=[f"tag {index}"])
            if index % 3 == 0:
                self.api.db.save_prompt_pair(f"Parent {index}", f"Negative {index}")
            if index % 4 == 0:
                self.api.db.record_prompt_use(positive, times=index + 1)
            make_png(self.output_dir / f"{index}.png")
            self.api.db.link_image_to_prompt(
                positive, str(self.output_dir / f"{index}.png")
            )
            with self.api.db.model.get_connection() as connection:
                connection.execute(
                    "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                    (positive, f"Title {index}"),
                )
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.api.db.model, "get_connection", side_effect=trace_connection
        ):
            response, result = await self.get(limit=200)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(result["items"]), 200)
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        self.assertLessEqual(len(selects), 5)
        epoch = self.api.db.get_execution_epoch()
        self.assertEqual(result["library_epoch"], epoch)
        self.assertEqual(
            [query for query in selects if "storage_epoch" in query], selects[:1]
        )
        self.assertIn("MAX(", selects[0].upper())
        self.assertTrue(
            all(
                item["title"].startswith("Title ")
                and len(item["tags"]) == 1
                and len(item["previews"]) == 1
                and len(item["edit_token"]) == 64
                for item in result["items"]
            )
        )
        # Usage fields and the variant total ride on the same five SELECTs.
        self.assertEqual(result["variant_total"], 67)
        self.assertEqual(
            result["variant_total"],
            sum(item["variant_count"] for item in result["items"]),
        )
        used = {
            int(item["text"].split()[-1]): item
            for item in result["items"]
            if item["last_used_at"] is not None
        }
        self.assertEqual(sorted(used), list(range(0, 200, 4)))
        self.assertTrue(
            all(
                item["run_count"] == index + 1
                and item["last_used_at"].endswith("+00:00")
                for index, item in used.items()
            )
        )
        self.assertTrue(
            all(
                type(item["run_count"]) is int
                for item in result["items"]
                if item["last_used_at"] is None
            )
        )

    async def test_long_name_cursor_stays_small_and_preserves_full_sorting(self):
        prefix = "猫" * 10_000
        self._save_prompt(prefix + "Z")
        alpha = self._save_prompt(prefix + "A")
        _, first = await self.get(sort="name", limit=1)
        self.assertEqual(first["items"][0]["id"], alpha)
        self.assertLess(len(first["next_cursor"]), 512)
        with self.api.db.model.get_connection() as connection:
            connection.execute("DELETE FROM prompts WHERE id=?", (alpha,))
        _, second = await self.get(sort="name", cursor=first["next_cursor"])
        self.assertEqual([item["text"] for item in second["items"]], [prefix + "Z"])
        titled = self._save_prompt("Zzz untitled order")
        with self.api.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (titled, prefix + "0"),
            )
        _, first = await self.get(sort="name", limit=1)
        self.assertEqual(
            (first["items"][0]["id"], first["items"][0]["title"]),
            (titled, prefix + "0"),
        )
        self.assertLess(len(first["next_cursor"]), 512)
        _, second = await self.get(sort="name", cursor=first["next_cursor"])
        self.assertEqual([item["text"] for item in second["items"]], [prefix + "Z"])

    async def test_name_cursor_expiry_and_eviction_require_refresh_without_large_urls(
        self,
    ):
        from py.api import library as module

        for text in ("Alpha", "Beta", "Gamma"):
            self._save_prompt(text)
        now = [100]
        store = module._NameCursorStore(max_entries=1, clock=lambda: now[0])
        with patch.object(module, "_NAME_CURSORS", store):
            _, first = await self.get(sort="name", limit=1)
            now[0] = 3699
            response, _ = await self.get(sort="name", cursor=first["next_cursor"])
            self.assertEqual(response.status, 200)
            now[0] = 3700
            response, result = await self.get(sort="name", cursor=first["next_cursor"])
            self.assertEqual(response.status, 400)
            self.assertIn("refresh", result["error"])
            response, fresh = await self.get(sort="name", limit=1)
            self.assertEqual(response.status, 200)
            _, replacement = await self.get(sort="name", limit=1)
            self.assertNotEqual(fresh["next_cursor"], replacement["next_cursor"])
            response, result = await self.get(sort="name", cursor=fresh["next_cursor"])
            self.assertEqual(response.status, 400)
            self.assertIn("refresh", result["error"])
            response, _ = await self.get(
                sort="name", cursor=replacement["next_cursor"], text="Alpha"
            )
            self.assertEqual(response.status, 400)

    async def test_batch_preview_hydration_keeps_two_real_images_per_parent(self):
        for parent_index in range(2):
            positive = self._save_prompt(f"Parent {parent_index}")
            for index in range(3):
                path = make_png(self.output_dir / f"{parent_index}-{index}.png")
                self.api.db.link_image_to_prompt(positive, str(path))
        _, result = await self.get()
        self.assertEqual(len(result["items"]), 2)
        for item in result["items"]:
            self.assertEqual(len(item["previews"]), 2)
            self.assertTrue(item["previews"][0]["path"].endswith("-2.png"))
            self.assertTrue(item["previews"][1]["path"].endswith("-1.png"))
            self.assertTrue(all(image["root_index"] == 0 for image in item["previews"]))

    async def test_root_scope_library_gallery_and_previews_share_formatter(self):
        from urllib.parse import parse_qs, quote, urlsplit

        relative = "nested/猫 % #.png"
        pair = self.api.db.save_prompt_pair("Cat", "Noise", title="Pair title")
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        with self.api.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (positive, "Positive title"),
            )
        path = make_png(self.output_dir / relative)
        make_png(self.output_dir / "thumbnails/nested/猫 % #_thumb.png")
        image = self.api.db.link_image_to_prompt(positive, str(path))
        self.api.db.link_image_to_pair(pair, image, {"source": "fixture"})
        _, listing = await self.get()
        preview = listing["items"][0]["previews"][0]
        _, gallery = await self.get(f"/{positive}/images", pair_id=pair)
        descriptor = gallery["images"][0]
        epoch = self.api.db.get_execution_epoch()
        self.assertEqual(
            (listing["library_epoch"], gallery["library_epoch"]), (epoch,) * 2
        )
        self.assertRegex(descriptor.get("root_scope", ""), r"^[0-9a-f]{64}$")
        self.assertEqual(preview["root_scope"], descriptor["root_scope"])
        for entry in (preview, descriptor):
            self.assertIn(quote(relative, safe="/"), entry["url"])
            for url in (entry["url"], entry["thumbnail_url"]):
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["root_scope"], [entry["root_scope"]])
                self.assertEqual((await self.client.get(url)).status, 200)
            self.assertIn("v", parse_qs(urlsplit(entry["thumbnail_url"]).query))
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{quote(relative, safe='/')}",
            params={"root": 0, "root_scope": descriptor["root_scope"]},
        )
        result = await response.json()
        self.assertEqual(result["root_scope"], descriptor["root_scope"])
        self.assertEqual(
            result["image"],
            {"root_index": 0, "path": relative, "filename": "猫 % #.png"},
        )
        # D39: the recipe has no category or tags, so both fall back.
        self.assertEqual(result["library"]["category_scope"], "positive")
        self.assertEqual(result["library"]["tags_scope"], "positive")
        self.assertEqual(result["library"]["pair_id"], pair)
        self.assertEqual(result["library"]["positive_id"], positive)
        # D10/D20: the linked positive's title; "Pair title" names the variant.
        self.assertEqual(result["library"]["title"], "Positive title")

    async def test_library_gallery_reports_favorites_with_one_lookup_per_page(self):
        # The Library-embedded media cards carry the Images star (#249 PR E):
        # favorites come from the store keyed by root and relative path.
        pair = self.api.db.save_prompt_pair("Cat", "Noise")
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        for index in range(5):
            path = make_png(self.output_dir / "sub" / f"{index}.png")
            image = self.api.db.link_image_to_prompt(positive, str(path))
            self.api.db.link_image_to_pair(pair, image, {"source": "fixture"})
        response = await self.client.put(
            "/prompt_manager/images/favorite",
            json={"root_index": 0, "path": "sub/3.png"},
        )
        self.assertEqual(response.status, 200)
        lookups = []
        original = self.api.db.get_image_favorites

        def counted(root_key, paths):
            lookups.append(list(paths))
            return original(root_key, paths)

        with patch.object(self.api.db, "get_image_favorites", side_effect=counted):
            _, gallery = await self.get(f"/{positive}/images", pair_id=pair)
        stars = {image["path"]: image["favorite"] for image in gallery["images"]}
        self.assertEqual(stars, {f"sub/{index}.png": index == 3 for index in range(5)})
        # One batched read per page, never one per card.
        self.assertEqual([len(paths) for paths in lookups], [5])
        # Shelf previews show no star and read no favorites.
        lookups.clear()
        with patch.object(self.api.db, "get_image_favorites", side_effect=counted):
            _, listing = await self.get()
        self.assertEqual(lookups, [])
        self.assertNotIn("favorite", listing["items"][0]["previews"][0])
        response = await self.client.delete(
            "/prompt_manager/images/favorite",
            json={"root_index": 0, "path": "sub/3.png"},
        )
        self.assertEqual(response.status, 200)
        _, gallery = await self.get(f"/{positive}/images", pair_id=pair)
        self.assertFalse(any(image["favorite"] for image in gallery["images"]))

    async def test_epoch_scoped_all_library_cursors(self):
        from database.execution_history_schema import rotate_history_epoch

        pairs = [
            self.api.db.save_prompt_pair("Cat", f"Neg {index}") for index in range(3)
        ]
        positive = self.api.db.get_prompt_pair(pairs[0])["positive_prompt_id"]
        self._save_prompt("Dog")
        for index in range(3):
            path = make_png(self.output_dir / f"{index}.png")
            image = self.api.db.link_image_to_prompt(positive, str(path))
            self.api.db.link_image_to_pair(pairs[0], image, {"source": "test"})
        self.api.db.archive_prompt_pair(pairs[0])
        self.api.db.archive_prompt_pair(pairs[1])
        epoch = self.api.db.get_execution_epoch()
        _, parents = await self.get(limit=1)
        _, variants = await self.get(f"/{positive}", variant_limit=1, archive="all")
        _, images = await self.get(
            f"/{positive}/images", pair_id=pairs[0], archive="archived", limit=1
        )
        _, options = await self.get("/options")
        for envelope in (parents, variants, images, options):
            self.assertEqual(envelope["library_epoch"], epoch)
        self.assertEqual(
            (variants["variant_total"], variants["positive"]["archived_variant_count"]),
            (3, 2),
        )
        self.assertEqual(variants["positive"]["variant_count"], 1)
        _, archived = await self.get(f"/{positive}", archive="archived")
        self.assertEqual(
            [(item["id"], item["archived"]) for item in archived["variants"]],
            [(pairs[1], True), (pairs[0], True)],
        )
        _, every = await self.get(
            f"/{positive}/images", pair_id=pairs[0], archive="all"
        )
        self.assertEqual(every["total"], 3)
        _, unfiltered = await self.get(f"/{positive}/images")
        self.assertEqual(unfiltered["total"], 3)
        for path, params in (
            (f"/{positive}", {"archive": "hidden"}),
            (f"/{positive}/images", {"archive": "hidden"}),
            (f"/{positive}/images", {"pair_id": pairs[0]}),
            (
                f"/{positive}",
                {"variant_cursor": variants["variants_next_cursor"]},
            ),
            (
                f"/{positive}/images",
                {
                    "pair_id": pairs[0],
                    "archive": "all",
                    "cursor": images["next_cursor"],
                },
            ),
        ):
            response, _ = await self.get(path, **params)
            self.assertEqual(response.status, 400, (path, params))
        negative_only = self.api.db.get_prompt_pair(pairs[2])["negative_prompt_id"]
        response, _ = await self.get(f"/{negative_only}/images")
        self.assertEqual(response.status, 404)
        rotate_history_epoch(self.api.db.model.get_connection())
        for path, params in (
            ("", {"cursor": parents["next_cursor"]}),
            (
                f"/{positive}",
                {"archive": "all", "variant_cursor": variants["variants_next_cursor"]},
            ),
            (
                f"/{positive}/images",
                {
                    "pair_id": pairs[0],
                    "archive": "archived",
                    "cursor": images["next_cursor"],
                },
            ),
        ):
            response, result = await self.get(path, **params)
            self.assertEqual(response.status, 400, (path, params))
            self.assertIn("refresh", result["error"])
        _, fresh = await self.get(limit=1)
        self.assertNotEqual(fresh["library_epoch"], epoch)
        response, _ = await self.get(limit=1, cursor=fresh["next_cursor"])
        self.assertEqual(response.status, 200)

    async def patch_parent(self, path, body=None, *, data=None):
        response = await self.client.patch(
            "/prompt_manager/library/" + str(path),
            json=body if data is None else None,
            data=data,
        )
        return response, await response.json()

    async def test_unconvertible_last_use_does_not_fail_list_detail_or_receipt(self):
        positive = self._save_prompt("Cat")
        other = self._save_prompt("Dog")
        with self.api.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET last_used_at=? WHERE id=?",
                ("9999-12-31T23:59:59-01:00", positive),
            )
            connection.execute(
                "UPDATE prompts SET last_used_at=? WHERE id=?",
                ("0001-01-01T00:00:00+01:00", other),
            )
        response, listed = await self.get()
        self.assertEqual(response.status, 200, listed)
        self.assertEqual(
            [item["last_used_at"] for item in listed["items"]], [None, None]
        )
        response, detail = await self.get(f"/{positive}")
        self.assertEqual(response.status, 200, detail)
        self.assertIsNone(detail["positive"]["last_used_at"])
        response, receipt = await self.patch_parent(
            positive,
            {
                "expected_epoch": detail["library_epoch"],
                "if_match": detail["positive"]["edit_token"],
                "metadata": {"title": "Portrait"},
            },
        )
        self.assertEqual(response.status, 200, receipt)
        self.assertEqual(receipt["positive"]["title"], "Portrait")
        self.assertIsNone(receipt["positive"]["last_used_at"])

    async def test_native_parent_patch_strict_validation(self):
        from database.execution_history_schema import rotate_history_epoch

        pair = self.api.db.save_prompt_pair("Cat", "Blur", title="Pair", rating=2)
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        negative_only = self.api.db.get_prompt_pair(pair)["negative_prompt_id"]
        self.api.db.update_prompt_metadata(positive, category="A", tags=["t"])
        pair_before = self.api.db.get_prompt_pair(pair)
        _, detail = await self.get(f"/{positive}")
        epoch = detail["library_epoch"]
        token = detail["positive"]["edit_token"]

        def request(**changes):
            value = {
                "expected_epoch": epoch,
                "if_match": token,
                "metadata": {"title": "Portrait"},
            }
            value.update(changes)
            return {key: item for key, item in value.items() if item is not ...}

        def state_rows():
            return (
                self.api.db.model.get_connection()
                .execute("SELECT COUNT(*) FROM prompt_library_state")
                .fetchone()[0]
            )

        invalid = [
            ("[]", None),
            ("{", None),
            (None, request(expected_epoch=...)),
            (None, request(if_match=...)),
            (None, request(metadata=...)),
            (None, request(archived=True)),
            (None, {**request(metadata=...), "archived": True}),
            (None, request(extra=1)),
            (None, request(metadata={})),
            (None, request(metadata=None)),
            (None, request(metadata=[["title", "x"]])),
            (None, request(expected_epoch=None)),
            (None, request(expected_epoch=5)),
            (None, request(expected_epoch="not-a-uuid")),
            (None, request(expected_epoch=epoch.upper())),
            (None, request(expected_epoch="{" + epoch + "}")),
            (None, request(if_match=None)),
            (None, request(if_match=token.upper())),
            (None, request(if_match=token[:-1])),
            (None, request(if_match=token + "0")),
        ]
        for metadata in (
            {"text": "Dog"},
            {"positive_prompt_id": positive},
            {"negative_prompt_id": negative_only},
            {"pair_id": pair},
            {"run_count": 1},
            {"archived": True},
            {"unknown": 1},
            {"title": "x", "variant_count": 3},
            {"tags": None},
            {"tags": "a,b"},
            {"tags": [1]},
            {"tags": [" "]},
            {"tags": ["x" * 51]},
            {"tags": [f"t{index}" for index in range(21)]},
            {"rating": True},
            {"rating": False},
            {"rating": 0},
            {"rating": 6},
            {"rating": "3"},
            {"rating": 2.0},
            {"title": 5},
            {"title": "x" * 10001},
            {"notes": ["n"]},
            {"notes": "x" * 10001},
            {"category": 1},
            {"category": "x" * 101},
            {"category": "bad\x01"},
        ):
            invalid.append((None, request(metadata=metadata)))
        for data, value in invalid:
            with self.subTest(data=data, body=value):
                response, result = await self.patch_parent(positive, value, data=data)
                self.assertEqual(response.status, 400)
                self.assertEqual(set(result), {"success", "code", "error"})
                self.assertEqual((result["success"], result["code"]), (False, None))
        for path in ("abc", "0", "-1", str(2**63)):
            response, result = await self.patch_parent(path, request())
            self.assertEqual((response.status, result["code"]), (400, None))
        self.assertEqual(state_rows(), 0)

        for missing in (9999, negative_only):
            response, result = await self.patch_parent(missing, request())
            self.assertEqual((response.status, result["code"]), (404, None))
            self.assertFalse(result["success"])
        self.assertEqual(state_rows(), 0)
        _, legacy_missing = await self.get("/9999")
        self.assertEqual(
            legacy_missing, {"success": False, "error": "Positive prompt not found"}
        )

        response, updated = await self.patch_parent(
            positive,
            request(metadata={"title": "Portrait", "rating": None, "tags": []}),
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(set(updated), {"success", "library_epoch", "positive"})
        self.assertTrue(updated["success"])
        self.assertEqual(updated["library_epoch"], epoch)
        self.assertIsNone(updated["positive"]["rating"])
        self.assertEqual(updated["positive"]["tags"], [])
        self.assertEqual(updated["positive"]["title"], "Portrait")
        self.assertEqual(updated["positive"]["category"], "A")
        self.assertNotIn("previews", updated["positive"])
        self.assertEqual(self.api.db.get_prompt_pair(pair), pair_before)
        _, reread = await self.get(f"/{positive}")
        self.assertEqual(
            reread["positive"]["edit_token"], updated["positive"]["edit_token"]
        )

        response, body = await self.patch_parent(positive, request())
        self.assertEqual((response.status, body["code"]), (409, "metadata-conflict"))
        self.assertFalse(body["success"])
        self.assertEqual(body["library_epoch"], epoch)
        self.assertEqual(body["positive"], updated["positive"])
        self.assertIsInstance(body["error"], str)

        new_epoch = rotate_history_epoch(self.api.db.model.get_connection())
        for target in (positive, 9999):
            response, body = await self.patch_parent(
                target, request(if_match=updated["positive"]["edit_token"])
            )
            self.assertEqual((response.status, body["code"]), (409, "storage-changed"))
            self.assertNotIn("positive", body)  # storage changed before owner lookup
            self.assertEqual(body["library_epoch"], new_epoch)

        with patch.object(
            self.api.db,
            "update_library_parent",
            side_effect=RuntimeError(f"no such table at {self.comfy_dir}"),
        ):
            response, result = await self.patch_parent(positive, request())
        self.assertEqual(response.status, 500)
        self.assertEqual(set(result), {"success", "code", "error"})
        self.assertIsNone(result["code"])
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))
        self.assertNotIn("table", result["error"])

    async def post_library(self, path="", body=None, *, data=None):
        response = await self.client.post(
            "/prompt_manager/library" + path,
            json=body if data is None else None,
            data=data,
        )
        return response, await response.json()

    def library_rows(self):
        connection = self.api.db.model.get_connection()
        return [
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("prompts", "prompt_pairs", "prompt_library_state", "tags")
        ]

    async def test_native_create_strict_validation_and_receipts(self):
        from database.execution_history_schema import rotate_history_epoch

        _, options = await self.get("/options")
        epoch = options["library_epoch"]

        def request(**changes):
            value = {
                "expected_epoch": epoch,
                "positive_text": "Portrait",
                "negative_text": "",
                "parent_metadata": {"title": "Portrait", "tags": ["soft"]},
            }
            value.update(changes)
            return {key: item for key, item in value.items() if item is not ...}

        invalid = [
            ("[]", None),
            ("{", None),
            (None, request(negative_text=...)),
            (None, request(negative_text=None)),
            (None, request(negative_text=5)),
            (None, request(negative_text="x" * 10001)),
            (None, request(positive_text=...)),
            (None, request(positive_text=None)),
            (None, request(positive_text="   ")),
            (None, request(positive_text="x" * 10001)),
            (None, request(expected_epoch=...)),
            (None, request(expected_epoch=None)),
            (None, request(expected_epoch=epoch.upper())),
            (None, request(parent_metadata=...)),
            (None, request(parent_metadata=None)),
            (None, request(parent_metadata={})),
            (None, request(parent_metadata={"category": "A"})),
            (None, request(parent_metadata={"title": " \t "})),
            (None, request(parent_metadata={"title": None})),
            (None, request(parent_metadata={"title": "x" * 10001})),
            (None, request(parent_metadata={"title": "t", "archived": True})),
            (None, request(parent_metadata={"title": "t", "text": "x"})),
            (None, request(parent_metadata={"title": "t", "tags": None})),
            (None, request(parent_metadata={"title": "t", "rating": True})),
            (None, request(extra=1)),
            (None, request(if_match="0" * 64)),
            (None, request(source_positive_id=1)),
        ]
        for data, value in invalid:
            with self.subTest(data=data, body=value):
                response, result = await self.post_library(body=value, data=data)
                self.assertEqual(response.status, 400)
                self.assertEqual(set(result), {"success", "code", "error"})
                self.assertEqual((result["success"], result["code"]), (False, None))
        self.assertEqual(self.library_rows(), [0, 0, 0, 0])

        response, created = await self.post_library(body=request())
        self.assertEqual(response.status, 200)
        self.assertEqual(
            set(created),
            {
                "success",
                "library_epoch",
                "positive",
                "pair",
                "positive_created",
                "pair_created",
                "parent_metadata_applied",
            },
        )
        self.assertTrue(created["success"])
        self.assertEqual(
            [created[k] for k in ("positive_created", "pair_created")], [True, True]
        )
        self.assertIs(created["parent_metadata_applied"], True)
        self.assertEqual(created["positive"]["title"], "Portrait")
        self.assertEqual(created["positive"]["tags"], ["soft"])
        self.assertNotIn("previews", created["positive"])
        self.assertIsNone(created["pair"]["negative_prompt_id"])
        self.assertEqual(created["pair"]["negative_text"], "")
        _, detail = await self.get(f"/{created['positive']['id']}")
        self.assertEqual(
            detail["positive"]["edit_token"], created["positive"]["edit_token"]
        )

        # A resubmitted request after a lost response returns a real receipt.
        response, repeat = await self.post_library(
            body=request(negative_text="   ", positive_text=" portrait ")
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            [repeat[k] for k in ("positive_created", "pair_created")], [False, False]
        )
        self.assertIs(repeat["parent_metadata_applied"], False)
        self.assertEqual(repeat["pair"], created["pair"])
        self.assertEqual(repeat["positive"], created["positive"])

        self.assertTrue(self.api.db.archive_prompt_pair(created["pair"]["id"]))
        _, archived = await self.post_library(body=request())
        self.assertIs(archived["pair"]["archived"], True)
        self.assertIs(archived["pair_created"], False)
        self.assertIs(
            self.api.db.get_prompt_pair(created["pair"]["id"])["archived"], True
        )

        rows = self.library_rows()
        new_epoch = rotate_history_epoch(self.api.db.model.get_connection())
        response, body = await self.post_library(
            body=request(positive_text="Another", negative_text="Grain")
        )
        self.assertEqual((response.status, body["code"]), (409, "storage-changed"))
        self.assertEqual(body["library_epoch"], new_epoch)
        self.assertNotIn("positive", body)
        self.assertEqual(self.library_rows(), rows)

    async def test_native_add_negative_strict_validation_and_receipts(self):
        from database.execution_history_schema import rotate_history_epoch

        pair = self.api.db.save_prompt_pair("Cat", "Blur", title="Pair", rating=2)
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        negative_only = self.api.db.get_prompt_pair(pair)["negative_prompt_id"]
        pair_before = self.api.db.get_prompt_pair(pair)
        _, detail = await self.get(f"/{positive}")
        epoch = detail["library_epoch"]
        token = detail["positive"]["edit_token"]

        def request(**changes):
            value = {"expected_epoch": epoch, "if_match": token, "negative_text": ""}
            value.update(changes)
            return {key: item for key, item in value.items() if item is not ...}

        def variants(target):
            return self.post_library(f"/{target}/variants", request())

        invalid = [
            ("[]", None),
            (None, request(negative_text=...)),
            (None, request(negative_text=None)),
            (None, request(negative_text="x" * 10001)),
            (None, request(positive_text="Dog")),
            (None, request(parent_metadata={"title": "x"})),
            (None, request(if_match=...)),
            (None, request(if_match=token.upper())),
            (None, request(expected_epoch="not-a-uuid")),
        ]
        for data, value in invalid:
            with self.subTest(data=data, body=value):
                response, result = await self.post_library(
                    f"/{positive}/variants", value, data=data
                )
                self.assertEqual(response.status, 400)
                self.assertEqual((result["success"], result["code"]), (False, None))
        for path in ("abc", "0", str(2**63)):
            response, result = await self.post_library(f"/{path}/variants", request())
            self.assertEqual((response.status, result["code"]), (400, None))
        rows = self.library_rows()
        for missing in (9999, negative_only):
            response, result = await variants(missing)
            self.assertEqual((response.status, result["code"]), (404, None))
        self.assertEqual(self.library_rows(), rows)

        response, added = await self.post_library(
            f"/{positive}/variants", request(negative_text=" Noise ")
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            [added[k] for k in ("positive_created", "pair_created")], [False, True]
        )
        self.assertIs(added["parent_metadata_applied"], False)
        self.assertEqual(added["pair"]["negative_text"], "Noise")
        self.assertEqual(added["positive"]["edit_token"], token)
        self.assertEqual(added["positive"]["variant_count"], 2)
        _, repeat = await self.post_library(
            f"/{positive}/variants", request(negative_text="blur")
        )
        self.assertEqual(repeat["pair"], pair_before)
        self.assertIs(repeat["pair_created"], False)

        self.api.db.update_prompt_metadata(positive, category="Changed")
        rows = self.library_rows()
        response, body = await variants(positive)
        self.assertEqual((response.status, body["code"]), (409, "metadata-conflict"))
        self.assertEqual(body["positive"]["category"], "Changed")
        self.assertEqual(body["library_epoch"], epoch)
        new_epoch = rotate_history_epoch(self.api.db.model.get_connection())
        response, body = await variants(positive)
        self.assertEqual((response.status, body["code"]), (409, "storage-changed"))
        self.assertEqual(body["library_epoch"], new_epoch)
        self.assertNotIn("positive", body)
        self.assertEqual(self.library_rows(), rows)

    async def test_command_wrapper_hides_unexpected_type_errors(self):
        _, options = await self.get("/options")
        body = {
            "expected_epoch": options["library_epoch"],
            "positive_text": "Cat",
            "negative_text": "",
            "parent_metadata": {"title": "Cat"},
        }
        secret = f"unsupported operand at {self.comfy_dir}"
        with (
            patch.object(
                self.api.db, "create_library_recipe", side_effect=TypeError(secret)
            ),
            self.assertLogs("prompt_manager.api", level="ERROR") as logs,
        ):
            response, result = await self.post_library(body=body)
        self.assertEqual(response.status, 500)
        self.assertEqual(
            result, {"success": False, "code": None, "error": "Library command failed"}
        )
        self.assertIn(secret, "\n".join(logs.output))

    async def test_command_wrapper_unknown_conflict_code_uses_generic_message(self):
        from database.library_commands import LibraryCommandError

        positive = self._save_prompt("Cat")
        _, detail = await self.get(f"/{positive}")
        body = {
            "expected_epoch": detail["library_epoch"],
            "if_match": detail["positive"]["edit_token"],
            "metadata": {"title": "x"},
        }
        error = LibraryCommandError("future-code", detail["library_epoch"])
        with patch.object(self.api.db, "update_library_parent", side_effect=error):
            response, result = await self.patch_parent(positive, body)
        self.assertEqual(response.status, 409)
        self.assertEqual(result["code"], "future-code")
        self.assertEqual(result["library_epoch"], detail["library_epoch"])
        self.assertIsInstance(result["error"], str)
        self.assertNotIn("positive", result)

    async def test_full_export_download_rejects_queries_and_keeps_legacy_formats(self):
        self.api.db.save_prompt_pair("Cat", "Blur")
        self._save_prompt("Solo")
        response = await self.client.get("/prompt_manager/library/export")
        document = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.headers["Content-Disposition"],
            'attachment; filename="prompt-manager-library-v1.json"',
        )
        self.assertEqual(
            (document["schema_version"], document["kind"]),
            (1, "prompt-manager-library"),
        )
        solo = next(o for o in document["positives"] if o["positive_text"] == "Solo")
        self.assertEqual(solo["variants"], [])
        for query in ("?limit=1", "?x", "?archive=all"):
            rejected = await self.client.get("/prompt_manager/library/export" + query)
            self.assertEqual(rejected.status, 400)
            self.assertNotIn("Content-Disposition", rejected.headers)
        legacy = await self.client.get("/prompt_manager/pairs/export")
        self.assertEqual((await legacy.json())["kind"], "prompt-manager-pairs")
        self.assertEqual(
            legacy.headers["Content-Disposition"],
            'attachment; filename="prompt-pairs.json"',
        )
        positive = (await self.client.get("/prompt_manager/library")).status
        self.assertEqual(positive, 200)

    async def test_non_numeric_positive_id_returns_fixed_message(self):
        response = await self.client.patch("/prompt_manager/library/export", json={})
        result = await response.json()
        self.assertEqual(response.status, 400)
        self.assertEqual(result["error"], "Invalid positive prompt ID")
        self.assertNotIn("literal", json.dumps(result))


class TestLibraryLoraMedia(ImageAPITestCase):
    """#355 (G6): media a LoRA import linked outside the output folders shows
    on the Library page and opens in the inspector while the integration is on."""

    async def setUpAsync(self):
        await super().setUpAsync()
        import tempfile
        from pathlib import Path

        self._lora_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._lora_tmp.cleanup)
        self.lora_dir = Path(self._lora_tmp.name).resolve() / "sidecars"
        self.lora_dir.mkdir()
        self.lora_on = True
        self.api._lora_image_dirs = lambda: [self.lora_dir] if self.lora_on else []

    async def get(self, path="", **params):
        response = await self.client.get(
            "/prompt_manager/library" + path, params=params
        )
        return response, await response.json()

    def _imported(self):
        """A LoRA prompt linked like the import links it: the cover first."""
        still = make_png(self.lora_dir / "abc" / "cover.png")
        video = self.lora_dir / "abc" / "clip.mp4"
        video.write_bytes(b"video")
        self.api.db.replace_category_prompts(
            "lora-manager",
            [
                {
                    "name": "neon",
                    "text": "neon street",
                    "hash": generate_prompt_hash("neon street"),
                    "tags": ["lora-manager"],
                    "images": [str(still), str(video)],
                }
            ],
        )
        positive = self.api.db.search_prompts(category="lora-manager")[0]["id"]
        ids = {
            os.path.basename(row["image_path"]): row["id"]
            for row in self.api.db.get_prompt_images(positive)
        }
        return positive, ids

    async def test_list_previews_show_linked_lora_media_cover_first(self):
        positive, _ids = self._imported()
        response, result = await self.get()
        self.assertEqual(response.status, 200, result)
        item = next(i for i in result["items"] if i["id"] == positive)
        previews = item["previews"]
        self.assertEqual([p["filename"] for p in previews], ["cover.png", "clip.mp4"])
        # The media keeps the rooted identity every workspace view checks; no
        # preview is made inside LoraManager's folders, so the card loads the
        # original and a video shows its label.
        self.assertEqual({p["root_index"] for p in previews}, {1})
        self.assertTrue(
            all(
                p["url"].startswith("/prompt_manager/images/serve/abc/")
                for p in previews
            )
        )
        self.assertEqual([p["thumbnail_url"] for p in previews], [None, None])
        self.assertEqual([p["is_video"] for p in previews], [False, True])
        self.assertEqual([p["linked_only"] for p in previews], [True, True])
        self.assertNotIn(str(self.lora_dir), json.dumps(result))
        file = await self.client.get(previews[0]["url"])
        self.assertEqual(file.status, 200)
        await file.read()
        self.assertFalse((self.lora_dir / "thumbnails").exists())

    async def test_detail_and_images_page_list_lora_media(self):
        positive, _ids = self._imported()
        make_png(self.output_dir / "out.png")
        _, detail = await self.get(f"/{positive}")
        self.assertEqual(
            [p["filename"] for p in detail["positive"]["previews"]],
            ["cover.png", "clip.mp4"],
        )
        _, page = await self.get(f"/{positive}/images")
        self.assertEqual(
            [i["filename"] for i in page["images"]], ["cover.png", "clip.mp4"]
        )
        # No favorite star: favorites belong to output listings.
        self.assertNotIn("favorite", page["images"][0])
        self.assertTrue(page["images"][0]["linked_only"])

    async def test_inspector_reads_lora_media_by_its_root(self):
        positive, _ids = self._imported()
        _, page = await self.get(f"/{positive}/images")
        image = page["images"][0]
        response = await self.client.get(
            "/prompt_manager/images/inspect/" + image["path"],
            params={
                "root": str(image["root_index"]),
                "root_scope": image["root_scope"],
            },
        )
        self.assertEqual(response.status, 200, await response.text())

    async def test_the_inspector_reads_only_linked_files_under_a_lora_root(self):
        # Security review: a LoRA root exposes only the files the import linked.
        self._imported()
        make_png(self.lora_dir / "abc" / "unlinked.png")
        scope = self.api._gallery_root_scope(self.lora_dir, 1)
        for name, status in (("unlinked.png", 403), ("cover.png", 200)):
            with self.subTest(name=name):
                response = await self.client.get(
                    "/prompt_manager/images/inspect/abc/" + name,
                    params={"root": "1", "root_scope": scope},
                )
                self.assertEqual(response.status, status, await response.text())

    async def test_only_recorded_lora_media_is_served_by_path(self):
        self._imported()
        make_png(self.lora_dir / "abc" / "unlinked.png")
        served = await self.client.get(
            "/prompt_manager/images/serve/abc/cover.png", params={"root": "1"}
        )
        self.assertEqual(served.status, 200)
        await served.read()
        unlinked = await self.client.get(
            "/prompt_manager/images/serve/abc/unlinked.png", params={"root": "1"}
        )
        self.assertEqual(unlinked.status, 403)
        preview = await self.client.get(
            "/prompt_manager/images/serve/thumbnails/abc/cover_thumb.webp",
            params={"root": "1"},
        )
        self.assertEqual(preview.status, 403)
        self.assertFalse((self.lora_dir / "thumbnails").exists())
        # Without a root, only the output folders are searched.
        unrooted = await self.client.get("/prompt_manager/images/serve/abc/cover.png")
        self.assertEqual(unrooted.status, 404)

    async def test_a_download_recorded_through_a_symlinked_cache_is_served(self):
        from pathlib import Path

        real = self.lora_dir.parent / "real-cache"
        real.mkdir()
        link = self.lora_dir.parent / "cache-link"
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        self.api._lora_image_dirs = lambda: [link]
        image = make_png(real / "neon" / "a1b2.jpg")
        stored = str(link / "neon" / "a1b2.jpg")
        positive = self._save_prompt("downloaded")
        self.api.db.link_image_to_prompt(positive, stored)
        _, page = await self.get(f"/{positive}/images")
        self.assertEqual([i["filename"] for i in page["images"]], [image.name])
        served = await self.client.get(page["images"][0]["url"])
        self.assertEqual(served.status, 200)
        await served.read()
        self.assertTrue(Path(stored).is_file())

    async def test_a_malformed_root_is_refused_not_an_error(self):
        response = await self.client.get(
            "/prompt_manager/images/serve/abc/cover.png", params={"root": "\u00b2"}
        )
        self.assertEqual(response.status, 403)

    async def test_lora_media_is_hidden_while_the_integration_is_off(self):
        positive, _ids = self._imported()
        self.lora_on = False
        _, result = await self.get()
        item = next(i for i in result["items"] if i["id"] == positive)
        self.assertEqual(item["previews"], [])
        response = await self.client.get(
            "/prompt_manager/images/inspect/abc/cover.png", params={"root": "1"}
        )
        self.assertEqual(response.status, 400)
        served = await self.client.get(
            "/prompt_manager/images/serve/abc/cover.png", params={"root": "1"}
        )
        self.assertEqual(served.status, 403)


class TestNameCursorBudget(unittest.TestCase):
    def test_lru_byte_budget_scope_and_oversize_keys_are_bounded(self):
        from py.api.library import _NameCursorStore

        store = _NameCursorStore(max_entries=2, max_bytes=40)
        one = store.put("one", "scope")
        two = store.put("two", "scope")
        self.assertEqual(store.get(one, "scope"), "one")
        three = store.put("three", "scope")
        with self.assertRaises(ValueError):
            store.get(two, "scope")
        self.assertEqual(store.get(three, "scope"), "three")
        with self.assertRaises(ValueError):
            store.get(one, "other")
        self.assertLessEqual(store.size, store.max_bytes)
        larger = store.put("x" * 35, "scope")
        self.assertEqual(len(store.entries), 1)
        self.assertEqual(store.get(larger, "scope"), "x" * 35)
        with self.assertRaises(ValueError):
            store.put("x" * 41, "scope")
        self.assertEqual(len(store.entries), 1)
