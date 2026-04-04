"""
Unit tests for LoRA Manager integration utilities.

Tests metadata parsing, trigger word extraction, image URL extraction,
directory detection, TriggerWordCache, and image download logic.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.lora_utils import (
    TriggerWordCache,
    get_civitai_image_urls,
    get_example_prompt_from_metadata,
    get_lora_image_cache_dir,
    get_trigger_words_from_metadata,
    read_lora_metadata,
)

# ── Sample metadata fixtures ───────────────────────────────────────────


def _make_metadata(
    trained_words=None,
    images=None,
    model_name="test_lora",
    file_name="test.safetensors",
):
    """Build a realistic LoRA metadata dict for testing."""
    meta = {"file_name": file_name}
    civitai = {}
    if trained_words is not None:
        civitai["trainedWords"] = trained_words
    if images is not None:
        civitai["images"] = images
    if model_name:
        civitai["model"] = {"name": model_name}
    if civitai:
        meta["civitai"] = civitai
    return meta


# ── Pure function tests (no mocking) ──────────────────────────────────


class TestGetTriggerWords(unittest.TestCase):
    """Test get_trigger_words_from_metadata — pure dict extraction."""

    def test_extracts_words(self):
        meta = _make_metadata(trained_words=["word1", "word2", "word3"])
        self.assertEqual(
            get_trigger_words_from_metadata(meta), ["word1", "word2", "word3"]
        )

    def test_strips_whitespace(self):
        meta = _make_metadata(trained_words=["  padded  ", "\ttabbed\t"])
        self.assertEqual(get_trigger_words_from_metadata(meta), ["padded", "tabbed"])

    def test_filters_empty_strings(self):
        meta = _make_metadata(trained_words=["valid", "", "  ", "also_valid"])
        self.assertEqual(get_trigger_words_from_metadata(meta), ["valid", "also_valid"])

    def test_no_civitai_key(self):
        self.assertEqual(get_trigger_words_from_metadata({}), [])

    def test_no_trained_words(self):
        meta = _make_metadata()
        self.assertEqual(get_trigger_words_from_metadata(meta), [])

    def test_trained_words_not_list(self):
        meta = {"civitai": {"trainedWords": "not a list"}}
        self.assertEqual(get_trigger_words_from_metadata(meta), [])

    def test_non_string_items_filtered(self):
        meta = _make_metadata(trained_words=["valid", 123, None, "also_valid"])
        self.assertEqual(get_trigger_words_from_metadata(meta), ["valid", "also_valid"])


class TestGetExamplePrompt(unittest.TestCase):
    """Test get_example_prompt_from_metadata — extracts first usable prompt."""

    def test_extracts_first_prompt(self):
        images = [
            {"meta": {"prompt": "a beautiful landscape"}},
            {"meta": {"prompt": "second prompt"}},
        ]
        meta = _make_metadata(images=images)
        self.assertEqual(
            get_example_prompt_from_metadata(meta), "a beautiful landscape"
        )

    def test_skips_empty_prompts(self):
        images = [
            {"meta": {"prompt": ""}},
            {"meta": {"prompt": "   "}},
            {"meta": {"prompt": "valid prompt"}},
        ]
        meta = _make_metadata(images=images)
        self.assertEqual(get_example_prompt_from_metadata(meta), "valid prompt")

    def test_no_images(self):
        meta = _make_metadata(images=[])
        self.assertIsNone(get_example_prompt_from_metadata(meta))

    def test_no_civitai(self):
        self.assertIsNone(get_example_prompt_from_metadata({}))

    def test_images_without_meta(self):
        images = [{"url": "http://example.com/img.jpg"}]
        meta = _make_metadata(images=images)
        self.assertIsNone(get_example_prompt_from_metadata(meta))

    def test_meta_without_prompt(self):
        images = [{"meta": {"seed": 12345}}]
        meta = _make_metadata(images=images)
        self.assertIsNone(get_example_prompt_from_metadata(meta))

    def test_non_dict_images_skipped(self):
        images = ["not a dict", None, {"meta": {"prompt": "found it"}}]
        meta = _make_metadata(images=images)
        self.assertEqual(get_example_prompt_from_metadata(meta), "found it")

    def test_non_string_prompt_skipped(self):
        images = [{"meta": {"prompt": 12345}}, {"meta": {"prompt": "real prompt"}}]
        meta = _make_metadata(images=images)
        self.assertEqual(get_example_prompt_from_metadata(meta), "real prompt")


class TestGetCivitaiImageUrls(unittest.TestCase):
    """Test get_civitai_image_urls — extracts image URLs from metadata."""

    def test_extracts_urls(self):
        images = [
            {"url": "https://civitai.com/img1.jpg"},
            {"url": "https://civitai.com/img2.jpg"},
        ]
        meta = _make_metadata(images=images)
        urls = get_civitai_image_urls(meta)
        self.assertEqual(len(urls), 2)
        self.assertIn("https://civitai.com/img1.jpg", urls)

    def test_filters_empty_urls(self):
        images = [{"url": ""}, {"url": "https://civitai.com/valid.jpg"}]
        meta = _make_metadata(images=images)
        urls = get_civitai_image_urls(meta)
        self.assertEqual(urls, ["https://civitai.com/valid.jpg"])

    def test_no_images(self):
        meta = _make_metadata(images=[])
        self.assertEqual(get_civitai_image_urls(meta), [])

    def test_no_civitai(self):
        self.assertEqual(get_civitai_image_urls({}), [])

    def test_images_without_url_key(self):
        images = [{"id": 1}, {"url": "https://civitai.com/valid.jpg"}]
        meta = _make_metadata(images=images)
        urls = get_civitai_image_urls(meta)
        self.assertEqual(urls, ["https://civitai.com/valid.jpg"])

    def test_non_dict_images_skipped(self):
        images = [None, "bad", {"url": "https://civitai.com/valid.jpg"}]
        meta = _make_metadata(images=images)
        urls = get_civitai_image_urls(meta)
        self.assertEqual(urls, ["https://civitai.com/valid.jpg"])


# ── Filesystem-dependent tests ────────────────────────────────────────


class TestReadLoraMetadata(unittest.TestCase):
    """Test read_lora_metadata — file I/O with JSON parsing."""

    def test_reads_valid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".metadata.json", delete=False
        ) as f:
            json.dump({"civitai": {"trainedWords": ["test"]}}, f)
            f.flush()
            path = Path(f.name)
        try:
            result = read_lora_metadata(path)
            self.assertIsNotNone(result)
            self.assertEqual(result["civitai"]["trainedWords"], ["test"])
        finally:
            os.unlink(path)

    def test_returns_none_for_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".metadata.json", delete=False
        ) as f:
            f.write("not valid json {{{")
            f.flush()
            path = Path(f.name)
        try:
            result = read_lora_metadata(path)
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_returns_none_for_missing_file(self):
        result = read_lora_metadata(Path("/nonexistent/file.metadata.json"))
        self.assertIsNone(result)


class TestGetLoraImageCacheDir(unittest.TestCase):
    """Test get_lora_image_cache_dir — returns and creates cache path."""

    def test_returns_path(self):
        cache_dir = get_lora_image_cache_dir()
        self.assertIsInstance(cache_dir, Path)
        self.assertTrue(str(cache_dir).endswith("data/lora_images"))

    def test_directory_exists(self):
        cache_dir = get_lora_image_cache_dir()
        self.assertTrue(cache_dir.is_dir())


# ── TriggerWordCache tests ────────────────────────────────────────────


class TestTriggerWordCache(unittest.TestCase):
    """Test TriggerWordCache — thread-safe trigger word lookup."""

    def setUp(self):
        self.cache = TriggerWordCache()

    def test_initial_state(self):
        self.assertFalse(self.cache.is_loaded)
        self.assertEqual(self.cache.get_trigger_words("anything"), [])

    def test_load_from_temp_directory(self):
        """Create temp metadata files and verify cache loads them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a metadata file
            meta = {
                "file_name": "my_lora.safetensors",
                "civitai": {"trainedWords": ["trigger1", "trigger2"]},
            }
            meta_path = Path(tmpdir) / "my_lora.safetensors.metadata.json"
            meta_path.write_text(json.dumps(meta))

            # Patch find_lora_directories to return our temp dir
            with patch("py.lora_utils.find_lora_directories", return_value=[tmpdir]):
                count = self.cache.load(tmpdir)

            self.assertTrue(self.cache.is_loaded)
            # Cache keys by both file_name stem and metadata filename stem
            self.assertGreaterEqual(count, 1)
            self.assertEqual(
                self.cache.get_trigger_words("my_lora"), ["trigger1", "trigger2"]
            )

    def test_case_insensitive_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {
                "file_name": "MyLoRA.safetensors",
                "civitai": {"trainedWords": ["word1"]},
            }
            (Path(tmpdir) / "MyLoRA.safetensors.metadata.json").write_text(
                json.dumps(meta)
            )

            with patch("py.lora_utils.find_lora_directories", return_value=[tmpdir]):
                self.cache.load(tmpdir)

            self.assertEqual(self.cache.get_trigger_words("mylora"), ["word1"])
            self.assertEqual(self.cache.get_trigger_words("MYLORA"), ["word1"])

    def test_clear(self):
        # Manually set cache state
        self.cache._cache = {"test": ["word"]}
        self.cache._loaded = True

        self.cache.clear()
        self.assertFalse(self.cache.is_loaded)
        self.assertEqual(self.cache.get_trigger_words("test"), [])

    def test_unknown_lora_returns_empty(self):
        self.cache._cache = {"known": ["word"]}
        self.cache._loaded = True
        self.assertEqual(self.cache.get_trigger_words("unknown"), [])

    def test_thread_safety(self):
        """Verify concurrent access doesn't raise."""
        self.cache._cache = {"lora": ["word"]}
        self.cache._loaded = True

        errors = []

        def reader():
            try:
                for _ in range(100):
                    self.cache.get_trigger_words("lora")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
