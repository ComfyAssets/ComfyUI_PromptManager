"""CI runs tests only for changes that can affect code (#639).

``tools/ci/change_class.py`` sorts a change by the files it touches: ``inert``
(nothing reads the file at run or test time), ``docs`` (a Python test reads
it) or ``code`` (everything else, and every case of doubt).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "ci"))
import change_class  # noqa: E402

SCRIPT = ROOT / "tools" / "ci" / "change_class.py"


class TestClassify(unittest.TestCase):
    def test_files_nothing_reads_are_inert(self):
        for paths in (
            [".github/FUNDING.yml"],
            ["SECURITY.md"],
            ["images/pm-01.png", ".github/ISSUE_TEMPLATE/bug_report.md"],
        ):
            self.assertEqual(change_class.classify(paths), "inert", paths)

    def test_documents_a_test_reads_are_docs(self):
        for paths in (
            ["README.md"],
            ["README-legacy.md", "assets/readme/hero.webp"],
            ["assets/brand/banner.webp", ".github/FUNDING.yml"],
        ):
            self.assertEqual(change_class.classify(paths), "docs", paths)

    def test_anything_else_is_code(self):
        for paths in (
            ["py/api/images.py"],
            ["README.md", "web/js/workspace/index.mjs"],
            [".github/FUNDING.yml", "pyproject.toml"],
            # The browser suite compares production against the concept.
            ["docs/concepts/workspace/app.js"],
            # The tooling tests read the tables in it.
            ["TESTING.md"],
            [".github/workflows/test.yml"],
            ["LICENSE"],
            # Look-alikes of listed names are not listed.
            ["docs/README.md"],
            ["web/images/x.png"],
            ["SECURITY.md.py"],
            [".github/funding.yml"],
        ):
            self.assertEqual(change_class.classify(paths), "code", paths)

    def test_no_files_is_code(self):
        self.assertEqual(change_class.classify([]), "code")

    def test_every_document_a_test_reads_exists(self):
        for name in change_class.DOCS_FILES:
            self.assertTrue((ROOT / name).is_file(), name)
        for folder in change_class.DOCS_FOLDERS:
            self.assertTrue((ROOT / folder).is_dir(), folder)


class TestCommandLine(unittest.TestCase):
    """The script against a real repository with one commit per kind of change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.commit({"py/a.py": "a\n", "README.md": "r\n"})
        self.base = self.head()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, files):
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def head(self):
        return self.git("rev-parse", "HEAD")

    def run_script(self, *args, **env):
        clean = {
            key: value
            for key, value in os.environ.items()
            if key not in change_class.ENVIRONMENT
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            env={**clean, **env},
            capture_output=True,
            text=True,
        )

    def class_of(self, **env):
        result = self.run_script(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_a_pull_request_is_classed_by_its_own_files(self):
        self.git("checkout", "-q", "-b", "topic")
        self.commit({".github/FUNDING.yml": "x\n"})
        topic = self.head()
        # main moved on with code meanwhile: that is not this pull request's change.
        self.git("checkout", "-q", "main")
        self.commit({"py/a.py": "b\n"})
        self.assertEqual(
            self.class_of(
                EVENT_NAME="pull_request", BASE_SHA=self.head(), HEAD_SHA=topic
            ),
            "class=inert",
        )

    def test_a_merge_group_and_a_push_are_classed_by_their_range(self):
        self.commit({"README.md": "r2\n"})
        for event in ("merge_group", "push"):
            self.assertEqual(
                self.class_of(
                    EVENT_NAME=event, BASE_SHA=self.base, HEAD_SHA=self.head()
                ),
                "class=docs",
                event,
            )
        self.commit({"py/a.py": "c\n"})
        self.assertEqual(
            self.class_of(
                EVENT_NAME="merge_group", BASE_SHA=self.base, HEAD_SHA=self.head()
            ),
            "class=code",
        )

    def test_a_deleted_or_renamed_file_counts_under_both_names(self):
        self.git("mv", "py/a.py", "SECURITY.md")
        self.git("commit", "-q", "-m", "rename")
        self.assertEqual(
            self.class_of(
                EVENT_NAME="merge_group", BASE_SHA=self.base, HEAD_SHA=self.head()
            ),
            "class=code",
        )

    def test_doubt_is_code(self):
        self.commit({".github/FUNDING.yml": "x\n"})
        head = self.head()
        zero = "0" * 40
        for env in (
            {"EVENT_NAME": "workflow_dispatch", "BASE_SHA": self.base},
            {"EVENT_NAME": "schedule", "BASE_SHA": self.base, "HEAD_SHA": head},
            {"EVENT_NAME": "push", "BASE_SHA": zero, "HEAD_SHA": head},
            {"EVENT_NAME": "push", "BASE_SHA": "", "HEAD_SHA": head},
            {"EVENT_NAME": "push", "BASE_SHA": "f" * 40, "HEAD_SHA": head},
            {"EVENT_NAME": "push", "BASE_SHA": head, "HEAD_SHA": head},
            {},
        ):
            self.assertEqual(self.class_of(**env), "class=code", env)

    def test_the_pre_push_command_is_skipped_only_for_an_inert_push(self):
        marker = self.repo / "ran"
        command = (
            "--unless-inert",
            sys.executable,
            "-c",
            "open('ran', 'w').close()",
        )
        self.commit({".github/FUNDING.yml": "x\n"})
        refs = {"PRE_COMMIT_FROM_REF": self.base, "PRE_COMMIT_TO_REF": self.head()}
        result = self.run_script(*command, **refs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertIn("inert", result.stdout)
        # No range from pre-commit (a manual run) and a docs push both run it.
        self.assertEqual(self.run_script(*command).returncode, 0)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.commit({"README.md": "r3\n"})
        refs["PRE_COMMIT_TO_REF"] = self.head()
        self.assertEqual(self.run_script(*command, **refs).returncode, 0)
        self.assertTrue(marker.exists())

    def test_the_pre_push_command_keeps_its_exit_code(self):
        result = self.run_script(
            "--unless-inert", sys.executable, "-c", "raise SystemExit(7)"
        )
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
