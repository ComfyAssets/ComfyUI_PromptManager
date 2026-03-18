"""
Tests for issue #119: Database path configuration.

Verifies that _resolve_db_path() correctly reads from config, resolves
relative paths against the extension root, and handles absolute paths.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import _resolve_db_path

EXTENSION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestResolveDbPath(unittest.TestCase):
    """Test _resolve_db_path() database path resolution."""

    def test_explicit_absolute_path_used_as_is(self):
        """An explicit absolute path should be returned unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "custom.db")
            result = _resolve_db_path(path)
            self.assertEqual(result, path)

    def test_explicit_relative_path_resolved_to_extension_root(self):
        """An explicit relative path should resolve against the extension root."""
        result = _resolve_db_path("data/my.db")
        expected = os.path.join(EXTENSION_ROOT, "data", "my.db")
        self.assertEqual(result, expected)

    def test_none_falls_back_to_default_without_config(self):
        """Without config (outside ComfyUI), should default to prompts.db."""
        # py.config requires ComfyUI's server module, so import fails naturally
        result = _resolve_db_path(None)
        expected = os.path.join(EXTENSION_ROOT, "prompts.db")
        self.assertEqual(result, expected)

    def test_none_reads_config_when_available(self):
        """None should read DEFAULT_DB_PATH from config when importable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = os.path.join(tmpdir, "prompts.db")
            fake_config_mod = types.ModuleType("py.config")
            fake_pm_config = type(
                "PromptManagerConfig", (), {"DEFAULT_DB_PATH": custom_path}
            )
            fake_config_mod.PromptManagerConfig = fake_pm_config

            with patch.dict(sys.modules, {"py.config": fake_config_mod}):
                result = _resolve_db_path(None)

            self.assertEqual(result, custom_path)

    def test_config_relative_path_resolved_to_extension_root(self):
        """A relative path from config should resolve against extension root."""
        fake_config_mod = types.ModuleType("py.config")
        fake_pm_config = type(
            "PromptManagerConfig", (), {"DEFAULT_DB_PATH": "custom_data/prompts.db"}
        )
        fake_config_mod.PromptManagerConfig = fake_pm_config

        with patch.dict(sys.modules, {"py.config": fake_config_mod}):
            result = _resolve_db_path(None)

        expected = os.path.join(EXTENSION_ROOT, "custom_data", "prompts.db")
        self.assertEqual(result, expected)

    def test_result_is_always_absolute(self):
        """Result should always be an absolute path."""
        result = _resolve_db_path("prompts.db")
        self.assertTrue(os.path.isabs(result))

    def test_parent_directory_created(self):
        """Parent directories should be created for custom paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "deep", "nested", "prompts.db")
            result = _resolve_db_path(nested)
            self.assertEqual(result, nested)
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "deep", "nested")))


class TestPromptDatabaseUsesConfig(unittest.TestCase):
    """Test that PromptDatabase() picks up the resolved path."""

    @patch("database.operations._resolve_db_path")
    def test_constructor_calls_resolve_with_none(self, mock_resolve):
        """PromptDatabase() should call _resolve_db_path with None."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            mock_resolve.return_value = f.name

        try:
            from database.operations import PromptDatabase

            db = PromptDatabase()
            mock_resolve.assert_called_once_with(None)
        finally:
            os.unlink(f.name)

    @patch("database.operations._resolve_db_path")
    def test_constructor_passes_explicit_path(self, mock_resolve):
        """PromptDatabase(path) should call _resolve_db_path with that path."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            mock_resolve.return_value = f.name

        try:
            from database.operations import PromptDatabase

            db = PromptDatabase("/explicit/path.db")
            mock_resolve.assert_called_once_with("/explicit/path.db")
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
