"""Tests for the core API module (py/api/__init__.py): helpers and base routes."""

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
sys.modules["server"] = _mock_server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import _public_error, _public_path  # noqa: E402

EXTRA_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"


class FolderPathsFixture(unittest.TestCase):
    """Temp ComfyUI tree exposed through a stub folder_paths module."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.comfy_dir = Path(self.tmpdir) / "ComfyUI"
        self.output_dir = self.comfy_dir / "output"
        self.output_dir.mkdir(parents=True)

        self._orig_folder_paths = sys.modules.get("folder_paths")
        self.addCleanup(self._restore_folder_paths)
        self._orig_extra = os.environ.pop(EXTRA_ROOTS_ENV, None)
        self.addCleanup(self._restore_extra_env)

        self.install_folder_paths(base_path=str(self.comfy_dir))

    def install_folder_paths(self, **attrs):
        attrs.setdefault("get_output_directory", lambda: str(self.output_dir))
        sys.modules["folder_paths"] = types.SimpleNamespace(**attrs)

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


class TestPublicPath(FolderPathsFixture):

    def test_path_under_base_is_relative_posix(self):
        target = self.output_dir / "renders" / "a.png"
        self.assertEqual(_public_path(str(target)), "output/renders/a.png")

    def test_base_itself_is_dot(self):
        self.assertEqual(_public_path(str(self.comfy_dir)), ".")

    def test_path_outside_base_is_basename_only(self):
        secret = Path(self.tmpdir) / "secret" / "passwords.txt"
        secret.parent.mkdir()
        secret.write_text("x")
        self.assertEqual(_public_path(str(secret)), "passwords.txt")
        self.assertNotIn(self.tmpdir, _public_path(str(secret)))

    def test_path_object_accepted(self):
        self.assertEqual(_public_path(self.output_dir), "output")

    def test_falls_back_to_output_parent_without_base_path(self):
        self.install_folder_paths()  # no base_path attribute
        self.assertEqual(_public_path(str(self.output_dir / "x.png")), "output/x.png")

    def test_env_extra_root_keeps_its_own_name(self):
        extra = Path(self.tmpdir) / "nas" / "gallery"
        (extra / "sub").mkdir(parents=True)
        os.environ[EXTRA_ROOTS_ENV] = str(extra)
        self.assertEqual(_public_path(str(extra / "sub")), "gallery/sub")

    def test_without_folder_paths_returns_basename(self):
        sys.modules.pop("folder_paths", None)
        self.assertEqual(_public_path(str(self.output_dir / "img.png")), "img.png")

    def test_empty_or_none_gives_empty_string(self):
        self.assertEqual(_public_path(""), "")
        self.assertEqual(_public_path(None), "")


class TestPublicError(FolderPathsFixture):

    def test_oserror_uses_strerror_and_basename(self):
        err = FileNotFoundError(
            2, "No such file or directory", str(self.tmpdir) + "/a/b.png"
        )
        text = _public_error(err)
        self.assertIn("No such file or directory", text)
        self.assertIn("b.png", text)
        self.assertNotIn(self.tmpdir, text)

    def test_oserror_without_filename(self):
        err = PermissionError(13, "Permission denied")
        self.assertEqual(_public_error(err), "Permission denied")

    def test_oserror_without_strerror_uses_type_name(self):
        err = OSError()
        self.assertEqual(_public_error(err), "OSError")

    def test_non_oserror_is_str(self):
        self.assertEqual(_public_error(ValueError("bad value")), "bad value")

    def test_non_oserror_with_empty_message_uses_type_name(self):
        self.assertEqual(_public_error(RuntimeError()), "RuntimeError")


if __name__ == "__main__":
    unittest.main()
