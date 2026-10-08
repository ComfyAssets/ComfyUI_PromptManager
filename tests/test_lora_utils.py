"""
Unit tests for LoRA Manager integration utilities.

Tests metadata parsing, trigger word extraction, image URL extraction,
directory detection, TriggerWordCache, and image download logic.
"""

import email
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.response
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py import lora_utils
from py.lora_utils import (
    CIVITAI_HOSTS,
    MAX_CIVITAI_DOWNLOAD_BYTES,
    TriggerWordCache,
    download_civitai_images,
    get_civitai_image_urls,
    get_example_prompt_from_metadata,
    get_lora_image_cache_dir,
    get_trigger_words_from_metadata,
    is_civitai_url,
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


# ── CivitAI download tests (no network) ───────────────────────────────


def _png_bytes():
    """A valid 2x2 PNG so PIL can decode the fake download."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


class _FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, body, content_length=None):
        self._body = body
        self._pos = 0
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, amt=-1):
        if amt is None or amt < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Records every request and serves a canned response."""

    def __init__(self, response):
        self.response = response
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return self.response


class TestCivitaiUrlPolicy(unittest.TestCase):

    def test_allow_list_contains_expected_hosts(self):
        self.assertEqual(
            CIVITAI_HOSTS,
            frozenset(
                {
                    "civitai.com",
                    "www.civitai.com",
                    "api.civitai.com",
                    "image.civitai.com",
                }
            ),
        )
        self.assertEqual(MAX_CIVITAI_DOWNLOAD_BYTES, 50 * 1024 * 1024)

    def test_https_civitai_hosts_are_allowed(self):
        for host in CIVITAI_HOSTS:
            self.assertTrue(is_civitai_url(f"https://{host}/api/download/1"), host)

    def test_other_hosts_and_schemes_are_refused(self):
        for url in (
            "https://evil.example/x",
            "http://civitai.com/api/download/1",
            "https://civitai.com.evil.example/x",
            "https://notcivitai.com/x",
            "ftp://civitai.com/x",
            "not a url",
            "",
        ):
            self.assertFalse(is_civitai_url(url), url)


class TestDownloadOne(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.target = self.tmpdir / "out.jpg"

    def _run(self, url, opener, api_key="secret-key"):
        with patch.object(lora_utils, "_open_url", opener):
            return lora_utils._download_one(url, self.target, api_key)

    def test_off_host_url_is_refused_without_a_request(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))

        result = self._run("https://evil.example/x", opener)

        self.assertIsNone(result)
        self.assertEqual(opener.requests, [])
        self.assertFalse(self.target.exists())

    def test_plain_http_civitai_is_refused(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))

        result = self._run("http://civitai.com/api/download/1", opener)

        self.assertIsNone(result)
        self.assertEqual(opener.requests, [])

    def test_civitai_download_sends_bearer_header(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))

        result = self._run("https://civitai.com/api/download/1", opener)

        self.assertEqual(os.path.realpath(result), os.path.realpath(str(self.target)))
        self.assertEqual(len(opener.requests), 1)
        req = opener.requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer secret-key")
        self.assertTrue(self.target.exists())

    def test_no_bearer_header_without_a_key(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))

        self._run("https://image.civitai.com/a.png", opener, api_key="")

        self.assertIsNone(opener.requests[0].get_header("Authorization"))

    def test_oversized_content_length_is_refused(self):
        opener = _FakeOpener(
            _FakeResponse(_png_bytes(), content_length=MAX_CIVITAI_DOWNLOAD_BYTES + 1)
        )

        result = self._run("https://civitai.com/api/download/1", opener)

        self.assertIsNone(result)
        self.assertFalse(self.target.exists())

    def test_reading_stops_past_cap_when_length_is_absent(self):
        body = b"x" * (MAX_CIVITAI_DOWNLOAD_BYTES + 1)
        response = _FakeResponse(body)
        opener = _FakeOpener(response)

        with patch.object(lora_utils, "MAX_CIVITAI_DOWNLOAD_BYTES", 1024):
            result = self._run("https://civitai.com/api/download/1", opener)

        self.assertIsNone(result)
        self.assertLessEqual(response._pos, 1024 + lora_utils._DOWNLOAD_CHUNK_BYTES)
        self.assertFalse(self.target.exists())


class _FakeHTTPSHandler(urllib.request.HTTPSHandler):
    """Serves canned responses through a real urllib opener, recording requests.

    CivitAI hosts answer with a 302 to ``redirect_to``; every other host
    answers 200 with a PNG body.
    """

    def __init__(self, redirect_to):
        super().__init__()
        self.redirect_to = redirect_to
        self.requests = []

    def https_open(self, req):
        self.requests.append(req)
        host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
        if host in CIVITAI_HOSTS:
            headers = email.message_from_string(f"Location: {self.redirect_to}\n")
            return urllib.response.addinfourl(BytesIO(b""), headers, req.full_url, 302)
        headers = email.message_from_string("Content-Type: image/png\n")
        return urllib.response.addinfourl(
            BytesIO(_png_bytes()), headers, req.full_url, 200
        )


class TestRedirectsAreRefused(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_redirect_to_other_host_is_not_followed(self):
        handler = _FakeHTTPSHandler("https://evil.example/steal")
        opener = lora_utils._build_opener(handler)

        with patch.object(lora_utils, "_opener", opener):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.tmpdir / "x.jpg", "key"
            )

        self.assertIsNone(result)
        self.assertEqual(
            [r.full_url for r in handler.requests],
            ["https://civitai.com/api/download/1"],
        )
        for req in handler.requests:
            host = urllib.parse.urlsplit(req.full_url).hostname
            if host not in CIVITAI_HOSTS:
                self.assertIsNone(req.get_header("Authorization"))

    def test_redirect_to_civitai_is_not_followed_either(self):
        handler = _FakeHTTPSHandler("https://image.civitai.com/other.png")
        opener = lora_utils._build_opener(handler)

        with patch.object(lora_utils, "_opener", opener):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.tmpdir / "x.jpg", "key"
            )

        self.assertIsNone(result)
        self.assertEqual(len(handler.requests), 1)


class TestDownloadCivitaiImages(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.meta_path = self.tmpdir / "lora.safetensors.metadata.json"
        self.meta_path.write_text("{}")

    def test_only_civitai_urls_are_fetched(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        metadata = _make_metadata(
            images=[
                {"url": "https://evil.example/steal"},
                {"url": "https://civitai.com/api/download/1"},
                {"url": "not-a-url"},
            ]
        )

        with patch.object(lora_utils, "_open_url", opener):
            paths = download_civitai_images(
                metadata, self.meta_path, self.tmpdir / "cache", "key"
            )

        self.assertEqual(len(paths), 1)
        self.assertEqual(
            [r.full_url for r in opener.requests],
            ["https://civitai.com/api/download/1"],
        )

    def test_cached_files_are_not_refetched(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        metadata = _make_metadata(images=[{"url": "https://civitai.com/a.png"}])
        cache = self.tmpdir / "cache"

        with patch.object(lora_utils, "_open_url", opener):
            first = download_civitai_images(metadata, self.meta_path, cache, "")
            second = download_civitai_images(metadata, self.meta_path, cache, "")

        self.assertEqual(first, second)
        self.assertEqual(len(opener.requests), 1)

    def test_no_images_returns_empty(self):
        self.assertEqual(
            download_civitai_images({}, self.meta_path, self.tmpdir / "cache"), []
        )


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
