"""Importing the extension package wires the queue hook to the shared database.

The package is loaded the way ComfyUI loads it (``__init__`` with relative
imports) with ComfyUI's ``server`` and ``folder_paths`` modules stubbed.
"""

import contextlib
import importlib.util
import io
import logging
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock as mock

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.logging_config import get_logger_manager  # noqa: E402

get_logger_manager()

try:
    from tests.open_handles import open_handles_under  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import open_handles_under  # noqa: E402


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
        app=types.SimpleNamespace(middlewares=[]),
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


def _import_package(name, db_path, output_dir, server=None):
    """Import ROOT/__init__.py as package ``name`` with ComfyUI stubbed.

    Returns (package, server instance) to the scoped fixture.
    """
    saved = {key: sys.modules.get(key) for key in ("server", "folder_paths")}
    server = server or fake_server_module()
    sys.modules["server"] = server
    sys.modules["folder_paths"] = fake_folder_paths_module(output_dir)
    try:
        from py.config import PromptManagerConfig

        config_path = os.path.join(output_dir, "promptmanager-test-config.json")
        with open(config_path, "w", encoding="utf-8") as config:
            json.dump({"database": {"default_path": db_path}}, config)
        with (
            mock.patch.object(PromptManagerConfig, "DEFAULT_DB_PATH", db_path),
            mock.patch.dict(os.environ, {"PROMPT_MANAGER_CONFIG_PATH": config_path}),
        ):
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
                cleanup_package(package)
                raise
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module
    return package, server.PromptServer.instance


def cleanup_package(package):
    """Stop fixture-owned workers before releasing its other resources."""
    registration = getattr(package, "_history_registration", None)
    if registration is not None:
        assert registration.dispose(3), "package history worker leaked"
    monitor = getattr(package, "_global_image_monitor", None)
    if monitor is not None:
        monitor.stop_monitoring()
    close_every_database()
    sys.modules.pop(package.__name__, None)


@contextlib.contextmanager
def load_package(name, db_path, output_dir, server=None):
    """Dispose package workers before closing its isolated logging handlers."""
    root_logger = logging.getLogger("prompt_manager")
    loggers = [root_logger] + [
        logger
        for key, logger in list(logging.Logger.manager.loggerDict.items())
        if key.startswith("prompt_manager.") and isinstance(logger, logging.Logger)
    ]
    previous = {
        logger: (
            logger.handlers[:],
            logger.level,
            logger.propagate,
            logger.disabled,
            logger.filters[:],
        )
        for logger in loggers
    }
    prior_handlers = {handler for state in previous.values() for handler in state[0]}
    root_logger.handlers = []
    try:
        package, instance = _import_package(name, db_path, output_dir, server)
        # A previously imported package may reuse its existing logger singleton.
        if not root_logger.handlers:
            root_logger.handlers = previous[root_logger][0][:]
        try:
            yield package, instance
        finally:
            cleanup_package(package)
    finally:
        current_loggers = [root_logger] + [
            logger
            for key, logger in list(logging.Logger.manager.loggerDict.items())
            if key.startswith("prompt_manager.") and isinstance(logger, logging.Logger)
        ]
        created_handlers = {
            handler
            for logger in current_loggers
            for handler in logger.handlers
            if handler not in prior_handlers
        }
        for logger in current_loggers:
            (
                logger.handlers,
                level,
                logger.propagate,
                logger.disabled,
                logger.filters,
            ) = previous.get(logger, ([], logging.NOTSET, True, False, []))
            logger.setLevel(level)
        for handler in created_handlers:
            handler.close()


def close_every_database():
    """Close connections of every PromptModel registry in the process.

    A package loaded under its own name imports its own copy of
    database.models, whose PromptModel class keeps its own registry.
    """
    for name, module in list(sys.modules.items()):
        if name.endswith("database.models") and hasattr(module, "PromptModel"):
            module.PromptModel.close_all_instances()


class PackageTestCase(unittest.TestCase):
    package_name = "prompt_manager_pkg"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.addClassCleanup(cls.assert_no_open_handles)
        cls.output_dir = os.path.join(cls.tmp.name, "output")
        os.makedirs(cls.output_dir)
        cls.db_path = cls.make_db_path(cls.tmp.name)
        package_context = contextlib.ExitStack()
        cls.addClassCleanup(package_context.close)
        cls.package, cls.server = package_context.enter_context(
            load_package(
                cls.package_name, cls.db_path, cls.output_dir, server=cls.make_server()
            )
        )

    @classmethod
    def make_db_path(cls, tmp):
        return os.path.join(tmp, "prompts.db")

    @classmethod
    def make_server(cls):
        return fake_server_module()

    @classmethod
    def assert_no_open_handles(cls):
        hits = open_handles_under(cls.tmp.name)
        assert not hits, f"files still open before temp dir cleanup: {hits}"


