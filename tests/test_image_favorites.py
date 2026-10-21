"""Persisted image favorites (spec D7): store, schema and rooted API boundary."""

import json
import ntpath
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed

from database.image_favorites import (
    MAX_KEY_LENGTH,
    TABLE,
    image_favorites_schema_error,
    migrate_image_favorites,
)
from database.models import PromptModel
from database.operations import PromptDatabase

FAVORITE = "/prompt_manager/images/favorite"


class TestImageFavoriteApi(ImageAPITestCase):
    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    async def send(self, method, body):
        response = await self.client.request(method, FAVORITE, json=body)
        return response, await response.json()

    async def workspace(self, **params):
        response = await self.client.get(
            "/prompt_manager/images/output", params={"mode": "workspace", **params}
        )
        self.assertEqual(response.status, 200)
        data = await response.json()
        return {image["path"]: image for image in data["images"]}

    async def test_favorite_round_trip_and_idempotent(self):
        make_png(self.output_dir / "sub" / "a.png")
        make_png(self.output_dir / "b.png")
        items = await self.workspace()
        self.assertIs(items["sub/a.png"]["favorite"], False)
        self.assertIs(items["b.png"]["favorite"], False)

        body = {"root_index": 0, "path": "sub/a.png"}
        for _ in range(2):  # a repeated PUT is a no-op, not an error
            response, data = await self.send("PUT", body)
            self.assertEqual(response.status, 200)
            self.assertEqual(
                data,
                {
                    "success": True,
                    "favorite": True,
                    "root_index": 0,
                    "path": "sub/a.png",
                },
            )
        items = await self.workspace()
        self.assertIs(items["sub/a.png"]["favorite"], True)
        self.assertIs(items["b.png"]["favorite"], False)
        count = self.api.db.model.get_connection().execute(
            f"SELECT COUNT(*) FROM {TABLE}"
        )
        self.assertEqual(count.fetchone()[0], 1)

        for _ in range(2):
            response, data = await self.send("DELETE", body)
            self.assertEqual(response.status, 200)
            self.assertTrue(data["success"])
            self.assertIs(data["favorite"], False)
        items = await self.workspace()
        self.assertIs(items["sub/a.png"]["favorite"], False)

    def rows(self):
        return [
            tuple(row)
            for row in self.api.db.model.get_connection().execute(
                f"SELECT root_key, relative_path FROM {TABLE} ORDER BY 1, 2"
            )
        ]

    async def test_favorite_is_keyed_by_root_not_index(self):
        second = self.comfy_dir / "second"
        make_png(second / "a.png")
        make_png(self.output_dir / "a.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        response, data = await self.send("PUT", {"root_index": 1, "path": "a.png"})
        self.assertEqual(response.status, 200)
        self.assertNotIn(str(self.comfy_dir), json.dumps(data))
        root_key = Path(os.path.normcase(os.path.realpath(second))).as_posix()
        self.assertEqual(self.rows(), [(root_key, "a.png")])
        # The same root at another index keeps its favorite; the other root
        # with the same relative path does not inherit it.
        self.api._get_all_output_dirs = lambda: [second, self.output_dir]
        self.api.invalidate_gallery_cache()
        self.assertIs((await self.workspace(root=0))["a.png"]["favorite"], True)
        self.assertIs((await self.workspace(root=1))["a.png"]["favorite"], False)

    async def test_roots_sharing_a_folder_name_keep_separate_favorites(self):
        # Two roots under different parents, both shown as "output".
        with tempfile.TemporaryDirectory() as other_parent:
            other = Path(os.path.realpath(other_parent)) / "output"
            make_png(other / "a.png")
            make_png(self.output_dir / "a.png")
            self.api._get_all_output_dirs = lambda: [self.output_dir, other]
            response = await self.client.get(
                "/prompt_manager/images/output", params={"mode": "workspace"}
            )
            labels = {
                item["root_index"]: item["root_dir"]
                for item in (await response.json())["images"]
            }
            self.assertEqual(labels, {0: "output", 1: "output"})

            await self.send("PUT", {"root_index": 0, "path": "a.png"})
            self.assertIs((await self.workspace(root=0))["a.png"]["favorite"], True)
            self.assertIs((await self.workspace(root=1))["a.png"]["favorite"], False)

            await self.send("PUT", {"root_index": 1, "path": "a.png"})
            response, data = await self.send(
                "DELETE", {"root_index": 0, "path": "a.png"}
            )
            self.assertEqual(response.status, 200)
            self.assertNotIn(other_parent, json.dumps(data))
            self.assertIs((await self.workspace(root=0))["a.png"]["favorite"], False)
            self.assertIs((await self.workspace(root=1))["a.png"]["favorite"], True)
            listing = await self.client.get(
                "/prompt_manager/images/output", params={"mode": "workspace"}
            )
            self.assertNotIn(other_parent, await listing.text())

    async def test_windows_case_only_rename_keeps_the_star(self):
        make_png(self.output_dir / "Sub" / "A.png")
        with patch("py.api.images._path_flavor", ntpath):
            response, data = await self.send(
                "PUT", {"root_index": 0, "path": "Sub/A.png"}
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(data["path"], "Sub/A.png")  # the listing's spelling
            (self.output_dir / "Sub" / "A.png").rename(
                self.output_dir / "Sub" / "a.png"
            )
            (self.output_dir / "Sub").rename(self.output_dir / "sub")
            self.api.invalidate_gallery_cache()
            self.assertIs((await self.workspace())["sub/a.png"]["favorite"], True)
            response, data = await self.send(
                "DELETE", {"root_index": 0, "path": "SUB/A.PNG"}
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(data["path"], "sub/a.png")
            self.assertIs((await self.workspace())["sub/a.png"]["favorite"], False)
        self.assertEqual(self.rows(), [])

    async def test_posix_case_distinct_files_keep_independent_stars(self):
        make_png(self.output_dir / "A.png")
        make_png(self.output_dir / "a.png")
        if len(list(self.output_dir.iterdir())) != 2:
            self.skipTest("case-insensitive filesystem")
        await self.send("PUT", {"root_index": 0, "path": "A.png"})
        items = await self.workspace()
        self.assertIs(items["A.png"]["favorite"], True)
        self.assertIs(items["a.png"]["favorite"], False)

    async def test_favorite_rejects_traversal_and_unknown_root(self):
        make_png(self.output_dir / "a.png")
        make_png(self.comfy_dir / "outside.png")
        (self.output_dir / "notes.txt").write_text("not media")
        make_png(self.output_dir / "thumbnails" / "a_thumb.png")
        cases = [
            ({"root_index": 0, "path": "../outside.png"}, 403),
            ({"root_index": 0, "path": "sub/../../outside.png"}, 403),
            ({"root_index": 0, "path": str(self.comfy_dir / "outside.png")}, 403),
            ({"root_index": 0, "path": "/a.png"}, 403),
            ({"root_index": 0, "path": "C:/a.png"}, 403),
            ({"root_index": 0, "path": "a.png\0.png"}, 403),
            ({"root_index": 0, "path": "notes.txt"}, 403),
            ({"root_index": 1, "path": "a.png"}, 400),
            ({"root_index": -1, "path": "a.png"}, 400),
            ({"root_index": "0", "path": "a.png"}, 400),
            ({"root_index": True, "path": "a.png"}, 400),
            ({"root_index": 0}, 400),
            ({"root_index": 0, "path": ""}, 400),
            ({"root_index": 0, "path": 7}, 400),
            ({"root_index": 0, "path": "x" * 5000 + ".png"}, 400),
            ({"root_index": 0, "path": "missing.png"}, 404),
            ({"root_index": 0, "path": "thumbnails/a_thumb.png"}, 404),
        ]
        for method in ("PUT", "DELETE"):
            for body, status in cases:
                if method == "DELETE" and status == 404:
                    continue  # unlisting a stale favorite is allowed (below)
                with self.subTest(method=method, body=str(body)[:60]):
                    response, data = await self.send(method, body)
                    self.assertEqual(response.status, status)
                    self.assertIs(data["success"], False)
                    self.assertNotIn(str(self.comfy_dir), data["error"])
        response = await self.client.put(FAVORITE, data=b"[1]")
        self.assertEqual(response.status, 400)
        response = await self.client.put(FAVORITE, data=b"{not json")
        self.assertEqual(response.status, 400)
        count = self.api.db.model.get_connection().execute(
            f"SELECT COUNT(*) FROM {TABLE}"
        )
        self.assertEqual(count.fetchone()[0], 0)

    async def test_delete_clears_a_favorite_whose_file_is_gone(self):
        target = make_png(self.output_dir / "gone.png")
        await self.send("PUT", {"root_index": 0, "path": "gone.png"})
        target.unlink()
        self.api.invalidate_gallery_cache()
        response, data = await self.send(
            "DELETE", {"root_index": 0, "path": "gone.png"}
        )
        self.assertEqual(response.status, 200)
        self.assertIs(data["favorite"], False)
        count = self.api.db.model.get_connection().execute(
            f"SELECT COUNT(*) FROM {TABLE}"
        )
        self.assertEqual(count.fetchone()[0], 0)

    async def test_legacy_listing_is_unchanged_by_favorites(self):
        make_png(self.output_dir / "a.png")
        await self.send("PUT", {"root_index": 0, "path": "a.png"})
        response = await self.client.get("/prompt_manager/images/output")
        data = await response.json()
        self.assertEqual(len(data["images"]), 1)
        self.assertNotIn("favorite", data["images"][0])

    async def test_toggle_lookup_is_built_once_per_scan(self):
        make_png(self.output_dir / "a.png")
        files = await self.api._get_gallery_files(self.output_dir)
        first = self.api._favorite_listing(self.output_dir, files)
        self.assertEqual(first, {"a.png": "a.png"})
        self.assertIs(self.api._favorite_listing(self.output_dir, files), first)
        make_png(self.output_dir / "b.png")
        self.api.invalidate_gallery_cache()
        rescanned = await self.api._get_gallery_files(self.output_dir)
        self.assertEqual(
            self.api._favorite_listing(self.output_dir, rescanned),
            {"a.png": "a.png", "b.png": "b.png"},
        )
        self.api.reset_gallery_roots_cache()
        self.assertEqual(self.api._favorite_listing_cache, {})

    async def test_monitor_invalidation_drops_the_favorites_lookup(self):
        make_png(self.output_dir / "a.png")
        await self.send("PUT", {"root_index": 0, "path": "a.png"})
        self.assertNotEqual(self.api._favorite_listing_cache, {})
        # The image monitor's invalidation must not keep the old scan alive.
        self.api.invalidate_gallery_cache()
        self.assertEqual(self.api._favorite_listing_cache, {})
        self.assertIs((await self.workspace())["a.png"]["favorite"], True)

    async def test_put_rejects_a_key_beyond_the_stored_bound(self):
        make_png(self.output_dir / "a.png")
        too_long = "/" + "r" * MAX_KEY_LENGTH
        with patch("py.api.images._favorite_root_key", return_value=too_long):
            response, data = await self.send("PUT", {"root_index": 0, "path": "a.png"})
        self.assertEqual(response.status, 403)
        self.assertFalse(data["success"])
        self.assertNotIn(too_long, json.dumps(data))
        self.assertEqual(self.rows(), [])
        # The live database still validates (a stored over-long key would
        # fail startup and restore).
        conn = self.api.db.model.get_connection()
        self.assertIsNone(image_favorites_schema_error(conn))

    async def test_favorite_store_failure_keeps_the_listing(self):
        make_png(self.output_dir / "a.png")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        self.api.db.get_image_favorites = broken
        items = await self.workspace()
        self.assertIs(items["a.png"]["favorite"], False)
        self.api.db.set_image_favorite = broken
        response, data = await self.send("PUT", {"root_index": 0, "path": "a.png"})
        self.assertEqual(response.status, 500)
        self.assertEqual(data, {"success": False, "error": "Favorite update failed"})


class TestWorkspaceFavoritesFilter(ImageAPITestCase):
    """``favorites=1`` on the workspace listing: the Favorites page (#249 D51)."""

    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    async def page(self, status=200, **params):
        response = await self.client.get(
            "/prompt_manager/images/output", params={"mode": "workspace", **params}
        )
        data = await response.json()
        self.assertEqual(response.status, status, data)
        return data

    async def star(self, path, root_index=0, method="PUT"):
        response = await self.client.request(
            method, FAVORITE, json={"root_index": root_index, "path": path}
        )
        self.assertEqual(response.status, 200)

    def make(self, *names):
        for offset, name in enumerate(names):
            target = make_png(self.output_dir / name)
            os.utime(target, (1_700_000_000 + offset, 1_700_000_000 + offset))

    async def test_lists_only_favorites_and_counts_them(self):
        self.make("a.png", "sub/b.png", "c.png", "sub/d.png")
        await self.star("a.png")
        await self.star("sub/d.png")
        data = await self.page(favorites="1")
        self.assertEqual([i["path"] for i in data["images"]], ["sub/d.png", "a.png"])
        self.assertEqual(data["total"], 2)
        self.assertTrue(all(i["favorite"] is True for i in data["images"]))
        # The nav count's read: limit=1 still reports every favorite's total.
        self.assertEqual((await self.page(favorites="1", limit="1"))["total"], 2)
        # Other filters intersect with it.
        sub = await self.page(favorites="1", subfolder="sub")
        self.assertEqual([i["path"] for i in sub["images"]], ["sub/d.png"])
        self.assertEqual((await self.page(favorites="1", q="a.png"))["total"], 1)
        self.assertEqual((await self.page(favorites="1", media="video"))["total"], 0)
        # favorites=0 (and no parameter) is the whole listing.
        self.assertEqual((await self.page(favorites="0"))["total"], 4)
        self.assertEqual((await self.page())["total"], 4)
        await self.star("a.png", method="DELETE")
        self.assertEqual((await self.page(favorites="1"))["total"], 1)

    async def test_cursor_pages_walk_only_favorites_in_every_order(self):
        self.make("a.png", "b.png", "c.png", "d.png", "e.png")
        for name in ("a.png", "c.png", "e.png"):
            await self.star(name)
        expected = {
            "newest": ["e.png", "c.png", "a.png"],
            "oldest": ["a.png", "c.png", "e.png"],
            "name": ["a.png", "c.png", "e.png"],
            "name-desc": ["e.png", "c.png", "a.png"],
            "rating": ["e.png", "c.png", "a.png"],
        }
        for sort, names in expected.items():
            seen, cursor = [], None
            while True:
                params = {"favorites": "1", "sort": sort, "limit": "1"}
                if cursor:
                    params["cursor"] = cursor
                data = await self.page(**params)
                self.assertEqual(data["total"], 3, sort)
                seen += [i["path"] for i in data["images"]]
                cursor = data["next_cursor"]
                if not data["has_more"]:
                    break
            self.assertEqual(seen, names, sort)
        # A cursor is bound to its filters: a whole-listing cursor is refused.
        whole = await self.page(limit="1")
        await self.page(400, favorites="1", cursor=whole["next_cursor"])

    async def test_favorites_follow_their_own_root(self):
        second = self.comfy_dir / "second"
        make_png(second / "a.png")
        make_png(self.output_dir / "a.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        await self.star("a.png", root_index=1)
        data = await self.page(favorites="1")
        self.assertEqual(
            [(i["root_index"], i["path"]) for i in data["images"]], [(1, "a.png")]
        )
        self.assertEqual((await self.page(favorites="1", root="0"))["total"], 0)
        self.assertEqual((await self.page(favorites="1", root="1"))["total"], 1)

    async def test_windows_case_folded_favorite_is_listed(self):
        make_png(self.output_dir / "Sub" / "A.png")
        with patch("py.api.images._path_flavor", ntpath):
            await self.star("Sub/A.png")
            (self.output_dir / "Sub" / "A.png").rename(
                self.output_dir / "Sub" / "a.png"
            )
            (self.output_dir / "Sub").rename(self.output_dir / "sub")
            self.api.invalidate_gallery_cache()
            data = await self.page(favorites="1")
        self.assertEqual([i["path"] for i in data["images"]], ["sub/a.png"])

    async def test_a_favorite_whose_file_is_gone_is_not_listed(self):
        self.make("a.png", "b.png")
        await self.star("a.png")
        await self.star("b.png")
        (self.output_dir / "b.png").unlink()
        self.api.invalidate_gallery_cache()
        data = await self.page(favorites="1")
        self.assertEqual([i["path"] for i in data["images"]], ["a.png"])
        self.assertEqual(data["total"], 1)

    async def test_rejects_other_values(self):
        for value in ("true", "2", "", "yes"):
            data = await self.page(400, favorites=value)
            self.assertFalse(data["success"])

    async def test_store_failure_is_an_error_not_an_unfiltered_page(self):
        self.make("a.png")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        self.api.db.get_root_favorites = broken
        data = await self.page(500, favorites="1")
        self.assertFalse(data["success"])
        self.assertNotIn("images", data)


class TestImageFavoriteSchema(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "favorites.db")

    def tearDown(self):
        PromptModel.close_all_instances()
        assert_closed(self, self.tmpdir)
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def test_store_round_trip_and_bounded_lookup(self):
        db = PromptDatabase(self.path)
        try:
            self.assertTrue(db.set_image_favorite("output", "a.png"))
            self.assertTrue(db.set_image_favorite("output", "a.png"))
            db.set_image_favorite("other", "a.png")
            self.assertEqual(
                db.get_image_favorites("output", ["a.png", "b.png"]), {"a.png"}
            )
            self.assertEqual(db.get_image_favorites("output", []), set())
            # One root's favorites, read through the primary key.
            self.assertEqual(db.get_root_favorites("output"), {"a.png"})
            self.assertEqual(db.get_root_favorites("other"), {"a.png"})
            self.assertEqual(db.get_root_favorites("missing"), set())
            many = [f"{n}.png" for n in range(2000)] + ["a.png"]
            self.assertEqual(db.get_image_favorites("output", many), {"a.png"})
            self.assertTrue(db.clear_image_favorite("output", "a.png"))
            self.assertFalse(db.clear_image_favorite("output", "a.png"))
            self.assertEqual(db.get_image_favorites("output", ["a.png"]), set())
            created = db.model.get_connection().execute(
                f"SELECT created_at FROM {TABLE}"
            )
            self.assertRegex(created.fetchone()[0], r"^\d{4}-\d\d-\d\dT")
        finally:
            db.close_all()

    def test_schema_rejects_unsupported_definitions_and_values(self):
        variants = {
            "missing": None,
            "wrong table": f"CREATE TABLE {TABLE} (x TEXT)",
            "view": f"CREATE VIEW {TABLE} AS SELECT 1",
            "nullable key": (
                f"CREATE TABLE {TABLE} (root_key TEXT, relative_path TEXT, "
                "created_at TEXT, PRIMARY KEY(root_key, relative_path))"
            ),
        }
        for label, ddl in variants.items():
            with self.subTest(label):
                with closing(sqlite3.connect(":memory:")) as conn:
                    if ddl:
                        conn.execute(ddl)
                    error = image_favorites_schema_error(conn)
                    if ddl is None:
                        self.assertIsNone(error)
                        self.assertIsNotNone(
                            image_favorites_schema_error(conn, allow_missing=False)
                        )
                    else:
                        self.assertIsNotNone(error)
                        with self.assertRaises(sqlite3.DatabaseError):
                            migrate_image_favorites(conn)
        with closing(sqlite3.connect(":memory:")) as conn:
            migrate_image_favorites(conn)
            migrate_image_favorites(conn)  # idempotent
            self.assertIsNone(image_favorites_schema_error(conn, allow_missing=False))
            conn.execute(f"INSERT INTO {TABLE} VALUES ('r', 'a.png', 'now')")
            self.assertIsNone(image_favorites_schema_error(conn))
            conn.execute(f"INSERT INTO {TABLE} VALUES ('r', x'00ff', 'now')")
            self.assertIsNotNone(image_favorites_schema_error(conn))
            conn.execute(f"DELETE FROM {TABLE} WHERE typeof(relative_path)='blob'")
            conn.execute(f"INSERT INTO {TABLE} VALUES ('r', ?, 'now')", ("x" * 5000,))
            self.assertIsNotNone(image_favorites_schema_error(conn))
            conn.execute(f"DELETE FROM {TABLE}")
            conn.execute(f"CREATE INDEX extra ON {TABLE}(created_at)")
            self.assertIsNotNone(image_favorites_schema_error(conn))

    def test_verify_rejects_a_backup_with_an_invalid_favorites_table(self):
        db = PromptDatabase(self.path)
        db.close_all()
        PromptModel.close_all_instances()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"DROP TABLE {TABLE}")
            conn.execute(f"CREATE TABLE {TABLE} (anything TEXT)")
            conn.commit()
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("favorites", reason.lower())


if __name__ == "__main__":
    unittest.main()
