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
import time
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
from py.api.jobs import RESTORE_JOB_KIND
from py.api.lora_import_routes import LORA_PREVIEW_LEASE
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

    # N1: a collection larger than SQLite's bound-variable limit is replaced.
    def test_more_imports_than_sqlite_variables(self):
        conn = self.db.model.get_connection()
        if not hasattr(conn, "setlimit"):
            self.skipTest("sqlite3.Connection.setlimit needs Python 3.11")
        rows = [
            (f"bulk lora {n}", CATEGORY, generate_prompt_hash(f"bulk lora {n}"))
            for n in range(1200)
        ]
        with conn:
            conn.executemany(
                "INSERT INTO prompts(text, category, hash, created_at, updated_at) "
                "VALUES(?, ?, ?, '2026-01-01', '2026-01-01')",
                rows,
            )
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        state = self.db.category_import_state(CATEGORY)
        self.assertEqual((len(state["ids"]), state["paired"]), (1202, 0))

        result = self.db.replace_category_prompts(
            CATEGORY, [_record("only one")], expected_ids=state["ids"]
        )

        self.assertEqual((result["removed"], result["imported"]), (1202, 1))


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
        self.failing = set()  # URLs whose download fails

        def fake_open(req, timeout=None):
            self.requests.append(req)
            self.downloading.set()
            if req.full_url in self.failing:
                raise OSError("connection reset")
            if self.gate is not None:
                self.gate.wait(10)
            return _FakeResponse(_png_bytes())

        self._patches = [
            patch.object(
                lora_utils, "default_lora_roots", return_value=[str(self.loras)]
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
        """``neon`` has only a CivitAI image (downloaded); ``plain`` has a
        local preview (linked where it is, nothing downloaded)."""
        _write_metadata(
            self.loras,
            "neon",
            words=["glow"],
            images=["https://civitai.com/a.png"],
            prompt="neon street",
        )
        _write_metadata(self.loras, "plain", images=["https://civitai.com/p.png"])
        (self.loras / "plain.png").write_bytes(_png_bytes())

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
            {
                k: plan[k]
                for k in (
                    "lora_files",
                    "with_metadata",
                    "skipped",
                    "with_local_media",
                    "current_imports",
                )
            },
            {
                "lora_files": 3,
                "with_metadata": 2,
                "skipped": 1,
                "with_local_media": 1,
                "current_imports": 2,
            },
        )
        self.assertEqual(
            plan["layout"],
            {
                "source": "environment",
                "settings_file": str(self.lm_settings / "settings.json"),
                "error": None,
                "mode": "alongside",
                "library": "default",
                "example_images": False,
            },
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
        # The settings file is the only absolute path the review shows.
        shown = dict(plan, layout=dict(plan["layout"], settings_file=None))
        self.assertNotIn(str(self.tmp_root), json.dumps(shown))

    async def test_unusable_settings_review_offers_no_token(self):
        self._two_loras()
        (self.lm_settings / "settings.json").write_text("[1, 2]")

        resp, plan = await self._post(PREVIEW)

        self.assertEqual(resp.status, 200, plan)
        self.assertIsNone(plan["token"])
        self.assertEqual(
            plan["layout"]["error"], "the settings file is not a JSON object"
        )
        self.assertEqual((plan["lora_files"], plan["with_metadata"]), (0, 0))
        self.assertEqual(self.requests, [])

    async def test_centralized_review_counts_what_it_skips(self):
        sidecars = self.tmp_root / "sidecars"
        examples = self.tmp_root / "examples"
        (self.lm_settings / "settings.json").write_text(
            json.dumps(
                {
                    "sidecar_storage_mode": "centralized",
                    "sidecar_storage_path": str(sidecars),
                    "example_images_path": str(examples),
                    "libraries": {"comfyui": {}},
                    "active_library": "comfyui",
                }
            )
        )
        component = sidecars / "loras-0a1b2c3d"
        component.mkdir(parents=True)
        model = self.loras / "neon.safetensors"
        model.write_bytes(b"m")
        meta = {"model_name": "neon", "file_path": str(model), "sha256": "AB12"}
        (component / "neon.metadata.json").write_text(json.dumps(meta))
        (examples / "ab12").mkdir(parents=True)
        (examples / "ab12" / "image_1.png").write_bytes(_png_bytes())
        gone = dict(meta, file_path=str(self.loras / "gone.safetensors"))
        (component / "gone.metadata.json").write_text(json.dumps(gone))
        (component / "broken.metadata.json").write_text("{{{")

        resp, plan = await self._post(PREVIEW)

        self.assertEqual(resp.status, 200, plan)
        self.assertEqual(
            (
                plan["lora_files"],
                plan["with_metadata"],
                plan["skipped"],
                plan["with_local_media"],
                plan["planned_downloads"],
            ),
            (3, 1, 2, 1, 0),
        )
        self.assertEqual(
            (plan["layout"]["mode"], plan["layout"]["library"]),
            ("centralized", "comfyui"),
        )
        self.assertTrue(plan["layout"]["example_images"])
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        job = self.api._jobs.get(body["job"]["id"])
        await job.task
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(self.requests, [])
        self.assertEqual(
            [row["detail"] for row in job.outcomes["skipped"]],
            ["broken.metadata.json"],
        )
        imported = self.api.db.search_prompts(category=CATEGORY)
        images = self.api.db.get_prompt_images(imported[0]["id"])
        self.assertEqual(
            [os.path.basename(i["image_path"]) for i in images], ["image_1.png"]
        )

    async def test_an_unlistable_folder_says_so_in_the_review(self):
        self._two_loras()
        error = PermissionError(13, "Permission denied", str(self.loras / "lost+found"))
        with patch.object(lora_import, "discover_sidecars", side_effect=error):
            resp, body = await self._post(PREVIEW)
        self.assertEqual((resp.status, body["code"]), (500, "folder-unreadable"))
        self.assertEqual(
            body["error"],
            "A LoRA folder could not be listed (Permission denied: lost+found). "
            "Nothing was changed.",
        )
        self.assertNotIn(str(self.tmp_root), json.dumps(body))

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
        meta = self.loras / "plain.metadata.json"
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
        self.assertEqual(len(self.api.db.get_prompt_images(neon["id"])), 1)
        # plain's local preview is linked where it is; its CivitAI image is not
        # downloaded (#355).
        plain = next(p for p in imported if p["text"] == "plain")
        self.assertEqual(
            [i["image_path"] for i in self.api.db.get_prompt_images(plain["id"])],
            [os.path.realpath(self.loras / "plain.png")],
        )
        self.assertEqual(
            [r.full_url for r in self.requests], ["https://civitai.com/a.png"]
        )
        self.assertEqual(len(self.api.db.search_prompts(category="portraits")), 1)
        # The token is single-use.
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "plan-used"))

    async def test_a_finished_import_clears_the_layout_cache(self):
        # #362: the import may link files under a folder LoraManager created
        # after the image routes cached its layout.
        self._two_loras()
        with patch("py.lora_utils.clear_layout_cache") as clear:
            _plan, job = await self._apply_reviewed()
            await job.task
        self.assertEqual(job.state, "finished", job.log)
        clear.assert_called_once_with()

    async def test_discovery_failure_keeps_previous_imports(self):
        self._two_loras()
        before = _snapshot(self.api.db)

        # Legacy scan: discovery raises once the stream is open.
        with patch.object(
            lora_import, "discover_sidecars", side_effect=OSError("share gone")
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

    async def test_a_disconnected_legacy_scan_stops_and_holds_until_it_stopped(self):
        # I2: the admin page closes its modal while the downloads run.
        for number in range(4):
            _write_metadata(
                self.loras,
                f"lora{number}",
                images=[f"https://civitai.com/{number}.png"],
            )
        before = _snapshot(self.api.db)
        self.gate = threading.Event()
        resp = await self.client.request("POST", SCAN)
        await resp.content.readline()  # the stream is open
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.downloading.wait, 10))
        resp.close()
        await asyncio.sleep(0.05)  # the server sees the closed connection
        # Its records hold stays while the in-flight download runs.
        self.assertIsNotNone(self.api._jobs.active("lora_import"))
        self.gate.set()
        for _ in range(500):
            if self.api._jobs.active("lora_import") is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(self.api._jobs.active("lora_import"))
        # R2 (re-review): the user left and nothing failed, so Activity says so.
        job = next(j for j in self.api._jobs.list() if j.kind == "lora_import")
        self.assertEqual(job.state, "cancelled")
        # Staging stopped at the next LoRA: no download runs after the hold.
        served = len(self.requests)
        self.assertLess(served, 4)
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.requests), served)
        self.assertEqual(_snapshot(self.api.db), before)

    async def test_an_import_sweeps_stale_temp_downloads_from_the_cache(self):
        # N7 (re-review): a download killed mid-write leaves a .pm-*.part file;
        # the next import removes it once it is an hour old.
        self._two_loras()
        stale = self.cache_dir / "old_lora" / ".pm-dead.part"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"x")
        stamp = time.time() - 2 * 3600
        os.utime(stale, (stamp, stamp))

        resp = await self.client.request("POST", SCAN)
        await resp.text()

        self.assertFalse(stale.exists())

    async def test_legacy_scan_events_unchanged_on_success(self):
        self._two_loras()
        (self.loras / "broken.metadata.json").write_text("{{{")
        (self.loras / "broken.safetensors").write_bytes(b"model")

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


