"""Move library, final review round (#249 S6b PR #318).

- I1: a switch that fails at any point is undone while both files are still
  locked: the copy is removed, the old library stays in use and a restart
  keeps it. A follow-up after a complete switch only logs.
- I2: a background write (run counting, the image monitor) that fails
  because Move library switched runs once more, on the new library; a write
  racing the switch on a real connection (a run count, an image link) lands
  in the new library only.
- The switching thread never closes a connection another thread is inside
  a statement on (a use-after-free in SQLite: "Windows fatal exception:
  access violation"); it interrupts it and the owner closes it.
- M2: the copy is verified through a file URI that survives ``#`` and ``%``.
- M6: a restart after a crash between the switch and the rename renames the
  old files, never deletes them.
"""

import contextlib
import json
import os
import sqlite3
import sys
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import library_move as lm  # noqa: E402
from database import models  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from tests.background_stops import close_handler  # noqa: E402
from tests.open_handles import assert_closed  # noqa: E402
from utils import library_location as ll  # noqa: E402
from utils import usage_tracking  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.test_library_move import KEY, SimulatedCrash, _MoveCase, _rows
except ImportError:  # discovered with tests/ as the top-level directory
    from test_library_move import KEY, SimulatedCrash, _MoveCase, _rows

WAIT = 30


def _run_count(path, text):
    uri = "file:" + path.replace(os.sep, "/") + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        row = conn.execute("SELECT run_count FROM prompts WHERE text=?", (text,))
        return row.fetchone()[0]


class _CrossThreadCloseProbe:
    """Record every ``close()`` this thread calls on a connection in use elsewhere.

    The writer marks its connection with ``using`` when its statement starts.
    Closing that connection from the switching thread while the writer is
    inside the statement frees memory SQLite is still using; on Windows the
    process dies with an access violation, on Linux the writer sees garbage.
    """

    def __init__(self):
        self.in_use = set()
        self.violations = []

    def using(self, conn):
        self.in_use.add(conn)

    def _profile(self, _frame, event, arg):
        if event != "c_call" or getattr(arg, "__name__", "") != "close":
            return
        target = getattr(arg, "__self__", None)
        if isinstance(target, sqlite3.Connection) and target in self.in_use:
            self.violations.append(threading.current_thread().name)

    @contextlib.contextmanager
    def watching(self):
        previous = sys.getprofile()
        sys.setprofile(self._profile)
        try:
            yield self
        finally:
            sys.setprofile(previous)


