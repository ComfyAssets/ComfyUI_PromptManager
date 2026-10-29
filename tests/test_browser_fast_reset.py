"""Browser-test resets seed a scratch copy and publish it in one commit (#384)."""

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from tests.browser import fast_reset
from tests.browser.fast_reset import ScratchSeeder, table_digests

# The owner lock of /dev/shm scratch folders needs POSIX flock (#419).
HAS_FLOCK = unittest.skipUnless(os.name == "posix", "needs POSIX flock")


def digests(db: PromptDatabase) -> dict:
    return table_digests(db.model.get_connection())


def add_prompt(db: PromptDatabase, text: str) -> int:
    with db.model.get_connection() as conn:
        return conn.execute(
            "INSERT INTO prompts (text, hash, created_at, updated_at) "
            "VALUES (?, ?, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (text, hashlib.sha256(text.encode()).hexdigest()),
        ).lastrowid


def add_image(db: PromptDatabase, prompt_id: int, name: str) -> int:
    with db.model.get_connection() as conn:
        return conn.execute(
            "INSERT INTO generated_images (prompt_id, image_path, filename, "
            "generation_time) VALUES (?, ?, ?, '2026-01-01 00:00:00')",
            (prompt_id, f"/out/{name}", name),
        ).lastrowid


def reseed(api) -> str:
    """A seeder in the old style: clears its tables, then many small commits."""
    db = api.db
    with db.model.get_connection() as conn:
        for table in ("generated_images", "prompt_tags", "tags", "prompts"):
            conn.execute(f"DELETE FROM {table}")
    for index in range(30):
        prompt_id = add_prompt(db, f"seeded {index}")
        add_image(db, prompt_id, f"seeded-{index}.png")
        with db.model.get_connection() as conn:
            tag_id = conn.execute(
                "INSERT INTO tags (name) VALUES (?)", (f"tag{index}",)
            ).lastrowid
            conn.execute(
                "INSERT INTO prompt_tags (prompt_id, tag_id) VALUES (?, ?)",
                (prompt_id, tag_id),
            )
    return "manifest"


