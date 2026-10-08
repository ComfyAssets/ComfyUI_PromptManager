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
            resp = urllib.response.addinfourl(BytesIO(b""), headers, req.full_url, 302)
            resp.msg = "Found"  # urllib's error processor reads response.msg
            return resp
        headers = email.message_from_string("Content-Type: image/png\n")
        resp = urllib.response.addinfourl(
            BytesIO(_png_bytes()), headers, req.full_url, 200
        )
        resp.msg = "OK"
        return resp


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


# ── Detection, directories and metadata helpers ───────────────────────


class _FakeFolderPaths:
    def __init__(self, base_path, lora_dirs=None):
        self.base_path = base_path
        self._lora_dirs = lora_dirs or []

    def get_folder_paths(self, name):
        return list(self._lora_dirs)


def _make_comfy_root(tmp):
    """Create a fake ComfyUI tree: main.py + custom_nodes/ + models/loras."""
    root = Path(tmp) / "ComfyUI"
    (root / "custom_nodes").mkdir(parents=True)
    (root / "models" / "loras").mkdir(parents=True)
    (root / "main.py").write_text("")
    return root


def _make_lora_manager(custom_nodes, name="ComfyUI-Lora-Manager"):
    lm = custom_nodes / name
    (lm / "py").mkdir(parents=True)
    (lm / "__init__.py").write_text("")
    return lm


class TestFindComfyuiRoot(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_folder_paths_base_path_wins(self):
        root = _make_comfy_root(self.tmp)
        with patch.dict(sys.modules, {"folder_paths": _FakeFolderPaths(str(root))}):
            self.assertEqual(lora_utils.find_comfyui_root(), root)

    def test_walks_up_from_module_file_without_folder_paths(self):
        root = _make_comfy_root(self.tmp)
        fake_file = root / "custom_nodes" / "ext" / "py" / "lora_utils.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("")
        with (
            patch.dict(sys.modules, {"folder_paths": None}),
            patch.object(lora_utils, "__file__", str(fake_file)),
        ):
            self.assertEqual(lora_utils.find_comfyui_root(), root.resolve())

    def test_returns_none_when_no_root_found(self):
        fake_file = self.tmp / "lonely" / "lora_utils.py"
        fake_file.parent.mkdir()
        fake_file.write_text("")
        with (
            patch.dict(sys.modules, {"folder_paths": None}),
            patch.object(lora_utils, "__file__", str(fake_file)),
        ):
            self.assertIsNone(lora_utils.find_comfyui_root())


class TestDetectLoraManager(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_custom_path_is_used_when_it_looks_right(self):
        lm = _make_lora_manager(self.tmp)
        with patch.object(lora_utils, "find_comfyui_root", return_value=None):
            self.assertEqual(lora_utils.detect_lora_manager(str(lm)), str(lm.resolve()))

    def test_custom_path_without_init_is_ignored(self):
        bogus = self.tmp / "nope"
        bogus.mkdir()
        with patch.object(lora_utils, "find_comfyui_root", return_value=None):
            self.assertIsNone(lora_utils.detect_lora_manager(str(bogus)))

    def test_auto_detects_under_custom_nodes_case_insensitively(self):
        root = _make_comfy_root(self.tmp)
        (root / "custom_nodes" / "other").mkdir()
        lm = _make_lora_manager(root / "custom_nodes", "comfyui-LORA-manager")
        with patch.object(lora_utils, "find_comfyui_root", return_value=root):
            self.assertEqual(lora_utils.detect_lora_manager(), str(lm.resolve()))

    def test_lora_manager_dir_variant_is_accepted(self):
        lm = self.tmp / "LoraManager"
        (lm / "lora_manager").mkdir(parents=True)
        (lm / "__init__.py").write_text("")
        self.assertTrue(lora_utils._looks_like_lora_manager(lm))

    def test_nothing_found(self):
        with patch.object(lora_utils, "find_comfyui_root", return_value=None):
            self.assertIsNone(lora_utils.detect_lora_manager())


class TestFindLoraDirectories(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _make_comfy_root(self.tmp)
        self.lm = _make_lora_manager(self.root / "custom_nodes")

    def test_collects_default_extra_runtime_and_metadata_dirs(self):
        extra_abs = self.tmp / "extra_abs"
        extra_abs.mkdir()
        (self.root / "rel_loras").mkdir()
        (self.root / "extra_model_paths.yaml").write_text(
            "a:\n  base_path: %s\n  loras: |\n    rel_loras\n    %s\n\n"
            "b: notadict\nc:\n  base_path: x\n"
            % (self.root.as_posix(), extra_abs.as_posix())
        )
        runtime = self.tmp / "runtime"
        runtime.mkdir()
        nested = self.lm / "user" / "loras"
        nested.mkdir(parents=True)
        (nested / "x.metadata.json").write_text("{}")

        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=self.root),
            patch.dict(
                sys.modules,
                {"folder_paths": _FakeFolderPaths(str(self.root), [str(runtime)])},
            ),
        ):
            dirs = lora_utils.find_lora_directories(str(self.lm))

        expected = {
            str((self.root / "models" / "loras").resolve()),
            str((self.root / "rel_loras").resolve()),
            str(extra_abs.resolve()),
            str(runtime.resolve()),
            str(nested.resolve()),
        }
        self.assertEqual(set(dirs), expected)
        self.assertEqual(dirs, sorted(dirs))

    def test_non_dict_yaml_and_parse_errors_are_ignored(self):
        (self.root / "extra_model_paths.yml").write_text("- just\n- a list\n")
        self.assertEqual(lora_utils._get_extra_lora_paths(self.root), [])
        (self.root / "extra_model_paths.yml").write_text("a: [unclosed\n")
        self.assertEqual(lora_utils._get_extra_lora_paths(self.root), [])

    def test_without_root_only_metadata_dirs_are_found(self):
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=None),
            patch.dict(sys.modules, {"folder_paths": None}),
        ):
            self.assertEqual(lora_utils.find_lora_directories(str(self.lm)), [])


