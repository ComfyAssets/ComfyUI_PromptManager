#!/usr/bin/env python3
"""Report a nightly run by issue: one issue per failing job (#666).

The nightly workflow (``.github/workflows/nightly.yml``) blocks nothing, so
its result has to reach someone. After the other jobs end, this script reads
the run's jobs and, for each one:

- a failed or timed-out leg: opens ``nightly: <job> failing``, or comments on
  the open issue of that title, with the run, the commit and the failed legs;
- every leg passed: comments on the open issue of that title, if there is
  one, and closes it;
- anything else (cancelled, skipped): changes nothing, because the run says
  nothing about the job.

Matrix shards count as one job: ``page workflows (webkit, shard 2/3)`` reports
as ``page workflows (webkit)``. Titles are matched exactly, so an issue is
never opened twice and never closed by another job's result.

Runs with ``GH_TOKEN``, ``GITHUB_REPOSITORY``, ``GITHUB_RUN_ID``,
``GITHUB_SHA`` and ``GITHUB_SERVER_URL`` from the workflow, plus
``RUN_ATTEMPT``, and calls the ``gh`` CLI. It exits non-zero when a call
fails, so a report that could not be written shows as a red job.
"""

import json
import os
import re
import subprocess
import sys
from typing import Callable, Iterable, Optional

SHARD = re.compile(r", shard \d+/\d+| \(shard \d+/\d+\)")
FAILED = frozenset({"failure", "timed_out"})
OWN_JOB = "report by issue"
TITLE = "nightly: {} failing"

Gh = Callable[[list[str]], str]


def job_group(name: str) -> str:
    """The job a matrix leg belongs to: its name without the shard."""
    return SHARD.sub("", name).strip()


def group_jobs(jobs: Iterable[dict]) -> dict[str, list[dict]]:
    """The run's finished jobs by group, without this script's own job."""
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        if job.get("name") == OWN_JOB or not job.get("conclusion"):
            continue
        groups.setdefault(job_group(job["name"]), []).append(job)
    return groups


def verdict(legs: Iterable[dict]) -> Optional[str]:
    """``failing``, ``passing``, or None when the legs do not say."""
    conclusions = [leg["conclusion"] for leg in legs]
    if any(conclusion in FAILED for conclusion in conclusions):
        return "failing"
    if conclusions and all(conclusion == "success" for conclusion in conclusions):
        return "passing"
    return None


def run_gh(arguments: list[str]) -> str:
    result = subprocess.run(
        ["gh", *arguments], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(arguments[:3])}: {result.stderr.strip()}")
    return result.stdout


def read_jobs(gh: Gh, repository: str, run_id: str, attempt: str) -> list[dict]:
    path = f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs"
    pages = gh(["api", "--paginate", "--slurp", f"{path}?per_page=100"])
    return [job for page in json.loads(pages) for job in page["jobs"]]


def open_issue(gh: Gh, repository: str, title: str) -> Optional[int]:
    """The number of the open issue with exactly this title, if any."""
    found = gh(
        [
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--search",
            f'in:title "{title}"',
            "--json",
            "number,title",
            "--limit",
            "100",
        ]
    )
    numbers = [
        issue["number"] for issue in json.loads(found) if issue["title"] == title
    ]
    return min(numbers) if numbers else None


def failure_text(legs: list[dict], run_url: str, sha: str) -> str:
    failed = [leg for leg in legs if leg["conclusion"] in FAILED]
    lines = [
        f"- [{leg['name']}]({leg['html_url']}): {leg['conclusion']}" for leg in failed
    ]
    return "\n".join(
        [
            f"The nightly run {run_url} failed at {sha}:",
            "",
            *lines,
            "",
            (
                "Nothing is blocked by this. Read the job log, fix the cause, and the"
                " next green nightly run closes this issue. Do not rerun to get green"
                ' (TESTING.md "Nightly").'
            ),
        ]
    )


def report(gh: Gh, environment: dict[str, str]) -> list[str]:
    """Write the issues for one run; the lines saying what was done."""
    repository = environment["GITHUB_REPOSITORY"]
    run_id = environment["GITHUB_RUN_ID"]
    sha = environment["GITHUB_SHA"][:7]
    server = environment.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repository}/actions/runs/{run_id}"
    jobs = read_jobs(gh, repository, run_id, environment.get("RUN_ATTEMPT", "1"))
    done = []
    for group, legs in sorted(group_jobs(jobs).items()):
        state = verdict(legs)
        if state is None:
            done.append(f"{group}: no verdict, nothing changed")
            continue
        title = TITLE.format(group)
        number = open_issue(gh, repository, title)
        if state == "failing":
            body = failure_text(legs, run_url, sha)
            if number is None:
                gh(
                    [
                        "issue",
                        "create",
                        "--repo",
                        repository,
                        "--title",
                        title,
                        "--body",
                        body,
                    ]
                )
                done.append(f"{group}: failing, opened an issue")
            else:
                gh(
                    [
                        "issue",
                        "comment",
                        str(number),
                        "--repo",
                        repository,
                        "--body",
                        body,
                    ]
                )
                done.append(f"{group}: failing, commented on #{number}")
        elif number is not None:
            body = f"Passed in the nightly run {run_url} at {sha}."
            gh(["issue", "close", str(number), "--repo", repository, "--comment", body])
            done.append(f"{group}: passing, closed #{number}")
        else:
            done.append(f"{group}: passing")
    return done


def main() -> int:
    try:
        for line in report(run_gh, dict(os.environ)):
            print(line)
    except (RuntimeError, KeyError, ValueError, OSError) as error:
        print(f"nightly_report: {error!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