class LoraImportDownloadOutcomeTests(LoraImportAPITestCase):
    """#337: failed CivitAI downloads show in the job's status line."""

    URLS = ["https://civitai.com/a.png", "https://civitai.com/b.png"]

    def _lora_with_images(self):
        _write_metadata(
            self.loras,
            "neon",
            words=["glow"],
            images=list(self.URLS),
            prompt="neon street",
        )
        _write_metadata(self.loras, "plain")

    async def _run(self):
        _plan, job = await self._apply_reviewed()
        await job.task
        return job

    async def test_status_line_unchanged_when_every_download_succeeds(self):
        self._lora_with_images()

        job = await self._run()

        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.status_line, "Imported 2 LoRAs")
        self.assertEqual(job.outcomes["download_errors"], [])

    async def test_status_line_counts_failed_downloads(self):
        self._lora_with_images()
        self.failing = {self.URLS[1]}

        job = await self._run()

        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            (job.counters["downloaded"], job.counters["download_errors"]), (1, 1)
        )
        self.assertEqual(
            job.status_line,
            "Imported 2 LoRAs · 1 example image could not be downloaded",
        )
        self.assertNotIn("Warning", job.status_line)
        self.assertEqual(job.outcomes["download_errors"], [])

    async def test_every_download_failing_is_a_warning(self):
        self._lora_with_images()
        self.failing = set(self.URLS)

        job = await self._run()

        # The import itself succeeded, so the job still finishes.
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            (job.counters["downloaded"], job.counters["download_errors"]), (0, 2)
        )
        warning = "Warning: none of the 2 example images could be downloaded"
        self.assertEqual(job.status_line, f"{warning} · Imported 2 LoRAs")
        self.assertEqual(
            job.outcomes["download_errors"],
            [
                {
                    "path": "",
                    "root": "",
                    "root_index": None,
                    "detail": f"{warning}; the log has the details",
                }
            ],
        )
        self.assertTrue(any(warning in line for line in job.log), job.log)
        self.assertEqual(job.summary()["status_line"], job.status_line)
        self.assertNotIn(SECRET_KEY, json.dumps(job.report()))

    async def test_one_planned_download_failing_reads_in_the_singular(self):
        _write_metadata(self.loras, "neon", images=[self.URLS[0]], prompt="neon street")
        self.failing = {self.URLS[0]}

        job = await self._run()

        self.assertEqual(
            job.status_line,
            "Warning: the example image could not be downloaded · Imported 1 LoRA",
        )

    async def test_legacy_scan_events_unchanged_when_every_download_fails(self):
        self._lora_with_images()
        self.failing = set(self.URLS)

        resp = await self.client.request("POST", SCAN)
        events = self._sse(await resp.text())

        self.assertEqual(resp.status, 200)
        self.assertEqual(
            events[-1],
            {
                "type": "complete",
                "progress": 100,
                "total": 2,
                "imported": 2,
                "skipped": 0,
            },
        )
        for event in events[:-1]:
            self.assertEqual(event["type"], "progress")
            self.assertNotIn("Warning", event.get("status", ""))


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

    async def test_start_refused_when_the_integration_changed_since_the_review(self):
        # M5: disabled, or moved to another install, after the review.
        self._two_loras()
        before = _snapshot(self.api.db)
        resp, plan = await self._post(PREVIEW)
        IntegrationConfig.LORA_MANAGER_ENABLED = False
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "plan-changed"))
        self.assertIn("integration changed", body["error"])

        IntegrationConfig.LORA_MANAGER_ENABLED = True
        resp, plan = await self._post(PREVIEW)
        other = _make_lora_manager(self.custom_nodes / "other")
        target = self.custom_nodes / "Other-Lora-Manager"
        other.rename(target)
        IntegrationConfig.LORA_MANAGER_PATH = str(target)
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual((resp.status, body["code"]), (409, "plan-changed"))
        self.assertEqual(self.api._jobs.list(), [])
        self.assertEqual(_snapshot(self.api.db), before)

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

    async def test_a_refused_import_keeps_the_layout_cache(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        self.api.db.save_prompt(
            text="late", category=CATEGORY, prompt_hash=generate_prompt_hash("late")
        )
        with patch("py.lora_utils.clear_layout_cache") as clear:
            resp, body = await self._post(APPLY, {"token": plan["token"]})
            job = self.api._jobs.get(body["job"]["id"])
            await job.task
        self.assertEqual(job.scope["failure"], "plan-changed")
        clear.assert_not_called()

    async def test_start_refused_when_a_skipped_sidecar_changed(self):
        # #362 CUJ 4: the review counted the orphan as skipped.
        self._two_loras()
        orphan = self.loras / "orphan.metadata.json"
        orphan.write_text(json.dumps({"model_name": "orphan"}))
        resp, plan = await self._post(PREVIEW)
        self.assertEqual(resp.status, 200, plan)
        self.assertEqual((plan["lora_files"], plan["skipped"]), (3, 1))
        before = _snapshot(self.api.db)
        orphan.write_text(orphan.read_text() + "  ")
        resp, body = await self._post(APPLY, {"token": plan["token"]})
        self.assertEqual(resp.status, 409, body)
        self.assertEqual(body["code"], "plan-changed")
        self.assertEqual(self.api._jobs.list(), [])
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
        # M4: the job keeps what staging skipped, by name, though it failed.
        self.assertEqual(job.counters["skipped"], 2)
        self.assertEqual(
            sorted(row["detail"] for row in job.outcomes["skipped"]), ["neon", "plain"]
        )

    async def _change_during_download(self, change):
        """Start the reviewed import, run ``change()`` while a download is held."""
        self._two_loras()
        before = _snapshot(self.api.db)
        self.gate = threading.Event()
        _plan, job = await self._apply_reviewed()
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.downloading.wait, 10))
        change()
        self.gate.set()
        await job.task
        self.assertEqual(job.state, "failed", job.log)
        self.assertEqual(job.scope["failure"], "plan-changed")
        self.assertIn("Review the import again", job.status_line)
        self.assertEqual(_snapshot(self.api.db), before)
        return job

    async def test_files_changed_while_staging_fail_the_job(self):
        # I1: LoraManager rewrites a metadata file while the downloads run.
        meta = self.loras / "plain.metadata.json"
        await self._change_during_download(
            lambda: meta.write_text(meta.read_text() + "  ")
        )

    async def test_a_file_removed_while_staging_fails_the_job(self):
        meta = self.loras / "plain.metadata.json"
        await self._change_during_download(meta.unlink)

    async def test_a_file_added_while_staging_fails_the_job(self):
        await self._change_during_download(lambda: _write_metadata(self.loras, "late"))

    async def test_legacy_scan_refuses_files_changed_while_it_stages(self):
        self._two_loras()
        before = _snapshot(self.api.db)
        self.gate = threading.Event()
        meta = self.loras / "plain.metadata.json"
        resp = await self.client.request("POST", SCAN)
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.downloading.wait, 10))
        meta.write_text(meta.read_text() + "  ")
        self.gate.set()
        events = self._sse(await resp.text())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("changed during the scan", events[-1]["message"])
        self.assertEqual(_snapshot(self.api.db), before)

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


