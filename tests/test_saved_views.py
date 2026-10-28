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
    SavedViewInvalid,
    encode_state,
    migrate_saved_views,
    normalize_state,
    saved_views_schema_error,
    validate_name,
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
    "favorites": False,
    "collection": "",
}
LIBRARY_STATE = {"q": "", "category": "", "sort": "rating", "tags": []}
IMAGES_DEFAULTS = {
    "q": "",
    "category": "",
    "sort": "newest",
    "source": "all",
    "folder": "",
    "media": "all",
    "tags": [],
    "favorites": False,
    "collection": "",
}
LIBRARY_DEFAULTS = {"q": "", "category": "", "sort": "newest", "tags": []}
# A row saved by an older build: no favorites key, a retired key, a sort
# that is no longer offered and an over-long folder.
OLD_IMAGES_STATE = json.dumps(
    {
        "q": "sunset",
        "category": "",
        "sort": "shuffle",
        "source": "all",
        "folder": "x" * 2000,
        "media": "image",
        "tags": ["golden"],
        "layout": "grid",
    }
)


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
            # Favorites (the Favorites page's filter) is a JSON boolean.
            {**good, "state": {**IMAGES_STATE, "favorites": "true"}},
            {**good, "state": {**IMAGES_STATE, "favorites": 1}},
            {**good, "state": {**IMAGES_STATE, "favorites": None}},
            {
                **good,
                "state": {k: v for k, v in IMAGES_STATE.items() if k != "favorites"},
            },
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
        favorite = await self.create(state={"favorites": True})
        self.assertIs(favorite["state"]["favorites"], True)
        for sort in ("newest", "oldest", "name", "rating"):
            await self.create(page="library", state={"sort": sort, "tags": ["a b"]})
        _, listed = await self.send("GET")
        self.assertEqual(len(listed["views"]), 20)

    async def test_write_requires_the_collection_key(self):
        # #331 D140: a view saved on a collection page keeps the collection id.
        without = {k: v for k, v in IMAGES_STATE.items() if k != "collection"}
        good = {"page": "images", "name": "Grid", "state": IMAGES_STATE}
        cases = [
            {**good, "state": without},
            {**good, "state": {**IMAGES_STATE, "collection": "0"}},
            {**good, "state": {**IMAGES_STATE, "collection": "x"}},
            {**good, "state": {**IMAGES_STATE, "collection": 12}},
            {**good, "state": {**IMAGES_STATE, "collection": "012"}},
            {**good, "state": {**IMAGES_STATE, "collection": "1" * 10}},
            {**good, "state": {**IMAGES_STATE, "collection": None}},
            # Favorites and a collection are never both a view's filter.
            {
                **good,
                "state": {**IMAGES_STATE, "favorites": True, "collection": "3"},
            },
        ]
        for body in cases:
            with self.subTest(body=body):
                response, data = await self.send("POST", body=body)
                self.assertEqual(response.status, 400, data)
                self.assertFalse(data["success"])
        for collection in ("1", "12", "9" * 9):
            view = await self.create(state={"collection": collection})
            self.assertEqual(view["state"]["collection"], collection)
            self.assertIs(view["state"]["favorites"], False)

    async def test_an_old_row_without_a_collection_reads_empty(self):
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('images', 'Old', ?, 0, 'x', 'x')",
                (json.dumps({"q": "cat", "favorites": True}),),
            )
        _, data = await self.send("GET")
        (view,) = data["views"]
        self.assertEqual(
            view["state"], {**IMAGES_DEFAULTS, "q": "cat", "favorites": True}
        )
        self.assertEqual(view["state"]["collection"], "")

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

    async def test_internal_value_errors_are_a_generic_500(self):
        # Only the request's own validation is a 400; a ValueError or
        # TypeError raised by the database layer is a server fault (M4).
        for error in (
            ValueError("secret parser detail"),
            TypeError("secret type detail"),
            json.JSONDecodeError("secret json detail", "{", 0),
        ):

            def broken(*_args, error=error):
                raise error

            for method, path, body, attr in (
                ("GET", "", None, "list_saved_views"),
                (
                    "POST",
                    "",
                    {"page": "library", "name": "L", "state": LIBRARY_STATE},
                    "create_saved_view",
                ),
                ("PATCH", "/1", {"name": "X"}, "rename_saved_view"),
                ("DELETE", "/1", None, "delete_saved_view"),
            ):
                with self.subTest(error=type(error).__name__, method=method):
                    setattr(self.api.db, attr, broken)
                    try:
                        with self.assertLogs("prompt_manager.api", level="ERROR"):
                            response, data = await self.send(method, path, body)
                    finally:
                        delattr(self.api.db, attr)
                    self.assertEqual(response.status, 500)
                    self.assertEqual(
                        data,
                        {"success": False, "error": "Saved view operation failed"},
                    )

    async def test_a_corrupt_state_is_skipped_by_the_listing(self):
        kept = await self.create(page="library", name="Kept", state={})
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('images', 'Broken', '{', 0, 'x', 'x')"
            )
        response, data = await self.send("GET")
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["views"], [kept])
        # Hidden from the listing, it is not found by rename either.
        broken_id = kept["id"] + 1
        response, _ = await self.send("PATCH", f"/{broken_id}", {"name": "X"})
        self.assertEqual(response.status, 404)
        response, _ = await self.send("DELETE", f"/{broken_id}")
        self.assertEqual(response.status, 200)

    async def test_a_row_saved_under_older_rules_lists_normalized(self):
        stale = json.dumps(
            {
                "q": "cat",
                "sort": "random",
                "media": "video",
                "tags": ["ok", 7],
                "retired": True,
            }
        )
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('images', ' Old\tview ', ?, 0, 'x', 'x')",
                (stale,),
            )
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('library', '', '{}', 1, 'x', 'x')"
            )
        response, data = await self.send("GET")
        self.assertEqual(response.status, 200, data)
        images, library = data["views"]
        self.assertEqual(images["name"], "Oldview")
        self.assertEqual(
            images["state"],
            {**IMAGES_DEFAULTS, "q": "cat", "media": "video"},
        )
        self.assertEqual(library["name"], "Saved view")
        self.assertEqual(library["state"], LIBRARY_DEFAULTS)
        # A stale view still renames and deletes.
        response, data = await self.send(
            "PATCH", f"/{images['id']}", {"name": "Renamed"}
        )
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["view"]["state"], images["state"])


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
            "long name": (f"UPDATE {TABLE} SET name = '{'x' * 401}'",),
            "blob name": (f"UPDATE {TABLE} SET name = CAST('Cats' AS BLOB)",),
            "state json": (f"UPDATE {TABLE} SET state = '{{'",),
            "state array": (f"UPDATE {TABLE} SET state = '[]'",),
            "state string": (f"UPDATE {TABLE} SET state = '\"q\"'",),
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

    def _insert_old_rows(self, path):
        """Rows written under older value rules, straight into the file."""
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('images', 'Golden hour', ?, 5, 'x', 'x')",
                (OLD_IMAGES_STATE,),
            )
            conn.execute(
                f"INSERT INTO {TABLE} (page, name, state, position, created_at, "
                "updated_at) VALUES ('library', ?, ?, 6, 'x', 'x')",
                (" " + "n" * 120, json.dumps({"sort": "name-desc", "q": 4})),
            )
            conn.commit()

    def _assert_old_rows_normalized(self, views):
        self.assertEqual(
            [(view["page"], view["name"], view["state"]) for view in views],
            [
                (
                    "images",
                    "Golden hour",
                    {
                        **IMAGES_DEFAULTS,
                        "q": "sunset",
                        "media": "image",
                        "tags": ["golden"],
                    },
                ),
                ("library", "n" * 100, LIBRARY_DEFAULTS),
            ],
        )

    def test_a_database_with_rows_from_older_rules_still_opens(self):
        self.db.close_all()
        self._insert_old_rows(self.path)
        PromptModel.reset_schema_cache()

        reopened = PromptDatabase(self.path)
        try:
            self._assert_old_rows_normalized(reopened.list_saved_views())
        finally:
            reopened.close_all()
        ok, reason = PromptModel.verify_database_file(
            self.path, require_library_state=True
        )
        self.assertTrue(ok, reason)

    def test_restore_accepts_rows_from_older_rules(self):
        src = self._source()
        self._insert_old_rows(src)
        ok, reason = PromptModel.verify_database_file(src, require_library_state=True)
        self.assertTrue(ok, reason)

        self.db.model.restore_from_file(src)

        self._assert_old_rows_normalized(self.db.list_saved_views())

    def _insert_corrupt_rows(self, path):
        """A valid view between two rows whose state is no JSON object."""
        with closing(sqlite3.connect(path)) as conn:
            ids = []
            for name, state in (
                ("Broken", "{not json"),
                ("Kept", json.dumps(LIBRARY_STATE)),
                ("Array", "[1, 2]"),
            ):
                ids.append(
                    conn.execute(
                        f"INSERT INTO {TABLE} (page, name, state, position, "
                        "created_at, updated_at) VALUES ('library', ?, ?, 0, "
                        "'x', 'x')",
                        (name, state),
                    ).lastrowid
                )
            conn.commit()
        return ids

    def test_startup_tolerates_a_corrupt_state_and_list_skips_it(self):
        self.db.close_all()
        broken, kept, array = self._insert_corrupt_rows(self.path)
        PromptModel.reset_schema_cache()

        with self.assertLogs("prompt_manager.database", level="WARNING") as logs:
            reopened = PromptDatabase(self.path)
        try:
            self.assertEqual(
                [(view["id"], view["name"]) for view in reopened.list_saved_views()],
                [(kept, "Kept")],
            )
        finally:
            reopened.close_all()
        output = "\n".join(logs.output)
        self.assertIn(f"id {broken}", output)
        self.assertIn(f"id {array}", output)
        self.assertNotIn("not json", output)
        # The rows stay in place, untouched.
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 3
            )

    def test_restore_rejects_a_backup_with_a_corrupt_state(self):
        src = self._source()
        self._insert_corrupt_rows(src)
        ok, reason = PromptModel.verify_database_file(src)
        self.assertFalse(ok)
        self.assertEqual(reason, "Saved views contains invalid values")
        with self.assertRaises(ValueError):
            self.db.model.restore_from_file(src)

    def test_normalize_state_keeps_valid_values_and_defaults_the_rest(self):
        self.assertEqual(normalize_state("images", IMAGES_STATE), IMAGES_STATE)
        self.assertEqual(normalize_state("library", LIBRARY_STATE), LIBRARY_STATE)
        self.assertEqual(
            normalize_state("images", json.dumps(IMAGES_STATE)), IMAGES_STATE
        )
        for raw in (None, "{", "[]", '"q"', 7, ["q"], b"{}"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_state("library", raw), LIBRARY_DEFAULTS)
        self.assertEqual(
            normalize_state(
                "images",
                {
                    "q": "bad\x00",
                    "source": 3,
                    "favorites": "true",
                    "tags": [f"t{n}" for n in range(21)],
                    "folder": "kept",
                },
            ),
            {**IMAGES_DEFAULTS, "folder": "kept"},
        )
        # A stored row with both filters (never written by this build) opens the
        # collection, as a listing URL with both does.
        self.assertEqual(
            normalize_state("images", {"favorites": True, "collection": "4"}),
            {**IMAGES_DEFAULTS, "collection": "4"},
        )
        for bad in ("0", "x", 12, "012"):
            with self.subTest(collection=bad):
                self.assertEqual(
                    normalize_state("images", {"collection": bad}), IMAGES_DEFAULTS
                )
        self.assertEqual(normalize_state("history", {}), {})

    def test_validation_errors_are_saved_view_invalid(self):
        for call in (
            lambda: validate_name(""),
            lambda: validate_name(3),
            lambda: encode_state("history", {}),
            lambda: encode_state("library", {"q": ""}),
            lambda: encode_state("library", {**LIBRARY_STATE, "sort": "x"}),
        ):
            with self.assertRaises(SavedViewInvalid):
                call()
        self.assertTrue(issubclass(SavedViewInvalid, ValueError))

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
