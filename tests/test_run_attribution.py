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


if __name__ == "__main__":
    unittest.main()