class LoraImportRestoreTests(LoraImportAPITestCase):
    """The import and a restore (#249 S8) never run beside each other."""

    async def test_a_restore_is_refused_while_the_import_runs(self):
        self._two_loras()
        self.gate = threading.Event()
        _plan, job = await self._apply_reviewed()
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.downloading.wait, 10))

        resp = await self.client.request("POST", "/prompt_manager/restore")
        body = json.loads(await resp.text())
        self.assertEqual(resp.status, 409)
        self.assertIn(lora_import.JOB_TITLE, body["error"])
        # The workspace restore job is refused for the same running import.
        blocker = self.api._jobs.blocker(RESTORE_JOB_KIND, admission="exclusive")
        self.assertIs(blocker, job)

        self.gate.set()
        await job.task
        self.assertEqual(job.state, "finished")

    async def test_the_import_is_refused_while_a_restore_runs(self):
        self._two_loras()
        resp, plan = await self._post(PREVIEW)
        self.assertEqual(resp.status, 200, plan)
        before = _snapshot(self.api.db)
        async with self.api._jobs.hold(
            RESTORE_JOB_KIND, "Restore library", admission="exclusive"
        ):
            # A review reads the library: refused, not run beside the switch.
            resp, body = await self._post(PREVIEW)
            self.assertEqual(resp.status, 409, body)
            self.assertEqual(body["code"], "busy")
            self.assertEqual(body["running"]["title"], "Restore library")
            resp, body = await self._post(APPLY, {"token": plan["token"]})
            self.assertEqual(resp.status, 409, body)
            self.assertEqual(body["code"], "busy")
            # The admin page's scan says so in its own error event.
            resp = await self.client.request("POST", SCAN)
            events = self._sse(await resp.text())
            self.assertEqual(events[-1]["type"], "error")
            self.assertIn("Restore library", events[-1]["message"])
        self.assertEqual(_snapshot(self.api.db), before)
        self.assertIsNone(self.api._jobs.active("lora_import"))

    async def test_a_running_review_holds_a_lease_a_restore_waits_for(self):
        self._two_loras()
        seen = []
        real_preview = lora_import.preview

        def preview(*args):
            seen.append(self.api._jobs.leases)
            return real_preview(*args)

        with patch.object(lora_import, "preview", preview):
            resp, plan = await self._post(PREVIEW)
        self.assertEqual(resp.status, 200, plan)
        self.assertEqual(seen, [{LORA_PREVIEW_LEASE: 1}])
        self.assertEqual(self.api._jobs.leases, {})


