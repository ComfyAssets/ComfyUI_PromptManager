"""PromptManagerLogger: memory buffer, file rotation, level changes, truncation."""

import logging
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import logging_config
from utils.logging_config import (
    MemoryBufferHandler,
    PromptManagerLogger,
    get_logger,
    get_logger_manager,
)


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
        self.manager.update_config({"console_logging": False})
        # Drop the "Updated logging configuration" entry.
        self.manager._log_buffer.clear()
        # Other tests leave daemon threads (image monitor, watchdog) logging under
        # "prompt_manager.*"; only records from this test's thread may reach the buffer.
        # The same goes for the rotating file handler: a stray line after a
        # truncate would roll the file over again and resurrect the backups.
        this_thread = threading.get_ident()
        for handler in self.manager.logger.handlers:
            handler.addFilter(lambda record: record.thread == this_thread)
        self.logger = logging.getLogger("prompt_manager.coverage_test")
        self.addCleanup(self._restore_global_logging)

    def _restore_global_logging(self):
        for handler in logging.getLogger("prompt_manager").handlers[:]:
            handler.close()
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
        original_stat = pathlib.Path.stat

        def stat_failing_for_logs(path, *args, **kwargs):
            if path.suffix == ".log":
                raise OSError("denied")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "stat", autospec=True) as stat:
            stat.side_effect = stat_failing_for_logs
            self.assertEqual(self.manager.get_log_files(), [])

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
        with mock.patch.object(pathlib.Path, "unlink", side_effect=OSError("busy")):
            result = self.manager.truncate_logs()
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["truncated"], ["prompt_manager.log"])


class TestConfiguration(IsolatedLoggerTestCase):
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


if __name__ == "__main__":
    unittest.main()
