"""Needs review items (#249 E2): capture-bound ambiguous evidence.

The scan and the live monitor persist one review item per ambiguous file:
the file is captured, its metadata is read again from the capture, the
default (ingestion) resolver runs on it, and only an ``ambiguous`` result is
stored, bound to the capture's byte record. Statuses, counters and links of
both producers stay what they were.
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
    from tests.test_api_admin import AdminAPITestCase, _sse_payloads
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed
    from test_api_admin import AdminAPITestCase, _sse_payloads

from PIL import Image, PngImagePlugin

from database.operations import PromptDatabase
from database.review_schema import canonical_evidence, evidence_digest
from py.api import PromptManagerAPI
from py.config import GalleryConfig
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler
from utils.media_capture import CaptureCapacity, CaptureStore
from utils.prompt_tracker import PromptTracker

try:  # guarded so a missing module fails assertions, not test loading
    from utils import review_capture
except ImportError:
    review_capture = None

NOW = "2026-10-22T00:00:00+00:00"
# Two negative prompt delimiters: the A1111 record is ambiguous.
DELIMITERS = (
    "cat\nNegative prompt: blur\nNegative prompt: noise\nSteps: 20, Sampler: Euler"
)


def _sampler(positive, negative="bad"):
    return {"positive": positive, "negative": negative}


def ambiguous_graph(*positives):
    """One KSampler per positive text, each with its own encoder: ambiguous."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
    }
    for index, text in enumerate(positives or ("cat", "dog")):
        encoder, sampler = str(10 + 2 * index), str(11 + 2 * index)
        graph[encoder] = {"class_type": "CLIPTextEncode", "inputs": {"text": text}}
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": ["2", 0],
            },
        }
    return graph


def resolved_graph(text="a red square"):
    return ambiguous_graph(text)


def write_png(path, *, prompt=None, parameters=None, color=(255, 0, 0)):
    info = PngImagePlugin.PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if parameters is not None:
        info.add_text("parameters", parameters)
    Image.new("RGB", (2, 2), color).save(str(path), pnginfo=info)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReviewTestCase(AdminAPITestCase):
    """Scan helpers over a capture store kept inside the test directory."""

    async def get_application(self):
        app = await super().get_application()
        self.capture_parent = Path(self.tmpdir) / "captures"
        self.api._captures = CaptureStore(temp_parent=self.capture_parent)
        return app

    async def tearDownAsync(self):
        self.api.close_annotation_captures()
        await super().tearDownAsync()

    def _roots(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]

    async def _scan(self, scope="new"):
        resp = await self.client.request(
            "POST", "/prompt_manager/jobs/scan", json={"scope": scope}
        )
        data = await resp.json()
        self.assertEqual(resp.status, 202, data)
        await asyncio.wait_for(self.api._scan_job.task, 20)
        detail = await self.client.request(
            "GET", f"/prompt_manager/jobs/{data['job']['id']}"
        )
        return (await detail.json())["job"]

    def _items(self):
        conn = self.api.db.model.get_connection()
        return [
            dict(row)
            for row in conn.execute(
                "SELECT i.*, b.sha256, b.size, b.mtime_ns, f.locator "
                "FROM media_review_items i "
                "JOIN media_annotation_bytes b ON b.id=i.byte_id "
                "JOIN media_annotation_files f ON f.id=b.file_id "
                "ORDER BY i.created_at, i.id"
            )
        ]

    def _review_revision(self):
        conn = self.api.db.model.get_connection()
        return conn.execute(
            "SELECT review_index_revision FROM media_review_metadata"
        ).fetchone()[0]

    def _capture_files(self):
        if not self.capture_parent.exists():
            return []
        return [p for p in self.capture_parent.rglob("*") if p.is_file()]

    def _require_module(self):
        self.assertIsNotNone(review_capture, "utils.review_capture is not implemented")