class LoraImportStageTests(unittest.TestCase):
    """The stages over a disposable LoraManager install (#355)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.lm = _make_lora_manager(self.tmp / "custom_nodes")
        self.loras = self.tmp / "loras"
        self.loras.mkdir()
        self.settings = self.tmp / "settings"
        self.settings.mkdir()
        self.roots = [str(self.loras)]
        patchers = [
            patch.object(
                lora_utils,
                "custom_nodes_directories",
                return_value=[self.tmp / "custom_nodes"],
            ),
            patch.object(lora_utils, "default_lora_roots", lambda: list(self.roots)),
            patch.dict(os.environ, {"LORA_MANAGER_SETTINGS_DIR": str(self.settings)}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def settings_file(self, data):
        (self.settings / "settings.json").write_text(json.dumps(data))

    def discover(self):
        return lora_import.discover(str(self.lm))

    def test_discover_sorts_and_drops_repeats(self):
        a = self.loras / "a"
        b = self.loras / "a" / "b"
        b.mkdir(parents=True)
        _write_metadata(b, "zeta")
        _write_metadata(a, "alpha")
        self.roots = [str(a), str(b)]
        discovery = self.discover()
        names = [s.metadata_path.name for s in discovery.sidecars]
        self.assertEqual(names, ["alpha.metadata.json", "zeta.metadata.json"])
        self.assertEqual(discovery.scanned, 2)

    def test_discover_without_an_install_finds_nothing(self):
        _write_metadata(self.loras, "alpha")
        discovery = lora_import.discover(str(self.tmp))
        self.assertEqual(discovery.layout.error, "no LoraManager install found")
        self.assertEqual(discovery.sidecars, [])

    def test_fingerprint_follows_size_and_mtime(self):
        meta = _write_metadata(self.loras, "one")
        first = lora_import.fingerprint(self.discover())
        self.assertEqual(lora_import.fingerprint(self.discover()), first)
        meta.write_text(meta.read_text() + " ")
        self.assertNotEqual(lora_import.fingerprint(self.discover()), first)
        changed = lora_import.fingerprint(self.discover())
        meta.unlink()
        self.assertNotEqual(lora_import.fingerprint(self.discover()), changed)

    def test_fingerprint_ignores_unrelated_settings(self):
        _write_metadata(self.loras, "one")
        self.settings_file({"civitai_api_key": "a"})
        first = lora_import.fingerprint(self.discover())
        self.settings_file({"civitai_api_key": "b", "theme": "dark"})
        self.assertEqual(lora_import.fingerprint(self.discover()), first)
        sidecars = self.tmp / "sidecars"
        sidecars.mkdir()
        self.settings_file(
            {
                "sidecar_storage_mode": "centralized",
                "sidecar_storage_path": str(sidecars),
            }
        )
        self.assertNotEqual(lora_import.fingerprint(self.discover()), first)

    # #362: a skipped sidecar is part of what the review counted.

    def _assert_fingerprint_follows(self, change):
        first = lora_import.fingerprint(self.discover())
        self.assertEqual(lora_import.fingerprint(self.discover()), first)
        change()
        self.assertNotEqual(lora_import.fingerprint(self.discover()), first)

    def _assert_add_edit_remove_change_it(self, path, text):
        self._assert_fingerprint_follows(lambda: path.write_text(text))
        self._assert_fingerprint_follows(
            lambda: path.write_text(path.read_text() + "  ")
        )
        self._assert_fingerprint_follows(path.unlink)

    def _centralized(self):
        sidecars = self.tmp / "sidecars"
        sidecars.mkdir()
        self.settings_file(
            {
                "sidecar_storage_mode": "centralized",
                "sidecar_storage_path": str(sidecars),
            }
        )
        model = self.loras / "kept.safetensors"
        model.write_bytes(b"m")
        (sidecars / "kept.metadata.json").write_text(
            json.dumps({"file_path": str(model)})
        )
        return sidecars

    def test_fingerprint_follows_a_centralized_sidecar_whose_model_is_missing(self):
        sidecars = self._centralized()
        gone = self.loras / "gone.safetensors"
        self._assert_add_edit_remove_change_it(
            sidecars / "gone.metadata.json", json.dumps({"file_path": str(gone)})
        )
        self.assertEqual(len(self.discover().sidecars), 1)

    def test_fingerprint_follows_a_checkpoint_sidecar_outside_the_lora_roots(self):
        sidecars = self._centralized()
        checkpoints = self.tmp / "checkpoints"
        checkpoints.mkdir()
        ckpt = checkpoints / "base.safetensors"
        ckpt.write_bytes(b"c")
        self._assert_add_edit_remove_change_it(
            sidecars / "base.metadata.json", json.dumps({"file_path": str(ckpt)})
        )

    def test_fingerprint_follows_an_alongside_sidecar_without_its_model(self):
        _write_metadata(self.loras, "kept")
        self._assert_add_edit_remove_change_it(
            self.loras / "orphan.metadata.json", json.dumps({"model_name": "orphan"})
        )
        self.assertEqual(len(self.discover().sidecars), 1)

    def test_fingerprint_follows_a_missing_file_between_stat_calls(self):
        meta = _write_metadata(self.loras, "one")
        discovery = self.discover()
        first = lora_import.fingerprint(discovery)
        meta.unlink()
        self.assertNotEqual(lora_import.fingerprint(discovery), first)

    def test_stage_cancel_before_a_file_raises(self):
        _write_metadata(self.loras, "one")
        with self.assertRaises(lora_import.ImportCancelled):
            lora_import.stage(
                self.discover(),
                self.tmp,
                media_extensions=MEDIA,
                cancelled=lambda: True,
            )

    def test_local_media_means_no_download(self):
        _write_metadata(self.loras, "neon", images=["https://civitai.com/a.png"])
        (self.loras / "neon.mp4").write_bytes(b"video")

        def refuse(*args, **kwargs):
            raise AssertionError("a LoRA with local media downloads nothing")

        with patch.object(lora_utils, "download_civitai_images", refuse):
            staged = lora_import.stage(
                self.discover(), self.tmp, media_extensions=MEDIA
            )
        self.assertEqual(staged.skipped, [])
        self.assertEqual(
            staged.records[0]["images"], [os.path.realpath(self.loras / "neon.mp4")]
        )

    def test_a_lora_without_local_media_downloads(self):
        _write_metadata(self.loras, "neon", images=["https://civitai.com/a.png"])
        calls = []

        def download(metadata, path, cache_dir, api_key, stats=None):
            calls.append(path.name)
            return ["/cache/a.jpg"]

        with patch.object(lora_utils, "download_civitai_images", download):
            staged = lora_import.stage(
                self.discover(), self.tmp, media_extensions=MEDIA
            )
        self.assertEqual(calls, ["neon.metadata.json"])
        self.assertEqual(staged.records[0]["images"], ["/cache/a.jpg"])

    def test_alongside_records_match_the_previous_import(self):
        _write_metadata(
            self.loras,
            "neon",
            words=["glow", "glow"],
            images=["https://civitai.com/a.png"],
            prompt="neon street",
        )
        (self.loras / "neon.png").write_bytes(b"x")
        staged = lora_import.stage(self.discover(), self.tmp, media_extensions=MEDIA)
        self.assertEqual(
            staged.records,
            [
                {
                    "name": "neon",
                    "text": "neon street",
                    "hash": generate_prompt_hash("neon street"),
                    "tags": ["lora-manager", "lora:neon", "glow"],
                    "images": [os.path.realpath(self.loras / "neon.png")],
                }
            ],
        )

    def test_preview_counts_local_media_and_downloads(self):
        _write_metadata(self.loras, "local", images=["https://civitai.com/l.png"])
        (self.loras / "local.webp").write_bytes(b"x")
        _write_metadata(
            self.loras,
            "remote",
            images=["https://civitai.com/r1.png", "https://civitai.com/r2.png"],
        )
        (self.loras / "orphan.metadata.json").write_text("{}")
        found = lora_import.preview(str(self.lm), self.tmp / "cache", MEDIA)
        self.assertEqual(
            {
                k: found[k]
                for k in (
                    "lora_files",
                    "with_metadata",
                    "skipped",
                    "with_local_media",
                    "planned_downloads",
                )
            },
            {
                "lora_files": 3,
                "with_metadata": 2,
                "skipped": 1,
                "with_local_media": 1,
                "planned_downloads": 2,
            },
        )
        self.assertEqual(found["names"], ["local", "remote"])
        self.assertEqual(found["layout"]["mode"], "alongside")

    def test_preview_of_an_unusable_layout(self):
        (self.settings / "settings.json").write_text("nope")
        _write_metadata(self.loras, "one")
        found = lora_import.preview(str(self.lm), self.tmp / "cache", MEDIA)
        self.assertEqual(
            found["layout"]["error"], "the settings file is not valid JSON"
        )
        self.assertEqual((found["lora_files"], found["with_metadata"]), (0, 0))


MEDIA = frozenset({".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"})


if __name__ == "__main__":
    unittest.main()
