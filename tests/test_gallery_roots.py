"""Tests for gallery root validation (GalleryConfig.validate_gallery_root).

A gallery root may only point inside one of ComfyUI's own directories
(output/input/temp/user, as reported by ``folder_paths``) or inside a
directory listed in ``PROMPT_MANAGER_EXTRA_GALLERY_ROOTS``. Anything else,
including filesystem roots and the home directory, is rejected so the
image-serving routes can never be pointed at arbitrary files.
"""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402

EXTRA_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"


class TestUpdateConfigValidatesRoots(unittest.TestCase):
    """Roots hand-edited into config.json go through validate_gallery_root."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir(parents=True)
        self.outside = Path(self.tmpdir) / "outside"
        self.outside.mkdir()
        self._orig_folder_paths = sys.modules.get("folder_paths")
        sys.modules["folder_paths"] = types.SimpleNamespace(
            base_path=str(self.comfy_dir),
            get_output_directory=lambda: str(self.output_dir),
        )
        self.addCleanup(self._restore_folder_paths)
        self._orig_dirs = list(GalleryConfig.MONITORING_DIRECTORIES)
        self.addCleanup(
            setattr, GalleryConfig, "MONITORING_DIRECTORIES", self._orig_dirs
        )
        self._orig_extra = os.environ.pop(EXTRA_ROOTS_ENV, None)
        self.addCleanup(self._restore_extra_env)

    def _restore_folder_paths(self):
        if self._orig_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self._orig_folder_paths

    def _restore_extra_env(self):
        if self._orig_extra is None:
            os.environ.pop(EXTRA_ROOTS_ENV, None)
        else:
            os.environ[EXTRA_ROOTS_ENV] = self._orig_extra

    def test_invalid_roots_are_dropped_with_a_warning(self):
        good = self.output_dir / "renders"
        good.mkdir()
        with self.assertLogs("prompt_manager.config", level="WARNING") as logs:
            GalleryConfig.update_config(
                {
                    "monitoring": {
                        "directories": [
                            str(self.outside),
                            str(good),
                            os.path.abspath(os.sep),
                            42,
                        ]
                    }
                }
            )
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(good)])
        self.assertEqual(len(logs.output), 3)
        self.assertNotIn(str(self.outside), "".join(logs.output))

    def test_non_list_directories_are_ignored(self):
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]
        GalleryConfig.update_config({"monitoring": {"directories": "/etc"}})
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(self.output_dir)])

    def test_load_from_file_drops_invalid_roots(self):
        config_path = os.path.join(self.tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump(
                {
                    "gallery": {
                        "monitoring": {
                            "directories": [str(self.outside), str(self.output_dir)]
                        }
                    }
                },
                f,
            )
        PromptManagerConfig.load_from_file(config_path)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [str(self.output_dir)])


class GalleryRootTestCase(unittest.TestCase):
    """Shared fixture: a fake ComfyUI tree exposed through a stub folder_paths."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._rmtree, self.tmpdir)

        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.input_dir = self.comfy_dir / "input"
        self.output_dir.mkdir(parents=True)
        self.input_dir.mkdir(parents=True)

        self._orig_folder_paths = sys.modules.get("folder_paths")
        sys.modules["folder_paths"] = types.SimpleNamespace(
            base_path=str(self.comfy_dir),
            get_output_directory=lambda: str(self.output_dir),
            get_input_directory=lambda: str(self.input_dir),
            get_temp_directory=self._raise_runtime_error,
            # get_user_directory intentionally absent
        )
        self.addCleanup(self._restore_folder_paths)

        self._orig_extra = os.environ.pop(EXTRA_ROOTS_ENV, None)
        self.addCleanup(self._restore_extra_env)

    @staticmethod
    def _raise_runtime_error():
        raise RuntimeError("temp directory unavailable")

    @staticmethod
    def _rmtree(path):
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def _restore_folder_paths(self):
        if self._orig_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self._orig_folder_paths

    def _restore_extra_env(self):
        if self._orig_extra is None:
            os.environ.pop(EXTRA_ROOTS_ENV, None)
        else:
            os.environ[EXTRA_ROOTS_ENV] = self._orig_extra


