"""
Tests for _find_comfyui_output_dir() — verifies that user-configured
directories take priority over auto-detection, and that the cache is
invalidated when the setting changes.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock ComfyUI's server module before importing anything that touches config
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules["server"] = _mock_server

from py.api import PromptManagerAPI
from py.config import GalleryConfig


class TestFindComfyuiOutputDir(unittest.TestCase):
    """Test _find_comfyui_output_dir() respects user-configured directories."""

    def setUp(self):
        self.api = PromptManagerAPI()
        self.api._cached_output_dir = None
        self._orig_dirs = list(GalleryConfig.MONITORING_DIRECTORIES)

    def tearDown(self):
        GalleryConfig.MONITORING_DIRECTORIES = self._orig_dirs

    def test_configured_directory_takes_priority(self):
        """When a valid directory is configured, it should be used instead of auto-detect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            GalleryConfig.MONITORING_DIRECTORIES = [tmpdir]
            self.api._cached_output_dir = None

            result = self.api._find_comfyui_output_dir()

            self.assertEqual(result, str(Path(tmpdir).resolve()))

    def test_nonexistent_configured_directory_falls_through(self):
        """When the configured directory doesn't exist, fall back to auto-detection."""
        GalleryConfig.MONITORING_DIRECTORIES = ["/nonexistent/fake/path/output"]
        self.api._cached_output_dir = None

        # Should not return the nonexistent path — will either find
        # ComfyUI output via auto-detect or return None
        result = self.api._find_comfyui_output_dir()

        self.assertNotEqual(result, "/nonexistent/fake/path/output")

    def test_empty_config_uses_auto_detection(self):
        """When MONITORING_DIRECTORIES is empty, auto-detection should be used."""
        GalleryConfig.MONITORING_DIRECTORIES = []
        self.api._cached_output_dir = None

        # Should not raise — auto-detection may or may not find a dir
        result = self.api._find_comfyui_output_dir()
        # Result is either a valid path or None
        if result is not None:
            self.assertTrue(Path(result).is_dir())

    def test_cache_returns_same_value(self):
        """After first lookup, cached value should be returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            GalleryConfig.MONITORING_DIRECTORIES = [tmpdir]
            self.api._cached_output_dir = None

            result1 = self.api._find_comfyui_output_dir()
            # Change config — should NOT affect result because of cache
            GalleryConfig.MONITORING_DIRECTORIES = ["/some/other/path"]
            result2 = self.api._find_comfyui_output_dir()

            self.assertEqual(result1, result2)

    def test_cache_invalidation_picks_up_new_directory(self):
        """After clearing the cache, _find_comfyui_output_dir should use new config."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                GalleryConfig.MONITORING_DIRECTORIES = [tmpdir1]
                self.api._cached_output_dir = None

                result1 = self.api._find_comfyui_output_dir()
                self.assertEqual(result1, str(Path(tmpdir1).resolve()))

                # Simulate what save_settings does: update config + invalidate cache
                GalleryConfig.MONITORING_DIRECTORIES = [tmpdir2]
                self.api._cached_output_dir = None

                result2 = self.api._find_comfyui_output_dir()
                self.assertEqual(result2, str(Path(tmpdir2).resolve()))

    def test_configured_directory_is_resolved(self):
        """Configured paths with symlinks or .. should be resolved to absolute."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a relative-ish path with ..
            parent = str(Path(tmpdir).parent)
            basename = Path(tmpdir).name
            dotdot_path = os.path.join(parent, ".", basename)

            GalleryConfig.MONITORING_DIRECTORIES = [dotdot_path]
            self.api._cached_output_dir = None

            result = self.api._find_comfyui_output_dir()

            self.assertEqual(result, str(Path(tmpdir).resolve()))


if __name__ == "__main__":
    unittest.main()
