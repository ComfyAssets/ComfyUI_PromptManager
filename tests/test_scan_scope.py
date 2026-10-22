"""Scan scope (#249 Activity-2 Task 4): new and unresolved files by default.

A "Scan for metadata" job started without ``scope`` (or with ``"new"``) skips
the files the Library already links with a resolved link, before their
metadata is read, and counts them as Already known. ``"all"`` reads every file
again, as the legacy ``/prompt_manager/scan`` route always does.
"""

import asyncio
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

try:
    from tests.test_api_admin import AdminAPITestCase, _sse_payloads, _write_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase, _sse_payloads, _write_png

from database import operations  # noqa: E402
from database.pair_ingestion import ingest_resolved_pair  # noqa: E402
from py.config import GalleryConfig  # noqa: E402
from utils.prompt_pair_resolver import resolve_prompt_pair  # noqa: E402

SKIPPED_DETAIL = "Skipped · already linked"
ON_DISK = object()  # _link_resolved: store the file's size on disk


def _graph(text):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": text}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


class ScanScopeTestCase(AdminAPITestCase):
    """Helpers: link files the way the scan and the monitor do, spy on reads."""

    def _link_resolved(self, path, text, size=ON_DISK):
        """``path`` linked with a resolved pair link (what a resolved scan writes).

        The row stores ``size`` as its file size: by default the file's size on
        disk (None when the file does not exist).
        """
        if size is ON_DISK:
            size = os.path.getsize(path) if os.path.exists(path) else None
        metadata = {"prompt": _graph(text)}
        if size is not None:
            metadata["file_info"] = {"size": size}
        result = ingest_resolved_pair(
            self.api.db, str(path), metadata, resolve_prompt_pair(metadata)
        )
        self.assertEqual(result["status"], "added")

    def _link_without_pair(self, path, text):
        """``path`` linked to a prompt with no pair link (fallback / FIFO link)."""
        prompt_id = self.api.db.save_prompt(text, None, [], None, None)
        self.assertTrue(self.api.db.link_image_to_prompt(prompt_id, str(path)))
        return prompt_id

    def _spy_reads(self):
        reads = []
        original = self.api._extract_comfyui_metadata

        def spy(path):
            reads.append(os.path.basename(path))
            return original(path)

        self.api._extract_comfyui_metadata = spy
        return reads

    async def _scan(self, body=None):
        kwargs = {} if body is None else {"json": body}
        resp = await self.client.request("POST", "/prompt_manager/jobs/scan", **kwargs)
        data = await resp.json()
        self.assertEqual(resp.status, 202, data)
        await asyncio.wait_for(self.api._scan_job.task, 10)
        detail = await self.client.request(
            "GET", f"/prompt_manager/jobs/{data['job']['id']}"
        )
        return (await detail.json())["job"]


