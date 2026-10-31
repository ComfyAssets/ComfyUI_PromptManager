"""Where LoraManager keeps its files (#355): ``py/lora_manager_layout.py``.

Every test builds its own LoraManager install, settings file, LoRA roots,
sidecar root and example-images folder in a temporary folder, and injects the
environment and the user config folder: no test reads the developer's real
LoraManager settings or opens the network.
"""

import json
import ntpath
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py import lora_manager_layout as layout_module
from py.lora_manager_layout import (
    METADATA_SUFFIX,
    PREVIEW_EXTENSIONS,
    Sidecar,
    discover_sidecars,
    layout_summary,
    load_layout,
    local_media,
)

MEDIA = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".bmp"}
)
MARKER = "marker-from-unkept-settings-0123"
BAD_SIDECAR_REASON = (
    "the sidecar folder is a filesystem root, the home folder or a folder "
    "that contains it"
)


class LayoutTestCase(unittest.TestCase):
    """A disposable LoraManager install and the folders its settings name."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugin = self.tmp / "custom_nodes" / "ComfyUI-Lora-Manager"
        (self.plugin / "py").mkdir(parents=True)
        (self.plugin / "__init__.py").write_text("")
        self.user_config = self.tmp / "user-config" / "ComfyUI-LoRA-Manager"
        self.env_dir = self.tmp / "env-settings"
        self.default_loras = self.tmp / "models" / "loras"
        self.default_loras.mkdir(parents=True)
        self.environ = {}
        self.config_calls = []

    def user_config_dir(self):
        self.config_calls.append(True)
        return str(self.user_config)

    def write_settings(self, folder, data):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def load(self, path="__plugin__", **kwargs):
        lora_manager = str(self.plugin) if path == "__plugin__" else path
        kwargs.setdefault("default_roots", lambda: [str(self.default_loras)])
        kwargs.setdefault("environ", self.environ)
        kwargs.setdefault("user_config_dir", self.user_config_dir)
        return load_layout(lora_manager, **kwargs)

    def model(self, folder, stem):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}.safetensors"
        path.write_bytes(b"model")
        return path

    def sidecar(self, folder, stem, data=None):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}{METADATA_SUFFIX}"
        path.write_text(json.dumps(data if data is not None else {}), "utf-8")
        return path


class SettingsLookupTests(LayoutTestCase):

    def test_environment_folder_wins_over_portable_and_user_config(self):
        env_file = self.write_settings(self.env_dir, {"sidecar_storage_mode": "x"})
        self.write_settings(self.plugin, {"use_portable_settings": True})
        self.write_settings(self.user_config, {})
        self.environ["LORA_MANAGER_SETTINGS_DIR"] = str(self.env_dir)

        layout = self.load()

        self.assertEqual(layout.source, "environment")
        self.assertEqual(layout.settings_path, env_file)
        self.assertIsNone(layout.error)
        self.assertEqual(self.config_calls, [])

    def test_an_empty_environment_folder_is_ignored(self):
        self.environ["LORA_MANAGER_SETTINGS_DIR"] = ""
        self.assertEqual(self.load().source, "user config")

    def test_portable_flag_in_the_plugin_settings(self):
        plugin_file = self.write_settings(self.plugin, {"use_portable_settings": True})
        layout = self.load()
        self.assertEqual(layout.source, "portable")
        self.assertEqual(layout.settings_path, plugin_file)
        self.assertEqual(self.config_calls, [])

    def test_portable_environment_one_turns_it_on(self):
        self.environ["LORA_MANAGER_PORTABLE"] = "1"
        layout = self.load()
        self.assertEqual(layout.source, "portable")
        self.assertEqual(layout.settings_path, self.plugin / "settings.json")

    def test_portable_environment_zero_turns_it_off_despite_the_file(self):
        self.write_settings(self.plugin, {"use_portable_settings": True})
        self.environ["LORA_MANAGER_PORTABLE"] = "0"
        self.assertEqual(self.load().source, "user config")

    def test_other_portable_environment_values_are_ignored(self):
        for value in ("true", "yes"):
            with self.subTest(value=value):
                self.environ["LORA_MANAGER_PORTABLE"] = value
                self.assertEqual(self.load().source, "user config")
                self.write_settings(self.plugin, {"use_portable_settings": True})
                self.assertEqual(self.load().source, "portable")
                (self.plugin / "settings.json").unlink()

    def test_a_string_portable_flag_is_ignored(self):
        self.write_settings(self.plugin, {"use_portable_settings": "true"})
        self.assertEqual(self.load().source, "user config")

    def test_an_unreadable_plugin_file_means_not_portable(self):
        (self.plugin / "settings.json").write_text("{not json")
        self.assertEqual(self.load().source, "user config")

    def test_user_config_folder_is_the_injected_one(self):
        user_file = self.write_settings(self.user_config, {})
        layout = self.load()
        self.assertEqual(layout.source, "user config")
        self.assertEqual(layout.settings_path, user_file)
        self.assertEqual(self.config_calls, [True])

    def test_the_real_user_config_folder_is_never_opened(self):
        opened = []
        real_open = open

        def spy(path, *args, **kwargs):
            opened.append(os.path.realpath(str(path)))
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", spy):
            layout = self.load()
        self.assertEqual(layout.settings_path, self.user_config / "settings.json")
        for path in opened:
            self.assertTrue(path.startswith(str(self.tmp)), path)

    def test_a_missing_settings_file_means_defaults(self):
        layout = self.load()
        self.assertIsNone(layout.error)
        self.assertEqual(layout.mode, "alongside")
        self.assertIsNone(layout.example_images_root)
        self.assertIsNone(layout.sidecar_root)
        self.assertEqual(layout.library, "default")
        self.assertEqual(
            layout.lora_roots, (os.path.normcase(str(self.default_loras)),)
        )

    def test_settings_dir_is_expanded_and_made_absolute(self):
        self.write_settings(self.env_dir, {})
        # Relative to the process folder (relpath fails across Windows drives).
        # The cleanup returns before the temp folder is removed (LIFO).
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.tmp)
        self.environ["LORA_MANAGER_SETTINGS_DIR"] = self.env_dir.name
        self.assertEqual(self.load().settings_path, self.env_dir / "settings.json")


class UnusableLayoutTests(LayoutTestCase):

    def test_no_install(self):
        layout = self.load(path=None)
        self.assertEqual(layout.error, "no LoraManager install found")
        self.assertIsNone(layout.settings_path)
        self.assertIsNone(layout.source)
        # The default LoRA roots stay known for serving and trigger words.
        self.assertEqual(
            layout.lora_roots, (os.path.normcase(str(self.default_loras)),)
        )
        self.assertEqual(layout.media_roots, layout.lora_roots)
        self.assertEqual(discover_sidecars(layout).sidecars, [])

    def test_unreadable_settings(self):
        self.write_settings(self.user_config, {})
        with patch.object(layout_module, "_read_bytes", side_effect=PermissionError):
            layout = self.load()
        self.assertEqual(layout.error, "the settings file could not be read")

    def test_settings_that_are_not_json(self):
        self.user_config.mkdir(parents=True)
        (self.user_config / "settings.json").write_text("{nope")
        self.assertEqual(self.load().error, "the settings file is not valid JSON")

    def test_settings_that_are_not_utf8(self):
        self.user_config.mkdir(parents=True)
        (self.user_config / "settings.json").write_bytes(b'{"a": "\xff"}')
        self.assertEqual(self.load().error, "the settings file is not valid JSON")

    def test_settings_that_are_not_an_object(self):
        self.write_settings(self.user_config, ["a"])
        self.assertEqual(self.load().error, "the settings file is not a JSON object")

    def test_oversized_settings(self):
        self.user_config.mkdir(parents=True)
        with open(self.user_config / "settings.json", "wb") as handle:
            handle.write(b" " * (layout_module.MAX_SETTINGS_BYTES + 1))
        self.assertEqual(self.load().error, "the settings file is larger than 5 MB")

    def test_platformdirs_import_failure(self):
        def missing():
            raise ImportError("no platformdirs")

        layout = self.load(user_config_dir=missing)
        self.assertEqual(layout.error, "platformdirs is not available")
        self.assertEqual(layout.mode, "alongside")

    def test_default_user_config_dir_needs_platformdirs(self):
        with patch.dict(sys.modules, {"platformdirs": None}):
            with self.assertRaises(ImportError):
                layout_module._platform_user_config_dir()

    def test_default_user_config_dir_uses_the_app_name(self):
        fake = type(sys)("platformdirs")
        calls = []
        fake.user_config_dir = lambda *a, **k: calls.append((a, k)) or "/x"
        with patch.dict(sys.modules, {"platformdirs": fake}):
            self.assertEqual(layout_module._platform_user_config_dir(), "/x")
        self.assertEqual(calls, [(("ComfyUI-LoRA-Manager",), {"appauthor": False})])

    def test_missing_centralized_sidecar_root(self):
        self.write_settings(
            self.user_config,
            {
                "sidecar_storage_mode": "centralized",
                "sidecar_storage_path": str(self.tmp / "gone"),
            },
        )
        layout = self.load()
        self.assertEqual(layout.error, "the sidecar folder does not exist")
        self.assertEqual(layout.mode, "centralized")
        self.assertEqual(
            layout.lora_roots, (os.path.normcase(str(self.default_loras)),)
        )
        self.assertEqual(layout.media_roots, layout.lora_roots)
        self.assertEqual(discover_sidecars(layout).scanned, 0)

    def test_a_centralized_sidecar_root_that_is_a_filesystem_root(self):
        self.write_settings(
            self.user_config,
            {"sidecar_storage_mode": "centralized", "sidecar_storage_path": "/"},
        )
        layout = self.load()
        self.assertEqual(layout.error, BAD_SIDECAR_REASON)
        self.assertNotIn(os.path.normcase(os.path.realpath("/")), layout.media_roots)

    def test_a_centralized_sidecar_root_that_contains_home(self):
        home = self.tmp / "outer" / "me"
        home.mkdir(parents=True)
        self.write_settings(
            self.user_config,
            {
                "sidecar_storage_mode": "centralized",
                "sidecar_storage_path": str(self.tmp / "outer"),
            },
        )
        with patch.object(layout_module.Path, "home", return_value=home):
            layout = self.load()
        self.assertEqual(layout.error, BAD_SIDECAR_REASON)
        self.assertEqual(
            layout.media_roots, (os.path.normcase(str(self.default_loras)),)
        )


class SecretTests(LayoutTestCase):

    def test_other_settings_keys_never_leave_the_module(self):
        # Raw text keeps credential-named keys out of test variables.
        self.user_config.mkdir(parents=True)
        (self.user_config / "settings.json").write_text(
            '{"civitai_api_key": "%s", "nested": {"token": "%s"}}' % (MARKER, MARKER),
            encoding="utf-8",
        )
        layout = self.load()
        self.assertNotIn(MARKER, repr(layout))
        self.assertNotIn(MARKER, json.dumps(layout_summary(layout, Path.home())))

    def test_only_the_seven_keys_are_kept(self):
        self.assertEqual(
            layout_module.SETTINGS_KEYS,
            frozenset(
                {
                    "sidecar_storage_mode",
                    "sidecar_storage_path",
                    "example_images_path",
                    "folder_paths",
                    "extra_folder_paths",
                    "libraries",
                    "active_library",
                }
            ),
        )
        kept = layout_module._settings_subset(
            json.loads('{"civitai_api_key": "%s", "libraries": {}}' % MARKER)
        )
        self.assertEqual(kept, {"libraries": {}})


class ActiveLibraryTests(LayoutTestCase):

    def setUp(self):
        super().setUp()
        self.top = self.tmp / "top-loras"
        self.lib_a = self.tmp / "a-loras"
        self.lib_b = self.tmp / "b-loras"
        self.extra = self.tmp / "b-extra"
        for folder in (self.top, self.lib_a, self.lib_b, self.extra):
            folder.mkdir()
        self.examples = self.tmp / "examples"
        self.examples.mkdir()

    def settings(self, **extra):
        data = {
            "folder_paths": {"loras": [str(self.top)]},
            "libraries": {
                "first lib": {"folder_paths": {"loras": [str(self.lib_a)]}},
                "second": {
                    "folder_paths": {"loras": [str(self.lib_b)]},
                    "extra_folder_paths": {"loras": [str(self.extra)]},
                },
            },
            "example_images_path": str(self.examples),
        }
        data.update(extra)
        self.write_settings(self.user_config, data)

    def test_the_active_library_lists_are_used(self):
        self.settings(active_library="second")
        (self.examples / "second").mkdir()
        layout = self.load()
        self.assertEqual(layout.library, "second")
        self.assertIn(os.path.normcase(str(self.lib_b)), layout.lora_roots)
        self.assertIn(os.path.normcase(str(self.extra)), layout.lora_roots)
        self.assertNotIn(os.path.normcase(str(self.top)), layout.lora_roots)
        self.assertNotIn(os.path.normcase(str(self.lib_a)), layout.lora_roots)
        self.assertIn(os.path.normcase(str(self.default_loras)), layout.lora_roots)
        self.assertEqual(
            layout.example_images_root, os.path.normcase(str(self.examples / "second"))
        )

    def test_an_unknown_active_library_falls_back_to_the_first(self):
        self.settings(active_library="missing")
        (self.examples / "first_lib").mkdir()
        layout = self.load()
        self.assertEqual(layout.library, "first lib")
        self.assertIn(os.path.normcase(str(self.lib_a)), layout.lora_roots)
        # The sanitized library name is the example-images subfolder.
        self.assertEqual(
            layout.example_images_root,
            os.path.normcase(str(self.examples / "first_lib")),
        )

    def test_no_libraries_means_default_and_the_top_level_lists(self):
        self.write_settings(
            self.user_config,
            {
                "folder_paths": {"loras": str(self.top)},
                "extra_folder_paths": {"loras": [str(self.extra), 7]},
                "example_images_path": str(self.examples),
            },
        )
        layout = self.load()
        self.assertEqual(layout.library, "default")
        self.assertIn(os.path.normcase(str(self.top)), layout.lora_roots)
        self.assertIn(os.path.normcase(str(self.extra)), layout.lora_roots)
        self.assertEqual(
            layout.example_images_root, os.path.normcase(str(self.examples))
        )

    def test_one_library_has_no_library_subfolder(self):
        self.write_settings(
            self.user_config,
            {
                "libraries": {
                    "comfyui": {"folder_paths": {"loras": [str(self.lib_a)]}}
                },
                "active_library": "comfyui",
                "example_images_path": str(self.examples),
            },
        )
        layout = self.load()
        self.assertEqual(layout.library, "comfyui")
        self.assertEqual(
            layout.example_images_root, os.path.normcase(str(self.examples))
        )

    def test_a_mixed_case_library_folder_keeps_its_example_images(self):
        # Review finding: the library subfolder must be canonical like its
        # root, or every example image fails the media check on Windows
        # (normcase lowercases there; native proof is the Windows CI job).
        self.write_settings(
            self.user_config,
            {
                "libraries": {"Studio A": {}, "other": {}},
                "active_library": "Studio A",
                "example_images_path": str(self.examples),
            },
        )
        image = self.examples / "Studio_A" / "abc" / "1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"x")
        model = self.default_loras / "m.safetensors"
        model.write_bytes(b"m")
        (self.default_loras / "m.metadata.json").write_text("{}")
        layout = self.load()
        self.assertEqual(
            layout.example_images_root,
            os.path.normcase(os.path.realpath(self.examples / "Studio_A")),
        )
        entry = discover_sidecars(layout).sidecars[0]
        self.assertEqual(
            local_media(entry, {"sha256": "ABC"}, layout, MEDIA),
            [os.path.realpath(image)],
        )

    def test_a_missing_library_subfolder_means_no_example_images(self):
        self.settings(active_library="second")
        self.assertIsNone(self.load().example_images_root)

    def test_an_unset_or_missing_example_folder_means_none(self):
        self.write_settings(self.user_config, {"example_images_path": "  "})
        self.assertIsNone(self.load().example_images_root)
        self.write_settings(
            self.user_config, {"example_images_path": str(self.tmp / "gone")}
        )
        self.assertIsNone(self.load().example_images_root)

    def test_sanitize_library_name(self):
        sanitize = layout_module._sanitize_library_name
        self.assertEqual(sanitize("My Lib/2"), "My_Lib_2")
        self.assertEqual(sanitize("ok-name_1.0"), "ok-name_1.0")
        self.assertEqual(sanitize(""), "default")

    def test_odd_library_shapes_are_ignored(self):
        self.write_settings(
            self.user_config,
            {"libraries": {"x": "not a dict"}, "folder_paths": ["not", "a dict"]},
        )
        layout = self.load()
        self.assertEqual(layout.library, "x")
        self.assertEqual(
            layout.lora_roots, (os.path.normcase(str(self.default_loras)),)
        )


class CentralizedDiscoveryTests(LayoutTestCase):

    def setUp(self):
        super().setUp()
        self.loras = self.tmp / "loras"
        self.checkpoints = self.tmp / "checkpoints"
        self.checkpoints.mkdir()
        self.sidecars = self.tmp / "sidecars"
        self.sidecars.mkdir()
        self.write_settings(
            self.user_config,
            {
                "sidecar_storage_mode": "centralized",
                "sidecar_storage_path": str(self.sidecars),
                "folder_paths": {"loras": [str(self.loras)]},
            },
        )
        self.loras.mkdir()

    def test_component_folders_and_the_legacy_nested_form(self):
        a = self.model(self.loras, "alpha")
        b = self.model(self.loras / "sub", "beta")
        self.sidecar(self.sidecars / "loras-1a2b3c4d", "alpha", {"file_path": str(a)})
        self.sidecar(
            self.sidecars / "comfyui" / "loras-1a2b3c4d" / "sub",
            "beta",
            {"file_path": b.as_posix()},
        )
        discovery = discover_sidecars(self.load())
        self.assertEqual(discovery.scanned, 2)
        self.assertEqual(
            sorted(s.model_path.name for s in discovery.sidecars),
            ["alpha.safetensors", "beta.safetensors"],
        )
        self.assertEqual(discovery.unreadable, [])
        self.assertEqual(
            [str(s.metadata_path) for s in discovery.sidecars],
            sorted(str(s.metadata_path) for s in discovery.sidecars),
        )

    def test_missing_models_and_checkpoints_are_skipped(self):
        ckpt = self.model(self.checkpoints, "base")
        self.sidecar(self.sidecars / "c", "base", {"file_path": str(ckpt)})
        self.sidecar(
            self.sidecars / "l",
            "gone",
            {"file_path": str(self.loras / "gone.safetensors")},
        )
        self.sidecar(self.sidecars / "l", "nopath", {"model_name": "x"})
        discovery = discover_sidecars(self.load())
        self.assertEqual(discovery.scanned, 3)
        self.assertEqual(discovery.sidecars, [])

    def test_every_scanned_file_is_listed_resolved_and_sorted(self):
        # #362: the fingerprint covers every scanned sidecar, kept or skipped.
        ckpt = self.model(self.checkpoints, "base")
        alpha = self.model(self.loras, "alpha")
        self.sidecar(self.sidecars / "z", "base", {"file_path": str(ckpt)})
        self.sidecar(self.sidecars / "a", "alpha", {"file_path": str(alpha)})
        self.sidecar(self.sidecars / "m", "nopath", {"model_name": "x"})
        discovery = discover_sidecars(self.load())
        files = discovery.scanned_files
        self.assertIsInstance(files, tuple)
        self.assertEqual(len(files), 3)
        self.assertEqual(discovery.scanned, len(files))
        self.assertEqual([str(f) for f in files], sorted(str(f) for f in files))
        self.assertEqual(
            [f.name for f in files],
            [
                f"alpha{METADATA_SUFFIX}",
                f"nopath{METADATA_SUFFIX}",
                f"base{METADATA_SUFFIX}",
            ],
        )
        for path in files:
            self.assertEqual(str(path), os.path.realpath(path))
        self.assertEqual(len(discovery.sidecars), 1)

    def test_leftover_sidecars_beside_models_are_not_scanned(self):
        model = self.model(self.loras, "alpha")
        self.sidecar(self.loras, "alpha", {"file_path": str(model)})
        discovery = discover_sidecars(self.load())
        self.assertEqual((discovery.scanned, discovery.sidecars), (0, []))

    def test_unparseable_sidecars_are_recorded(self):
        (self.sidecars / f"broken{METADATA_SUFFIX}").write_text("{{{")
        (self.sidecars / f"list{METADATA_SUFFIX}").write_text("[1]")
        discovery = discover_sidecars(self.load())
        self.assertEqual(discovery.scanned, 2)
        self.assertEqual(
            sorted(discovery.unreadable),
            [f"broken{METADATA_SUFFIX}", f"list{METADATA_SUFFIX}"],
        )

    def test_default_sidecar_root_is_beside_the_settings(self):
        self.write_settings(
            self.user_config,
            {
                "sidecar_storage_mode": "centralized",
                "folder_paths": {"loras": [str(self.loras)]},
            },
        )
        (self.user_config / "sidecars").mkdir()
        layout = self.load()
        self.assertEqual(
            layout.sidecar_root, os.path.normcase(str(self.user_config / "sidecars"))
        )
        self.assertIn(layout.sidecar_root, layout.media_roots)

    def test_an_unknown_mode_is_alongside(self):
        self.write_settings(self.user_config, {"sidecar_storage_mode": "elsewhere"})
        self.assertEqual(self.load().mode, "alongside")

    def test_an_unlistable_folder_raises(self):
        layout = self.load()

        def failing_walk(top, onerror=None, followlinks=False):
            onerror(PermissionError(13, "denied", top))
            return iter(())

        with patch.object(layout_module.os, "walk", failing_walk):
            with self.assertRaises(OSError):
                discover_sidecars(layout)


class AlongsideDiscoveryTests(LayoutTestCase):

    def test_a_sidecar_with_its_model_is_kept(self):
        model = self.model(self.default_loras, "alpha")
        meta = self.sidecar(self.default_loras, "alpha")
        self.sidecar(self.default_loras, "orphan")
        discovery = discover_sidecars(self.load())
        self.assertEqual(discovery.scanned, 2)
        self.assertEqual(
            discovery.sidecars,
            [Sidecar(Path(os.path.realpath(meta)), Path(os.path.realpath(model)))],
        )

    def test_a_folder_reached_through_two_nested_roots_is_listed_once(self):
        nested = self.default_loras / "nested"
        self.model(nested, "alpha")
        self.sidecar(nested, "alpha")
        layout = self.load(default_roots=lambda: [str(self.default_loras), str(nested)])
        discovery = discover_sidecars(layout)
        self.assertEqual((discovery.scanned, len(discovery.sidecars)), (1, 1))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks")
    def test_a_symlink_loop_is_walked_once(self):
        self.model(self.default_loras, "alpha")
        self.sidecar(self.default_loras, "alpha")
        try:
            os.symlink(self.default_loras, self.default_loras / "loop")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges here")
        discovery = discover_sidecars(self.load())
        self.assertEqual((discovery.scanned, len(discovery.sidecars)), (1, 1))


class LocalMediaTests(LayoutTestCase):

    def setUp(self):
        super().setUp()
        self.examples = self.tmp / "examples"
        self.examples.mkdir()
        self.write_settings(
            self.user_config, {"example_images_path": str(self.examples)}
        )
        self.model(self.default_loras, "alpha")
        self.meta = self.sidecar(self.default_loras, "alpha")
        self.layout = self.load()
        self.entry = discover_sidecars(self.layout).sidecars[0]

    def media(self, metadata=None):
        return local_media(self.entry, metadata or {}, self.layout, MEDIA)

    def touch(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return os.path.realpath(path)

    def test_preview_url_comes_first(self):
        preview = self.touch(self.default_loras / "custom.jpeg")
        self.touch(self.default_loras / "alpha.webp")
        self.assertEqual(self.media({"preview_url": preview}), [preview])

    def test_a_relative_preview_url_is_logged_and_skipped(self):
        fallback = self.touch(self.default_loras / "alpha.webp")
        self.touch(self.default_loras / "relative.png")
        with self.assertLogs("prompt_manager.lora_manager_layout", "DEBUG") as logs:
            media = self.media({"preview_url": "relative.png"})
        self.assertEqual(media, [fallback])
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "DEBUG")
        self.assertIn(self.meta.name, logs.output[0])

    def test_preview_url_outside_media_roots_or_not_servable_is_ignored(self):
        outside = self.touch(self.tmp / "elsewhere" / "p.png")
        avif = self.touch(self.default_loras / "p.avif")
        fallback = self.touch(self.default_loras / "alpha.png")
        for url in (
            outside,
            avif,
            "relative.png",
            7,
            str(self.default_loras / "x.png"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.media({"preview_url": url}), [fallback])

    def test_suffix_order_follows_lora_manager(self):
        self.assertEqual(
            PREVIEW_EXTENSIONS,
            (
                ".webp",
                ".preview.webp",
                ".preview.png",
                ".preview.jpeg",
                ".preview.jpg",
                ".preview.mp4",
                ".png",
                ".jpeg",
                ".jpg",
                ".mp4",
                ".gif",
                ".webm",
                ".avif",
                ".jxl",
            ),
        )
        mp4 = self.touch(self.default_loras / "alpha.mp4")
        self.assertEqual(self.media(), [mp4])
        webp = self.touch(self.default_loras / "alpha.preview.webp")
        self.assertEqual(self.media(), [webp])

    def test_an_avif_only_preview_gives_nothing(self):
        self.touch(self.default_loras / "alpha.avif")
        self.assertEqual(self.media(), [])

    def test_example_images_in_numeric_order(self):
        folder = self.examples / "abcdef"
        names = ["image_10.png", "image_2.png", "image_1.jpg", "notes.txt"]
        for name in names:
            self.touch(folder / name)
        (folder / "sub").mkdir()
        preview = self.touch(self.default_loras / "alpha.png")
        found = self.media({"sha256": "ABCDEF"})
        self.assertEqual(
            [os.path.basename(p) for p in found],
            ["alpha.png", "image_1.jpg", "image_2.png", "image_10.png"],
        )
        self.assertEqual(found[0], preview)

    def test_missing_sha256_or_folder_gives_no_example_images(self):
        self.touch(self.examples / "abc" / "1.png")
        self.assertEqual(self.media(), [])
        self.assertEqual(self.media({"sha256": "zzz"}), [])
        self.assertEqual(self.media({"sha256": 5}), [])

    def test_an_unlistable_example_folder_gives_what_was_found(self):
        self.touch(self.examples / "abc" / "1.png")
        with patch.object(layout_module.os, "scandir", side_effect=PermissionError):
            self.assertEqual(self.media({"sha256": "abc"}), [])

    def test_cover_moves_the_first_still_in_front_of_a_video(self):
        video = self.touch(self.default_loras / "alpha.mp4")
        still = self.touch(self.examples / "abc" / "2.png")
        other = self.touch(self.examples / "abc" / "1.webm")
        self.assertEqual(self.media({"sha256": "abc"}), [still, video, other])

    def test_videos_only_keep_their_order(self):
        video = self.touch(self.default_loras / "alpha.mp4")
        self.assertEqual(self.media(), [video])

    def test_repeats_are_dropped(self):
        preview = self.touch(self.default_loras / "alpha.png")
        self.assertEqual(self.media({"preview_url": preview}), [preview])

    def test_video_suffixes_match_the_video_module(self):
        from utils.video_metadata import VIDEO_EXTENSIONS

        self.assertEqual(layout_module.VIDEO_SUFFIXES, frozenset(VIDEO_EXTENSIONS))


# Windows path rules without the filesystem. On Windows ntpath.realpath asks
# the network for \\server\share and waits seconds for a host that does not
# exist (#487); elsewhere ntpath.realpath already is ntpath.abspath.
LEXICAL_NT = SimpleNamespace(
    normcase=ntpath.normcase,
    realpath=ntpath.abspath,
    dirname=ntpath.dirname,
    sep=ntpath.sep,
)


class FolderGuardTests(unittest.TestCase):
    """Portable path-flavor controls (TESTING.md Windows portability preflight)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def guard_nt(self, path, home="C:\\Users\\Vito"):
        return layout_module._guard_folder(
            path, home, pathmod=LEXICAL_NT, isdir=lambda _p: True
        )

    def test_filesystem_roots_and_home_are_rejected(self):
        self.assertIsNone(layout_module._guard_folder("/", str(self.tmp)))
        self.assertIsNone(self.guard_nt("C:\\"))
        self.assertIsNone(self.guard_nt("c:/"))
        self.assertIsNone(self.guard_nt("\\\\server\\share\\"))
        self.assertIsNone(self.guard_nt("c:\\USERS\\vito"))
        self.assertIsNone(self.guard_nt("C:\\Users\\Vito\\"))
        self.assertEqual(
            self.guard_nt("C:\\Users\\Vito\\Loras"), "c:\\users\\vito\\loras"
        )
        self.assertIsNone(layout_module._guard_folder(str(self.tmp), str(self.tmp)))

    def test_ancestors_of_home_are_rejected(self):
        # Security review (#355): a settings folder that contains the home
        # folder would serve every user's files.
        home = self.tmp / "home" / "me"
        inside = home / "loras"
        inside.mkdir(parents=True)
        guard = layout_module._guard_folder
        self.assertIsNone(guard(str(self.tmp / "home"), str(home)))
        self.assertIsNone(guard(str(self.tmp), str(home)))
        self.assertIsNone(guard(str(home), str(home)))
        self.assertEqual(guard(str(inside), str(home)), os.path.normcase(str(inside)))
        # A sibling sharing the name prefix is not an ancestor.
        sibling = self.tmp / "home" / "me2"
        sibling.mkdir()
        self.assertEqual(guard(str(sibling), str(home)), os.path.normcase(str(sibling)))

    def test_windows_ancestors_of_home_are_rejected(self):
        self.assertIsNone(self.guard_nt("C:\\Users", home="C:\\Users\\me"))
        self.assertIsNone(self.guard_nt("c:/users/", home="C:\\Users\\me"))
        self.assertEqual(
            self.guard_nt("C:\\Users\\me\\Loras", home="C:\\Users\\me"),
            "c:\\users\\me\\loras",
        )
        self.assertEqual(
            self.guard_nt("C:\\Users\\meta", home="C:\\Users\\me"),
            "c:\\users\\meta",
        )

    def test_settings_folders_that_contain_home_are_not_served(self):
        home = self.tmp / "home" / "me"
        home.mkdir(parents=True)
        settings = self.tmp / "settings"
        settings.mkdir()
        (settings / "settings.json").write_text(
            json.dumps(
                {
                    "folder_paths": {"loras": [str(self.tmp / "home")]},
                    "example_images_path": str(self.tmp),
                }
            )
        )
        with patch.object(layout_module.Path, "home", return_value=home):
            layout = load_layout(
                str(self.tmp),
                default_roots=lambda: [],
                environ={"LORA_MANAGER_SETTINGS_DIR": str(settings)},
                user_config_dir=lambda: str(self.tmp / "unused"),
            )
        self.assertIsNone(layout.error)
        self.assertEqual(layout.lora_roots, ())
        self.assertIsNone(layout.example_images_root)
        self.assertEqual(layout.media_roots, ())

    def test_missing_folders_add_nothing(self):
        self.assertIsNone(layout_module._guard_folder(str(self.tmp / "x"), "/home"))
        # A missing share on this machine: an unknown server name made
        # Windows wait about 2.8 s for the network (#487).
        self.assertIsNone(
            layout_module._guard_folder(
                "\\\\localhost\\pm-no-such-share\\loras", str(self.tmp)
            )
        )
        self.assertEqual(
            layout_module._guard_folder(str(self.tmp), "/nonexistent-home"),
            os.path.normcase(str(self.tmp)),
        )

    def test_windows_model_path_matches_a_lower_case_root(self):
        self.assertTrue(
            layout_module._inside(
                "C:/Models/Loras/A.safetensors", ("c:\\models\\loras",), LEXICAL_NT
            )
        )
        self.assertFalse(
            layout_module._inside(
                "C:/Models/LorasX/A.safetensors", ("c:\\models\\loras",), LEXICAL_NT
            )
        )
        self.assertFalse(
            layout_module._inside(
                "\\\\other\\share\\A.safetensors", ("c:\\models\\loras",), LEXICAL_NT
            )
        )

    def test_mixed_case_native_containment(self):
        inside = str(self.tmp / "Loras" / "a.png")
        root = os.path.normcase(str(self.tmp / "Loras"))
        self.assertTrue(layout_module._inside(inside, (root,)))
        self.assertFalse(layout_module._inside(str(self.tmp / "other.png"), (root,)))


