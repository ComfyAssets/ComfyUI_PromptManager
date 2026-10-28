"""The prompt-link read of the workspace Images page (#354).

``PromptDatabase.get_image_link_matches`` returns every row of its filter
SELECT in one SQLite step (one JSON value) instead of one step per row, so the
read does not have to win the GIL back once per linked image while ComfyUI
generates. Its result must equal the streamed read it replaces, which stays as
the fallback, on every filter and on odd values.
"""

import itertools
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import operations  # noqa: E402
from database.operations import PromptDatabase, link_filter_query  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

LOGGER = "prompt_manager.database"
MISSING_FUNCTION_SQL = (
    "SELECT pm_missing_function(json_group_array(json_array("
    "file_path, rating, matched, positive))) FROM ({select})"
)
QUERIES = ("", "lantern", "LANTERN title", "no-such-text")
CATEGORIES = (None, "cat-a", "cat-b", "cat-none")
TAG_SETS = ([], ["t1"], ["t1", "t2"], ["t-none"])

PROMPTS = (
    # id, text, category, rating, tags, library title
    (1, "red lantern at dusk", "cat-a", 5, ("t1", "t2"), None),
    (2, "blue harbor", "cat-a", None, ("t1",), None),
    (3, "green field", "cat-b", 3, (), "Lantern title"),
    (4, "pair positive", "cat-b", 4, ("t2",), None),
    (5, "pair negative lantern", "cat-a", 1, ("t1",), None),
)
QUOTED = '/out/it\'s "quoted".png'
BACKSLASHED = "C:\\out\\back\\slash.png"
UNICODE = "/out/ünïcødé 画像.png"
NEWLINE = "/out/new\nline.png"
IMAGES = (
    # prompt id, file path
    (1, "/out/a.png"),
    (2, QUOTED),
    (2, BACKSLASHED),
    (3, UNICODE),
    (1, NEWLINE),
    (1, "/out/ambiguous.png"),
    (2, "/out/ambiguous.png"),
    (5, "/out/pair.png"),  # linked through pair 1: its positive is prompt 4
    (3, "/out/two-pairs.png"),  # two pairs: no single pair, so prompt 3
    (1, None),  # no file path: never listed
)


class LinkMatchesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name))
        self.db = PromptDatabase(str(self.root / "prompts.db"))
        for name in ("_one_step_unsupported", "_one_step_warned"):
            patcher = patch.object(operations, name, False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.statements = []
        original = self.db.model.get_connection

        def traced_connection():
            connection = original()
            connection.set_trace_callback(self.statements.append)
            return connection

        patcher = patch.object(
            self.db.model, "get_connection", side_effect=traced_connection
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.root)
        self.temp.cleanup()

    def seed(self):
        with self.db.model.get_connection() as conn:
            for prompt_id, text, category, rating, tags, title in PROMPTS:
                conn.execute(
                    "INSERT INTO prompts(id,text,category,rating,hash) "
                    "VALUES(?,?,?,?,?)",
                    (prompt_id, text, category, rating, f"hash-{prompt_id}"),
                )
                for tag in tags:
                    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                    conn.execute(
                        "INSERT INTO prompt_tags(prompt_id,tag_id) "
                        "SELECT ?, id FROM tags WHERE name=?",
                        (prompt_id, tag),
                    )
                if title:
                    conn.execute(
                        "INSERT INTO prompt_library_state(prompt_id,title) "
                        "VALUES(?,?)",
                        (prompt_id, title),
                    )
            ids = {}
            for prompt_id, path in IMAGES:
                cursor = conn.execute(
                    "INSERT INTO generated_images"
                    "(prompt_id,image_path,filename,file_path) VALUES(?,?,?,?)",
                    (prompt_id, path or "/elsewhere/x.png", "x.png", path),
                )
                ids.setdefault(path, cursor.lastrowid)
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(1,4,5),(2,3,5),(3,3,NULL)"
            )
            conn.executemany(
                "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                "VALUES(?,?,'{}')",
                [
                    (ids["/out/pair.png"], 1),
                    (ids["/out/two-pairs.png"], 2),
                    (ids["/out/two-pairs.png"], 3),
                ],
            )

    def add_image(self, prompt_id, path):
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO generated_images(prompt_id,image_path,filename,file_path)"
                " VALUES(?,?,?,?)",
                (prompt_id, "/blob/x.png", "x.png", path),
            )

    def streamed(self, q, category, tags, every_link=False, wanted=None):
        """Today's read, called directly (the fallback)."""
        sql, params = link_filter_query(q, category, tags, every_link=every_link)
        with self.db.model.get_connection() as conn:
            return operations._streamed_link_matches(conn, sql, params, wanted)

    def selects(self):
        found = [s for s in self.statements if s.lstrip().upper().startswith("SELECT")]
        self.statements.clear()
        return found


