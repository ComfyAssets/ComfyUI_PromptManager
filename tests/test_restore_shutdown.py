"""A restore cut off by a server stop waits for its worker thread (#360).

A cancelled handler (or job) task cannot stop the executor thread that
stages or publishes. Its cleanup (the stage, the upload) and the registry's
``restore`` admission must outlive that thread, and the live library ends as
the old or the restored one, never partial, with the safety backup kept.

Every positive wait is on an event. The one bounded wait (``SETTLE``) is a
negative check: the cancelled task must still be pending while its worker is
held.
"""

import asyncio
import os
import shutil
import sqlite3
import threading
from unittest.mock import patch

from database import restore_stage
from database.models import PromptModel
from py.api import admin as admin_mod
from py.api.jobs import RESTORE_JOB_KIND

try:
    from tests.test_restore_plans import RestoreTestCase, _texts
except ImportError:  # discovered with tests/ as the top-level directory
    from test_restore_plans import RestoreTestCase, _texts

WAIT = 60  # a ceiling only a hung test reaches
SETTLE = 0.3  # the negative check's bound: the old code's cleanup takes ms


class _Gate:
    """Hold one blocking call on its worker thread until released."""

    def __init__(self, real):
        self.real = real
        self.entered = threading.Event()
        self.release = threading.Event()
        self.done = threading.Event()

    def method(self):
        """A function to patch onto the class (bound like the real one)."""

        def held(*args):
            return self(*args)

        return held

    def __call__(self, *args):
        self.entered.set()
        self.release.wait(WAIT)
        try:
            return self.real(*args)
        finally:
            self.done.set()


class ShutdownRestoreCase(RestoreTestCase):
    def gate(self, real):
        gate = _Gate(real)
        # A failed check never leaves the worker held until WAIT.
        self._gates = getattr(self, "_gates", []) + [gate]
        self.addCleanup(gate.release.set)
        return gate

    def stages(self):
        """The stage folders (the upload's prefix starts the same way)."""
        return [
            name
            for name in super().stages()
            if os.path.isdir(os.path.join(self.tmpdir, name))
        ]

    def uploads(self):
        """The upload files (not the -wal/-shm its readers may add)."""
        return sorted(
            name
            for name in os.listdir(self.tmpdir)
            if name.startswith(restore_stage.UPLOAD_PREFIX) and name.endswith(".tmp")
        )

    def fake_upload(self, texts):
        """Make ``_receive_restore_upload`` hand over a prepared upload."""
        self.upload(texts, "source.db")
        source = os.path.join(self.tmpdir, "uploads", "source.db")
        target = os.path.join(self.tmpdir, restore_stage.UPLOAD_PREFIX + "x.tmp")
        shutil.copyfile(source, target)

        async def receive(request, uploads):
            uploads.append(target)
            return target, None

        return patch.object(self.api, "_receive_restore_upload", receive)

    async def wait_thread(self, event):
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, event.wait, WAIT))

    async def assert_still_pending(self, task):
        done, _ = await asyncio.wait({task}, timeout=SETTLE)
        if done:
            for gate in getattr(self, "_gates", ()):
                gate.release.set()  # let the worker end before teardown
            self.fail("the cancelled task ended while its worker still ran")