class TestScanRecording(ReviewTestCase):
    async def test_scan_persists_only_ambiguous_files_with_capture_digest(self):
        self._require_module()
        self._roots()
        ambiguous = self.output_dir / "sub" / "two.png"
        ambiguous.parent.mkdir()
        write_png(ambiguous, prompt=ambiguous_graph("cat", "dog"))
        write_png(self.output_dir / "resolved.png", prompt=resolved_graph())
        write_png(self.output_dir / "plain.png")
        write_png(
            self.output_dir / "a1111.png",
            parameters="cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler",
        )

        job = await self._scan()

        items = self._items()
        self.assertEqual(len(items), 1, items)
        item = items[0]
        self.assertEqual(item["sha256"], digest(ambiguous))
        self.assertEqual(item["size"], ambiguous.stat().st_size)
        self.assertEqual(item["mtime_ns"], ambiguous.stat().st_mtime_ns)
        self.assertEqual(
            item["locator"], os.path.normcase(os.path.realpath(str(ambiguous)))
        )
        self.assertEqual(
            (item["state"], item["revision"], item["limited"]), ("pending", 0, 0)
        )
        self.assertEqual(item["resolver_version"], 1)
        self.assertEqual(item["candidate_count"], 2)
        self.assertEqual(item["evidence_hash"], evidence_digest(item["evidence"]))
        evidence = json.loads(item["evidence"])
        self.assertEqual(canonical_evidence(evidence), item["evidence"])
        self.assertEqual(evidence["resolver_version"], 1)
        self.assertEqual(
            sorted(c["positive"]["text"] for c in evidence["candidates"]),
            ["cat", "dog"],
        )
        for candidate in evidence["candidates"]:
            self.assertEqual(
                set(candidate),
                {"hash", "sampler_id", "state", "reason", "positive", "negative"},
            )
            self.assertEqual(
                set(candidate["positive"]), {"text", "storage_text", "provenance"}
            )
            self.assertEqual(candidate["negative"]["text"], "bad")
            body = {k: v for k, v in candidate.items() if k != "hash"}
            self.assertEqual(
                candidate["hash"],
                hashlib.sha256(canonical_evidence(body).encode()).hexdigest(),
            )
        self.assertEqual(self._review_revision(), 1)
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertIn("? 1 image added to Needs review", "\n".join(job["log"]))
        self.assertEqual(self._capture_files(), [])
        self.assertEqual(self.api._captures.stats()["captures"], 0)

    async def test_scan_statuses_counters_and_outcome_rows_unchanged(self):
        self._require_module()
        self._roots()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        write_png(self.output_dir / "one.png", prompt=resolved_graph())
        write_png(
            self.output_dir / "delims.png",
            parameters=DELIMITERS,
        )
        write_png(self.output_dir / "none.png")
        files = sorted(self.output_dir.iterdir())
        roots = self._scan_roots()

        def run(api, recorder=None):
            batch = api._extract_batch_metadata_sync(files)
            outcomes = []
            if recorder is None:
                counts = api._ingest_scan_batch_sync(batch, roots, outcomes)
            else:
                from py.api import admin

                with patch.object(admin, "record_ambiguous_file", recorder):
                    counts = api._ingest_scan_batch_sync(batch, roots, outcomes)
            prompts = api.db.get_recent_prompts()["prompts"]
            return counts, outcomes, sorted(p["text"] for p in prompts)

        other_db = PromptDatabase(os.path.join(self.tmpdir, "other.db"))
        other = PromptManagerAPI(db=other_db)

        def broken(*args, **kwargs):
            raise RuntimeError("recording failed")

        recorded = run(self.api)
        with self.assertLogs("prompt_manager.api", logging.WARNING) as logs:
            without = run(other, broken)
        self.assertEqual(recorded, without)
        self.assertEqual(recorded[0]["ambiguous"], 1)
        self.assertEqual(recorded[0]["added"], 2)
        self.assertTrue(any("Needs review" in line for line in logs.output))
        self.assertEqual(len(self._items()), 2)
        other.close_annotation_captures()
        other_db.close_all()

    def _scan_roots(self):
        from py.api.admin import _scan_roots

        return _scan_roots(self.api._get_all_output_dirs())

    async def test_new_scope_rereads_ambiguous_files_and_stays_idempotent(self):
        self._require_module()
        self._roots()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        write_png(self.output_dir / "one.png", prompt=resolved_graph())
        first = await self._scan("new")
        revision = self._review_revision()
        reads = []
        original = self.api._extract_comfyui_metadata

        def spy(path):
            reads.append(os.path.basename(path))
            return original(path)

        self.api._extract_comfyui_metadata = spy
        second = await self._scan("new")

        self.assertEqual(reads, ["two.png"])
        self.assertEqual(first["scope"]["review_items"], 1)
        self.assertEqual(second["scope"]["review_items"], 0)
        self.assertNotIn("added to Needs review", "\n".join(second["log"]))
        self.assertEqual(len(self._items()), 1)
        self.assertEqual(self._review_revision(), revision)
        self.assertEqual(self._capture_files(), [])

    async def test_repeat_scan_keeps_decisions(self):
        self._require_module()
        self._roots()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        await self._scan("all")
        item = self._items()[0]
        candidate = json.loads(item["evidence"])["candidates"][0]["hash"]
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='selected', revision=1 WHERE id=?",
                (item["id"],),
            )
            conn.execute(
                "INSERT INTO media_review_decisions(id,item_id,decision_revision,"
                "created_at,"
                "outcome,candidate_hash,evidence) VALUES(?,?,?,?,?,?,?)",
                (
                    "00000000-0000-4000-8000-0000000000d1",
                    item["id"],
                    1,
                    NOW,
                    "selected",
                    candidate,
                    '{"kind":"user-selection"}',
                ),
            )

        job = await self._scan("all")

        items = self._items()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["state"], items[0]["revision"]), ("selected", 1))
        self.assertEqual(items[0]["updated_at"], item["updated_at"])
        decisions = conn.execute("SELECT id FROM media_review_decisions").fetchall()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(job["scope"]["review_items"], 0)

    async def test_changed_bytes_create_new_pending_item_and_keep_old(self):
        self._require_module()
        self._roots()
        path = self.output_dir / "two.png"
        write_png(path, prompt=ambiguous_graph("cat", "dog"))
        await self._scan()
        old = self._items()[0]
        write_png(path, prompt=ambiguous_graph("cat", "dog"), color=(0, 0, 255))
        self.assertNotEqual(digest(path), old["sha256"])

        job = await self._scan()

        items = self._items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], old["id"])
        self.assertEqual(items[0]["sha256"], old["sha256"])
        self.assertEqual(items[1]["sha256"], digest(path))
        self.assertEqual(items[1]["state"], "pending")
        self.assertEqual(items[0]["locator"], items[1]["locator"])
        self.assertEqual(job["scope"]["review_items"], 1)

    async def test_parameters_delimiter_ambiguity_is_limited_without_candidates(self):
        self._require_module()
        self._roots()
        write_png(
            self.output_dir / "delims.png",
            parameters=DELIMITERS,
        )
        many = [f"prompt {n}" for n in range(17)]
        write_png(self.output_dir / "many.png", prompt=ambiguous_graph(*many))

        job = await self._scan()

        items = {Path(item["locator"]).name: item for item in self._items()}
        self.assertEqual(set(items), {"delims.png", "many.png"})
        for item in items.values():
            self.assertEqual((item["limited"], item["candidate_count"]), (1, 0))
            evidence = json.loads(item["evidence"])
            self.assertEqual(evidence["candidates"], [])
            self.assertIs(evidence["limited"], True)
        self.assertEqual(
            json.loads(items["many.png"]["evidence"])["candidate_total"], 17
        )
        self.assertEqual(job["scope"]["review_items"], 2)

    async def test_capacity_errors_are_logged_and_counted_not_fatal(self):
        self._require_module()
        self._roots()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))

        def full(*args, **kwargs):
            raise CaptureCapacity("Too many captures are in progress")

        with patch.object(self.api._captures, "capture", side_effect=full):
            job = await self._scan()

        self.assertEqual(job["state"], "finished")
        # The browser parser links this ambiguous file: Activity counts it Added.
        self.assertEqual(job["counters"]["added"], 1)
        self.assertEqual(job["scope"]["review_items"], 0)
        self.assertEqual(job["scope"]["review_errors"], 1)
        self.assertIn(
            "! 1 image could not be added to Needs review", "\n".join(job["log"])
        )
        self.assertEqual(self._items(), [])

    async def test_legacy_scan_events_are_unchanged(self):
        self._require_module()
        self._roots()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        resp = await self.client.request("POST", "/prompt_manager/scan")
        payloads = _sse_payloads(await resp.text())
        final = payloads[-1]
        self.assertEqual(final["type"], "complete")
        self.assertNotIn("review_items", final)
        self.assertEqual(len(self._items()), 1)