class TestScratchSeeder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.live = self.database("live.db")
        self.api = types.SimpleNamespace(db=self.live, marker="live api")
        self.seeder = ScratchSeeder(self.api, self.root / "scratch", memory_root=None)
        self.addCleanup(self.seeder.close)
        # Rows from "earlier tests": AUTOINCREMENT positions have moved on.
        for index in range(5):
            add_image(self.live, add_prompt(self.live, f"old {index}"), f"o{index}")
        with self.live.model.get_connection() as conn:
            conn.execute("DELETE FROM prompts WHERE text = 'old 4'")

    def database(self, name: str) -> PromptDatabase:
        db = PromptDatabase(str(self.root / name))
        self.addCleanup(db.close_all)
        return db

    def oracle(self, seed):
        """Run ``seed`` the old way, on a copy of the live database."""
        path = self.root / "oracle.db"
        target = sqlite3.connect(path)
        try:
            self.live.model.get_connection().backup(target)
        finally:
            target.close()
        db = self.database("oracle.db")
        seed(types.SimpleNamespace(db=db))
        return digests(db)

    def test_publishes_exactly_what_the_seed_writes_on_live(self):
        expected = self.oracle(reseed)
        self.assertEqual(self.seeder.run(reseed), "manifest")
        self.assertEqual(digests(self.live), expected)
        with self.live.model.get_connection() as conn:
            first = conn.execute("SELECT MIN(id) FROM prompts").fetchone()[0]
        self.assertEqual(first, 6, "AUTOINCREMENT continues as on live")

    def test_runs_twice_from_the_state_the_first_run_left(self):
        self.seeder.run(reseed)
        expected = self.oracle(reseed)
        self.seeder.run(reseed)
        self.assertEqual(digests(self.live), expected)

    def test_tables_the_seed_did_not_change_keep_their_rows(self):
        before = digests(self.live)

        def rename(api):
            with api.db.model.get_connection() as conn:
                conn.execute("UPDATE prompts SET text = 'renamed' WHERE id = 1")

        self.seeder.run(rename)
        after = digests(self.live)
        changed = {table for table in after if after[table] != before[table]}
        self.assertEqual(changed, {"prompts"})
        with self.live.model.get_connection() as conn:
            images = conn.execute("SELECT COUNT(*) FROM generated_images").fetchone()
        # Replacing prompts' rows never cascades into generated_images.
        self.assertEqual(images[0], 4)

    def test_the_seed_sees_the_scratch_database_and_the_live_api(self):
        seen = {}

        def seed(api):
            seen["db"] = api.db
            seen["marker"] = api.marker
            api.note = "set by the seed"

        self.seeder.run(seed)
        self.assertIsNot(seen["db"], self.live)
        self.assertNotEqual(seen["db"].model.db_path, self.live.model.db_path)
        self.assertEqual(seen["marker"], "live api")
        self.assertEqual(self.api.note, "set by the seed")
        self.assertIs(self.api.db, self.live)

    def test_cache_drops_wait_until_the_rows_are_published(self):
        seen = []

        def invalidate_gallery_cache(retire=False):
            with self.live.model.get_connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            seen.append((count, retire))

        self.api.invalidate_gallery_cache = invalidate_gallery_cache

        def seed(api):
            reseed(api)
            api.invalidate_gallery_cache(retire=True)
            self.assertEqual(seen, [], "not dropped while live has the old rows")

        self.seeder.run(seed)
        self.assertEqual(seen, [(30, True)])

    def test_a_failing_seed_drops_no_cache(self):
        seen = []
        self.api.invalidate_gallery_cache = lambda: seen.append(True)

        def broken(api):
            api.invalidate_gallery_cache()
            raise ValueError("seed failed")

        with self.assertRaises(ValueError):
            self.seeder.run(broken)
        self.assertEqual(seen, [])

    def test_a_failing_seed_leaves_live_untouched(self):
        before = digests(self.live)

        def broken(api):
            reseed(api)
            raise ValueError("seed failed")

        with self.assertRaisesRegex(ValueError, "seed failed"):
            self.seeder.run(broken)
        self.assertEqual(digests(self.live), before)
        self.assertIs(self.api.db, self.live)

    def test_a_concurrent_live_commit_fails_the_reset(self):
        other = self.database("live.db")

        def racing(api):
            reseed(api)
            add_prompt(other, "written to live meanwhile")

        with self.assertRaisesRegex(RuntimeError, "changed while"):
            self.seeder.run(racing)
        with self.live.model.get_connection() as conn:
            texts = [row[0] for row in conn.execute("SELECT text FROM prompts")]
        self.assertIn("written to live meanwhile", texts)
        self.assertNotIn("seeded 0", texts)

    def test_a_write_open_during_the_seed_and_committed_later_fails_it(self):
        other = self.database("live.db")
        began, finished = threading.Event(), []

        def write():
            with other.model.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO prompts (text, hash) VALUES ('concurrent', 'c')"
                )
                began.set()
                time.sleep(0.5)  # the publish waits on this lock meanwhile
            other.model.close()
            finished.append(True)

        writer = threading.Thread(target=write)

        def racing(api):
            reseed(api)
            writer.start()
            self.assertTrue(began.wait(10))

        with self.assertRaisesRegex(RuntimeError, "changed while"):
            self.seeder.run(racing)
        writer.join()
        self.assertEqual(finished, [True])
        with self.live.model.get_connection() as conn:
            texts = [row[0] for row in conn.execute("SELECT text FROM prompts")]
        self.assertIn("concurrent", texts)
        self.assertNotIn("seeded 0", texts)
        self.assert_live_connection_restored()

    def test_a_failed_publish_leaves_live_and_its_connection_as_they_were(self):
        before = digests(self.live)
        columns = fast_reset._columns

        def broken_columns(conn, table, *schema):
            # The publish asks for the live table's columns without a schema.
            return columns(conn, table, *schema) if schema else ["no_such_column"]

        with mock.patch.object(fast_reset, "_columns", broken_columns):
            with self.assertRaises(sqlite3.OperationalError):
                self.seeder.run(reseed)
        self.assertEqual(digests(self.live), before)
        self.assert_live_connection_restored()

    def assert_live_connection_restored(self):
        conn = self.live.model.get_connection()
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        names = [row[1] for row in conn.execute("PRAGMA database_list")]
        self.assertEqual(names, ["main"])

    def test_the_live_connection_is_left_as_it_was(self):
        self.seeder.run(reseed)
        conn = self.live.model.get_connection()
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        names = [row[1] for row in conn.execute("PRAGMA database_list")]
        self.assertEqual(names, ["main"])
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_on_live_mode_runs_the_seed_on_the_live_api(self):
        seeder = ScratchSeeder(
            self.api, self.root / "unused", on_live=True, memory_root=self.root
        )
        self.addCleanup(seeder.close)
        expected = self.oracle(reseed)
        seen = []
        self.assertEqual(
            seeder.run(lambda api: seen.append(api) or reseed(api)), "manifest"
        )
        self.assertIs(seen[0], self.api)
        self.assertEqual(digests(self.live), expected)
        self.assertFalse((self.root / "unused").exists())

    @HAS_FLOCK
    def test_the_scratch_lives_under_the_memory_root_when_there_is_one(self):
        memory = self.root / "memory"
        memory.mkdir()
        seeder = ScratchSeeder(self.api, self.root / "fallback", memory_root=memory)
        self.addCleanup(seeder.close)
        expected = self.oracle(reseed)
        seeder.run(reseed)
        self.assertEqual(digests(self.live), expected)
        self.assertEqual(len(list(memory.glob("pm-reset-*/seed.db"))), 1)
        self.assertFalse((self.root / "fallback").exists())
        seeder.close()
        self.assertEqual(list(memory.iterdir()), [])

    @unittest.skipIf(os.name == "posix", "without flock the memory root is unused")
    def test_without_flock_the_scratch_stays_on_disk(self):
        memory = self.root / "memory"
        memory.mkdir()
        seeder = ScratchSeeder(self.api, self.root / "fallback", memory_root=memory)
        self.addCleanup(seeder.close)
        seeder.run(reseed)
        self.assertEqual(list(memory.iterdir()), [])
        self.assertTrue((self.root / "fallback" / "seed.db").exists())

    def test_a_full_memory_root_puts_the_scratch_on_disk(self):
        memory = self.root / "memory"
        memory.mkdir()
        seeder = ScratchSeeder(self.api, self.root / "fallback", memory_root=memory)
        self.addCleanup(seeder.close)
        full = mock.patch.object(
            fast_reset.shutil,
            "disk_usage",
            return_value=types.SimpleNamespace(free=fast_reset.MEMORY_FREE_BYTES - 1),
        )
        with full:
            seeder.run(reseed)
        self.assertEqual(list(memory.iterdir()), [])
        self.assertTrue((self.root / "fallback" / "seed.db").exists())

    @HAS_FLOCK
    def test_the_scratch_folder_is_held_by_a_lock_until_close(self):
        memory = self.root / "memory"
        memory.mkdir()
        seeder = ScratchSeeder(self.api, self.root / "fallback", memory_root=memory)
        self.addCleanup(seeder.close)
        seeder.run(reseed)
        (folder,) = memory.iterdir()
        self.assertTrue(folder.name.startswith(f"pm-reset-held-{os.getpid()}-"))
        self.assertFalse(lock_is_free(folder / "owner.lock"))
        seeder.close()
        self.assertEqual(list(memory.iterdir()), [])

    @HAS_FLOCK
    def test_a_lock_that_cannot_be_taken_puts_the_scratch_on_disk(self):
        memory = self.root / "memory"
        memory.mkdir()
        seeder = ScratchSeeder(self.api, self.root / "fallback", memory_root=memory)
        self.addCleanup(seeder.close)
        refuse = mock.patch.object(
            fast_reset.fcntl, "flock", side_effect=OSError("no locks here")
        )
        with refuse:
            seeder.run(reseed)
        self.assertEqual(list(memory.iterdir()), [])
        self.assertTrue((self.root / "fallback" / "seed.db").exists())

    def test_close_releases_the_scratch_files(self):
        self.seeder.run(reseed)
        self.seeder.close()
        assert_closed(self, self.root / "scratch")
        self.seeder.close()  # idempotent