class TestFailedSwitchIsUndone(_MoveCase):
    """I1: whatever part of the switch fails, nothing was moved."""

    def _assert_not_moved(self, caught):
        self.assertEqual(caught.exception.code, "switch-failed")
        self.assertIn("Nothing was moved", caught.exception.message)
        self.assertIsNone(ll.current_override())
        self.assertEqual(self.db.model.db_path, self.old_db)
        # Writes keep landing in the old library, and a restart keeps it.
        self.db.save_prompt("written after the failed switch")
        self.assertIn("written after the failed switch", repr(_rows(self.old_db)))
        self.assertEqual(self.location().source, ll.SOURCE_NODE_FOLDER)
        self.assertNotEqual(ll.read_marker(self.target).get("state"), "moved")
        self.assert_target_clean()
        self.assertTrue(os.path.isfile(self.config))
        assert_closed(self, self.target)

    def _failing(self, point):
        real_switch_to = ll.switch_to
        real_repoint = models.PromptModel.repoint_instances

        def switch(old_db, location, detached):
            if point == "before":
                raise RuntimeError("switch failed before anything")
            if point == "resolver":
                real_switch_to(location)
                raise RuntimeError("switch failed after the resolver")
            if point == "repoint":
                real_switch_to(location)
                detached.extend(real_repoint(old_db, location.db_path))
                raise RuntimeError("switch failed after the models")
            lm.switch_process(old_db, location, detached)
            raise RuntimeError("switch failed at its very end")

        return switch

    def test_a_switch_failing_at_any_point_is_undone(self):
        node = PromptDatabase(self.old_db)
        node.get_prompt_by_id(1)
        for point in ("before", "resolver", "repoint", "complete"):
            with self.subTest(point=point):
                with self.assertRaises(lm.MoveFailed) as caught:
                    self.move(switch=self._failing(point))
                self._assert_not_moved(caught)
                self.assertEqual(node.model.db_path, self.old_db)
                self.assertEqual(node.get_prompt_by_id(1)["text"], "a cat number 0")

    def test_a_failed_switch_closes_the_old_connections_it_detached(self):
        node = PromptDatabase(self.old_db)
        for point in ("repoint", "complete"):
            with self.subTest(point=point):
                before = node.model.get_connection()
                with self.assertRaises(lm.MoveFailed):
                    self.move(switch=self._failing(point))
                with self.assertRaises(sqlite3.ProgrammingError):
                    before.execute("SELECT 1")
                # The model reopens the old library on next use.
                self.assertEqual(node.get_prompt_by_id(1)["text"], "a cat number 0")

    def test_a_failing_follow_up_after_the_switch_only_logs(self):
        def follow_up():
            raise RuntimeError("the health report could not be dropped")

        with self.assertLogs("prompt_manager.database.library_move", "ERROR"):
            result = self.move(after_switch=follow_up)
        self.assertIsNone(result.retire_problem)
        self.assertEqual(self.db.model.db_path, self.new_db)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
            )
        )


class _Flaky:
    """A write that fails once as if Move library switched while it waited."""

    def __init__(self, db, name):
        self.db, self.name, self.calls = db, name, 0
        self.real = getattr(db, name)

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.db.model.close_all()  # what the switch does: a new generation
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return self.real(*args, **kwargs)


