"""
Tests for GalleryConfig and PromptManagerConfig.

The config module imports `from server import PromptServer` at module level,
which is ComfyUI's server. We mock it before import to run tests standalone.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# Mock ComfyUI's server module before importing config
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py import config as config_module
from py.config import GalleryConfig, IntegrationConfig, PromptManagerConfig


class TestGalleryConfig(unittest.TestCase):
    """Test GalleryConfig class."""

    def setUp(self):
        """Save original values to restore after each test."""
        self._orig = GalleryConfig.get_config()

    def tearDown(self):
        """Restore original config values."""
        GalleryConfig.update_config(self._orig)

    def test_get_config_structure(self):
        config = GalleryConfig.get_config()
        self.assertIn("monitoring", config)
        self.assertIn("tracking", config)
        self.assertIn("database", config)
        self.assertIn("web_interface", config)
        self.assertIn("performance", config)

    def test_default_values(self):
        config = GalleryConfig.get_config()
        self.assertTrue(config["monitoring"]["enabled"])
        self.assertEqual(
            config["monitoring"]["extensions"],
            [".png", ".jpg", ".jpeg", ".webp", ".gif"],
        )
        self.assertEqual(config["tracking"]["prompt_timeout"], 600)
        self.assertEqual(config["web_interface"]["images_per_page"], 20)

    def test_update_monitoring(self):
        GalleryConfig.update_config(
            {"monitoring": {"enabled": False, "processing_delay": 5.0}}
        )
        self.assertFalse(GalleryConfig.MONITORING_ENABLED)
        self.assertEqual(GalleryConfig.PROCESSING_DELAY, 5.0)

    def test_update_tracking(self):
        GalleryConfig.update_config({"tracking": {"prompt_timeout": 300}})
        self.assertEqual(GalleryConfig.PROMPT_TIMEOUT, 300)

    def test_update_database(self):
        GalleryConfig.update_config(
            {"database": {"auto_cleanup": False, "max_image_age_days": 30}}
        )
        self.assertFalse(GalleryConfig.AUTO_CLEANUP_MISSING_FILES)
        self.assertEqual(GalleryConfig.MAX_IMAGE_AGE_DAYS, 30)

    def test_update_web_interface(self):
        GalleryConfig.update_config(
            {"web_interface": {"images_per_page": 50, "thumbnail_size": 512}}
        )
        self.assertEqual(GalleryConfig.IMAGES_PER_PAGE, 50)
        # #317 R1: the never-read thumbnail_size is ignored (thumbnails.edge).
        self.assertFalse(hasattr(GalleryConfig, "THUMBNAIL_SIZE"))

    def test_update_performance(self):
        GalleryConfig.update_config({"performance": {"max_concurrent_processing": 5}})
        self.assertEqual(GalleryConfig.MAX_CONCURRENT_PROCESSING, 5)

    def test_partial_update_preserves_other_values(self):
        original_timeout = GalleryConfig.PROMPT_TIMEOUT
        GalleryConfig.update_config({"monitoring": {"enabled": False}})
        self.assertEqual(GalleryConfig.PROMPT_TIMEOUT, original_timeout)

    def test_empty_update_changes_nothing(self):
        before = GalleryConfig.get_config()
        GalleryConfig.update_config({})
        after = GalleryConfig.get_config()
        self.assertEqual(before, after)


class TestPromptManagerConfig(unittest.TestCase):
    """Test PromptManagerConfig class."""

    def setUp(self):
        self._orig = PromptManagerConfig.get_config()

    def tearDown(self):
        PromptManagerConfig.update_config(self._orig)

    def test_get_config_structure(self):
        config = PromptManagerConfig.get_config()
        self.assertIn("database", config)
        self.assertIn("web_ui", config)
        self.assertIn("performance", config)
        self.assertIn("gallery", config)

    def test_gallery_config_nested(self):
        config = PromptManagerConfig.get_config()
        # Gallery section should match GalleryConfig output
        self.assertIn("monitoring", config["gallery"])
        self.assertIn("tracking", config["gallery"])

    def test_default_database_values(self):
        config = PromptManagerConfig.get_config()
        self.assertEqual(config["database"]["default_path"], "prompts.db")
        self.assertTrue(config["database"]["enable_duplicate_detection"])
        self.assertTrue(config["database"]["enable_auto_save"])

    def test_default_web_ui_values(self):
        config = PromptManagerConfig.get_config()
        self.assertEqual(config["web_ui"]["webui_display_mode"], "newtab")

    def test_update_database(self):
        PromptManagerConfig.update_config({"database": {"default_path": "custom.db"}})
        self.assertEqual(PromptManagerConfig.DEFAULT_DB_PATH, "custom.db")

    def test_update_web_ui(self):
        PromptManagerConfig.update_config({"web_ui": {"webui_display_mode": "popup"}})
        self.assertEqual(PromptManagerConfig.WEBUI_DISPLAY_MODE, "popup")

    def test_update_performance(self):
        PromptManagerConfig.update_config({"performance": {"max_search_results": 50}})
        self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, 50)

    def test_infinite_scroll_default_off_and_only_booleans_apply(self):
        self.assertIn("infinite_scroll", PromptManagerConfig.get_config()["web_ui"])
        PromptManagerConfig.update_config({"web_ui": {"infinite_scroll": True}})
        self.assertTrue(PromptManagerConfig.INFINITE_SCROLL)
        PromptManagerConfig.update_config({"web_ui": {"infinite_scroll": "yes"}})
        self.assertTrue(PromptManagerConfig.INFINITE_SCROLL)
        PromptManagerConfig.update_config({"web_ui": {"infinite_scroll": False}})
        self.assertFalse(PromptManagerConfig.INFINITE_SCROLL)

    def test_worker_threads_default_is_half_the_cores_and_current_is_in_range(self):
        # The current value may come from config.json (loaded at import), so the
        # rule is checked on the constant and only the range on the live value.
        cores = PromptManagerConfig.max_worker_threads()
        self.assertGreaterEqual(cores, 1)
        self.assertEqual(PromptManagerConfig.DEFAULT_WORKER_THREADS, max(1, cores // 2))
        current = PromptManagerConfig.get_config()["performance"]["worker_threads"]
        self.assertGreaterEqual(current, 1)
        self.assertLessEqual(current, cores)

    def test_update_worker_threads_is_clamped_to_the_machine(self):
        cores = PromptManagerConfig.max_worker_threads()
        PromptManagerConfig.update_config({"performance": {"worker_threads": 1}})
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, 1)
        PromptManagerConfig.update_config(
            {"performance": {"worker_threads": cores + 50}}
        )
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, cores)
        PromptManagerConfig.update_config({"performance": {"worker_threads": 0}})
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, 1)

    def test_update_worker_threads_ignores_junk_from_a_hand_edited_file(self):
        PromptManagerConfig.update_config({"performance": {"worker_threads": 1}})
        PromptManagerConfig.update_config({"performance": {"worker_threads": "lots"}})
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, 1)
        PromptManagerConfig.update_config({"performance": {"worker_threads": True}})
        self.assertEqual(PromptManagerConfig.WORKER_THREADS, 1)

    def test_update_propagates_to_gallery(self):
        PromptManagerConfig.update_config(
            {"gallery": {"monitoring": {"enabled": False}}}
        )
        self.assertFalse(GalleryConfig.MONITORING_ENABLED)
        # Restore
        GalleryConfig.MONITORING_ENABLED = True

    def test_save_and_load_file(self):
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", dir=tempfile.gettempdir()
        )
        tmp.close()
        try:
            # Modify a value
            PromptManagerConfig.MAX_SEARCH_RESULTS = 42
            PromptManagerConfig.save_to_file(tmp.name)

            # Reset and reload
            PromptManagerConfig.MAX_SEARCH_RESULTS = 100
            PromptManagerConfig.load_from_file(tmp.name)
            self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, 42)
        finally:
            os.unlink(tmp.name)

    def test_load_nonexistent_file_uses_defaults(self):
        # Should not raise, just log and continue
        original = PromptManagerConfig.MAX_SEARCH_RESULTS
        PromptManagerConfig.load_from_file("/nonexistent/path/config.json")
        self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, original)

    def test_load_invalid_json_file(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        tmp.write("not valid json {{")
        tmp.close()
        try:
            original = PromptManagerConfig.MAX_SEARCH_RESULTS
            PromptManagerConfig.load_from_file(tmp.name)
            # Should not crash, keeps existing values
            self.assertEqual(PromptManagerConfig.MAX_SEARCH_RESULTS, original)
        finally:
            os.unlink(tmp.name)

    def test_save_creates_directory(self):
        tmp_dir = tempfile.mkdtemp()
        nested_path = os.path.join(tmp_dir, "subdir", "config.json")
        try:
            PromptManagerConfig.save_to_file(nested_path)
            self.assertTrue(os.path.exists(nested_path))
            with open(nested_path) as f:
                data = json.load(f)
            self.assertIn("database", data)
        finally:
            import shutil

            shutil.rmtree(tmp_dir)

    def test_saved_file_is_private_to_the_user(self):
        import stat

        tmp_dir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp_dir, "config.json")
            PromptManagerConfig.save_to_file(path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if os.name == "posix":
                self.assertEqual(mode, 0o600)
            with open(path) as f:
                self.assertIn("database", json.load(f))
            # Overwriting an existing, more open file tightens it as well
            os.chmod(path, 0o644)
            PromptManagerConfig.save_to_file(path)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_saved_file_is_valid_json(self):
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", dir=tempfile.gettempdir()
        )
        tmp.close()
        try:
            PromptManagerConfig.save_to_file(tmp.name)
            with open(tmp.name) as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)
        finally:
            os.unlink(tmp.name)


class TestIntegrationConfig(unittest.TestCase):
    """Test IntegrationConfig for LoRA Manager settings."""

    def setUp(self):
        """Save original values to restore after each test."""
        self._orig = IntegrationConfig.get_config()

    def tearDown(self):
        """Restore original config values."""
        IntegrationConfig.update_config(self._orig)

    def test_reset_to_disabled(self):
        """Integrations can be fully disabled via update_config."""
        IntegrationConfig.update_config(
            {
                "lora_manager": {
                    "enabled": False,
                    "path": "",
                    "trigger_words_enabled": False,
                    "civitai_api_key": "",
                }
            }
        )
        config = IntegrationConfig.get_config()
        lora = config["lora_manager"]
        self.assertFalse(lora["enabled"])
        self.assertEqual(lora["path"], "")
        self.assertFalse(lora["trigger_words_enabled"])
        self.assertEqual(lora["civitai_api_key"], "")

    def test_get_config_structure(self):
        config = IntegrationConfig.get_config()
        self.assertIn("lora_manager", config)
        lora = config["lora_manager"]
        self.assertIn("enabled", lora)
        self.assertIn("path", lora)
        self.assertIn("trigger_words_enabled", lora)
        self.assertIn("civitai_api_key", lora)

    def test_update_config_enables(self):
        IntegrationConfig.update_config(
            {
                "lora_manager": {
                    "enabled": True,
                    "path": "/some/path",
                    "trigger_words_enabled": True,
                    "civitai_api_key": "test-key-123",
                }
            }
        )
        self.assertTrue(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertEqual(IntegrationConfig.LORA_MANAGER_PATH, "/some/path")
        self.assertTrue(IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED)
        self.assertEqual(IntegrationConfig.CIVITAI_API_KEY, "test-key-123")

    def test_update_partial(self):
        """Updating one field shouldn't affect others."""
        # Reset to known state first
        IntegrationConfig.update_config(
            {"lora_manager": {"enabled": False, "path": "/known"}}
        )
        # Now update only enabled
        IntegrationConfig.update_config({"lora_manager": {"enabled": True}})
        self.assertTrue(IntegrationConfig.LORA_MANAGER_ENABLED)
        self.assertEqual(IntegrationConfig.LORA_MANAGER_PATH, "/known")

    def test_update_empty_dict_noop(self):
        """Updating with empty dict preserves current state."""
        before = IntegrationConfig.get_config()
        IntegrationConfig.update_config({})
        after = IntegrationConfig.get_config()
        self.assertEqual(before, after)

    def test_roundtrip(self):
        """get_config → update_config → get_config should be stable."""
        IntegrationConfig.update_config(
            {"lora_manager": {"enabled": True, "path": "/test"}}
        )
        config1 = IntegrationConfig.get_config()
        IntegrationConfig.update_config(config1)
        config2 = IntegrationConfig.get_config()
        self.assertEqual(config1, config2)