class TestModelNameAndPreviews(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_model_name_precedence(self):
        get_name = lora_utils.get_model_name_from_metadata
        self.assertEqual(get_name({"model_name": "top"}), "top")
        self.assertEqual(get_name({"civitai": {"model": {"name": "civ"}}}), "civ")
        self.assertEqual(get_name({"file_name": "f.safetensors"}), "f.safetensors")
        self.assertEqual(get_name({}), "unknown")

    def test_preview_images_found_by_stem(self):
        meta_path = self.tmp / "mylora.safetensors.metadata.json"
        meta_path.write_text("{}")
        (self.tmp / "mylora.png").write_bytes(b"x")
        (self.tmp / "mylora.preview.jpg").write_bytes(b"x")

        found = lora_utils.get_preview_images_from_metadata({}, meta_path)

        self.assertEqual(
            sorted(os.path.basename(f) for f in found),
            ["mylora.png", "mylora.preview.jpg"],
        )
        self.assertEqual(
            lora_utils.get_preview_image_from_metadata({}, meta_path), found[0]
        )

    def test_preview_uses_file_name_from_metadata(self):
        meta_path = self.tmp / "other.metadata.json"
        (self.tmp / "named.webp").write_bytes(b"x")
        found = lora_utils.get_preview_images_from_metadata(
            {"file_name": "named.safetensors"}, meta_path
        )
        self.assertEqual([os.path.basename(f) for f in found], ["named.webp"])
        self.assertIsNone(lora_utils.get_preview_image_from_metadata({}, meta_path))

    def test_example_images_dir(self):
        self.assertIsNone(lora_utils.get_example_images_dir(str(self.tmp)))
        nested = self.tmp / "user" / "example_images"
        nested.mkdir(parents=True)
        self.assertEqual(
            lora_utils.get_example_images_dir(str(self.tmp)), str(nested.resolve())
        )
        direct = self.tmp / "example_images"
        direct.mkdir()
        self.assertEqual(
            lora_utils.get_example_images_dir(str(self.tmp)), str(direct.resolve())
        )


class TestDownloadEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_malformed_url_is_refused(self):
        self.assertFalse(is_civitai_url("https://[invalid"))

    def test_non_numeric_content_length_is_ignored(self):
        resp = _FakeResponse(b"abc")
        resp.headers["Content-Length"] = "lots"
        self.assertEqual(lora_utils._read_capped(resp, 10), b"abc")

    def test_download_uses_metadata_filename_when_file_name_missing(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        meta_path = self.tmp / "stemmed.safetensors.metadata.json"
        metadata = {"civitai": {"images": [{"url": "https://civitai.com/a.png"}]}}

        with patch.object(lora_utils, "_open_url", opener):
            paths = download_civitai_images(metadata, meta_path, self.tmp / "cache")

        self.assertEqual(len(paths), 1)
        self.assertIn("stemmed", paths[0])

    def test_failed_downloads_are_dropped(self):
        opener = _FakeOpener(_FakeResponse(b"not an image"))
        metadata = {"civitai": {"images": [{"url": "https://civitai.com/a.png"}]}}

        with patch.object(lora_utils, "_open_url", opener):
            paths = download_civitai_images(
                metadata, self.tmp / "x.metadata.json", self.tmp / "cache"
            )

        self.assertEqual(paths, [])


class TestInjectTriggerWords(unittest.TestCase):

    def setUp(self):
        self.cache = TriggerWordCache()
        self.cache._cache = {"style": ["neon", "glow"], "empty": []}
        self.cache._loaded = True

    def test_not_loaded_returns_unchanged(self):
        cache = TriggerWordCache()
        self.assertEqual(
            lora_utils.inject_trigger_words("<lora:style:1>", cache),
            ("<lora:style:1>", []),
        )

    def test_no_lora_tags_returns_unchanged(self):
        self.assertEqual(
            lora_utils.inject_trigger_words("plain prompt", self.cache),
            ("plain prompt", []),
        )

    def test_words_are_appended_once(self):
        text = "a <lora:Style:0.8> b <lora:style:1>"
        self.assertEqual(
            lora_utils.inject_trigger_words(text, self.cache),
            (f"{text}, neon, glow", ["neon", "glow"]),
        )

    def test_words_already_present_are_not_repeated(self):
        text = "NEON city <lora:style:1>"
        self.assertEqual(
            lora_utils.inject_trigger_words(text, self.cache),
            (f"{text}, glow", ["glow"]),
        )

    def test_lora_without_words_leaves_text_alone(self):
        text = "x <lora:empty:1> <lora:unknown:1>"
        self.assertEqual(lora_utils.inject_trigger_words(text, self.cache), (text, []))

    def test_cache_load_skips_bad_and_wordless_metadata(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "bad.metadata.json").write_text("{{{")
        (tmp / "nowords.metadata.json").write_text("{}")
        (tmp / "named.metadata.json").write_text(
            json.dumps({"civitai": {"trainedWords": ["w"]}})
        )
        cache = TriggerWordCache()
        with patch.object(lora_utils, "find_lora_directories", return_value=[str(tmp)]):
            self.assertEqual(cache.load(str(tmp)), 1)
        self.assertEqual(cache.get_trigger_words("named"), ["w"])


if __name__ == "__main__":
    unittest.main()
