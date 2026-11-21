"""The logo and icons are wired in where a user sees them (#632).

Every page Prompt Manager serves names the favicon, the files it names exist
at the sizes they claim, the README and the Registry metadata point at files
that are in the repository, and the Registry package keeps what the pages
load at run time.
"""

import re
import tomllib
import unittest
from pathlib import Path

from PIL import Image

try:
    from tests.test_package_contents import _ignored
except ImportError:  # discovered with tests/ as the top-level directory
    from test_package_contents import _ignored

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("workspace.html", "admin-redirect.html", "index.html")
ICON_LINK = re.compile(
    r'<link rel="(icon|apple-touch-icon)"(?: type="image/png")?'
    r' sizes="(\d+)x(\d+)" href="(lib/brand/[a-z0-9-]+\.png)">'
)
RAW_PREFIX = "https://raw.githubusercontent.com/ComfyAssets/ComfyUI_PromptManager/main/"


def _size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


class TestBrandAssets(unittest.TestCase):
    def test_every_page_links_icons_that_exist_at_the_stated_size(self):
        for page in PAGES:
            with self.subTest(page=page):
                html = (ROOT / "web" / page).read_text(encoding="utf-8")
                links = ICON_LINK.findall(html)
                self.assertEqual(
                    [kind for kind, *_ in links], ["icon", "apple-touch-icon"]
                )
                for _kind, width, height, href in links:
                    # Pages sit beside lib/ under /prompt_manager/, like their CSS.
                    file = ROOT / "web" / href
                    self.assertEqual(_size(file), (int(width), int(height)), href)

    def test_page_icons_ship_in_the_registry_package(self):
        for file in sorted((ROOT / "web" / "lib" / "brand").iterdir()):
            relative = file.relative_to(ROOT).as_posix()
            self.assertFalse(_ignored(relative), relative)
            self.assertLess(file.stat().st_size, 16 * 1024, relative)

    def test_readme_opens_with_the_banner(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        first = next(line for line in readme.splitlines() if line.strip())
        match = re.fullmatch(r"!\[[^\]]+\]\((assets/brand/[a-z0-9-]+\.webp)\)", first)
        self.assertIsNotNone(match, first)
        self.assertEqual(_size(ROOT / match.group(1)), (1280, 640))

    def test_registry_icon_is_the_app_icon_in_this_repository(self):
        with open(ROOT / "pyproject.toml", "rb") as handle:
            icon = tomllib.load(handle)["tool"]["comfy"]["Icon"]
        self.assertTrue(icon.startswith(RAW_PREFIX), icon)
        self.assertEqual(_size(ROOT / icon[len(RAW_PREFIX) :]), (256, 256))


if __name__ == "__main__":
    unittest.main()
