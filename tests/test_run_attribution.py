"""#342: recorded history attributes a file only to one certain, stored prompt."""

import json
import os
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from database.run_attribution import (
    RecordedOutput,
    recorded_output,
    stored_run_prompt,
)
from tests import test_execution_history_database as history_tests
from tests.background_stops import close_handler
from tests.test_api_admin import AdminAPITestCase
from tests import saver_anchor_graphs as shapes
from tests.test_pair_scan import sampled_graph
from tests.test_execution_context_database import known, snapshot
from tests.test_prompt_attribution import issue_graph, llm_text_graph, make_tracker
from utils.execution_contexts import (
    ContextBundle,
    PromptSnapshot,
    RecipeContext,
    TextEvidence,
)
from utils.execution_outputs import (
    OutputBundle,
    OutputReference,
    snapshot_output_roots,
)
from utils.hashing import generate_prompt_hash
from py.config import GalleryConfig
from utils.image_monitor import ImageGenerationHandler

STYLE = "windows" if os.name == "nt" else "posix"
LLM_TEXT = "Photorealistic aerial landscape of misty mountains at dawn"


def now_ms():
    return time.time_ns() // 1_000_000


class RecordedRuns:
    """Write runs into execution history the way the capture pipeline does."""

    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def record_run(
        self, db, output_root, files, positives, item=0, window=None, nodes=None
    ):
        started, finished = window or (now_ms() - 5000, now_ms() + 5000)
        start = self.start(db, item, started)
        db.write_execution_start(start)
        finish = self.finish(start, time=finished)
        db.write_execution_finish(finish)
        if positives:  # a run with no PromptManager node records no contexts
            db.write_execution_contexts(
                start.run_id,
                start.storage_epoch,
                ContextBundle(
                    tuple(
                        RecipeContext(
                            str(4 + i), snapshot(text), snapshot(""), "inferred"
                        )
                        for i, text in enumerate(positives)
                    ),
                    "complete",
                    "captured",
                ),
            )
        root = snapshot_output_roots(STYLE, output_root, output_root).output_key
        nodes = nodes or ["9"] * len(files)
        references = tuple(
            OutputReference(node, i, "output", sub, name, STYLE, root)
            for i, (node, (sub, name)) in enumerate(zip(nodes, files))
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
        self.addCleanup(close_handler, self, handler)
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


class TestRecordedOutputNode(RecordedRuns, unittest.TestCase):
    """#386: the save node history says wrote a file, under the same rules."""

    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    setUp = TestStoredRunPrompt.setUp

    def node(self, path=None):
        found = recorded_output(self.db, path or self.image, self.output)
        return found.saver_id if found is not None else None

    def test_one_run_one_saver_names_the_saver(self):
        other = os.path.join(self.output, "sub", "img_00002_.png")
        Path(other).write_bytes(b"png")
        self.record_run(
            self.db,
            self.output,
            [("sub", "img_00001_.png"), ("sub", "img_00002_.png")],
            [],
            nodes=["13", "23"],
        )
        self.assertEqual(self.node(), "13")
        self.assertEqual(self.node(other), "23")

    def test_a_run_without_prompt_manager_contexts_still_names_the_saver(self):
        self.record_run(self.db, self.output, [("sub", "img_00001_.png")], [])
        self.assertEqual(self.node(), "9")

    def test_two_runs_reporting_the_file_name_nothing(self):
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], [], nodes=["13"]
        )
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], [], item=1, nodes=["23"]
        )
        self.assertIsNone(self.node())

    def test_one_run_reporting_the_file_from_two_savers_names_nothing(self):
        self.record_run(
            self.db,
            self.output,
            [("sub", "img_00001_.png"), ("sub", "img_00001_.png")],
            [],
            nodes=["13", "23"],
        )
        self.assertIsNone(self.node())

    def test_a_file_written_outside_the_run_names_nothing(self):
        self.record_run(
            self.db,
            self.output,
            [("sub", "img_00001_.png")],
            [],
            window=(1_000_000, 2_000_000),
        )
        self.assertIsNone(self.node())

    def test_unrecorded_outside_or_unknown_roots_name_nothing(self):
        self.record_run(self.db, self.output, [("sub", "img_00001_.png")], [])
        other = os.path.join(self.output, "sub", "other.png")
        Path(other).write_bytes(b"png")
        self.assertIsNone(self.node(other))
        self.assertIsNone(recorded_output(self.db, self.image, None))
        self.assertIsNone(recorded_output(self.db, "", self.output))

    def test_the_run_texts_recorded_at_execution_come_with_the_saver(self):
        self.record_run(
            self.db, self.output, [("sub", "img_00001_.png")], ["an llm prompt"]
        )
        self.assertEqual(
            recorded_output(self.db, self.image, self.output),
            RecordedOutput("9", {"4": {"positive": "an llm prompt", "negative": ""}}),
        )

    def test_texts_only_inferred_from_the_graph_are_not_recorded(self):
        start = self.start(self.db, 0, now_ms() - 5000)
        self.db.write_execution_start(start)
        finish = self.finish(start, time=now_ms() + 5000)
        self.db.write_execution_finish(finish)
        inferred = TextEvidence("graph text", "inferred", "graph_literal", None)
        self.db.write_execution_contexts(
            start.run_id,
            start.storage_epoch,
            ContextBundle(
                (
                    RecipeContext(
                        "7",
                        PromptSnapshot(inferred, inferred, known(""), known("")),
                        snapshot("kept"),
                        "inferred",
                    ),
                ),
                "complete",
                "captured",
            ),
        )
        root = snapshot_output_roots(STYLE, self.output, self.output).output_key
        self.db.write_execution_outputs_for_finish(
            finish,
            OutputBundle(
                (
                    OutputReference(
                        "9", 0, "output", "sub", "img_00001_.png", STYLE, root
                    ),
                ),
                "complete",
                "reported",
            ),
        )
        self.assertEqual(
            recorded_output(self.db, self.image, self.output).texts,
            {"7": {"positive": None, "negative": "kept"}},
        )

    def test_several_savers_name_no_saver_but_keep_the_texts(self):
        self.record_run(
            self.db,
            self.output,
            [("sub", "img_00001_.png"), ("sub", "img_00001_.png")],
            ["text"],
            nodes=["13", "23"],
        )
        found = recorded_output(self.db, self.image, self.output)
        self.assertIsNone(found.saver_id)
        self.assertEqual(found.texts, {"4": {"positive": "text", "negative": ""}})

    def test_the_route_lookup_logs_a_failure_and_names_nothing(self):
        from database.run_attribution import recorded_output_lookup

        broken = mock.Mock()
        broken.model.get_connection.return_value.execute.side_effect = (
            sqlite3.OperationalError("locked")
        )
        broken.get_execution_contexts.side_effect = sqlite3.OperationalError("x")
        with mock.patch(
            "database.run_attribution.host_output_directory", return_value=self.output
        ):
            self.assertIsNone(recorded_output_lookup(broken)(self.image))
            with (
                mock.patch(
                    "database.run_attribution.recorded_runs",
                    side_effect=sqlite3.OperationalError("locked"),
                ),
                self.assertLogs("prompt_manager.run_attribution", "WARNING"),
            ):
                self.assertIsNone(
                    recorded_output_lookup(broken, close=True)(self.image)
                )
        broken.model.close.assert_called_once_with()

    def test_a_history_reference_spells_the_file_as_history_recorded_it(self):
        # On Windows the history route's target is normcased (lower case);
        # history matches names exactly, so the route passes its reference.
        from database.run_attribution import recorded_output_lookup

        upper = os.path.join(self.output, "sub", "IMG_00001_.png")
        Path(upper).write_bytes(b"png")
        self.record_run(self.db, self.output, [("sub", "IMG_00001_.png")], [])
        target = os.path.join(self.output, "sub", "img_00001_.png")
        reference = {
            "folder_type": "output",
            "subfolder": "sub",
            "filename": "IMG_00001_.png",
        }
        with mock.patch(
            "database.run_attribution.host_output_directory", return_value=self.output
        ):
            found = recorded_output_lookup(self.db, reference=reference)(target)
            self.assertEqual(found.saver_id, "9")
            temp = {**reference, "folder_type": "temp"}
            self.assertIsNone(recorded_output_lookup(self.db, reference=temp)(target))

    def test_a_library_without_history_tables_names_nothing(self):
        broken = mock.Mock()
        broken.model.get_connection.return_value.execute.side_effect = (
            sqlite3.OperationalError("no such table")
        )
        self.assertIsNone(recorded_output(broken, self.image, self.output))


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

    def scan_dataset(self, names, nodes):
        """Two branches of the shared-prefix dataset graph, one run, then a scan."""
        paths = [self.write(name) for name in names]
        self.record_run(
            self.api.db,
            str(self.output_dir),
            [("", path.name) for path in paths],
            [],
            nodes=nodes,
        )
        meta = {"prompt": json.dumps(shapes.shared_prefix())}
        return paths, self.api._ingest_scan_batch_sync([(p, meta) for p in paths])

    def test_the_recorded_saver_links_each_dataset_file_to_its_branch(self):
        """#247: before, the legacy parser gave every branch the first prompt."""
        paths, counts = self.scan_dataset(
            ["IMG_GEN_00001_.png", "IMG_GEN_00002_.png"], ["13", "23"]
        )
        self.assertEqual(counts["added"], 2, counts)
        for path, text in zip(paths, shapes.BRANCH_TEXTS):
            prompt = self.api.db.get_prompt_by_hash(generate_prompt_hash(text))
            self.assertEqual(self.linked_prompt(path), prompt["id"])
            self.assertEqual(prompt["category"], "scanned")

    def test_a_file_the_legacy_answer_linked_is_left_as_it_is(self):
        """#247 Q5: a rescan never adds a second link to the branch prompt."""
        db = self.api.db
        legacy = shapes.BRANCH_TEXTS[0]
        legacy_id = db.save_prompt(legacy, prompt_hash=generate_prompt_hash(legacy))
        path = self.write("IMG_GEN_00002_.png")
        db.link_image_to_prompt(legacy_id, str(path))
        self.record_run(db, str(self.output_dir), [("", path.name)], [], nodes=["23"])
        meta = {"prompt": json.dumps(shapes.shared_prefix())}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "conflict")
        self.assertEqual(self.linked_prompt(path), legacy_id)
        self.assertIsNone(
            db.get_prompt_by_hash(generate_prompt_hash(shapes.BRANCH_TEXTS[1]))
        )

    def test_a_recorded_run_never_adds_a_second_link(self):
        db = self.api.db
        db.save_prompt(text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT))
        other = db.save_prompt(text="a dog", prompt_hash=generate_prompt_hash("a dog"))
        path = self.write("bare_00003_.png")
        db.link_image_to_prompt(other, str(path))
        self.record_run(db, str(self.output_dir), [("", path.name)], [LLM_TEXT])
        self.assertEqual(self.api._ingest_scanned_file(path, {}), "conflict")
        rows = db.model.get_connection().execute(
            "SELECT prompt_id FROM generated_images WHERE image_path=?", (str(path),)
        )
        self.assertEqual([row[0] for row in rows], [other])

    def test_a_single_file_asks_history_once(self):
        db = self.api.db
        db.save_prompt(text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT))
        path = self.write("llm_00002_.png")
        self.record_run(db, str(self.output_dir), [("", path.name)], [LLM_TEXT])
        meta = {"prompt": json.dumps(llm_text_graph())}
        statements = []
        conn = db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            self.assertEqual(self.api._ingest_scanned_file(path, meta), "linked")
        finally:
            conn.set_trace_callback(None)
        lookups = [s for s in statements if "FROM execution_run_outputs" in s]
        self.assertEqual(len(lookups), 1)

    def test_a_file_the_recorded_saver_links_is_not_queued(self):
        """A default-ambiguous file linked at the inspector's step is no
        Needs review case."""
        with mock.patch.object(
            type(self.api), "_record_scanned_review", autospec=True
        ) as record:
            _paths, counts = self.scan_dataset(
                ["IMG_GEN_00003_.png", "IMG_GEN_00004_.png"], ["13", "23"]
            )
        self.assertEqual(counts["added"], 2, counts)
        record.assert_not_called()


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
        self.addCleanup(close_handler, self, self.handler)
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
        self.assertIn(
            os.path.normcase(os.path.realpath(path)), self.handler.awaiting_history()
        )
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

    def test_a_seal_during_the_first_pass_is_not_lost(self):
        """R1: the run seals while the image's own pass is still pending."""
        prompt_id = self.save(LLM_TEXT)
        path = self.image("race_00001_.png")
        real = self.handler._history_prompt
        sealed = []

        def history(image_path):
            result = real(image_path)  # before the run's outputs commit: None
            if not sealed:
                sealed.append(True)
                # The writer commits and notifies now; the path is still pending
                self.record_run(
                    self.db, self.output, [("", "race_00001_.png")], [LLM_TEXT]
                )
            return result

        self.handler._history_prompt = history
        self.assertTrue(self.handler.schedule(path))
        self.assertTrue(self.wait_for(lambda: self.linked))
        self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.linked, [("race_00001_.png", prompt_id)])

    def test_a_seal_while_the_pending_set_is_full_is_rechecked_later(self):
        from utils import image_monitor

        prompt_id = self.save(LLM_TEXT)
        path = self.image("full_00001_.png")
        self.handler.process_new_image(path)  # waiting, unlinked
        blocker = self.image("blocker.png")
        release = threading.Event()
        original = self.handler.process_new_image

        def slow(image_path, prompt_snapshot=None):
            if image_path == blocker:
                release.wait(5)
            return original(image_path, prompt_snapshot=prompt_snapshot)

        self.handler.process_new_image = slow
        with mock.patch.object(image_monitor, "MAX_PENDING_IMAGES", 1):
            self.assertTrue(self.handler.schedule(blocker))
            self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 1))
            self.record_run(self.db, self.output, [("", "full_00001_.png")], [LLM_TEXT])
            self.assertEqual(self.linked, [], "not scheduled while the set is full")
            release.set()
            self.assertTrue(self.wait_for(lambda: self.linked))
        self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.linked, [("full_00001_.png", prompt_id)])

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