class TestOneStepLinkRead(LinkMatchesTestCase):
    def test_plain_read_returns_every_unambiguous_link(self):
        self.seed()
        matches = self.db.get_image_link_matches("", None, [], every_link=True)
        self.assertEqual(
            matches,
            {
                "/out/a.png": (True, 5, 1),
                QUOTED: (True, None, 2),
                BACKSLASHED: (True, None, 2),
                UNICODE: (True, 3, 3),
                NEWLINE: (True, 5, 1),
                "/out/pair.png": (True, 4, 4),
                "/out/two-pairs.png": (True, 3, 3),
            },
        )
        self.assertIsNone(matches[QUOTED][1])
        self.assertIsInstance(matches["/out/a.png"][1], int)
        self.assertIsInstance(matches["/out/a.png"][2], int)

    def test_search_hits_follow_text_title_and_tags(self):
        self.seed()
        self.assertEqual(
            self.db.get_image_link_matches("lantern", None, []),
            {
                "/out/a.png": (True, 5, 1),
                NEWLINE: (True, 5, 1),
                UNICODE: (True, 3, 3),
                "/out/two-pairs.png": (True, 3, 3),
            },
        )
        self.assertEqual(
            self.db.get_image_link_matches("lantern", "cat-a", []),
            {
                "/out/a.png": (True, 5, 1),
                NEWLINE: (True, 5, 1),
                QUOTED: (False, None, 2),
                BACKSLASHED: (False, None, 2),
            },
        )

    def test_every_filter_equals_the_streamed_read(self):
        self.seed()
        wanted_sets = (None, {"/out/a.png", UNICODE, "/out/pair.png", "/absent.png"})
        for q, category, tags, every_link, wanted in itertools.product(
            QUERIES, CATEGORIES, TAG_SETS, (False, True), wanted_sets
        ):
            with self.subTest(
                q=q, category=category, tags=tags, every=every_link, wanted=wanted
            ):
                expected = self.streamed(q, category, tags, every_link, wanted)
                self.statements.clear()
                actual = self.db.get_image_link_matches(
                    q, category, list(tags), every_link=every_link, wanted=wanted
                )
                self.assertEqual(actual, expected)
                self.assertEqual(
                    [type(value) for row in actual.values() for value in row],
                    [type(value) for row in expected.values() for value in row],
                )
                found = self.selects()
                self.assertEqual(len(found), 1, found)
                self.assertIn("json_group_array", found[0])

    def test_empty_database_returns_nothing_in_one_statement(self):
        self.assertEqual(
            self.db.get_image_link_matches("", None, [], every_link=True), {}
        )
        self.assertEqual(self.db.get_image_link_matches("x", "c", ["t"]), {})
        found = self.selects()
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all("json_group_array" in query for query in found))

    def test_wanted_limits_the_result_to_the_listing(self):
        self.seed()
        self.assertEqual(
            self.db.get_image_link_matches(
                "", None, [], every_link=True, wanted={"/out/pair.png", "/x.png"}
            ),
            {"/out/pair.png": (True, 4, 4)},
        )
        self.assertEqual(
            self.db.get_image_link_matches("", None, [], every_link=True, wanted=set()),
            {},
        )

    def test_wrapped_query_keeps_the_ambiguity_probe_an_index_search(self):
        self.seed()
        sql, params = link_filter_query("lantern", "cat-a", ["t1"], every_link=True)
        with self.db.model.get_connection() as conn:
            plan = [
                row[3]
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN " + operations.link_matches_json_query(sql),
                    params,
                )
            ]
        self.assertIn(
            "SEARCH other USING COVERING INDEX idx_image_file_path (file_path=?)", plan
        )
        self.assertFalse([step for step in plan if step.startswith("SCAN other")])


