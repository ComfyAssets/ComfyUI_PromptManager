"""Image category routes (#332): PUT/DELETE /images/category, GET /images/categories."""

import json
import ntpath
import sqlite3
from unittest.mock import patch

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed
    from test_image_categories import category_checksum
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed
    from tests.test_image_categories import category_checksum

from database.image_categories import MAX_KEY_LENGTH, TABLE
from py.api.images import _favorite_root_key, image_category_key

CATEGORY = "/prompt_manager/images/category"
CATEGORIES = "/prompt_manager/images/categories"


class ImageCategoryTestCase(ImageAPITestCase):
    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def target(self, path, index=0):
        roots = self.api._get_all_output_dirs()
        return {
            "root_index": index,
            "root_scope": self.api._gallery_root_scope(roots[index], index),
            "path": path,
        }

    async def send(self, method, body, status=200):
        response = await self.client.request(method, CATEGORY, json=body)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        return json.loads(text)

    async def put(self, paths, category, status=200):
        items = [p if isinstance(p, dict) else self.target(p) for p in paths]
        return await self.send("PUT", {"items": items, "category": category}, status)

    async def delete(self, paths, status=200):
        items = [p if isinstance(p, dict) else self.target(p) for p in paths]
        return await self.send("DELETE", {"items": items}, status)

    async def listed(self, status=200):
        response = await self.client.get(CATEGORIES)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        return json.loads(text)

    def rows(self):
        return [
            tuple(row)
            for row in self.api.db.model.get_connection().execute(
                f"SELECT root_key, relative_path, category FROM {TABLE} "
                "ORDER BY root_key, relative_path"
            )
        ]

    def key(self, relative, root=None):
        roots = self.api._get_all_output_dirs()
        return image_category_key(roots, (root or self.output_dir) / relative)