class TestMonitorForeignRuns(RecordedRuns, unittest.TestCase):
    """#247: outputs of workflows without a PromptManager node, in the monitor."""

    def setUp(self):
        history_tests.HistoryDatabaseTests.setUp(self)
        self.db = history_tests.HistoryDatabaseTests.database(self)
        self.output = str(Path(self.temp.name) / "output")
        os.makedirs(os.path.join(self.output, "dataset"))
        self.tracker = make_tracker(self.db)
        self.handler = ImageGenerationHandler(self.db, self.tracker)
        self.handler.processing_delay = 0
        self.handler.settle_interval = 0.01
        self.addCleanup(close_handler, self, self.handler)
        self.metadata = {"prompt": shapes.shared_prefix()}
        saved = GalleryConfig.SAVE_FOREIGN_RUNS
        self.addCleanup(setattr, GalleryConfig, "SAVE_FOREIGN_RUNS", saved)
        GalleryConfig.SAVE_FOREIGN_RUNS = True
        patches = (
            mock.patch(
                "utils.image_monitor.host_output_directory", return_value=self.output
            ),
            mock.patch(
                "database.run_attribution.host_output_directory",
                return_value=self.output,
            ),
            mock.patch.object(
                self.handler.metadata_extractor,
                "extract_metadata",
                side_effect=lambda path: self.metadata,
            ),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    close_databases = history_tests.HistoryDatabaseTests.close_databases
    wait_for = TestMonitorHistoryRecheck.wait_for

    def image(self, name, subfolder="dataset"):
        path = os.path.join(self.output, subfolder, name)
        Path(path).write_bytes(b"png")
        return path

    def linked_text(self, path):
        row = (
            self.db.model.get_connection()
            .execute(
                "SELECT p.text FROM generated_images g "
                "JOIN prompts p ON p.id = g.prompt_id WHERE g.image_path = ?",
                (str(path),),
            )
            .fetchone()
        )
        return row[0] if row else None

    def key(self, path):
        return self.handler._awaiting_key(path)

    def prompt_count(self):
        connection = self.db.model.get_connection()
        return connection.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]

    def settled(self):
        return self.wait_for(lambda: self.handler.pending_count() == 0)

    def test_each_dataset_file_links_to_its_branch_once_the_run_ends(self):
        """CUJ 1: unlinked while the run is open, its own branch after the seal."""
        paths = [self.image(f"IMG_GEN_0000{n}_.png") for n in (1, 2)]
        for path in paths:
            self.handler.process_new_image(path)
        self.assertEqual([self.linked_text(path) for path in paths], [None, None])
        self.assertEqual(
            self.handler.awaiting_foreign(), [self.key(path) for path in paths]
        )
        self.assertEqual(self.handler.awaiting_history(), [])
        self.record_run(
            self.db,
            self.output,
            [("dataset", os.path.basename(path)) for path in paths],
            [],
            nodes=["13", "23"],
        )
        self.assertTrue(self.wait_for(lambda: all(map(self.linked_text, paths))))
        self.assertTrue(self.settled())
        self.assertEqual(
            [self.linked_text(path) for path in paths], list(shapes.BRANCH_TEXTS[:2])
        )
        self.assertEqual(self.handler.awaiting_foreign(), [])
        prompt = self.db.get_prompt_by_hash(
            generate_prompt_hash(shapes.BRANCH_TEXTS[0])
        )
        self.assertEqual(prompt["category"], "scanned")
        self.assertIn("Saved from a workflow run: IMG_GEN_00001_.png", prompt["notes"])

    def test_a_run_sealed_before_the_file_settles_links_it_at_once(self):
        path = self.image("IMG_GEN_00003_.png")
        self.record_run(
            self.db, self.output, [("dataset", "IMG_GEN_00003_.png")], [], nodes=["33"]
        )
        self.handler.process_new_image(path)
        self.assertEqual(self.linked_text(path), shapes.BRANCH_TEXTS[2])
        self.assertEqual(self.handler.awaiting_foreign(), [])

    def test_a_name_two_runs_reported_stays_unlinked(self):
        """CUJ 3: the file was overwritten; no run proves its saver."""
        path = self.image("IMG_GEN_00004_.png")
        reported = [("dataset", "IMG_GEN_00004_.png")]
        self.record_run(self.db, self.output, reported, [], nodes=["13"])
        self.record_run(self.db, self.output, reported, [], item=1, nodes=["23"])
        self.handler.process_new_image(path)
        self.assertIsNone(self.linked_text(path))
        reference = {"folder_type": "output", "subfolder": "dataset"}
        reference["filename"] = "IMG_GEN_00004_.png"
        with self.assertLogs("prompt_manager.image_monitor", "INFO") as logs:
            self.handler._recheck_foreign(path, reference)
        self.assertIn(
            "No certain prompt for IMG_GEN_00004_.png after its run ended; "
            "left unlinked",
            "\n".join(logs.output),
        )
        self.assertIsNone(self.linked_text(path))
        self.assertEqual(self.prompt_count(), 0)

    def test_the_setting_off_stores_nothing_at_settle_or_after_the_run(self):
        """CUJ 4: read for every file, so it applies without a restart."""
        GalleryConfig.SAVE_FOREIGN_RUNS = False
        skipped = self.image("IMG_GEN_00005_.png")
        with self.assertLogs("prompt_manager.image_monitor", "INFO") as logs:
            self.handler.process_new_image(skipped)
        self.assertIn(
            "No PromptManager prompt in workflow, skipping image",
            "\n".join(logs.output),
        )
        self.assertEqual(self.handler.awaiting_foreign(), [])
        GalleryConfig.SAVE_FOREIGN_RUNS = True
        waiting = self.image("IMG_GEN_00006_.png")
        self.handler.process_new_image(waiting)
        self.assertEqual(self.handler.awaiting_foreign(), [self.key(waiting)])
        GalleryConfig.SAVE_FOREIGN_RUNS = False
        self.record_run(
            self.db, self.output, [("dataset", "IMG_GEN_00006_.png")], [], nodes=["13"]
        )
        self.assertTrue(self.wait_for(lambda: not self.handler.awaiting_foreign()))
        self.assertTrue(self.settled())
        self.assertEqual(
            [self.linked_text(p) for p in (skipped, waiting)], [None, None]
        )
        self.assertEqual(self.prompt_count(), 0)

    def test_a_plain_foreign_workflow_links_at_settle_and_counts_no_run(self):
        """CUJ 2: the default profile decides; the tracker queue is untouched."""
        text = "a lighthouse on a cliff at dusk"
        self.metadata = {"prompt": sampled_graph(text, "blurry")}
        queued = self.db.save_prompt(
            text="queued item", prompt_hash=generate_prompt_hash("queued item")
        )
        self.tracker.set_current_prompt("queued item", {"prompt_id": queued})
        first, second = self.image("plain_00001_.png"), self.image("plain_00002_.png")
        self.handler.process_new_image(first)
        self.handler.process_new_image(second)
        self.assertEqual(
            [self.linked_text(first), self.linked_text(second)], [text, text]
        )
        prompt = self.db.get_prompt_by_hash(generate_prompt_hash(text))
        self.assertEqual(prompt["run_count"] or 0, 0)
        self.assertIsNone(prompt.get("last_used_at"))
        self.assertEqual(self.tracker.pop_next_prompt()["id"], queued)
        self.assertEqual(self.handler.awaiting_foreign(), [])

    def test_the_sealed_reference_spells_the_file_as_history_recorded_it(self):
        """Windows: the watcher's spelling differs in case from the run's report."""
        Path(self.output, "Dataset").mkdir()
        recorded = self.image("IMG_GEN_00007_.png", subfolder="Dataset")
        seen = os.path.join(self.output, "dataset", "img_gen_00007_.png")
        Path(seen).write_bytes(b"png")
        folded = staticmethod(lambda path: os.path.realpath(path).lower())
        with mock.patch.object(ImageGenerationHandler, "_awaiting_key", folded):
            self.handler.process_new_image(seen)
            self.assertIsNone(self.linked_text(seen))
            self.record_run(
                self.db,
                self.output,
                [("Dataset", os.path.basename(recorded))],
                [],
                nodes=["43"],
            )
            self.assertTrue(self.wait_for(lambda: self.linked_text(seen)))
            self.assertTrue(self.settled())
        self.assertEqual(self.linked_text(seen), shapes.BRANCH_TEXTS[3])

    def test_a_full_foreign_set_never_evicts_a_waiting_keyless_file(self):
        from utils import image_monitor

        keyless = self.image("bare_00001_.png", subfolder="")
        with mock.patch.object(
            self.handler.metadata_extractor, "extract_metadata", return_value=None
        ):
            self.handler.process_new_image(keyless)
        paths = [self.image(f"IMG_GEN_1000{n}_.png") for n in range(3)]
        with (
            mock.patch.object(image_monitor, "MAX_AWAITING_HISTORY", 2),
            self.assertLogs("prompt_manager.image_monitor", "INFO") as logs,
        ):
            for path in paths:
                self.handler.process_new_image(path)
            self.assertEqual(
                self.handler.awaiting_foreign(), [self.key(p) for p in paths[1:]]
            )
        self.assertEqual(self.handler.awaiting_history(), [self.key(keyless)])
        self.assertIn(
            "No recorded run for IMG_GEN_10000_.png; left unlinked",
            "\n".join(logs.output),
        )

    def test_an_expired_foreign_entry_is_logged_and_left_unlinked(self):
        from utils import image_monitor

        path = self.image("IMG_GEN_00008_.png")
        self.handler.process_new_image(path)
        later = time.monotonic() + image_monitor.AWAIT_HISTORY_SECONDS + 1
        with (
            mock.patch.object(image_monitor.time, "monotonic", return_value=later),
            self.assertLogs("prompt_manager.image_monitor", "INFO") as logs,
        ):
            self.assertEqual(self.handler.awaiting_foreign(), [])
        self.assertIn(
            "No recorded run for IMG_GEN_00008_.png; left unlinked",
            "\n".join(logs.output),
        )
        self.assertIsNone(self.linked_text(path))

    def test_a_seal_while_the_pending_set_is_full_is_rechecked_later(self):
        from utils import image_monitor

        path = self.image("IMG_GEN_00009_.png")
        self.handler.process_new_image(path)
        blocker = self.image("blocker.png", subfolder="")
        release = threading.Event()
        original = self.handler.process_new_image

        def slow(image_path, prompt_snapshot=None):
            if image_path == blocker:
                release.wait(5)
                return None
            return original(image_path, prompt_snapshot=prompt_snapshot)

        self.handler.process_new_image = slow
        with mock.patch.object(image_monitor, "MAX_PENDING_IMAGES", 1):
            self.assertTrue(self.handler.schedule(blocker))
            self.assertTrue(self.wait_for(lambda: self.handler.pending_count() == 1))
            self.record_run(
                self.db,
                self.output,
                [("dataset", "IMG_GEN_00009_.png")],
                [],
                nodes=["13"],
            )
            self.assertEqual(list(self.handler._foreign_due), [path])
            release.set()
            self.assertTrue(self.wait_for(lambda: self.linked_text(path)))
        self.assertTrue(self.settled())
        self.assertEqual(self.linked_text(path), shapes.BRANCH_TEXTS[0])

    def test_shutdown_forgets_waiting_and_due_foreign_files(self):
        path = self.image("IMG_GEN_00010_.png")
        self.handler.process_new_image(path)
        self.handler._foreign_due[path] = {"filename": "IMG_GEN_00010_.png"}
        self.handler.shutdown()
        self.assertEqual(self.handler.awaiting_foreign(), [])
        self.assertEqual(list(self.handler._foreign_due), [])

    def link_count(self, path):
        connection = self.db.model.get_connection()
        return connection.execute(
            "SELECT COUNT(*) FROM generated_images WHERE image_path = ?", (str(path),)
        ).fetchone()[0]

    def test_a_file_linked_to_another_prompt_never_gets_a_second_link(self):
        """One prompt per file: the pair path and the positive path both refuse."""
        other = self.db.save_prompt(
            "an earlier prompt", generate_prompt_hash("an earlier prompt")
        )
        plain = self.image("plain_00004_.png")
        self.db.link_image_to_prompt(other, plain)
        self.metadata = {"prompt": sampled_graph("a quiet harbor", "blurry")}
        with self.assertLogs("prompt_manager.image_monitor", "INFO") as logs:
            self.handler.process_new_image(plain)
        self.assertIn("Foreign run: conflict", "\n".join(logs.output))
        dataset = self.image("IMG_GEN_00011_.png")
        self.db.link_image_to_prompt(other, dataset)
        self.metadata = {"prompt": shapes.shared_prefix()}
        self.record_run(
            self.db, self.output, [("dataset", "IMG_GEN_00011_.png")], [], nodes=["13"]
        )
        self.handler.process_new_image(dataset)
        for path in (plain, dataset):
            self.assertEqual(self.linked_text(path), "an earlier prompt")
            self.assertEqual(self.link_count(path), 1)
        for text in ("a quiet harbor", shapes.BRANCH_TEXTS[0]):
            self.assertIsNone(self.db.get_prompt_by_hash(generate_prompt_hash(text)))

    def test_the_file_size_is_read_only_when_the_ladder_needs_it(self):
        """PR 1's lazy size: a foreign file decided without a store reads none."""
        # Foreign (a core encoder only) and no API graph: never, at step 3
        encoder = {"type": "CLIPTextEncode", "inputs": [], "widgets_values": ["x"]}
        self.metadata = {"text_encoder_nodes": [encoder]}
        path = self.image("bare_00002_.png", subfolder="")
        with mock.patch("utils.foreign_runs.os.path.getsize") as size:
            self.handler.process_new_image(path)
        size.assert_not_called()

    def test_a_failed_store_is_logged_and_leaves_the_file_unlinked(self):
        self.metadata = {"prompt": sampled_graph("a quiet harbor", "blurry")}
        path = self.image("plain_00003_.png")
        with (
            mock.patch(
                "utils.foreign_runs.ingest_resolved_pair",
                side_effect=sqlite3.OperationalError("locked"),
            ),
            self.assertLogs("prompt_manager.image_monitor", "ERROR"),
        ):
            self.handler.process_new_image(path)
        self.assertIsNone(self.linked_text(path))


