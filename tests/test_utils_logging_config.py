"""PromptManagerLogger: memory buffer, file rotation, level changes, truncation."""

import logging
import ntpath
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import logging_config  # noqa: E402
from utils.logging_config import (  # noqa: E402
    MemoryBufferHandler,
    PromptManagerLogger,
    RotatedChanged,
    get_logger,
    get_logger_manager,
)

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402


class IsolatedLoggerTestCase(unittest.TestCase):
    """A logger manager whose log directory is a temporary folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        package = os.path.join(self.tmp.name, "pkg")
        os.makedirs(os.path.join(package, "utils"))
        self.log_dir = os.path.join(package, "logs")
        fake_file = os.path.join(package, "utils", "logging_config.py")
        with mock.patch.object(logging_config, "__file__", fake_file):
            self.manager = object.__new__(PromptManagerLogger)  # bypass singleton
            self.manager.__init__()
        self.allowed_thread_names = set()
        self.manager.update_config({"console_logging": False})
        # Drop the "Updated logging configuration" entry.
        self.manager._log_buffer.clear()
        # Other tests leave daemon threads (image monitor, watchdog) logging
        # under "prompt_manager.*"; only records from this test's thread may
        # reach the buffer or the rotating file (a stray line after a truncate
        # would roll the file over and resurrect the backups). update_config
        # rebuilds the handlers, so the filter is re-applied after every call.
        self._filter_to_this_thread()
        original_update = self.manager.update_config

        def update_and_refilter(new_config):
            original_update(new_config)
            self._filter_to_this_thread()

        self.manager.update_config = update_and_refilter
        self.logger = logging.getLogger("prompt_manager.coverage_test")
        self.addCleanup(self._restore_global_logging)

    def _filter_to_this_thread(self):
        this_thread = threading.get_ident()
        # A test may let named helper threads of its own through too.
        allowed_names = self.allowed_thread_names

        def allowed(record):
            return record.thread == this_thread or record.threadName in allowed_names

        for handler in self.manager.logger.handlers:
            handler.filters.clear()
            handler.addFilter(allowed)

    def _restore_global_logging(self):
        for handler in logging.getLogger("prompt_manager").handlers[:]:
            handler.close()
        # Windows refuses to delete an open log: prove every handle is closed.
        assert_closed(self, self.tmp.name)
        get_logger_manager()._setup_loggers()
        self.tmp.cleanup()

    def _rotate(self, max_file_size=400, backup_count=2, messages=30):
        self.manager.update_config(
            {"max_file_size": max_file_size, "backup_count": backup_count}
        )
        for i in range(messages):
            self.logger.info("message %03d %s", i, "x" * 80)


class TestSingleton(unittest.TestCase):
    def test_manager_and_loggers_are_shared(self):
        self.assertIs(PromptManagerLogger(), PromptManagerLogger())
        self.assertIs(get_logger_manager(), get_logger_manager())
        self.assertIs(
            get_logger("prompt_manager.x"), logging.getLogger("prompt_manager.x")
        )
        self.assertEqual(get_logger().name, "prompt_manager")

    def test_init_is_a_noop_on_the_existing_instance(self):
        manager = get_logger_manager()
        before = manager.config
        manager.__init__()
        self.assertIs(manager.config, before)


class TestMemoryBuffer(IsolatedLoggerTestCase):
    def test_records_are_buffered_most_recent_first(self):
        self.logger.info("first")
        self.logger.warning("second")
        self.logger.error("third")
        recent = self.manager.get_recent_logs(limit=2)
        self.assertEqual([r["message"] for r in recent], ["third", "second"])
        entry = recent[0]
        self.assertEqual(entry["level"], "ERROR")
        self.assertEqual(entry["logger"], self.logger.name)
        self.assertIn("third", entry["formatted"])
        for key in ("timestamp", "module", "filename", "lineno", "thread", "process"):
            self.assertIn(key, entry)

    def test_level_filter(self):
        self.logger.info("info")
        self.logger.warning("warn")
        self.logger.error("err")
        self.assertEqual(
            [r["message"] for r in self.manager.get_recent_logs(level="warning")],
            ["err", "warn"],
        )
        self.assertEqual(len(self.manager.get_recent_logs(level="NOPE")), 3)

    def test_buffer_is_capped_at_the_configured_size(self):
        for i in range(1005):
            self.logger.info("entry %d", i)
        stats = self.manager.get_log_stats()
        self.assertEqual(stats["buffer_count"], 1000)
        self.assertEqual(stats["level_counts"], {"INFO": 1000})
        self.assertEqual(stats["current_level"], "INFO")
        self.assertEqual(
            os.path.normcase(os.path.realpath(stats["log_directory"])),
            os.path.normcase(os.path.realpath(self.log_dir)),
        )
        self.assertEqual(
            self.manager.get_recent_logs(limit=1)[0]["message"], "entry 1004"
        )

    def test_handler_swallows_buffer_errors(self):
        manager = mock.Mock()
        manager.add_to_buffer.side_effect = RuntimeError("boom")
        handler = MemoryBufferHandler(manager)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
        handler.emit(record)  # must not raise
        manager.add_to_buffer.assert_called_once()


class TestFilesAndRotation(IsolatedLoggerTestCase):
    def test_rotation_creates_backups_and_lists_them_newest_first(self):
        self._rotate()
        files = self.manager.get_log_files()
        names = sorted(f["filename"] for f in files)
        self.assertEqual(
            names,
            ["prompt_manager.log", "prompt_manager.log.1", "prompt_manager.log.2"],
        )
        main = [f for f in files if f["is_main"]]
        self.assertEqual(len(main), 1)
        self.assertTrue(all(f["size"] > 0 for f in files if not f["is_main"]))
        modified = [f["modified"] for f in files]
        self.assertEqual(modified, sorted(modified, reverse=True))
        self.assertEqual(self.manager.get_log_stats()["log_files_count"], 3)

    def test_read_log_file_tail_and_whole(self):
        self._rotate()
        tail = self.manager.read_log_file("prompt_manager.log.1", lines=1)
        self.assertEqual(len(tail), 1)
        whole = self.manager.read_log_file("prompt_manager.log.1", lines=0)
        self.assertGreater(len(whole), 1)
        self.assertTrue(whole[-1].endswith(tail[0]))

    def test_read_log_file_rejects_missing_and_outside_paths(self):
        with self.assertRaises(FileNotFoundError):
            self.manager.read_log_file("missing.log")
        outside = os.path.join(self.tmp.name, "pkg", "outside.log")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("secret\n")
        with self.assertRaises(ValueError):
            self.manager.read_log_file(os.path.join("..", "outside.log"))

    def test_read_log_file_reraises_read_errors(self):
        self.logger.info("something")
        with mock.patch("builtins.open", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.manager.read_log_file("prompt_manager.log")

    def test_unreadable_files_are_skipped_in_the_listing(self):
        self.logger.info("something")
        original_lstat = os.lstat

        def lstat_failing_for_logs(path, *args, **kwargs):
            if str(path).endswith(".log"):
                raise OSError("denied")
            return original_lstat(path, *args, **kwargs)

        with mock.patch.object(logging_config.os, "lstat", lstat_failing_for_logs):
            self.assertEqual(self.manager.get_log_files(), [])

    def test_truncate_does_not_roll_the_log_over_while_deleting_backups(self):
        # With a file limit smaller than one record, any log line written
        # while the backups are being deleted rolls the main log over and
        # resurrects prompt_manager.log.1. The handler's own stream must also
        # be reset, or its stale offset reports the old size, rolls over on the
        # next record anyway and pads the file with zeros on POSIX.
        self._rotate(max_file_size=400, backup_count=2, messages=10)
        result = self.manager.truncate_logs()
        self.assertEqual(result["errors"], [])
        self.logger.info("after truncate")
        leftovers = sorted(p.name for p in pathlib.Path(self.log_dir).iterdir())
        contents = {
            name: pathlib.Path(self.log_dir, name).read_bytes() for name in leftovers
        }
        self.assertEqual(leftovers, ["prompt_manager.log"], contents)
        main_log = pathlib.Path(self.log_dir, "prompt_manager.log").read_bytes()
        self.assertNotIn(b"\x00", main_log)
        self.assertIn(b"after truncate", main_log)

    def test_truncate_clears_main_log_backups_and_buffer(self):
        self._rotate()
        result = self.manager.truncate_logs()
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            sorted(result["truncated"]),
            ["prompt_manager.log", "prompt_manager.log.1", "prompt_manager.log.2"],
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.log_dir, "prompt_manager.log.1"))
        )
        # Only the "Truncated log file" lines written after clearing remain
        self.assertEqual(
            [
                r["message"]
                for r in self.manager.get_recent_logs()
                if "message 0" in r["message"]
            ],
            [],
        )

    def test_truncate_reports_files_it_cannot_remove(self):
        self._rotate()

        def busy(path, *args, **kwargs):
            raise PermissionError(13, "busy", str(path))

        with mock.patch.object(logging_config.os, "unlink", busy):
            result = self.manager.truncate_logs()
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["truncated"], ["prompt_manager.log"])
        for error in result["errors"]:
            self.assertNotIn(self.tmp.name, error)
            self.assertRegex(error, r"prompt_manager\.log\.[12]")


class TestRotatedCopyRule(IsolatedLoggerTestCase):
    """Rule R (#329): only prompt_manager.log.<n> regular files are deletable."""

    def setUp(self):
        super().setUp()
        self.logger.info("active log line")  # the active log exists
        self.outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(self.outside)

    def _path(self, name):
        return os.path.join(self.log_dir, name)

    def _write(self, name, data=b"rotated\n"):
        with open(self._path(name), "wb") as handle:
            handle.write(data)
        return self._path(name)

    def _symlink(self, name, target_name="secret.txt", data=b"secret " * 1000):
        """A symlink in the log folder to a file outside it, or None."""
        target = os.path.join(self.outside, target_name)
        with open(target, "wb") as handle:
            handle.write(data)
        try:
            os.symlink(target, self._path(name))
        except (OSError, NotImplementedError):  # pragma: no cover - no privilege
            return None
        return target

    def _require_symlink(self, name, **kwargs):
        target = self._symlink(name, **kwargs)
        if target is None:  # pragma: no cover - Windows without the privilege
            self.skipTest("this account cannot create symlinks")
        return target

    def _kinds(self):
        return {row["name"]: row["kind"] for row in self.manager.scan_log_folder()}

    def _rotated_rows(self):
        return [r for r in self.manager.scan_log_folder() if r["kind"] == "rotated"]

    def _names(self):
        return sorted(os.listdir(self.log_dir))

    def _file_handler(self):
        return next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )

    def test_scan_classes_rotated_active_link_and_other(self):
        for name in ("prompt_manager.log.1", "prompt_manager.log.2"):
            self._write(name)
        self._write("prompt_manager.log.3", b"x" * 3000)
        for name in (
            "prompt_manager.log.0",
            "prompt_manager.log.01",
            "prompt_manager.log.bak",
            "prompt_manager.log.1.gz",
            "prompt_manager.log.-1",
            "notes.txt",
        ):
            self._write(name)
        os.makedirs(self._path("prompt_manager.log.5"))
        self._require_symlink("prompt_manager.log.4")
        kinds = self._kinds()
        self.assertEqual(
            kinds,
            {
                "prompt_manager.log": "active",
                "prompt_manager.log.1": "rotated",
                "prompt_manager.log.2": "rotated",
                "prompt_manager.log.3": "rotated",
                "prompt_manager.log.0": "other",
                "prompt_manager.log.01": "other",
                "prompt_manager.log.bak": "other",
                "prompt_manager.log.1.gz": "other",
                "prompt_manager.log.-1": "other",
                "notes.txt": "other",
                "prompt_manager.log.5": "other",
                "prompt_manager.log.4": "link",
            },
        )
        row = next(
            r
            for r in self.manager.scan_log_folder()
            if r["name"] == "prompt_manager.log.3"
        )
        self.assertEqual(
            set(row), {"name", "kind", "size", "mtime_ns", "ino", "modified"}
        )
        stat = os.lstat(self._path("prompt_manager.log.3"))
        self.assertEqual(row["size"], 3000)
        self.assertEqual(row["mtime_ns"], stat.st_mtime_ns)
        self.assertEqual(row["ino"], stat.st_ino)

    def test_scan_matches_names_case_insensitively_on_windows(self):
        self._write("PROMPT_MANAGER.LOG.2")
        if os.name == "nt":  # pragma: no cover - native case folding
            self.assertEqual(self._kinds()["PROMPT_MANAGER.LOG.2"], "rotated")
            return
        # POSIX names are case-sensitive: another file, never deleted.
        self.assertEqual(self._kinds()["PROMPT_MANAGER.LOG.2"], "other")
        with mock.patch.object(logging_config, "_name_key", ntpath.normcase):
            self.assertEqual(self._kinds()["PROMPT_MANAGER.LOG.2"], "rotated")
            result = self.manager.delete_rotated(self._rotated_rows())
        self.assertEqual(result["removed"], ["PROMPT_MANAGER.LOG.2"])

    def test_a_junction_is_classed_as_a_link_and_kept(self):
        # Portable control: whatever os.path.isjunction reports is a link.
        self._write("prompt_manager.log.4")
        self._write("prompt_manager.log.1")
        junction = self._path("prompt_manager.log.4")
        real_isjunction = os.path.isjunction

        def isjunction(path):
            return os.fspath(path) == junction or real_isjunction(path)

        with mock.patch.object(logging_config.os.path, "isjunction", isjunction):
            self.assertEqual(self._kinds()["prompt_manager.log.4"], "link")
            self.manager.delete_rotated(self._rotated_rows())
            self.manager.truncate_logs()
        self.assertIn("prompt_manager.log.4", self._names())
        self.assertNotIn("prompt_manager.log.1", self._names())

    @unittest.skipUnless(os.name == "nt", "junctions exist only on Windows")
    def test_junction_is_never_deleted_on_windows(self):  # pragma: no cover
        import _winapi

        target = os.path.join(self.outside, "kept")
        os.makedirs(target)
        with open(os.path.join(target, "inside.txt"), "wb") as handle:
            handle.write(b"keep me")
        _winapi.CreateJunction(target, self._path("prompt_manager.log.4"))
        self._write("prompt_manager.log.1")
        self.assertEqual(self._kinds()["prompt_manager.log.4"], "link")
        self.manager.delete_rotated(self._rotated_rows())
        self.manager.truncate_logs()
        self.assertIn("prompt_manager.log.4", self._names())
        self.assertTrue(os.path.exists(os.path.join(target, "inside.txt")))
        with self.assertRaises(FileNotFoundError):
            self.manager.log_file_entry("prompt_manager.log.4")
        os.rmdir(self._path("prompt_manager.log.4"))  # removes the junction only

    def test_delete_rotated_removes_exactly_the_listed_files(self):
        for n in (1, 2, 3):
            self._write(f"prompt_manager.log.{n}", b"r" * n)
        self._write("prompt_manager.log.bak")
        os.makedirs(self._path("prompt_manager.log.7"))
        target = self._symlink("prompt_manager.log.4")
        listed = [
            r for r in self._rotated_rows() if r["name"] != "prompt_manager.log.3"
        ]
        result = self.manager.delete_rotated(listed)
        self.assertEqual(
            result,
            {"removed": ["prompt_manager.log.1", "prompt_manager.log.2"], "failed": []},
        )
        expected = [
            "prompt_manager.log",
            "prompt_manager.log.3",
            "prompt_manager.log.7",
            "prompt_manager.log.bak",
        ]
        if target is not None:
            expected.append("prompt_manager.log.4")
            self.assertTrue(os.path.exists(target))
        self.assertEqual(self._names(), sorted(expected))

    def test_delete_rotated_refuses_all_when_one_changed(self):
        def change_size(path):
            with open(path, "ab") as handle:
                handle.write(b"more")

        def change_mtime(path):
            stat = os.lstat(path)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

        def change_inode(path):
            stat = os.lstat(path)
            with open(path, "rb") as handle:
                data = handle.read()
            other = path + ".new"
            with open(other, "wb") as handle:
                handle.write(data)
            os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            os.replace(other, path)
            if os.lstat(path).st_ino in (0, stat.st_ino):  # pragma: no cover
                self.skipTest("this filesystem reports no inode numbers")

        def remove(path):
            os.unlink(path)

        def replace_by_link(path):
            os.unlink(path)
            target = os.path.join(self.outside, "elsewhere.txt")
            with open(target, "wb") as handle:
                handle.write(b"rotated\n")
            try:
                os.symlink(target, path)
            except (OSError, NotImplementedError):  # pragma: no cover
                self.skipTest("this account cannot create symlinks")

        changes = {
            "size": change_size,
            "mtime": change_mtime,
            "inode": change_inode,
            "missing": remove,
            "link": replace_by_link,
        }
        for label, change in changes.items():
            with self.subTest(change=label):
                for n in (1, 2, 3):
                    self._write(f"prompt_manager.log.{n}", b"rotated\n")
                reviewed = self._rotated_rows()
                change(self._path("prompt_manager.log.2"))
                before = self._names()
                with self.assertRaises(RotatedChanged):
                    self.manager.delete_rotated(reviewed)
                self.assertEqual(self._names(), before)
                self.assertIn("prompt_manager.log.1", before)
                self.assertIn("prompt_manager.log.3", before)
                for name in self._names():
                    if name.startswith("prompt_manager.log."):
                        os.unlink(self._path(name))

    def test_rollover_waits_while_cleanup_holds_the_handler_lock(self):
        # The file is already over its limit: the next record rolls it over,
        # renaming .1 to .2 and the active log to .1.
        self.manager.update_config({"max_file_size": 64, "backup_count": 3})
        self.logger.info("x" * 100)
        self._write("prompt_manager.log.1", b"first copy\n")
        self._write("prompt_manager.log.2", b"second copy\n")
        reviewed = self._rotated_rows()
        holding = threading.Event()
        release = threading.Event()
        logged = threading.Event()
        real_unlink = os.unlink
        self.allowed_thread_names.add("pm-test-rollover")

        def log_from_another_thread():
            self.logger.info("rolls the log over")
            logged.set()

        worker = threading.Thread(
            target=log_from_another_thread, name="pm-test-rollover", daemon=True
        )

        def slow_unlink(path, *args, **kwargs):
            if not holding.is_set():
                worker.start()  # its record must wait for the cleanup's lock
                holding.set()
                if not release.wait(10):
                    raise RuntimeError("the test never released the cleanup")
            return real_unlink(path, *args, **kwargs)

        result = {}

        def cleanup():
            result.update(self.manager.delete_rotated(reviewed))

        with mock.patch.object(logging_config.os, "unlink", slow_unlink):
            cleaner = threading.Thread(target=cleanup, daemon=True)
            cleaner.start()
            self.assertTrue(holding.wait(10), "the cleanup never started deleting")
            # The record waits on the handler lock the cleanup holds.
            self.assertFalse(logged.wait(0.3), "a record was written mid-cleanup")
            release.set()
            cleaner.join(10)
            worker.join(10)
        self.assertFalse(cleaner.is_alive())
        self.assertTrue(logged.is_set())
        self.assertEqual(
            result,
            {"removed": ["prompt_manager.log.1", "prompt_manager.log.2"], "failed": []},
        )
        # The rollover ran after the cleanup: only the old active log moved.
        self.assertEqual(self._names(), ["prompt_manager.log", "prompt_manager.log.1"])
        with open(self._path("prompt_manager.log.1"), "rb") as handle:
            self.assertIn(b"x" * 100, handle.read())

    def test_open_rotated_copy_is_reported_not_deleted(self):
        for n in (1, 2, 3):
            self._write(f"prompt_manager.log.{n}")
        real_unlink = os.unlink
        held = self._path("prompt_manager.log.2")

        def unlink(path, *args, **kwargs):
            if os.fspath(path) == held:
                error = PermissionError(
                    13,
                    "The process cannot access the file because it is being "
                    "used by another process",
                    held,
                )
                error.winerror = 32
                raise error
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(logging_config.os, "unlink", unlink):
            result = self.manager.delete_rotated(self._rotated_rows())
        self.assertEqual(
            result["removed"], ["prompt_manager.log.1", "prompt_manager.log.3"]
        )
        [(name, error)] = result["failed"]
        self.assertEqual(name, "prompt_manager.log.2")
        self.assertIsInstance(error, PermissionError)
        self.assertIn("prompt_manager.log.2", self._names())

    @unittest.skipUnless(os.name == "nt", "only Windows refuses to unlink an open file")
    def test_open_rotated_copy_is_reported_not_deleted_on_windows(
        self,
    ):  # pragma: no cover
        for n in (1, 2, 3):
            self._write(f"prompt_manager.log.{n}")
        reviewed = self._rotated_rows()
        handle = open(self._path("prompt_manager.log.2"), "rb")
        try:
            result = self.manager.delete_rotated(reviewed)
        finally:
            handle.close()
        self.assertEqual(
            result["removed"], ["prompt_manager.log.1", "prompt_manager.log.3"]
        )
        [(name, error)] = result["failed"]
        self.assertEqual(name, "prompt_manager.log.2")
        self.assertIsInstance(error, PermissionError)
        self.assertIn("prompt_manager.log.2", self._names())

    def test_cleanup_never_logs_while_holding_the_handler_lock(self):
        for n in (1, 2):
            self._write(f"prompt_manager.log.{n}")
        handler = self._file_handler()
        depth = 0
        calls = []
        real_acquire, real_release = handler.acquire, handler.release

        def acquire():
            nonlocal depth
            real_acquire()
            depth += 1

        def release():
            nonlocal depth
            depth -= 1
            real_release()

        real_call_handlers = logging.Logger.callHandlers

        def call_handlers(logger_self, record):
            calls.append((record.getMessage(), depth))
            return real_call_handlers(logger_self, record)

        with (
            mock.patch.object(handler, "acquire", acquire),
            mock.patch.object(handler, "release", release),
            mock.patch.object(logging.Logger, "callHandlers", call_handlers),
        ):
            self.manager.delete_rotated(self._rotated_rows())
            self.assertEqual(depth, 0)
            self._write("prompt_manager.log.1")
            self.manager.truncate_logs()
            self.assertEqual(depth, 0)
        self.assertTrue(calls, "the cleanup logged no summary")
        self.assertEqual([c for c in calls if c[1]], [])

    def test_truncate_keeps_other_files_and_links(self):
        # Corrected (#329): the legacy glob deleted every prompt_manager.log*.
        self._rotate()
        self._write("prompt_manager.log.bak", b"mine")
        self._write("prompt_manager.log.1.gz", b"zip")
        target = self._symlink("prompt_manager.log.4")
        result = self.manager.truncate_logs()
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            sorted(result["truncated"]),
            ["prompt_manager.log", "prompt_manager.log.1", "prompt_manager.log.2"],
        )
        names = self._names()
        self.assertIn("prompt_manager.log.bak", names)
        self.assertIn("prompt_manager.log.1.gz", names)
        self.assertNotIn("prompt_manager.log.1", names)
        if target is not None:
            self.assertIn("prompt_manager.log.4", names)
            self.assertTrue(os.path.islink(self._path("prompt_manager.log.4")))
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"secret " * 1000)

    def test_truncate_with_file_logging_off_never_creates_the_active_log(self):
        self.manager.update_config({"file_logging": False})
        os.unlink(self._path("prompt_manager.log"))
        self._write("prompt_manager.log.1")
        result = self.manager.truncate_logs()
        self.assertEqual(result, {"truncated": ["prompt_manager.log.1"], "errors": []})
        self.assertEqual(self._names(), [])

    def test_truncate_never_empties_a_linked_active_log(self):
        self.manager.update_config({"file_logging": False})
        os.unlink(self._path("prompt_manager.log"))
        target = self._require_symlink("prompt_manager.log", data=b"keep")
        self.manager.truncate_logs()
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"keep")

    def test_log_files_mark_kind_and_lstat_links(self):
        self._write("prompt_manager.log.1", b"r" * 10)
        self._write("prompt_manager.log.bak", b"b")
        target = self._require_symlink("prompt_manager.log.4")
        rows = {row["filename"]: row for row in self.manager.get_log_files()}
        self.assertEqual(rows["prompt_manager.log"]["kind"], "active")
        self.assertTrue(rows["prompt_manager.log"]["is_main"])
        self.assertEqual(rows["prompt_manager.log.1"]["kind"], "rotated")
        self.assertEqual(rows["prompt_manager.log.bak"]["kind"], "other")
        link = rows["prompt_manager.log.4"]
        self.assertEqual(link["kind"], "link")
        self.assertEqual(
            link["size"], os.lstat(self._path("prompt_manager.log.4")).st_size
        )
        self.assertNotEqual(link["size"], os.path.getsize(target))
        for row in rows.values():
            self.assertEqual(
                set(row), {"filename", "path", "size", "modified", "is_main", "kind"}
            )

    def test_stats_exclude_links(self):
        self._write("prompt_manager.log.1", b"r" * 10)
        before = self.manager.get_log_stats()
        self._require_symlink("prompt_manager.log.4")
        after = self.manager.get_log_stats()
        self.assertEqual(after["log_files_count"], before["log_files_count"])
        self.assertEqual(after["total_log_size"], before["total_log_size"])

    def test_log_file_entry_accepts_only_listed_regular_files(self):
        self._write("prompt_manager.log.1")
        self._write("prompt_manager.log.bak")
        os.makedirs(self._path("folder.log"))
        self._symlink("prompt_manager.log.4")
        self.assertEqual(
            os.path.normcase(str(self.manager.log_file_entry("prompt_manager.log.1"))),
            os.path.normcase(self._path("prompt_manager.log.1")),
        )
        self.manager.log_file_entry("prompt_manager.log")
        self.manager.log_file_entry("prompt_manager.log.bak")
        for name in (
            "prompt_manager.log.4",
            "folder.log",
            "missing.log",
            "",
            ".",
            "..",
            "../outside/secret.txt",
            "..\\outside\\secret.txt",
            "prompt_manager.log:stream",
            "prompt_manager.log\x00",
        ):
            with self.subTest(name=name):
                with self.assertRaises(FileNotFoundError):
                    self.manager.log_file_entry(name)


