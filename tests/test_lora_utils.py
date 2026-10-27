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
import time
import types
import unittest
import urllib.error
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
        self.assertEqual(cache_dir.parts[-2:], ("data", "lora_images"))

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
                f"{prefix}{domain}"
                for domain in ("civitai.com", "civitai.red", "civitai.green")
                for prefix in ("", "www.", "api.", "image.")
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

    def test_userinfo_and_non_default_ports_are_refused(self):
        for url in (
            "https://evil.com\\@civitai.com/x",
            "https://user@civitai.com/x",
            "https://user:pw@civitai.com/x",
            "https://civitai.com:8443/x",
        ):
            self.assertFalse(is_civitai_url(url), url)

    def test_explicit_default_port_is_allowed(self):
        self.assertTrue(is_civitai_url("https://civitai.com:443/x"))

    def test_red_and_green_starting_hosts_are_allowed(self):
        # #340: CivitAI's own .red and .green domains serve the same content.
        for url in (
            "https://image.civitai.red/xG1/abc/width=450/1.jpeg",
            "https://civitai.red/api/download/1",
            "https://civitai.green/api/download/1",
            "https://image.civitai.green/xG1/abc/1.jpeg",
            "https://IMAGE.CIVITAI.RED/x",
            "https://civitai.green:443/x",
        ):
            self.assertTrue(is_civitai_url(url), url)

    def test_red_and_green_lookalikes_are_refused(self):
        for url in (
            "http://image.civitai.red/x",
            "http://civitai.green/x",
            "https://civitai.red.evil.example/x",
            "https://civitai.green.evil.example/x",
            "https://evilcivitai.red/x",
            "https://evilcivitai.green/x",
            "https://civitai.redx/x",
            "https://civitai.greenx/x",
            "https://civitai.red@evil.example/x",
            "https://user:pw@civitai.red/x",
            "https://civitai.green:8443/x",
            "https://blobs-b2.civitai.red/x",  # starting hosts stay a fixed list
            "https://evil.example/civitai.red",
        ):
            self.assertFalse(is_civitai_url(url), url)