class TestRecordAmbiguousFile(ReviewTestCase):
    def _record(self, relative):
        return review_capture.record_ambiguous_file(
            self.api.db, self.api._captures, self.output_dir, 0, relative
        )

    async def test_record_outcomes(self):
        self._require_module()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        write_png(self.output_dir / "one.png", prompt=resolved_graph())
        write_png(
            self.output_dir / "delims.png",
            parameters=DELIMITERS,
        )
        self.assertEqual(self._record("two.png"), "recorded")
        self.assertEqual(self._record("two.png"), "duplicate")
        self.assertEqual(self._record("one.png"), "skipped")
        self.assertEqual(self._record("missing.png"), "skipped")
        self.assertEqual(self._record("delims.png"), "limited")
        self.assertEqual(self._record("delims.png"), "duplicate")
        self.assertEqual(self._review_revision(), 2)

    async def test_restore_between_capture_and_write_skips(self):
        self._require_module()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        with patch.object(
            self.api.db, "get_execution_epoch", return_value="stale-epoch"
        ):
            self.assertEqual(self._record("two.png"), "skipped")
        self.assertEqual(self._items(), [])

    async def test_oversize_evidence_is_limited(self):
        self._require_module()
        big = "x" * (4 * 1024 * 1024 + 10)
        side = {"text": big, "storage_text": big, "provenance": {}}
        resolution = {
            "state": "ambiguous",
            "reason": "Multiple sampler branches",
            "candidates": [
                {
                    "state": "resolved",
                    "sampler_id": "3",
                    "reason": "",
                    "positive": side,
                    "negative": {"text": "", "storage_text": "", "provenance": {}},
                    "candidates": [],
                }
            ],
        }
        evidence, count, limited = review_capture.review_evidence(resolution)
        self.assertEqual((count, limited), (0, True))
        self.assertEqual(evidence["candidates"], [])
        self.assertLess(len(canonical_evidence(evidence)), 1024)

    async def test_nested_ambiguous_candidates_are_flattened_and_deduplicated(self):
        self._require_module()

        def side(text):
            return {"text": text, "storage_text": text, "provenance": {"n": text}}

        def leaf(sampler, text):
            return {
                "state": "resolved",
                "sampler_id": sampler,
                "reason": "",
                "positive": side(text),
                "negative": side("bad"),
                "candidates": [],
                "reason_code": None,
            }

        resolution = {
            "state": "ambiguous",
            "reason": "Multiple sampler branches",
            "candidates": [
                leaf("3", "cat"),
                {
                    "state": "ambiguous",
                    "sampler_id": None,
                    "reason": "stages",
                    "positive": None,
                    "negative": None,
                    "candidates": [leaf("5", "dog"), leaf("3", "cat")],
                },
            ],
        }
        evidence, count, limited = review_capture.review_evidence(resolution)
        self.assertEqual((count, limited), (2, False))
        self.assertEqual(
            [c["positive"]["text"] for c in evidence["candidates"]], ["cat", "dog"]
        )

    async def test_no_inference_or_model_access_during_recording(self):
        self._require_module()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        from py import autotag

        with (
            patch.object(autotag.AutoTagService, "load_model") as load,
            patch.object(autotag.AutoTagService, "download_model") as download,
            patch.object(autotag, "get_autotag_service") as service,
            patch.object(
                Image.Image, "load", autospec=True, side_effect=AssertionError("decode")
            ) as decode,
        ):
            self.assertEqual(self._record("two.png"), "recorded")
        load.assert_not_called()
        download.assert_not_called()
        service.assert_not_called()
        decode.assert_not_called()

    async def test_capture_released_and_handles_closed(self):
        self._require_module()
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph("cat", "dog"))
        self.assertEqual(self._record("two.png"), "recorded")
        self.assertEqual(self.api._captures.stats()["captures"], 0)
        self.assertEqual(self._capture_files(), [])
        write_png(self.output_dir / "three.png", prompt=ambiguous_graph("a", "b"))
        with patch.object(
            review_capture,
            "resolve_prompt_pair",
            side_effect=RuntimeError("resolver failed"),
        ):
            with self.assertRaises(RuntimeError):
                self._record("three.png")
        self.assertEqual(self.api._captures.stats()["captures"], 0)
        self.assertEqual(self._capture_files(), [])
        assert_closed(self, self.output_dir, self.capture_parent)


