"""Saved instructions with a default per language-model engine (#431, PR 2).

The entry and default validation, the in-memory migration of the old single
``autotag.defaults.template`` at load (first upgrade and re-upgrade), the
load fallbacks, and ``AutotagConfig.default_instructions_text``.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# config.py imports ComfyUI's server module; stand it in (as test_config does).
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

from py.config import AutotagConfig, PromptManagerConfig  # noqa: E402
from py.tagging_defaults import (  # noqa: E402
    BUILTIN_INSTRUCTIONS_ID,
    DEFAULT_TAG_TEMPLATE,
    MAX_SAVED_INSTRUCTIONS,
    validate_default_instructions,
    validate_instructions,
)

ATTRS = ("MODEL_TYPE", "INSTRUCTIONS", "DEFAULT_INSTRUCTIONS", "_LEGACY_TEMPLATE")


def entry(id_, name, text="Tags only."):
    return {"id": id_, "name": name, "text": text}


def sha12(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class TestValidation(unittest.TestCase):
    def test_entries_are_kept_with_trimmed_names(self):
        self.assertEqual(
            validate_instructions([entry("0123456789ab", "  Photo ", "Tags.")]),
            [entry("0123456789ab", "Photo", "Tags.")],
        )
        self.assertEqual(validate_instructions([]), [])

    def test_every_refusal_sentence(self):
        ok = entry("0123456789ab", "One")
        cases = (
            ("x", "Saved instructions must be a list."),
            (
                [
                    entry(f"{i:012x}", f"n{i}")
                    for i in range(MAX_SAVED_INSTRUCTIONS + 1)
                ],
                "Saved instructions: at most 20 entries besides Built-in.",
            ),
            ([ok, "x"], "Saved instructions: entry 2 has an invalid id."),
            (
                [entry("ABCDEF012345", "A")],
                "Saved instructions: entry 1 has an invalid id.",
            ),
            ([entry("builtin", "A")], "Saved instructions: entry 1 has an invalid id."),
            (
                [{"id": "0123456789ab", "name": "A"}],
                "Saved instructions: entry 1 has an invalid id.",
            ),
            (
                [ok, entry("0123456789ab", "Two")],
                "Saved instructions: entry 2 repeats the id of entry 1.",
            ),
            (
                [entry("0123456789ab", "  ")],
                "Saved instructions: entry 1 needs a name.",
            ),
            (
                [entry("0123456789ab", "x" * 61)],
                "Saved instructions: entry 1 name must be at most 60 characters.",
            ),
            (
                [entry("0123456789ab", "a\nb")],
                "Saved instructions: entry 1 name must not contain a line break.",
            ),
            (
                [ok, entry("0123456789ac", "one")],
                "Saved instructions: entry 2 name “one” is already used.",
            ),
            (
                [entry("0123456789ab", "BUILT-IN")],
                "Saved instructions: the name “Built-in” is reserved.",
            ),
            (
                [entry("0123456789ab", "A", "  ")],
                "Saved instructions: entry 1 text must be non-empty text.",
            ),
            (
                [entry("0123456789ab", "A", "x" * 4001)],
                "Saved instructions: entry 1 text must be at most 4000 characters.",
            ),
        )
        for value, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(ValueError) as caught:
                    validate_instructions(value)
                self.assertEqual(str(caught.exception), message)

    def test_defaults_name_built_in_or_a_saved_entry(self):
        saved = [entry("0123456789ab", "One")]
        self.assertEqual(
            validate_default_instructions(
                {"gguf": "builtin", "hf": "0123456789ab"}, saved
            ),
            {"gguf": "builtin", "hf": "0123456789ab"},
        )
        for value, message in (
            (
                {"gguf": "ffffffffffff", "hf": "builtin"},
                "Default for GGUF JoyCaption must be one of the saved instructions.",
            ),
            (
                {"gguf": "builtin", "hf": "nope"},
                "Default for HuggingFace JoyCaption must be one of the saved "
                "instructions.",
            ),
            (
                {"gguf": "builtin"},
                "Default instructions must name GGUF and HuggingFace.",
            ),
            (
                {"gguf": [], "hf": "builtin"},
                "Default for GGUF JoyCaption must be one of the saved instructions.",
            ),
            ("builtin", "Default instructions must name GGUF and HuggingFace."),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    validate_default_instructions(value, saved)
                self.assertEqual(str(caught.exception), message)


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        saved = {attr: getattr(AutotagConfig, attr) for attr in ATTRS}
        self.addCleanup(
            lambda: [setattr(AutotagConfig, a, v) for a, v in saved.items()]
        )
        AutotagConfig.update_config({"defaults": {}})

    def load(self, defaults, warnings=0):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"autotag": {"defaults": defaults}}, handle)
            before = os.stat(path).st_mtime_ns, open(path, "rb").read()
            if warnings:
                with self.assertLogs("prompt_manager.config", level="WARNING") as logs:
                    PromptManagerConfig.load_from_file(path)
                self.assertEqual(len(logs.records), warnings, logs.output)
                self.logs = [record.getMessage() for record in logs.records]
            else:
                PromptManagerConfig.load_from_file(path)
            # Nothing is written at load.
            self.assertEqual(
                (os.stat(path).st_mtime_ns, open(path, "rb").read()), before
            )

    def state(self):
        return [dict(e) for e in AutotagConfig.INSTRUCTIONS], dict(
            AutotagConfig.DEFAULT_INSTRUCTIONS
        )


class TestLoad(ConfigTestCase):
    def test_a_new_install_has_built_in_only_for_both_engines(self):
        self.load({})
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))
        self.assertEqual(
            AutotagConfig.default_instructions_text("gguf"), DEFAULT_TAG_TEMPLATE
        )
        self.assertEqual(
            AutotagConfig.all_instructions()[0],
            {
                "id": BUILTIN_INSTRUCTIONS_ID,
                "name": "Built-in",
                "text": DEFAULT_TAG_TEMPLATE,
            },
        )

    def test_saved_entries_and_per_engine_defaults_load(self):
        saved = [
            entry("0123456789ab", "Photo", "Photo tags."),
            entry("0123456789ac", "Anime", "Anime tags."),
        ]
        self.load(
            {
                "instructions": saved,
                "default_instructions": {"gguf": "0123456789ac", "hf": "0123456789ab"},
            }
        )
        self.assertEqual(
            self.state(), (saved, {"gguf": "0123456789ac", "hf": "0123456789ab"})
        )
        self.assertEqual(AutotagConfig.default_instructions_text("gguf"), "Anime tags.")
        self.assertEqual(AutotagConfig.default_instructions_text("hf"), "Photo tags.")
        with self.assertRaises(ValueError):
            AutotagConfig.default_instructions_text("wd14-vit")

    def test_a_dangling_default_falls_back_to_built_in_with_a_warning(self):
        saved = [entry("0123456789ab", "Photo")]
        self.load(
            {
                "instructions": saved,
                "default_instructions": {"gguf": "ffffffffffff", "hf": "0123456789ab"},
            },
            warnings=1,
        )
        self.assertEqual(
            self.state(), (saved, {"gguf": "builtin", "hf": "0123456789ab"})
        )
        self.assertEqual(
            self.logs,
            [
                "autotag.defaults.default_instructions.gguf names no saved "
                "instructions; using Built-in"
            ],
        )

    def test_a_damaged_list_falls_back_to_built_in_only_and_the_defaults_follow(self):
        self.load(
            {
                "instructions": [
                    entry("0123456789ab", "A"),
                    entry("0123456789ab", "B"),
                ],
                "default_instructions": {"gguf": "0123456789ab", "hf": "builtin"},
            },
            warnings=1,  # one warning; the defaults follow the list silently
        )
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))
        self.assertEqual(
            self.logs[0],
            "autotag.defaults.instructions entry 2 repeats the id of entry 1; "
            "using Built-in only",
        )

    def test_damaged_defaults_fall_back_with_a_warning(self):
        self.load({"default_instructions": "builtin"}, warnings=1)
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))


class TestMigration(ConfigTestCase):
    CUSTOM = "My own tags, please."

    def test_first_upgrade_makes_custom_template_the_default_for_both(self):
        self.load({"template": self.CUSTOM})
        self.assertEqual(
            self.state(),
            (
                [entry(sha12(self.CUSTOM), "Custom template", self.CUSTOM)],
                {"gguf": sha12(self.CUSTOM), "hf": sha12(self.CUSTOM)},
            ),
        )
        # Deterministic: a second load gives the same ids.
        first = self.state()
        self.load({"template": self.CUSTOM})
        self.assertEqual(self.state(), first)

    def test_a_template_equal_to_built_in_creates_no_entry(self):
        self.load({"template": DEFAULT_TAG_TEMPLATE})
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))

    def test_an_invalid_template_is_ignored_with_todays_warning(self):
        self.load({"template": "   "}, warnings=1)
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))
        self.assertIn("autotag.defaults.template", self.logs[0])

    def test_a_stored_default_is_never_overwritten_by_the_first_upgrade(self):
        self.load(
            {
                "template": self.CUSTOM,
                "default_instructions": {"gguf": "builtin", "hf": "builtin"},
            },
            warnings=0,
        )
        self.assertEqual(
            self.state(),
            (
                [entry(sha12(self.CUSTOM), "Custom template", self.CUSTOM)],
                {"gguf": "builtin", "hf": "builtin"},
            ),
        )

    def test_the_mirror_of_a_saved_default_imports_nothing(self):
        saved = [entry(sha12(self.CUSTOM), "Custom template", self.CUSTOM)]
        both = {"gguf": sha12(self.CUSTOM), "hf": "builtin"}
        self.load(
            {
                "template": self.CUSTOM,
                "instructions": saved,
                "default_instructions": both,
            }
        )
        self.assertEqual(self.state(), (saved, both))

    def test_re_upgrade_imports_an_edit_made_on_an_older_build(self):
        saved = [entry(sha12(self.CUSTOM), "Custom template", "Edited since.")]
        both = {"gguf": sha12(self.CUSTOM), "hf": "builtin"}
        with self.assertLogs("prompt_manager.config", level="INFO") as logs:
            self.load(
                {
                    "template": self.CUSTOM,
                    "instructions": saved,
                    "default_instructions": both,
                }
            )
        instructions, defaults = self.state()
        # The text's own hash is taken by the edited entry: the next free one.
        imported = hashlib.sha256((self.CUSTOM + "\n2").encode()).hexdigest()[:12]
        self.assertEqual(
            instructions, saved + [entry(imported, "Custom template (2)", self.CUSTOM)]
        )
        self.assertEqual(defaults, both)
        self.assertIn(
            "Imported the tagging template edited on an older version as "
            "'Custom template (2)'",
            "\n".join(logs.output),
        )

    def test_re_upgrade_into_a_full_list_imports_nothing_and_warns(self):
        saved = [entry(f"{i:012x}", f"n{i}") for i in range(MAX_SAVED_INSTRUCTIONS)]
        self.load({"template": self.CUSTOM, "instructions": saved}, warnings=1)
        self.assertEqual(self.state()[0], saved)
        self.assertIn("full", self.logs[0])


class TestGetConfig(ConfigTestCase):
    def test_get_config_writes_the_lists_and_the_gguf_mirror(self):
        AutotagConfig.INSTRUCTIONS = (entry("0123456789ab", "Photo", "Photo tags."),)
        AutotagConfig.DEFAULT_INSTRUCTIONS = {"gguf": "0123456789ab", "hf": "builtin"}
        defaults = AutotagConfig.get_config()["defaults"]
        self.assertEqual(
            defaults["instructions"], [entry("0123456789ab", "Photo", "Photo tags.")]
        )
        self.assertEqual(
            defaults["default_instructions"], {"gguf": "0123456789ab", "hf": "builtin"}
        )
        self.assertEqual(defaults["template"], "Photo tags.")
        AutotagConfig.DEFAULT_INSTRUCTIONS = {"gguf": "builtin", "hf": "0123456789ab"}
        self.assertNotIn("template", AutotagConfig.get_config()["defaults"])


class TestDamagedValues(ConfigTestCase):
    def test_a_non_text_default_falls_back_with_a_warning(self):
        self.load({"default_instructions": {"gguf": [], "hf": "builtin"}}, warnings=1)
        self.assertEqual(self.state(), ([], {"gguf": "builtin", "hf": "builtin"}))

    def test_a_damaged_list_warns_once_and_migrates_the_template_afresh(self):
        custom = "My own tags, please."
        self.load(
            {
                "template": custom,
                "instructions": "broken",
                "default_instructions": {"gguf": "0123456789ab", "hf": "builtin"},
            },
            warnings=1,
        )
        self.assertEqual(
            self.state(),
            (
                [entry(sha12(custom), "Custom template", custom)],
                {"gguf": sha12(custom), "hf": sha12(custom)},
            ),
        )


if __name__ == "__main__":
    unittest.main()
