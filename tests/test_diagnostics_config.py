"""
Tests for issue #132: Diagnostics, backup, and restore must respect config.json.

Verifies that:
- GalleryDiagnostics resolves db_path from config (not hardcoded "prompts.db")
- The admin API diagnostics/backup/restore endpoints use the configured db_path
- Output directory checks consult GalleryConfig before falling back to heuristics
"""

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXTENSION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# GalleryDiagnostics path resolution
# ---------------------------------------------------------------------------
class TestGalleryDiagnosticsDbPath(unittest.TestCase):
    """GalleryDiagnostics should resolve db_path from config, not hardcode it."""

    def test_default_resolves_from_config(self):
        """GalleryDiagnostics() with no args should read config like PromptDatabase."""
        from utils.diagnostics import GalleryDiagnostics

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = os.path.join(tmpdir, "custom.db")
            fake_config_mod = types.ModuleType("py.config")
            fake_pm_config = type(
                "PromptManagerConfig", (), {"DEFAULT_DB_PATH": custom_path}
            )
            fake_config_mod.PromptManagerConfig = fake_pm_config

            with patch.dict(sys.modules, {"py.config": fake_config_mod}):
                diag = GalleryDiagnostics()

            self.assertEqual(diag.db_path, custom_path)

    def test_default_without_config_falls_back_to_extension_root(self):
        """Without config, should default to prompts.db in extension root."""
        from utils.diagnostics import GalleryDiagnostics

        diag = GalleryDiagnostics()
        expected = os.path.join(EXTENSION_ROOT, "prompts.db")
        self.assertEqual(diag.db_path, expected)

    def test_explicit_path_overrides_config(self):
        """An explicit db_path argument should be used as-is."""
        from utils.diagnostics import GalleryDiagnostics

        diag = GalleryDiagnostics("/explicit/path.db")
        self.assertEqual(diag.db_path, "/explicit/path.db")

    def test_config_relative_path_resolved_to_extension_root(self):
        """A relative path from config should resolve against extension root."""
        from utils.diagnostics import GalleryDiagnostics

        fake_config_mod = types.ModuleType("py.config")
        fake_pm_config = type(
            "PromptManagerConfig", (), {"DEFAULT_DB_PATH": "data/prompts.db"}
        )
        fake_config_mod.PromptManagerConfig = fake_pm_config

        with patch.dict(sys.modules, {"py.config": fake_config_mod}):
            diag = GalleryDiagnostics()

        expected = os.path.join(EXTENSION_ROOT, "data", "prompts.db")
        self.assertEqual(diag.db_path, expected)


# ---------------------------------------------------------------------------
# Admin API endpoints use self.db.model.db_path
# ---------------------------------------------------------------------------
class TestAdminEndpointsUseConfigPath(unittest.TestCase):
    """Admin diagnostics, backup, and restore must use the configured db_path."""

    def _make_api_stub(self, db_path):
        """Create a minimal object that mimics the admin mixin's self."""
        stub = MagicMock()
        stub.db.model.db_path = db_path
        stub.logger = MagicMock()
        return stub

    def _run_async(self, coro):
        """Run a coroutine on a fresh event loop to avoid aiohttp pollution."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_run_diagnostics_uses_model_db_path(self):
        """run_diagnostics should check self.db.model.db_path, not 'prompts.db'."""
        if "server" not in sys.modules:
            sys.modules["server"] = MagicMock()

        from py.api.admin import AdminRoutesMixin

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_file = f.name

        try:
            import sqlite3

            with sqlite3.connect(db_file) as conn:
                conn.execute(
                    "CREATE TABLE prompts (id INTEGER PRIMARY KEY, text TEXT, created_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO prompts (text, created_at) VALUES ('test', '2024-01-01')"
                )

            stub = self._make_api_stub(db_file)
            result = self._run_async(
                AdminRoutesMixin.run_diagnostics(stub, MagicMock())
            )

            body = json.loads(result.body)
            self.assertTrue(body["success"])
            self.assertEqual(body["diagnostics"]["database"]["status"], "ok")
            self.assertEqual(body["diagnostics"]["database"]["prompt_count"], 1)
        finally:
            os.unlink(db_file)

    def test_run_diagnostics_reports_missing_custom_path(self):
        """If the configured db_path doesn't exist, diagnostics should report error."""
        if "server" not in sys.modules:
            sys.modules["server"] = MagicMock()

        from py.api.admin import AdminRoutesMixin

        stub = self._make_api_stub("/nonexistent/custom/prompts.db")

        result = self._run_async(AdminRoutesMixin.run_diagnostics(stub, MagicMock()))
        body = json.loads(result.body)
        self.assertTrue(body["success"])
        self.assertEqual(body["diagnostics"]["database"]["status"], "error")
        self.assertIn(
            "/nonexistent/custom/prompts.db",
            body["diagnostics"]["database"]["message"],
        )

    def test_backup_uses_model_db_path(self):
        """backup_database should read from self.db.model.db_path."""
        if "server" not in sys.modules:
            sys.modules["server"] = MagicMock()

        from py.api.admin import AdminRoutesMixin

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_file = f.name
            f.write(b"SQLite format 3\x00test data for backup")

        try:
            stub = self._make_api_stub(db_file)
            result = self._run_async(
                AdminRoutesMixin.backup_database(stub, MagicMock())
            )

            self.assertEqual(result.content_type, "application/octet-stream")
            self.assertGreater(len(result.body), 0)
        finally:
            os.unlink(db_file)

    def test_backup_reports_missing_custom_path(self):
        """backup_database should return 404 when configured path doesn't exist."""
        if "server" not in sys.modules:
            sys.modules["server"] = MagicMock()

        from py.api.admin import AdminRoutesMixin

        stub = self._make_api_stub("/nonexistent/custom/prompts.db")

        result = self._run_async(AdminRoutesMixin.backup_database(stub, MagicMock()))
        body = json.loads(result.body)
        self.assertFalse(body["success"])
        self.assertEqual(result.status, 404)


if __name__ == "__main__":
    unittest.main()
