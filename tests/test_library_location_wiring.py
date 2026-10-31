"""Every data path asks the one resolver (#249 S6b).

The database, the diagnostics database, config.json, the logs and the LoRA
image cache all follow ``utils.library_location``: a folder named by
``PROMPT_MANAGER_DATA_DIR`` (and, after a live move, the switched location).
"""

import os
import pathlib
import sys
import tempfile
import types
import unittest
import unittest.mock as mock
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import library_location as ll  # noqa: E402


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = os.path.realpath(self._tmp.name)
        self.data = os.path.join(base, "pm-data")
        patcher = mock.patch.dict(
            os.environ,
            {ll.DATA_DIR_ENV: self.data, ll.CONFIG_PATH_ENV: ""},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # This test's own ComfyUI folders (the shared stub serves the system
        # temp folder as "output", which would rightly refuse self.data).
        comfy = os.path.join(base, "ComfyUI")
        fp = types.ModuleType("folder_paths")
        for name in ("output", "input", "temp", "user"):
            setattr(fp, f"get_{name}_directory", lambda n=name: os.path.join(comfy, n))
        modules = mock.patch.dict(sys.modules, {"folder_paths": fp})
        modules.start()
        self.addCleanup(modules.stop)
        ll.clear_override()
        self.addCleanup(ll.clear_override)


class TestDatabasePath(_EnvCase):
    def test_database_follows_the_data_folder(self):
        from database.operations import _resolve_db_path

        with mock.patch.dict(sys.modules, {"py.config": None}):
            path = _resolve_db_path(None)
        self.assertEqual(path, os.path.join(self.data, "prompts.db"))
        self.assertTrue(os.path.isdir(self.data))
        if os.name == "posix":
            self.assertEqual(os.stat(self.data).st_mode & 0o777, 0o700)

    def test_diagnostics_use_the_same_resolver(self):
        from database.operations import _resolve_db_path
        from utils.diagnostics import GalleryDiagnostics

        with mock.patch.dict(sys.modules, {"py.config": None}):
            self.assertEqual(GalleryDiagnostics._resolve_db_path(), _resolve_db_path())

    def test_switched_location_wins(self):
        from database.operations import _resolve_db_path

        moved = ll.LibraryLocation(
            ll.SOURCE_DEFAULT,
            os.path.join(self.data, "moved", "prompts.db"),
            os.path.join(self.data, "moved", "config.json"),
            os.path.join(self.data, "moved"),
        )
        ll.switch_to(moved)
        self.assertEqual(_resolve_db_path(None), moved.db_path)


class TestConfigPath(_EnvCase):
    def test_config_json_follows_the_data_folder(self):
        from py.config import PromptManagerConfig

        self.assertEqual(
            PromptManagerConfig.get_config_path(),
            os.path.join(self.data, "config.json"),
        )

    def test_explicit_config_path_still_wins(self):
        from py.config import PromptManagerConfig

        custom = os.path.join(self.data, "elsewhere.json")
        with mock.patch.dict(os.environ, {ll.CONFIG_PATH_ENV: custom}):
            self.assertEqual(PromptManagerConfig.get_config_path(), custom)

    def test_keep_choice_round_trips(self):
        from py.config import PromptManagerConfig

        original = PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER
        self.addCleanup(
            setattr, PromptManagerConfig, "KEEP_LIBRARY_IN_NODE_FOLDER", original
        )
        PromptManagerConfig.update_config({"library": {"keep_in_node_folder": True}})
        self.assertTrue(PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER)
        self.assertEqual(
            PromptManagerConfig.get_config()["library"], {"keep_in_node_folder": True}
        )
        PromptManagerConfig.update_config({"library": {"keep_in_node_folder": "yes"}})
        self.assertTrue(PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER)


class TestLogsAndCache(_EnvCase):
    def test_logs_go_to_the_data_folder(self):
        from utils import logging_config
        from utils.logging_config import PromptManagerLogger

        manager = object.__new__(PromptManagerLogger)
        self.addCleanup(self._close, manager)
        manager.__init__()
        self.assertEqual(manager.log_dir, pathlib.Path(self.data, "logs"))
        self.assertTrue(manager.log_dir.is_dir())
        self.assertIs(logging_config.library_location, ll)

    @staticmethod
    def _close(manager):
        import logging

        for handler in logging.getLogger("prompt_manager").handlers[:]:
            if getattr(handler, "_prompt_manager_owned", False):
                handler.close()
        try:
            from utils.logging_config import get_logger_manager

            get_logger_manager()._setup_loggers()
        except Exception:
            # best effort restore in teardown; must not hide the test result
            pass

    def test_lora_image_cache_follows_the_data_folder(self):
        from py import lora_utils

        cache = lora_utils.get_lora_image_cache_dir()
        self.assertEqual(cache, pathlib.Path(self.data, "data", "lora_images"))


class TestNodeFolderUnchangedForLegacyInstalls(unittest.TestCase):
    """A node folder that holds the library keeps every path where it was."""

    def test_legacy_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            ext = os.path.join(tmp, "ext")
            os.makedirs(ext)
            open(os.path.join(ext, "prompts.db"), "wb").close()
            fp = types.SimpleNamespace(
                get_user_directory=lambda: os.path.join(tmp, "u")
            )
            location = ll.resolve_library(
                None, environ={}, extension_root=ext, folder_paths=fp
            )
            self.assertEqual(location.db_path, os.path.join(ext, "prompts.db"))
            self.assertEqual(
                ll.logs_dir(environ={}, extension_root=ext, folder_paths=fp),
                os.path.join(ext, "logs"),
            )


if __name__ == "__main__":
    unittest.main()
