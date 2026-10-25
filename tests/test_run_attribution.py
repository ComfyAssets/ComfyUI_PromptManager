"""#342: recorded history attributes a file only to one certain, stored prompt."""

import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from unittest import mock

from database.run_attribution import stored_run_prompt
from tests import test_execution_history_database as history_tests
from tests.test_api_admin import AdminAPITestCase
from tests.test_execution_context_database import snapshot
from tests.test_prompt_attribution import issue_graph, llm_text_graph, make_tracker
from utils.execution_contexts import ContextBundle, RecipeContext
from utils.execution_outputs import (
    OutputBundle,
    OutputReference,
    snapshot_output_roots,
)
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler

STYLE = "windows" if os.name == "nt" else "posix"
LLM_TEXT = "Photorealistic aerial landscape of misty mountains at dawn"


def now_ms():
    return time.time_ns() // 1_000_000


class RecordedRuns:
    """Write runs into execution history the way the capture pipeline does."""

    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def record_run(self, db, output_root, files, positives, item=0, window=None):
        started, finished = window or (now_ms() - 5000, now_ms() + 5000)
        start = self.start(db, item, started)
        db.write_execution_start(start)
        finish = self.finish(start, time=finished)
        db.write_execution_finish(finish)
        db.write_execution_contexts(
            start.run_id,
            start.storage_epoch,
            ContextBundle(
                tuple(
                    RecipeContext(str(4 + i), snapshot(text), snapshot(""), "inferred")
                    for i, text in enumerate(positives)
                ),
                "complete",
                "captured",
            ),
        )
        root = snapshot_output_roots(STYLE, output_root, output_root).output_key
        references = tuple(
            OutputReference("9", i, "output", sub, name, STYLE, root)
            for i, (sub, name) in enumerate(files)
        )
        db.write_execution_outputs_for_finish(
            finish, OutputBundle(references, "complete", "reported")
        )
        return start.run_id


class TestStoredRunPrompt(RecordedRuns, unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database

    def setUp(self):  # noqa: F811 - the shared temp dir and database list first
        history_tests.HistoryDatabaseTests.setUp(self)
        self.db = self.database()
        self.output = str(Path(self.temp.name) / "output")
        os.makedirs(os.path.join(self.output, "sub"))
        self.image = os.path.join(self.output, "sub", "img_00001_.png")
        Path(self.image).write_bytes(b"png")

    def save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def attribute(self, path=None):
        return stored_run_prompt(self.db, path or self.image, self.output)

    def test_one_run_naming_one_stored_prompt_attributes_the_file(self):
        prompt_id = self.save("an llm prompt")
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], ["an llm prompt"]
        )
        self.assertEqual(self.attribute(), prompt_id)

    def test_text_that_is_not_stored_attributes_nothing(self):
        """The #342 run: raw editor text was empty, nothing was stored."""
        self.save("a woman holding a coffee cup")
        self.record_run(self.db, self.output, [("sub", "img_00001_.png")], [""])
        self.assertIsNone(self.attribute())

    def test_two_runs_reporting_the_same_file_attribute_nothing(self):
        self.save("first")
        self.save("second")
        self.record_run(self.db, self.output, [("sub", "img_00001_.png")], ["first"])
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], ["second"], item=1
        )
        self.assertIsNone(self.attribute())

    def test_a_file_written_outside_the_run_attributes_nothing(self):
        self.save("an llm prompt")
        self.record_run(
            self.db,
            self.output,
            [("sub", "img_00001_.png")],
            ["an llm prompt"],
            window=(1_000_000, 2_000_000),
        )
        self.assertIsNone(self.attribute())

    def test_contexts_naming_different_prompts_attribute_nothing(self):
        self.save("left")
        self.save("right")
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], ["left", "right"]
        )
        self.assertIsNone(self.attribute())

    def test_files_outside_the_output_root_or_unrecorded_attribute_nothing(self):
        self.save("an llm prompt")
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], ["an llm prompt"]
        )
        outside = os.path.join(self.temp.name, "img_00001_.png")
        Path(outside).write_bytes(b"png")
        self.assertIsNone(self.attribute(outside))
        other = os.path.join(self.output, "sub", "other.png")
        Path(other).write_bytes(b"png")
        self.assertIsNone(self.attribute(other))
        self.assertIsNone(stored_run_prompt(self.db, self.image, None))
        self.assertIsNone(stored_run_prompt(self.db, self.image, "relative/root"))

    def test_a_library_without_history_tables_attributes_nothing(self):
        broken = mock.Mock()
        broken.model.get_connection.return_value.execute.side_effect = (
            sqlite3.OperationalError("no such table")
        )
        self.assertIsNone(stored_run_prompt(broken, self.image, self.output))

    def test_the_monitor_links_from_history_when_nothing_else_is_certain(self):
        prompt_id = self.save(LLM_TEXT)
        self.record_run(self.db, self.output, [("sub", "img_00001_.png")], [LLM_TEXT])
        handler = ImageGenerationHandler(self.db, make_tracker(self.db))
        linked = []
        handler.link_image_to_prompt = lambda path, prompt, meta: linked.append(
            prompt["id"]
        )
        with (
            mock.patch(
                "utils.image_monitor.host_output_directory", return_value=self.output
            ),
            mock.patch.object(
                handler.metadata_extractor,
                "extract_metadata",
                return_value={"prompt": llm_text_graph()},
            ),
        ):
            handler.process_new_image(self.image)
        self.assertEqual(linked, [prompt_id])