class TestReviewEdges(ReviewTestCase):
    def _side(self, text):
        return {"text": text, "storage_text": text, "provenance": {}}

    def _leaf(self, sampler, text):
        return {
            "state": "resolved",
            "sampler_id": sampler,
            "reason": "",
            "positive": self._side(text),
            "negative": self._side(""),
            "candidates": [],
        }

    async def test_evidence_over_the_bound_together_is_limited(self):
        self._require_module()
        half = "y" * (2 * 1024 * 1024 + 10)  # twice per side, twice per item
        resolution = {
            "state": "ambiguous",
            "reason": "Multiple sampler branches",
            "candidates": [self._leaf("3", half), self._leaf("5", half + "z")],
        }
        evidence, count, limited = review_capture.review_evidence(resolution)
        self.assertEqual((count, limited, evidence["candidate_total"]), (0, True, 2))

    async def test_malformed_candidates_are_ignored_or_limited(self):
        self._require_module()
        self.assertEqual(
            review_capture.review_evidence({"state": "ambiguous", "candidates": None}),
            (
                {
                    "resolver_version": 1,
                    "reason": "",
                    "limited": True,
                    "candidate_total": 0,
                    "candidates": [],
                },
                0,
                True,
            ),
        )
        resolution = {
            "state": "ambiguous",
            "reason": "x",
            "candidates": ["junk", self._leaf("3", "cat"), self._leaf("5", "dog")],
        }
        self.assertEqual(review_capture.review_evidence(resolution)[1:], (2, False))
        broken = self._leaf("7", "eel")
        broken["positive"] = {"storage_text": "no text"}
        resolution["candidates"].append(broken)
        self.assertEqual(review_capture.review_evidence(resolution)[1:], (0, True))

    async def test_unreadable_or_promptless_captures_are_skipped(self):
        self._require_module()
        record = review_capture.record_ambiguous_file
        (self.output_dir / "junk.png").write_bytes(b"not a png at all")
        write_png(self.output_dir / "plain.png")
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", "{not json")
        Image.new("RGB", (2, 2)).save(str(self.output_dir / "bad.png"), pnginfo=info)
        for name in ("junk.png", "plain.png", "bad.png"):
            self.assertEqual(
                record(self.api.db, self.api._captures, self.output_dir, 0, name),
                "skipped",
                name,
            )
        write_png(self.output_dir / "two.png", prompt=ambiguous_graph())
        with patch.object(self.api._captures, "acquire", return_value=None):
            self.assertEqual(
                record(self.api.db, self.api._captures, self.output_dir, 0, "two.png"),
                "skipped",
            )
        self.assertEqual(self._items(), [])
        self.assertEqual(self._capture_files(), [])

    async def test_record_review_item_validates_its_facts(self):
        self._require_module()
        epoch = self.api.db.get_execution_epoch()
        good = {
            "locator": "/out/a.png",
            "sha256": "a" * 64,
            "size": 1,
            "mtime_ns": 2,
            "epoch": epoch,
            "resolver_version": 1,
            "evidence": {"candidates": []},
            "candidate_count": 0,
            "limited": True,
        }
        for change in (
            {"resolver_version": 0},
            {"resolver_version": True},
            {"candidate_count": 17},
            {"candidate_count": -1},
            {"limited": 1},
            {"limited": True, "candidate_count": 2},
            {"sha256": "A" * 64},
            {"evidence": ["not", "an", "object"]},
        ):
            with self.assertRaises(ValueError, msg=change):
                self.api.db.record_review_item(**{**good, **change})
        self.assertTrue(self.api.db.record_review_item(**good))
        self.assertFalse(self.api.db.record_review_item(**good))
        items = self.api.db.review_items_for("/out/a.png")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence"], {"candidates": []})
        self.assertIs(items[0]["limited"], True)
        self.assertEqual(items[0]["sha256"], "a" * 64)
        self.assertEqual(self.api.db.review_items_for("/out/b.png"), [])
        self.assertEqual(self.api.db.review_index_revision(), 1)
        with self.assertRaises(ValueError):
            self.api.db.review_items_for("")
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute("DELETE FROM media_review_metadata")
        with self.assertRaises(sqlite3.DatabaseError):
            self.api.db.review_index_revision()


class TestMonitorRecording(ReviewTestCase):
    def _handler(self):
        handler = ImageGenerationHandler(self.api.db, PromptTracker(self.api.db))
        return handler

    def _linked_prompt(self, path):
        conn = self.api.db.model.get_connection()
        row = conn.execute(
            "SELECT prompt_id FROM generated_images WHERE image_path=?", (str(path),)
        ).fetchone()
        return row[0] if row else None

    async def test_monitor_keeps_fallback_link_and_records_item_best_effort(self):
        self._require_module()
        self._roots()
        path = self.output_dir / "two.png"
        write_png(path, prompt=ambiguous_graph("cat", "dog"))
        prompt_id = self.api.db.save_prompt(
            text="latest", prompt_hash=generate_prompt_hash("latest")
        )
        metadata = {"prompt": ambiguous_graph("cat", "dog")}
        context = {"id": prompt_id, "text": "latest", "fallback": True}

        handler = self._handler()

        def broken(image_path):
            raise sqlite3.OperationalError("database is locked")

        handler.review_recorder = broken
        with self.assertLogs("prompt_manager.image_monitor", logging.WARNING) as logs:
            handler.link_image_to_prompt(str(path), context, metadata)
        self.assertEqual(self._linked_prompt(path), prompt_id)
        self.assertTrue(any("Needs review" in line for line in logs.output))
        self.assertEqual(self._items(), [])

        other = self.output_dir / "three.png"
        write_png(other, prompt=ambiguous_graph("cat", "dog"))
        handler.review_recorder = self.api.record_review_file
        handler.link_image_to_prompt(str(other), context, metadata)
        self.assertEqual(self._linked_prompt(other), prompt_id)
        items = self._items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sha256"], digest(other))
        self.assertEqual(self._capture_files(), [])

    async def test_monitor_skips_resolved_and_files_outside_the_roots(self):
        self._require_module()
        self._roots()
        prompt_id = self.api.db.save_prompt(
            text="latest", prompt_hash=generate_prompt_hash("latest")
        )
        context = {"id": prompt_id, "text": "latest"}
        calls = []
        handler = self._handler()
        handler.review_recorder = lambda image_path: calls.append(image_path)
        resolved = self.output_dir / "one.png"
        write_png(resolved, prompt=resolved_graph())
        handler.link_image_to_prompt(
            str(resolved), context, {"prompt": resolved_graph()}
        )
        self.assertEqual(calls, [])
        outside = self.outside_dir / "two.png"
        write_png(outside, prompt=ambiguous_graph())
        self.assertEqual(self.api.record_review_file(str(outside)), "skipped")
        self.assertEqual(self._items(), [])

    async def test_monitor_wiring_reaches_new_and_existing_handlers(self):
        self._require_module()
        from utils.image_monitor import ImageMonitor

        monitor = ImageMonitor(self.api.db, PromptTracker(self.api.db))
        recorder = self.api.record_review_file
        monitor.set_review_recorder(recorder)
        self.assertIs(monitor.review_recorder, recorder)
        monitor.start_monitoring([str(self.output_dir)])
        try:
            self.assertIs(monitor.handler.review_recorder, recorder)
            monitor.set_review_recorder(None)
            self.assertIsNone(monitor.handler.review_recorder)
        finally:
            monitor.stop_monitoring()


if __name__ == "__main__":
    unittest.main()
