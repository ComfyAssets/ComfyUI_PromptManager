"""Thumbnail settings and naming (#317): the one module every generator reads.

``py/api/thumbnail_settings.py`` owns ``thumbnail_format`` / ``thumbnail_quality``
/ ``thumbnail_edge`` (``PromptManagerConfig``, config.json ``thumbnails``), the
preview's name ``thumbnails/<stem>_thumb.<ext of the format>`` and the lookup
that accepts any preview a source owns, preferring the configured format.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from unittest.mock import MagicMock

sys.modules.setdefault("server", MagicMock())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import thumbnail_settings as ts  # noqa: E402
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self._orig = PromptManagerConfig.get_config()
        self.addCleanup(PromptManagerConfig.update_config, self._orig)


class TestDefaultsAndConfig(ConfigCase):
    def test_defaults_are_webp_80_512(self):
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "webp", "quality": 80, "edge": 512}}
        )
        settings = ts.current()
        self.assertEqual(
            (settings.format, settings.quality, settings.edge), ("webp", 80, 512)
        )
        self.assertEqual(settings.extension, ".webp")
        self.assertEqual(settings.size, (512, 512))
        self.assertEqual(
            (ts.DEFAULT_FORMAT, ts.DEFAULT_QUALITY, ts.DEFAULT_EDGE), ("webp", 80, 512)
        )

    def test_config_round_trip_in_a_thumbnails_section(self):
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "jpeg", "quality": 55, "edge": 300}}
        )
        self.assertEqual(
            PromptManagerConfig.get_config()["thumbnails"],
            {"format": "jpeg", "quality": 55, "edge": 300},
        )
        self.assertEqual(ts.current().extension, ".jpg")

    def test_hand_edited_bad_values_are_ignored(self):
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "webp", "quality": 80, "edge": 512}}
        )
        for bad in (
            {"format": "png"},
            {"format": 3},
            {"quality": 0},
            {"quality": 101},
            {"quality": True},
            {"quality": "80"},
            {"edge": 64},
            {"edge": 4096},
            {"edge": 512.5},
        ):
            PromptManagerConfig.update_config({"thumbnails": bad})
        self.assertEqual(
            (ts.current().format, ts.current().quality, ts.current().edge),
            ("webp", 80, 512),
        )
        PromptManagerConfig.update_config({"thumbnails": "nonsense"})
        self.assertEqual(ts.current().format, "webp")

    def test_the_never_read_gallery_thumbnail_size_is_gone(self):
        # R1: older save_to_file wrote its default 256 into config.json; it
        # must never size a preview.
        self.assertFalse(hasattr(GalleryConfig, "THUMBNAIL_SIZE"))
        PromptManagerConfig.update_config(
            {"gallery": {"web_interface": {"thumbnail_size": 256}}}
        )
        self.assertNotIn("thumbnail_size", GalleryConfig.get_config()["web_interface"])
        self.assertNotEqual(ts.current().edge, 256)

    def test_load_from_file_reads_the_section(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as handle:
            json.dump({"thumbnails": {"format": "jxl", "quality": 90}}, handle)
        PromptManagerConfig.load_from_file(path)
        self.assertEqual((ts.current().format, ts.current().quality), ("jxl", 90))


class TestValidation(unittest.TestCase):
    def test_format(self):
        for value in ("webp", "jxl", "jpeg"):
            self.assertEqual(ts.validate_format(value), value)
        for value in ("png", "JPEG", "", None, 1):
            with self.assertRaises(ValueError):
                ts.validate_format(value)

    def test_quality(self):
        self.assertEqual(ts.validate_quality(1), 1)
        self.assertEqual(ts.validate_quality(100), 100)
        for value in (0, 101, True, 80.0, "80", None):
            with self.assertRaises(ValueError):
                ts.validate_quality(value)

    def test_edge(self):
        self.assertEqual(ts.validate_edge(128), 128)
        self.assertEqual(ts.validate_edge(2048), 2048)
        for value in (127, 2049, False, 512.0, "512"):
            with self.assertRaises(ValueError):
                ts.validate_edge(value)


class TestNaming(ConfigCase):
    def setUp(self):
        super().setUp()
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "webp", "quality": 80, "edge": 512}}
        )

    def test_images_and_videos_take_the_configured_extension(self):
        self.assertEqual(
            ts.thumbnail_rel(Path("sub/a.PNG")), "thumbnails/sub/a_thumb.webp"
        )
        self.assertEqual(
            ts.thumbnail_rel(Path("clip.mp4")), "thumbnails/clip_thumb.webp"
        )
        self.assertEqual(
            ts.thumbnail_rel(Path("a.png"), ".jxl"), "thumbnails/a_thumb.jxl"
        )

    def test_ownership(self):
        # R4: legacy-only extensions belong to a source of that extension.
        self.assertTrue(ts.owns(".png", ".png"))
        self.assertTrue(ts.owns(".png", ".PNG"))
        self.assertFalse(ts.owns(".png", ".mp4"))
        self.assertFalse(ts.owns(".png", ".jpg"))
        self.assertTrue(ts.owns(".jpeg", ".jpeg"))
        self.assertTrue(ts.owns(".gif", ".gif"))
        for ext in (".webp", ".jxl", ".jpg"):
            for source in (".png", ".mp4", ".gif", ".webp"):
                self.assertTrue(ts.owns(ext, source))
        self.assertFalse(ts.owns(".txt", ".png"))

    def test_candidates_prefer_the_configured_format_then_the_legacy_name(self):
        self.assertEqual(
            ts.candidate_extensions(".png", ".webp"), (".webp", ".png", ".jxl", ".jpg")
        )
        self.assertEqual(
            ts.candidate_extensions(".mp4", ".jxl"), (".jxl", ".jpg", ".webp")
        )
        self.assertEqual(
            ts.candidate_extensions(".jpg", ".jpg"), (".jpg", ".webp", ".jxl")
        )

    def test_content_types(self):
        self.assertEqual(ts.content_type(Path("a_thumb.webp")), "image/webp")
        self.assertEqual(ts.content_type(Path("a_thumb.jxl")), "image/jxl")
        self.assertEqual(ts.content_type(Path("a_thumb.jpg")), "image/jpeg")
        self.assertEqual(ts.content_type(Path("a_thumb.PNG")), "image/png")
        self.assertEqual(ts.content_type(Path("a_thumb.gif")), "image/gif")
        self.assertIsNone(ts.content_type(Path("a.txt")))


class TestLookup(ConfigCase):
    def setUp(self):
        super().setUp()
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "webp", "quality": 80, "edge": 512}}
        )
        self.root = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def test_missing_preview_is_none(self):
        self.assertIsNone(ts.existing_thumbnail(self.root, Path("a.png")))

    def test_legacy_preview_is_found_and_the_configured_one_preferred(self):
        legacy = self._touch("thumbnails/sub/a_thumb.png")
        self.assertEqual(ts.existing_thumbnail(self.root, Path("sub/a.png")), legacy)
        fresh = self._touch("thumbnails/sub/a_thumb.webp")
        self.assertEqual(ts.existing_thumbnail(self.root, Path("sub/a.png")), fresh)
        self.assertEqual(
            sorted(ts.owned_thumbnails(self.root, Path("sub/a.png"))),
            sorted([legacy, fresh]),
        )

    def test_a_preview_owned_by_another_source_is_not_used(self):
        self._touch("thumbnails/a_thumb.png")
        self.assertIsNone(ts.existing_thumbnail(self.root, Path("a.mp4")))
        video = self._touch("thumbnails/a_thumb.jpg")
        self.assertEqual(ts.existing_thumbnail(self.root, Path("a.mp4")), video)

    def test_a_folder_or_link_is_no_preview(self):
        (self.root / "thumbnails" / "a_thumb.webp").mkdir(parents=True)
        self.assertIsNone(ts.existing_thumbnail(self.root, Path("a.png")))


if __name__ == "__main__":
    unittest.main()