class TestConfiguration(IsolatedLoggerTestCase):
    def test_in_flight_record_cannot_reopen_retired_file_handler(self) -> None:
        handler = next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )
        stream = handler.stream
        selected = threading.Event()
        resume = threading.Event()
        errors = []
        message = "in-flight record selected before file logging disabled"

        def pause_before_handler_lock(record: logging.LogRecord) -> bool:
            if record.getMessage() == message:
                selected.set()
                if not resume.wait(5):
                    raise RuntimeError("logging thread was not released")
            return True

        def log_from_background_thread() -> None:
            try:
                self.manager.logger.info(message)
            except BaseException as error:
                errors.append(error)

        # Allow this record through the fixture's normal main-thread filter.
        handler.filters.clear()
        handler.addFilter(pause_before_handler_lock)
        worker = threading.Thread(target=log_from_background_thread, daemon=True)
        worker.start()
        try:
            self.assertTrue(selected.wait(5), "logger did not select the file handler")
            self.assertFalse(stream.closed)
            self.manager.update_config({"file_logging": False})
            self.assertNotIn(handler, self.manager.logger.handlers)
            self.assertTrue(stream.closed)
            self.assertIsNone(handler.stream)

            resume.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "logging thread did not finish")
            self.assertEqual(errors, [])
            self.assertIsNone(handler.stream)
            self.assertTrue(stream.closed)
            self.assertFalse(self.manager.config["file_logging"])
            self.assertFalse(
                any(
                    isinstance(h, logging.FileHandler)
                    for h in self.manager.logger.handlers
                )
            )
            self.assertNotIn(
                message,
                pathlib.Path(self.log_dir, "prompt_manager.log").read_text(),
            )
        finally:
            resume.set()
            worker.join(5)
            handler.close()

    def test_rebuild_closes_replaced_file_stream(self) -> None:
        old_handler = next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )
        stream = old_handler.stream
        self.addCleanup(old_handler.close)
        self.assertFalse(stream.closed)

        self.manager.update_config({"file_logging": False})

        self.assertTrue(stream.closed)
        self.assertIsNone(old_handler.stream)
        self.assertNotIn(old_handler, self.manager.logger.handlers)
        log_file = pathlib.Path(self.log_dir, "prompt_manager.log")
        renamed = log_file.rename(log_file.with_suffix(".retired"))
        renamed.unlink()
        self.assertFalse(renamed.exists())

    def test_repeated_rebuilds_retire_previous_handlers(self) -> None:
        for change in (
            {"max_file_size": 4096},
            {"backup_count": 3},
            {"console_logging": True},
            {"console_logging": False},
        ):
            with self.subTest(change=change):
                previous = self.manager.logger.handlers[:]
                for handler in previous:
                    self.addCleanup(handler.close)
                old_file = next(
                    h for h in previous if isinstance(h, logging.FileHandler)
                )
                stream = old_file.stream
                self.assertFalse(stream.closed)
                console_streams = [
                    h.stream for h in previous if type(h) is logging.StreamHandler
                ]

                self.manager.update_config(change)

                self.assertTrue(stream.closed)
                self.assertIsNone(old_file.stream)
                self.assertTrue(all(h._closed for h in previous))
                self.assertTrue(all(not s.closed for s in console_streams))
                self.assertTrue(
                    all(h not in self.manager.logger.handlers for h in previous)
                )
                current_files = [
                    h
                    for h in self.manager.logger.handlers
                    if isinstance(h, logging.FileHandler)
                ]
                self.assertEqual(len(current_files), 1)
                self.assertFalse(current_files[0].stream.closed)
                message = f"usable replacement after {change}"
                self.logger.info(message)
                self.assertIn(
                    message,
                    pathlib.Path(self.log_dir, "prompt_manager.log").read_text(),
                )
                self.assertEqual(
                    self.manager.get_recent_logs(limit=1)[0]["message"], message
                )

    def test_rebuild_does_not_close_external_handler(self) -> None:
        external_path = pathlib.Path(self.tmp.name, "external.log")
        external_handler = logging.FileHandler(
            external_path, mode="w", encoding="utf-8"
        )
        self.addCleanup(external_handler.close)
        other_logger = logging.Logger("external_shared_sink", level=logging.INFO)
        other_logger.addHandler(external_handler)
        self.addCleanup(other_logger.removeHandler, external_handler)
        self.manager.logger.addHandler(external_handler)

        with mock.patch.object(
            external_handler, "close", wraps=external_handler.close
        ) as close:
            self.manager.update_config({"backup_count": 3})
            close.assert_not_called()

        self.assertNotIn(external_handler, self.manager.logger.handlers)
        self.assertFalse(external_handler.stream.closed)
        other_logger.info("external sink still usable")
        self.assertIn("external sink still usable", external_path.read_text())

    def test_level_only_change_keeps_current_file_handler(self) -> None:
        handler = next(
            h
            for h in self.manager.logger.handlers
            if isinstance(h, logging.FileHandler)
        )
        stream = handler.stream

        self.manager.update_config({"level": "DEBUG"})

        self.assertIn(handler, self.manager.logger.handlers)
        self.assertIs(handler.stream, stream)
        self.assertFalse(stream.closed)
        self.logger.debug("same sink still usable")
        self.assertIn(
            "same sink still usable",
            pathlib.Path(self.log_dir, "prompt_manager.log").read_text(),
        )

    def test_replacement_manager_closes_previous_owned_handlers(self) -> None:
        previous = self.manager.logger.handlers[:]
        for handler in previous:
            self.addCleanup(handler.close)
        old_file = next(h for h in previous if isinstance(h, logging.FileHandler))
        stream = old_file.stream
        self.assertFalse(stream.closed)
        fake_file = pathlib.Path(self.tmp.name, "pkg", "utils", "logging_config.py")

        with mock.patch.object(logging_config, "__file__", str(fake_file)):
            replacement = object.__new__(PromptManagerLogger)
            replacement.__init__()

        self.assertIs(replacement.logger, self.manager.logger)
        self.assertTrue(stream.closed)
        self.assertIsNone(old_file.stream)
        self.assertTrue(all(h._closed for h in previous))
        self._filter_to_this_thread()
        self.logger.info("replacement manager sink")
        self.assertEqual(
            replacement.get_recent_logs(limit=1)[0]["message"],
            "replacement manager sink",
        )

    def test_level_change_applies_to_every_prompt_manager_logger(self):
        self.manager.update_config({"level": "DEBUG"})
        self.assertEqual(logging.getLogger("prompt_manager").level, logging.DEBUG)
        self.assertEqual(
            logging.getLogger("prompt_manager.database").level, logging.DEBUG
        )
        self.assertEqual(self.manager.get_config()["level"], "DEBUG")
        self.logger.debug("now visible")
        self.assertEqual(self.manager.get_recent_logs(limit=1)[0]["level"], "DEBUG")
        self.manager.update_config({"level": "bogus"})  # falls back to INFO
        self.assertEqual(logging.getLogger("prompt_manager").level, logging.INFO)

    def test_get_config_returns_a_copy(self):
        config = self.manager.get_config()
        config["level"] = "CRITICAL"
        self.assertEqual(self.manager.get_config()["level"], "INFO")

    def test_handler_flags_rebuild_the_handlers(self):
        root = logging.getLogger("prompt_manager")
        self.assertFalse(any(type(h) is logging.StreamHandler for h in root.handlers))
        self.assertTrue(any(isinstance(h, logging.FileHandler) for h in root.handlers))
        self.manager.update_config({"console_logging": True, "file_logging": False})
        self.assertTrue(any(type(h) is logging.StreamHandler for h in root.handlers))
        self.assertFalse(any(isinstance(h, logging.FileHandler) for h in root.handlers))
        self.assertTrue(any(isinstance(h, MemoryBufferHandler) for h in root.handlers))
        self.manager.update_config({"console_logging": False})


