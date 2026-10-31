"""Importing the extension package wires the queue hook to the shared database.

The package is loaded the way ComfyUI loads it (``__init__`` with relative
imports) with ComfyUI's ``server`` and ``folder_paths`` modules stubbed.
"""

import asyncio
import atexit
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
from pathlib import Path

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


def fake_server_module(app=None, queue_task_done=False):
    """A ``server`` module whose PromptServer.instance accepts routes and hooks.

    ``app`` replaces the default app (one without aiohttp hooks);
    ``queue_task_done`` gives the queue a ``task_done``, so history registers.
    """
    queue = FakePromptQueue()
    if queue_task_done:
        queue.task_done = lambda *a, **k: None
    instance = types.SimpleNamespace(
        routes=web.RouteTableDef(),
        prompt_queue=queue,
        handlers=[],
        app=app if app is not None else types.SimpleNamespace(middlewares=[]),
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
                cleanup_package(package, require_stopped=False)
                raise
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module
    return package, server.PromptServer.instance


def cleanup_package(package, require_stopped=True):
    """Stop fixture-owned workers before releasing its other resources.

    The package's own teardown runs first (the reload case of #301); with
    ``require_stopped`` every part it reports must have stopped. The direct
    stops after it also cover an import that failed before the teardown
    existed, and a test that made a teardown step fail on purpose.
    """
    teardown = getattr(package, "_stop_background_work", None)
    if callable(teardown):
        result = teardown()
        if require_stopped:
            assert all(result.values()), f"package teardown left work running: {result}"
    monitor = getattr(package, "_global_image_monitor", None)
    if monitor is not None:
        assert monitor.stop_monitoring(), "package image monitor still running"
    tracker = getattr(package, "_global_prompt_tracker", None)
    if tracker is not None:
        assert tracker.shutdown(), "package prompt cleanup thread still running"
    registration = getattr(package, "_history_registration", None)
    if registration is not None:
        assert registration.dispose(3), "package history worker leaked"
    close_every_database()
    sys.modules.pop(package.__name__, None)


@contextlib.contextmanager
def load_package(name, db_path, output_dir, server=None, require_stopped=True):
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
            cleanup_package(package, require_stopped=require_stopped)
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
        lines = [
            line
            for line in banner.splitlines()
            if "PromptManager" in line or "loaded" in line
        ]
        self.assertEqual(len(lines), 1, banner)
        self.assertRegex(
            lines[0], r"\[ComfyUI-PromptManager\] Version:.* \S+ - \d+ tools loaded$"
        )
        self.assertNotIn("Loaded:", banner)


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
            except Exception as error:
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
            if any("tools loaded" in str(arg) for arg in args):
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


PACKAGE_THREAD_NAMES = ("PromptManagerPromptCleanup", "PromptManagerImageWorker")


def package_threads():
    """Live threads this design owns: the tracker, the image worker, and the
    watchdog observer and emitters (which have no name of their own)."""
    from watchdog.utils import BaseThread

    return {
        thread
        for thread in threading.enumerate()
        if thread.name in PACKAGE_THREAD_NAMES or isinstance(thread, BaseThread)
    }


def hooked_app():
    return types.SimpleNamespace(
        middlewares=[], on_cleanup=[], on_startup=[], on_shutdown=[]
    )


WAIT_SECONDS = 30  # ceiling for every Event wait (TESTING.md test rules)


def start_busy_worker(package, folder, name="busy.png"):
    """The image worker alive in its processing delay."""
    handler = package._global_image_monitor.handler
    if handler is None:
        raise AssertionError("the package's monitor did not start")
    handler.processing_delay = 3600
    # Outside the watched folder, so the observer does not schedule it first.
    path = os.path.join(folder, name)
    with open(path, "wb") as image:
        image.write(b"x" * 16)
    if not handler.schedule(path) or not handler._worker.is_alive():
        raise AssertionError("the image worker did not start")
    return handler._worker


class PackageTeardownCase(unittest.TestCase):
    """Each test loads its own package (#301): the teardown runs once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.assertEqual(open_handles_under(self.tmp.name), []))
        self.output_dir = os.path.join(self.tmp.name, "output")
        os.makedirs(self.output_dir)
        self.db_path = os.path.join(self.tmp.name, "prompts.db")

    def load(self, server=None, require_stopped=True, suffix=""):
        name = f"pm_teardown_{self._testMethodName}{suffix}"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        package, _ = stack.enter_context(
            load_package(
                name,
                self.db_path,
                self.output_dir,
                server,
                require_stopped=require_stopped,
            )
        )
        return package

    def busy_worker(self, package, name="busy.png"):
        return start_busy_worker(package, self.tmp.name, name)

    def hold_a_preview(self, package, *, advance, kill_releases):
        """A preview worker held inside a fake encode that tracks a fake
        ffmpeg, and a package clock that the monitor step moves by
        ``advance`` seconds. Cleanups release the encode first."""
        # Not the watched folder. Real path for both the file and the root:
        # macOS /var vs /private/var, Windows short names.
        previews = os.path.realpath(os.path.join(self.tmp.name, "previews"))
        os.makedirs(previews)
        from PIL import Image

        image = os.path.join(previews, "held.png")
        Image.new("RGB", (8, 8), "red").save(image)
        held = types.SimpleNamespace(
            entered=threading.Event(),
            release=threading.Event(),
            killed=threading.Event(),
            stop_seen=[],
            timeouts=[],
            now=[100.0],
        )

        class FakeFfmpeg:
            def poll(self):  # _track keeps only processes still running
                return None

            def kill(self):
                held.killed.set()
                if kill_releases:
                    held.release.set()

        def held_encode(src, dst, settings, is_video, *, stop, guard, track):
            track(FakeFfmpeg())
            held.entered.set()
            held.release.wait(WAIT_SECONDS)
            held.stop_seen.append(stop())
            return {"action": "skipped"}

        api = package.api
        monitor = package._global_image_monitor
        real_stop = monitor.stop_monitoring
        real_close = api.close_auto_thumbnails

        def stop_then_advance(timeout):
            ok = real_stop(timeout)
            held.now[0] += advance
            return ok

        def recorded_close(final=False, timeout=None):
            held.timeouts.append(timeout)
            return real_close(final=final, timeout=timeout)

        for target, name, value in (
            (api, "_get_all_output_dirs", lambda: [Path(previews)]),
            (api, "_generate_one", held_encode),
            (api, "close_auto_thumbnails", recorded_close),
            (monitor, "stop_monitoring", stop_then_advance),
            (package, "_clock", lambda: held.now[0]),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(api.queue_capture_thumbnail(image), "queued")
        self.addCleanup(held.release.set)
        self.assertTrue(held.entered.wait(WAIT_SECONDS), "the encode never started")
        held.thumbnailer = api._auto_thumbnailer  # the teardown drops the API's
        held.worker = held.thumbnailer._worker
        # Cleanups run newest first: release the encode, then join the worker
        # (even when an assertion failed first), then end the patches.
        self.addCleanup(held.worker.join, WAIT_SECONDS)
        self.addCleanup(held.release.set)
        self.assertTrue(held.worker.is_alive())
        return held


class TestPackageTeardown(PackageTeardownCase):
    def test_an_image_in_hand_queues_no_preview_during_teardown(self):
        package = self.load()
        monitor = package._global_image_monitor
        real_stop = monitor.stop_monitoring
        image = os.path.join(self.output_dir, "in_hand.png")
        from PIL import Image

        Image.new("RGB", (8, 8), "red").save(image)
        seen = {}

        def stop_with_an_image_in_hand(timeout):
            # The worker read ``thumbnail_queue`` before the detach and now
            # hands its finished image over (at exit, Python 3.12+ refuses a
            # new thread here and logs a traceback).
            seen["queued"] = package.api.queue_capture_thumbnail(image)
            seen["worker"] = package.api._auto_thumbnailer
            return real_stop(timeout)

        with (
            mock.patch.object(
                package.api,
                "_get_all_output_dirs",
                return_value=[Path(os.path.realpath(self.output_dir))],
            ),
            mock.patch.object(
                monitor, "stop_monitoring", side_effect=stop_with_an_image_in_hand
            ),
        ):
            result = package._stop_background_work()
        self.assertEqual(seen["queued"], "skipped")
        self.assertIsNone(seen["worker"])
        self.assertTrue(all(result.values()), result)

    def test_teardown_kills_ffmpeg_and_stops_the_preview_worker(self):
        package = self.load()
        held = self.hold_a_preview(package, advance=4.0, kill_releases=True)

        result = package._stop_background_work()

        self.assertTrue(held.killed.is_set())
        self.assertEqual(held.timeouts, [6.0])  # min(30, 10 - 4)
        self.assertTrue(result["thumbnails"], result)
        self.assertFalse(held.worker.is_alive())
        self.assertEqual(held.stop_seen, [True])

    def test_on_cleanup_runs_the_teardown(self):
        app = hooked_app()
        package = self.load(fake_server_module(app=app))
        self.assertIs(app.on_cleanup[0], package._stop_background_work_on_cleanup)
        self.assertIsNone(package._background_work_result)

        async def run_cleanup():
            for callback in list(app.on_cleanup):
                await callback(app)

        asyncio.run(run_cleanup())
        self.assertEqual(
            package._background_work_result,
            {"monitor": True, "thumbnails": True, "tracker": True},
        )

    def test_teardown_runs_before_history_dispose_at_exit(self):
        with mock.patch("atexit.register", wraps=atexit.register) as register:
            package = self.load(fake_server_module(queue_task_done=True))
        registered = [call.args[0] for call in register.call_args_list]
        registration = package._history_registration
        self.assertIsNotNone(registration, "history did not register")
        self.assertIn(registration.dispose, registered)
        self.assertIn(package._stop_background_work, registered)
        # atexit runs newest first: the teardown before the history dispose.
        self.assertGreater(
            registered.index(package._stop_background_work),
            registered.index(registration.dispose),
        )


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname}:{record.getMessage()}")


class TestPackageTeardownOnce(PackageTestCase):
    """The tests that only observe one plain teardown share one package.

    The teardown runs once, so it runs here in ``setUpClass`` (per-test
    ``assertLogs`` cannot wrap it); the tests read what it recorded.
    """

    package_name = "pm_teardown_once"

    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            super().setUpClass()
        package = cls.package
        cls.monitor = package._global_image_monitor
        cls.observer = cls.monitor.observer
        cls.tracker_thread = package._global_prompt_tracker.cleanup_thread
        cls.worker = start_busy_worker(package, cls.tmp.name)
        cls.recorder_before = cls.monitor.review_recorder
        logger = logging.getLogger("prompt_manager.init")
        handler, level = ListHandler(), logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)  # otherwise set by the config
        try:
            with mock.patch("atexit.unregister", wraps=atexit.unregister) as unregister:
                cls.result = package._stop_background_work()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        cls.log = "\n".join(handler.lines)
        cls.unregister_calls = unregister.call_args_list

    def test_teardown_stops_every_part_once(self):
        expected = {"monitor": True, "thumbnails": True, "tracker": True}
        self.assertEqual(self.result, expected)
        self.assertEqual(list(self.result), ["monitor", "thumbnails", "tracker"])
        self.assertIn("Background work stopped in", self.log)
        self.assertIn("monitor=ok thumbnails=ok tracker=ok", self.log)
        self.assertFalse(self.worker.is_alive())
        self.assertFalse(self.observer.is_alive())
        self.assertFalse(self.tracker_thread.is_alive())
        copy = self.package._stop_background_work()
        copy["monitor"] = False  # a copy: the stored result is unchanged
        with mock.patch.object(self.monitor, "stop_monitoring") as again:
            self.assertEqual(self.package._stop_background_work(), expected)
        again.assert_not_called()

    def test_teardown_detaches_the_monitor_callbacks(self):
        self.assertIsNotNone(self.recorder_before)
        self.assertIsNone(self.monitor.review_recorder)
        self.assertIsNone(self.monitor.thumbnail_queue)
        self.assertIsNone(self.monitor.output_listener)

    def test_teardown_removes_its_atexit_callback(self):
        self.assertEqual(
            self.unregister_calls, [mock.call(self.package._stop_background_work)]
        )


class TestPackageTeardownFailures(PackageTeardownCase):
    """A failing step never skips the later ones (design D14, R2)."""

    def assert_tracker_stops_after(self, error):
        package = self.load(require_stopped=False)
        monitor = package._global_image_monitor
        tracker_thread = package._global_prompt_tracker.cleanup_thread
        with (
            mock.patch.object(monitor, "stop_monitoring", side_effect=error),
            self.assertLogs("prompt_manager.init", "WARNING") as logs,
        ):
            result = package._stop_background_work()
        self.assertFalse(result["monitor"])
        self.assertTrue(result["tracker"])
        self.assertFalse(tracker_thread.is_alive())
        text = "\n".join(logs.output)
        self.assertIn("Background work not stopped in time", text)
        self.assertRegex(text, r"not stopped in time \([0-9.]+ s\): monitor$")
        self.assertIn("Traceback", text)

    def test_teardown_step_failure_does_not_skip_later_steps(self):
        self.assert_tracker_stops_after(RuntimeError("stop failed"))

    def test_second_interrupt_does_not_skip_the_tracker(self):
        self.assert_tracker_stops_after(KeyboardInterrupt())

    def test_an_interrupt_between_steps_still_records_the_teardown(self):
        package = self.load(require_stopped=False)
        real_step = package._teardown_step

        def interrupted_before_the_previews(name, stop, logger):
            if name == "the preview worker":
                raise KeyboardInterrupt  # outside the step's own try
            return real_step(name, stop, logger)

        with (
            mock.patch.object(
                package, "_teardown_step", interrupted_before_the_previews
            ),
            mock.patch("atexit.unregister", wraps=atexit.unregister) as unregister,
            self.assertRaises(KeyboardInterrupt),
        ):
            package._stop_background_work()
        expected = {"monitor": True, "thumbnails": False, "tracker": False}
        self.assertEqual(package._background_work_result, expected)
        unregister.assert_called_once_with(package._stop_background_work)
        with mock.patch.object(
            package._global_image_monitor, "stop_monitoring"
        ) as again:
            self.assertEqual(package._stop_background_work(), expected)
        again.assert_not_called()

    def test_teardown_reports_a_preview_worker_still_held(self):
        package = self.load(require_stopped=False)
        held = self.hold_a_preview(package, advance=9.95, kill_releases=False)

        with self.assertLogs("prompt_manager.init", "WARNING") as logs:
            result = package._stop_background_work()

        self.assertTrue(held.killed.is_set())
        self.assertEqual(len(held.timeouts), 1)
        self.assertAlmostEqual(held.timeouts[0], 0.05, places=6)
        self.assertTrue(held.thumbnailer.closed)
        self.assertFalse(result["thumbnails"], result)
        self.assertRegex("\n".join(logs.output), r"not stopped in time .*thumbnails")
        held.release.set()
        held.worker.join(WAIT_SECONDS)
        self.assertFalse(held.worker.is_alive())
        self.assertEqual(held.stop_seen, [True])

    def test_teardown_shares_one_deadline(self):
        package = self.load(require_stopped=False)
        now = [100.0]
        seen = []

        def step(timeout, **_):
            seen.append(timeout)
            now[0] += 6.0
            return True

        with (
            mock.patch.object(package, "_clock", lambda: now[0]),
            mock.patch.object(
                package._global_image_monitor, "stop_monitoring", side_effect=step
            ),
            mock.patch.object(
                package.api,
                "close_auto_thumbnails",
                side_effect=lambda final=False, timeout=None: step(timeout),
            ),
            mock.patch.object(
                package._global_prompt_tracker, "shutdown", side_effect=step
            ),
        ):
            package._stop_background_work()
        self.assertEqual(seen, [5.0, 4.0, 0.0])


class TestRepeatedLoads(PackageTeardownCase):
    def test_repeated_loads_leave_no_threads(self):
        for round_ in range(3):
            before = package_threads()
            with contextlib.redirect_stdout(io.StringIO()):
                with load_package(
                    f"pm_teardown_repeat_{round_}",
                    self.db_path,
                    self.output_dir,
                ) as (package, _):
                    self.busy_worker(package, f"busy_{round_}.png")
                    started = package_threads() - before
                    self.assertTrue(started, "the package started no thread")
            left = {t for t in package_threads() - before if t.is_alive()}
            self.assertEqual(left, set(), f"round {round_}")
