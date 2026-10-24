"""Duplicate image files on the job registry (#249 System S6, Task 17).

``POST /prompt_manager/jobs/duplicates`` finds content-identical media files
as a ``read`` Activity job; ``GET /prompt_manager/maintenance/duplicates/{id}``
pages the groups it holds; ``duplicate_files`` previews a removal bound to the
reviewed keepers, and its apply job removes only the reviewed, revalidated
non-keepers, moving their database links to the kept copy. The legacy gallery
routes keep their shapes on the same registry kinds.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import open_handles_under
    from tests.test_api_maintenance import ApplyTestCase, _digest
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import open_handles_under
    from test_api_maintenance import ApplyTestCase, _digest

from py.api import duplicate_files  # noqa: E402
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402

WAIT = 10
FIND = "/prompt_manager/jobs/duplicates"
GROUPS = "/prompt_manager/maintenance/duplicates/"
PREVIEW = "/prompt_manager/maintenance/preview"
STREAM = "/prompt_manager/scan_duplicates/stream"
STATUS = "/prompt_manager/scan_duplicates/status"
LEGACY_DELETE = "/prompt_manager/delete_duplicate_images"


def _sse_payloads(body):
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DuplicateFilesTestCase(ApplyTestCase):
    async def get_application(self):
        app = await super().get_application()
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.output_dir)]
        return app

    def write(self, name, data, *, folder=None, mtime=None):
        path = Path(folder or self.output_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def second_source(self):
        """A second valid source outside ComfyUI (an extra gallery root)."""
        second = self.outside_dir / "renders"
        second.mkdir(parents=True, exist_ok=True)
        os.environ["PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"] = str(second)
        return second

    def trio(self):
        """a.png (oldest), b.png and c.png share bytes; d.png is unique."""
        self.write("a.png", b"same bytes", mtime=1_000_000)
        self.write("b.png", b"same bytes", mtime=2_000_000)
        self.write("sub/c.png", b"same bytes", mtime=3_000_000)
        self.write("d.png", b"unique", mtime=4_000_000)

    async def find(self):
        response = await self.client.request("POST", FIND)
        text = await response.text()
        self.assertEqual(response.status, 202, text)
        self.assertNotIn(self.tmpdir, text)
        job = self.api._jobs.get(json.loads(text)["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job

    async def groups(self, job_id, status=200, **query):
        params = "&".join(f"{key}={value}" for key, value in query.items())
        response = await self.client.request("GET", f"{GROUPS}{job_id}?{params}")
        text = await response.text()
        self.assertEqual(response.status, status, text)
        self.assertNotIn(self.tmpdir, text)
        return json.loads(text)

    async def review(self, job_id, keep, status=200):
        return await self.post(
            PREVIEW,
            {
                "operation": "duplicate_files",
                "options": {"job_id": job_id, "keep": keep},
            },
            status,
        )

    async def found_group(self):
        job = await self.find()
        [group] = (await self.groups(job.id))["groups"]
        return job, group

    @staticmethod
    def keep_of(group, path):
        [entry] = [f for f in group["files"] if f["path"] == path]
        return {
            "group_id": group["group_id"],
            "root_index": entry["root_index"],
            "path": entry["path"],
        }

    async def remove_all_but(self, path):
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, path)])
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        return plan, cleanup

    def gate_hashing(self):
        gate = threading.Event()
        calls = []
        original = self.api._calculate_file_hash

        def gated(path):
            calls.append(path)
            gate.wait(WAIT)
            return original(path)

        self.api._calculate_file_hash = gated
        self.addCleanup(gate.set)
        return gate, calls


class TestFindJob(DuplicateFilesTestCase):
    async def test_find_is_listed_in_activity_and_legacy_stream_attaches(self):
        self.trio()
        gate, calls = self.gate_hashing()
        response = await self.client.request("POST", FIND)
        self.assertEqual(response.status, 202)
        summary = (await response.json())["job"]
        listed = (
            await (await self.client.request("GET", "/prompt_manager/jobs")).json()
        )["jobs"]
        self.assertEqual([j["id"] for j in listed], [summary["id"]])
        self.assertEqual(summary["kind"], "duplicates")
        self.assertEqual(summary["title"], "Find duplicate image files")
        self.assertEqual(summary["admission"], "read")
        self.assertTrue(summary["cancellable"])
        self.assertEqual(summary["counters"], {"groups_found": 0})
        # The legacy gallery attaches to the same job, and its status says so.
        legacy = asyncio.ensure_future(self.client.request("POST", STREAM))
        await asyncio.sleep(0.1)
        status = await (await self.client.request("GET", STATUS)).json()
        self.assertTrue(status["running"])
        gate.set()
        body = await (await legacy).text()
        job = self.api._jobs.get(summary["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        events = _sse_payloads(body)
        done = events[-1]
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["found"], 1)
        self.assertEqual(
            sorted(i["filename"] for i in done["duplicates"][0]["images"]),
            ["a.png", "b.png", "c.png"],
        )
        self.assertEqual(len(calls), 4)  # one pass: each file hashed once
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.counters, {"groups_found": 1})
        [row] = job.outcomes["groups_found"]
        self.assertEqual(row["path"], "a.png")
        self.assertEqual(row["root_index"], 0)
        self.assertEqual(row["detail"], "3 identical files · 10 B each")
        self.assertEqual(job.status_line, "Found 1 group of duplicate files")
        finished = await (await self.client.request("GET", STATUS)).json()
        self.assertFalse(finished["running"])
        self.assertEqual(finished["last_event"]["found"], 1)

    async def test_legacy_stream_starts_a_registry_job(self):
        self.trio()
        body = await (await self.client.request("POST", STREAM)).text()
        self.assertEqual(_sse_payloads(body)[-1]["type"], "complete")
        [job] = self.api._jobs.list()
        self.assertEqual(job.kind, "duplicates")
        self.assertEqual(job.title, "Find duplicate image files (gallery page)")
        self.assertNotIn(self.tmpdir, body)
        # Its groups are reviewable in the workspace too.
        page = await self.groups(job.id)
        self.assertEqual(page["total"], 1)

    async def test_find_covers_every_source_with_its_index(self):
        second = self.second_source()
        self.write("x.png", b"twin", folder=second)
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            self._root(second),
        ]
        self.write("x.png", b"twin")
        job = await self.find()
        [group] = (await self.groups(job.id))["groups"]
        self.assertEqual(
            sorted((f["root_index"], f["path"]) for f in group["files"]),
            [(0, "x.png"), (1, "x.png")],
        )
        self.assertEqual({f["root"] for f in group["files"]}, {"output", "renders"})

    async def test_find_can_be_cancelled(self):
        self.trio()
        gate, _calls = self.gate_hashing()
        response = await self.client.request("POST", FIND)
        job_id = (await response.json())["job"]["id"]
        stream = asyncio.ensure_future(self.client.request("POST", STREAM))
        await asyncio.sleep(0.1)
        cancel = await self.client.request(
            "POST", f"/prompt_manager/jobs/{job_id}/cancel"
        )
        self.assertTrue((await cancel.json())["cancellation_requested"])
        gate.set()
        job = self.api._jobs.get(job_id)
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "cancelled")
        last = _sse_payloads(await (await stream).text())[-1]
        self.assertEqual(last, {"type": "error", "message": "Duplicate scan cancelled"})
        await self.groups(job_id, 409)

    async def test_second_find_is_busy_and_names_the_running_job(self):
        self.trio()
        gate, _calls = self.gate_hashing()
        first = (await (await self.client.request("POST", FIND)).json())["job"]
        second = await self.client.request("POST", FIND)
        self.assertEqual(second.status, 409)
        body = await second.json()
        self.assertEqual(body["code"], "busy")
        self.assertEqual(body["running"]["id"], first["id"])
        gate.set()

    async def test_failed_find_is_coded_and_releases_the_kind(self):
        self.write("a.png", b"x")
        with patch.object(
            self.api, "_hash_media_groups", side_effect=OSError(f"boom {self.tmpdir}")
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                job = await self.find()
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.status_line, "The duplicate search failed.")
        self.assertNotIn("boom", json.dumps(job.report()))
        await self.groups(job.id, 409)
        self.assertEqual((await self.find()).state, "finished")


class TestGroupPages(DuplicateFilesTestCase):
    async def test_groups_are_paged_ten_at_a_time(self):
        for index in range(12):
            for copy in range(2):
                self.write(f"g{index:02d}_{copy}.png", f"group {index}".encode())
        job = await self.find()
        first = await self.groups(job.id)
        self.assertEqual((first["total"], first["offset"], first["limit"]), (12, 0, 10))
        self.assertEqual(len(first["groups"]), 10)
        rest = await self.groups(job.id, offset=10, limit=10)
        self.assertEqual(len(rest["groups"]), 2)
        ids = [g["group_id"] for g in first["groups"] + rest["groups"]]
        self.assertEqual(len(set(ids)), 12)
        entry = first["groups"][0]["files"][0]
        for key in ("path", "root", "root_index", "url", "size", "media_type"):
            self.assertIn(key, entry)
        self.assertTrue(entry["url"].endswith("?root=0"))
        for query in ({"limit": 11}, {"limit": 0}, {"offset": -1}, {"offset": "x"}):
            with self.subTest(query=query):
                reply = await self.groups(job.id, 400, **query)
                self.assertEqual(reply["code"], "invalid-request")

    async def test_unknown_or_running_job_has_no_groups(self):
        reply = await self.groups("f" * 32, 404)
        self.assertEqual(reply["code"], "not-found")
        self.trio()
        gate, _calls = self.gate_hashing()
        job_id = (await (await self.client.request("POST", FIND)).json())["job"]["id"]
        reply = await self.groups(job_id, 409)
        self.assertEqual(reply["code"], "job-running")
        gate.set()


class TestReview(DuplicateFilesTestCase):
    async def test_preview_lists_only_non_keepers_and_writes_nothing(self):
        self.trio()
        prompt = self.save("Linked")
        self.api.db.link_image_to_prompt(prompt, str(self.output_dir / "b.png"))
        job, group = await self.found_group()
        db_path = self.api.db.model.db_path
        files = [db_path, db_path + "-wal"]
        before = [_digest(path) for path in files]
        media = {p: _digest(p) for p in self.output_dir.rglob("*") if p.is_file()}
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertEqual([_digest(path) for path in files], before)
        self.assertEqual(
            {p: _digest(p) for p in self.output_dir.rglob("*") if p.is_file()},
            media,
        )
        self.assertEqual(plan["operation"], "duplicate_files")
        self.assertEqual(
            plan["scope"], {"count": 2, "label": "2 duplicate files in 1 group"}
        )
        self.assertTrue(plan["media"]["deletes_files"])
        self.assertEqual(
            plan["media"]["label"],
            "Deletes 2 files (20 B) from disk; the kept copies stay. "
            "The safety backup covers the database, not these files.",
        )
        self.assertEqual(
            [row["label"] for row in plan["details"]], ["b.png", "sub/c.png"]
        )
        self.assertEqual(
            plan["details"][0]["detail"],
            "output · a copy of a.png · 1 database link moves to the kept copy",
        )
        self.assertEqual(plan["protected"], {"count": 0, "reasons": []})

    async def test_cannot_delete_keeper_or_outside_group(self):
        self.trio()
        self.write("e.png", b"other pair")
        self.write("f.png", b"other pair")
        job = await self.find()
        groups = (await self.groups(job.id))["groups"]
        trio = next(g for g in groups if len(g["files"]) == 3)
        pair = next(g for g in groups if len(g["files"]) == 2)
        bad = [
            # a file of another group named as this group's keeper
            [{**self.keep_of(trio, "a.png"), "path": "e.png"}],
            # a file that is in no group
            [{**self.keep_of(trio, "a.png"), "path": "d.png"}],
            # the right path under another source index
            [{**self.keep_of(trio, "a.png"), "root_index": 1}],
            [{**self.keep_of(trio, "a.png"), "group_id": "0" * 64}],
            [{**self.keep_of(trio, "a.png"), "extra": 1}],
            [],
            "a.png",
        ]
        for keep in bad:
            with self.subTest(keep=keep):
                reply = await self.review(job.id, keep, 400)
                self.assertEqual(reply["code"], "invalid-request")
        # Two keepers of one group: only the third copy goes.
        plan = await self.review(
            job.id,
            [self.keep_of(trio, "a.png"), self.keep_of(trio, "sub/c.png")],
        )
        self.assertEqual([row["label"] for row in plan["details"]], ["b.png"])
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.counters["removed"], 1)
        self.assertTrue((self.output_dir / "a.png").is_file())
        self.assertTrue((self.output_dir / "sub" / "c.png").is_file())
        self.assertFalse((self.output_dir / "b.png").exists())
        # The untouched group is untouched.
        self.assertTrue((self.output_dir / "e.png").is_file())
        self.assertTrue((self.output_dir / "f.png").is_file())
        self.assertEqual(len(pair["files"]), 2)

    async def test_a_source_removed_since_the_find_is_refused(self):
        self.trio()
        job, group = await self.found_group()
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.second_source())]
        reply = await self.review(job.id, [self.keep_of(group, "a.png")], 409)
        self.assertEqual(reply["code"], "sources-changed")

    def test_containment_is_decided_as_text_before_the_filesystem(self):
        root = str(self.output_dir)
        outside = os.path.join(str(self.outside_dir), "x.png")
        touched = []
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            touched.append(str(path))
            return real_lstat(path, *args, **kwargs)

        with patch.object(duplicate_files.os, "lstat", side_effect=lstat):
            for candidate in (
                outside,
                os.path.join(root, "..", "outside", "x.png"),
                os.path.join(root, "thumbnails", "a_thumb.png"),
                os.path.join(root, "notes.txt"),
            ):
                with self.subTest(candidate=candidate):
                    reason = duplicate_files.file_problem(candidate, root, "0" * 64, 1)
                    self.assertEqual(reason, duplicate_files.OUTSIDE_SOURCE)
        self.assertEqual(touched, [])

    @staticmethod
    def _can_symlink(directory):
        probe = Path(directory) / "probe-link"
        try:
            os.symlink(str(Path(directory) / "x"), str(probe))
        except (OSError, NotImplementedError):
            return False
        probe.unlink()
        return True

    async def test_symlink_rejected(self):
        if not self._can_symlink(self.tmpdir):  # pragma: no cover - Windows w/o rights
            self.skipTest("symlinks are not available")
        self.trio()
        job, group = await self.found_group()
        # b.png becomes a link to the keeper after the find: never removed.
        (self.output_dir / "b.png").unlink()
        os.symlink(str(self.output_dir / "a.png"), str(self.output_dir / "b.png"))
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertEqual([row["label"] for row in plan["details"]], ["sub/c.png"])
        self.assertEqual(
            plan["protected"],
            {"count": 1, "reasons": ["1 file is a link, not a regular file"]},
        )
        # A keeper that became a link protects its whole group.
        (self.output_dir / "a.png").rename(self.output_dir / "real.png")
        os.symlink(str(self.output_dir / "real.png"), str(self.output_dir / "a.png"))
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(
            plan["protected"]["reasons"],
            ["2 files kept: their kept copy changed or is gone"],
        )


class TestCleanupJob(DuplicateFilesTestCase):
    async def test_cleanup_is_a_records_job_with_a_safety_backup(self):
        self.trio()
        plan, cleanup = await self.remove_all_but("a.png")
        summary = cleanup.summary()
        self.assertEqual(summary["kind"], "duplicate_cleanup")
        self.assertEqual(summary["title"], "Remove duplicate image files")
        self.assertEqual(summary["admission"], "records")
        self.assertFalse(summary["cancellable"])
        self.assertEqual(summary["scope"], {"operation": "duplicate_files"})
        self.assertEqual(cleanup.state, "finished", cleanup.log)
        self.assertEqual(cleanup.counters, {"removed": 2, "kept": 1, "errors": 0})
        self.assertEqual(
            sorted(row["path"] for row in cleanup.outcomes["removed"]),
            ["b.png", "sub/c.png"],
        )
        self.assertEqual(cleanup.outcomes["kept"][0]["path"], "a.png")
        [backup] = self.backups()
        self.assertEqual(
            cleanup.status_line,
            f"Removed 2 duplicate files · Safety backup {backup}",
        )
        self.assertEqual(
            _sha(self.output_dir / "a.png"), hashlib.sha256(b"same bytes").hexdigest()
        )
        self.assertFalse((self.output_dir / "b.png").exists())
        self.assertFalse((self.output_dir / "sub" / "c.png").exists())
        self.assertNotIn(self.tmpdir, json.dumps(cleanup.report()))
        # Single use, like every plan.
        again = await self.apply(plan["token"], 409)
        self.assertEqual(again["code"], "plan-used")

    async def test_thumbnails_of_removed_files_are_removed(self):
        self.trio()
        self.write("clip.mp4", b"video")
        self.write("copy.mp4", b"video")
        kept = self.write("thumbnails/a_thumb.png", b"t")
        gone = self.write("thumbnails/b_thumb.png", b"t")
        video_thumb = self.write("thumbnails/copy_thumb.jpg", b"t")
        job = await self.find()
        groups = (await self.groups(job.id))["groups"]
        keep = []
        for group in groups:
            paths = sorted(f["path"] for f in group["files"])
            keep.append(
                self.keep_of(group, "a.png" if "a.png" in paths else "clip.mp4")
            )
        plan = await self.review(job.id, keep)
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertTrue(kept.is_file())
        self.assertFalse(gone.exists())
        self.assertFalse(video_thumb.exists())

    async def test_changed_content_skips_file(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.write("b.png", b"new bytes")  # changed since the review
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.counters, {"removed": 1, "kept": 2, "errors": 0})
        self.assertEqual((self.output_dir / "b.png").read_bytes(), b"new bytes")
        self.assertFalse((self.output_dir / "sub" / "c.png").exists())
        rows = {row["path"]: row["detail"] for row in cleanup.outcomes["kept"]}
        self.assertEqual(rows["b.png"], duplicate_files.CHANGED)

    async def test_a_changed_keeper_keeps_its_whole_group(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.write("a.png", b"edited keeper")
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.counters["removed"], 0)
        self.assertTrue((self.output_dir / "b.png").is_file())
        self.assertTrue((self.output_dir / "sub" / "c.png").is_file())
        self.assertTrue(cleanup.status_line.startswith("Removed 0 duplicate files · "))
        rows = {row["path"]: row["detail"] for row in cleanup.outcomes["kept"]}
        self.assertEqual(rows["b.png"], duplicate_files.KEEPER_CHANGED)

    async def test_partial_failure_reports_each_path(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = duplicate_files._unlink

        def unlink(path):
            if os.path.basename(path) == "c.png":
                raise PermissionError(13, f"Permission denied: {path}")
            real(path)

        self.write("z.png", b"same bytes")  # never reviewed: not touched
        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            body = await self.apply(plan["token"])
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.state, "failed")
        self.assertEqual(cleanup.counters, {"removed": 1, "kept": 1, "errors": 1})
        self.assertEqual(
            [row["path"] for row in cleanup.outcomes["removed"]], ["b.png"]
        )
        [error] = cleanup.outcomes["errors"]
        self.assertEqual(
            (error["path"], error["detail"]),
            ("sub/c.png", duplicate_files.LOCKED_FILE),
        )
        self.assertEqual(
            cleanup.status_line,
            "Stopped after an error · Removed 1 duplicate file · Safety backup "
            + self.backups()[0],
        )
        report = json.dumps(cleanup.report())
        self.assertNotIn("Permission denied", report)
        self.assertNotIn("Errno", report)
        self.assertTrue((self.output_dir / "z.png").is_file())

    async def test_stop_on_failure_leaves_later_files_untouched(self):
        self.write("a.png", b"same", mtime=1)
        for name in ("b.png", "c.png", "e.png"):
            self.write(name, b"same", mtime=2)
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        with (
            patch.object(
                duplicate_files, "_unlink", side_effect=OSError(5, "I/O error")
            ),
            self.assertLogs("prompt_manager", "WARNING"),
        ):
            body = await self.apply(plan["token"])
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.counters, {"removed": 0, "kept": 3, "errors": 1})
        self.assertEqual(
            cleanup.outcomes["errors"][0]["detail"], duplicate_files.DELETE_FAILED
        )
        not_attempted = [
            row
            for row in cleanup.outcomes["kept"]
            if row["detail"] == duplicate_files.NOT_ATTEMPTED
        ]
        self.assertEqual(len(not_attempted), 2)
        for name in ("b.png", "c.png", "e.png"):
            self.assertTrue((self.output_dir / name).is_file())

    async def test_locked_file_gives_a_coded_message(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        target = self.output_dir / "b.png"
        handle = None
        if os.name == "nt":  # pragma: no cover - the native sharing violation
            handle = open(target, "rb")
            context = patch.object(duplicate_files, "_unlink", duplicate_files._unlink)
        else:
            context = patch.object(
                duplicate_files,
                "_unlink",
                side_effect=PermissionError(
                    32, "The process cannot access the file", str(target)
                ),
            )
        try:
            with context:
                body = await self.apply(plan["token"])
                cleanup = self.api._jobs.get(body["job"]["id"])
                await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        finally:
            if handle is not None:  # pragma: no cover - Windows
                handle.close()
        [error] = cleanup.outcomes["errors"]
        self.assertEqual(error["detail"], duplicate_files.LOCKED_FILE)
        self.assertNotIn("WinError", json.dumps(cleanup.report()))
        self.assertTrue(target.is_file())

    async def test_handles_closed_before_unlink(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = duplicate_files._unlink
        open_at_unlink = []

        def unlink(path):
            open_at_unlink.extend(open_handles_under(path))
            real(path)

        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            body = await self.apply(plan["token"])
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.counters["removed"], 2)
        self.assertEqual(open_at_unlink, [])

    async def test_db_links_reconciled_only_for_removed_files(self):
        self.trio()
        db = self.api.db
        moved = self.save("Moves to the keeper")
        merged = self.save("Already on the keeper")
        db.link_image_to_prompt(moved, str(self.output_dir / "b.png"))
        db.link_image_to_prompt(moved, str(self.output_dir / "d.png"))
        db.link_image_to_prompt(merged, str(self.output_dir / "a.png"))
        db.link_image_to_prompt(merged, str(self.output_dir / "sub" / "c.png"))
        db.set_image_favorite(
            duplicate_files.favorite_root_key(self.output_dir), "b.png"
        )
        plan, cleanup = await self.remove_all_but("a.png")
        self.assertEqual(
            plan["details"][0]["detail"],
            "output · a copy of a.png · 1 database link moves to the kept copy",
        )

        def files_of(prompt_id):
            return sorted(
                os.path.relpath(image["image_path"], self.output_dir)
                for image in db.get_prompt_images(prompt_id)
            )

        self.assertEqual(files_of(moved), ["a.png", "d.png"])
        self.assertEqual(files_of(merged), ["a.png"])
        root_key = duplicate_files.favorite_root_key(self.output_dir)
        self.assertEqual(db.get_root_favorites(root_key), {"a.png"})
        self.assertIn(
            "Moved 2 database links to the kept copies", "\n".join(cleanup.log)
        )

    async def test_a_failed_removal_keeps_its_links(self):
        self.trio()
        prompt = self.save("Stays on c")
        self.api.db.link_image_to_prompt(prompt, str(self.output_dir / "sub" / "c.png"))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = duplicate_files._unlink

        def unlink(path):
            if os.path.basename(path) == "c.png":
                raise PermissionError(13, "denied")
            real(path)

        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            body = await self.apply(plan["token"])
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        [image] = self.api.db.get_prompt_images(prompt)
        self.assertTrue(image["image_path"].endswith("c.png"))

    async def test_cleanup_and_find_exclude_each_other(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        gate, _calls = self.gate_hashing()
        find = (await (await self.client.request("POST", FIND)).json())["job"]
        busy = await self.apply(plan["token"], 409)
        self.assertEqual((busy["code"], busy["running"]["id"]), ("busy", find["id"]))
        gate.set()
        await asyncio.wait_for(
            asyncio.shield(self.api._jobs.get(find["id"]).task), WAIT
        )
        # The token survived the refusal; the cleanup holds off a new find.
        release = asyncio.Event()
        real = self.api._run_duplicate_cleanup

        async def held(*args):
            await release.wait()
            return await real(*args)

        with patch.object(self.api, "_run_duplicate_cleanup", side_effect=held):
            body = await self.apply(plan["token"])
            refused = await self.client.request("POST", FIND)
            self.assertEqual(refused.status, 409)
            legacy = await self.client.request("POST", STREAM)
            self.assertEqual(legacy.status, 409)
            release.set()
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)

    async def test_cleanup_refused_up_front_when_the_backup_does_not_fit(self):
        from py.api import maintenance

        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        with patch.object(maintenance, "_free_bytes", return_value=0):
            reply = await self.apply(plan["token"], 507)
        self.assertEqual(reply["code"], "insufficient-space")
        self.assertTrue((self.output_dir / "b.png").is_file())

    async def test_policy_off_removes_without_a_backup(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        self.trio()
        _plan, cleanup = await self.remove_all_but("a.png")
        self.assertEqual(self.backups(), [])
        self.assertEqual(
            cleanup.status_line, "Removed 2 duplicate files · No safety backup"
        )


class TestLegacyDuplicateRoutes(DuplicateFilesTestCase):
    async def test_legacy_delete_duplicates_unchanged(self):
        self.trio()
        response = await self.client.request(
            "POST", LEGACY_DELETE, json={"image_paths": ["b.png", "nope.png"]}
        )
        body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            set(body),
            {"success", "deleted_count", "failed_count", "message", "failed_files"},
        )
        self.assertEqual((body["deleted_count"], body["failed_count"]), (1, 1))
        self.assertEqual(body["failed_files"], ["nope.png (file not found)"])
        [hold] = self.api._jobs.list()
        self.assertEqual(
            (hold.kind, hold.title, hold.admission),
            ("duplicate_cleanup", "Duplicate image delete (gallery page)", "records"),
        )

    async def test_legacy_delete_refused_while_a_find_runs(self):
        self.trio()
        gate, _calls = self.gate_hashing()
        await self.client.request("POST", FIND)
        response = await self.client.request(
            "POST", LEGACY_DELETE, json={"image_paths": ["b.png"]}
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(
            await response.json(),
            {"success": False, "error": "Find duplicate image files already running"},
        )
        self.assertTrue((self.output_dir / "b.png").is_file())
        gate.set()

    async def test_legacy_scan_holds_the_duplicates_kind(self):
        self.trio()
        gate, _calls = self.gate_hashing()
        legacy = asyncio.ensure_future(
            self.client.request("GET", "/prompt_manager/scan_duplicates")
        )
        await asyncio.sleep(0.1)
        [hold] = self.api._jobs.list()
        self.assertEqual(
            (hold.kind, hold.title, hold.admission, hold.cancellable),
            ("duplicates", "Find duplicate image files (gallery page)", "read", False),
        )
        refused = await self.client.request("POST", FIND)
        self.assertEqual(refused.status, 409)
        # A held legacy scan publishes no events: the stream does not attach.
        stream = await self.client.request("POST", STREAM)
        self.assertEqual(stream.status, 409)
        gate.set()
        body = await (await legacy).json()
        self.assertEqual(set(body), {"success", "duplicates", "message"})
        self.assertEqual(body["duplicates"][0]["count"], 3)

    async def test_legacy_stream_does_not_attach_to_a_find_missing_its_folder(self):
        other = self.second_source()
        self.write("x.png", b"x", folder=other)
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(other)]
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)
        gate, _calls = self.gate_hashing()
        await self.client.request("POST", FIND)
        stream = await self.client.request("POST", STREAM)
        self.assertEqual(stream.status, 409)
        gate.set()


class TestLinkQueries(DuplicateFilesTestCase):
    def test_link_queries_use_the_path_indexes(self):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            for sql in duplicate_files.query_plan_sql():
                plan = " ".join(
                    row[3]
                    for row in conn.execute(
                        "EXPLAIN QUERY PLAN " + sql, [None] * sql.count("?")
                    )
                )
                with self.subTest(sql=sql):
                    self.assertNotIn("SCAN generated_images", plan)
                    self.assertNotIn("SCAN image_favorites", plan)
        finally:
            conn.close()


class TestReviewHelpers(DuplicateFilesTestCase):
    """The pure checks of py/api/duplicate_review.py."""

    def test_file_problem_on_disk(self):
        from py.api import duplicate_review as review

        root = str(self.output_dir)
        good = self.write("ok.png", b"bytes")
        digest = hashlib.sha256(b"bytes").hexdigest()
        self.assertIsNone(review.file_problem(str(good), root, digest, 5))
        self.assertEqual(
            review.file_problem(str(good), root, digest, 6), review.CHANGED_CONTENT
        )
        self.assertEqual(
            review.file_problem(str(good), root, "0" * 64, 5), review.CHANGED_CONTENT
        )
        self.assertEqual(
            review.file_problem(os.path.join(root, "gone.png"), root, digest, 5),
            review.GONE,
        )
        folder = self.output_dir / "folder.png"
        folder.mkdir()
        self.assertEqual(review.file_problem(str(folder), root, digest, 5), review.LINK)
        with patch.object(review.os, "lstat", side_effect=OSError("io")):
            self.assertEqual(
                review.file_problem(str(good), root, digest, 5), review.UNREADABLE
            )

        def unreadable(_path):
            raise OSError("io")

        self.assertEqual(
            review.file_problem(str(good), root, digest, 5, unreadable),
            review.UNREADABLE,
        )

        def vanished(_path):
            raise FileNotFoundError

        self.assertEqual(
            review.file_problem(str(good), root, digest, 5, vanished), review.GONE
        )

    def test_a_linked_folder_is_outside_its_source(self):
        from py.api import duplicate_review as review

        if not TestReview._can_symlink(self.tmpdir):  # pragma: no cover
            self.skipTest("symlinks are not available")
        target = self.write("x.png", b"bytes", folder=self.outside_dir)
        os.symlink(str(self.outside_dir), str(self.output_dir / "linked"))
        digest = hashlib.sha256(b"bytes").hexdigest()
        path = os.path.join(str(self.output_dir), "linked", "x.png")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(
            review.file_problem(path, str(self.output_dir), digest, 5),
            review.OUTSIDE_SOURCE,
        )
        self.assertTrue(target.is_file())

    def test_options_are_validated(self):
        from py.api import duplicate_review as review

        keep = {"group_id": "g", "root_index": 0, "path": "a.png"}
        cases = [
            None,
            {"job_id": "x"},
            {"job_id": "", "keep": [keep]},
            {"job_id": "x" * 65, "keep": [keep]},
            {"job_id": "x", "keep": {}},
            {"job_id": "x", "keep": [keep] * (review.MAX_KEEP + 1)},
            {"job_id": "x", "keep": [{**keep, "group_id": 1}]},
            {"job_id": "x", "keep": [{**keep, "root_index": True}]},
            {"job_id": "x", "keep": [{**keep, "root_index": -1}]},
            {"job_id": "x", "keep": [{**keep, "path": ""}]},
            {"job_id": "x", "keep": [["g", 0, "a.png"]]},
        ]
        for options in cases:
            with self.subTest(options=options):
                self.assertIsNotNone(review._parse_options(options)[2])
        job_id, parsed, message = review._parse_options(
            {"job_id": "x", "keep": [{**keep, "root_index": None}]}
        )
        self.assertEqual((job_id, parsed, message), ("x", [("g", None, "a.png")], None))

    def test_sizes_read_naturally(self):
        from py.api.duplicate_review import human_bytes

        self.assertEqual(human_bytes(10), "10 B")
        self.assertEqual(human_bytes(1536), "1.5 KB")
        self.assertEqual(human_bytes(3 * 2**20), "3.0 MB")
        self.assertEqual(human_bytes(2**40 * 5000), "5,000.0 TB")

    async def test_a_kept_copy_listed_twice_counts_once(self):
        self.trio()
        job, group = await self.found_group()
        keep = self.keep_of(group, "a.png")
        plan = await self.review(job.id, [keep, keep])
        self.assertEqual(plan["scope"]["count"], 2)

    async def test_preview_failure_is_coded(self):
        self.trio()
        job, group = await self.found_group()
        with (
            patch.object(
                self.api, "_review_duplicate_files", side_effect=OSError("boom")
            ),
            self.assertLogs("prompt_manager", "ERROR"),
        ):
            reply = await self.review(job.id, [self.keep_of(group, "a.png")], 500)
        self.assertEqual(reply["error"], "Failed to review maintenance")

    async def test_a_source_removed_before_the_apply_deletes_nothing(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.second_source())]
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.state, "failed")
        self.assertEqual(cleanup.last_event["code"], "sources-changed")
        self.assertEqual(self.backups(), [])
        self.assertTrue((self.output_dir / "b.png").is_file())

    async def test_a_link_move_failure_is_reported(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        with (
            patch.object(
                self.api.db,
                "move_file_links",
                side_effect=sqlite3.OperationalError("x"),
            ),
            self.assertLogs("prompt_manager", "ERROR"),
        ):
            body = await self.apply(plan["token"])
            cleanup = self.api._jobs.get(body["job"]["id"])
            await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertEqual(cleanup.state, "failed")
        self.assertEqual(
            cleanup.outcomes["errors"][-1]["detail"], duplicate_files.LINKS_FAILED
        )
        self.assertEqual(cleanup.counters["removed"], 2)