class TestLegacyRestoreAtShutdown(ShutdownRestoreCase):
    async def test_cancel_during_publish_waits_for_the_worker(self):
        self.save("old")
        old_db = self.api.db
        gate = self.gate(PromptModel.publish_staged)
        with (
            self.fake_upload(["restored"]),
            patch.object(PromptModel, "publish_staged", gate.method()),
        ):
            task = asyncio.ensure_future(self.api.restore_database(None))
            await self.wait_thread(gate.entered)
            self.assertEqual(len(self.stages()), 1)
            task.cancel()
            await self.assert_still_pending(task)
            # The worker still has its stage and upload, and runs alone.
            self.assertEqual(len(self.stages()), 1)
            self.assertEqual(len(self.uploads()), 1)
            self.assertIsNotNone(self.api._jobs.active(RESTORE_JOB_KIND))
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT)
        self.assertTrue(gate.done.is_set())
        self.assertEqual(_texts(self.db_path), ["restored"])
        self.assertIsNot(self.api.db, old_db)  # the restored library adopted
        self.assertEqual(len(self.backups()), 1)  # the safety backup is kept
        self.assertEqual(self.stages(), [])
        self.assertEqual(self.uploads(), [])
        self.assertIsNone(self.api._jobs.active(RESTORE_JOB_KIND))

    async def test_cancel_during_staging_waits_and_changes_nothing(self):
        self.save("old")
        gate = self.gate(PromptModel.stage_restore)
        with (
            self.fake_upload(["restored"]),
            patch.object(PromptModel, "stage_restore", gate.method()),
        ):
            task = asyncio.ensure_future(self.api.restore_database(None))
            await self.wait_thread(gate.entered)
            task.cancel()
            await self.assert_still_pending(task)
            self.assertEqual(len(self.uploads()), 1)  # still being read
            self.assertIsNotNone(self.api._jobs.active(RESTORE_JOB_KIND))
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT)
        self.assertEqual(_texts(self.db_path), ["old"])
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.stages(), [])  # the finished stage is removed
        self.assertEqual(self.uploads(), [])
        self.assertIsNone(self.api._jobs.active(RESTORE_JOB_KIND))

    async def test_a_publish_that_fails_after_the_cancel_still_cancels(self):
        self.save("old")

        def refuse(model, staged):
            raise sqlite3.OperationalError("refused")

        gate = self.gate(refuse)
        with (
            self.fake_upload(["restored"]),
            patch.object(PromptModel, "publish_staged", gate.method()),
        ):
            task = asyncio.ensure_future(self.api.restore_database(None))
            await self.wait_thread(gate.entered)
            task.cancel()
            await self.assert_still_pending(task)
            gate.release.set()
            # Not answered as a 500: the cancel goes on.
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT)
        self.assertEqual(_texts(self.db_path), ["old"])
        self.assertEqual(self.stages(), [])
        self.assertEqual(self.uploads(), [])
        self.assertIsNone(self.api._jobs.active(RESTORE_JOB_KIND))

    async def test_a_second_cancel_still_adopts_the_restored_library(self):
        self.save("old")
        old_db = self.api.db
        publish = self.gate(PromptModel.publish_staged)
        prune = self.gate(admin_mod._prune_safety_backups)
        with (
            self.fake_upload(["restored"]),
            patch.object(PromptModel, "publish_staged", publish.method()),
            patch.object(admin_mod, "_prune_safety_backups", prune),
        ):
            task = asyncio.ensure_future(self.api.restore_database(None))
            await self.wait_thread(publish.entered)
            task.cancel()
            await self.assert_still_pending(task)
            publish.release.set()
            await self.wait_thread(prune.entered)  # adopting the library
            task.cancel()  # the loop's own shutdown cancels again
            await self.assert_still_pending(task)
            prune.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT)
        self.assertEqual(_texts(self.db_path), ["restored"])
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.stages(), [])
        self.assertIsNone(self.api._jobs.active(RESTORE_JOB_KIND))


class TestReviewedRestoreAtShutdown(ShutdownRestoreCase):
    async def test_cancelled_job_waits_for_the_publish(self):
        self.save("old")
        old_db = self.api.db
        body = await self.preview(self.upload(["restored"]))
        gate = self.gate(PromptModel.publish_staged)
        with patch.object(PromptModel, "publish_staged", gate.method()):
            started = await self.apply(body["token"])
            job = self.api._jobs.get(started["job"]["id"])
            await self.wait_thread(gate.entered)
            job.task.cancel()
            await self.assert_still_pending(job.task)
            self.assertEqual(len(self.stages()), 1)
            self.assertTrue(job.running)  # its exclusive admission holds
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(job.task, WAIT)
        self.assertEqual(_texts(self.db_path), ["restored"])
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.stages(), [])
        self.assertFalse(job.running)
