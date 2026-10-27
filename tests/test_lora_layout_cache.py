"""The LoraManager layout cache for the image hot paths (#362).

``py/lora_layout_cache.py``. Every test injects the install resolver, the
layout loader (both counting calls), the wall clock, the monotonic clock and,
where needed, ``stat`` and ``pathmod``, over a temporary folder. No test
sleeps: modification times are set with ``os.utime`` and the clocks are
plain attributes the test moves.
"""

import ntpath
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py import lora_layout_cache as cache_module
from py.lora_layout_cache import (
    MAX_AGE_SECONDS,
    MISSING,
    RACY_SECONDS,
    LayoutCache,
)
from py.lora_manager_layout import UNREADABLE, LoraManagerLayout, _canonical

SECOND_NS = 10**9
# NTFS keeps modification times in 100 ns steps and FAT in 2 s steps: every
# time a test sets is a whole millisecond, and a test whose file system rounds
# it anyway skips (``set_mtime``).
TICK_NS = 10**6


def make_layout(settings_path=None, media_roots=(), error=None, source="user config"):
    return LoraManagerLayout(
        settings_path=settings_path,
        source=source,
        error=error,
        mode="alongside",
        lora_roots=tuple(media_roots),
        sidecar_root=None,
        example_images_root=None,
        library="default",
        media_roots=tuple(media_roots),
    )