class TestBackgroundWritesSurviveTheSwitch(_MoveCase):
    """I2: run counting and the image monitor write again on a fresh connection."""

    def test_run_counting_retries_once_after_a_switch(self):
        db = PromptDatabase(self.old_db)
        text = "a counted prompt"
        db.save_prompt(text, prompt_hash=generate_prompt_hash(text))
        flaky = _Flaky(db, "record_prompt_use")
        with mock.patch.object(db, "record_prompt_use", flaky):
            pending = usage_tracking.PendingFirstUse()
            usage_tracking.count_runs([(generate_prompt_hash(text), text)], db, pending)
        self.assertEqual(flaky.calls, 2)
        self.assertEqual(_run_count(self.old_db, text), 1)

    def test_a_failure_without_a_switch_is_not_retried(self):
        db = PromptDatabase(self.old_db)
        calls = []

        def broken():
            calls.append(1)
            raise sqlite3.OperationalError("disk I/O error")

        with self.assertRaises(sqlite3.OperationalError):
            db.survive_library_switch(broken)
        self.assertEqual(calls, [1])

    def test_an_image_link_racing_the_switch_lands_in_the_new_library(self):
        from utils.image_monitor import ImageGenerationHandler

        self.db.model.close_all()
        image = os.path.join(self.comfy, "output", "late.png")
        writer_db = PromptDatabase(self.old_db)
        handler = ImageGenerationHandler(writer_db, mock.MagicMock())
        self.addCleanup(close_handler, self, handler)
        started = threading.Event()
        probe = _CrossThreadCloseProbe()
        outcome = {}

        def write():
            conn = writer_db.model.get_connection()

            def trace(sql):
                if sql.lstrip().upper().startswith(("BEGIN", "INSERT")):
                    probe.using(conn)
                    started.set()

            conn.set_trace_callback(trace)
            try:
                # No metadata: the fallback link, not the resolved pair.
                handler.link_image_to_prompt(image, {"id": 1}, {})
                outcome["ok"] = True
            except Exception as error:  # pragma: no cover - reported below
                outcome["error"] = error
            finally:
                writer_db.model.close()

        thread = threading.Thread(target=write)

        def checkpoint(step):
            if step == "locked":
                # The old file is write-locked now: start the writer and
                # wait until its first write is running (it then waits on us).
                thread.start()
                self.assertTrue(started.wait(WAIT))

        with probe.watching():
            result = self.move(checkpoint=checkpoint)
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(probe.violations, [])
        self.assertEqual(outcome, {"ok": True})
        self.assertIsNone(result.retire_problem)
        moved = [
            row
            for row in _rows(self.new_db)["generated_images"]
            if "late.png" in repr(row)
        ]
        self.assertEqual(len(moved), 1)
        renamed = os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
        self.assertNotIn("late.png", repr(_rows(renamed).get("generated_images")))

    def test_an_image_that_was_not_linked_is_not_reported_as_linked(self):
        from utils.image_monitor import ImageGenerationHandler

        db = PromptDatabase(self.old_db)
        handler = ImageGenerationHandler(db, mock.MagicMock())
        self.addCleanup(close_handler, self, handler)
        image = os.path.join(self.comfy, "output", "orphan.png")
        with mock.patch.object(handler, "logger") as log:
            handler.link_image_to_prompt(image, {"id": 99999}, {})
        messages = [str(call) for call in log.debug.call_args_list]
        self.assertFalse([m for m in messages if "Successfully linked" in m])
        self.assertNotIn("orphan.png", repr(_rows(self.old_db)["generated_images"]))

    def test_a_write_racing_the_switch_lands_in_the_new_library(self):
        text = "a racing prompt"
        self.db.save_prompt(text, prompt_hash=generate_prompt_hash(text))
        self.db.model.close_all()
        writer_db = PromptDatabase(self.old_db)
        thread, started, probe, outcome = self._racing_run_count(writer_db, text)

        def checkpoint(step):
            if step == "locked":
                # The old file is write-locked now: start the writer and
                # wait until its UPDATE is running (it then waits on us).
                thread.start()
                self.assertTrue(started.wait(WAIT))

        with probe.watching():
            result = self.move(checkpoint=checkpoint)
        # The writer's old connection was closed before the old file was
        # renamed: Windows refuses to rename an open file.
        self.assertIsNone(result.retire_problem)
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(probe.violations, [])
        self.assertEqual(outcome, {"ok": True})
        self.assertEqual(_run_count(self.new_db, text), 1)
        renamed = os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
        self.assertEqual(_run_count(renamed, text), 0)
        assert_closed(self, renamed)

    def test_a_write_racing_a_failed_switch_lands_in_the_old_library(self):
        text = "a prompt racing a failed switch"
        self.db.save_prompt(text, prompt_hash=generate_prompt_hash(text))
        self.db.model.close_all()
        writer_db = PromptDatabase(self.old_db)
        thread, started, probe, outcome = self._racing_run_count(writer_db, text)

        def checkpoint(step):
            if step == "locked":
                thread.start()
                self.assertTrue(started.wait(WAIT))

        def switch(old_db, location, detached):
            lm.switch_process(old_db, location, detached)
            raise RuntimeError("switch failed at its very end")

        with probe.watching():
            with self.assertRaises(lm.MoveFailed):
                self.move(checkpoint=checkpoint, switch=switch)
        # The copy was removed: no connection was left open on it.
        self.assert_target_clean()
        assert_closed(self, self.target)
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(probe.violations, [])
        self.assertEqual(outcome, {"ok": True})
        self.assertEqual(_run_count(self.old_db, text), 1)

    def test_a_retire_failure_after_a_complete_switch_does_not_fail_the_move(self):
        node = PromptDatabase(self.old_db)
        node.get_prompt_by_id(1)
        real = models.PromptModel.retire_detached
        calls = []

        def failing(connections, logger=None):
            calls.append(len(connections))
            real(connections, logger)  # closed, but the call still fails
            raise RuntimeError("retiring failed")

        with mock.patch.object(
            models.PromptModel, "retire_detached", staticmethod(failing)
        ):
            with self.assertLogs("prompt_manager.database.library_move", "ERROR"):
                result = self.move()
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], 0)
        self.assertEqual(node.model.db_path, self.new_db)
        self.assertEqual(result.location.db_path, self.new_db)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
            )
        )

    @staticmethod
    def _racing_run_count(writer_db, text):
        """A run count whose UPDATE marks its connection in use for the probe."""
        started = threading.Event()
        probe = _CrossThreadCloseProbe()
        outcome = {}

        def write():
            conn = writer_db.model.get_connection()

            def trace(sql):
                if sql.startswith("UPDATE"):
                    probe.using(conn)
                    started.set()

            conn.set_trace_callback(trace)
            try:
                usage_tracking.count_runs(
                    [(generate_prompt_hash(text), text)],
                    writer_db,
                    usage_tracking.PendingFirstUse(),
                )
                outcome["ok"] = True
            except Exception as error:  # pragma: no cover - reported below
                outcome["error"] = error
            finally:
                writer_db.model.close()

        return threading.Thread(target=write), started, probe, outcome