class TestLoraManagerPathPolicy(unittest.TestCase):
    """Only a direct child of a ComfyUI custom_nodes dir named *lora* qualifies."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _make_comfy_root(self.tmp)
        self.custom_nodes = self.root / "custom_nodes"
        patcher = patch.object(
            lora_utils, "custom_nodes_directories", return_value=[self.custom_nodes]
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _canonical(path):
        return os.path.normcase(os.path.realpath(str(path)))

    def test_direct_child_named_lora_is_accepted(self):
        lm = _make_lora_manager(self.custom_nodes)
        self.assertEqual(
            lora_utils.resolve_lora_manager_path(str(lm)), self._canonical(lm)
        )

    def test_name_match_is_case_insensitive(self):
        lm = _make_lora_manager(self.custom_nodes, "comfyui-LORA-manager")
        self.assertEqual(
            lora_utils.resolve_lora_manager_path(str(lm)), self._canonical(lm)
        )

    def test_outside_custom_nodes_is_rejected(self):
        lm = _make_lora_manager(self.tmp)
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(lm)))

    def test_child_without_lora_in_name_is_rejected(self):
        other = _make_lora_manager(self.custom_nodes, "ComfyUI-Other")
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(other)))

    def test_custom_nodes_itself_is_rejected(self):
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(self.custom_nodes)))

    def test_nested_directory_is_rejected(self):
        nested = _make_lora_manager(self.custom_nodes / "pack", "lora-sub")
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(nested)))

    def test_sibling_prefix_directory_is_rejected(self):
        sibling = self.root / "custom_nodes2"
        lm = _make_lora_manager(sibling)
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(lm)))

    def test_traversal_out_of_custom_nodes_is_rejected(self):
        _make_lora_manager(self.root, "lora-escape")
        candidate = os.path.join(str(self.custom_nodes), "..", "lora-escape")
        self.assertIsNone(lora_utils.resolve_lora_manager_path(candidate))

    def test_symlink_escaping_custom_nodes_is_rejected(self):
        target = _make_lora_manager(self.tmp)
        link = self.custom_nodes / "lora-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(link)))

    def test_relative_path_is_resolved_against_the_comfyui_root(self):
        lm = _make_lora_manager(self.custom_nodes)
        with patch.object(lora_utils, "find_comfyui_root", return_value=self.root):
            resolved = lora_utils.resolve_lora_manager_path(
                "custom_nodes/ComfyUI-Lora-Manager"
            )
        self.assertEqual(resolved, self._canonical(lm))

    def test_missing_file_or_bad_input_is_rejected(self):
        self.assertIsNone(
            lora_utils.resolve_lora_manager_path(str(self.custom_nodes / "lora-none"))
        )
        plain = self.custom_nodes / "lora.txt"
        plain.write_text("")
        self.assertIsNone(lora_utils.resolve_lora_manager_path(str(plain)))
        self.assertIsNone(lora_utils.resolve_lora_manager_path(""))
        self.assertIsNone(lora_utils.resolve_lora_manager_path(None))
        self.assertIsNone(lora_utils.resolve_lora_manager_path("bad\x00name"))


class TestCustomNodesDirectories(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _make_comfy_root(self.tmp)

    def test_uses_folder_paths_base_path(self):
        fake = _FakeFolderPaths(str(self.root))
        with patch.dict(sys.modules, {"folder_paths": fake}):
            dirs = lora_utils.custom_nodes_directories()
        self.assertEqual(dirs, [(self.root / "custom_nodes").resolve()])

    def test_includes_folder_paths_custom_nodes_entries(self):
        extra = self.tmp / "more_nodes"
        extra.mkdir()
        fake = types.SimpleNamespace(
            base_path=str(self.root),
            get_folder_paths=lambda name: (
                [str(extra)] if name == "custom_nodes" else []
            ),
        )
        with patch.dict(sys.modules, {"folder_paths": fake}):
            dirs = lora_utils.custom_nodes_directories()
        self.assertEqual(
            dirs, [(self.root / "custom_nodes").resolve(), extra.resolve()]
        )

    def test_falls_back_to_the_parent_of_this_package(self):
        with (
            patch.dict(sys.modules, {"folder_paths": None}),
            patch.object(lora_utils, "find_comfyui_root", return_value=None),
        ):
            dirs = lora_utils.custom_nodes_directories()
        package_dir = Path(lora_utils.__file__).resolve().parent.parent
        self.assertEqual(dirs, [package_dir.parent])


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


class TestDownloadIsAtomic(unittest.TestCase):
    """I2 (S10 review): a cached image is never seen half written."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.target = self.tmpdir / "out.jpg"

    def test_the_image_appears_only_once_complete(self):
        seen = []
        real_replace = os.replace

        def replace(source, destination):
            # At the moment the name appears, the temp copy is whole and closed.
            seen.append((Path(source).parent, self.target.exists()))
            with open(source, "rb") as handle:
                self.assertEqual(handle.read(2), b"\xff\xd8")
            return real_replace(source, destination)

        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        with (
            patch.object(lora_utils, "_open_url", opener),
            patch.object(lora_utils.os, "replace", side_effect=replace),
        ):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.target, ""
            )
        self.assertEqual(seen, [(self.tmpdir, False)])
        self.assertEqual(os.path.realpath(result), os.path.realpath(self.target))
        self.assertEqual(sorted(p.name for p in self.tmpdir.iterdir()), ["out.jpg"])

    def test_a_failed_write_leaves_neither_the_image_nor_a_temp_file(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        with (
            patch.object(lora_utils, "_open_url", opener),
            patch.object(lora_utils.os, "replace", side_effect=OSError("disk full")),
        ):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.target, ""
            )
        self.assertIsNone(result)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_an_unreadable_image_leaves_no_temp_file(self):
        opener = _FakeOpener(_FakeResponse(b"not an image"))
        with patch.object(lora_utils, "_open_url", opener):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.target, ""
            )
        self.assertIsNone(result)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_the_image_gets_the_permissions_a_plain_save_gives(self):
        # S10 re-review N7: the cached JPEG honours the umask like img.save did.
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        opener = _FakeOpener(_FakeResponse(_png_bytes()))
        with patch.object(lora_utils, "_open_url", opener):
            lora_utils._download_one(
                "https://civitai.com/api/download/1", self.target, ""
            )
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)


class TestStaleDownloadSweep(unittest.TestCase):
    """S10 re-review N7: a killed download's ``.pm-*.part`` is swept later."""

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.cache, True)
        self.lora = self.cache / "my_lora"
        self.lora.mkdir()

    def _file(self, path, age_seconds):
        path.write_bytes(b"x")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_old_temp_downloads_are_removed(self):
        old = self._file(self.lora / ".pm-abc123.part", 2 * 3600)
        old_top = self._file(self.cache / ".pm-top.part", 2 * 3600)
        fresh = self._file(self.lora / ".pm-new.part", 60)
        image = self._file(self.lora / "abc.jpg", 2 * 3600)
        other = self._file(self.lora / "keep.part", 2 * 3600)
        prefixed = self._file(self.lora / ".pm-keep.jpg", 2 * 3600)
        deep = self.lora / "nested"
        deep.mkdir()
        nested = self._file(deep / ".pm-deep.part", 2 * 3600)

        lora_utils.sweep_stale_downloads(self.cache)

        self.assertFalse(old.exists())
        self.assertFalse(old_top.exists())
        for kept in (fresh, image, other, prefixed, nested):
            self.assertTrue(kept.exists(), kept.name)

    def test_a_missing_cache_is_fine(self):
        lora_utils.sweep_stale_downloads(self.cache / "absent")

    def test_a_file_that_cannot_be_removed_is_left(self):
        old = self._file(self.lora / ".pm-abc.part", 2 * 3600)
        with patch.object(lora_utils.os, "remove", side_effect=PermissionError):
            lora_utils.sweep_stale_downloads(self.cache)
        self.assertTrue(old.exists())


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

    def test_redirect_to_civitai_is_followed_without_the_key(self):
        handler = _FakeHTTPSHandler("https://image.civitai.com/other.png")
        opener = lora_utils._build_opener(handler)

        with patch.object(lora_utils, "_opener", opener):
            result = lora_utils._download_one(
                "https://civitai.com/api/download/1", self.tmpdir / "x.jpg", "key"
            )

        # image.civitai.com redirects again (the fake handler always does),
        # so the hop limit ends this chain; the first request alone had the key.
        self.assertIsNone(result)
        self.assertEqual(len(handler.requests), lora_utils.MAX_CIVITAI_REDIRECTS + 1)
        self.assertEqual(handler.requests[0].get_header("Authorization"), "Bearer key")
        for req in handler.requests[1:]:
            self.assertIsNone(req.get_header("Authorization"))


