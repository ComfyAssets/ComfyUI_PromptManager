"""Registry leases: short request-scoped work versus a database switch (#249 S8).

A lease is refused while an ``exclusive`` job (restore, Move library,
Optimize) runs, never refuses another lease or any job, is not listed, and an
exclusive job can await the leases already running. Ordering is driven by
events only.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.jobs import JobBusy, JobRegistry  # noqa: E402


class TestLeases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = JobRegistry()

    def start(self, kind, admission):
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        job = self.registry.start(
            kind, kind.title(), admission=admission, scope={}, runner=runner
        )
        return job, gate

    async def test_leases_share_and_are_not_listed(self):
        scan, gate = self.start("scan", "records")
        async with self.registry.lease("Needs review decision"):
            async with self.registry.lease("Needs review decision"):
                self.assertEqual(self.registry.leases, {"Needs review decision": 2})
                # A lease never refuses a job, even an exclusive one.
                self.assertIsNone(self.registry.blocker("health", admission="read"))
        self.assertEqual(self.registry.leases, {})
        self.assertEqual([job.kind for job in self.registry.list()], ["scan"])
        gate.set()
        await scan.task

    async def test_lease_refused_while_exclusive_job_runs(self):
        restore, gate = self.start("restore", "exclusive")
        with self.assertRaises(JobBusy) as refused:
            async with self.registry.lease("Maintenance preview"):
                self.fail("admitted beside a restore")
        self.assertIs(refused.exception.running, restore)
        self.assertEqual(self.registry.leases, {})
        gate.set()
        await restore.task
        async with self.registry.lease("Maintenance preview"):
            pass

    async def test_exclusive_job_awaits_running_leases(self):
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def decision():
            async with self.registry.lease("Needs review decision"):
                entered.set()
                await release.wait()
                order.append("decision done")

        task = asyncio.ensure_future(decision())
        await entered.wait()
        drained = asyncio.ensure_future(self.registry.drain_leases())
        await asyncio.sleep(0)
        self.assertFalse(drained.done())
        release.set()
        self.assertTrue(await drained)
        order.append("drained")
        await task
        self.assertEqual(order, ["decision done", "drained"])
        self.assertTrue(await self.registry.drain_leases())  # none: at once

    async def test_drain_reports_a_lease_that_outlives_the_ceiling(self):
        hold = self.registry.lease("Stuck preview")
        await hold.__aenter__()
        try:
            with self.assertLogs("py.api.jobs", "WARNING") as logs:
                self.assertFalse(await self.registry.drain_leases(timeout=0))
            self.assertIn("Stuck preview", "\n".join(logs.output))
            self.assertEqual(self.registry._drain_waiters, [])
        finally:
            await hold.__aexit__(None, None, None)
        self.assertEqual(self.registry.leases, {})

    async def test_a_failed_body_still_releases_the_lease(self):
        with self.assertRaises(RuntimeError):
            async with self.registry.lease("Needs review decision"):
                raise RuntimeError("boom")
        self.assertEqual(self.registry.leases, {})


if __name__ == "__main__":
    unittest.main()
