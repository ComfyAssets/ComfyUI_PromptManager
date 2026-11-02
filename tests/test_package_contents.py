"""What the Registry package leaves out (#615).

``comfy node publish`` zips the tracked files minus ``.comfyignore``. The checks
here hold the two ways that can go wrong: a development folder creeping back
into the download, or a rule that drops something ComfyUI needs at run time.
"""

import ast
import fnmatch
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = ("py", "database", "utils", "web")
DEVELOPMENT = (
    "tests/test_package_contents.py",
    "tools/vendor/package.json",
    "docs/application-redesign.md",
    ".github/workflows/publish.yml",
    "images/x.png",
    "TESTING.md",
    "requirements-dev.txt",
    "image_meta_data.png",
    "test_metadata_file.png",
)
KEPT = (
    "__init__.py",
    "prompt_manager.py",
    "pyproject.toml",
    "requirements.txt",
    "LICENSE",
    "README.md",
    "README-legacy.md",
    "assets/readme/hero.webp",
)


def _patterns():
    lines = (ROOT / ".comfyignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and line[0] != "#"]


def _ignored(path):
    """The rules this file allows: ``folder/`` from the root, or a root file."""
    for pattern in _patterns():
        if pattern.endswith("/"):
            if path.startswith(pattern.lstrip("/")):
                return True
        elif fnmatch.fnmatchcase(path, pattern.lstrip("/")):
            return True
    return False


class TestPackageContents(unittest.TestCase):
    def test_every_rule_is_anchored_to_the_root(self):
        # An unanchored "tests/" would also drop web/js/tests, and "images/" a
        # runtime folder of that name deeper down.
        for pattern in _patterns():
            self.assertTrue(pattern.startswith("/"), pattern)
            self.assertNotIn("!", pattern)

    def test_development_files_are_left_out(self):
        for path in DEVELOPMENT:
            self.assertTrue(_ignored(path), path)

    def test_what_comfyui_needs_stays(self):
        for path in KEPT:
            self.assertFalse(_ignored(path), path)
        for folder in RUNTIME_DIRS:
            for file in (ROOT / folder).rglob("*"):
                if file.is_file():
                    relative = file.relative_to(ROOT).as_posix()
                    self.assertFalse(_ignored(relative), relative)

    def test_runtime_code_imports_nothing_left_out(self):
        sources = list(ROOT.glob("*.py"))
        for folder in ("py", "database", "utils"):
            sources.extend((ROOT / folder).rglob("*.py"))
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                for name in names:
                    self.assertNotIn(name.split(".")[0], ("tests", "tools"), source)

    def test_the_old_pages_name_their_replacement(self):
        for page, section in (
            ("admin", "library"),
            ("gallery", "images"),
            ("metadata", "inspect"),
        ):
            html = (ROOT / "web" / f"{page}.html").read_text(encoding="utf-8")
            self.assertRegex(html, r'id="legacy-removal-notice"')
            self.assertRegex(html, re.escape(f'href="workspace#/{section}"'))


if __name__ == "__main__":
    sys.exit(unittest.main())