class TestBoundedLookup(RecordedRuns, unittest.TestCase):
    """M5: history lookups are batched, and the history schema is unchanged."""

    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database

    def output_lookups(self, db, action):
        statements = []
        conn = db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            result = action()
        finally:
            conn.set_trace_callback(None)
        return result, [s for s in statements if "FROM execution_run_outputs" in s]

    def test_one_query_per_chunk_not_per_file_and_same_answers(self):
        from database import run_attribution

        db = self.database()
        output = str(Path(self.temp.name) / "output")
        os.makedirs(output)
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        names = [f"img_{n:05}_.png" for n in range(25)]
        paths = [os.path.join(output, name) for name in names]
        for path in paths:
            Path(path).write_bytes(b"png")
        self.record_run(db, output, [("", n) for n in names[:10]], [LLM_TEXT])
        self.record_run(db, output, [("", n) for n in names[5:10]], [""], item=1)
        with mock.patch.object(run_attribution, "LOOKUP_CHUNK", 10):
            result, lookups = self.output_lookups(
                db, lambda: run_attribution.stored_run_prompts(db, paths, output)
            )
        self.assertEqual(len(lookups), 3)  # 25 names in chunks of 10
        expected = {path: None for path in paths}
        expected.update({path: prompt_id for path in paths[:5]})
        self.assertEqual(result, expected)
        for path in paths[:12]:
            self.assertEqual(
                run_attribution.stored_run_prompt(db, path, output), expected[path]
            )

    def test_the_history_schema_gains_no_index(self):
        from database.execution_history_schema import history_schema_error

        db = self.database()
        conn = db.model.get_connection()
        indexes = [
            row[1]
            for row in conn.execute("PRAGMA index_list(execution_run_outputs)")
            if row[3] != "pk"
        ]
        self.assertEqual(indexes, [])
        self.assertIsNone(history_schema_error(conn, allow_missing=False))


