"""AVIF is a media type the library collects (#652).

Every file-type list names ``.avif``; only the collection points ask Pillow
(``utils.media_formats.collectable``), so an install whose Pillow cannot
decode AVIF behaves as it did before. The fixture is what ComfyUI's own
``SaveImageAdvanced`` writes (``tools/make_avif_fixture.py``): a still AVIF
whose EXIF item carries the ``prompt`` and ``workflow`` records.

The "Pillow cannot read it" cases patch ``avif_supported`` and run everywhere;
the "Pillow reads it" cases open the fixture and are skipped on a Pillow
built without an AVIF decoder.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from host_stubs import install_folder_paths_stub
except ImportError:  # imported as tests.test_avif_media_type
    from tests.host_stubs import install_folder_paths_stub

install_folder_paths_stub()
sys.modules.setdefault("server", MagicMock())

from py.api import images as images_module  # noqa: E402
from py.api.autotag_routes import AutotagRoutesMixin  # noqa: E402
from py.api.inspection_service import SERVABLE_EXTENSIONS  # noqa: E402
from py.api.thumbnail_settings import CONTENT_TYPES  # noqa: E402
from utils import media_capture, media_formats  # noqa: E402
from utils.image_monitor import ImageGenerationHandler  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "media" / "comfyui_avif.avif"
AVIF_READABLE = media_formats.avif_supported()


class TestMediaFormats(unittest.TestCase):
    """The one runtime answer every collection point asks."""

    def test_avif_supported_asks_pillow(self):
        with patch("PIL.features.check", return_value=True) as check:
            self.assertTrue(media_formats.avif_supported())
            check.assert_called_with("avif")
        with patch("PIL.features.check", return_value=False):
            self.assertFalse(media_formats.avif_supported())

    def test_collectable_gates_only_avif(self):
        with patch.object(media_formats, "avif_supported", return_value=False):
            self.assertFalse(media_formats.collectable(".avif"))
            self.assertFalse(media_formats.collectable(".AVIF"))
            self.assertTrue(media_formats.collectable(".png"))
            self.assertTrue(media_formats.collectable(".webp"))
            self.assertTrue(media_formats.collectable(".gif"))
        with patch.object(media_formats, "avif_supported", return_value=True):
            self.assertTrue(media_formats.collectable(".avif"))


class TestGalleryWalk(unittest.TestCase):
    """The gallery and thumbnail walks list AVIF only where it can be read."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        shutil.copyfile(FIXTURE, os.path.join(self.tmpdir, "gen.avif"))

    def _walked(self):
        return [
            Path(entry.path).name
            for entry in images_module._walk_media(self.tmpdir, 10)
        ]

    def test_the_walk_lists_avif_when_pillow_reads_it(self):
        with patch.object(media_formats, "avif_supported", return_value=True):
            self.assertEqual(self._walked(), ["gen.avif"])

    def test_the_walk_skips_avif_when_pillow_cannot_read_it(self):
        with patch.object(media_formats, "avif_supported", return_value=False):
            self.assertEqual(self._walked(), [])

    def test_the_settled_entry_agrees_with_the_walk(self):
        with patch.object(media_formats, "avif_supported", return_value=False):
            self.assertIsNone(images_module._settled_entry(self.tmpdir, "gen.avif"))
        with patch.object(media_formats, "avif_supported", return_value=True):
            path, _mtime = images_module._settled_entry(self.tmpdir, "gen.avif")
        self.assertEqual(Path(path).name, "gen.avif")


class TestMonitorAcceptance(unittest.TestCase):
    """The monitor watches an AVIF output only where Pillow can read it."""

    def _monitor(self):
        monitor = ImageGenerationHandler.__new__(ImageGenerationHandler)
        monitor.supported_extensions = (".png", ".avif")
        monitor.watched_roots = ()
        return monitor

    def test_an_avif_output_is_watched_when_pillow_reads_it(self):
        monitor = self._monitor()
        output = os.path.join(tempfile.gettempdir(), "output", "gen.avif")
        with patch.object(media_formats, "avif_supported", return_value=True):
            self.assertTrue(monitor.is_image_file(output))

    def test_an_avif_output_is_ignored_when_pillow_cannot_read_it(self):
        monitor = self._monitor()
        output = os.path.join(tempfile.gettempdir(), "output", "gen.avif")
        with patch.object(media_formats, "avif_supported", return_value=False):
            self.assertFalse(monitor.is_image_file(output))