class CacheTestCase(unittest.TestCase):
    """An install, its plugin settings file, a settings file and a LoRA root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.install_dir = self.tmp / "custom_nodes" / "ComfyUI-Lora-Manager"
        self.install_dir.mkdir(parents=True)
        self.plugin_settings = self.install_dir / "settings.json"
        self.settings = self.tmp / "user-config" / "settings.json"
        self.settings.parent.mkdir()
        self.root_dir = self.tmp / "loras"
        self.root_dir.mkdir()
        # Files are written ten minutes before the injected wall clock, so
        # nothing is racy unless a test sets a recent modification time.
        self.now_ns = time.time_ns() // TICK_NS * TICK_NS
        self.mono = 1000.0
        self.resolved = []
        self.loaded = []
        self.install = _canonical(str(self.install_dir))
        self.layout = make_layout(
            settings_path=self.settings,
            media_roots=(_canonical(str(self.root_dir)),),
        )
        self.write(self.settings, "{}")

    def write(self, path, text, mtime_ns=None):
        Path(path).write_text(text, encoding="utf-8")
        stamp = mtime_ns if mtime_ns is not None else self.now_ns - 600 * SECOND_NS
        self.set_mtime(path, stamp)

    def set_mtime(self, path, stamp):
        os.utime(path, ns=(stamp, stamp))
        if os.stat(path).st_mtime_ns != stamp:
            self.skipTest("this file system rounds modification times")

    def resolve(self, key):
        self.resolved.append(key)
        return self.install

    def load(self, install):
        self.loaded.append(install)
        return self.layout

    def cache(self, **kwargs):
        kwargs.setdefault("wall_ns", lambda: self.now_ns)
        kwargs.setdefault("monotonic", lambda: self.mono)
        return LayoutCache(
            kwargs.pop("resolve", self.resolve), kwargs.pop("load", self.load), **kwargs
        )


class ValidationTests(CacheTestCase):
    def test_hit_reuses_the_entry_without_resolving_or_loading(self):
        cache = self.cache()
        first = cache.get("")
        second = cache.get("")
        self.assertEqual(len(self.resolved), 1)
        self.assertEqual(len(self.loaded), 1)
        self.assertIs(first[1], second[1])
        self.assertIs(first[0], second[0])
        self.assertEqual(first, (self.install, self.layout))

    def test_settings_size_change_reloads_once(self):
        cache = self.cache()
        cache.get("")
        stamp = self.settings.stat().st_mtime_ns
        self.write(self.settings, '{"a": 1}', mtime_ns=stamp)
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_settings_mtime_change_reloads_once(self):
        cache = self.cache()
        cache.get("")
        self.set_mtime(self.settings, self.settings.stat().st_mtime_ns + TICK_NS)
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_settings_deletion_reloads_once(self):
        cache = self.cache()
        cache.get("")
        self.settings.unlink()
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_settings_creation_reloads_once(self):
        self.settings.unlink()
        cache = self.cache()
        cache.get("")
        self.write(self.settings, "{}")
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_plugin_settings_change_reloads_once(self):
        cache = self.cache()
        cache.get("")
        self.write(self.plugin_settings, '{"use_portable_settings": true}')
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_a_different_configured_path_reloads(self):
        cache = self.cache()
        cache.get("")
        cache.get("other")
        self.assertEqual(self.resolved, ["", "other"])
        self.assertEqual(len(self.loaded), 2)

    def test_a_different_home_folder_reloads(self):
        # The folder guard compares against the home folder: a cached layout
        # must not outlive a change of it.
        home = {"value": str(self.tmp / "home-a")}
        cache = self.cache(home=lambda: home["value"])
        cache.get("")
        cache.get("")
        home["value"] = str(self.tmp / "home-b")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_configured_path_is_stripped_for_the_key_and_the_resolver(self):
        cache = self.cache()
        cache.get("  x ")
        cache.get("x")
        self.assertEqual(self.resolved, ["x"])
        self.assertEqual(len(self.loaded), 1)

    def test_a_recently_modified_file_is_not_stored(self):
        self.write(self.settings, "{}", mtime_ns=self.now_ns - SECOND_NS)
        cache = self.cache()
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)
        self.assertIsNone(cache._entry)
        self.now_ns += RACY_SECONDS * SECOND_NS
        cache.get("")
        self.assertIsNotNone(cache._entry)
        cache.get("")
        self.assertEqual(len(self.loaded), 3)

    def test_racy_boundary(self):
        # Exactly RACY_SECONDS old is trusted; anything newer is not.
        boundary = self.now_ns - RACY_SECONDS * SECOND_NS
        self.write(self.settings, "{}", mtime_ns=boundary + TICK_NS)
        cache = self.cache()
        cache.get("")
        self.assertIsNone(cache._entry)
        self.set_mtime(self.settings, boundary)
        cache.get("")
        self.assertIsNotNone(cache._entry)

    def test_a_modification_time_in_the_future_is_racy(self):
        self.write(self.plugin_settings, "{}", mtime_ns=self.now_ns + 60 * SECOND_NS)
        cache = self.cache()
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 2)
        self.assertIsNone(cache._entry)

    def test_a_missing_file_is_not_racy(self):
        self.assertFalse(self.plugin_settings.exists())
        self.settings.unlink()
        cache = self.cache()
        cache.get("")
        self.assertIsNotNone(cache._entry)
        signatures = dict(cache._entry.signatures)
        self.assertTrue(all(value is MISSING for value in signatures.values()))
        self.assertEqual(len(signatures), 2)

    def test_entry_is_reused_below_the_maximum_age_and_reloaded_at_it(self):
        cache = self.cache()
        cache.get("")
        stored_at = self.mono
        self.mono = stored_at + MAX_AGE_SECONDS - 0.5
        cache.get("")
        self.assertEqual(len(self.loaded), 1)
        self.mono = stored_at + MAX_AGE_SECONDS
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_a_wall_clock_moved_backward_does_not_extend_the_age(self):
        cache = self.cache()
        cache.get("")
        self.now_ns -= 3600 * SECOND_NS
        self.mono += MAX_AGE_SECONDS
        # The old entry is too old; the new load sees files "from the future"
        # (racy) and is not stored, but it does load.
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_stat_failure_on_a_hit_reloads(self):
        failing = {"on": False}

        def stat(path):
            if failing["on"]:
                raise PermissionError(13, "sharing violation", path)
            return os.stat(path)

        cache = self.cache(stat=stat)
        cache.get("")
        failing["on"] = True
        cache.get("")
        self.assertEqual(len(self.loaded), 2)
        self.assertIsNone(cache._entry)

    def test_stat_failure_on_a_miss_leaves_the_layout_unstored(self):
        def stat(path):
            raise PermissionError(13, "sharing violation", path)

        cache = self.cache(stat=stat)
        self.assertEqual(cache.get(""), (self.install, self.layout))
        self.assertIsNone(cache._entry)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_an_unreadable_settings_layout_is_not_stored(self):
        self.layout = make_layout(settings_path=self.settings, error=UNREADABLE)
        cache = self.cache()
        self.assertEqual(cache.get("")[1].error, UNREADABLE)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)
        self.assertIsNone(cache._entry)

    def test_other_layout_errors_are_stored(self):
        self.layout = make_layout(
            settings_path=self.settings, error="the settings file is not valid JSON"
        )
        cache = self.cache()
        cache.get("")
        cache.get("")
        self.assertEqual(len(self.loaded), 1)

    def _symlink(self, link, target):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks are not available here: {error}")

    def test_a_media_root_swapped_for_a_symlink_reloads(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        cache = self.cache()
        cache.get("")
        shutil.rmtree(self.root_dir)
        self._symlink(self.root_dir, elsewhere)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_an_install_swapped_for_a_symlink_reloads(self):
        elsewhere = self.tmp / "elsewhere-lm"
        elsewhere.mkdir()
        cache = self.cache()
        cache.get("")
        moved = self.tmp / "moved-lm"
        self.install_dir.rename(moved)
        self._symlink(self.install_dir, elsewhere)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_a_root_whose_realpath_fails_reloads(self):
        cache = self.cache()
        cache.get("")
        with patch.object(
            cache_module, "_canonical", side_effect=OSError("realpath failed")
        ):
            cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_a_stat_value_error_cannot_validate(self):
        def stat(path):
            raise ValueError("embedded null character in path")

        cache = self.cache(stat=stat)
        cache.get("")
        self.assertIsNone(cache._entry)

    def test_not_a_directory_is_missing(self):
        def stat(path):
            raise NotADirectoryError(20, "Not a directory", path)

        cache = self.cache(stat=stat)
        cache.get("")
        self.assertTrue(
            all(value is MISSING for _path, value in cache._entry.signatures)
        )
        cache.get("")
        self.assertEqual(len(self.loaded), 1)

    def test_an_offline_windows_share_cannot_validate(self):
        def stat(path):
            error = FileNotFoundError(2, "The network path was not found", path)
            error.winerror = 53
            raise error

        cache = self.cache(stat=stat)
        cache.get("")
        self.assertIsNone(cache._entry)

    def test_a_failed_reload_leaves_no_stale_entry(self):
        failing = {"on": False}

        def load(install):
            self.loaded.append(install)
            if failing["on"]:
                raise RuntimeError("boom")
            return self.layout

        cache = self.cache(load=load)
        cache.get("")
        self.mono += MAX_AGE_SECONDS
        failing["on"] = True
        with self.assertRaises(RuntimeError):
            cache.get("")
        self.assertIsNone(cache._entry)

    def test_a_resolve_failure_propagates_and_stores_nothing(self):
        def resolve(key):
            raise RuntimeError("boom")

        cache = self.cache(resolve=resolve)
        with self.assertRaises(RuntimeError):
            cache.get("")
        self.assertIsNone(cache._entry)

    def test_without_a_settings_path_only_the_plugin_file_is_watched(self):
        self.layout = make_layout(media_roots=self.layout.media_roots)
        cache = self.cache()
        cache.get("")
        self.assertEqual(
            [path for path, _sig in cache._entry.signatures],
            [os.path.join(self.install, "settings.json")],
        )

    def test_portable_settings_are_watched_once(self):
        self.layout = make_layout(settings_path=self.install_dir / "settings.json")
        cache = self.cache()
        cache.get("")
        self.assertEqual(len(cache._entry.signatures), 1)

    def test_a_load_failure_propagates_and_stores_nothing(self):
        calls = []

        def load(install):
            calls.append(install)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return self.layout

        cache = self.cache(load=load)
        with self.assertRaises(RuntimeError):
            cache.get("")
        self.assertIsNone(cache._entry)
        self.assertEqual(cache.get(""), (self.install, self.layout))
        self.assertEqual(len(calls), 2)

    def test_without_an_install_nothing_is_watched_and_the_entry_ages_out(self):
        stats = []

        def stat(path):
            stats.append(path)
            return os.stat(path)

        self.layout = make_layout(media_roots=self.layout.media_roots, source=None)
        cache = self.cache(resolve=lambda key: self.resolved.append(key), stat=stat)
        self.assertEqual(cache.get(""), (None, self.layout))
        self.assertEqual(cache._entry.signatures, ())
        cache.get("")
        self.assertEqual(len(self.loaded), 1)
        self.assertEqual(stats, [])
        self.mono += MAX_AGE_SECONDS
        cache.get("")
        self.assertEqual(len(self.loaded), 2)

    def test_windows_paths_differing_only_in_case_are_watched_once(self):
        stats = []

        def stat(path):
            stats.append(path)
            return SimpleNamespace(st_mtime_ns=self.now_ns - 600 * SECOND_NS, st_size=2)

        install = _canonical("C:\\ComfyUI\\custom_nodes\\LM", ntpath)
        # The hit check needs ntpath's realpath to be idempotent here.
        self.assertEqual(install, _canonical(install, ntpath))
        self.layout = make_layout(
            settings_path=PureWindowsPath(
                "C:\\ComfyUI\\custom_nodes\\lm\\SETTINGS.JSON"
            )
        )
        cache = self.cache(resolve=lambda key: install, stat=stat, pathmod=ntpath)
        cache.get("")
        self.assertEqual(len(cache._entry.signatures), 1)
        self.assertEqual(len(stats), 1)
        cache.get("")
        self.assertEqual(len(self.loaded), 1)
        self.assertEqual(len(stats), 2)


class ConcurrencyTests(CacheTestCase):
    def test_a_load_that_started_before_clear_stores_nothing(self):
        started = threading.Event()
        release = threading.Event()

        def load(install):
            self.loaded.append(install)
            if len(self.loaded) == 1:
                started.set()
                self.assertTrue(release.wait(10))
            return self.layout

        cache = self.cache(load=load)
        results = []
        worker = threading.Thread(target=lambda: results.append(cache.get("")))
        worker.start()
        self.assertTrue(started.wait(10))
        cache.clear()
        release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [(self.install, self.layout)])
        self.assertIsNone(cache._entry)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)
        self.assertIsNotNone(cache._entry)

    def test_concurrent_misses_all_get_a_layout_and_one_entry_is_stored(self):
        cache = self.cache()
        count = 6
        barrier = threading.Barrier(count)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait(10)
            result = cache.get("")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(results, [(self.install, self.layout)] * count)
        self.assertIsNotNone(cache._entry)
        loads = len(self.loaded)
        cache.get("")
        self.assertEqual(len(self.loaded), loads)

    def test_a_valid_hit_takes_no_lock(self):
        cache = self.cache()
        cache.get("")
        results = []
        with cache._lock:
            worker = threading.Thread(target=lambda: results.append(cache.get("")))
            worker.start()
            worker.join(10)
            alive = worker.is_alive()
        worker.join(10)
        self.assertFalse(alive)
        self.assertEqual(results, [(self.install, self.layout)])

    def test_clear_forces_a_reload(self):
        cache = self.cache()
        cache.get("")
        cache.clear()
        self.assertIsNone(cache._entry)
        cache.get("")
        self.assertEqual(len(self.loaded), 2)


class LoggingTests(CacheTestCase):
    LOGGER = "prompt_manager.lora_layout_cache"

    def get(self, cache):
        with self.assertLogs(self.LOGGER, "DEBUG") as logs:
            cache.get("")
            # assertLogs needs one record; this marker is not counted.
            cache_module.logger.debug("marker")
        return [r for r in logs.records if r.getMessage() != "marker"]

    def test_a_new_layout_logs_one_info_line(self):
        cache = self.cache()
        records = self.get(cache)
        self.assertEqual([r.levelname for r in records], ["INFO"])
        self.assertEqual(
            records[0].getMessage(),
            "LoraManager layout changed: user config settings, alongside storage, "
            "1 media folders",
        )
        self.mono += MAX_AGE_SECONDS
        self.assertEqual(self.get(cache), [])  # an equal layout, reloaded
        cache.clear()
        self.assertEqual(self.get(cache), [])
        self.layout = replace(self.layout, media_roots=(), lora_roots=())
        self.mono += MAX_AGE_SECONDS
        self.assertEqual(len(self.get(cache)), 1)

    def test_no_settings_source_prints_no(self):
        self.layout = replace(self.layout, source=None)
        records = self.get(self.cache())
        self.assertIn("layout changed: no settings,", records[0].getMessage())

    def test_an_unstored_load_logs_no_info_line(self):
        self.write(self.settings, "{}", mtime_ns=self.now_ns)
        self.assertEqual(
            [r for r in self.get(self.cache()) if r.levelname == "INFO"], []
        )

    def test_a_newly_refused_default_folder_logs_one_warning(self):
        self.layout = replace(self.layout, refused_defaults=("/home",))
        cache = self.cache()
        records = self.get(cache)
        warnings = [r for r in records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("/home", warnings[0].getMessage())
        self.mono += MAX_AGE_SECONDS
        self.assertEqual(self.get(cache), [])
        cache.clear()
        self.assertEqual(self.get(cache), [])
        self.layout = replace(self.layout, refused_defaults=("/home", "/"))
        self.mono += MAX_AGE_SECONDS
        warnings = [r for r in self.get(cache) if r.levelname == "WARNING"]
        self.assertEqual([w.getMessage().count("'/'") for w in warnings], [1])


class ModuleTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(cache_module.RACY_SECONDS, 3)
        self.assertEqual(cache_module.MAX_AGE_SECONDS, 30)


if __name__ == "__main__":
    unittest.main()
