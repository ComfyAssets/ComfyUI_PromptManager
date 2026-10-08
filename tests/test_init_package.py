"""Importing the extension package wires the queue hook to the shared database.

The package is loaded the way ComfyUI loads it (``__init__`` with relative
imports) with ComfyUI's ``server`` and ``folder_paths`` modules stubbed.
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
import unittest.mock as mock

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class FakePromptQueue:
    def __init__(self):
        self.results = []

    def get(self, timeout=None):
        return self.results.pop(0) if self.results else None


def fake_server_module():
    """A ``server`` module whose PromptServer.instance accepts routes and hooks."""
    instance = types.SimpleNamespace(
        routes=web.RouteTableDef(),
        prompt_queue=FakePromptQueue(),
        handlers=[],
    )
    instance.add_on_prompt_handler = instance.handlers.append
    module = types.ModuleType("server")
    module.PromptServer = type("PromptServer", (), {"instance": instance})
    return module


def fake_folder_paths_module(output_dir):
    module = types.ModuleType("folder_paths")
    module.base_path = output_dir
    module.models_dir = os.path.join(output_dir, "models")
    module.get_output_directory = lambda: output_dir
    module.get_folder_paths = lambda name: []
    return module


def load_package(name, db_path, output_dir):
    """Import ROOT/__init__.py as package ``name`` with ComfyUI stubbed.

    Returns (package, server instance). The caller stops the image monitor.
    """
    saved = {key: sys.modules.get(key) for key in ("server", "folder_paths")}
    server = fake_server_module()
    sys.modules["server"] = server
    sys.modules["folder_paths"] = fake_folder_paths_module(output_dir)
    try:
        from py.config import PromptManagerConfig

        with mock.patch.object(PromptManagerConfig, "DEFAULT_DB_PATH", db_path):
            spec = importlib.util.spec_from_file_location(
                name,
                os.path.join(ROOT, "__init__.py"),
                submodule_search_locations=[ROOT],
            )
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            try:
                spec.loader.exec_module(package)
            except BaseException:
                sys.modules.pop(name, None)
                raise
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module
    return package, server.PromptServer.instance


class PackageTestCase(unittest.TestCase):
    package_name = "prompt_manager_pkg"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.output_dir = os.path.join(cls.tmp.name, "output")
        os.makedirs(cls.output_dir)
        cls.db_path = cls.make_db_path(cls.tmp.name)
        cls.package, cls.server = load_package(
            cls.package_name, cls.db_path, cls.output_dir
        )

    @classmethod
    def make_db_path(cls, tmp):
        return os.path.join(tmp, "prompts.db")

    @classmethod
    def tearDownClass(cls):
        monitor = getattr(cls.package, "_global_image_monitor", None)
        if monitor is not None:
            monitor.stop_monitoring()
        sys.modules.pop(cls.package_name, None)
        cls.tmp.cleanup()


class TestPackageWiring(PackageTestCase):
    def test_nodes_are_registered(self):
        self.assertEqual(
            sorted(self.package.NODE_CLASS_MAPPINGS),
            ["PromptManager", "PromptManagerText", "PromptSearchList"],
        )

    def test_db_factory_returns_the_shared_database(self):
        self.assertIsNotNone(self.package._global_db)
        self.assertIs(self.package._db_factory(), self.package._global_db)
        self.assertEqual(
            os.path.normcase(os.path.realpath(self.package._global_db.model.db_path)),
            os.path.normcase(os.path.realpath(self.db_path)),
        )

    def test_db_factory_raises_while_the_database_is_unavailable(self):
        with mock.patch.object(self.package, "_global_db", None):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.package._db_factory()

    def test_queue_hook_is_registered_and_the_queue_get_is_wrapped(self):
        self.assertEqual(len(self.server.handlers), 1)
        self.assertTrue(
            getattr(self.server.prompt_queue.get, "_prompt_manager_dequeue_hook", False)
        )

    def test_image_monitor_watches_the_stubbed_output_directory(self):
        status = self.package._global_image_monitor.get_status()
        self.assertTrue(status["running"])
        self.assertEqual(
            [
                os.path.normcase(os.path.realpath(d))
                for d in status["monitored_directories"]
            ],
            [os.path.normcase(os.path.realpath(self.output_dir))],
        )


class TestPackageWithoutDatabase(PackageTestCase):
    """The database failing to initialise must not break the queue hook."""

    package_name = "prompt_manager_pkg_nodb"

    @classmethod
    def make_db_path(cls, tmp):
        blocker = os.path.join(tmp, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        return os.path.join(blocker, "prompts.db")  # parent is a file: init fails

    def test_global_db_is_defined_but_unset(self):
        self.assertIsNone(self.package._global_db)
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.package._db_factory()

    def test_queue_hook_still_registers_and_survives_a_dequeue(self):
        self.assertEqual(len(self.server.handlers), 1)
        request = self.server.handlers[0](
            {"prompt": {"1": {"class_type": "PromptManager", "inputs": {"text": "x"}}}}
        )
        self.server.prompt_queue.results.append(
            ((0, request["prompt_id"], request["prompt"], {}, [], False), 1)
        )
        self.assertIsNotNone(self.server.prompt_queue.get())


if __name__ == "__main__":
    unittest.main()