class TestScanBatchLookup(RecordedRuns, AdminAPITestCase):
    """M5: a scan batch asks history once per chunk of names, not per file."""

    def test_a_batch_its_metadata_links_never_asks_history(self):
        """R3: the batch lookup is made only when a file needs it.

        Since #247 every file the default profile leaves undecided asks (the
        recorded saver may name its branch), so these files resolve as pairs.
        """
        files = []
        for n in range(3):
            path = self.output_dir / f"typed_{n:05}_.png"
            path.write_bytes(b"png")
            files.append(path)
        meta = {"prompt": json.dumps(sampled_graph("typed mountains"))}
        statements = []
        conn = self.api.db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            counts = self.api._ingest_scan_batch_sync([(f, meta) for f in files])
        finally:
            conn.set_trace_callback(None)
        self.assertEqual(counts["added"] + counts["linked"], 3, counts)
        lookups = [s for s in statements if "FROM execution_run_outputs" in s]
        self.assertEqual(lookups, [])

    def test_metadata_less_batch_links_with_one_history_lookup(self):
        db = self.api.db
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        files = []
        for n in range(6):
            path = self.output_dir / f"bare_{n:05}_.png"
            path.write_bytes(b"png")
            files.append(path)
        self.record_run(
            db, str(self.output_dir), [("", f.name) for f in files[:4]], [LLM_TEXT]
        )
        statements = []
        conn = db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            counts = self.api._ingest_scan_batch_sync([(f, {}) for f in files])
        finally:
            conn.set_trace_callback(None)
        lookups = [s for s in statements if "FROM execution_run_outputs" in s]
        self.assertEqual(len(lookups), 1)
        self.assertEqual(counts["linked"], 4)
        linked = {
            row[0]
            for row in conn.execute(
                "SELECT filename FROM generated_images WHERE prompt_id=?",
                (prompt_id,),
            )
        }
        self.assertEqual(linked, {f.name for f in files[:4]})

    def test_the_batch_saver_equals_the_files_recorded_output(self):
        from py.api.admin import _BatchHistory

        db = self.api.db
        names = ["one_node.png", "two_nodes.png", "two_runs.png", "unrecorded.png"]
        files = []
        for name in names:
            path = self.output_dir / name
            path.write_bytes(b"png")
            files.append(path)
        root = str(self.output_dir)
        self.record_run(db, root, [("", "one_node.png")], [], nodes=["13"])
        self.record_run(
            db, root, [("", "two_nodes.png")] * 2, [], item=1, nodes=["13", "23"]
        )
        self.record_run(db, root, [("", "two_runs.png")], [], item=2)
        self.record_run(db, root, [("", "two_runs.png")], [], item=3)
        history = _BatchHistory(self.api, files)
        with mock.patch(
            "database.run_attribution.host_output_directory", return_value=root
        ):
            for path in files:
                with self.subTest(path=path.name):
                    expected = recorded_output(db, str(path), root)
                    found = history.recorded(str(path))
                    if expected is None:
                        self.assertIsNone(found)
                    else:
                        self.assertEqual(found.saver_id, expected.saver_id)
        self.assertEqual(history.recorded(str(files[0])).saver_id, "13")
        self.assertIsNone(history.recorded(str(files[1])).saver_id)

    def test_a_failed_batch_lookup_is_logged_and_names_nothing(self):
        from py.api.admin import _BatchHistory

        path = self.output_dir / "x.png"
        history = _BatchHistory(self.api, [path])
        with (
            mock.patch(
                "py.api.admin.recorded_runs", side_effect=sqlite3.OperationalError("x")
            ),
            self.assertLogs("prompt_manager", "WARNING"),
        ):
            self.assertIsNone(history.recorded(str(path)))
        self.assertIsNone(history.get(str(path)))

    def test_a_failed_prompt_lookup_is_logged_and_names_nothing(self):
        from py.api.admin import _BatchHistory

        path = self.output_dir / "one.png"
        path.write_bytes(b"png")
        root = str(self.output_dir)
        self.record_run(self.api.db, root, [("", path.name)], [LLM_TEXT])
        history = _BatchHistory(self.api, [path])
        with (
            mock.patch(
                "database.run_attribution.host_output_directory", return_value=root
            ),
            mock.patch(
                "py.api.admin.named_prompt", side_effect=sqlite3.OperationalError("x")
            ),
            self.assertLogs("prompt_manager", "WARNING"),
        ):
            self.assertIsNone(history.get(str(path)))
            self.assertEqual(history.recorded(str(path)).saver_id, "9")


