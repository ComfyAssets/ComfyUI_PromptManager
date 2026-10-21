"""Saved views (D16): the persisted store, its restore schema and the API."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

try:
    from test_api_images import ImageAPITestCase
    from open_handles import assert_closed
except ImportError:
    from tests.test_api_images import ImageAPITestCase
    from tests.open_handles import assert_closed

from database.models import PromptModel
from database.operations import PromptDatabase
from database.saved_views import (
    MAX_STATE_BYTES,
    TABLE,
    migrate_saved_views,
    saved_views_schema_error,
)
from utils.hashing import generate_prompt_hash

URL = "/prompt_manager/saved-views"

IMAGES_STATE = {
    "q": "cat",
    "category": "Animals",
    "sort": "newest",
    "source": "all",
    "folder": "",
    "media": "all",
    "tags": ["fluffy"],
}
LIBRARY_STATE = {"q": "", "category": "", "sort": "rating", "tags": []}


class TestSavedViewsApi(ImageAPITestCase):
    async def send(self, method, path="", body=None, **kwargs):
        if body is not None:
            kwargs["json"] = body
        response = await self.client.request(method, URL + path, **kwargs)
        return response, await response.json()

    async def create(self, page="images", name="Cats", state=None):
        state = dict(
            IMAGES_STATE if page == "images" else LIBRARY_STATE, **(state or {})
        )
        response, data = await self.send(
            "POST", body={"page": page, "name": name, "state": state}
        )
        self.assertEqual(response.status, 201, data)
        return data["view"]

    async def test_create_list_rename_delete_round_trip(self):
        response, data = await self.send("GET")
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {"success": True, "views": []})

        first = await self.create(name="  Cats  ")
        self.assertEqual(
            first,
            {
                "id": first["id"],
                "page": "images",
                "name": "Cats",
                "state": IMAGES_STATE,
            },
        )
        second = await self.create(page="library", name="Best", state={})
        _, data = await self.send("GET")
        self.assertTrue(data["success"])
        self.assertEqual(data["views"], [first, second])

        response, data = await self.send(
            "PATCH", f"/{first['id']}", {"name": " Kittens "}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {"success": True, "view": {**first, "name": "Kittens"}})
        _, listed = await self.send("GET")
        # Renaming keeps the view's place in the list.
        self.assertEqual(
            [view["name"] for view in listed["views"]], ["Kittens", "Best"]
        )

        response, data = await self.send("DELETE", f"/{first['id']}")
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {"success": True, "id": first["id"]})
        _, listed = await self.send("GET")
        self.assertEqual(listed["views"], [second])

        for method, body in (("PATCH", {"name": "Again"}), ("DELETE", None)):
            response, data = await self.send(method, f"/{first['id']}", body)
            self.assertEqual(response.status, 404)
            self.assertFalse(data["success"])
            self.assertIn("error", data)

    async def test_views_are_ordered_by_position_and_ids_are_not_reused(self):
        views = [await self.create(name=f"View {n}") for n in range(3)]
        await self.send("DELETE", f"/{views[2]['id']}")
        later = await self.create(name="Later")
        self.assertGreater(later["id"], views[2]["id"])
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                f"UPDATE {TABLE} SET position = -1 WHERE id = ?", (views[1]["id"],)
            )
        _, listed = await self.send("GET")
        self.assertEqual(
            [view["name"] for view in listed["views"]], ["View 1", "View 0", "Later"]
        )

    async def test_create_rejects_invalid_bodies(self):
        good = {"page": "images", "name": "Cats", "state": IMAGES_STATE}
        cases = [
            ["not an object"],
            {"page": "images", "name": "Cats"},
            {**good, "extra": 1},
            {**good, "page": "history"},
            {**good, "page": 1},
            {**good, "name": ""},
            {**good, "name": "   "},
            {**good, "name": "x" * 101},
            {**good, "name": 7},
            {**good, "name": "bad\nname"},
            {**good, "state": "q=cat"},
            {**good, "state": None},
            {**good, "state": {**IMAGES_STATE, "unknown": ""}},
            {**good, "state": {k: v for k, v in IMAGES_STATE.items() if k != "q"}},
            {**good, "state": {**IMAGES_STATE, "q": 5}},
            {**good, "state": {**IMAGES_STATE, "q": "x" * 501}},
            {**good, "state": {**IMAGES_STATE, "category": "x" * 101}},
            {**good, "state": {**IMAGES_STATE, "sort": "random"}},
            {**good, "state": {**IMAGES_STATE, "media": "audio"}},
            {**good, "state": {**IMAGES_STATE, "source": "-1"}},
            {**good, "state": {**IMAGES_STATE, "source": 0}},
            {**good, "state": {**IMAGES_STATE, "folder": None}},
            {**good, "state": {**IMAGES_STATE, "folder": "x" * 1025}},
            {**good, "state": {**IMAGES_STATE, "tags": "cat"}},
            {**good, "state": {**IMAGES_STATE, "tags": [1]}},
            {**good, "state": {**IMAGES_STATE, "tags": [" "]}},
            {**good, "state": {**IMAGES_STATE, "tags": ["a,b"]}},
            {**good, "state": {**IMAGES_STATE, "tags": ["x" * 51]}},
            {**good, "state": {**IMAGES_STATE, "tags": [f"t{n}" for n in range(21)]}},
            # Library has no source, folder or media, and its own sorts.
            {"page": "library", "name": "L", "state": IMAGES_STATE},
            {
                "page": "library",
                "name": "L",
                "state": {**LIBRARY_STATE, "sort": "name-desc"},
            },
        ]
        for body in cases:
            with self.subTest(body=body):
                response, data = await self.send("POST", body=body)
                self.assertEqual(response.status, 400)
                self.assertFalse(data["success"])
                self.assertIsInstance(data["error"], str)
        response = await self.client.post(URL, data="{not json")
        self.assertEqual(response.status, 400)
        self.assertFalse((await response.json())["success"])
        _, listed = await self.send("GET")
        self.assertEqual(listed["views"], [])

    async def test_encoded_state_is_capped(self):
        # Each value is within its own limit; together they pass 4 KB.
        state = {
            **IMAGES_STATE,
            "q": "é" * 500,
            "folder": "é" * 1024,
            "tags": ["é" * 50 for _ in range(20)],
        }
        encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        self.assertGreater(len(encoded.encode("utf-8")), MAX_STATE_BYTES)
        response, data = await self.send(
            "POST", body={"page": "images", "name": "Big", "state": state}
        )
        self.assertEqual(response.status, 400)
        self.assertIn("4 KB", data["error"])

    async def test_every_documented_value_is_accepted(self):
        for sort in ("newest", "oldest", "name", "name-desc", "rating"):
            for media in ("all", "image", "video"):
                await self.create(state={"sort": sort, "media": media, "source": "3"})
        for sort in ("newest", "oldest", "name", "rating"):
            await self.create(page="library", state={"sort": sort, "tags": ["a b"]})
        _, listed = await self.send("GET")
        self.assertEqual(len(listed["views"]), 19)

    async def test_rename_and_delete_validate_id_and_body(self):
        view = await self.create()
        for path in ("/0", "/-1", "/abc", "/1.5", f"/{2**63}"):
            for method, body in (("PATCH", {"name": "X"}), ("DELETE", None)):
                with self.subTest(path=path, method=method):
                    response, data = await self.send(method, path, body)
                    self.assertEqual(response.status, 400)
                    self.assertFalse(data["success"])
        for body in ({}, {"name": ""}, {"name": "X", "page": "library"}, ["X"]):
            with self.subTest(body=body):
                response, _ = await self.send("PATCH", f"/{view['id']}", body)
                self.assertEqual(response.status, 400)
        _, listed = await self.send("GET")
        self.assertEqual(listed["views"], [view])

    async def test_database_failure_is_a_500_envelope(self):
        def broken():
            raise sqlite3.OperationalError("disk I/O error")

        self.api.db.list_saved_views = broken
        with self.assertLogs("prompt_manager.api", level="ERROR"):
            response, data = await self.send("GET")
        self.assertEqual(response.status, 500)
        self.assertEqual(
            data, {"success": False, "error": "Saved view operation failed"}
        )


class TestSavedViewsSchema(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "prompts.db")
        self.db = PromptDatabase(self.path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        PromptModel.close_all_instances()
        assert_closed(self, self.tmpdir)
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _source(self, name="source.db", views=()):
        """A closed database file holding a prompt and the given views."""
        path = os.path.join(self.tmpdir, name)
        src = PromptDatabase(path)
        try:
            src.save_prompt(text="kept", prompt_hash=generate_prompt_hash("kept"))
            for page, view_name, state in views:
                src.create_saved_view(page, view_name, state)
        finally:
            src.close_all()
        return path

    def test_current_database_has_a_valid_table(self):
        conn = self.db.model.get_connection()
        self.assertIsNone(saved_views_schema_error(conn, allow_missing=False))
        ok, reason = PromptModel.verify_database_file(
            self.path, require_library_state=True
        )
        self.assertTrue(ok, reason)

    def test_migration_is_idempotent(self):
        conn = self.db.model.get_connection()
        self.db.create_saved_view("images", "Cats", IMAGES_STATE)
        migrate_saved_views(conn)
        self.assertEqual(len(self.db.list_saved_views()), 1)

    def test_backup_restore_round_trip_keeps_saved_views(self):
        self.db.create_saved_view("library", "Live", LIBRARY_STATE)
        src_path = self._source(
            views=[
                ("images", "Cats", IMAGES_STATE),
                ("library", "Best", LIBRARY_STATE),
            ]
        )
        # The views may still sit in the WAL; the backup must carry them.
        backup = os.path.join(self.tmpdir, "backup.db")
        reopened = PromptDatabase(src_path)
        try:
            self.assertTrue(reopened.model.backup_database(backup))
        finally:
            reopened.close_all()

        previous = self.db.model.restore_from_file(backup)

        self.assertEqual(
            [
                (view["page"], view["name"], view["state"])
                for view in self.db.list_saved_views()
            ],
            [("images", "Cats", IMAGES_STATE), ("library", "Best", LIBRARY_STATE)],
        )
        created = self.db.create_saved_view("images", "After", IMAGES_STATE)
        self.assertEqual(self.db.list_saved_views()[-1], created)
        with closing(sqlite3.connect(previous)) as conn:
            self.assertEqual(
                conn.execute(f"SELECT name FROM {TABLE}").fetchall(), [("Live",)]
            )

    def test_restore_old_backup_without_saved_views(self):
        self.db.create_saved_view("images", "Live", IMAGES_STATE)
        src = self._source()
        with closing(sqlite3.connect(src)) as conn:
            conn.execute(f"DROP TABLE {TABLE}")
            conn.commit()
        self.assertTrue(PromptModel.verify_database_file(src)[0])
        ok, _ = PromptModel.verify_database_file(src, require_library_state=True)
        self.assertFalse(ok)

        self.db.model.restore_from_file(src)

        self.assertEqual(self.db.list_saved_views(), [])
        self.db.create_saved_view("library", "New", LIBRARY_STATE)
        self.assertEqual(len(self.db.list_saved_views()), 1)
        with closing(sqlite3.connect(src)) as conn:  # the source is untouched
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)
                ).fetchone()
            )

    def _tampered(self, *statements, views=(("images", "Cats", IMAGES_STATE),)):
        src = self._source(
            name=f"tampered{len(os.listdir(self.tmpdir))}.db", views=views
        )
        with closing(sqlite3.connect(src)) as conn:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        return src

    def test_restore_rejects_tampered_saved_views(self):
        bad_state = json.dumps({**IMAGES_STATE, "unknown": 1})
        cases = {
            "definition": (
                f"DROP TABLE {TABLE}",
                f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, page TEXT, "
                "name TEXT, state TEXT, position INTEGER, created_at TEXT, "
                "updated_at TEXT)",
            ),
            "index": (f"CREATE INDEX saved_views_extra ON {TABLE}(name)",),
            "page": (
                "PRAGMA ignore_check_constraints = ON",
                f"UPDATE {TABLE} SET page = 'history'",
            ),
            "name": (f"UPDATE {TABLE} SET name = ''",),
            "long name": (f"UPDATE {TABLE} SET name = '{'x' * 101}'",),
            "untrimmed": (f"UPDATE {TABLE} SET name = ' Cats'",),
            "blob name": (f"UPDATE {TABLE} SET name = CAST('Cats' AS BLOB)",),
            "state json": (f"UPDATE {TABLE} SET state = '{{'",),
            "state keys": (f"UPDATE {TABLE} SET state = '{bad_state}'",),
            "state size": (
                f"UPDATE {TABLE} SET state = '{'x' * (MAX_STATE_BYTES + 1)}'",
            ),
            "position": (f"UPDATE {TABLE} SET position = 'first'",),
            "timestamp": (f"UPDATE {TABLE} SET created_at = X'00'",),
        }
        for label, statements in cases.items():
            with self.subTest(label=label):
                src = self._tampered(*statements)
                ok, reason = PromptModel.verify_database_file(src)
                self.assertFalse(ok)
                self.assertIn("Saved views", reason)
                with self.assertRaises(ValueError):
                    self.db.model.restore_from_file(src)

    def test_migration_refuses_an_unsupported_table(self):
        path = os.path.join(self.tmpdir, "foreign.db")
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY)")
            with self.assertRaises(sqlite3.DatabaseError):
                migrate_saved_views(conn)
            # The savepoint rolled back, so the foreign table is untouched.
            self.assertEqual(
                conn.execute(
                    f"SELECT COUNT(*) FROM pragma_table_info('{TABLE}')"
                ).fetchone()[0],
                1,
            )
        self.assertTrue(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
