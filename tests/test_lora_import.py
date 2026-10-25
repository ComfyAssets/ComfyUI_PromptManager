"""The staged LoRA reference import (#221, #249 System S10).

A failed import must leave the previous ``lora-manager`` collection exactly as
it was (prompts, tags and image links); a preview writes nothing and requests
nothing; an apply does what was reviewed or nothing. Discovery, metadata and
CivitAI downloads are disposable fixtures: no network, no LoraManager install.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_lora import (
        SECRET_KEY,
        LoraAPITestCase,
        _FakeResponse,
        _make_lora_manager,
        _png_bytes,
        _write_metadata,
    )
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_lora import (
        SECRET_KEY,
        LoraAPITestCase,
        _FakeResponse,
        _make_lora_manager,
        _png_bytes,
        _write_metadata,
    )

from database.category_import import EmptyReplacement
from database.maintenance import PlanChanged
from database.operations import PairDependencyError, PromptDatabase
from py import lora_import, lora_utils
from py.config import IntegrationConfig, PromptManagerConfig
from utils.hashing import generate_prompt_hash

CATEGORY = "lora-manager"
PREVIEW = "/prompt_manager/lora/import/preview"
APPLY = "/prompt_manager/lora/import/apply"
SCAN = "/prompt_manager/lora/scan"


def _snapshot(db):
    """Every row of the tables an import touches, in a fixed order."""
    tables = {
        "prompts": "SELECT id, text, category, hash FROM prompts ORDER BY id",
        "prompt_tags": "SELECT prompt_id, tag_id FROM prompt_tags ORDER BY 1, 2",
        "tags": "SELECT id, name FROM tags ORDER BY id",
        "generated_images": (
            "SELECT id, prompt_id, image_path, file_path FROM generated_images "
            "ORDER BY id"
        ),
    }
    with db.model.get_connection() as conn:
        return {
            name: [tuple(row) for row in conn.execute(sql).fetchall()]
            for name, sql in tables.items()
        }


def _seed_previous_imports(db):
    """Two earlier imports with tags and image links, and one other prompt."""
    ids = []
    for name in ("old-one", "old-two"):
        prompt_id = db.save_prompt(
            text=f"{name} prompt",
            category=CATEGORY,
            tags=[CATEGORY, f"lora:{name}"],
            prompt_hash=generate_prompt_hash(f"{name} prompt"),
        )
        db.link_image_to_prompt(str(prompt_id), f"/cache/{name}/a.jpg")
        db.link_image_to_prompt(str(prompt_id), f"/cache/{name}/b.jpg")
        ids.append(prompt_id)
    db.save_prompt(
        text="mine",
        category="portraits",
        tags=["keep"],
        prompt_hash=generate_prompt_hash("mine"),
    )
    return ids


def _record(text, images=(), name=None):
    return {
        "name": name or text,
        "text": text,
        "hash": generate_prompt_hash(text),
        "tags": [CATEGORY, f"lora:{name or text}"],
        "images": list(images),
    }


class CategoryReplacementTests(unittest.TestCase):
    """``replace_category_prompts``: one transaction, all or nothing."""

    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        handle.close()
        self.path = handle.name
        self.db = PromptDatabase(self.path)
        self.ids = _seed_previous_imports(self.db)

    def tearDown(self):
        self.db.close_all()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def test_transaction_failure_rolls_back_everything(self):
        before = _snapshot(self.db)
        records = [_record("new one", ["/c/n1.jpg"]), _record("new two", ["/c/n2.jpg"])]
        real = self.db._insert_image_link
        calls = []

        def failing(conn, prompt_id, image, metadata=None):
            calls.append(image)
            if len(calls) == 2:  # after the old rows went and one record landed
                raise sqlite3.OperationalError("disk I/O error")
            return real(conn, prompt_id, image, metadata)

        with patch.object(self.db, "_insert_image_link", side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.replace_category_prompts(CATEGORY, records)

        self.assertEqual(len(calls), 2)
        self.assertEqual(_snapshot(self.db), before)

    def test_pair_dependency_blocks_without_partial_delete(self):
        # A saved pair reuses an imported prompt as its positive component.
        self.db.save_prompt_pair("old-two prompt", "blurry")
        before = _snapshot(self.db)
        self.assertEqual(
            self.db.category_import_state(CATEGORY), {"ids": self.ids, "paired": 1}
        )

        with self.assertRaises(PairDependencyError):
            self.db.replace_category_prompts(CATEGORY, [_record("new one")])

        self.assertEqual(_snapshot(self.db), before)

    def test_explicit_empty_replacement_requires_review_flag(self):
        before = _snapshot(self.db)
        with self.assertRaises(EmptyReplacement):
            self.db.replace_category_prompts(CATEGORY, [])
        self.assertEqual(_snapshot(self.db), before)

        result = self.db.replace_category_prompts(CATEGORY, [], allow_empty=True)

        self.assertEqual(result["removed"], 2)
        self.assertEqual(self.db.category_import_state(CATEGORY)["ids"], [])
        self.assertEqual(len(self.db.search_prompts(category="portraits")), 1)

    def test_changed_imports_since_review_are_plan_changed(self):
        before = _snapshot(self.db)
        with self.assertRaises(PlanChanged):
            self.db.replace_category_prompts(
                CATEGORY, [_record("new one")], expected_ids=self.ids[:1]
            )
        self.assertEqual(_snapshot(self.db), before)

    def test_replacement_links_repeats_and_existing_prompts(self):
        records = [
            _record("fresh", ["/c/f.jpg"]),
            _record("Fresh ", ["/c/f2.jpg"], name="fresh-again"),  # same hash
            _record("mine", ["/c/m.jpg"], name="mine-lora"),  # exists elsewhere
        ]

        result = self.db.replace_category_prompts(
            CATEGORY, records, expected_ids=self.ids
        )

        self.assertEqual(
            (result["removed"], result["imported"], result["linked"]), (2, 1, 1)
        )
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["images"], 3)
        imported = self.db.search_prompts(category=CATEGORY)
        self.assertEqual([p["text"] for p in imported], ["fresh"])
        self.assertEqual(len(self.db.get_prompt_images(imported[0]["id"])), 2)
        mine = self.db.search_prompts(category="portraits")[0]
        self.assertEqual(len(self.db.get_prompt_images(mine["id"])), 1)
        self.assertEqual(
            [row["outcome"] for row in result["rows"]],
            ["imported", "duplicate", "linked"],
        )


class LoraImportAPITestCase(LoraAPITestCase):
    """The routes with LoraManager enabled over a disposable LoRA folder."""

    async def get_application(self):
        app = await super().get_application()
        self.lm = _make_lora_manager(self.custom_nodes)
        self.loras = self.tmp_root / "loras"
        self.loras.mkdir()
        self.cache_dir = self.tmp_root / "img_cache"
        IntegrationConfig.LORA_MANAGER_ENABLED = True
        IntegrationConfig.LORA_MANAGER_PATH = str(self.lm)
        IntegrationConfig.CIVITAI_API_KEY = SECRET_KEY
        self.requests = []
        self.gate = None  # a threading.Event a download waits on
        self.downloading = threading.Event()

        def fake_open(req, timeout=None):
            self.requests.append(req)
            self.downloading.set()
            if self.gate is not None:
                self.gate.wait(10)
            return _FakeResponse(_png_bytes())

        self._patches = [
            patch.object(
                lora_utils, "find_lora_directories", return_value=[str(self.loras)]
            ),
            patch.object(
                lora_utils, "get_lora_image_cache_dir", return_value=self.cache_dir
            ),
            patch.object(lora_utils, "_open_url", fake_open),
            patch.object(PromptManagerConfig, "SAFETY_BACKUP", False),
        ]
        for item in self._patches:
            item.start()
        self.old_ids = _seed_previous_imports(self.api.db)
        return app

    async def tearDownAsync(self):
        if self.gate is not None:
            self.gate.set()
        for job in self.api._jobs.list():
            if job.task is not None and not job.task.done():
                await job.task
        for item in reversed(self._patches):
            item.stop()
        await super().tearDownAsync()

    def _two_loras(self):
        _write_metadata(
            self.loras,
            "neon",
            words=["glow"],
            images=["https://civitai.com/a.png"],
            prompt="neon street",
        )
        (self.loras / "neon.png").write_bytes(_png_bytes())
        _write_metadata(self.loras, "plain")

    async def _post(self, url, body=None):
        resp = await self._post_json(url, {} if body is None else body)
        return resp, json.loads(await resp.text())

    async def _apply_reviewed(self):
        resp, plan = await self._post(PREVIEW)
        self.assertEqual(resp.status, 200, plan)
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual(resp.status, 202, body)
        job = self.api._jobs.get(body["job"]["id"])
        return plan, job

    @staticmethod
    def _sse(text):
        return [
            json.loads(line[len("data: ") :])
            for line in text.splitlines()
            if line.startswith("data: ")
        ]


class LoraImportRouteTests(LoraImportAPITestCase):

    async def test_preview_writes_nothing_and_makes_no_requests(self):
        self._two_loras()
        (self.loras / "broken.metadata.json").write_text("{{{")
        db_path = self.api.db.model.db_path
        before = {s: _read(db_path + s) for s in ("", "-wal")}
        shm = _size(db_path + "-shm")
        rows = _snapshot(self.api.db)

        resp, plan = await self._post(PREVIEW)

        self.assertEqual(resp.status, 200, plan)
        self.assertEqual(
            {k: plan[k] for k in ("lora_files", "with_metadata", "current_imports")},
            {"lora_files": 3, "with_metadata": 2, "current_imports": 2},
        )
        self.assertEqual(plan["blocked_by_pairs"], 0)
        self.assertEqual(plan["planned_downloads"], 1)
        self.assertEqual(plan["network"], {"planned_downloads": 1, "api_key": True})
        self.assertTrue(plan["token"])
        self.assertEqual(plan["scope"]["count"], 2)
        self.assertFalse(plan["media"]["deletes_files"])
        self.assertEqual(self.requests, [])
        self.assertFalse(self.cache_dir.exists())
        self.assertEqual({s: _read(db_path + s) for s in ("", "-wal")}, before)
        self.assertEqual(_size(db_path + "-shm"), shm)
        self.assertEqual(_snapshot(self.api.db), rows)
        self.assertNotIn(SECRET_KEY, json.dumps(plan))
        self.assertNotIn(str(self.tmp_root), json.dumps(plan))

    async def test_preview_requires_the_integration(self):
        IntegrationConfig.LORA_MANAGER_ENABLED = False
        resp, body = await self._post(PREVIEW)
        self.assertEqual((resp.status, body["code"]), (400, "lora-disabled"))

    async def test_blocked_preview_offers_no_token(self):
        self._two_loras()
        self.api.db.save_prompt_pair("old-one prompt", "blurry")

        resp, plan = await self._post(PREVIEW)

        self.assertEqual(resp.status, 200)
        self.assertEqual(plan["blocked_by_pairs"], 1)
        self.assertIsNone(plan["token"])
        self.assertEqual(plan["protected"]["count"], 1)

    async def test_changed_source_after_preview_is_409(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        rows = _snapshot(self.api.db)
        meta = self.loras / "plain.safetensors.metadata.json"
        meta.write_text(meta.read_text() + " ")

        resp, body = await self._post(APPLY, {"token": plan["token"]})

        self.assertEqual((resp.status, body["code"]), (409, "plan-changed"))
        self.assertEqual(self.api._jobs.list(), [])
        self.assertEqual(self.requests, [])
        self.assertEqual(_snapshot(self.api.db), rows)

    async def test_apply_imports_through_the_job(self):
        self._two_loras()

        plan, job = await self._apply_reviewed()
        self.assertEqual(
            (job.kind, job.title, job.admission),
            ("lora_import", plan_title(), "records"),
        )
        await job.task

        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            {k: job.counters[k] for k in lora_import.GROUPS},
            {
                "imported": 2,
                "linked": 0,
                "skipped": 0,
                "downloaded": 1,
                "download_errors": 0,
                "errors": 0,
            },
        )
        self.assertFalse(job.cancellable)
        self.assertEqual(
            self.requests[0].get_header("Authorization"), f"Bearer {SECRET_KEY}"
        )
        imported = self.api.db.search_prompts(category=CATEGORY)
        self.assertEqual(sorted(p["text"] for p in imported), ["neon street", "plain"])
        neon = next(p for p in imported if p["text"] == "neon street")
        self.assertEqual(len(self.api.db.get_prompt_images(neon["id"])), 2)
        self.assertEqual(len(self.api.db.search_prompts(category="portraits")), 1)
        # The token is single-use.
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "plan-used"))

    async def test_discovery_failure_keeps_previous_imports(self):
        self._two_loras()
        before = _snapshot(self.api.db)

        # Legacy scan: discovery raises once the stream is open.
        with patch.object(
            lora_utils, "find_lora_directories", side_effect=OSError("share gone")
        ):
            resp = await self.client.request("POST", SCAN)
            events = self._sse(await resp.text())
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(_snapshot(self.api.db), before)

        # Job: staging fails after the review.
        with patch.object(lora_import, "stage", side_effect=OSError("share gone")):
            _plan, job = await self._apply_reviewed()
            await job.task
        self.assertEqual(job.state, "failed")
        self.assertIn("previous LoRA imports were kept", job.status_line)
        self.assertEqual(job.scope["failure"], "import-failed")
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_a_failed_replacement_keeps_previous_imports_and_says_so(self):
        self._two_loras()
        before = _snapshot(self.api.db)
        with patch.object(
            self.api.db,
            "replace_category_prompts",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            _plan, job = await self._apply_reviewed()
            await job.task

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(job.counters["downloaded"], 1)
        self.assertIn("previous LoRA imports were kept", job.status_line)
        self.assertIn("stay in the image cache", job.status_line)
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_cancel_during_downloads_writes_nothing(self):
        self._two_loras()
        before = _snapshot(self.api.db)
        self.gate = threading.Event()

        _plan, job = await self._apply_reviewed()
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.downloading.wait, 10))
        self.assertTrue(job.summary()["cancellable"])
        self.assertTrue(self.api._jobs.cancel(job.id))
        self.gate.set()
        await job.task

        self.assertEqual(job.state, "cancelled")
        self.assertEqual(_snapshot(self.api.db), before)
        self.assertTrue(any("stay in the image cache" in line for line in job.log))

    async def test_empty_reviewed_import_removes_the_collection(self):
        # No metadata at all: the review says so, and its apply is the flag.
        resp, plan = await self._post(PREVIEW)
        self.assertEqual((plan["with_metadata"], plan["current_imports"]), (0, 2))
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        job = self.api._jobs.get(body["job"]["id"])
        await job.task

        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(self.api.db.search_prompts(category=CATEGORY), [])

        # The legacy scan has no review: an empty replacement is refused.
        _seed_previous_imports(self.api.db)
        before = _snapshot(self.api.db)
        resp = await self.client.request("POST", SCAN)
        events = self._sse(await resp.text())
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_apply_refused_while_a_records_job_holds(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        async with self.api._jobs.hold("scan", "Metadata scan", admission="records"):
            resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "busy"))
        # The token was not used: it still applies.
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual(resp.status, 202)
        await self.api._jobs.get(body["job"]["id"]).task

    async def test_safety_backup_policy_runs_before_the_replace(self):
        self._two_loras()
        order = []
        real = self.api.db.replace_category_prompts

        async def step(job, db, backup_on):
            order.append(("backup", backup_on))
            return True, "prompts.db.backup_x"

        def replace(*args, **kwargs):
            order.append(("replace", None))
            return real(*args, **kwargs)

        with (
            patch.object(PromptManagerConfig, "SAFETY_BACKUP", True),
            patch.object(self.api, "_safety_backup_step", side_effect=step),
            patch.object(self.api.db, "replace_category_prompts", side_effect=replace),
        ):
            plan, job = await self._apply_reviewed()
            await job.task
        self.assertEqual(plan["backup"]["policy"], "on")
        self.assertEqual(order, [("backup", True), ("replace", None)])

    async def test_legacy_scan_events_unchanged_on_success(self):
        self._two_loras()
        (self.loras / "broken.metadata.json").write_text("{{{")

        resp = await self.client.request("POST", SCAN)
        events = self._sse(await resp.text())

        self.assertEqual(resp.status, 200)
        progress_keys = {
            "type",
            "status",
            "progress",
            "total",
            "processed",
            "imported",
            "skipped",
        }
        for event in events[:-1]:
            self.assertEqual(event["type"], "progress")
            self.assertLessEqual(set(event), progress_keys)
            self.assertIsInstance(event["status"], str)
            self.assertIsInstance(event["progress"], int)
        per_record = [e for e in events if "processed" in e]
        self.assertEqual([e["processed"] for e in per_record], [1, 2, 3])
        self.assertEqual(
            events[-1],
            {
                "type": "complete",
                "progress": 100,
                "total": 3,
                "imported": 2,
                "skipped": 1,
            },
        )
        imported = self.api.db.search_prompts(category=CATEGORY)
        self.assertEqual(sorted(p["text"] for p in imported), ["neon street", "plain"])


class LoraImportRefusalTests(LoraImportAPITestCase):
    """Every refusal leaves the previous imports as they were."""

    async def test_preview_refusals(self):
        IntegrationConfig.LORA_MANAGER_PATH = ""
        resp, body = await self._post(PREVIEW)
        self.assertEqual((resp.status, body["code"]), (400, "lora-path"))

        IntegrationConfig.LORA_MANAGER_PATH = str(_make_lora_manager(self.tmp_root))
        resp, body = await self._post(PREVIEW)
        self.assertEqual((resp.status, body["code"]), (400, "lora-path"))
        self.assertNotIn(str(self.tmp_root), json.dumps(body))

        IntegrationConfig.LORA_MANAGER_PATH = str(self.lm)
        resp, body = await self._post(PREVIEW, {"extra": 1})
        self.assertEqual((resp.status, body["code"]), (400, "invalid-request"))

        with patch.object(lora_import, "preview", side_effect=OSError("gone")):
            resp, body = await self._post(PREVIEW)
        self.assertEqual(resp.status, 500)
        self.assertFalse(body["success"])

        generations = iter([(0, "a"), (1, "a")])
        with patch.object(
            self.api, "_database_identity", side_effect=lambda db: next(generations)
        ):
            resp, body = await self._post(PREVIEW)
        self.assertEqual((resp.status, body["code"]), (409, "storage-changed"))

    async def test_apply_refusals(self):
        self._two_loras()
        before = _snapshot(self.api.db)
        resp, body = await self._post(APPLY, {"token": ""})
        self.assertEqual((resp.status, body["code"]), (400, "invalid-request"))
        resp, body = await self._post(APPLY, {"token": "unknown"})
        self.assertEqual((resp.status, body["code"]), (410, "plan-expired"))

        other = self.api._maintenance_plans.issue(
            "optimize", "f", {"backup": "off"}, self.api._database_identity(self.api.db)
        )
        resp, body = await self._post(APPLY, {"token": other})
        self.assertEqual((resp.status, body["code"]), (410, "plan-expired"))

        resp, plan = await self._post(PREVIEW)
        with patch.object(lora_import, "discover", side_effect=OSError("gone")):
            resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (500, "apply-failed"))

        short = ("database folder", "the safety backup", 10, 1)
        with patch("py.api.lora_import_routes.disk_plan", return_value=({}, short, 0)):
            resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (507, "insufficient-space"))

        self.api._db_generation += 1
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "storage-changed"))
        self.assertEqual(self.api._jobs.list(), [])
        self.assertEqual(_snapshot(self.api.db), before)

    async def _failed_job(self, **patches):
        before = _snapshot(self.api.db)
        _plan, job = await self._apply_reviewed()
        await job.task
        self.assertEqual(job.state, "failed", job.log)
        self.assertEqual(_snapshot(self.api.db), before)
        return job

    async def test_imports_changed_after_the_review_fail_the_job(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        self.api.db.save_prompt(
            text="late", category=CATEGORY, prompt_hash=generate_prompt_hash("late")
        )
        before = _snapshot(self.api.db)
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        job = self.api._jobs.get(body["job"]["id"])
        await job.task
        self.assertEqual((job.state, job.scope["failure"]), ("failed", "plan-changed"))
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_a_pair_saved_after_the_review_blocks_the_job(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        self.api.db.save_prompt_pair("old-one prompt", "blurry")
        before = _snapshot(self.api.db)
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        job = self.api._jobs.get(body["job"]["id"])
        await job.task
        self.assertEqual(
            (job.state, job.scope["failure"]), ("failed", "blocked-by-pairs")
        )
        self.assertIn("Saved pairs", job.status_line)
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_nothing_prepared_keeps_the_collection(self):
        self._two_loras()
        with patch.object(lora_import, "build_record", side_effect=ValueError("x")):
            job = await self._failed_job()
        self.assertEqual(job.scope["failure"], "nothing-to-import")
        self.assertEqual(job.counters["skipped"], 0)

    async def test_files_changed_while_staging_fail_the_job(self):
        self._two_loras()
        real = lora_import.stage

        def changed(*args, **kwargs):
            staged = real(*args, **kwargs)
            staged.fingerprint = "sha256:other"
            return staged

        with patch.object(lora_import, "stage", side_effect=changed):
            job = await self._failed_job()
        self.assertEqual(job.scope["failure"], "plan-changed")

    async def test_a_failed_safety_backup_changes_nothing(self):
        self._two_loras()
        from py.api import maintenance

        with (
            patch.object(PromptManagerConfig, "SAFETY_BACKUP", True),
            patch.object(
                maintenance,
                "take_safety_backup",
                side_effect=maintenance.SafetyBackupFailed("no room"),
            ),
        ):
            job = await self._failed_job()
        self.assertEqual(job.scope["failure"], "backup-failed")

    async def test_cancel_right_after_the_start_writes_nothing(self):
        self._two_loras()
        before = _snapshot(self.api.db)
        resp, plan = await self._post(PREVIEW)
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        job = self.api._jobs.get(body["job"]["id"])
        self.api._jobs.cancel(job.id)  # at once, while it stages
        await job.task
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(_snapshot(self.api.db), before)


def plan_title():
    return lora_import.JOB_TITLE


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _size(path):
    return os.path.getsize(path) if os.path.exists(path) else None


class LoraImportStageTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_discover_sorts_and_drops_repeats(self):
        a = self.tmp / "a"
        b = self.tmp / "a" / "b"
        b.mkdir(parents=True)
        _write_metadata(b, "zeta")
        _write_metadata(a, "alpha")
        with patch.object(
            lora_utils, "find_lora_directories", return_value=[str(a), str(b)]
        ):
            files = lora_import.discover(str(self.tmp))
        self.assertEqual([f.name for f in files][0], "alpha.safetensors.metadata.json")
        self.assertEqual(len(files), 2)

    def test_fingerprint_follows_size_and_mtime(self):
        meta = _write_metadata(self.tmp, "one")
        first = lora_import.fingerprint([meta])
        self.assertEqual(lora_import.fingerprint([meta]), first)
        meta.write_text(meta.read_text() + " ")
        self.assertNotEqual(lora_import.fingerprint([meta]), first)
        meta.unlink()
        self.assertNotEqual(lora_import.fingerprint([meta]), first)

    def test_stage_cancel_before_a_file_raises(self):
        meta = _write_metadata(self.tmp, "one")
        with self.assertRaises(lora_import.ImportCancelled):
            lora_import.stage([meta], self.tmp, cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
