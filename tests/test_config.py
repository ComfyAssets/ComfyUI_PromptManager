"""
Tests for GalleryConfig and PromptManagerConfig.

The config module imports `from server import PromptServer` at module level,
which is ComfyUI's server. We mock it before import to run tests standalone.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# Mock ComfyUI's server module before importing config
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules["server"] = _mock_server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.config import GalleryConfig, PromptManagerConfig


class TestGalleryConfig(unittest.TestCase):
    """Test GalleryConfig class."""

    def setUp(self):
        """Save original values to restore after each test."""
        self._orig = GalleryConfig.get_config()

    def tearDown(self):
        """Restore original config values."""
        GalleryConfig.update_config(self._orig)

    def test_get_config_structure(self):
        config = GalleryConfig.get_config()
        self.assertIn("monitoring", config)
        self.assertIn("tracking", config)
        self.assertIn("database", config)
        self.assertIn("web_interface", config)
        self.assertIn("performance", config)

    def test_default_values(self):
        config = GalleryConfig.get_config()
        self.assertTrue(config["monitoring"]["enabled"])
        self.assertEqual(
            config["monitoring"]["extensions"],
            [".png", ".jpg", ".jpeg", ".webp", ".gif"],
        )
        self.assertEqual(config["tracking"]["prompt_timeout"], 600)
        self.assertEqual(config["web_interface"]["images_per_page"], 20)

    def test_update_monitoring(self):
        GalleryConfig.update_config(
            {"monitoring": {"enabled": False, "processing_delay": 5.0}}
        )
        self.assertFalse(GalleryConfig.MONITORING_ENABLED)
        self.assertEqual(GalleryConfig.PROCESSING_DELAY, 5.0)

    def test_update_tracking(self):
        GalleryConfig.update_config({"tracking": {"prompt_timeout": 300}})
        self.assertEqual(GalleryConfig.PROMPT_TIMEOUT, 300)

    def test_update_database(self):
        GalleryConfig.update_config(
            {"database": {"auto_cleanup": False, "max_image_age_days": 30}}
        )
        self.assertFalse(GalleryConfig.AUTO_CLEANUP_MISSING_FILES)
        self.assertEqual(GalleryConfig.MAX_IMAGE_AGE_DAYS, 30)

    def test_update_web_interface(self):
        GalleryConfig.update_config(
            {"web_interface": {"images_per_page": 50, "thumbnail_size": 512}}
        )
        self.assertEqual(GalleryConfig.IMAGES_PER_PAGE, 50)
        self.assertEqual(GalleryConfig.THUMBNAIL_SIZE, 512)

    def test_update_performance(self):
        GalleryConfig.update_config({"performance": {"max_concurrent_processing": 5}})
        self.assertEqual(GalleryConfig.MAX_CONCURRENT_PROCESSING, 5)

    def test_partial_update_preserves_other_values(self):
        original_timeout = GalleryConfig.PROMPT_TIMEOUT
        GalleryConfig.update_config({"monitoring": {"enabled": False}})
        self.assertEqual(GalleryConfig.PROMPT_TIMEOUT, original_timeout)

    def test_empty_update_changes_nothing(self):
        before = GalleryConfig.get_config()
        GalleryConfig.update_config({})
        after = GalleryConfig.get_config()
        self.assertEqual(before, after)


class TestPromptManagerConfig(unittest.TestCase):
    """Test PromptManagerConfig class."""

    def setUp(self):
        self._orig = PromptManagerConfig.get_config()

    def tearDown(self):
        PromptManagerConfig.update_config(self._orig)

    def test_get_config_structure(self):
        config = PromptManagerConfig.get_config()
        self.assertIn("database", config)
        self.assertIn("web_ui", config)
        self.assertIn("performance", config)
        self.assertIn("gallery", config)

    def test_gallery_config_nested(self):
        config = PromptManagerConfig.get_config()
        # Gallery section should match GalleryConfig output
        self.assertIn("monitoring", config["gallery"])
        self.assertIn("tracking", config["gallery"])

    def test_default_database_values(self):
        config = PromptManagerConfig.get_config()
        self.assertEqual(config["database"]["default_path"], "prompts.db")
        self.assertTrue(config["database"]["enable_duplicate_detection"])
        self.assertTrue(config["database"]["enable_auto_save"])

    def test_default_web_ui_values(self):
        config = PromptManagerConfig.get_config()
        self.assertEqual(config["web_ui"]["webui_display_mode"], "newtab")

    def test_update_database(self):
        PromptManagerConfig.update_config({"database": {"default_path": "custom.db"}})
        self.assertEqual(PromptManagerConfig.DEFAULT_DB_PATH, "custom.db")

    def test_update_web_ui(self):
        PromptManagerConfig.update_config({"web_ui": {"webui_display_mode": "popup"}})
        self.assertEqual(PromptManagerConfig.WEBUI_DISPLAY_MODE, "popup")

    def test_update_performance(self):
        PromptManagerConfig.update_config({"performance": {"max_search_results": 50}})
        self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, 50)

    def test_update_propagates_to_gallery(self):
        PromptManagerConfig.update_config(
            {"gallery": {"monitoring": {"enabled": False}}}
        )
        self.assertFalse(GalleryConfig.MONITORING_ENABLED)
        # Restore
        GalleryConfig.MONITORING_ENABLED = True

    def test_save_and_load_file(self):
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", dir=tempfile.gettempdir()
        )
        tmp.close()
        try:
            # Modify a value
            PromptManagerConfig.MAX_SEARCH_RESULTS = 42
            PromptManagerConfig.save_to_file(tmp.name)

            # Reset and reload
            PromptManagerConfig.MAX_SEARCH_RESULTS = 100
            PromptManagerConfig.load_from_file(tmp.name)
            self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, 42)
        finally:
            os.unlink(tmp.name)

    def test_load_nonexistent_file_uses_defaults(self):
        # Should not raise, just log and continue
        original = PromptManagerConfig.MAX_SEARCH_RESULTS
        PromptManagerConfig.load_from_file("/nonexistent/path/config.json")
        self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, original)

    def test_load_invalid_json_file(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        tmp.write("not valid json {{")
        tmp.close()
        try:
            original = PromptManagerConfig.MAX_SEARCH_RESULTS
            PromptManagerConfig.load_from_file(tmp.name)
            # Should not crash, keeps existing values
            self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, original)
        finally:
            os.unlink(tmp.name)

    def test_save_creates_directory(self):
        tmp_dir = tempfile.mkdtemp()
        nested_path = os.path.join(tmp_dir, "subdir", "config.json")
        try:
            PromptManagerConfig.save_to_file(nested_path)
            self.assertTrue(os.path.exists(nested_path))
            with open(nested_path) as f:
                data = json.load(f)
            self.assertIn("database", data)
        finally:
            import shutil

            shutil.rmtree(tmp_dir)

    def test_saved_file_is_valid_json(self):
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", dir=tempfile.gettempdir()
        )
        tmp.close()
        try:
            PromptManagerConfig.save_to_file(tmp.name)
            with open(tmp.name) as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
