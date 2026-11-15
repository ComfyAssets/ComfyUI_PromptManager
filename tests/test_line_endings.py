"""Pinned files keep their bytes in every clone (#636).

``npm run --prefix tools/vendor check`` compares the vendored Viewer.js and
ComfyAssets Toolkit files, and the workspace's copy of the concept
stylesheet, with recorded hashes. Git for Windows converts text files to CRLF
on checkout by default, which changes those bytes, so ``.gitattributes`` marks
them as never converted. This holds that for every tracked file under them.
"""

import os
import shutil
import subprocess  # nosec B404 - fixed git arguments, no shell
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = (
    "web/lib/viewerjs",
    "web/js/shared/toolkit",
    "web/js/workspace/concept.css",
    "docs/concepts/workspace/style.css",
)


def _git(*args: str) -> str:
    # Without GIT_* variables: the pre-push hook exports GIT_DIR and
    # GIT_INDEX_FILE, which must not choose the repository read here.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    return subprocess.run(  # nosec B603 B607 - fixed git arguments, no shell
        ["git", *args], cwd=ROOT, env=env, check=True, capture_output=True, text=True
    ).stdout


@unittest.skipUnless(
    shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout"
)
class TestPinnedLineEndings(unittest.TestCase):
    def test_every_pinned_file_is_never_converted(self):
        files = _git("ls-files", "-z", "--", *PINNED).split("\0")
        files = [name for name in files if name]
        self.assertGreater(len(files), 8, files)
        report = _git("check-attr", "-z", "text", "--", *files).split("\0")
        # -z output: path, attribute, value, repeated.
        values = dict(zip(report[0::3], report[2::3]))
        for name in files:
            with self.subTest(path=name):
                self.assertEqual(values[name], "unset")

    def test_the_repository_stores_them_with_lf(self):
        report = _git("ls-files", "--eol", "--", *PINNED).splitlines()
        self.assertTrue(report)
        for line in report:
            with self.subTest(line=line):
                self.assertNotIn("i/crlf", line)
                self.assertNotIn("i/mixed", line)


if __name__ == "__main__":
    unittest.main()
