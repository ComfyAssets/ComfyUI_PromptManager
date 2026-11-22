#!/usr/bin/env python3
"""Sort a change by the files it touches, so CI tests only what can break (#639).

Four classes. Two come from allowlists:

- ``inert``: every file is one nothing reads at run or test time (the funding
  file, the security policy, the old README's screenshots, issue templates).
  No test job runs, and the pre-push hook skips the suite.
- ``docs``: every file is inert or a document (the READMEs, the changelog and
  their images).
  No test job runs in CI (#666). Two Python tests read these files
  (``tests/test_brand_assets.py``, ``tests/test_package_contents.py``), so
  the pre-push hook runs those two and not the whole suite (#679).
- ``release``: ``pyproject.toml`` is the only file, and its ``version`` line
  the only line, that changed. CI runs the package-content check only.
- ``code``: anything else. A file on no list is code, and so is every case of
  doubt: no files, an unknown event, a range git cannot read.

``docs/`` is code (the browser suite compares production against
``docs/concepts/workspace/``) and so is ``TESTING.md`` (the tooling tests read
its tables). To add a file to a list, first check that no test and no shipped
code opens it.

Usage:

``change_class.py``
    Prints ``class=<inert|docs|release|code>`` for the workflow's
    ``$GITHUB_OUTPUT``, from ``EVENT_NAME``, ``BASE_SHA`` and ``HEAD_SHA``.
    A pull request is compared with its merge base, so commits that landed on
    main meanwhile do not count; a push is compared end to end.

``change_class.py --unless-inert <command...>``
    The pre-push hook: runs the command unless pre-commit's pushed range
    (``PRE_COMMIT_FROM_REF``..``PRE_COMMIT_TO_REF``) is inert, and exits
    with the command's code. For a docs range it runs ``DOCS_TESTS`` in place
    of the command. The command is looked up on ``PATH`` first, so a
    bare ``npm`` finds ``npm.cmd`` on Windows, which ``subprocess`` alone
    does not do.
"""

import os
import re
import shutil
import subprocess
import sys
from typing import Iterable, Optional

INERT_FILES = frozenset({".github/FUNDING.yml", "SECURITY.md"})
INERT_FOLDERS = ("images/", ".github/ISSUE_TEMPLATE/")
DOCS_FILES = frozenset({"README.md", "README-legacy.md", "CHANGELOG.md"})
DOCS_FOLDERS = ("assets/readme/", "assets/brand/")
# The only tests that open a docs file; a docs push runs these alone (#679).
DOCS_TESTS = ("tests.test_brand_assets", "tests.test_package_contents")

VERSION_FILE = "pyproject.toml"
VERSION_LINE = re.compile(r'version = "[^"]+"')

RANGE_EVENTS = frozenset({"pull_request", "push"})
ENVIRONMENT = frozenset(
    {
        "EVENT_NAME",
        "BASE_SHA",
        "HEAD_SHA",
        "PRE_COMMIT_FROM_REF",
        "PRE_COMMIT_TO_REF",
    }
)


def classify(paths: Iterable[str]) -> str:
    """``inert``, ``docs`` or ``code`` for the repository paths of one change."""
    found = "code"
    for path in paths:
        if path in INERT_FILES or path.startswith(INERT_FOLDERS):
            if found == "code":
                found = "inert"
        elif path in DOCS_FILES or path.startswith(DOCS_FOLDERS):
            found = "docs"
        else:
            return "code"
    return found


def is_version_bump(diff: str) -> bool:
    """Whether a zero-context diff of one file changes only its version line.

    True for exactly one removed and one added line that are both
    ``version = "..."`` at the start of the line. A second changed line, an
    added or removed line without its pair, or a changed mode is not a bump.
    """
    removed, added = [], []
    for line in diff.splitlines():
        if line.startswith(("old mode", "new mode", "deleted file", "new file")):
            return False
        if line.startswith("-") and not line.startswith("--- "):
            removed.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++ "):
            added.append(line[1:])
    if len(removed) != 1 or len(added) != 1 or removed == added:
        return False
    return all(VERSION_LINE.fullmatch(line.rstrip("\r")) for line in removed + added)


def _git_diff(span: str, *options: str) -> Optional[str]:
    """``git diff`` output for a range, or None when git cannot say."""
    try:
        result = subprocess.run(
            ["git", "diff", *options, span, "--"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _span(base: str, head: str, three_dot: bool) -> Optional[str]:
    if not base or not head or not base.strip("0") or not head.strip("0"):
        return None
    return f"{base}...{head}" if three_dot else f"{base}..{head}"


def changed_paths(base: str, head: str, three_dot: bool) -> Optional[list[str]]:
    """Paths that differ between two commits, or None when git cannot say.

    Renames are listed under both names, so a file moved onto a listed name
    still counts as the code file it was.
    """
    span = _span(base, head, three_dot)
    if span is None:
        return None
    names = _git_diff(span, "--name-only", "--no-renames", "-z")
    if names is None:
        return None
    return [path for path in names.split("\0") if path]


def class_of_range(base: str, head: str, three_dot: bool = False) -> str:
    paths = changed_paths(base, head, three_dot)
    if paths is None:
        return "code"
    for path in paths:
        print(f"changed: {path}", file=sys.stderr)
    if paths == [VERSION_FILE]:
        diff = _git_diff(_span(base, head, three_dot), "--unified=0", "--no-color")
        if diff is not None and is_version_bump(diff):
            return "release"
    return classify(paths)


def run_command(argv: list[str]) -> int:
    """Run ``argv`` with its program resolved through ``PATH``; its exit code."""
    if not argv:
        print("change_class: --unless-inert needs a command", file=sys.stderr)
        return 2
    program = shutil.which(argv[0])
    if program is None:
        print(f"change_class: command not found: {argv[0]}", file=sys.stderr)
        return 127
    return subprocess.run([program, *argv[1:]], check=False).returncode


def main(argv: list[str]) -> int:
    if argv[:1] == ["--unless-inert"]:
        found = class_of_range(
            os.environ.get("PRE_COMMIT_FROM_REF", ""),
            os.environ.get("PRE_COMMIT_TO_REF", ""),
        )
        if found == "inert":
            print("Skipped: this push only changes inert files (tools/ci).")
            return 0
        if found == "docs":
            print("This push only changes docs: running the tests that read them.")
            command = [sys.executable, "-m", "unittest", *DOCS_TESTS]
            return subprocess.run(command, check=False).returncode
        return run_command(argv[1:])
    event = os.environ.get("EVENT_NAME", "")
    found = "code"
    if event in RANGE_EVENTS:
        found = class_of_range(
            os.environ.get("BASE_SHA", ""),
            os.environ.get("HEAD_SHA", ""),
            three_dot=event == "pull_request",
        )
    print(f"class={found}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