class _ChainHTTPSHandler(urllib.request.HTTPSHandler):
    """Serves ``routes`` (url -> (status, Location or None)) through a real
    urllib opener, recording every request; an unknown URL answers 404."""

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.requests = []

    def https_open(self, req):
        self.requests.append(req)
        status, location = self.routes.get(req.full_url, (404, None))
        if location is not None:
            headers = email.message_from_string(f"Location: {location}\n")
            resp = urllib.response.addinfourl(
                BytesIO(b""), headers, req.full_url, status
            )
            resp.msg = "Redirect"
            return resp
        body = _png_bytes() if status == 200 else b""
        headers = email.message_from_string("Content-Type: image/png\n")
        resp = urllib.response.addinfourl(BytesIO(body), headers, req.full_url, status)
        resp.msg = "OK" if status == 200 else "Not Found"
        return resp

    def http_open(self, req):  # a plain-HTTP hop must never be requested
        self.requests.append(req)
        raise AssertionError(f"plain HTTP requested: {req.full_url}")


START = "https://image.civitai.com/xG1/abc/width=450/1.jpeg"
BLOB = "https://blobs-b2.civitai.com/file/civitai-media-cache/1/original"


class TestCivitaiRedirects(unittest.TestCase):
    """#337: image.civitai.com now redirects to blobs-b2.civitai.com.

    A redirect is followed only over HTTPS to civitai.com or a subdomain of
    it, at most MAX_CIVITAI_REDIRECTS hops, and never with the API key.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.target = self.tmpdir / "x.jpg"

    def _download(self, routes, url=START, api_key="secret-key"):
        handler = _ChainHTTPSHandler(routes)
        opener = lora_utils._build_opener(handler)
        with patch.object(lora_utils, "_opener", opener):
            result = lora_utils._download_one(url, self.target, api_key)
        return result, handler.requests

    def _refused(self, location, status=302):
        result, requests = self._download({START: (status, location)})
        self.assertIsNone(result)
        self.assertEqual([r.full_url for r in requests], [START])
        self.assertFalse(self.target.exists())

    def test_redirect_to_an_allowed_host_succeeds_without_the_key(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                if self.target.exists():
                    os.remove(self.target)
                result, requests = self._download(
                    {START: (status, BLOB), BLOB: (200, None)}
                )

                self.assertIsNotNone(result)
                self.assertTrue(self.target.exists())
                self.assertEqual([r.full_url for r in requests], [START, BLOB])
                self.assertEqual(
                    requests[0].get_header("Authorization"), "Bearer secret-key"
                )
                self.assertIsNone(requests[1].get_header("Authorization"))
                # urllib adds Host itself; nothing else rides along.
                self.assertEqual(
                    [h for h in requests[1].header_items() if h[0] != "Host"],
                    [("User-agent", "ComfyUI-PromptManager/1.0")],
                )

    def test_a_relative_location_resolves_on_the_same_host(self):
        follow = "https://image.civitai.com/elsewhere/1.jpeg"
        result, requests = self._download(
            {START: (302, "/elsewhere/1.jpeg"), follow: (200, None)}
        )

        self.assertIsNotNone(result)
        self.assertEqual([r.full_url for r in requests], [START, follow])
        self.assertIsNone(requests[1].get_header("Authorization"))

    def test_a_plain_http_redirect_is_refused(self):
        self._refused("http://blobs-b2.civitai.com/file/1")

    def test_a_redirect_to_another_host_is_refused(self):
        self._refused("https://evil.example/steal")

    def test_lookalike_hosts_are_refused(self):
        for location in (
            "https://civitai.com.evil.example/x",
            "https://evilcivitai.com/x",
            "https://civitai.com@evil.example/x",
            "https://user:pw@blobs-b2.civitai.com/x",
            "https://blobs-b2.civitai.com:8443/x",
            "https://civitai.co/x",
            "//evil.example/x",
            "ftp://blobs-b2.civitai.com/x",
            "https://[::1]/x",
        ):
            with self.subTest(location=location):
                self._refused(location)

    def test_a_redirect_without_a_location_is_refused(self):
        result, requests = self._download({START: (302, "")})
        self.assertIsNone(result)
        self.assertEqual(len(requests), 1)

    def test_the_hop_limit_is_enforced(self):
        limit = lora_utils.MAX_CIVITAI_REDIRECTS
        self.assertEqual(limit, 3)
        hops = [f"https://blobs-b2.civitai.com/hop/{i}" for i in range(limit + 1)]

        # Exactly ``limit`` redirects: followed.
        routes = {START: (302, hops[0])}
        for i in range(limit - 1):
            routes[hops[i]] = (302, hops[i + 1])
        routes[hops[limit - 1]] = (200, None)
        result, requests = self._download(routes)
        self.assertIsNotNone(result)
        self.assertEqual(len(requests), limit + 1)
        self.assertTrue(
            all(r.get_header("Authorization") is None for r in requests[1:])
        )

        # One more redirect: refused, and the next hop is never requested.
        os.remove(self.target)
        routes[hops[limit - 1]] = (302, hops[limit])
        routes[hops[limit]] = (200, None)
        result, requests = self._download(routes)
        self.assertIsNone(result)
        self.assertEqual(len(requests), limit + 1)
        self.assertNotIn(hops[limit], [r.full_url for r in requests])
        self.assertFalse(self.target.exists())

    def test_a_redirect_loop_ends_at_the_hop_limit(self):
        result, requests = self._download({START: (302, START)})
        self.assertIsNone(result)
        self.assertEqual(len(requests), lora_utils.MAX_CIVITAI_REDIRECTS + 1)

    def test_size_cap_applies_after_a_redirect(self):
        with patch.object(lora_utils, "MAX_CIVITAI_DOWNLOAD_BYTES", 10):
            result, requests = self._download({START: (302, BLOB), BLOB: (200, None)})
        self.assertIsNone(result)
        self.assertEqual(len(requests), 2)
        self.assertFalse(self.target.exists())

    def test_an_error_after_a_redirect_fails_cleanly(self):
        result, requests = self._download({START: (302, BLOB)})  # BLOB is 404
        self.assertIsNone(result)
        self.assertEqual(len(requests), 2)
        self.assertFalse(self.target.exists())

    def test_the_opener_itself_still_refuses_every_redirect(self):
        handler = _ChainHTTPSHandler({START: (302, BLOB), BLOB: (200, None)})
        opener = lora_utils._build_opener(handler)
        req = urllib.request.Request(START, headers={"Authorization": "Bearer k"})

        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(req, timeout=5)
        caught.exception.close()
        self.assertEqual(len(handler.requests), 1)

    def test_redirect_logs_never_carry_the_key_or_the_query(self):
        signed = BLOB + "?X-Amz-Signature=sig123"
        with self.assertLogs("prompt_manager.lora_utils", level="DEBUG") as logs:
            self._download(
                {START: (302, "https://evil.example/x?token=t0k3n")},
                api_key="secret-key",
            )
            self._download({START: (302, signed), signed: (200, None)})
        text = "\n".join(logs.output)
        self.assertNotIn("secret-key", text)
        self.assertNotIn("t0k3n", text)
        self.assertNotIn("sig123", text)


RED_BLOB = "https://blobs-b2.civitai.red/file/civitai-media-cache/1/original"
GREEN_BLOB = "https://media.civitai.green/file/1/original"


class TestCivitaiRedAndGreenDomains(unittest.TestCase):
    """#340: civitai.red and civitai.green are CivitAI's own domains.

    They are redirect targets under the civitai.com rules (HTTPS, default
    port, no credentials, parsed host name, 3 hops, never the key) and
    starting hosts whose first request may carry the key.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.target = self.tmpdir / "x.jpg"

    def _download(self, routes, url=START, api_key="secret-key"):
        handler = _ChainHTTPSHandler(routes)
        opener = lora_utils._build_opener(handler)
        with patch.object(lora_utils, "_opener", opener):
            result = lora_utils._download_one(url, self.target, api_key)
        return result, handler.requests

    def test_redirect_targets_on_red_and_green_are_allowed(self):
        for url in (
            "https://civitai.red/x",
            "https://civitai.green/x",
            RED_BLOB,
            GREEN_BLOB,
            "https://a.b.civitai.red:443/x",
            "https://CIVITAI.GREEN/x",
        ):
            self.assertTrue(lora_utils.is_civitai_redirect_target(url), url)

    def test_redirect_target_lookalikes_are_refused(self):
        for url in (
            "https://civitai.red.evil.example/x",
            "https://civitai.green.evil.example/x",
            "https://evilcivitai.red/x",
            "https://evilcivitai.green/x",
            "https://civitai.redx/x",
            "https://civitai.greenx/x",
            "https://civitai.red@evil.example/x",
            "https://user:pw@blobs-b2.civitai.red/x",
            "https://user@civitai.green/x",
            "https://blobs-b2.civitai.red:8443/x",
            "http://blobs-b2.civitai.red/x",
            "http://civitai.green/x",
            "ftp://civitai.red/x",
            "https://evil.example/civitai.red",
            "https://evil.example/?h=civitai.green",
            "https://red/x",
            "https://.civitai.red.evil/x",
        ):
            self.assertFalse(lora_utils.is_civitai_redirect_target(url), url)

    def test_a_redirect_to_red_or_green_is_followed_without_the_key(self):
        for blob in (RED_BLOB, GREEN_BLOB):
            with self.subTest(blob=blob):
                if self.target.exists():
                    os.remove(self.target)
                result, requests = self._download(
                    {START: (302, blob), blob: (200, None)}
                )

                self.assertIsNotNone(result)
                self.assertEqual([r.full_url for r in requests], [START, blob])
                self.assertEqual(
                    requests[0].get_header("Authorization"), "Bearer secret-key"
                )
                self.assertIsNone(requests[1].get_header("Authorization"))

    def test_lookalike_and_plain_http_redirects_are_refused(self):
        for location in (
            "https://civitai.red.evil.example/x",
            "https://evilcivitai.red/x",
            "https://civitai.redx/x",
            "https://civitai.green.evil.example/x",
            "https://evilcivitai.green/x",
            "https://civitai.greenx/x",
            "http://blobs-b2.civitai.red/x",
            "http://civitai.green/x",
        ):
            with self.subTest(location=location):
                result, requests = self._download({START: (302, location)})
                self.assertIsNone(result)
                self.assertEqual([r.full_url for r in requests], [START])
                self.assertFalse(self.target.exists())

    def test_a_red_starting_host_gets_the_key_and_its_redirect_does_not(self):
        start = "https://image.civitai.red/xG1/abc/width=450/1.jpeg"
        result, requests = self._download(
            {start: (302, BLOB), BLOB: (200, None)}, url=start
        )

        self.assertIsNotNone(result)
        self.assertEqual([r.full_url for r in requests], [start, BLOB])
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer secret-key")
        self.assertIsNone(requests[1].get_header("Authorization"))

    def test_a_green_starting_host_downloads_directly(self):
        start = "https://civitai.green/api/download/1"
        result, requests = self._download({start: (200, None)}, url=start)

        self.assertIsNotNone(result)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer secret-key")

    def test_the_hop_limit_spans_domains(self):
        hops = [RED_BLOB, GREEN_BLOB, BLOB, "https://civitai.red/4"]
        routes = {START: (302, hops[0])}
        for here, there in zip(hops, hops[1:]):
            routes[here] = (302, there)
        routes[hops[-1]] = (200, None)

        result, requests = self._download(routes)

        self.assertIsNone(result)
        self.assertEqual(len(requests), lora_utils.MAX_CIVITAI_REDIRECTS + 1)
        self.assertNotIn(hops[-1], [r.full_url for r in requests])
        for req in requests[1:]:
            self.assertIsNone(req.get_header("Authorization"))