class TestLinkedOutputRoot(RecordedRuns, unittest.TestCase):
    """R2: ComfyUI's output folder reached through a symlink or junction."""

    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database

    def linked_root(self):
        self.real = str(Path(self.temp.name) / "big_drive" / "output")
        os.makedirs(os.path.join(self.real, "sub"))
        link = str(Path(self.temp.name) / "output_link")
        try:
            os.symlink(self.real, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        return link

    def test_either_spelling_of_the_file_is_attributed(self):
        db = self.database()
        link = self.linked_root()
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        real_file = os.path.join(self.real, "sub", "img_00001_.png")
        Path(real_file).write_bytes(b"png")
        # History keeps the host's spelling of its output folder (the link)
        self.record_run(db, link, [("sub", "img_00001_.png")], [LLM_TEXT])
        link_file = os.path.join(link, "sub", "img_00001_.png")
        self.assertEqual(stored_run_prompt(db, real_file, link), prompt_id)
        self.assertEqual(stored_run_prompt(db, link_file, link), prompt_id)
        # The real folder named as the root is another root key: not attributed
        self.assertIsNone(stored_run_prompt(db, real_file, self.real))

    def test_the_monitor_rechecks_a_file_seen_through_the_real_path(self):
        db = self.database()
        link = self.linked_root()
        prompt_id = db.save_prompt(
            text=LLM_TEXT, prompt_hash=generate_prompt_hash(LLM_TEXT)
        )
        real_file = os.path.join(self.real, "late_00001_.png")
        Path(real_file).write_bytes(b"png")
        handler = ImageGenerationHandler(db, make_tracker(db))
        handler.processing_delay = 0
        handler.settle_interval = 0.01
        self.addCleanup(close_handler, self, handler)
        linked = []
        handler.link_image_to_prompt = lambda path, prompt, meta: linked.append(
            prompt["id"]
        )
        with (
            mock.patch("utils.image_monitor.host_output_directory", return_value=link),
            mock.patch.object(
                handler.metadata_extractor, "extract_metadata", return_value=None
            ),
        ):
            handler.process_new_image(real_file)
            self.assertEqual(linked, [])
            self.record_run(db, link, [("", "late_00001_.png")], [LLM_TEXT])
            deadline = time.monotonic() + 5
            while not linked and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(linked, [prompt_id])


if __name__ == "__main__":
    unittest.main()
