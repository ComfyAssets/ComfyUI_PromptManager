"""Maintenance routes (py/api/maintenance.py, #249 System S6).

``POST /prompt_manager/maintenance/preview`` answers a reviewed ``Plan`` for
one of four operations and writes nothing; its token binds a later apply to
exactly that plan. Errors are coded and never carry a server path or an
exception text.
"""

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from py.api.maintenance_plans import PlanExpired  # noqa: E402
from py.config import GalleryConfig  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

WAIT = 10
PREVIEW = "/prompt_manager/maintenance/preview"
OPERATIONS = ("duplicate_prompts", "optimize", "unused_components", "missing_links")
PLAN_KEYS = {
    "success",
    "token",
    "operation",
    "expires_at",
    "scope",
    "protected",
    "backup",
    "media",
    "details",
}


def _digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class MaintenanceAPITestCase(AdminAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.api._find_comfyui_output_dir = lambda: (
            str(self.output_dir) if self.output_dir.is_dir() else None
        )
        return app

    def save(self, text, **metadata):
        return self.api.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def image(self, prompt_id, name, *, exists=True, folder=None):
        path = Path(folder or self.output_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            path.write_bytes(b"png")
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    async def post(self, url, body, status):
        response = await self.client.request("POST", url, json=body)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        self.assertNotIn(self.tmpdir, text)
        return json.loads(text)

    async def preview(self, operation, status=200, **body):
        return await self.post(PREVIEW, {"operation": operation, **body}, status)


class TestPreviewRoute(MaintenanceAPITestCase):
    async def test_preview_answers_a_plan_for_each_operation(self):
        prompt = self.save("Kept")
        self.image(prompt, "gone.png", exists=False)
        self.save("Unused")
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                plan = await self.preview(operation, options={})
                self.assertEqual(set(plan), PLAN_KEYS)
                self.assertTrue(plan["success"])
                self.assertEqual(plan["operation"], operation)
                self.assertIsInstance(plan["token"], str)
                self.assertEqual(set(plan["scope"]), {"count", "label"})
                self.assertEqual(set(plan["protected"]), {"count", "reasons"})
                self.assertEqual(
                    plan["backup"],
                    {
                        "policy": "on",
                        "destination_label": "prompts.db.backup_<date and time>",
                    },
                )
                self.assertFalse(plan["media"]["deletes_files"])
                self.assertLessEqual(len(plan["details"]), 50)
                self.assertTrue(plan["expires_at"].endswith("+00:00"))
                self.assertNotIn("fingerprint", json.dumps(plan))
        missing = await self.preview("missing_links")
        self.assertEqual(
            missing["scope"], {"count": 1, "label": "1 link to a missing file"}
        )
        unused = await self.preview("unused_components")
        self.assertEqual(unused["scope"]["count"], 1)

    async def test_preview_writes_nothing(self):
        prompt = self.save("Kept")
        self.image(prompt, "kept.png")
        self.image(prompt, "gone.png", exists=False)
        self.save("Unused")
        db_path = self.api.db.model.db_path
        self.api.db.get_statistics()
        files = [db_path, db_path + "-wal"]
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                before = [_digest(path) for path in files]
                shm = os.path.getsize(db_path + "-shm")
                media = {p.name: _digest(p) for p in self.output_dir.iterdir()}
                await self.preview(operation)
                self.assertEqual([_digest(path) for path in files], before)
                self.assertEqual(os.path.getsize(db_path + "-shm"), shm)
                self.assertEqual(
                    {p.name: _digest(p) for p in self.output_dir.iterdir()}, media
                )
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["Unused", "Kept"]
        )

    async def test_unknown_operation_is_400(self):
        cases = [
            {"operation": "reboot"},
            {"operation": "duplicate_files"},  # S6 Task 17, not yet
            {"operation": 3},
            {},
            {"operation": "optimize", "options": {"all": True}},
            {"operation": "optimize", "options": []},
            {"operation": "optimize", "extra": 1},
        ]
        for body in cases:
            with self.subTest(body=body):
                reply = await self.post(PREVIEW, body, 400)
                self.assertEqual(reply["success"], False)
                self.assertEqual(reply["code"], "invalid-request")
        response = await self.client.request(
            "POST", PREVIEW, data=b"[1]", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "invalid-request")
        big = json.dumps({"operation": "optimize", "pad": "x" * 8192})
        response = await self.client.request(
            "POST", PREVIEW, data=big, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 413)
        self.assertEqual((await response.json())["code"], "too-large")

    async def test_preview_failure_is_coded_without_exception_text(self):
        with patch.object(
            self.api.db,
            "preview_maintenance",
            side_effect=sqlite3.OperationalError(f"boom {self.tmpdir}"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                reply = await self.preview("optimize", status=500)
        self.assertEqual(
            reply,
            {"success": False, "code": None, "error": "Failed to review maintenance"},
        )

    async def test_scope_too_large_is_413(self):
        from py.api.maintenance_plans import PlanStore

        self.api._maintenance_plans = PlanStore(max_bytes=10)
        self.save("Unused")
        reply = await self.preview("unused_components", status=413)
        self.assertEqual(reply["code"], "too-large")

    async def test_preview_during_a_restore_swap_is_storage_changed(self):
        real = self.api.db.preview_maintenance

        def swapped(*args, **kwargs):
            draft = real(*args, **kwargs)
            self.api._db_generation += 1  # a restore replaced the database
            return draft

        with patch.object(self.api.db, "preview_maintenance", side_effect=swapped):
            reply = await self.preview("optimize", status=409)
        self.assertEqual(reply["code"], "storage-changed")

    async def test_missing_links_preview_uses_the_health_roots(self):
        unmounted = Path(self.tmpdir) / "unmounted"
        unmounted.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir), str(unmounted)]
        prompt = self.save("Links")
        self.image(prompt, "gone.png", exists=False)
        self.image(prompt, "away.png", folder=unmounted, exists=False)
        unmounted.rmdir()
        plan = await self.preview("missing_links")
        self.assertEqual(plan["scope"]["count"], 1)
        self.assertEqual(
            plan["protected"],
            {"count": 1, "reasons": ["1 link under an unavailable source"]},
        )

    async def test_restart_loses_plans(self):
        plan = await self.preview("optimize")
        self.assertEqual(
            self.api._maintenance_plans.peek(plan["token"], "optimize").operation,
            "optimize",
        )
        restarted = PromptManagerAPI(db=PromptDatabase(self.api.db.model.db_path))
        try:
            with self.assertRaises(PlanExpired):
                restarted._maintenance_plans.peek(plan["token"], "optimize")
        finally:
            restarted.db.close_all()


class TestLegacyMaintenance(MaintenanceAPITestCase):
    async def test_vacuum_failure_reported_not_success(self):
        with patch(
            "database.models.sqlite3.connect",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                reply = await self.post(
                    "/prompt_manager/maintenance", {"operations": ["vacuum"]}, 200
                )
        self.assertFalse(reply["all_successful"])
        self.assertFalse(reply["results"]["vacuum"]["success"])
        self.assertEqual(reply["results"]["vacuum"]["error"], "database is locked")

    async def test_legacy_orphaned_image_cleanup_skips_unavailable_sources(self):
        unmounted = Path(self.tmpdir) / "unmounted"
        unmounted.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir), str(unmounted)]
        prompt = self.save("Links")
        self.image(prompt, "gone.png", exists=False)
        away = self.image(prompt, "away.png", folder=unmounted, exists=False)
        unmounted.rmdir()
        reply = await self.post(
            "/prompt_manager/maintenance",
            {"operations": ["cleanup_orphaned_images"]},
            200,
        )
        self.assertEqual(
            reply["results"]["cleanup_orphaned_images"],
            {
                "success": True,
                "removed_count": 1,
                "message": "Removed 1 orphaned image records",
            },
        )
        self.assertIsNotNone(self.api.db.get_image_by_id(away))


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
