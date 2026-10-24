"""Where the library lives (#249 S6b): the one resolver, ``utils/library_location``.

Every case builds its own extension root (the node folder) and ComfyUI user
folder in a temp directory and hands the resolver a fake ``folder_paths``, so
no test reads the real environment, the repository's own files or ComfyUI.
"""

import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import library_location as ll  # noqa: E402


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _folder_paths(base, *, helper=True):
    """A ComfyUI ``folder_paths`` stand-in rooted at ``base``."""
    module = types.SimpleNamespace(base_path=base)
    for name in ("output", "input", "temp", "user"):
        directory = os.path.join(base, name)
        os.makedirs(directory, exist_ok=True)
        setattr(module, f"get_{name}_directory", lambda d=directory: d)
    if helper:
        module.get_system_user_directory = lambda name="system": os.path.join(
            base, "user", "__" + name
        )
    return module


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.realpath(self._tmp.name)
        self.comfy = os.path.join(self.base, "ComfyUI")
        self.ext = os.path.join(self.comfy, "custom_nodes", "ComfyUI_PromptManager")
        os.makedirs(self.ext)
        self.fp = _folder_paths(self.comfy)
        self.default_dir = os.path.join(self.comfy, "user", "__promptmanager")
        ll.clear_override()
        self.addCleanup(ll.clear_override)

    def resolve(self, default_path=None, environ=None, **kwargs):
        kwargs.setdefault("folder_paths", self.fp)
        return ll.resolve_library(
            default_path, environ=environ or {}, extension_root=self.ext, **kwargs
        )

    def config_location(self, environ=None, **kwargs):
        kwargs.setdefault("folder_paths", self.fp)
        return ll.config_location(
            environ=environ or {}, extension_root=self.ext, **kwargs
        )


class TestDefaultDataFolder(_Case):
    def test_uses_comfyui_system_user_helper(self):
        self.assertEqual(ll.default_data_dir(self.fp), self.default_dir)

    def test_falls_back_to_user_directory_without_helper(self):
        fp = _folder_paths(self.comfy, helper=False)
        self.assertEqual(ll.default_data_dir(fp), self.default_dir)

    def test_none_outside_comfyui(self):
        self.assertIsNone(ll.default_data_dir(None))
        self.assertIsNone(ll.default_data_dir(types.SimpleNamespace()))