class TestPackageWiring(PackageTestCase):
    def test_nodes_are_registered(self):
        self.assertEqual(
            sorted(self.package.NODE_CLASS_MAPPINGS),
            [
                "PromptManager",
                "PromptManagerFocused",
                "PromptManagerPair",
                "PromptManagerSplit",
                "PromptManagerText",
                "PromptSearchList",
            ],
        )

    def test_split_display_name_and_execution_contract_are_registered(self):
        node = self.package.NODE_CLASS_MAPPINGS["PromptManagerSplit"]
        self.assertEqual(
            self.package.NODE_DISPLAY_NAME_MAPPINGS["PromptManagerSplit"],
            "Prompt Manager (Split)",
        )
        focused = self.package.NODE_CLASS_MAPPINGS["PromptManagerFocused"]
        self.assertTrue(issubclass(node, focused))
        self.assertEqual(node.INPUT_TYPES(), focused.INPUT_TYPES())

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


class TestVersion(PackageTestCase):
    def test_version_comes_from_pyproject(self):
        import re

        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            expected = re.search(r'version\s*=\s*["\']([^"\']+)["\']', f.read())
        self.assertEqual(self.package.get_version(), expected.group(1))

    def test_unreadable_pyproject_gives_unknown(self):
        with mock.patch.object(self.package, "Path", side_effect=OSError("denied")):
            self.assertEqual(self.package.get_version(), "unknown")
        missing = mock.Mock()
        missing.parent.__truediv__ = lambda self_, name: mock.Mock(exists=lambda: False)
        with mock.patch.object(self.package, "Path", return_value=missing):
            self.assertEqual(self.package.get_version(), "unknown")


class TestPackageWhenHookRegistrationFails(PackageTestCase):
    """A server that rejects the hook must not break the rest of the package."""

    package_name = "prompt_manager_pkg_nohook"

    @classmethod
    def make_server(cls):
        module = fake_server_module()
        instance = module.PromptServer.instance

        def reject(handler):
            raise RuntimeError("hooks unsupported")

        instance.add_on_prompt_handler = reject
        return module

    def test_nodes_and_monitoring_still_come_up(self):
        self.assertIn("PromptManager", self.package.NODE_CLASS_MAPPINGS)
        self.assertIsNotNone(self.package._global_db)
        self.assertTrue(self.package._global_image_monitor.is_monitoring)
        self.assertEqual(self.server.handlers, [])


if __name__ == "__main__":
    unittest.main()


