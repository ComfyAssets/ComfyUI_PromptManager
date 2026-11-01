"""Unit tests for py/api/jobs.py: the in-memory session job registry.

Fake runners only: no files, no database. Ordering is driven by events and
awaited tasks, never by wall-clock bounds.
"""

import asyncio
import contextlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import jobs as jobs_module  # noqa: E402
from py.api.jobs import (  # noqa: E402
    DEFAULT_SCAN_SCOPE,
    LOG_CAP_LINE,
    MAX_FINISHED_JOBS,
    MAX_LOG_LINES,
    MAX_OUTCOME_ROWS,
    SCAN_GROUP_NAMES,
    SCAN_GROUPS,
    SCAN_SCOPES,
    Job,
    JobBusy,
    JobRegistry,
)


async def _collect(job, stop_after=None):
    seen = []
    async with contextlib.aclosing(job.events()) as events:
        async for payload in events:
            seen.append(payload)
            if stop_after is not None and len(seen) >= stop_after:
                break
    return seen


def _gated_runner(gate, publish=None):
    """A runner that publishes ``publish`` (if any) and waits for ``gate``."""

    async def runner(job):
        if publish is not None:
            job.publish(publish)
        await gate.wait()

    return runner


async def _noop(job):
    return None


class TestJobFanOut(unittest.IsolatedAsyncioTestCase):
    """The SSE fan-out the scan job always had, now on every Job."""

    async def test_new_job_is_running_with_no_event(self):
        job = Job("scan", "Scan")
        self.assertTrue(job.running)
        self.assertIsNone(job.last_event)
        self.assertEqual(job.state, "running")
        self.assertEqual(len(job.id), 32)

    async def test_subscriber_receives_events_until_finish(self):
        job = Job("scan", "Scan")
        consumer = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)

        job.publish({"type": "progress", "progress": 10})
        job.publish({"type": "complete"})
        job.finish("finished")

        self.assertEqual(
            await asyncio.wait_for(consumer, 2),
            [{"type": "progress", "progress": 10}, {"type": "complete"}],
        )
        self.assertFalse(job.running)

    async def test_late_subscriber_gets_latest_snapshot_then_live_events(self):
        job = Job("scan", "Scan")
        job.publish({"type": "progress", "progress": 10})
        job.publish({"type": "progress", "progress": 40})

        consumer = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)
        job.publish({"type": "complete"})
        job.finish()

        self.assertEqual(
            await asyncio.wait_for(consumer, 2),
            [{"type": "progress", "progress": 40}, {"type": "complete"}],
        )

    async def test_subscriber_after_finish_gets_only_the_final_event(self):
        job = Job("scan", "Scan")
        job.publish({"type": "progress", "progress": 99})
        job.publish({"type": "complete"})
        job.finish()

        self.assertEqual(await _collect(job), [{"type": "complete"}])

    async def test_lead_comes_first_and_nothing_published_meanwhile_is_lost(self):
        """S5 I1: the ``job`` frame leads, with the subscription already in place."""
        job = Job("thumbnails", "Thumbnail generation")
        job.publish({"type": "status"})
        lead = {"type": "job"}
        seen = []
        async with contextlib.aclosing(job.events(lead=lead)) as events:
            async for payload in events:
                seen.append(payload)
                if payload is lead:
                    # Published while the consumer is still sending the lead.
                    job.publish({"type": "progress"})
                    job.publish({"type": "complete"})
                    job.finish()
        self.assertEqual(
            seen,
            [lead, {"type": "status"}, {"type": "progress"}, {"type": "complete"}],
        )

    async def test_lead_on_a_finished_job_then_its_last_event(self):
        job = Job("thumbnails", "Thumbnail generation")
        job.publish({"type": "complete"})
        job.finish()
        seen = []
        async with contextlib.aclosing(job.events(lead={"type": "job"})) as events:
            async for payload in events:
                seen.append(payload)
        self.assertEqual(seen, [{"type": "job"}, {"type": "complete"}])

    async def test_departed_subscriber_is_forgotten(self):
        job = Job("scan", "Scan")
        job.publish({"type": "progress", "progress": 1})
        await _collect(job, stop_after=1)

        self.assertEqual(job.subscriber_count, 0)
        job.publish({"type": "complete"})  # must not raise or queue anywhere
        job.finish()

    async def test_second_subscriber_attaches_to_running_job(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        job = registry.start(
            "scan",
            "Scan",
            record_changing=True,
            scope={},
            runner=_gated_runner(gate, {"type": "progress", "progress": 30}),
        )
        await asyncio.sleep(0)  # the runner publishes its first event
        first = asyncio.ensure_future(_collect(job))
        second = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)
        self.assertEqual(job.subscriber_count, 2)
        self.assertIs(registry.active("scan"), job)

        job.publish({"type": "progress", "progress": 60})
        gate.set()
        await asyncio.wait_for(job.task, 2)

        expected = [
            {"type": "progress", "progress": 30},
            {"type": "progress", "progress": 60},
        ]
        self.assertEqual(await asyncio.wait_for(first, 2), expected)
        self.assertEqual(await asyncio.wait_for(second, 2), expected)
        self.assertEqual(job.summary()["progress"], 60)

    async def test_finish_rejects_unknown_state_and_is_final(self):
        job = Job("scan", "Scan")
        with self.assertRaises(ValueError):
            job.finish("running")
        job.finish("cancelled")
        job.finish("finished")  # a second finish keeps the first state
        self.assertEqual(job.state, "cancelled")
        self.assertIsNotNone(job.finished_at)


