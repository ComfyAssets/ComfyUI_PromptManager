"""Move library (#249 S6b): copy, verify, switch, then retire the old files.

Each test builds a node-folder library (a real PromptDatabase with prompts,
images and workflow documents, a config.json holding a key, a pooled safety
backup and the pre-migration backup) and a ComfyUI user folder in a temp
directory, then moves it with ``database.library_move``. Failures and crash
windows are injected through the move's ``checkpoint`` hook: an ``Exception``
is a failure the move cleans up after, ``SimulatedCrash`` (a BaseException)
stops it dead like a killed process.
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import library_move as lm  # noqa: E402
from database.execution_history import (  # noqa: E402
    notify_history_restore,
    subscribe_history_restore,
)
from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from database.review_changes import (  # noqa: E402
    notify_review_change,
    subscribe_review_changes,
)
from tests.open_handles import assert_closed, open_handles_under  # noqa: E402
from utils import library_location as ll  # noqa: E402

KEY = "civitai-secret-key"
POOL = "prompts.db.backup_20261001_101010_000001"
PRE = "prompts.db.pre-workflow-storage_20261002_101010_000002"


class SimulatedCrash(BaseException):
    """The process died here: nothing after this point runs."""


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _rows(path):
    """Every table's rows of ``path`` (read-only), to compare libraries."""
    uri = "file:" + path.replace(os.sep, "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                repr(tuple(row)) for row in conn.execute(f'SELECT * FROM "{table}"')
            )
            for table in tables
        }
    finally:
        conn.close()


def _workflow(seed):
    return {
        "nodes": [
            {"id": n, "type": f"Node{seed}", "widgets": [seed, n]} for n in range(30)
        ]
    }


class _MoveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self._tmp.name)
        self.comfy = os.path.join(self.base, "ComfyUI")
        self.ext = os.path.join(self.comfy, "custom_nodes", "ComfyUI_PromptManager")
        os.makedirs(self.ext)
        self.fp = types.SimpleNamespace()
        for name in ("output", "input", "temp", "user"):
            folder = os.path.join(self.comfy, name)
            os.makedirs(folder)
            setattr(self.fp, f"get_{name}_directory", lambda f=folder: f)
        self.target = os.path.join(self.comfy, "user", "__promptmanager")
        self.old_db = os.path.join(self.ext, "prompts.db")
        self.new_db = os.path.join(self.target, "prompts.db")
        ll.clear_override()
        self.db = PromptDatabase(self.old_db)
        for index in range(3):
            prompt_id = self.db.save_prompt(f"a cat number {index}", category="cats")
            self.db.link_image_to_prompt(
                str(prompt_id),
                os.path.join(self.comfy, "output", f"cat{index}.png"),
                {"workflow": _workflow(index % 2), "prompt": {"1": {"seed": index}}},
            )
        self.db.model.close_all()
        self.config = os.path.join(self.ext, "config.json")
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump(
                {"integrations": {"lora_manager": {"civitai_api_key": KEY}}}, handle
            )
        for name, stamp in ((POOL, 1_700_000_000), (PRE, 1_700_000_100)):
            path = os.path.join(self.ext, name)
            source = sqlite3.connect(self.old_db)
            copy = sqlite3.connect(path)
            try:
                source.backup(copy)
            finally:
                copy.close()
                source.close()
            os.utime(path, (stamp, stamp))
        self.before = {
            "rows": _rows(self.old_db),
            "config": _digest(self.config),
            "pool": _digest(os.path.join(self.ext, POOL)),
            "pre": _digest(os.path.join(self.ext, PRE)),
        }
        self.config_lock = threading.RLock()

    def tearDown(self):
        import gc

        gc.collect()
        PromptModel.close_all_instances()
        ll.clear_override()
        try:
            assert_closed(self, self.ext, self.target)
        finally:
            self._tmp.cleanup()

    def location(self, **kwargs):
        return ll.resolve_library(
            None, environ={}, extension_root=self.ext, folder_paths=self.fp, **kwargs
        )

    def move(self, **kwargs):
        plan = lm.plan_move(self.location())
        kwargs.setdefault("config_lock", self.config_lock)
        return lm.move_library(plan, **kwargs)

    def assert_old_library_untouched(self):
        self.assertTrue(os.path.isfile(self.old_db))
        self.assertEqual(_rows(self.old_db), self.before["rows"])
        self.assertEqual(_digest(self.config), self.before["config"])
        self.assertEqual(_digest(os.path.join(self.ext, POOL)), self.before["pool"])
        self.assertEqual(_digest(os.path.join(self.ext, PRE)), self.before["pre"])
        self.assertEqual(
            os.path.normcase(self.db.model.db_path), os.path.normcase(self.old_db)
        )
        self.assertEqual(self.location().source, ll.SOURCE_NODE_FOLDER)

    def assert_target_clean(self):
        leftovers = os.listdir(self.target) if os.path.isdir(self.target) else []
        self.assertEqual(leftovers, [])