class TestAutotagListing(unittest.TestCase):
    """The legacy auto-tag listing follows the same rule."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        shutil.copyfile(FIXTURE, os.path.join(self.tmpdir, "gen.avif"))

    def test_the_listing_names_avif_when_pillow_reads_it(self):
        with patch.object(media_formats, "avif_supported", return_value=True):
            found, _truncated = AutotagRoutesMixin._collect_scan_files(
                Path(self.tmpdir)
            )
        self.assertEqual([path.name for path in found], ["gen.avif"])

    def test_the_listing_skips_avif_when_pillow_cannot_read_it(self):
        with patch.object(media_formats, "avif_supported", return_value=False):
            found, _truncated = AutotagRoutesMixin._collect_scan_files(
                Path(self.tmpdir)
            )
        self.assertEqual(found, [])


class TestCaptureGate(unittest.TestCase):
    """The capture store refuses an AVIF the listing would not offer."""

    def test_avif_is_capturable_only_when_pillow_reads_it(self):
        with patch.object(media_formats, "avif_supported", return_value=False):
            with self.assertRaises(media_capture.CaptureDenied):
                media_capture._relative_parts("gen.avif")
        with patch.object(media_formats, "avif_supported", return_value=True):
            _parts, extension = media_capture._relative_parts("gen.avif")
        self.assertEqual(extension, ".avif")


class TestServedTypes(unittest.TestCase):
    """AVIF is served and inspected as itself, whatever this Pillow reads."""

    def test_avif_is_in_the_servable_set(self):
        self.assertIn(".avif", SERVABLE_EXTENSIONS)
        self.assertEqual(media_capture.CAPTURE_EXTENSIONS, SERVABLE_EXTENSIONS)

    def test_the_content_type_is_named(self):
        self.assertEqual(CONTENT_TYPES[".avif"], "image/avif")


@unittest.skipUnless(AVIF_READABLE, "Pillow here cannot decode AVIF")
class TestMonitorLinksAnAvif(unittest.TestCase):
    """The monitor reads the AVIF's own EXIF and links its positive prompt."""

    def setUp(self):
        from database.operations import PromptDatabase
        from tests.background_stops import close_handler
        from utils.hashing import generate_prompt_hash
        from utils.image_monitor import ImageGenerationHandler
        from utils.prompt_tracker import PromptTracker

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "prompts.db")
        self.image = os.path.join(self.tmpdir, "gen.avif")
        shutil.copyfile(FIXTURE, self.image)
        self.db = PromptDatabase(self.db_path)
        self.handler = ImageGenerationHandler(self.db, PromptTracker(self.db))
        self.linked = []
        self.handler.link_image_to_prompt = (
            lambda path, prompt, meta: self.linked.append(prompt["id"])
        )
        self.positive = self.db.save_prompt(
            text="a small avif fixture",
            prompt_hash=generate_prompt_hash("a small avif fixture"),
        )
        self.db.save_prompt(text="bad", prompt_hash=generate_prompt_hash("bad"))
        self.addCleanup(self._close, close_handler)

    def _close(self, close_handler):
        close_handler(self, self.handler)  # and the tracker's cleanup thread
        self.db.close_all()  # Windows cannot unlink an open database file

    def test_the_output_links_to_its_positive_prompt(self):
        self.handler.process_new_image(self.image)

        self.assertEqual(self.linked, [self.positive])


@unittest.skipUnless(AVIF_READABLE, "Pillow here cannot decode AVIF")
class TestTheFixture(unittest.TestCase):
    """The checked-in AVIF is ComfyUI's output shape, and both records read."""

    def test_exif_text_answers_both_records(self):
        from PIL import Image

        from utils.exif_text import exif_text
        from utils.png_text import png_text

        with Image.open(FIXTURE) as image:
            self.assertEqual(png_text(image), {})
            text = exif_text(image)

        self.assertEqual(sorted(text), ["prompt", "workflow"])
        self.assertIn("a small avif fixture", text["prompt"])
        self.assertIn("KSampler", text["workflow"])