class TestResolutionOrder(_Case):
    def test_new_install_uses_default_folder_and_creates_nothing_in_node_folder(self):
        location = self.resolve()
        self.assertEqual(location.source, ll.SOURCE_DEFAULT)
        self.assertEqual(location.db_path, os.path.join(self.default_dir, "prompts.db"))
        self.assertEqual(
            location.config_path, os.path.join(self.default_dir, "config.json")
        )
        self.assertFalse(location.movable)
        self.assertEqual(os.listdir(self.ext), [])

    def test_legacy_node_folder_library_stays_in_use(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        location = self.resolve()
        self.assertEqual(location.source, ll.SOURCE_NODE_FOLDER)
        self.assertEqual(location.db_path, os.path.join(self.ext, "prompts.db"))
        self.assertEqual(location.config_path, os.path.join(self.ext, "config.json"))
        self.assertEqual(location.data_dir, self.ext)
        self.assertTrue(location.movable)
        self.assertEqual(location.target_dir, self.default_dir)

    def test_legacy_wins_over_an_unmarked_default_folder_library(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        _touch(os.path.join(self.default_dir, "prompts.db"))
        self.assertEqual(self.resolve().source, ll.SOURCE_NODE_FOLDER)

    def test_explicit_default_path_is_honoured_as_today(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        elsewhere = os.path.join(self.base, "elsewhere", "pm.db")
        location = self.resolve(elsewhere)
        self.assertEqual(location.source, ll.SOURCE_CONFIG)
        self.assertEqual(location.db_path, elsewhere)
        self.assertFalse(location.movable)
        relative = self.resolve("data/my.db")
        self.assertEqual(relative.db_path, os.path.join(self.ext, "data", "my.db"))
        self.assertEqual(relative.source, ll.SOURCE_CONFIG)

    def test_default_setting_value_is_not_explicit(self):
        for value in (None, "", "prompts.db"):
            self.assertEqual(self.resolve(value).source, ll.SOURCE_DEFAULT, value)

    def test_environment_folder_wins(self):
        data = os.path.join(self.base, "pm-data")
        location = self.resolve(environ={ll.DATA_DIR_ENV: data})
        self.assertEqual(location.source, ll.SOURCE_ENVIRONMENT)
        self.assertEqual(location.db_path, os.path.join(data, "prompts.db"))
        self.assertEqual(location.config_path, os.path.join(data, "config.json"))

    def test_environment_folder_with_a_library_wins_over_legacy(self):
        data = os.path.join(self.base, "pm-data")
        _touch(os.path.join(data, "prompts.db"))
        _touch(os.path.join(self.ext, "prompts.db"))
        location = self.resolve(environ={ll.DATA_DIR_ENV: data})
        self.assertEqual(location.source, ll.SOURCE_ENVIRONMENT)

    def test_empty_environment_folder_never_replaces_a_legacy_library(self):
        data = os.path.join(self.base, "pm-data")
        _touch(os.path.join(self.ext, "prompts.db"))
        location = self.resolve(environ={ll.DATA_DIR_ENV: data})
        self.assertEqual(location.source, ll.SOURCE_NODE_FOLDER)
        self.assertEqual(location.db_path, os.path.join(self.ext, "prompts.db"))
        self.assertEqual(location.problem, "data-dir-empty")
        self.assertEqual(location.target_dir, data)
        self.assertTrue(location.movable)

    def test_config_path_environment_is_honoured(self):
        custom = os.path.join(self.base, "cfg", "custom.json")
        location = self.resolve(environ={ll.CONFIG_PATH_ENV: custom})
        self.assertEqual(location.config_path, custom)
        self.assertEqual(location.source, ll.SOURCE_DEFAULT)

    def test_without_comfyui_the_node_folder_is_used(self):
        location = self.resolve(folder_paths=None)
        self.assertEqual(location.source, ll.SOURCE_NODE_FOLDER)
        self.assertEqual(location.db_path, os.path.join(self.ext, "prompts.db"))
        self.assertFalse(location.movable)

    def test_override_after_a_live_move_wins(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        moved = ll.LibraryLocation(
            source=ll.SOURCE_DEFAULT,
            db_path=os.path.join(self.default_dir, "prompts.db"),
            config_path=os.path.join(self.default_dir, "config.json"),
            data_dir=self.default_dir,
            target_dir=self.default_dir,
        )
        ll.switch_to(moved)
        self.assertEqual(self.resolve(), moved)
        self.assertEqual(self.config_location(), (moved.config_path, moved.data_dir))
        ll.clear_override()
        self.assertEqual(self.resolve().source, ll.SOURCE_NODE_FOLDER)


class TestMarker(_Case):
    def _marker(self, directory, state):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, ll.MARKER_NAME), "w") as handle:
            json.dump({"version": 1, "state": state}, handle)

    def test_moved_marker_wins_while_the_old_file_was_not_renamed_yet(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        _touch(os.path.join(self.default_dir, "prompts.db"))
        self._marker(self.default_dir, "moved")
        location = self.resolve()
        self.assertEqual(location.source, ll.SOURCE_DEFAULT)
        self.assertEqual(location.db_path, os.path.join(self.default_dir, "prompts.db"))
        self.assertEqual(
            location.config_path, os.path.join(self.default_dir, "config.json")
        )

    def test_pending_marker_keeps_the_old_library(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        _touch(os.path.join(self.default_dir, "prompts.db"))
        self._marker(self.default_dir, "pending")
        self.assertEqual(self.resolve().source, ll.SOURCE_NODE_FOLDER)
        data = os.path.join(self.base, "pm-data")
        _touch(os.path.join(data, "prompts.db"))
        self._marker(data, "pending")
        location = self.resolve(environ={ll.DATA_DIR_ENV: data})
        self.assertEqual(location.source, ll.SOURCE_NODE_FOLDER)
        self.assertEqual(location.problem, "move-unfinished")

    def test_unreadable_marker_is_not_trusted(self):
        _touch(os.path.join(self.ext, "prompts.db"))
        _touch(os.path.join(self.default_dir, "prompts.db"))
        _touch(os.path.join(self.default_dir, ll.MARKER_NAME), b"{not json")
        self.assertEqual(self.resolve().source, ll.SOURCE_NODE_FOLDER)
        self.assertEqual(ll.read_marker(self.default_dir), {})

    def test_renamed_old_file_is_never_picked(self):
        _touch(os.path.join(self.ext, "prompts.db.moved-to-__promptmanager"))
        _touch(os.path.join(self.default_dir, "prompts.db"))
        self.assertEqual(self.resolve().source, ll.SOURCE_DEFAULT)


class TestConfigLocation(_Case):
    def test_legacy_config_without_a_database_keeps_being_read(self):
        _touch(os.path.join(self.ext, "config.json"), b"{}")
        path, data_dir = self.config_location()
        self.assertEqual(path, os.path.join(self.ext, "config.json"))
        self.assertEqual(data_dir, self.ext)

    def test_legacy_config_alone_never_puts_a_new_database_in_the_node_folder(self):
        _touch(os.path.join(self.ext, "config.json"), b"{}")
        location = self.resolve()
        self.assertEqual(location.db_path, os.path.join(self.default_dir, "prompts.db"))
        self.assertEqual(location.config_path, os.path.join(self.ext, "config.json"))
        self.assertFalse(location.movable)

    def test_new_install_reads_config_from_the_data_folder(self):
        path, data_dir = self.config_location()
        self.assertEqual(path, os.path.join(self.default_dir, "config.json"))
        self.assertEqual(data_dir, self.default_dir)

    def test_logs_and_cache_follow_the_data_folder(self):
        self.assertEqual(
            ll.logs_dir(environ={}, extension_root=self.ext, folder_paths=self.fp),
            os.path.join(self.default_dir, "logs"),
        )
        _touch(os.path.join(self.ext, "prompts.db"))
        self.assertEqual(
            ll.logs_dir(environ={}, extension_root=self.ext, folder_paths=self.fp),
            os.path.join(self.ext, "logs"),
        )
        self.assertEqual(
            ll.cache_dir(
                "lora_images", environ={}, extension_root=self.ext, folder_paths=self.fp
            ),
            os.path.join(self.ext, "data", "lora_images"),
        )


class TestDataDirValidation(_Case):
    def problem(self, path, **kwargs):
        kwargs.setdefault("folder_paths", self.fp)
        return ll.data_dir_problem(path, **kwargs)

    def test_valid_folder(self):
        self.assertIsNone(self.problem(os.path.join(self.base, "pm-data")))
        self.assertIsNone(self.problem(self.default_dir))

    def test_relative_and_empty_paths_are_refused(self):
        self.assertEqual(self.problem("pm-data"), "data-dir-not-absolute")
        self.assertEqual(self.problem(""), "data-dir-not-absolute")

    def test_served_folders_are_refused(self):
        for name in ("output", "input", "temp"):
            inside = os.path.join(self.comfy, name, "pm")
            self.assertEqual(self.problem(inside), "data-dir-served", name)
        self.assertEqual(
            self.problem(os.path.join(self.comfy, "output")), "data-dir-served"
        )

    def test_public_user_folder_is_refused_but_system_users_are_fine(self):
        public = os.path.join(self.comfy, "user", "default", "pm")
        self.assertEqual(self.problem(public), "data-dir-served")
        self.assertIsNone(self.problem(os.path.join(self.comfy, "user", "__pm")))

    def test_gallery_source_is_refused(self):
        source = os.path.join(self.base, "gallery")
        os.makedirs(source)
        self.assertEqual(
            self.problem(os.path.join(source, "pm"), gallery_roots=[source]),
            "data-dir-served",
        )
        self.assertEqual(
            self.problem(
                os.path.join(source, "pm"),
                environ={ll.EXTRA_GALLERY_ROOTS_ENV: source},
            ),
            "data-dir-served",
        )

    def test_symlink_into_output_is_refused(self):
        link = os.path.join(self.base, "link-to-output")
        try:
            os.symlink(os.path.join(self.comfy, "output"), link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        self.assertEqual(self.problem(os.path.join(link, "pm")), "data-dir-served")

    def test_case_variant_spelling_of_output_is_refused_where_the_volume_folds_case(
        self,
    ):
        upper = os.path.join(self.comfy, "OUTPUT")
        if not os.path.isdir(upper):
            self.skipTest("this volume is case-sensitive")
        self.assertEqual(self.problem(os.path.join(upper, "pm")), "data-dir-served")

    def test_invalid_environment_folder_is_ignored_with_a_problem(self):
        inside = os.path.join(self.comfy, "output", "pm")
        location = self.resolve(environ={ll.DATA_DIR_ENV: inside})
        self.assertEqual(location.source, ll.SOURCE_DEFAULT)
        self.assertEqual(location.problem, "data-dir-served")
        self.assertFalse(os.path.exists(inside))
        relative = self.resolve(environ={ll.DATA_DIR_ENV: "relative/dir"})
        self.assertEqual(relative.problem, "data-dir-not-absolute")


class TestOneRuleForConfigAndDatabase(_Case):
    """M1: config.json and the database never split over PROMPT_MANAGER_DATA_DIR."""

    def test_a_source_listed_only_in_a_config_file_rules_out_the_folder_for_both(self):
        source = os.path.join(self.base, "gallery")
        os.makedirs(source)
        _touch(
            os.path.join(self.ext, "config.json"),
            json.dumps({"gallery": {"monitoring": {"directories": [source]}}}).encode(),
        )
        environ = {ll.DATA_DIR_ENV: os.path.join(source, "pm")}
        location = self.resolve(environ=environ)
        config_path, data_dir = self.config_location(environ=environ)
        self.assertEqual(location.problem, "data-dir-served")
        self.assertEqual(
            (location.config_path, location.data_dir), (config_path, data_dir)
        )
        self.assertNotEqual(location.source, ll.SOURCE_ENVIRONMENT)

    def test_a_relative_source_is_taken_from_comfyui(self):
        self.fp.base_path = self.comfy
        os.makedirs(os.path.join(self.comfy, "pictures"))
        _touch(
            os.path.join(self.ext, "config.json"),
            json.dumps(
                {"gallery": {"monitoring": {"directories": ["pictures"]}}}
            ).encode(),
        )
        environ = {ll.DATA_DIR_ENV: os.path.join(self.comfy, "pictures", "pm")}
        self.assertEqual(self.resolve(environ=environ).problem, "data-dir-served")
        self.assertEqual(self.config_location(environ=environ)[1], self.ext)

    def test_unreadable_or_odd_config_files_add_no_source(self):
        for payload in (
            b"{not json",
            b"[]",
            json.dumps({"gallery": {"monitoring": {"directories": "x"}}}).encode(),
        ):
            _touch(os.path.join(self.ext, "config.json"), payload)
            environ = {ll.DATA_DIR_ENV: os.path.join(self.base, "pm-data")}
            self.assertIsNone(self.resolve(environ=environ).problem)


class TestSameFolder(_Case):
    def test_same_folder_follows_links_and_case(self):
        self.assertTrue(ll.same_folder(self.ext, self.ext + os.sep))
        self.assertFalse(ll.same_folder(self.ext, self.default_dir))
        self.assertFalse(ll.same_folder(self.ext, os.path.join(self.base, "missing")))


if __name__ == "__main__":
    unittest.main()