class TestScanUsesHistory(RecordedRuns, AdminAPITestCase):
    """The output scan applies the same rules as the monitor."""

    def linked_prompt(self, path):
        row = (
            self.api.db.model.get_connection()
            .execute(
                "SELECT prompt_id FROM generated_images WHERE image_path=?",
                (str(path),),
            )
            .fetchone()
        )
        return row[0] if row else None

    def write(self, name):
        path = self.output_dir / name
        path.write_bytes(b"png")
        return path

    def test_an_llm_text_file_links_to_the_prompt_its_run_stored(self):
        db = self.api.db
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        path = self.write("llm_00001_.png")
        self.record_run(db, str(self.output_dir), [("", path.name)], [LLM_TEXT])
        meta = {"prompt": json.dumps(llm_text_graph())}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "linked")
        self.assertEqual(self.linked_prompt(path), prompt_id)

    def test_a_metadata_less_file_links_through_its_recorded_run(self):
        """I1: --disable-metadata outputs carry no prompt; history still proves one."""
        db = self.api.db
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        path = self.write("bare_00001_.png")
        self.record_run(db, str(self.output_dir), [("", path.name)], [LLM_TEXT])
        self.assertEqual(self.api._ingest_scanned_file(path, {}), "linked")
        self.assertEqual(self.linked_prompt(path), prompt_id)
        self.assertEqual(self.api._ingest_scanned_file(path, None), "unchanged")
        detailed = self.api._ingest_scanned_file(path, {}, detailed=True)
        self.assertEqual(detailed["status"], "unchanged")
        self.assertTrue(detailed["title"])

    def test_a_metadata_less_file_without_a_recorded_run_is_still_skipped(self):
        path = self.write("bare_00002_.png")
        self.assertIsNone(self.api._ingest_scanned_file(path, {}))
        self.assertIsNone(self.api._ingest_scanned_file(path, {"workflow": {}}))
        self.assertIsNone(self.linked_prompt(path))

    def test_the_issue_file_stays_unlinked_even_with_its_pair_record(self):
        """Resolved with an empty stored positive: before #342 the scan raised."""
        db = self.api.db
        db.save_prompt(
            text="a woman holding a coffee cup",
            prompt_hash=generate_prompt_hash("a woman holding a coffee cup"),
        )
        path = self.write("moss_00009_.png")
        self.record_run(db, str(self.output_dir), [("", path.name)], [""])
        evidence = {
            "version": 1,
            "nodes": {
                "18:35": {
                    "records": [
                        {
                            "text": "",
                            "negative_text": "",
                            "positive_text": LLM_TEXT,
                            "negative_effective_text": "",
                            "prepend_text": LLM_TEXT,
                            "append_text": "",
                        }
                    ]
                }
            },
        }
        meta = {
            "prompt": json.dumps(issue_graph()),
            "comfyassets_prompt_pairs": json.dumps(evidence),
        }
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "unresolved")
        self.assertIsNone(self.linked_prompt(path))
        self.assertEqual(db.list_prompt_pairs()["pairs"], [])


