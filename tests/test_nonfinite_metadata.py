"""ComfyUI's ``NaN``/``Infinity`` metadata ingests, and is stored as strict JSON.

ComfyUI serializes the prompt with Python's ``json.dumps``, so a node whose
``IS_CHANGED`` returns ``float("nan")`` (the "always re-run" idiom) writes
``"is_changed": [NaN]`` into every saved PNG; widgets can hold ``Infinity``.
The legacy pages replaced ``NaN`` with ``null`` before ``JSON.parse``, so such
files must ingest. Whatever is stored or answered as JSON stays strict (the
non-finite numbers become ``null``) and the prompt text and hash never change.
"""

import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from database.operations import PromptDatabase
from tests.history_waits import drain_writer
from tests.test_api_admin import AdminAPITestCase
from tests.test_api_images import ImageAPITestCase
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

# The words "NaN" and "Infinity" inside the prompt are text, never numbers.
POSITIVE = "  A cat named NaN beside an Infinity pool, ratio: NaN, [NaN]  "
NEGATIVE = "blurry"
NAN, INF = float("nan"), float("inf")


def graph(positive=POSITIVE, negative=NEGATIVE):
    """An API prompt graph with non-finite numbers where ComfyUI writes them."""
    g = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0], "model": ["4", 0]},
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "5": {
            "class_type": "Image Filter",
            "inputs": {"strength": INF, "floor": -INF},
            "_meta": {"title": "Image Filter"},
            "is_changed": [NAN],
        },
    }
    return g


WORKFLOW = {
    "nodes": [
        {"id": 5, "type": "Image Filter", "widgets_values": [NAN, INF, -INF]},
    ],
    "links": [],
}


def comfy_text(value):
    """The record text ComfyUI writes: Python's default, non-strict JSON."""
    text = json.dumps(value)
    assert "NaN" in text and "Infinity" in text
    return text


def write_png(path, prompt_graph=None, workflow=WORKFLOW):
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", comfy_text(prompt_graph or graph()))
    if workflow is not None:
        info.add_text("workflow", comfy_text(workflow))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (4, 4)) as image:
        image.save(path, pnginfo=info)
    return path


def _reject(name):
    raise ValueError(f"non-strict JSON constant {name}")


def strict_loads(text):
    return json.loads(text, parse_constant=_reject)


class StrictRows:
    """Assertions over the rows a write left behind."""

    def image_rows(self, db):
        """The stored texts, read through the one workflow document accessor."""
        from database.workflow_documents import DocumentReader, resolve_image_row

        with db.model.get_connection() as conn:
            reader = DocumentReader(conn)
            return [
                resolve_image_row(reader, row)
                for row in conn.execute(
                    "SELECT prompt_id, workflow_data, prompt_metadata, parameters, "
                    "workflow_document_id, prompt_document_id "
                    "FROM generated_images ORDER BY id"
                )
            ]

    def assert_strict_snapshot(self, row, *, workflow=True):
        prompt = strict_loads(row["prompt_metadata"])
        self.assertEqual(prompt["5"]["is_changed"], [None])
        self.assertEqual(prompt["5"]["inputs"], {"strength": None, "floor": None})
        self.assertEqual(prompt["1"]["inputs"]["text"], POSITIVE)
        stored_workflow = strict_loads(row["workflow_data"])
        if workflow:
            self.assertEqual(
                stored_workflow["nodes"][0]["widgets_values"], [None, None, None]
            )
        if row["parameters"] is not None:
            strict_loads(row["parameters"])

    def assert_prompt_text(self, db, prompt_id):
        with db.model.get_connection() as conn:
            row = conn.execute(
                "SELECT text, hash FROM prompts WHERE id=?", (prompt_id,)
            ).fetchone()
        self.assertEqual(row["text"], POSITIVE.strip())
        self.assertEqual(row["hash"], generate_prompt_hash(POSITIVE))


def dumps_strict(value, **kwargs):
    from utils.json_strict import dumps_strict as dumps

    return dumps(value, **kwargs)