class TestConsoleNoise(IsolatedLoggerTestCase):
    """ComfyUI's console only sees warnings unless the level is DEBUG."""

    def _console_handler(self) -> logging.StreamHandler:
        return next(
            h
            for h in logging.getLogger("prompt_manager").handlers
            if type(h) is logging.StreamHandler
        )

    def test_records_do_not_propagate_to_comfyui_root_logger(self) -> None:
        self.assertFalse(logging.getLogger("prompt_manager").propagate)

    def test_console_hides_info_at_the_default_level(self) -> None:
        self.manager.update_config({"console_logging": True})
        self.assertEqual(self._console_handler().level, logging.WARNING)
        self.logger.info("startup chatter")
        self.assertEqual(self.manager.get_recent_logs(limit=1)[0]["level"], "INFO")
        log_file = pathlib.Path(self.log_dir) / "prompt_manager.log"
        self.assertIn("startup chatter", log_file.read_text())
        self.manager.update_config({"console_logging": False})

    def test_debug_level_opens_the_console(self) -> None:
        self.manager.update_config({"console_logging": True, "level": "DEBUG"})
        self.assertEqual(self._console_handler().level, logging.DEBUG)
        self.manager.update_config({"level": "INFO"})
        self.assertEqual(self._console_handler().level, logging.WARNING)
        self.manager.update_config({"console_logging": False})


if __name__ == "__main__":
    unittest.main()