class TestHostHelpers(unittest.TestCase):
    """The host lookups degrade to "no proof", never to an error."""

    def test_host_output_directory(self):
        import sys
        import types

        from database import run_attribution

        cases = (
            (types.SimpleNamespace(get_output_directory=lambda: "/out"), "/out"),
            (types.SimpleNamespace(get_output_directory=lambda: ""), None),
            (types.SimpleNamespace(get_output_directory=lambda: 1 / 0), None),
        )
        original = sys.modules.get("folder_paths")
        try:
            for module, expected in cases:
                sys.modules["folder_paths"] = module
                self.assertEqual(run_attribution.host_output_directory(), expected)
            sys.modules["folder_paths"] = None  # import fails: outside ComfyUI
            self.assertIsNone(run_attribution.host_output_directory())
        finally:
            if original is None:
                sys.modules.pop("folder_paths", None)
            else:
                sys.modules["folder_paths"] = original

    def test_run_window_needs_integer_times_and_a_readable_file(self):
        from database.run_attribution import _written_during

        self.assertFalse(_written_during(__file__, None, 5))
        self.assertFalse(_written_during(os.path.join("no", "such.png"), 0, 5))

    def test_executing_run_id(self):
        import sys
        import types

        from utils.run_keys import executing_run_id

        def host(context):
            def get_executing_context():
                if isinstance(context, Exception):
                    raise context
                return context

            return types.SimpleNamespace(get_executing_context=get_executing_context)

        original = sys.modules.get("comfy_execution.utils")
        try:
            for context, expected in (
                (types.SimpleNamespace(prompt_id="run-1"), "run-1"),
                (types.SimpleNamespace(prompt_id=""), None),
                (None, None),
                (RuntimeError("no context"), None),
            ):
                sys.modules["comfy_execution.utils"] = host(context)
                self.assertEqual(executing_run_id(), expected)
            sys.modules["comfy_execution.utils"] = None
            self.assertIsNone(executing_run_id())
        finally:
            if original is None:
                sys.modules.pop("comfy_execution.utils", None)
            else:
                sys.modules["comfy_execution.utils"] = original