class TestSetAndClear(ImageCategoryTestCase):
    async def test_put_sets_listed_and_counts_missing(self):
        make_png(self.output_dir / "a.png")
        make_png(self.output_dir / "sub" / "b.png")
        data = await self.put(["a.png", "sub/b.png", "missing.png"], "  Portraits ")
        self.assertEqual(
            data,
            {
                "success": True,
                "category": "Portraits",
                "updated": 2,
                "unchanged": 0,
                "not_found": 1,
            },
        )
        root_key = _favorite_root_key(self.output_dir)
        self.assertEqual(
            self.rows(),
            [(root_key, "a.png", "Portraits"), (root_key, "sub/b.png", "Portraits")],
        )
        data = await self.put(["a.png", "sub/b.png"], "Portraits")
        self.assertEqual((data["updated"], data["unchanged"]), (0, 2))
        data = await self.put(["a.png"], "Scans")
        self.assertEqual((data["updated"], data["unchanged"]), (1, 0))
        self.assertEqual(self.api.db.image_category(*self.key("a.png")), "Scans")

    async def test_put_with_no_listed_file_is_404_and_writes_nothing(self):
        data = await self.put(["missing.png", "gone/x.png"], "Scans", status=404)
        self.assertEqual(data["code"], "files-not-found")
        self.assertEqual(self.rows(), [])

    async def test_duplicate_targets_are_written_and_counted_once(self):
        make_png(self.output_dir / "a.png")
        item = self.target("a.png")
        data = await self.put([item, dict(item)], "Scans")
        self.assertEqual((data["updated"], data["not_found"]), (1, 0))

    async def test_delete_clears_listed_and_missing_files(self):
        make_png(self.output_dir / "a.png")
        await self.put(["a.png"], "Scans")
        self.api.db.set_image_categories([self.key("gone.png")], "Scans")
        data = await self.delete(["a.png", "gone.png", "never.png"])
        self.assertEqual(data, {"success": True, "cleared": 2})
        self.assertEqual(self.rows(), [])
        self.assertEqual((await self.delete(["a.png"]))["cleared"], 0)

    async def test_put_never_writes_prompts(self):
        make_png(self.output_dir / "a.png")
        make_png(self.output_dir / "b.png")
        positive = self._save_prompt("castle at dusk", category="Landscapes")
        self.api.db.link_image_to_prompt(positive, str(self.output_dir / "a.png"))
        negative = self._save_prompt("blurry", category="Negatives")
        self.api.db.save_prompt_pair("castle at dusk", "blurry", category="Recipes")
        conn = self.api.db.model.get_connection()
        before = category_checksum(conn)
        links = conn.execute("SELECT * FROM generated_images").fetchall()
        await self.put(["a.png", "b.png"], "Portraits")
        await self.listed()
        await self.delete(["a.png"])
        self.assertEqual(category_checksum(conn), before)
        self.assertEqual(
            conn.execute("SELECT * FROM generated_images").fetchall(), links
        )
        self.assertIsNotNone(negative)

    async def test_two_sources_in_one_request(self):
        second = self.comfy_dir / "renders"
        make_png(self.output_dir / "a.png")
        make_png(second / "a.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        data = await self.put([self.target("a.png", 0), self.target("a.png", 1)], "X")
        self.assertEqual(data["updated"], 2)
        self.assertEqual(
            {row[0] for row in self.rows()},
            {_favorite_root_key(self.output_dir), _favorite_root_key(second)},
        )
        self.assertEqual((await self.delete([self.target("a.png", 1)]))["cleared"], 1)

    async def test_nested_sources_key_by_the_deepest(self):
        inner = self.output_dir / "nested"
        make_png(inner / "a.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, inner]
        # Named through the outer source, stored under the inner one.
        data = await self.put([self.target("nested/a.png", 0)], "Scans")
        self.assertEqual(data["updated"], 1)
        self.assertEqual(self.rows(), [(_favorite_root_key(inner), "a.png", "Scans")])
        # The same file through the inner source is the same key.
        data = await self.put([self.target("a.png", 1)], "Scans")
        self.assertEqual(data["unchanged"], 1)
        self.assertEqual((await self.listed())["categories"], ["Scans"])
        self.assertEqual((await self.delete([self.target("a.png", 1)]))["cleared"], 1)

    async def test_video_files_can_have_a_category(self):
        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        data = await self.put(["clip.mp4"], "Clips")
        self.assertEqual(data["updated"], 1)

    async def test_windows_case_folds_the_key(self):
        make_png(self.output_dir / "Sub" / "A.png")
        with patch("py.api.images._path_flavor", ntpath):
            data = await self.put(["Sub/A.png"], "Scans")
            self.assertEqual(data["updated"], 1)
            data = await self.put(["sub/a.PNG"], "Scans")
            self.assertEqual(data["unchanged"], 1)
            self.assertEqual(self.rows()[0][1], "sub/a.png")
            self.assertEqual((await self.delete(["SUB/A.png"]))["cleared"], 1)

    async def test_a_key_beyond_the_stored_bound_is_403(self):
        make_png(self.output_dir / "a.png")
        too_long = "/" + "r" * MAX_KEY_LENGTH
        with patch("py.api.images._favorite_root_key", return_value=too_long):
            data = await self.put(["a.png"], "Scans", status=403)
            self.assertNotIn(too_long, json.dumps(data))
            await self.delete(["a.png"], status=403)
        self.assertEqual(self.rows(), [])

    async def test_an_unexpected_failure_is_a_logged_500(self):
        make_png(self.output_dir / "a.png")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("secret detail")

        self.api.db.set_image_categories = broken
        self.api.db.clear_image_categories = broken
        with self.assertLogs("prompt_manager", level="ERROR"):
            data = await self.put(["a.png"], "Scans", status=500)
        self.assertEqual(data["error"], "The category could not be changed.")
        self.assertNotIn("secret", json.dumps(data))
        with self.assertLogs("prompt_manager", level="ERROR"):
            data = await self.delete(["a.png"], status=500)
        self.assertNotIn("secret", json.dumps(data))

    async def test_delete_category_never_reaches_delete_image(self):
        make_png(self.output_dir / "a.png")
        calls = []

        async def recorded(request):
            calls.append(request.match_info)
            raise AssertionError("delete_image reached")

        self.api.delete_image = recorded
        await self.put(["a.png"], "Scans")
        data = await self.delete(["a.png"])
        self.assertEqual(data["cleared"], 1)
        self.assertEqual(calls, [])


class TestRefusals(ImageCategoryTestCase):
    async def test_malformed_bodies_are_400(self):
        make_png(self.output_dir / "a.png")
        item = self.target("a.png")
        cases = [
            {"items": [item]},
            {"category": "x"},
            {"items": [item], "category": "x", "extra": 1},
            {"items": item, "category": "x"},
            {"items": [], "category": "x"},
            {"items": [item] * 501, "category": "x"},
            {"items": [item], "category": 3},
            {"items": [item], "category": None},
            {"items": [item], "category": " \t "},
            {"items": [item], "category": "x" * 101},
            {"items": [item], "category": "a\x07b"},
            {"items": [{**item, "root_index": "0"}], "category": "x"},
            {"items": [{**item, "root_scope": "nothex"}], "category": "x"},
            {"items": [{"root_index": 0, "path": "a.png"}], "category": "x"},
            {"items": [{**item, "root_index": 1}], "category": "x"},
        ]
        for body in cases:
            with self.subTest(body=str(body)[:80]):
                data = await self.send("PUT", body, status=400)
                self.assertFalse(data["success"])
        await self.send("DELETE", {"items": [item], "category": "x"}, status=400)
        await self.send("DELETE", {"items": []}, status=400)
        response = await self.client.request(
            "PUT", CATEGORY, data="not json", headers={"Content-Type": "text/plain"}
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.rows(), [])

    async def test_unsafe_paths_are_403(self):
        (self.output_dir / "notes.txt").write_text("not media")
        for path in ("../outside.png", "/a.png", "C:/a.png", "a\\b.png", "a/./b.png"):
            with self.subTest(path=path):
                await self.put([self.target(path)], "x", status=403)
        data = await self.put([self.target("notes.txt")], "x", status=403)
        self.assertEqual(data["error"], "Only media files can have a category")
        await self.delete([self.target("notes.txt")], status=403)

    async def test_a_stale_scope_is_409(self):
        make_png(self.output_dir / "a.png")
        item = {**self.target("a.png"), "root_scope": "0" * 64}
        data = await self.put([item], "x", status=409)
        self.assertEqual(data["code"], "source-changed")
        data = await self.delete([item], status=409)
        self.assertEqual(data["code"], "source-changed")

    async def test_an_oversized_body_is_413(self):
        response = await self.client.request(
            "PUT",
            CATEGORY,
            data=b'{"items": [], "category": "' + b"x" * (2 * 1024 * 1024) + b'"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 413)


class TestCategoryList(ImageCategoryTestCase):
    async def test_lists_only_values_whose_file_is_listed(self):
        make_png(self.output_dir / "a.png")
        make_png(self.output_dir / "c.png")
        await self.put(["a.png"], "B")
        await self.put(["c.png"], "A")
        self.api.db.set_image_categories([self.key("gone.png")], "Z")
        self.assertEqual(
            await self.listed(), {"success": True, "categories": ["A", "B"]}
        )

    async def test_empty_library_lists_nothing(self):
        self.assertEqual((await self.listed())["categories"], [])

    async def test_a_failed_read_is_500(self):
        make_png(self.output_dir / "a.png")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("secret detail")

        self.api.db.own_categories_of = broken
        with self.assertLogs("prompt_manager", level="ERROR"):
            data = await self.listed(status=500)
        self.assertEqual(data["error"], "Image categories could not be read.")
        self.assertNotIn("secret", json.dumps(data))