class TestJobRegistry(unittest.IsolatedAsyncioTestCase):

    async def test_a_compatible_kind_never_blocks(self):
        """S5 M7: a caller may name running kinds that cannot conflict with it."""
        registry = JobRegistry()
        gate = asyncio.Event()
        thumbs = registry.start(
            "thumbnails",
            "Thumbnail generation",
            admission="guarded",
            runner=_gated_runner(gate),
        )
        self.assertIs(registry.blocker("probe", admission="records"), thumbs)
        self.assertIsNone(
            registry.blocker("probe", admission="records", compatible=("thumbnails",))
        )
        backup = registry.start(
            "backup", "Backup", admission="guarded", runner=_gated_runner(gate)
        )
        self.assertIs(
            registry.blocker("probe", admission="records", compatible=("thumbnails",)),
            backup,
        )
        gate.set()
        await asyncio.wait_for(asyncio.gather(thumbs.task, backup.task), 2)

    async def test_start_lists_and_finishes_a_job(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        job = registry.start(
            "scan",
            "Scan for metadata",
            record_changing=True,
            scope={"files": "new", "output_folders": 2},
            runner=_gated_runner(gate),
        )
        self.assertEqual(job.state, "running")
        self.assertIs(registry.get(job.id), job)
        self.assertIsNone(registry.get("missing"))
        summary = job.summary()
        self.assertEqual(
            set(summary),
            {
                "id",
                "kind",
                "title",
                "state",
                "started_at",
                "finished_at",
                "progress",
                "status_line",
                "processed",
                "total",
                "counters",
                "scope",
                "listed",
                "log_length",
                "cancellable",
                "admission",
            },
        )
        self.assertEqual(
            (summary["cancellable"], summary["admission"]), (True, "records")
        )
        self.assertEqual(summary["scope"], {"files": "new", "output_folders": 2})
        # What the detail would list, so a view reads it only when that changed.
        self.assertEqual((summary["listed"], summary["log_length"]), ({}, 0))
        self.assertIsNone(summary["finished_at"])

        gate.set()
        await asyncio.wait_for(job.task, 2)
        self.assertEqual(job.state, "finished")
        self.assertIsNone(registry.active("scan"))

        # Running jobs list first, then finished ones newest first.
        older = registry.start(
            "thumbs", "Thumbnails", record_changing=False, scope={}, runner=_noop
        )
        await asyncio.wait_for(older.task, 2)
        hold = asyncio.Event()
        running = registry.start(
            "other",
            "Other",
            record_changing=False,
            scope={},
            runner=_gated_runner(hold),
        )
        listed = registry.list()
        self.assertEqual([j.id for j in listed], [running.id, older.id, job.id])

        # Only the last MAX_FINISHED_JOBS finished jobs are kept.
        for index in range(MAX_FINISHED_JOBS + 5):
            done = registry.start(
                f"k{index}", "Fake", record_changing=False, scope={}, runner=_noop
            )
            await asyncio.wait_for(done.task, 2)
        listed = registry.list()
        finished = [j for j in listed if not j.running]
        self.assertEqual(len(finished), MAX_FINISHED_JOBS)
        self.assertEqual(finished[0].id, done.id)
        self.assertIsNone(registry.get(job.id))
        self.assertIs(listed[0], running)
        hold.set()
        await asyncio.wait_for(running.task, 2)

    async def test_record_changing_jobs_are_exclusive(self):
        registry = JobRegistry()
        gate = asyncio.Event()
        first = registry.start(
            "scan",
            "Scan for metadata",
            record_changing=True,
            scope={},
            runner=_gated_runner(gate),
        )
        with self.assertRaises(JobBusy) as raised:
            registry.start(
                "maintenance",
                "Maintenance",
                record_changing=True,
                scope={},
                runner=_noop,
            )
        self.assertIs(raised.exception.running, first)
        self.assertIn("Scan for metadata", str(raised.exception))

        reader = registry.start(
            "report", "Report", record_changing=False, scope={}, runner=_noop
        )
        await asyncio.wait_for(reader.task, 2)
        self.assertEqual(reader.state, "finished")

        # One job per kind, even a read-only one.
        hold = asyncio.Event()
        registry.start(
            "report",
            "Report",
            record_changing=False,
            scope={},
            runner=_gated_runner(hold),
        )
        with self.assertRaises(JobBusy):
            registry.start(
                "report", "Report", record_changing=False, scope={}, runner=_noop
            )
        gate.set()
        hold.set()
        await asyncio.wait_for(first.task, 2)

    async def test_cancel_is_idempotent_and_keeps_partial_counts(self):
        registry = JobRegistry()
        reached = asyncio.Event()
        proceed = asyncio.Event()

        async def runner(job):
            for index in range(3):
                if job.cancelled:
                    return
                job.record("added", {"path": f"a{index}.png", "root": "output"}, "+")
                if index == 0:
                    reached.set()
                    await proceed.wait()

        job = registry.start(
            "scan",
            "Scan",
            record_changing=True,
            scope={},
            runner=runner,
            groups=SCAN_GROUP_NAMES,
        )
        await asyncio.wait_for(reached.wait(), 2)
        self.assertTrue(registry.cancel(job.id))
        self.assertTrue(registry.cancel(job.id))  # still running: still requested
        proceed.set()
        await asyncio.wait_for(job.task, 2)

        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.counters["added"], 1)  # the work done before the stop
        self.assertFalse(registry.cancel(job.id))  # finished: a no-op
        self.assertFalse(registry.cancel("missing"))
        self.assertEqual(job.state, "cancelled")

    async def test_failing_runner_ends_failed_and_frees_the_slot(self):
        registry = JobRegistry()

        async def runner(job):
            raise RuntimeError("boom")

        job = registry.start(
            "scan", "Scan", record_changing=True, scope={}, runner=runner
        )
        with self.assertLogs("py.api.jobs", level="ERROR"):
            await asyncio.wait_for(job.task, 2)
        self.assertEqual(job.state, "failed")
        self.assertIsNone(registry.active("scan"))

    async def test_runner_that_finishes_its_own_job_keeps_its_state(self):
        registry = JobRegistry()

        async def runner(job):
            job.finish("failed")

        job = registry.start(
            "scan", "Scan", record_changing=True, scope={}, runner=runner
        )
        await asyncio.wait_for(job.task, 2)
        self.assertEqual(job.state, "failed")