class TestVerifyUri(unittest.TestCase):
    """M2: a copy whose path holds ``#`` and ``%`` verifies."""

    def test_copy_path_with_uri_characters_verifies(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "lib #1 %20")
            os.makedirs(folder)
            path = os.path.join(folder, "copy.db")
            db = PromptDatabase(path)
            db.save_prompt("a prompt")
            db.model.close_all()
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.execute("PRAGMA journal_mode=DELETE")
                counts, documents = lm._describe(conn)
            lm._verify_copy(path, counts, documents)
            models.PromptModel.close_all_instances()


class TestRestartFinishesTheRename(_MoveCase):
    """M6: the marker names the old files; a restart renames them."""

    def _crash_after_switch(self):
        def checkpoint(step):
            if step == "switched":
                raise SimulatedCrash(step)

        with self.assertRaises(SimulatedCrash):
            self.move(checkpoint=checkpoint)
        models.PromptModel.close_all_instances()
        ll.clear_override()

    def test_a_restart_renames_the_old_files_never_deletes(self):
        self._crash_after_switch()
        self.assertTrue(os.path.isfile(self.old_db))
        restarted = self.location()
        self.assertEqual(restarted.db_path, self.new_db)
        marker = ll.read_marker(self.target)
        self.assertEqual(
            (marker["from"], marker["config_from"]), (self.old_db, self.config)
        )
        renamed = lm.finish_interrupted_move(restarted)
        self.assertEqual(
            sorted(renamed),
            [
                "config.json.moved-to-__promptmanager",
                "prompts.db.moved-to-__promptmanager",
            ],
        )
        self.assertFalse(os.path.exists(self.old_db))
        moved_config = os.path.join(self.ext, "config.json.moved-to-__promptmanager")
        with open(moved_config, encoding="utf-8") as handle:
            self.assertEqual(
                json.load(handle)["integrations"]["lora_manager"]["civitai_api_key"],
                KEY,
            )
        self.assertEqual(lm.finish_interrupted_move(self.location()), [])

    def test_nothing_is_renamed_while_the_old_file_is_in_use(self):
        self._crash_after_switch()
        reader = sqlite3.connect(self.old_db)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM prompts").fetchone()
            PromptDatabase(self.old_db).save_prompt("in the wal")
            self.assertEqual(lm.finish_interrupted_move(self.location()), [])
            self.assertTrue(os.path.isfile(self.old_db))
        finally:
            reader.rollback()
            reader.close()
            models.PromptModel.close_all_instances()

    def test_a_node_folder_library_has_nothing_to_finish(self):
        self.assertEqual(lm.finish_interrupted_move(self.location()), [])
        self.assertTrue(os.path.isfile(self.old_db))


if __name__ == "__main__":
    unittest.main()