class TestScanScope(ScanScopeTestCase):

    async def test_new_scope_skips_known_resolved_files_without_reading_them(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        known = self.output_dir / "known.png"
        _write_png(known, prompt_text="a red square")
        _write_png(self.output_dir / "new.png", prompt_text="a blue circle")
        self._link_resolved(known, "a red square")
        reads = self._spy_reads()

        job = await self._scan()

        self.assertEqual(reads, ["new.png"])
        self.assertEqual(job["scope"]["files"], "new")
        self.assertEqual(job["counters"]["known"], 1)
        self.assertEqual(job["counters"]["added"], 1)
        self.assertEqual(job["processed"], 2)
        self.assertEqual(
            job["outcomes"]["known"],
            [
                {
                    "path": "known.png",
                    "root": "output",
                    "root_index": 0,
                    "detail": SKIPPED_DETAIL,
                }
            ],
        )
        self.assertIn(f"= output/known.png · {SKIPPED_DETAIL}", "\n".join(job["log"]))
        self.assertTrue(
            job["log"][0].endswith(
                "Scan started · 1 output folder · new and unresolved files"
            )
        )
        legacy = self.api._scan_job.last_event
        self.assertEqual((legacy["processed"], legacy["unchanged"]), (2, 1))
        self.assertTrue(job["status_line"].startswith("Processing file 2 of 2"))

    async def test_whole_known_batch_skips_the_executor_reads(self):
        from py.api import admin

        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        for name in ("a.png", "b.png"):
            _write_png(self.output_dir / name, prompt_text=f"prompt {name}")
            self._link_resolved(self.output_dir / name, f"prompt {name}")
        _write_png(self.output_dir / "c.png", prompt_text="prompt c")
        reads = self._spy_reads()

        with patch.object(admin, "SCAN_BATCH_SIZE", 1):
            job = await self._scan({"scope": "new"})

        self.assertEqual(reads, ["c.png"])
        self.assertEqual(job["counters"]["known"], 2)
        self.assertEqual(job["counters"]["added"], 1)

    async def test_unresolved_files_are_rescanned_in_new_scope(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        names = ("fallback.png", "conflict.png", "unlinked.png")
        for name in names:
            _write_png(self.output_dir / name, prompt_text=f"text of {name}")
        # Linked without a resolved pair link (the monitor's fallback / FIFO link).
        self._link_without_pair(self.output_dir / "fallback.png", "text of fallback")
        # Resolved once, then also linked to a second prompt: ambiguous.
        conflict = self.output_dir / "conflict.png"
        self._link_resolved(conflict, "text of conflict.png")
        self._link_without_pair(conflict, "another prompt")
        reads = self._spy_reads()

        job = await self._scan()

        self.assertEqual(sorted(reads), sorted(names))
        self.assertNotIn(SKIPPED_DETAIL, json.dumps(job["outcomes"]))

    async def test_all_scope_reads_everything(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        known = self.output_dir / "known.png"
        _write_png(known, prompt_text="a red square")
        self._link_resolved(known, "a red square")
        reads = self._spy_reads()

        job = await self._scan({"scope": "all"})

        self.assertEqual(reads, ["known.png"])
        self.assertEqual(job["scope"]["files"], "all")
        self.assertEqual(job["counters"]["known"], 1)
        self.assertNotEqual(job["outcomes"]["known"][0]["detail"], SKIPPED_DETAIL)
        self.assertTrue(
            job["log"][0].endswith("Scan started · 1 output folder · all files")
        )

    async def test_legacy_scan_route_reads_everything(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        known = self.output_dir / "known.png"
        _write_png(known, prompt_text="a red square")
        self._link_resolved(known, "a red square")
        reads = self._spy_reads()

        resp = await self.client.request("POST", "/prompt_manager/scan", data="{}")
        payloads = _sse_payloads(await resp.text())

        self.assertEqual(reads, ["known.png"])
        self.assertEqual(self.api._scan_job.scope["files"], "all")
        self.assertEqual(payloads[-1]["type"], "complete")
        self.assertEqual(payloads[-1]["unchanged"], 1)

    async def test_file_replaced_at_a_known_path_is_reread_and_needs_review(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        path = self.output_dir / "out_00012.png"
        _write_png(path, prompt_text="the first prompt")
        self._link_resolved(path, "the first prompt")
        before = os.path.getsize(path)
        # ComfyUI reused the name for a new image with another prompt.
        _write_png(path, prompt_text="a much longer second prompt for the same name")
        self.assertNotEqual(os.path.getsize(path), before)
        reads = self._spy_reads()

        job = await self._scan()

        self.assertEqual(reads, ["out_00012.png"])
        self.assertEqual(job["counters"]["review"], 1)
        self.assertEqual(job["counters"]["known"], 0)
        self.assertIn("different prompt", job["outcomes"]["review"][0]["detail"])

    async def test_scanned_files_store_their_size_so_the_next_new_scan_skips(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        _write_png(self.output_dir / "fresh.png", prompt_text="a fresh prompt")
        first = await self._scan()
        self.assertEqual(first["counters"]["added"], 1)
        reads = self._spy_reads()

        second = await self._scan()

        self.assertEqual(reads, [])
        self.assertEqual(second["outcomes"]["known"][0]["detail"], SKIPPED_DETAIL)

    async def test_stored_null_size_is_reread(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        path = self.output_dir / "old.png"
        _write_png(path, prompt_text="an old prompt")
        self._link_resolved(path, "an old prompt", size=None)
        reads = self._spy_reads()

        job = await self._scan()

        self.assertEqual(reads, ["old.png"])
        # The read stores the size it was missing, so the file is known next time.
        self.assertEqual(job["counters"]["added"], 1)
        self.assertEqual(
            self.api.db.get_known_resolved_paths({str(path): os.path.getsize(path)}),
            {str(path)},
        )

    async def test_known_set_failure_falls_back_to_reading_every_file(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        known = self.output_dir / "known.png"
        _write_png(known, prompt_text="a red square")
        self._link_resolved(known, "a red square")
        reads = self._spy_reads()

        def broken(files):
            raise sqlite3.OperationalError("database is locked")

        self.api.db.get_known_resolved_paths = broken
        with self.assertLogs("prompt_manager.api", level="WARNING"):
            job = await self._scan()

        self.assertEqual(job["state"], "finished")
        self.assertEqual(reads, ["known.png"])
        self.assertIn(
            "Known files could not be read · reading every file", "\n".join(job["log"])
        )

    async def test_empty_read_body_asks_for_the_default_scope(self):
        """A chunked POST with no bytes can read a body: still the default."""
        from py.api.job_routes import _scan_scope

        class Request:
            can_read_body = True

            def __init__(self, text):
                self._text = text

            async def text(self):
                return self._text

        self.assertEqual(await _scan_scope(Request("")), "new")
        self.assertEqual(await _scan_scope(Request("  \n")), "new")
        self.assertEqual(await _scan_scope(Request('{"scope": "all"}')), "all")
        self.assertIsNone(await _scan_scope(Request("{")))

    async def test_invalid_scope_is_refused(self):
        for body in ({"scope": "everything"}, {"scope": 1}, ["new"]):
            with self.subTest(body=body):
                resp = await self.client.request(
                    "POST", "/prompt_manager/jobs/scan", json=body
                )
                self.assertEqual(resp.status, 400)
                data = await resp.json()
                self.assertFalse(data["success"])
        resp = await self.client.request(
            "POST", "/prompt_manager/jobs/scan", data="not json"
        )
        self.assertEqual(resp.status, 400)
        self.assertIsNone(self.api._scan_job)


class TestKnownResolvedPaths(ScanScopeTestCase):
    """``PromptDatabase.get_known_resolved_paths``: one streamed query."""

    async def test_returns_the_given_paths_that_are_known_and_resolved(self):
        known, fallback = self.output_dir / "known.png", self.output_dir / "fb.png"
        self._link_resolved(known, "a red square", size=10)
        self._link_without_pair(fallback, "a fallback")
        asked = {str(known): 10, str(fallback): 10, str(self.output_dir / "n.png"): 10}

        self.assertEqual(self.api.db.get_known_resolved_paths(asked), {str(known)})
        self.assertEqual(self.api.db.get_known_resolved_paths({}), set())

    async def test_size_must_match_and_a_stored_null_size_is_not_known(self):
        same, other = self.output_dir / "same.png", self.output_dir / "other.png"
        unknown = self.output_dir / "unknown.png"
        self._link_resolved(same, "same size", size=10)
        self._link_resolved(other, "other size", size=10)
        self._link_resolved(unknown, "no stored size", size=None)
        asked = {str(same): 10, str(other): 11, str(unknown): 10}

        self.assertEqual(self.api.db.get_known_resolved_paths(asked), {str(same)})
        # A file the walk could not stat is not known either.
        self.assertEqual(self.api.db.get_known_resolved_paths({str(same): None}), set())

    async def test_row_without_file_path_matches_through_image_path(self):
        known = self.output_dir / "legacy.png"
        self._link_resolved(known, "a legacy row", size=10)
        with sqlite3.connect(self.api.db.model.db_path) as conn:
            conn.execute("UPDATE generated_images SET file_path=NULL")
        conn.close()

        found = self.api.db.get_known_resolved_paths({str(known): 10})
        self.assertEqual(found, {str(known)})

    async def test_stored_spelling_is_normalized_like_the_scanned_path(self):
        known = self.output_dir / "sub" / "known.png"
        self._link_resolved(known, "a red square", size=10)
        spelled = os.path.join(str(self.output_dir), "sub", "..", "sub", "known.png")
        with sqlite3.connect(self.api.db.model.db_path) as conn:
            conn.execute("UPDATE generated_images SET file_path=?", (spelled,))
        conn.close()

        found = self.api.db.get_known_resolved_paths({str(known): 10})
        self.assertEqual(found, {str(known)})

    async def test_one_query_streamed_in_small_fetches(self):
        paths = [self.output_dir / f"{index}.png" for index in range(5)]
        for path in paths:
            self._link_resolved(path, f"prompt {path.name}", size=10)
        original = self.api.db.model.get_connection
        opened = []

        def counting():
            opened.append(1)
            return original()

        self.api.db.model.get_connection = counting
        with patch.object(operations, "KNOWN_PATHS_FETCH_SIZE", 2):
            found = self.api.db.get_known_resolved_paths({str(p): 10 for p in paths})
        self.assertEqual(found, {str(p) for p in paths})
        self.assertEqual(len(opened), 1)

    async def test_windows_path_case_matches_known_set(self):
        stored = self.output_dir / "Sub" / "Known.PNG"
        self._link_resolved(stored, "a red square", size=10)
        # A row written before paths were case-folded keeps its own spelling.
        with sqlite3.connect(self.api.db.model.db_path) as conn:
            conn.execute(
                "UPDATE generated_images SET file_path=?", (str(stored).upper(),)
            )
        conn.close()
        asked = str(self.output_dir / "sub" / "known.png")

        with patch("os.path.normcase", lambda path: path.lower()):
            self.assertEqual(self.api.db.get_known_resolved_paths({asked: 10}), {asked})
        if os.path.normcase("A") == "A":  # a case-sensitive platform
            self.assertEqual(self.api.db.get_known_resolved_paths({asked: 10}), set())


class TestSkippedOutcome(unittest.TestCase):

    def test_skipped_file_is_already_known_with_its_own_detail(self):
        from py.api.admin import _scan_outcome

        root = os.path.abspath("out")
        group, row, line = _scan_outcome(
            os.path.join(root, "a", "b.png"), [(root, "output", 0)], "skipped"
        )
        self.assertEqual(group, "known")
        self.assertEqual(row["detail"], SKIPPED_DETAIL)
        self.assertEqual(row["path"], "a/b.png")
        self.assertEqual(line, f"= output/a/b.png · {SKIPPED_DETAIL}")


if __name__ == "__main__":
    unittest.main()