class TestValidateGalleryRootRejects(GalleryRootTestCase):

    def test_filesystem_root_rejected(self):
        ok, reason = GalleryConfig.validate_gallery_root(os.path.abspath(os.sep))
        self.assertFalse(ok)
        self.assertIn("root", reason.lower())

    @unittest.skipUnless(os.name == "nt", "drive roots only exist on Windows")
    def test_windows_drive_root_rejected(self):
        ok, _ = GalleryConfig.validate_gallery_root("C:\\")
        self.assertFalse(ok)

    def test_home_directory_rejected(self):
        ok, reason = GalleryConfig.validate_gallery_root(os.path.expanduser("~"))
        self.assertFalse(ok)
        self.assertIn("home", reason.lower())

    def test_directory_outside_comfyui_rejected(self):
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        ok, _ = GalleryConfig.validate_gallery_root(str(outside))
        self.assertFalse(ok)

    def test_nonexistent_path_rejected(self):
        missing = self.output_dir / "does_not_exist"
        ok, reason = GalleryConfig.validate_gallery_root(str(missing))
        self.assertFalse(ok)
        self.assertIn("exist", reason.lower())

    def test_file_rejected(self):
        file_path = self.output_dir / "image.png"
        file_path.write_bytes(b"PNG")
        ok, reason = GalleryConfig.validate_gallery_root(str(file_path))
        self.assertFalse(ok)
        self.assertIn("directory", reason.lower())

    def test_empty_and_non_string_rejected(self):
        self.assertFalse(GalleryConfig.validate_gallery_root("")[0])
        self.assertFalse(GalleryConfig.validate_gallery_root("   ")[0])
        self.assertFalse(GalleryConfig.validate_gallery_root(None)[0])
        self.assertFalse(GalleryConfig.validate_gallery_root(42)[0])

    def test_sibling_with_shared_prefix_rejected(self):
        """'output2' must not pass because it string-starts with 'output'."""
        sibling = self.comfy_dir / "output2"
        sibling.mkdir()
        ok, _ = GalleryConfig.validate_gallery_root(str(sibling))
        self.assertFalse(ok)

    def test_traversal_out_of_output_rejected(self):
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        sneaky = os.path.join(str(self.output_dir), "..", "..", "elsewhere")
        ok, _ = GalleryConfig.validate_gallery_root(sneaky)
        self.assertFalse(ok)

    def test_symlink_escaping_output_rejected(self):
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        link = self.output_dir / "escape"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available on this platform")
        ok, _ = GalleryConfig.validate_gallery_root(str(link))
        self.assertFalse(ok)

    def test_env_root_that_is_filesystem_root_is_ignored(self):
        """Listing '/' in the env var must not re-open the whole disk."""
        os.environ[EXTRA_ROOTS_ENV] = os.path.abspath(os.sep)
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        ok, _ = GalleryConfig.validate_gallery_root(str(outside))
        self.assertFalse(ok)


