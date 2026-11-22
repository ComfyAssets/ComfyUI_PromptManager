"""CI runs tests only for changes that can affect code (#639).

``tools/ci/change_class.py`` sorts a change by the files it touches: ``inert``
(nothing reads the file at run or test time), ``docs`` (a document),
``release`` (only the version line of ``pyproject.toml``, #666) or ``code``
(everything else, and every case of doubt).
"""

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "ci"))
import change_class  # noqa: E402

SCRIPT = ROOT / "tools" / "ci" / "change_class.py"


def _isolated_environment(**extra):
    """The environment without git's own variables and the script's inputs.

    A git hook exports ``GIT_DIR`` and ``GIT_INDEX_FILE`` for the repository
    being pushed. A child ``git`` that inherits them works on that repository
    instead of the temp one, so the pre-push hook must never pass them on.
    """
    kept = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in change_class.ENVIRONMENT
    }
    return {**kept, **extra}


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
            ["CHANGELOG.md"],
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

    def test_a_version_bump_is_one_rewritten_version_line(self):
        head = (
            "diff --git a/pyproject.toml b/pyproject.toml\n"
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -4 +4 @@\n"
        )
        bump = head + '-version = "3.7.1"\n+version = "3.7.2"\n'
        self.assertTrue(change_class.is_version_bump(bump))
        self.assertTrue(change_class.is_version_bump(bump.replace("\n", "\r\n")))
        for diff in (
            "",
            head,
            head + '-version = "3.7.1"\n',
            head + '+version = "3.7.2"\n',
            bump + '+name = "x"\n',
            bump + "@@ -9 +9 @@\n-a\n+b\n",
            head + '-version = "3.7.1"\n+version = "3.7.1"\n',
            head + '-version = "3.7.1"\n+ version = "3.7.2"\n',
            head + '-version = "3.7.1"\n+version = "3.7.2" # x\n',
            head + '-target-version = "a"\n+target-version = "b"\n',
            "old mode 100644\nnew mode 100755\n" + bump,
        ):
            self.assertFalse(change_class.is_version_bump(diff), diff)

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
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        # As in a git hook: these name another repository, which must stay untouched.
        self.other = Path(self.tmp.name) / "other.git"
        patcher = unittest.mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(self.other),
                "GIT_INDEX_FILE": str(self.other / "index"),
                "GIT_WORK_TREE": str(self.tmp.name),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertFalse(self.other.exists()))
        self.git("init", "-q", "-b", "main")
        self.commit({"py/a.py": "a\n", "README.md": "r\n"})
        self.base = self.head()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=self.repo,
            env=_isolated_environment(),
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
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            env=_isolated_environment(**env),
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

    def test_a_push_is_classed_by_its_range(self):
        self.commit({"README.md": "r2\n"})
        self.assertEqual(
            self.class_of(EVENT_NAME="push", BASE_SHA=self.base, HEAD_SHA=self.head()),
            "class=docs",
        )
        self.commit({"py/a.py": "c\n"})
        self.assertEqual(
            self.class_of(EVENT_NAME="push", BASE_SHA=self.base, HEAD_SHA=self.head()),
            "class=code",
        )

    def test_no_merge_group_is_classed(self):
        # The merge queue is gone (#666): its event is unknown, and doubt is code.
        self.commit({"README.md": "r2\n"})
        self.assertEqual(
            self.class_of(
                EVENT_NAME="merge_group", BASE_SHA=self.base, HEAD_SHA=self.head()
            ),
            "class=code",
        )

    PROJECT = '[project]\nname = "promptmanager"\nversion = "{}"\nrequires = []\n'

    def bump(self, text, **more):
        """The class of a pull request whose one commit rewrites pyproject.toml."""
        if not (self.repo / "pyproject.toml").exists():
            self.commit({"pyproject.toml": self.PROJECT.format("3.7.1")})
        base = self.head()
        self.git("checkout", "-q", "-b", "release")
        self.commit({"pyproject.toml": text, **more})
        found = self.class_of(
            EVENT_NAME="pull_request", BASE_SHA=base, HEAD_SHA=self.head()
        )
        self.git("checkout", "-q", "main")
        self.git("branch", "-q", "-D", "release")
        return found

    def test_only_the_version_line_is_a_release(self):
        self.assertEqual(self.bump(self.PROJECT.format("3.7.2")), "class=release")

    def test_a_release_with_any_other_change_is_code(self):
        bumped = self.PROJECT.format("3.7.2")
        for text, more in (
            # Another line of the same file.
            (bumped.replace("requires = []", 'requires = ["x"]'), {}),
            # Another file, even an inert one.
            (bumped, {".github/FUNDING.yml": "x\n"}),
            (bumped, {"py/a.py": "b\n"}),
            # The version line removed or doubled, never rewritten.
            (bumped.replace('version = "3.7.2"\n', ""), {}),
            (bumped.replace("requires", 'version = "9"\nrequires'), {}),
            # A line that only looks like the version.
            (
                self.PROJECT.format("3.7.1").replace("requires = []", "requires = [1]"),
                {},
            ),
            (self.PROJECT.format("3.7.1").replace("version =", "target-version ="), {}),
        ):
            with self.subTest(text=text, more=more):
                self.assertEqual(self.bump(text, **more), "class=code")

    def test_a_pushed_release_is_a_release(self):
        self.commit({"pyproject.toml": self.PROJECT.format("3.7.1")})
        base = self.head()
        self.commit({"pyproject.toml": self.PROJECT.format("3.8.0")})
        self.assertEqual(
            self.class_of(EVENT_NAME="push", BASE_SHA=base, HEAD_SHA=self.head()),
            "class=release",
        )

    def test_a_deleted_or_renamed_file_counts_under_both_names(self):
        self.git("mv", "py/a.py", "SECURITY.md")
        self.git("commit", "-q", "-m", "rename")
        self.assertEqual(
            self.class_of(EVENT_NAME="push", BASE_SHA=self.base, HEAD_SHA=self.head()),
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
        # No range from pre-commit (a manual run) and a code push both run it.
        self.assertEqual(self.run_script(*command).returncode, 0)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.commit({"py/x.py": "x = 1\n"})
        refs["PRE_COMMIT_TO_REF"] = self.head()
        self.assertEqual(self.run_script(*command, **refs).returncode, 0)
        self.assertTrue(marker.exists())

    def test_a_docs_push_runs_only_the_tests_that_read_the_docs(self):
        # #679: the full suite took minutes for a README edit.
        ran = unittest.mock.Mock(return_value=unittest.mock.Mock(returncode=5))
        with unittest.mock.patch.dict(os.environ, _isolated_environment(), clear=True):
            with (
                unittest.mock.patch.object(
                    change_class, "class_of_range", return_value="docs"
                ),
                unittest.mock.patch.object(change_class.subprocess, "run", ran),
            ):
                code = change_class.main(["--unless-inert", "python", "-m", "full"])
        ran.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "unittest",
                "tests.test_brand_assets",
                "tests.test_package_contents",
            ],
            check=False,
        )
        self.assertEqual(code, 5)

    def test_the_docs_tests_named_by_the_hook_exist(self):
        for module in change_class.DOCS_TESTS:
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(path.is_file(), module)

    def test_the_pre_push_command_is_found_through_path(self):
        # On Windows a bare "npm" is npm.cmd: only a PATH lookup with PATHEXT finds it.
        found = unittest.mock.Mock(return_value=unittest.mock.Mock(returncode=3))
        with unittest.mock.patch.dict(os.environ, _isolated_environment(), clear=True):
            with (
                unittest.mock.patch.object(
                    change_class.shutil, "which", return_value="C:/node/npm.cmd"
                ) as which,
                unittest.mock.patch.object(change_class.subprocess, "run", found),
            ):
                code = change_class.main(["--unless-inert", "npm", "test"])
        which.assert_called_once_with("npm")
        found.assert_called_once_with(["C:/node/npm.cmd", "test"], check=False)
        self.assertEqual(code, 3)

    def test_a_missing_or_absent_pre_push_command_fails_clearly(self):
        with unittest.mock.patch.dict(os.environ, _isolated_environment(), clear=True):
            with (
                unittest.mock.patch.object(
                    change_class.shutil, "which", return_value=None
                ),
                unittest.mock.patch.object(change_class.subprocess, "run") as run,
            ):
                self.assertEqual(change_class.main(["--unless-inert", "nope"]), 127)
                self.assertEqual(change_class.main(["--unless-inert"]), 2)
        run.assert_not_called()

    def test_the_pre_push_command_keeps_its_exit_code(self):
        result = self.run_script(
            "--unless-inert", sys.executable, "-c", "raise SystemExit(7)"
        )
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
