#!/usr/bin/env python3
"""Sort a change by the files it touches, so CI tests only what can break (#639).

Three classes, decided from allowlists:

- ``inert``: every file is one nothing reads at run or test time (the funding
  file, the security policy, the old README's screenshots, issue templates).
  No test job runs.
- ``docs``: every file is inert or a document a Python test reads (the
  READMEs and their images, checked by ``tests/test_brand_assets.py`` and
  ``tests/test_package_contents.py``). Only the Python 3.12 suite runs.
- ``code``: anything else. A file on no list is code, and so is every case of
  doubt: no files, an unknown event, a range git cannot read.

``docs/`` is code (the browser suite compares production against
``docs/concepts/workspace/``) and so is ``TESTING.md`` (the tooling tests read
its tables). To add a file to a list, first check that no test and no shipped
code opens it.

Usage:

``change_class.py``
    Prints ``class=<inert|docs|code>`` for the workflow's ``$GITHUB_OUTPUT``,
    from ``EVENT_NAME``, ``BASE_SHA`` and ``HEAD_SHA``. A pull request is
    compared with its merge base, so commits that landed on main meanwhile
    do not count; a merge group and a push are compared end to end.

``change_class.py --unless-inert <command...>``
    The pre-push hook: runs the command unless pre-commit's pushed range
    (``PRE_COMMIT_FROM_REF``..``PRE_COMMIT_TO_REF``) is inert, and exits
    with the command's code. The command is looked up on ``PATH`` first, so a
    bare ``npm`` finds ``npm.cmd`` on Windows, which ``subprocess`` alone
    does not do.
"""

import os
import shutil
import subprocess
import sys
from typing import Iterable, Optional

INERT_FILES = frozenset({".github/FUNDING.yml", "SECURITY.md"})
INERT_FOLDERS = ("images/", ".github/ISSUE_TEMPLATE/")
DOCS_FILES = frozenset({"README.md", "README-legacy.md"})
DOCS_FOLDERS = ("assets/readme/", "assets/brand/")

RANGE_EVENTS = frozenset({"pull_request", "merge_group", "push"})
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


def changed_paths(base: str, head: str, three_dot: bool) -> Optional[list[str]]:
    """Paths that differ between two commits, or None when git cannot say.

    Renames are listed under both names, so a file moved onto a listed name
    still counts as the code file it was.
    """
    if not base or not head or not base.strip("0") or not head.strip("0"):
        return None
    span = f"{base}...{head}" if three_dot else f"{base}..{head}"
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", span, "--"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [path for path in result.stdout.split("\0") if path]


def class_of_range(base: str, head: str, three_dot: bool = False) -> str:
    paths = changed_paths(base, head, three_dot)
    if paths is None:
        return "code"
    for path in paths:
        print(f"changed: {path}", file=sys.stderr)
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
