"""The nightly run reports by issue: one per failing job (#666).

``tools/ci/nightly_report.py`` against a recorded ``gh``: what it reads and
which issue it opens, comments on or closes for each kind of result.
"""

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "ci"))
import nightly_report  # noqa: E402

ENVIRONMENT = {
    "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_RUN_ID": "77",
    "GITHUB_SHA": "0123456789abcdef",
    "GITHUB_SERVER_URL": "https://github.example",
    "RUN_ATTEMPT": "2",
}
RUN_URL = "https://github.example/owner/repo/actions/runs/77"


def job(name, conclusion):
    return {"name": name, "conclusion": conclusion, "html_url": f"https://x/{name}"}


class FakeGh:
    """Answers the two reads from fixtures and records every call."""

    def __init__(self, jobs, issues=()):
        self.pages = [{"jobs": jobs[:2]}, {"jobs": jobs[2:]}]
        self.issues = list(issues)
        self.calls = []

    def __call__(self, arguments):
        self.calls.append(arguments)
        if arguments[0] == "api":
            return json.dumps(self.pages)
        if arguments[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        return ""

    def writes(self):
        return [
            call for call in self.calls if call[1] in ("create", "comment", "close")
        ]


class TestGrouping(unittest.TestCase):
    def test_shards_report_as_one_job(self):
        for name, group in (
            ("page workflows (webkit, shard 2/3)", "page workflows (webkit)"),
            ("frontend coverage (shard 1/4)", "frontend coverage"),
            ("py3.13 on ubuntu-latest", "py3.13 on ubuntu-latest"),
            ("real ComfyUI compatibility", "real ComfyUI compatibility"),
        ):
            self.assertEqual(nightly_report.job_group(name), group)

    def test_the_report_job_and_unfinished_jobs_are_left_out(self):
        groups = nightly_report.group_jobs(
            [
                job("report by issue", None),
                job("perf budget", None),
                job("page workflows (touch, shard 1/3)", "success"),
                job("page workflows (touch, shard 2/3)", "failure"),
            ]
        )
        self.assertEqual(list(groups), ["page workflows (touch)"])
        self.assertEqual(len(groups["page workflows (touch)"]), 2)

    def test_a_verdict_needs_a_failure_or_every_leg_green(self):
        legs = lambda *conclusions: [job("j", c) for c in conclusions]  # noqa: E731
        self.assertEqual(nightly_report.verdict(legs("success", "failure")), "failing")
        self.assertEqual(nightly_report.verdict(legs("timed_out")), "failing")
        self.assertEqual(
            nightly_report.verdict(legs("cancelled", "failure")), "failing"
        )
        self.assertEqual(nightly_report.verdict(legs("success", "success")), "passing")
        self.assertIsNone(nightly_report.verdict(legs("success", "cancelled")))
        self.assertIsNone(nightly_report.verdict(legs("skipped")))
        self.assertIsNone(nightly_report.verdict([]))


class TestReport(unittest.TestCase):
    def test_it_reads_every_page_of_this_attempts_jobs(self):
        gh = FakeGh([job("a", "success"), job("b", "success"), job("c", "success")])
        done = nightly_report.report(gh, ENVIRONMENT)
        self.assertEqual(
            gh.calls[0],
            [
                "api",
                "--paginate",
                "--slurp",
                "repos/owner/repo/actions/runs/77/attempts/2/jobs?per_page=100",
            ],
        )
        self.assertEqual(done, ["a: passing", "b: passing", "c: passing"])
        self.assertEqual(gh.writes(), [])

    def test_a_failing_job_without_an_issue_opens_one(self):
        gh = FakeGh(
            [
                job("page workflows (webkit, shard 1/3)", "success"),
                job("page workflows (webkit, shard 2/3)", "failure"),
                job("page workflows (webkit, shard 3/3)", "timed_out"),
            ],
            # A longer title and another job's issue are not this job's issue.
            issues=[
                {
                    "number": 5,
                    "title": "nightly: page workflows (webkit) failing again",
                },
                {"number": 6, "title": "nightly: page workflows (firefox) failing"},
            ],
        )
        done = nightly_report.report(gh, ENVIRONMENT)
        (create,) = gh.writes()
        self.assertEqual(create[:4], ["issue", "create", "--repo", "owner/repo"])
        self.assertEqual(
            create[create.index("--title") + 1],
            "nightly: page workflows (webkit) failing",
        )
        body = create[create.index("--body") + 1]
        self.assertIn(f"{RUN_URL} failed at 0123456", body)
        self.assertIn("shard 2/3)](https://x/", body)
        self.assertIn("shard 3/3)", body)
        self.assertIn("timed_out", body)
        self.assertNotIn("shard 1/3", body)
        self.assertEqual(done, ["page workflows (webkit): failing, opened an issue"])
        search = gh.calls[1]
        self.assertEqual(
            search[search.index("--search") + 1],
            'in:title "nightly: page workflows (webkit) failing"',
        )
        self.assertEqual(search[search.index("--state") + 1], "open")

    def test_a_job_that_still_fails_comments_on_its_open_issue(self):
        gh = FakeGh(
            [job("perf budget", "failure")],
            issues=[
                {"number": 9, "title": "nightly: perf budget failing"},
                {"number": 4, "title": "nightly: perf budget failing"},
            ],
        )
        done = nightly_report.report(gh, ENVIRONMENT)
        (comment,) = gh.writes()
        self.assertEqual(comment[:5], ["issue", "comment", "4", "--repo", "owner/repo"])
        self.assertIn(RUN_URL, comment[-1])
        self.assertEqual(done, ["perf budget: failing, commented on #4"])

    def test_a_green_job_closes_its_open_issue_with_a_comment(self):
        gh = FakeGh(
            [job("perf budget", "success")],
            issues=[{"number": 9, "title": "nightly: perf budget failing"}],
        )
        done = nightly_report.report(gh, ENVIRONMENT)
        (close,) = gh.writes()
        self.assertEqual(close[:5], ["issue", "close", "9", "--repo", "owner/repo"])
        self.assertEqual(
            close[close.index("--comment") + 1],
            f"Passed in the nightly run {RUN_URL} at 0123456.",
        )
        self.assertEqual(done, ["perf budget: passing, closed #9"])

    def test_a_cancelled_or_skipped_job_changes_nothing(self):
        gh = FakeGh(
            [job("perf budget", "cancelled"), job("real ComfyUI", "skipped")],
            issues=[{"number": 9, "title": "nightly: perf budget failing"}],
        )
        done = nightly_report.report(gh, ENVIRONMENT)
        self.assertEqual(gh.writes(), [])
        self.assertEqual(len(gh.calls), 1, "no issue is even looked up")
        self.assertEqual(
            done,
            [
                "perf budget: no verdict, nothing changed",
                "real ComfyUI: no verdict, nothing changed",
            ],
        )


class TestCommandLine(unittest.TestCase):
    def test_gh_is_called_and_its_failure_is_an_error(self):
        ok = unittest.mock.Mock(returncode=0, stdout="out", stderr="")
        with unittest.mock.patch.object(
            nightly_report.subprocess, "run", return_value=ok
        ) as run:
            self.assertEqual(nightly_report.run_gh(["issue", "list"]), "out")
        run.assert_called_once_with(
            ["gh", "issue", "list"], capture_output=True, text=True, check=False
        )
        bad = unittest.mock.Mock(returncode=1, stdout="", stderr="HTTP 403\n")
        with unittest.mock.patch.object(
            nightly_report.subprocess, "run", return_value=bad
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                nightly_report.run_gh(["issue", "list"])

    def test_main_prints_what_was_done_and_fails_when_a_call_fails(self):
        with (
            unittest.mock.patch.object(
                nightly_report, "report", return_value=["a: passing"]
            ),
            unittest.mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(nightly_report.main(), 0)
        printed.assert_called_once_with("a: passing")
        for error in (RuntimeError("gh api: HTTP 403"), KeyError("GITHUB_RUN_ID")):
            with (
                unittest.mock.patch.object(nightly_report, "report", side_effect=error),
                unittest.mock.patch("builtins.print"),
            ):
                self.assertEqual(nightly_report.main(), 1)


if __name__ == "__main__":
    unittest.main()