class TestJobOutcomesAndLog(unittest.TestCase):

    def test_outcome_and_log_caps(self):
        job = Job("scan", "Scan", groups=SCAN_GROUP_NAMES)
        for index in range(MAX_OUTCOME_ROWS + 50):
            job.record("errors", {"path": f"f{index}.png", "root": "output"}, "!")
        self.assertEqual(len(job.outcomes["errors"]), MAX_OUTCOME_ROWS)
        self.assertEqual(job.counters["errors"], MAX_OUTCOME_ROWS + 50)
        self.assertEqual(job.outcomes["errors"][0]["path"], "f0.png")

        job = Job("scan", "Scan")
        for index in range(MAX_LOG_LINES + 100):
            job.log_line(f"line {index}")
        self.assertEqual(len(job.log), MAX_LOG_LINES + 1)
        self.assertEqual(job.log[-1], LOG_CAP_LINE)
        self.assertTrue(job.log[0].endswith("line 0"))
        self.assertEqual(LOG_CAP_LINE, "[Log capped at 2,000 lines]")

    def test_paths_are_root_relative(self):
        job = Job("scan", "Scan", groups=SCAN_GROUP_NAMES)
        absolute = os.path.abspath(os.path.join("outputs", "sub", "a.png"))
        job.record(
            "added",
            {"path": absolute, "root": "output", "detail": "x"},
            f"+ {absolute}",
        )
        job.record("known", {"path": "sub/b.png", "root": "output"}, "= sub/b.png")
        job.record("review", {"path": "C:\\out\\c.png", "root": "output"}, "?")
        rows = [row for group in job.outcomes.values() for row in group]
        for row in rows:
            self.assertFalse(os.path.isabs(row["path"]), row)
            self.assertNotIn(":", row["path"])
        self.assertEqual(job.outcomes["added"][0]["path"], "a.png")
        self.assertEqual(job.outcomes["known"][0]["path"], "sub/b.png")
        self.assertEqual(job.outcomes["review"][0]["path"], "c.png")
        self.assertEqual(job.outcomes["known"][0]["detail"], "")
        self.assertNotIn(absolute, "\n".join(job.log))

    def test_record_rejects_unknown_group(self):
        job = Job("scan", "Scan", groups=SCAN_GROUP_NAMES)
        with self.assertRaises(ValueError):
            job.record("bogus", {"path": "a.png", "root": "output"}, "x")

    def test_detail_and_progress(self):
        job = Job(
            "scan",
            "Scan",
            scope={"files": "new", "output_folders": 2},
            groups=SCAN_GROUP_NAMES,
        )
        job.update(progress=150, status_line="Processing file 3 of 4", processed=3)
        job.update(total=4)
        job.record("known", {"path": "a.png", "root": "output"}, "= a.png")
        detail = job.detail()
        self.assertEqual(detail["progress"], 100)
        self.assertEqual(detail["status_line"], "Processing file 3 of 4")
        self.assertEqual((detail["processed"], detail["total"]), (3, 4))
        self.assertEqual(detail["counters"]["known"], 1)
        self.assertEqual(detail["outcomes"]["known"][0]["path"], "a.png")
        self.assertEqual(len(detail["log"]), 1)
        detail["outcomes"]["known"].clear()  # a copy, not the live list
        self.assertEqual(len(job.outcomes["known"]), 1)
        job.publish({"type": "progress", "progress": -5})
        self.assertEqual(job.summary()["progress"], 0)

    def test_scan_groups_follow_the_counter_mapping(self):
        self.assertEqual(
            SCAN_GROUPS,
            {
                "added": "added",
                "linked": "added",
                "enriched": "added",
                "unchanged": "known",
                # Activity-2: a file the "new" scope skipped is Already known.
                "skipped": "known",
                "ambiguous": "review",
                "conflict": "review",
                "unresolved": "review",
                "failed": "errors",
            },
        )
        self.assertEqual((SCAN_SCOPES, DEFAULT_SCAN_SCOPE), (("new", "all"), "new"))
        self.assertEqual(
            SCAN_GROUP_NAMES, ("added", "known", "review", "no_metadata", "errors")
        )

    def test_record_counted_counts_exactly_lists_capped_and_logs_nothing(self):
        job = Job("thumbnails", "Thumbnail generation", groups=("skipped",))
        rows = [
            {"path": os.path.abspath(f"out{os.sep}f{i}.png"), "root": "output"}
            for i in range(MAX_OUTCOME_ROWS + 5)
        ]
        job.record_counted("skipped", 10_000, rows)
        job.record_counted("skipped", 1, [{"path": "late.png", "root_index": True}])
        self.assertEqual(job.counters["skipped"], 10_001)
        self.assertEqual(len(job.outcomes["skipped"]), MAX_OUTCOME_ROWS)
        self.assertEqual(job.outcomes["skipped"][0]["path"], "f0.png")
        self.assertIsNone(job.outcomes["skipped"][0]["root_index"])
        self.assertEqual(job.log, [])
        with self.assertRaises(ValueError):
            job.record_counted("bogus", 1)

    def test_record_counted_complete_keeps_every_row_for_the_report(self):
        # #550: a repair's record of each change is never cut at the cap.
        job = Job("maintenance", "Pair category and tags", groups=("changed", "errors"))
        rows = [
            {"path": "", "detail": f"Prompt {i}"} for i in range(MAX_OUTCOME_ROWS + 5)
        ]
        job.record_counted("changed", len(rows), rows, complete=True)
        self.assertEqual(len(job.outcomes["changed"]), MAX_OUTCOME_ROWS)
        report = job.report()["outcomes"]
        self.assertEqual(len(report["changed"]["rows"]), MAX_OUTCOME_ROWS + 5)
        self.assertEqual(
            report["changed"]["rows"][-1]["detail"], f"Prompt {MAX_OUTCOME_ROWS + 4}"
        )
        self.assertFalse(report["changed"]["truncated"])
        self.assertEqual(report["errors"], {"count": 0, "rows": [], "truncated": False})

    def test_record_into_a_complete_group_keeps_the_row_for_the_report(self):
        # #586: a group kept whole stays whole when later rows are recorded.
        job = Job("duplicate_cleanup", "Remove", groups=("kept",))
        rows = [
            {"path": f"f{i}.png", "detail": "Kept"} for i in range(MAX_OUTCOME_ROWS)
        ]
        job.record_counted("kept", len(rows), rows, complete=True)
        job.record("kept", {"path": "late.png", "detail": "Kept copy"}, "= late.png")
        self.assertEqual(len(job.outcomes["kept"]), MAX_OUTCOME_ROWS)
        report = job.report()["outcomes"]["kept"]
        self.assertEqual(report["count"], MAX_OUTCOME_ROWS + 1)
        self.assertEqual(report["rows"][-1]["path"], "late.png")
        self.assertFalse(report["truncated"])

    def test_thumbnail_job_groups(self):
        self.assertEqual(
            jobs_module.THUMBNAIL_GROUP_NAMES, ("generated", "skipped", "errors")
        )
        self.assertEqual(jobs_module.THUMBNAILS_JOB_KIND, "thumbnails")

    def test_image_tag_job_kind_and_groups(self):
        # #249 D1: bulk Add tags / Auto-tag, counter-tile order.
        self.assertEqual(getattr(jobs_module, "IMAGE_TAG_JOB_KIND", None), "image-tags")
        self.assertEqual(
            getattr(jobs_module, "IMAGE_TAG_GROUP_NAMES", None),
            ("tagged", "unchanged", "skipped", "errors"),
        )


if __name__ == "__main__":
    unittest.main()


class TestListedSummary(unittest.TestCase):
    def test_summary_counts_listed_rows_and_log_lines(self):
        job = Job("scan", "Scan", groups=("added", "errors"))
        job.record(
            "added", {"path": "a.png", "root": "output", "root_index": 0}, "+ a.png"
        )
        job.record("errors", {"path": "b.png", "root_index": "x"}, "! b.png")
        summary = job.summary()
        self.assertEqual(summary["listed"], {"added": 1, "errors": 1})
        self.assertEqual(summary["log_length"], 2)
        rows = job.detail()["outcomes"]
        self.assertEqual(rows["added"][0]["root_index"], 0)
        # Only a whole non-negative number is a root index.
        self.assertIsNone(rows["errors"][0]["root_index"])