class TestValidateGalleryRootAccepts(GalleryRootTestCase):

    def test_output_directory_itself_accepted(self):
        ok, reason = GalleryConfig.validate_gallery_root(str(self.output_dir))
        self.assertTrue(ok, reason)

    def test_subdirectory_of_output_accepted(self):
        sub = self.output_dir / "renders" / "2026"
        sub.mkdir(parents=True)
        ok, reason = GalleryConfig.validate_gallery_root(str(sub))
        self.assertTrue(ok, reason)

    def test_subdirectory_of_input_accepted(self):
        sub = self.input_dir / "refs"
        sub.mkdir()
        ok, reason = GalleryConfig.validate_gallery_root(str(sub))
        self.assertTrue(ok, reason)

    def test_directory_listed_in_env_accepted(self):
        extra_a = Path(self.tmpdir) / "extra_a"
        extra_b = Path(self.tmpdir) / "extra_b"
        extra_a.mkdir()
        extra_b.mkdir()
        os.environ[EXTRA_ROOTS_ENV] = os.pathsep.join([str(extra_a), str(extra_b)])

        ok_a, reason_a = GalleryConfig.validate_gallery_root(str(extra_a))
        ok_b, reason_b = GalleryConfig.validate_gallery_root(str(extra_b / "."))
        self.assertTrue(ok_a, reason_a)
        self.assertTrue(ok_b, reason_b)

    def test_relative_path_resolves_against_comfyui_base_not_cwd(self):
        sub = self.output_dir / "rel"
        sub.mkdir()
        orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        self.addCleanup(os.chdir, orig_cwd)

        ok, reason = GalleryConfig.validate_gallery_root("output/rel")
        self.assertTrue(ok, reason)
        self.assertEqual(
            GalleryConfig.resolve_gallery_root("output/rel"),
            os.path.normcase(os.path.realpath(sub)),
        )
        # A path that only exists relative to the CWD is not accepted.
        (Path(self.tmpdir) / "rel").mkdir()
        self.assertFalse(GalleryConfig.validate_gallery_root("rel")[0])

    def test_accepts_without_folder_paths_when_env_lists_dir(self):
        sys.modules.pop("folder_paths", None)
        extra = Path(self.tmpdir) / "extra"
        extra.mkdir()
        os.environ[EXTRA_ROOTS_ENV] = str(extra)
        ok, reason = GalleryConfig.validate_gallery_root(str(extra))
        self.assertTrue(ok, reason)

    def test_rejects_everything_without_folder_paths_and_env(self):
        sys.modules.pop("folder_paths", None)
        ok, _ = GalleryConfig.validate_gallery_root(str(self.output_dir))
        self.assertFalse(ok)


class TestGetAllOutputDirsFiltersRoots(GalleryRootTestCase):
    """PromptManagerAPI._get_all_output_dirs() only returns validated roots."""

    def setUp(self):
        super().setUp()
        from py.api import PromptManagerAPI

        self.api = PromptManagerAPI()
        self.api._cached_output_dir = None
        self._orig_dirs = list(GalleryConfig.MONITORING_DIRECTORIES)
        self.addCleanup(self._restore_dirs)

    def _restore_dirs(self):
        GalleryConfig.MONITORING_DIRECTORIES = self._orig_dirs

    def _dirs(self):
        return [os.path.normcase(str(p)) for p in self.api._get_all_output_dirs()]

    def test_filesystem_root_is_never_served(self):
        GalleryConfig.MONITORING_DIRECTORIES = [os.path.abspath(os.sep)]
        dirs = self._dirs()
        self.assertNotIn(os.path.normcase(os.path.abspath(os.sep)), dirs)
        self.assertEqual(dirs, [os.path.normcase(os.path.realpath(self.output_dir))])

    def test_outside_directory_is_dropped_and_valid_one_kept(self):
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        good = self.output_dir / "good"
        good.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(outside), str(good)]
        self.assertEqual(self._dirs(), [os.path.normcase(os.path.realpath(good))])

    def test_all_rejected_falls_back_to_comfyui_output_only(self):
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(outside)]
        self.assertEqual(
            self._dirs(), [os.path.normcase(os.path.realpath(self.output_dir))]
        )

    def test_no_configured_dirs_uses_auto_detection(self):
        GalleryConfig.MONITORING_DIRECTORIES = []
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)
        self.assertEqual(self._dirs(), [os.path.normcase(str(self.output_dir))])

    def test_nothing_available_returns_empty_list(self):
        sys.modules.pop("folder_paths", None)
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(outside)]
        self.assertEqual(self._dirs(), [])


if __name__ == "__main__":
    unittest.main()