class TestDumpsStrict(unittest.TestCase):
    def test_finite_values_serialize_byte_identically(self):
        value = {"b": [1, 2.5, "NaN", None, True], "a": {"x": "Infinity"}}
        for kwargs in ({}, {"sort_keys": True, "separators": (",", ":")}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(
                    dumps_strict(value, **kwargs),
                    json.dumps(value, allow_nan=False, **kwargs),
                )

    def test_non_finite_numbers_become_null_and_strings_stay(self):
        value = {"n": NAN, "i": [INF, -INF], "s": "NaN Infinity", "t": (NAN, 1)}
        encoded = dumps_strict(value, sort_keys=True)
        self.assertEqual(
            strict_loads(encoded),
            {"n": None, "i": [None, None], "s": "NaN Infinity", "t": [None, 1]},
        )

    def test_other_errors_still_raise(self):
        cycle = []
        cycle.append(cycle)
        for value in (cycle, [NAN, cycle]):
            with self.assertRaisesRegex(ValueError, "Circular"):
                dumps_strict(value)
        with self.assertRaises(TypeError):
            dumps_strict({"x": object()})
        self.assertEqual(
            dumps_strict({"x": object()}, default=lambda _: NAN), '{"x": null}'
        )


class TestScanIngestsNonFiniteMetadata(StrictRows, AdminAPITestCase):
    def ingest(self, name, prompt_graph=None):
        path = write_png(self.output_dir / name, prompt_graph)
        extracted = self.api._extract_comfyui_metadata(str(path))
        return self.api._ingest_scan_batch_sync([(path, extracted)])

    def test_resolved_pair_with_nan_is_added_with_strict_snapshots(self):
        counts = self.ingest("nan.png")
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["added"], 1)
        rows = self.image_rows(self.api.db)
        self.assertEqual(len(rows), 1)
        self.assert_strict_snapshot(rows[0])
        self.assert_prompt_text(self.api.db, rows[0]["prompt_id"])
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(
            [(p["positive_text"], p["negative_text"]) for p in pairs],
            [(POSITIVE.strip(), NEGATIVE)],
        )
        # A repeated scan compares the same strict snapshot: unchanged.
        self.assertEqual(self.ingest("nan.png")["unchanged"], 1)

    async def test_scan_route_reports_no_failures(self):
        from py.config import GalleryConfig

        write_png(self.output_dir / "nan.png")
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        resp = await self.client.request("POST", "/prompt_manager/scan")
        body = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertIn('"type": "complete"', body)
        self.assertIn('"added": 1', body)
        self.assertIn('"failed": 0', body)


class TestMonitorIngestsNonFiniteMetadata(StrictRows, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "db.sqlite"))
        self.pid = self.db.save_prompt(
            POSITIVE.strip(), prompt_hash=generate_prompt_hash(POSITIVE)
        )
        self.tracker = SimpleNamespace(
            pop_next_prompt=lambda: None,
            get_current_prompt=lambda: None,
            extend_prompt_timeout=lambda *args: None,
        )
        self.handler = ImageGenerationHandler(self.db, self.tracker)
        self.handler.logger = logging.getLogger("test.nonfinite.monitor")
        self.handler.logger.disabled = True

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.temp.name)
        self.temp.cleanup()

    def test_new_image_with_nan_links_its_pair(self):
        # The monitor links only images a PromptManager node produced.
        g = graph()
        g["1"]["class_type"] = "PromptManager"
        path = write_png(Path(self.temp.name) / "out" / "nan.png", g)
        self.handler.process_new_image(str(path))
        rows = self.image_rows(self.db)
        self.assertEqual([row["prompt_id"] for row in rows], [self.pid])
        self.assert_strict_snapshot(rows[0])
        self.assert_prompt_text(self.db, self.pid)
        pairs = self.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image_count"], 1)

    def test_legacy_link_stores_strict_json(self):
        # The plain prompt link (no exact pair) must not write NaN either.
        path = str(Path(self.temp.name) / "plain.png")
        metadata = {"prompt": graph(), "workflow": WORKFLOW, "parameters": {"x": NAN}}
        self.assertTrue(self.db.link_image_to_prompt(self.pid, path, metadata))
        row = self.image_rows(self.db)[0]
        self.assert_strict_snapshot(row)
        self.assertEqual(strict_loads(row["parameters"]), {"x": None})

    def test_stored_images_read_back_without_non_finite_numbers(self):
        path = str(Path(self.temp.name) / "legacy.png")
        self.db.link_image_to_prompt(self.pid, path, {"prompt": graph()})
        with self.db.model.get_connection() as conn:
            # A row an older version wrote with Python's default json.dumps.
            conn.execute(
                "UPDATE generated_images SET prompt_metadata=?", (comfy_text(graph()),)
            )
            conn.commit()
        images = self.db.get_prompt_images(str(self.pid))
        strict_loads(json.dumps(images, default=str))