class TestConsoleBanner(unittest.TestCase):
    """Loading the node must not depend on the console being able to print emoji."""

    def test_package_loads_with_a_cp1252_console(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(close_every_database)
        output_dir = os.path.join(tmp.name, "output")
        os.makedirs(output_dir)
        name = "prompt_manager_pkg_cp1252"
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

        package_context = contextlib.ExitStack()
        self.addCleanup(package_context.close)
        with contextlib.redirect_stdout(stream):
            package, _ = package_context.enter_context(
                load_package(name, os.path.join(tmp.name, "p.db"), output_dir)
            )
        self.addCleanup(sys.modules.pop, name, None)
        monitor = getattr(package, "_global_image_monitor", None)
        if monitor is not None:
            self.addCleanup(monitor.stop_monitoring)

        stream.flush()
        banner = stream.buffer.getvalue().decode("cp1252")
        self.assertIn("Loaded:", banner)


class PriorFileHandler(logging.FileHandler):
    """A caller-owned sink that stays closed once its owner closes it.

    ``FileHandler.emit`` reopens the file when ``stream`` is None, so a record
    that selected this handler before ``close()`` would reopen prior.log after
    it, and Windows then refuses the temp directory cleanup (WinError 32).
    ``Handler.handle`` and ``FileHandler.close`` hold the same handler lock, so
    checking ``_closed`` in ``emit`` is race free (as in the product's
    ``_OwnedRotatingFileHandler``).
    """

    def emit(self, record):
        if not self._closed:
            super().emit(record)


class TestPackageLoggerIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Runs after restore_logging, before the cleanup Windows refuses while
        # prior.log is open; /proc makes Linux fail the same way.
        self.addCleanup(self.assert_tmp_has_no_open_handles)
        self.logger = logging.getLogger("prompt_manager")
        self.previous = (
            self.logger.handlers[:],
            self.logger.level,
            self.logger.propagate,
            self.logger.disabled,
        )
        self.addCleanup(self.restore_logging)
        self.path = os.path.join(self.tmp.name, "prior.log")
        self.handler = PriorFileHandler(self.path)
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = True
        self.logger.disabled = True
        self.child = logging.getLogger("prompt_manager.database")
        self.child_previous = self.child.level
        self.addCleanup(self.child.setLevel, self.child_previous)
        self.child.setLevel(logging.ERROR)
        self.saved_modules = {
            key: sys.modules.get(key) for key in ("server", "folder_paths")
        }
        self.saved_config = os.environ.get("PROMPT_MANAGER_CONFIG_PATH")
        self.server = fake_server_module()
        self.server.PromptServer.instance.prompt_queue.task_done = lambda *a, **k: None

    def restore_logging(self):
        # Detach before closing, so new records stop selecting these handlers.
        retired = [h for h in self.logger.handlers if h not in self.previous[0]]
        (
            self.logger.handlers,
            self.logger.level,
            self.logger.propagate,
            self.logger.disabled,
        ) = self.previous
        for handler in retired:
            handler.close()
        self.handler.close()

    def assert_tmp_has_no_open_handles(self):
        self.assertEqual(open_handles_under(self.tmp.name), [])

    def assert_logging_restored(self, handlers, registration):
        for key, module in self.saved_modules.items():
            self.assertIs(sys.modules.get(key), module)
        self.assertEqual(
            os.environ.get("PROMPT_MANAGER_CONFIG_PATH"), self.saved_config
        )
        self.assertEqual(self.logger.handlers, [self.handler])
        self.assertEqual(self.logger.level, logging.DEBUG)
        self.assertTrue(self.logger.propagate)
        self.assertTrue(self.logger.disabled)
        self.assertEqual(self.child.level, logging.ERROR)
        self.assertFalse(self.handler.stream.closed)
        self.assertTrue(all(handler.stream is None for handler in handlers))
        self.assertFalse(registration.writer.status().alive)
        self.logger.disabled = False
        self.logger.warning("prior logging still works")
        self.logger.disabled = True
        self.handler.flush()
        with open(self.path, encoding="utf-8") as log:
            self.assertIn("prior logging still works", log.read())

    def test_package_scope_closes_its_handlers_after_worker_disposal(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with load_package(
                "prompt_manager_pkg_logger_scope",
                os.path.join(self.tmp.name, "p.db"),
                self.tmp.name,
                self.server,
            ) as (package, _):
                handlers = [
                    h
                    for h in self.logger.handlers
                    if isinstance(h, logging.FileHandler)
                ]
                self.assertTrue(handlers)
                registration = package._history_registration
                original_dispose = registration.dispose
                open_at_disposal = []

                def dispose(timeout=2):
                    open_at_disposal.append(all(h.stream is not None for h in handlers))
                    return original_dispose(timeout)

                registration.dispose = dispose
        self.assertEqual(open_at_disposal, [True])
        self.assert_logging_restored(handlers, registration)

    def test_record_in_flight_at_restore_cannot_reopen_prior_log(self):
        """A record that selected prior.log before restore never reopens it.

        Leaked daemon threads of other tests (image workers, watchdog) log
        under ``prompt_manager.*``. One that reaches the prior handler while
        restore_logging closes it would, with a plain FileHandler, reopen the
        file on emit and keep it open through TemporaryDirectory.cleanup().
        """
        message = "record selected before restore_logging"
        selected = threading.Event()
        resume = threading.Event()
        errors = []

        def pause_before_handler_lock(record):
            if record.getMessage() == message:
                selected.set()
                if not resume.wait(5):
                    raise RuntimeError("logging thread was not released")
            return True

        def log_from_background_thread():
            try:
                logging.getLogger("prompt_manager.late_emitter").warning(message)
            except BaseException as error:
                errors.append(error)

        self.handler.addFilter(pause_before_handler_lock)
        worker = threading.Thread(target=log_from_background_thread, daemon=True)
        worker.start()
        try:
            self.assertTrue(selected.wait(5), "record did not select prior.log")
            self.restore_logging()
            self.assertIsNone(self.handler.stream)
        finally:
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive(), "logging thread did not finish")
        self.assertEqual(errors, [])
        self.assertIsNone(self.handler.stream)
        self.assert_tmp_has_no_open_handles()
        with open(self.path, encoding="utf-8") as log:
            self.assertNotIn(message, log.read())

    def test_failed_package_import_closes_handlers_and_restores_logging(self):
        name = "prompt_manager_pkg_logger_failure"
        captured = {}
        original_print = print

        def fail_banner(*args, **kwargs):
            if any("Total:" in str(arg) for arg in args):
                captured["package"] = sys.modules[name]
                captured["handlers"] = [
                    h
                    for h in self.logger.handlers
                    if isinstance(h, logging.FileHandler)
                ]
                raise RuntimeError("injected banner failure")
            return original_print(*args, **kwargs)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch("builtins.print", side_effect=fail_banner),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected banner failure"):
                with load_package(
                    name,
                    os.path.join(self.tmp.name, "p.db"),
                    self.tmp.name,
                    self.server,
                ):
                    self.fail("failed import yielded a package")
        self.assertNotIn(name, sys.modules)
        self.assertTrue(captured["handlers"])
        self.assert_logging_restored(
            captured["handlers"], captured["package"]._history_registration
        )