class _RaisingOpener:
    """An ``_open_url`` stand-in that raises ``error`` for every request."""

    def __init__(self, error):
        self.error = error
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        raise self.error


class TestDownloadFailureWarnings(unittest.TestCase):
    """#340: each failed download logs one warning line with a short reason,
    the URL without its query or credentials, and never the key."""

    LOGGER = "prompt_manager.lora_utils"
    SIGNED = START + "?token=t0k3n"

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.target = self.tmpdir / "x.jpg"

    def _warning(self, run):
        """Run ``run`` and return its one WARNING-or-above record's text."""
        with self.assertLogs(self.LOGGER, level="WARNING") as logs:
            result = run()
        self.assertIsNone(result)
        self.assertFalse(self.target.exists())
        self.assertEqual(len(logs.records), 1, logs.output)
        record = logs.records[0]
        self.assertEqual(record.levelname, "WARNING")
        text = record.getMessage()
        self.assertNotIn("secret-key", text)
        self.assertNotIn("t0k3n", text)
        self.assertNotIn("sig123", text)
        self.assertIn(START.split("?")[0], text)
        return text

    def _chain(self, routes, url=SIGNED):
        handler = _ChainHTTPSHandler(routes)
        opener = lora_utils._build_opener(handler)

        def run():
            with patch.object(lora_utils, "_opener", opener):
                return lora_utils._download_one(url, self.target, "secret-key")

        return run

    def _fake(self, opener, url=SIGNED):
        def run():
            with patch.object(lora_utils, "_open_url", opener):
                return lora_utils._download_one(url, self.target, "secret-key")

        return run

    def test_http_status(self):
        text = self._warning(self._chain({}))  # unknown URL answers 404
        self.assertIn("HTTP 404", text)

    def test_http_status_after_a_redirect(self):
        text = self._warning(self._chain({self.SIGNED: (302, BLOB)}))
        self.assertIn("HTTP 404", text)

    def test_refused_redirect(self):
        text = self._warning(
            self._chain({self.SIGNED: (302, "https://evil.example/x?sig123=1")})
        )
        self.assertIn("redirect refused", text)
        self.assertIn("https://evil.example/x", text)

    def test_hop_limit(self):
        text = self._warning(self._chain({self.SIGNED: (302, self.SIGNED)}))
        self.assertIn("more than 3 redirects", text)

    def test_size_cap(self):
        opener = _FakeOpener(
            _FakeResponse(_png_bytes(), content_length=MAX_CIVITAI_DOWNLOAD_BYTES + 1)
        )
        text = self._warning(self._fake(opener))
        self.assertIn("larger than the 50 MB cap", text)

    def test_decode_error(self):
        text = self._warning(self._fake(_FakeOpener(_FakeResponse(b"not an image"))))
        self.assertIn("image could not be decoded", text)

    def test_timeout(self):
        for error in (
            TimeoutError("timed out"),
            urllib.error.URLError(TimeoutError("timed out")),
        ):
            with self.subTest(error=type(error).__name__):
                text = self._warning(self._fake(_RaisingOpener(error)))
                self.assertIn("timed out", text)

    def test_network_error(self):
        for error in (
            urllib.error.URLError("[Errno -2] Name or service not known"),
            ConnectionResetError(104, "Connection reset by peer"),
        ):
            with self.subTest(error=type(error).__name__):
                text = self._warning(self._fake(_RaisingOpener(error)))
                self.assertIn("network error", text)

    def test_a_timeout_while_reading_the_body(self):
        class SlowResponse(_FakeResponse):
            def read(self, amt=-1):
                raise TimeoutError("timed out")

        text = self._warning(self._fake(_FakeOpener(SlowResponse(b""))))
        self.assertIn("timed out", text)

    def test_a_failed_save(self):
        opener = _FakeOpener(_FakeResponse(_png_bytes()))

        def run():
            with patch.object(
                lora_utils.os, "replace", side_effect=OSError(28, "No space left")
            ):
                return self._fake(opener)()

        text = self._warning(run)
        self.assertIn("could not be saved", text)

    def test_a_refused_starting_url(self):
        def run():
            return lora_utils._download_one(
                "http://image.civitai.com/x?token=t0k3n", self.target, "secret-key"
            )

        with self.assertLogs(self.LOGGER, level="WARNING") as logs:
            self.assertIsNone(run())
        self.assertEqual(len(logs.records), 1)
        text = logs.records[0].getMessage()
        self.assertIn("not an allowed CivitAI URL", text)
        self.assertNotIn("t0k3n", text)

    def test_an_unexpected_error_names_only_its_type(self):
        error = ValueError("unknown url type: https://x?token=t0k3n")
        text = self._warning(self._fake(_RaisingOpener(error)))
        self.assertIn("unexpected ValueError", text)

    def test_the_line_names_the_download(self):
        text = self._warning(self._chain({}))
        self.assertTrue(text.startswith("CivitAI image download failed"), text)

    def test_a_successful_download_logs_no_warning(self):
        with self.assertNoLogs(self.LOGGER, level="WARNING"):
            result = self._fake(_FakeOpener(_FakeResponse(_png_bytes())))()
        self.assertIsNotNone(result)


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
            meta_path = Path(tmpdir) / "my_lora.metadata.json"
            meta_path.write_text(json.dumps(meta))
            (Path(tmpdir) / "my_lora.safetensors").write_bytes(b"m")

            # No LoraManager install: the default LoRA folders, alongside.
            with patch("py.lora_utils.default_lora_roots", return_value=[tmpdir]):
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
            (Path(tmpdir) / "MyLoRA.metadata.json").write_text(json.dumps(meta))
            (Path(tmpdir) / "MyLoRA.safetensors").write_bytes(b"m")

            with patch("py.lora_utils.default_lora_roots", return_value=[tmpdir]):
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
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=None),
            patch.object(lora_utils, "custom_nodes_directories", return_value=[]),
        ):
            self.assertIsNone(lora_utils.detect_lora_manager())

    def test_auto_detects_in_an_extra_custom_nodes_folder(self):
        # #144: ComfyUI's main custom_nodes has no LoraManager; an extra
        # custom_nodes path registered through folder_paths (extra model
        # paths, --base-directory) has it.
        root = _make_comfy_root(self.tmp)
        extra = self.tmp / "shared_nodes"
        extra.mkdir()
        lm = _make_lora_manager(extra, "ComfyUI-Lora-Manager")
        fake = types.SimpleNamespace(
            base_path=str(root),
            get_folder_paths=lambda name: (
                [str(root / "custom_nodes"), str(extra)]
                if name == "custom_nodes"
                else []
            ),
        )
        with (
            patch.dict(sys.modules, {"folder_paths": fake}),
            patch.object(lora_utils, "find_comfyui_root", return_value=root),
        ):
            self.assertEqual(lora_utils.detect_lora_manager(), str(lm.resolve()))

    def test_main_custom_nodes_wins_over_an_extra_folder(self):
        root = _make_comfy_root(self.tmp)
        main = _make_lora_manager(root / "custom_nodes")
        extra = self.tmp / "shared_nodes"
        extra.mkdir()
        _make_lora_manager(extra)
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=root),
            patch.object(
                lora_utils,
                "custom_nodes_directories",
                return_value=[(root / "custom_nodes").resolve(), extra.resolve()],
            ),
        ):
            self.assertEqual(lora_utils.detect_lora_manager(), str(main.resolve()))

    def test_an_entry_that_cannot_be_checked_is_skipped(self):
        # S10 review N5: a permission error on one entry, not a missing folder.
        root = _make_comfy_root(self.tmp)
        blocked = root / "custom_nodes" / "a-lora-manager"
        blocked.mkdir()
        lm = _make_lora_manager(root / "custom_nodes", "b-Lora-Manager")
        real_is_dir = Path.is_dir

        def is_dir(path):
            if path == blocked:
                raise PermissionError(13, "Permission denied", str(path))
            return real_is_dir(path)

        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=root),
            patch.object(lora_utils, "custom_nodes_directories", return_value=[]),
            patch.object(Path, "is_dir", is_dir),
        ):
            self.assertEqual(lora_utils.detect_lora_manager(), str(lm.resolve()))

    def test_a_folder_that_cannot_be_listed_is_skipped(self):
        root = _make_comfy_root(self.tmp)
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=root),
            patch.object(lora_utils, "custom_nodes_directories", return_value=[]),
            patch.object(Path, "iterdir", side_effect=PermissionError(13, "denied")),
        ):
            self.assertIsNone(lora_utils.detect_lora_manager())

    def test_an_unreadable_extra_folder_is_skipped(self):
        root = _make_comfy_root(self.tmp)
        missing = self.tmp / "gone"
        extra = self.tmp / "shared_nodes"
        extra.mkdir()
        lm = _make_lora_manager(extra)
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=root),
            patch.object(
                lora_utils,
                "custom_nodes_directories",
                return_value=[missing, extra.resolve()],
            ),
        ):
            self.assertEqual(lora_utils.detect_lora_manager(), str(lm.resolve()))