class TestMove(_MoveCase):
    def test_move_copies_verifies_switches_and_retires_the_old_files(self):
        result = self.move()

        self.assertEqual(_rows(self.new_db), self.before["rows"])
        self.assertEqual(result.counts["prompts"], 3)
        self.assertEqual(result.counts["generated_images"], 3)
        self.assertEqual(
            result.counts["workflow_documents"], 5
        )  # 2 workflows, 3 graphs
        self.assertEqual(
            sorted(result.files), sorted(["config.json", POOL, PRE, "prompts.db"])
        )
        self.assertIsNone(result.retire_problem)
        # The live database object now reads and writes the new file.
        self.assertEqual(self.db.model.db_path, self.new_db)
        self.db.save_prompt("written after the move")
        self.assertEqual(len(_rows(self.new_db)["prompts"]), 4)
        image = self.db.get_prompt_images(
            str(self.db.search_prompts(text="number 1")[0]["id"])
        )[0]
        self.assertEqual(image["workflow_data"], _workflow(1))
        # config.json, the pooled backup and the pre-migration backup: same bytes.
        self.assertEqual(
            _digest(os.path.join(self.target, "config.json")), self.before["config"]
        )
        self.assertEqual(_digest(os.path.join(self.target, POOL)), self.before["pool"])
        self.assertEqual(_digest(os.path.join(self.target, PRE)), self.before["pre"])
        self.assertEqual(
            int(os.path.getmtime(os.path.join(self.target, POOL))), 1_700_000_000
        )
        # The old files are renamed, never deleted; the backups stay.
        renamed = sorted(os.listdir(self.ext))
        self.assertIn("prompts.db.moved-to-__promptmanager", renamed)
        self.assertIn("config.json.moved-to-__promptmanager", renamed)
        self.assertNotIn("prompts.db", renamed)
        self.assertNotIn("config.json", renamed)
        self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in renamed))
        self.assertEqual(
            _rows(os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")),
            self.before["rows"],
        )
        self.assertEqual(ll.read_marker(self.target)["state"], ll.MARKER_MOVED)

    def test_new_files_are_private(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions")
        self.move()
        self.assertEqual(os.stat(self.target).st_mode & 0o777, 0o700)
        for name in ("prompts.db", "config.json", POOL, PRE, ll.MARKER_NAME):
            mode = os.stat(os.path.join(self.target, name)).st_mode & 0o777
            self.assertEqual(mode, 0o600, name)

    def test_restart_uses_the_new_library_and_never_the_renamed_file(self):
        self.move()
        self.assertEqual(self.location().db_path, self.new_db)
        ll.clear_override()
        restarted = self.location()
        self.assertEqual(restarted.source, ll.SOURCE_DEFAULT)
        self.assertEqual(restarted.db_path, self.new_db)
        self.assertEqual(
            restarted.config_path, os.path.join(self.target, "config.json")
        )

    def test_every_open_database_object_is_repointed(self):
        node = PromptDatabase(self.old_db)
        monitor = PromptDatabase(self.old_db)
        monitor.get_prompt_by_id(1)
        other = PromptDatabase(os.path.join(self.base, "other.db"))
        self.move()
        self.assertEqual(node.model.db_path, self.new_db)
        self.assertEqual(monitor.model.db_path, self.new_db)
        self.assertEqual(monitor.get_prompt_by_id(1)["text"], "a cat number 0")
        self.assertEqual(other.model.db_path, os.path.join(self.base, "other.db"))
        assert_closed(
            self, os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
        )

    def test_change_observers_follow_the_library(self):
        reviews, restores = [], []
        unsubscribe_review = subscribe_review_changes(
            self.old_db, lambda: reviews.append(1)
        )
        unsubscribe_restore = subscribe_history_restore(
            self.old_db, lambda: restores.append(1)
        )
        self.addCleanup(unsubscribe_review)
        self.addCleanup(unsubscribe_restore)
        self.move()
        self.assertEqual(restores, [1])  # the history readers start a new epoch
        notify_review_change(self.new_db)
        notify_history_restore(self.new_db)
        self.assertEqual(reviews, [1])
        self.assertEqual(restores, [1, 1])

    def test_files_are_only_renamed_inside_one_folder(self):
        real_replace, real_rename = os.replace, os.rename
        moves = []

        def replace(src, dst, *args, **kwargs):
            moves.append((src, dst))
            return real_replace(src, dst, *args, **kwargs)

        def rename(src, dst, *args, **kwargs):
            moves.append((src, dst))
            return real_rename(src, dst, *args, **kwargs)

        with (
            mock.patch.object(lm.os, "replace", replace),
            mock.patch.object(lm.os, "rename", rename),
        ):
            self.move()
        self.assertTrue(moves)
        for src, dst in moves:
            self.assertEqual(os.path.dirname(src), os.path.dirname(dst), (src, dst))

    def test_a_write_during_the_copy_is_copied_again(self):
        writes = []

        def checkpoint(step):
            if step == "files" and not writes:
                writes.append(PromptDatabase(self.old_db).save_prompt("late write"))

        progress = []
        self.move(checkpoint=checkpoint, progress=progress.append)
        self.assertIn("late write", repr(_rows(self.new_db)["prompts"]))
        self.assertTrue(any("changed" in line for line in progress), progress)

    def test_a_library_that_keeps_changing_is_not_moved(self):
        def checkpoint(step):
            if step == "files":
                PromptDatabase(self.old_db).save_prompt(f"write {os.urandom(4).hex()}")

        with self.assertRaises(lm.MoveFailed) as caught:
            self.move(checkpoint=checkpoint, attempts=2)
        self.assertEqual(caught.exception.code, "library-busy")
        self.assertEqual(self.location().source, ll.SOURCE_NODE_FOLDER)
        self.assert_target_clean()

    def test_old_file_still_open_elsewhere_is_left_in_place(self):
        reader = sqlite3.connect(self.old_db)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM prompts").fetchone()
        PromptDatabase(self.old_db).save_prompt("in the wal")
        result = self.move()
        self.assertEqual(result.retire_problem, "old-in-use")
        self.assertTrue(os.path.isfile(self.old_db))
        self.assertEqual(self.db.model.db_path, self.new_db)
        reader.rollback()
        reader.close()
        ll.clear_override()
        self.assertEqual(self.location().source, ll.SOURCE_DEFAULT)


class TestFailures(_MoveCase):
    def _fail_at(self, step):
        def checkpoint(name):
            if name == step:
                raise RuntimeError(f"failure at {step}")

        return checkpoint

    def test_a_failure_at_any_step_leaves_the_old_library_in_use_and_untouched(self):
        for step in ("copied", "verified", "files", "locked", "pending", "published"):
            with self.subTest(step=step):
                with self.assertRaises(RuntimeError):
                    self.move(checkpoint=self._fail_at(step))
                self.assert_old_library_untouched()
                self.assert_target_clean()
                assert_closed(self, self.target)

    def test_a_copy_that_does_not_verify_is_refused(self):
        def checkpoint(step):
            if step == "copied":
                (temp,) = [
                    name for name in os.listdir(self.target) if name.endswith(".db")
                ]
                with open(os.path.join(self.target, temp), "r+b") as handle:
                    handle.truncate(4096)

        with self.assertRaises(lm.MoveFailed) as caught:
            self.move(checkpoint=checkpoint)
        self.assertEqual(caught.exception.code, "verify-failed")
        self.assertNotIn(self.base, caught.exception.message)
        self.assert_old_library_untouched()
        self.assert_target_clean()

    def test_a_copied_file_that_differs_is_refused(self):
        def checkpoint(step):
            if step == "files":
                for name in os.listdir(self.target):
                    if name.endswith(".bak"):
                        with open(os.path.join(self.target, name), "ab") as handle:
                            handle.write(b"x")

        with self.assertRaises(lm.MoveFailed) as caught:
            self.move(checkpoint=checkpoint)
        self.assertEqual(caught.exception.code, "copy-failed")
        self.assert_old_library_untouched()
        self.assert_target_clean()

    def test_not_movable_locations_are_refused(self):
        os.makedirs(self.target)
        with open(self.new_db, "wb") as handle:
            handle.write(b"someone else's library")
        with self.assertRaises(lm.MoveFailed) as caught:
            lm.plan_move(self.location())
        self.assertEqual(caught.exception.code, "target-has-library")
        os.remove(self.new_db)
        default = ll.LibraryLocation(
            ll.SOURCE_DEFAULT, self.new_db, "c", self.target, self.target
        )
        with self.assertRaises(lm.MoveFailed) as caught:
            lm.plan_move(default)
        self.assertEqual(caught.exception.code, "not-movable")

    def test_target_that_is_the_node_folder_is_refused(self):
        link = os.path.join(self.base, "link-to-node-folder")
        try:
            os.symlink(self.ext, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        location = ll.LibraryLocation(
            ll.SOURCE_NODE_FOLDER, self.old_db, self.config, self.ext, link
        )
        with self.assertRaises(lm.MoveFailed) as caught:
            lm.plan_move(location)
        self.assertEqual(caught.exception.code, "same-folder")

    def test_disk_check_measures_the_target_volume(self):
        measured = []

        def free(path):
            measured.append(path)
            return 1024

        with mock.patch.object(lm, "_free_bytes", free):
            plan = lm.plan_move(self.location())
            with self.assertRaises(lm.MoveFailed) as caught:
                lm.move_library(plan, config_lock=self.config_lock)
        self.assertEqual(caught.exception.code, "insufficient-space")
        self.assertTrue(measured)
        for path in measured:
            self.assertTrue(
                os.path.normcase(self.comfy) in os.path.normcase(path)
                and "custom_nodes" not in path,
                path,
            )
        self.assertGreater(plan.required_bytes, os.path.getsize(self.old_db))
        self.assert_old_library_untouched()


class TestCrashWindows(_MoveCase):
    """A crash between steps never loses the library or picks a stale one."""

    def _crash_at(self, step):
        def checkpoint(name):
            if name == step:
                raise SimulatedCrash(step)

        return checkpoint

    def _restart(self):
        PromptModel.close_all_instances()
        ll.clear_override()
        return self.location()

    def test_crash_before_the_switch_keeps_the_old_library_and_a_retry_succeeds(self):
        for step in ("copied", "files", "pending", "published"):
            with self.subTest(step=step):
                with self.assertRaises(SimulatedCrash):
                    self.move(checkpoint=self._crash_at(step))
                restarted = self._restart()
                self.assertEqual(restarted.source, ll.SOURCE_NODE_FOLDER)
                self.assertEqual(restarted.db_path, self.old_db)
                self.assertEqual(_rows(self.old_db), self.before["rows"])
        result = self.move()
        self.assertEqual(_rows(self.new_db), self.before["rows"])
        self.assertIsNone(result.retire_problem)
        self.assertFalse(
            [name for name in os.listdir(self.target) if name.startswith(".pm-move-")]
        )

    def test_crash_after_the_marker_uses_the_new_library(self):
        for step in ("marked", "switched"):
            with self.subTest(step=step):
                with self.assertRaises(SimulatedCrash):
                    self.move(checkpoint=self._crash_at(step))
                restarted = self._restart()
                self.assertEqual(restarted.source, ll.SOURCE_DEFAULT)
                self.assertEqual(restarted.db_path, self.new_db)
                self.assertEqual(_rows(self.new_db), self.before["rows"])
                # The old file was not renamed yet and is never picked.
                self.assertTrue(os.path.isfile(self.old_db))
                # Reset for the next window: back to the node folder only.
                for name in os.listdir(self.target):
                    os.remove(os.path.join(self.target, name))

    def test_crash_after_the_rename(self):
        with self.assertRaises(SimulatedCrash):
            self.move(checkpoint=self._crash_at("retired"))
        restarted = self._restart()
        self.assertEqual(restarted.db_path, self.new_db)
        self.assertFalse(os.path.exists(self.old_db))


class TestEnvironmentTarget(_MoveCase):
    def test_move_into_the_environment_folder(self):
        data = os.path.join(self.base, "pm-data")
        environ = {ll.DATA_DIR_ENV: data}
        location = ll.resolve_library(
            None, environ=environ, extension_root=self.ext, folder_paths=self.fp
        )
        self.assertEqual(location.problem, "data-dir-empty")
        result = lm.move_library(lm.plan_move(location), config_lock=self.config_lock)
        self.assertEqual(result.location.source, ll.SOURCE_ENVIRONMENT)
        self.assertEqual(_rows(os.path.join(data, "prompts.db")), self.before["rows"])
        ll.clear_override()
        self.assertEqual(
            ll.resolve_library(
                None, environ=environ, extension_root=self.ext, folder_paths=self.fp
            ).source,
            ll.SOURCE_ENVIRONMENT,
        )
        self.assertEqual(open_handles_under(self.old_db), [])


if __name__ == "__main__":
    unittest.main()