class TestConfigPathOverride(unittest.TestCase):
    """PROMPT_MANAGER_CONFIG_PATH relocates the persisted config.json."""

    ENV = "PROMPT_MANAGER_CONFIG_PATH"

    def setUp(self):
        self._orig_env = os.environ.pop(self.ENV, None)
        self._orig = PromptManagerConfig.get_config()

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop(self.ENV, None)
        else:
            os.environ[self.ENV] = self._orig_env
        PromptManagerConfig.update_config(self._orig)

    def test_default_config_path_is_repo_config_json(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        expected = os.path.join(repo_root, "config.json")
        self.assertEqual(
            os.path.normcase(PromptManagerConfig.get_config_path()),
            os.path.normcase(expected),
        )

    def test_env_override_is_used(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom = os.path.join(tmpdir, "custom.json")
            os.environ[self.ENV] = custom
            self.assertEqual(PromptManagerConfig.get_config_path(), custom)

    def test_save_without_path_uses_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom = os.path.join(tmpdir, "nested", "custom.json")
            os.environ[self.ENV] = custom
            PromptManagerConfig.RESULT_TIMEOUT = 42

            PromptManagerConfig.save_to_file()

            with open(custom) as f:
                saved = json.load(f)
            self.assertEqual(saved["web_ui"]["result_timeout"], 42)

    def test_load_without_path_uses_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom = os.path.join(tmpdir, "custom.json")
            with open(custom, "w") as f:
                json.dump({"web_ui": {"result_timeout": 77}}, f)
            os.environ[self.ENV] = custom

            PromptManagerConfig.load_from_file()

            self.assertEqual(PromptManagerConfig.RESULT_TIMEOUT, 77)


class TestAutotagConfig(unittest.TestCase):
    """``autotag.defaults.suggestion_limit``: integer 1..100, default 10 (D68)."""

    def setUp(self):
        self.config_class = getattr(config_module, "AutotagConfig", None)
        self.assertIsNotNone(self.config_class, "AutotagConfig is not implemented")
        self._orig = PromptManagerConfig.get_config()
        self._defaults = {
            attr: getattr(self.config_class, attr, None)
            for attr in (
                "MODEL_TYPE",
                "EXISTING",
                "GENERAL_THRESHOLD",
                "CHARACTER_THRESHOLD",
                "TEMPLATE",
                "SUGGESTION_LIMIT",
            )
        }

    def tearDown(self):
        for attr, value in self._defaults.items():
            setattr(self.config_class, attr, value)
        PromptManagerConfig.update_config(self._orig)

    def _load(self, value):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            with open(path, "w") as f:
                # json writes NaN for float("nan"), which json.load reads back.
                json.dump({"autotag": {"defaults": {"suggestion_limit": value}}}, f)
            with self.assertLogs("prompt_manager.config", level="WARNING") as logs:
                PromptManagerConfig.load_from_file(path)
        return logs

    def test_every_copy_of_the_tag_set_cap_is_one_value(self):
        # A suggestion request is bounded by the image tag set cap; the
        # copies that cannot import it (config loads before the database,
        # the inspection envelope stays storage-free) are pinned to it here.
        from database.annotations import MAX_IMAGE_TAGS
        from py import autotag
        from utils import inspection_response

        self.assertEqual(autotag.MAX_SUGGESTION_LIMIT, MAX_IMAGE_TAGS)
        self.assertEqual(config_module.MAX_SUGGESTION_LIMIT, MAX_IMAGE_TAGS)
        self.assertEqual(inspection_response.MAX_IMAGE_TAGS, MAX_IMAGE_TAGS)

    def test_default_is_ten_and_persisted_under_autotag_defaults(self):
        self.assertEqual(config_module.DEFAULT_SUGGESTION_LIMIT, 10)
        self.assertEqual(config_module.MAX_SUGGESTION_LIMIT, 100)
        self.assertEqual(self.config_class.suggestion_limit(), 10)
        self.assertEqual(
            PromptManagerConfig.get_config()["autotag"],
            {
                "defaults": {
                    "model_type": "wd14-swinv2",
                    "existing": "skip",
                    "general_threshold": 0.35,
                    "character_threshold": 0.85,
                    "suggestion_limit": 10,
                }
            },
        )
        # The built-in template is not written; a saved one is.
        self.config_class.TEMPLATE = "Saved."
        self.assertEqual(
            PromptManagerConfig.get_config()["autotag"]["defaults"]["template"],
            "Saved.",
        )
        self.config_class.TEMPLATE = config_module.DEFAULT_TAG_TEMPLATE
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            PromptManagerConfig.update_config(
                {"autotag": {"defaults": {"suggestion_limit": 25}}}
            )
            PromptManagerConfig.save_to_file(path)
            self.config_class.SUGGESTION_LIMIT = 10
            PromptManagerConfig.load_from_file(path)
        self.assertEqual(self.config_class.suggestion_limit(), 25)

    def test_valid_limits_are_kept(self):
        for value in (1, 10, 100):
            with self.subTest(value=value):
                PromptManagerConfig.update_config(
                    {"autotag": {"defaults": {"suggestion_limit": value}}}
                )
                self.assertEqual(self.config_class.suggestion_limit(), value)

    def test_autotag_defaults_fall_back_per_key_on_load(self):
        """#328: each saved tagging default is checked alone on load; a bad
        hand-edited value falls back to its default with one warning and
        leaves the other keys as saved."""
        good = {
            "model_type": "gguf",
            "existing": "merge",
            "general_threshold": 0.4,
            "character_threshold": 0.7,
            "template": "Tags only.",
            "suggestion_limit": 15,
        }
        fallback = {
            "model_type": "wd14-swinv2",
            "existing": "skip",
            "general_threshold": 0.35,
            "character_threshold": 0.85,
            "template": config_module.DEFAULT_TAG_TEMPLATE,
            "suggestion_limit": 10,
        }
        bad_values = {
            "model_type": ["wd14-convnext", 3, None],
            "existing": ["replace", "", True],
            "general_threshold": [1.5, -0.1, float("nan"), "0.4", True],
            "character_threshold": [2, float("inf"), None],
            # 4000 characters are at most 16,000 UTF-8 bytes, so the 16 KiB
            # byte cap can only be met past the character cap.
            "template": ["", "   ", "x" * 4001, "\U0001f600" * 4097, "\ud800", 7],
        }
        for key, values in bad_values.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    saved = dict(good, **{key: value})
                    with tempfile.TemporaryDirectory() as tmpdir:
                        path = os.path.join(tmpdir, "config.json")
                        with open(path, "w") as f:
                            json.dump({"autotag": {"defaults": saved}}, f)
                        with self.assertLogs(
                            "prompt_manager.config", level="WARNING"
                        ) as logs:
                            PromptManagerConfig.load_from_file(path)
                    self.assertEqual(len(logs.records), 1, logs.output)
                    self.assertIn(f"autotag.defaults.{key}", logs.output[0])
                    loaded = {
                        k: getattr(self.config_class, attr)
                        for k, (attr, _v, _d) in self.config_class._KEYS.items()
                    }
                    self.assertEqual(loaded, dict(good, **{key: fallback[key]}))

    def test_tagging_validators_moved_with_their_messages(self):
        validate_threshold = config_module.validate_threshold
        validate_template = config_module.validate_template
        self.assertEqual(validate_threshold("general_threshold", 1), 1.0)
        for value, message in (
            (float("nan"), "general_threshold must be a finite number"),
            ("0.4", "general_threshold must be a finite number"),
            (1.01, "general_threshold must be between 0 and 1"),
        ):
            with self.assertRaises(ValueError) as caught:
                validate_threshold("general_threshold", value)
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(validate_template("Tags."), "Tags.")
        for value, message in (
            ("  ", "instructions must be non-empty text"),
            ("x" * 4001, "instructions must be at most 4000 characters"),
            ("\ud800", "instructions must be valid Unicode text"),
        ):
            with self.assertRaises(ValueError) as caught:
                validate_template(value)
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(
            config_module.MODEL_TYPES, {"gguf", "hf", "wd14-swinv2", "wd14-vit"}
        )

    def test_suggestion_limit_setting_validated_on_load(self):
        for value in (0, 101, "10", float("nan"), 2.5, True, None, [10]):
            with self.subTest(value=value):
                self.config_class.SUGGESTION_LIMIT = 42
                logs = self._load(value)
                self.assertEqual(self.config_class.suggestion_limit(), 10)
                self.assertIn("suggestion_limit", "\n".join(logs.output))

    def test_missing_or_malformed_sections_keep_the_current_value(self):
        self.config_class.SUGGESTION_LIMIT = 7
        for config in ({}, {"autotag": {}}, {"autotag": {"defaults": {}}}):
            PromptManagerConfig.update_config(config)
            self.assertEqual(self.config_class.suggestion_limit(), 7)
        with self.assertLogs("prompt_manager.config", level="WARNING"):
            PromptManagerConfig.update_config({"autotag": "nope"})
        with self.assertLogs("prompt_manager.config", level="WARNING"):
            PromptManagerConfig.update_config({"autotag": {"defaults": [1]}})
        self.assertEqual(self.config_class.suggestion_limit(), 7)


if __name__ == "__main__":
    unittest.main()