class TestDefaultLoraRoots(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _make_comfy_root(self.tmp)
        self.lm = _make_lora_manager(self.root / "custom_nodes")

    def test_collects_default_extra_and_runtime_dirs_only(self):
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
            dirs = lora_utils.default_lora_roots()

        # #355: folders inside the LoraManager install are no longer roots.
        expected = {
            str((self.root / "models" / "loras").resolve()),
            str((self.root / "rel_loras").resolve()),
            str(extra_abs.resolve()),
            str(runtime.resolve()),
        }
        self.assertEqual(set(dirs), expected)
        self.assertEqual(dirs, sorted(dirs))

    def test_non_dict_yaml_and_parse_errors_are_ignored(self):
        (self.root / "extra_model_paths.yml").write_text("- just\n- a list\n")
        self.assertEqual(lora_utils._get_extra_lora_paths(self.root), [])
        (self.root / "extra_model_paths.yml").write_text("a: [unclosed\n")
        self.assertEqual(lora_utils._get_extra_lora_paths(self.root), [])

    def test_without_root_nothing_is_found(self):
        with (
            patch.object(lora_utils, "find_comfyui_root", return_value=None),
            patch.dict(sys.modules, {"folder_paths": None}),
        ):
            self.assertEqual(lora_utils.default_lora_roots(), [])

    def test_the_removed_helpers_are_gone(self):
        for name in (
            "find_lora_directories",
            "get_preview_images_from_metadata",
            "get_preview_image_from_metadata",
            "get_example_images_dir",
        ):
            self.assertFalse(hasattr(lora_utils, name), name)


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
        for stem in ("bad", "nowords", "named"):
            (tmp / f"{stem}.safetensors").write_bytes(b"m")
        cache = TriggerWordCache()
        with patch.object(lora_utils, "default_lora_roots", return_value=[str(tmp)]):
            self.assertEqual(cache.load(str(tmp)), 1)
        self.assertEqual(cache.get_trigger_words("named"), ["w"])


class TestTriggerWordCacheLayout(unittest.TestCase):
    """#355: trigger words come from the sidecars LoraManager really uses."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.custom_nodes = self.tmp / "custom_nodes"
        self.lm = _make_lora_manager(self.custom_nodes)
        self.loras = self.tmp / "loras"
        self.loras.mkdir()
        self.sidecars = self.tmp / "sidecars"
        self.settings = self.tmp / "settings"
        self.settings.mkdir()
        patchers = [
            patch.object(
                lora_utils, "custom_nodes_directories", return_value=[self.custom_nodes]
            ),
            patch.object(lora_utils, "default_lora_roots", return_value=[]),
            patch.dict(os.environ, {"LORA_MANAGER_SETTINGS_DIR": str(self.settings)}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def centralized(self):
        self.sidecars.mkdir(exist_ok=True)
        (self.settings / "settings.json").write_text(
            json.dumps(
                {
                    "sidecar_storage_mode": "centralized",
                    "sidecar_storage_path": str(self.sidecars),
                    "folder_paths": {"loras": [str(self.loras)]},
                }
            )
        )

    def sidecar(self, folder, stem, model, words):
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "file_name": stem,
            "file_path": str(model),
            "civitai": {"trainedWords": words},
        }
        (folder / f"{stem}.metadata.json").write_text(json.dumps(meta))

    def test_centralized_sidecars_are_read(self):
        self.centralized()
        model = self.loras / "neon.safetensors"
        model.write_bytes(b"m")
        self.sidecar(self.sidecars / "loras-0a1b2c3d", "neon", model, ["glow"])
        # A leftover sidecar beside the model is not what LoraManager reads.
        self.sidecar(self.loras, "neon", model, ["stale"])
        cache = TriggerWordCache()
        self.assertEqual(cache.load(str(self.lm)), 1)
        self.assertEqual(cache.get_trigger_words("neon"), ["glow"])

    def test_both_the_model_and_the_sidecar_name_are_keys(self):
        self.centralized()
        model = self.loras / "model_name.safetensors"
        model.write_bytes(b"m")
        self.sidecar(self.sidecars / "c", "sidecar_name", model, ["w"])
        cache = TriggerWordCache()
        self.assertEqual(cache.load(str(self.lm)), 2)
        self.assertEqual(cache.get_trigger_words("model_name"), ["w"])
        self.assertEqual(cache.get_trigger_words("sidecar_name"), ["w"])

    def test_an_empty_path_means_auto_detection(self):
        self.centralized()
        model = self.loras / "neon.safetensors"
        model.write_bytes(b"m")
        self.sidecar(self.sidecars / "c", "neon", model, ["glow"])
        cache = TriggerWordCache()
        with patch.object(lora_utils, "detect_lora_manager", return_value=str(self.lm)):
            self.assertEqual(cache.load(""), 1)

    def test_an_unlistable_folder_keeps_the_previous_map(self):
        cache = TriggerWordCache()
        cache._cache = {"old": ["word"]}
        cache._loaded = True
        with (
            patch.object(
                lora_utils, "discover_sidecars", side_effect=OSError("share gone")
            ),
            self.assertLogs("prompt_manager.lora_utils", level="WARNING") as logs,
        ):
            self.assertEqual(cache.load(str(self.lm)), 1)
        self.assertEqual(cache.get_trigger_words("old"), ["word"])
        self.assertIn("share gone", "\n".join(logs.output))

    def test_a_failed_load_waits_before_a_node_retries(self):
        # Review finding: a node calls load while the cache is not loaded; a
        # failing discovery must not rescan on every prompt run.
        cache = TriggerWordCache()
        self.assertTrue(cache.retry_due())
        clock = [100.0]
        with (
            patch.object(lora_utils.time, "monotonic", lambda: clock[0]),
            patch.object(lora_utils, "discover_sidecars", side_effect=OSError("x")),
            self.assertLogs("prompt_manager.lora_utils", level="WARNING"),
        ):
            cache.load(str(self.lm))
            self.assertFalse(cache.is_loaded)
            self.assertFalse(cache.retry_due())
            clock[0] += lora_utils.TRIGGER_RETRY_SECONDS
            self.assertTrue(cache.retry_due())
        cache.load(str(self.lm))
        self.assertTrue(cache.is_loaded)
        self.assertTrue(cache.retry_due())

    def test_any_other_failure_never_raises(self):
        cache = TriggerWordCache()
        with (
            patch.object(lora_utils, "load_layout", side_effect=RuntimeError("bad")),
            self.assertLogs("prompt_manager.lora_utils", level="WARNING"),
        ):
            self.assertEqual(cache.load(str(self.lm)), 0)
        self.assertFalse(cache.is_loaded)

    def test_an_unusable_layout_falls_back_to_the_default_roots(self):
        (self.settings / "settings.json").write_text("{not json")
        model = self.loras / "neon.safetensors"
        model.write_bytes(b"m")
        self.sidecar(self.loras, "neon", model, ["glow"])
        cache = TriggerWordCache()
        with (
            patch.object(
                lora_utils, "default_lora_roots", return_value=[str(self.loras)]
            ),
            self.assertLogs("prompt_manager.lora_utils", level="WARNING") as logs,
        ):
            self.assertEqual(cache.load(str(self.lm)), 1)
        self.assertEqual(cache.get_trigger_words("neon"), ["glow"])
        self.assertIn("not valid JSON", "\n".join(logs.output))

    def test_layout_wraps_the_install_and_the_default_roots(self):
        with patch.object(
            lora_utils, "default_lora_roots", return_value=[str(self.loras)]
        ):
            layout = lora_utils.lora_manager_layout(str(self.lm))
        self.assertIsNone(layout.error)
        self.assertEqual(layout.source, "environment")
        self.assertIn(os.path.normcase(str(self.loras)), layout.lora_roots)
        self.assertEqual(
            lora_utils.lora_manager_layout(str(self.tmp)).error,
            "no LoraManager install found",
        )


if __name__ == "__main__":
    unittest.main()