class TestMonitorHistoryRecheck(RecordedRuns, unittest.TestCase):
    """I1: a keyless image gets one history-only re-check when its run is sealed."""

    def setUp(self):
        history_tests.HistoryDatabaseTests.setUp(self)
        self.db = history_tests.HistoryDatabaseTests.database(self)
        self.output = str(Path(self.temp.name) / "output")
        os.makedirs(self.output)
        self.handler = ImageGenerationHandler(self.db, make_tracker(self.db))
        self.handler.processing_delay = 0
        self.handler.settle_interval = 0.01
        self.addCleanup(self.handler.shutdown)
        self.linked = []
        self.handler.link_image_to_prompt = lambda path, prompt, meta: (
            self.linked.append((os.path.basename(path), prompt["id"]))
        )
        patches = (
            mock.patch(
                "utils.image_monitor.host_output_directory", return_value=self.output
            ),
            mock.patch.object(
                self.handler.metadata_extractor, "extract_metadata", return_value=None
            ),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    close_databases = history_tests.HistoryDatabaseTests.close_databases

    def image(self, name):
        path = os.path.join(self.output, name)
        Path(path).write_bytes(b"png")
        return path

    def save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_the_run_ending_after_processing_links_the_image_once(self):
        prompt_id = self.save(LLM_TEXT)
        path = self.image("late_00001_.png")
        self.handler.process_new_image(path)  # the run is still executing
        self.assertEqual(self.linked, [])
        self.assertIn(os.path.normcase(path), self.handler.awaiting_history())
        self.record_run(self.db, self.output, [("", "late_00001_.png")], [LLM_TEXT])
        self.assertTrue(self.wait_for(lambda: self.linked))
        self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.linked, [("late_00001_.png", prompt_id)])
        self.assertEqual(self.handler.awaiting_history(), [])
        # Another run reporting the same name later is not a second re-check
        self.record_run(
            self.db, self.output, [("", "late_00001_.png")], [LLM_TEXT], item=1
        )
        self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(len(self.linked), 1)

    def test_a_run_naming_nothing_stored_leaves_the_image_unlinked(self):
        path = self.image("empty_00001_.png")
        self.handler.process_new_image(path)
        self.record_run(self.db, self.output, [("", "empty_00001_.png")], [""])
        self.assertTrue(self.wait_for(lambda: not self.handler.awaiting_history()))
        self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.linked, [])

    def test_history_found_at_once_leaves_nothing_awaiting(self):
        prompt_id = self.save(LLM_TEXT)
        self.record_run(self.db, self.output, [("", "done_00001_.png")], [LLM_TEXT])
        self.handler.process_new_image(self.image("done_00001_.png"))
        self.assertEqual(self.linked, [("done_00001_.png", prompt_id)])
        self.assertEqual(self.handler.awaiting_history(), [])

    def test_keyed_images_do_not_wait_for_history(self):
        with mock.patch.object(
            self.handler.metadata_extractor,
            "extract_metadata",
            return_value={"prompt": llm_text_graph()},
        ):
            self.handler.process_new_image(self.image("keyed_00001_.png"))
        self.assertEqual(self.handler.awaiting_history(), [])

    def test_the_waiting_set_is_bounded_and_expires(self):
        from utils import image_monitor

        with mock.patch.object(image_monitor, "MAX_AWAITING_HISTORY", 2):
            for n in range(3):
                self.handler.process_new_image(self.image(f"b_{n}.png"))
        self.assertEqual(
            [os.path.basename(p) for p in self.handler.awaiting_history()],
            ["b_1.png", "b_2.png"],
        )
        later = time.monotonic() + image_monitor.AWAIT_HISTORY_SECONDS + 1
        with mock.patch.object(image_monitor.time, "monotonic", return_value=later):
            self.assertEqual(self.handler.awaiting_history(), [])

    def test_shutdown_forgets_waiting_images_and_a_dead_handler_is_dropped(self):
        import gc

        from database import run_attribution

        self.handler.process_new_image(self.image("gone_00001_.png"))
        self.handler.shutdown()
        self.assertEqual(self.handler.awaiting_history(), [])
        other = ImageGenerationHandler(self.db, make_tracker(self.db))
        with mock.patch.object(
            other.metadata_extractor, "extract_metadata", return_value=None
        ):
            other.process_new_image(self.image("gone_00002_.png"))
        del other
        gc.collect()
        run_attribution.notify_outputs_sealed("run", [])  # no dead callback raises


class TestOutputLookupIndex(unittest.TestCase):
    """M5: the history lookup by file name uses an index, added by migration."""

    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database

    def test_index_exists_is_used_validated_and_reinstalled(self):
        from database.execution_history_schema import (
            history_schema_error,
            migrate_execution_history,
        )
        from database.run_attribution import _RUNS_FOR_OUTPUT

        db = self.database()
        conn = db.model.get_connection()
        name = "idx_history_read_execution_run_outputs"
        columns = [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
        self.assertEqual(columns, ["filename", "subfolder", "root_key", "path_style"])
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN " + _RUNS_FOR_OUTPUT, ("posix", "k", "", "f.png")
            )
        )
        self.assertIn(name, plan)
        self.assertIsNone(history_schema_error(conn, allow_missing=False))
        conn.execute(f"DROP INDEX {name}")
        self.assertIsNone(history_schema_error(conn, allow_missing=False))
        migrate_execution_history(conn)
        migrate_execution_history(conn)
        self.assertTrue(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
        )

    def test_any_other_index_on_the_outputs_table_is_still_refused(self):
        from database.execution_output_schema import output_schema_error

        db = self.database()
        conn = db.model.get_connection()
        conn.execute("CREATE INDEX idx_rogue ON execution_run_outputs(filename)")
        self.assertIn("unexpected", output_schema_error(conn))
        conn.execute("DROP INDEX idx_rogue")
        name = "idx_history_read_execution_run_outputs"
        conn.execute(f"DROP INDEX {name}")
        conn.execute(f"CREATE INDEX {name} ON execution_run_outputs(filename)")
        self.assertIn("unexpected", output_schema_error(conn))


if __name__ == "__main__":
    unittest.main()