class TestStreamedFallback(LinkMatchesTestCase):
    def test_missing_json_functions_stream_and_stop_trying(self):
        self.seed()
        expected = self.streamed("", None, [], True)
        self.statements.clear()
        wrapper = operations.link_matches_json_query
        with (
            patch.object(operations, "_LINK_MATCHES_JSON_SQL", MISSING_FUNCTION_SQL),
            patch.object(
                operations, "link_matches_json_query", side_effect=wrapper
            ) as attempts,
            self.assertLogs(LOGGER, "WARNING") as logged,
        ):
            first = self.db.get_image_link_matches("", None, [], every_link=True)
            found = self.selects()
            self.assertEqual(attempts.call_count, 1)
            second = self.db.get_image_link_matches("", None, [], every_link=True)
            again = self.selects()
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(len(logged.records), 1, logged.output)
        self.assertIn("no such function: pm_missing_function", logged.output[0])
        self.assertTrue(operations._one_step_unsupported)
        # SQLite refuses the wrapped statement before it runs (not traced).
        self.assertEqual(len(found), 1, found)
        self.assertNotIn("json_group_array", found[0])
        # The next read skips the attempt for the rest of the process.
        self.assertEqual(attempts.call_count, 1)
        self.assertEqual(len(again), 1, again)
        self.assertNotIn("json_group_array", again[0])

    def test_blob_path_streams_that_read_and_keeps_trying(self):
        self.seed()
        self.add_image(2, b"/out/blob.png")
        expected = self.streamed("", None, [], True)
        self.assertEqual(expected[b"/out/blob.png"], (True, None, 2))
        self.statements.clear()
        with self.assertLogs(LOGGER, "WARNING") as logged:
            first = self.db.get_image_link_matches("", None, [], every_link=True)
            second = self.db.get_image_link_matches("", None, [], every_link=True)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(len(logged.records), 1, logged.output)
        self.assertFalse(operations._one_step_unsupported)
        found = self.selects()
        self.assertEqual(sum("json_group_array" in query for query in found), 2)
        self.assertEqual(len(found), 4, found)

    def test_blob_that_reads_as_json_still_streams(self):
        # SQLite 3.45+ reads a BLOB that is valid JSONB as JSON (b"\x00" is
        # null) instead of refusing it; the streamed read keeps the bytes.
        self.seed()
        self.add_image(2, b"\x00")
        expected = self.streamed("", None, [], True)
        self.assertIn(b"\x00", expected)
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertEqual(
                self.db.get_image_link_matches("", None, [], every_link=True), expected
            )
        self.assertFalse(operations._one_step_unsupported)

    def test_blob_rating_streams(self):
        self.seed()
        with self.db.model.get_connection() as conn:
            # Only a database written around the CHECK can hold one.
            conn.execute("PRAGMA ignore_check_constraints=ON")
            conn.execute("UPDATE prompts SET rating=? WHERE id=3", (b"\x00",))
            conn.execute("PRAGMA ignore_check_constraints=OFF")
        expected = self.streamed("", None, [], True)
        self.assertEqual(expected[UNICODE], (True, b"\x00", 3))
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertEqual(
                self.db.get_image_link_matches("", None, [], every_link=True), expected
            )

    def test_a_failed_streamed_read_still_raises(self):
        with (
            patch.object(operations, "_LINK_MATCHES_JSON_SQL", MISSING_FUNCTION_SQL),
            patch.object(operations, "_LINK_FILTER_SQL", "SELECT broken FROM nowhere"),
            self.assertLogs(LOGGER, "WARNING"),
        ):
            with self.assertRaises(operations.sqlite3.OperationalError):
                self.db.get_image_link_matches("", None, [], every_link=True)


if __name__ == "__main__":
    unittest.main()