class TestInspectorAnswersStrictJSON(ImageAPITestCase):
    async def test_inspect_route_resolves_and_answers_strict_json(self):
        write_png(self.output_dir / "nan.png")
        response = await self.client.get(
            "/prompt_manager/images/inspect/nan.png", params={"root": "0"}
        )
        self.assertEqual(response.status, 200)
        result = strict_loads(await response.text())
        self.assertEqual(result["resolution"]["state"], "resolved")
        self.assertEqual(
            result["prompts"]["positive"],
            {"status": "resolved", "text": POSITIVE, "source": "graph-inferred"},
        )

    async def test_prompt_images_route_answers_strict_json(self):
        pid = self.api.db.save_prompt(
            POSITIVE.strip(), prompt_hash=generate_prompt_hash(POSITIVE)
        )
        path = str(write_png(self.output_dir / "linked.png"))
        self.api.db.link_image_to_prompt(pid, path, {"prompt": graph()})
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "UPDATE generated_images SET prompt_metadata=?", (comfy_text(graph()),)
            )
            conn.commit()
        response = await self.client.get(f"/prompt_manager/prompts/{pid}/images")
        self.assertEqual(response.status, 200)
        result = strict_loads(await response.text())
        self.assertEqual(len(result["images"]), 1)
        stored = result["images"][0]["prompt_metadata"]
        self.assertEqual(stored["1"]["inputs"]["text"], POSITIVE)
        self.assertEqual(stored["5"]["is_changed"], [None])


class TestHistoryCapturesNonFiniteGraphs(unittest.TestCase):
    """The queued graph ComfyUI hands the hook may carry float('nan')."""

    JOB = "a0c26da3-eb26-405c-b143-c662a84c3112"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = PromptDatabase(os.path.join(self.directory.name, "history.db"))
        self.addCleanup(self.db.model.reset_schema_cache)
        self.addCleanup(self.db.model.close_all)

    def test_queued_graph_with_nan_records_its_run_and_contexts(self):
        from tests.test_execution_history_hooks import HostQueue, status
        from utils.execution_context_capture import record_runtime_context
        from utils.execution_history import register_history_hook

        queue = HostQueue()
        registration = register_history_hook(
            SimpleNamespace(prompt_queue=queue), lambda: self.db
        )
        self.addCleanup(lambda: registration.dispose(3))
        self.addCleanup(registration.writer.invalidate)
        deadline = time.monotonic() + 3
        while not registration.writer.status().accepting:
            self.assertLess(time.monotonic(), deadline, "writer never accepted")
            time.sleep(0.005)
        g = {
            "p": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": POSITIVE, "negative_text": NEGATIVE},
            },
            "s": {
                "class_type": "KSampler",
                "inputs": {"positive": ["p", 0], "negative": ["p", 1], "cfg": INF},
                "is_changed": [NAN],
            },
            "5": graph()["5"],
        }
        extra = {"extra_pnginfo": {"workflow": WORKFLOW}}
        queue.results.append(((0, self.JOB, g, extra, ["s"]), 1))
        queue.get()
        host = types.ModuleType("comfy_execution.utils")
        host.get_executing_context = lambda: SimpleNamespace(
            prompt_id=self.JOB, node_id="p", list_index=0
        )
        with patch.dict(sys.modules, {"comfy_execution.utils": host}):
            for role, text in (("positive", POSITIVE), ("negative", NEGATIVE)):
                record_runtime_context(
                    "PromptManagerPair", text, "", "", text, role=role
                )
        queue.task_done(1, {}, status("execution_success", job=self.JOB))
        drain_writer(registration.writer, clock=time.monotonic)
        with self.db.model.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            runs = [dict(r) for r in conn.execute("SELECT * FROM execution_runs")]
        self.assertEqual([run["outcome"] for run in runs], ["succeeded"])
        contexts = self.db.get_execution_contexts(runs[0]["run_id"])
        self.assertEqual(contexts["state"], "complete")
        self.assertEqual(contexts["contexts"][0]["positive"]["raw"], POSITIVE)
        strict_loads(json.dumps(contexts))


if __name__ == "__main__":
    unittest.main()