# A process that takes the owner lock of a folder, says so, and waits.
HOLDER = """
import fcntl, os, sys, time
fd = os.open(os.path.join(sys.argv[1], "owner.lock"), os.O_RDWR | os.O_CREAT)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(600)
"""


def lock_is_free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR)
    try:
        fast_reset.fcntl.flock(fd, fast_reset.fcntl.LOCK_EX | fast_reset.fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


@HAS_FLOCK
class TestOrphanedScratch(unittest.TestCase):
    """Only folders whose owner lock is free are orphans (#419)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Path(self.tmp.name)

    def folder(self, name: str) -> Path:
        folder = self.memory / name
        folder.mkdir()
        (folder / "seed.db").write_bytes(b"x")
        return folder

    def hold(self, folder: Path) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(folder)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        return holder

    def test_a_folder_whose_owner_was_killed_is_removed(self):
        folder = self.folder("pm-reset-held-123-abc")
        holder = self.hold(folder)
        holder.kill()
        holder.wait()
        fast_reset.remove_orphaned_scratch(self.memory)
        self.assertFalse(folder.exists())

    def test_a_held_folder_is_kept_whatever_pid_its_name_carries(self):
        # Under --ipc=host the owner's pid is from another PID namespace: it
        # does not exist here, but its lock does.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        folder = self.folder(f"pm-reset-held-{dead.pid}-abc")
        self.hold(folder)
        fast_reset.remove_orphaned_scratch(self.memory)
        self.assertTrue(folder.exists())
        self.assertTrue((folder / "seed.db").exists())

    def test_folders_without_an_owner_lock_are_kept(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        legacy = self.folder(f"pm-reset-{dead.pid}-abc")
        staging = self.folder(f"pm-reset-new-{dead.pid}-def")
        unlocked = self.folder(f"pm-reset-held-{dead.pid}-ghi")
        fast_reset.remove_orphaned_scratch(self.memory)
        for folder in (legacy, staging, unlocked):
            self.assertTrue(folder.exists(), folder.name)

    def test_a_folder_held_by_another_seeder_in_this_process_is_kept(self):
        api = types.SimpleNamespace(db=PromptDatabase(str(self.memory / "live.db")))
        self.addCleanup(api.db.close_all)
        root = self.memory / "shm"
        root.mkdir()
        other = ScratchSeeder(api, self.memory / "fallback", memory_root=root)
        self.addCleanup(other.close)
        other.run(reseed)
        (folder,) = root.iterdir()
        fast_reset.remove_orphaned_scratch(root)
        self.assertTrue(folder.exists())


if __name__ == "__main__":
    unittest.main()