class RefusedDefaultsTests(LayoutTestCase):
    """#362: ComfyUI's default LoRA folders pass the same guard; the refused
    ones that exist are listed so the layout cache can warn about them."""

    def setUp(self):
        super().setUp()
        self.outer = self.tmp / "outer"
        self.home = self.outer / "me"
        self.home.mkdir(parents=True)
        self.fs_root = os.path.abspath(os.sep)
        self.defaults = [
            str(self.home),
            str(self.outer),
            self.fs_root,
            str(self.tmp / "missing"),
            str(self.default_loras),
        ]
        self.expected = (
            layout_module._canonical(str(self.home)),
            layout_module._canonical(str(self.outer)),
            layout_module._canonical(self.fs_root),
        )
        home_patch = patch.object(layout_module.Path, "home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def load_defaults(self, path="__plugin__"):
        return self.load(path, default_roots=lambda: list(self.defaults))

    def test_refused_existing_defaults_are_listed_on_a_usable_layout(self):
        self.write_settings(self.user_config, {})
        layout = self.load_defaults()
        self.assertIsNone(layout.error)
        self.assertEqual(layout.refused_defaults, self.expected)
        self.assertEqual(
            layout.lora_roots, (os.path.normcase(str(self.default_loras)),)
        )

    def test_they_are_listed_on_an_unusable_centralized_layout(self):
        self.write_settings(
            self.user_config,
            {"sidecar_storage_mode": "centralized", "sidecar_storage_path": "/"},
        )
        layout = self.load_defaults()
        self.assertEqual(layout.error, BAD_SIDECAR_REASON)
        self.assertEqual(layout.refused_defaults, self.expected)

    def test_they_are_listed_without_an_install(self):
        layout = self.load_defaults(None)
        self.assertEqual(layout.error, "no LoraManager install found")
        self.assertEqual(layout.refused_defaults, self.expected)

    def test_nothing_is_listed_when_every_default_is_accepted(self):
        self.write_settings(self.user_config, {})
        self.assertEqual(self.load().refused_defaults, ())


class SummaryTests(LayoutTestCase):

    def test_summary_names_what_the_import_read(self):
        examples = self.tmp / "ex"
        examples.mkdir()
        sidecars = self.plugin / "sidecars"
        sidecars.mkdir()
        self.write_settings(
            self.plugin,
            {
                "use_portable_settings": True,
                "sidecar_storage_mode": "centralized",
                "example_images_path": str(examples),
                "libraries": {"comfyui": {}},
            },
        )
        layout = self.load()
        summary = layout_summary(layout, self.tmp)
        self.assertEqual(
            summary,
            {
                "source": "portable",
                "settings_file": os.path.join(
                    "~", "custom_nodes", "ComfyUI-Lora-Manager", "settings.json"
                ),
                "error": None,
                "mode": "centralized",
                "library": "comfyui",
                "example_images": True,
            },
        )

    def test_summary_outside_home_and_without_settings(self):
        summary = layout_summary(self.load(path=None), self.tmp / "home")
        self.assertEqual(summary["settings_file"], None)
        self.assertEqual(summary["error"], "no LoraManager install found")
        self.assertFalse(summary["example_images"])
        layout = self.load()
        self.assertEqual(
            layout_summary(layout, self.tmp / "home")["settings_file"],
            str(self.user_config / "settings.json"),
        )


if __name__ == "__main__":
    unittest.main()
