"""Saved instructions through POST /settings and the legacy fallbacks (#431).

Server-side pairing (a save carrying either key writes both, the missing one
from the running, migrated configuration), the downgrade mirror in the old
``template`` key, the refusals, GET /settings, and the legacy single route's
blank-prompt fallback per request engine. The "restart" is a reload of the
isolated config.json (``PROMPT_MANAGER_CONFIG_PATH`` in the temp tree).
"""

import hashlib

try:
    from test_settings_sources import SourcesTestCase
    from test_autotag_legacy import LegacyTestCase
except ImportError:
    from tests.test_settings_sources import SourcesTestCase
    from tests.test_autotag_legacy import LegacyTestCase

from py.config import AutotagConfig, PromptManagerConfig
from py.tagging_defaults import DEFAULT_TAG_TEMPLATE

ATTRS = ("MODEL_TYPE", "INSTRUCTIONS", "DEFAULT_INSTRUCTIONS", "_LEGACY_TEMPLATE")
SETTINGS = "/prompt_manager/settings"
CUSTOM = "My own tags, please."
CUSTOM_ID = hashlib.sha256(CUSTOM.encode()).hexdigest()[:12]


def entry(id_, name, text):
    return {"id": id_, "name": name, "text": text}


class InstructionsTestCase(SourcesTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        saved = {attr: getattr(AutotagConfig, attr) for attr in ATTRS}
        self.addCleanup(
            lambda: [setattr(AutotagConfig, a, v) for a, v in saved.items()]
        )

    def restart(self):
        PromptManagerConfig.load_from_file(self.config_path)

    async def save(self, payload, revision=True):
        if revision:
            payload = {"expected_revision": (await self._get())["revision"], **payload}
        return await self._post(SETTINGS, payload)

    def disk(self):
        return self._read_config()["autotag"]["defaults"]


class TestMigrationPersists(InstructionsTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._write_config(
            {"custom": {"keep": True}, "autotag": {"defaults": {"template": CUSTOM}}}
        )
        self.restart()

    async def test_get_shows_the_migrated_entry_and_writes_nothing(self):
        before = self._config_bytes()
        settings = (await self._get())["settings"]
        self.assertEqual(
            settings["autotag_instructions"],
            [entry(CUSTOM_ID, "Custom template", CUSTOM)],
        )
        self.assertEqual(
            settings["autotag_default_instructions"],
            {"gguf": CUSTOM_ID, "hf": CUSTOM_ID},
        )
        self.assertEqual(settings["autotag_template"], CUSTOM)
        self.assertEqual(settings["autotag_builtin_instructions"], DEFAULT_TAG_TEMPLATE)
        self.assertEqual(self._config_bytes(), before)

    async def test_a_save_of_only_the_hf_default_writes_the_migrated_list_too(self):
        status, data = await self.save(
            {"autotag_default_instructions": {"gguf": CUSTOM_ID, "hf": "builtin"}}
        )
        self.assertEqual(status, 200, data)
        disk = self.disk()
        self.assertEqual(
            disk["instructions"], [entry(CUSTOM_ID, "Custom template", CUSTOM)]
        )
        self.assertEqual(
            disk["default_instructions"], {"gguf": CUSTOM_ID, "hf": "builtin"}
        )
        self.assertEqual(disk["template"], CUSTOM)
        self.assertEqual(self._read_config()["custom"], {"keep": True})
        self.restart()
        self.assertEqual(
            (list(AutotagConfig.INSTRUCTIONS), AutotagConfig.DEFAULT_INSTRUCTIONS),
            (
                [entry(CUSTOM_ID, "Custom template", CUSTOM)],
                {"gguf": CUSTOM_ID, "hf": "builtin"},
            ),
        )

    async def test_gguf_built_in_deletes_the_mirror_and_keeps_custom_template(self):
        status, data = await self.save(
            {"autotag_default_instructions": {"gguf": "builtin", "hf": CUSTOM_ID}}
        )
        self.assertEqual(status, 200, data)
        self.assertNotIn("template", self.disk())
        self.restart()
        self.assertEqual(
            list(AutotagConfig.INSTRUCTIONS),
            [entry(CUSTOM_ID, "Custom template", CUSTOM)],
        )
        self.assertEqual(
            AutotagConfig.DEFAULT_INSTRUCTIONS, {"gguf": "builtin", "hf": CUSTOM_ID}
        )
        self.assertEqual(
            AutotagConfig.default_instructions_text("gguf"), DEFAULT_TAG_TEMPLATE
        )

    async def test_a_save_of_only_the_list_keeps_the_defaults_that_still_exist(self):
        photo = entry("0123456789ab", "Photo", "Photo tags.")
        status, data = await self.save(
            {
                "autotag_instructions": [
                    entry(CUSTOM_ID, "Custom template", CUSTOM),
                    photo,
                ]
            }
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            self.disk()["default_instructions"], {"gguf": CUSTOM_ID, "hf": CUSTOM_ID}
        )
        self.assertEqual(AutotagConfig.INSTRUCTIONS[1], photo)
        # A list that drops a default without moving it is refused, nothing written.
        before = self._config_bytes()
        status, data = await self.save({"autotag_instructions": [photo]})
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data["error"],
            "Default for GGUF JoyCaption must be one of the saved instructions.",
        )
        self.assertEqual(self._config_bytes(), before)


class TestRefusals(InstructionsTestCase):
    async def test_the_old_template_key_is_refused(self):
        before = self._config_bytes()
        status, data = await self.save({"autotag_template": "Tags only."})
        self.assertEqual(
            (status, data["error"]),
            (
                400,
                "LLM tag template was replaced by Saved instructions. Reload the page.",
            ),
        )
        self.assertEqual(self._config_bytes(), before)

    async def test_instruction_keys_without_a_revision_are_refused(self):
        for payload in (
            {"autotag_instructions": []},
            {"autotag_default_instructions": {"gguf": "builtin", "hf": "builtin"}},
        ):
            with self.subTest(payload=payload):
                status, data = await self.save(payload, revision=False)
                self.assertEqual(
                    (status, data["error"]), (400, "Reload the page and try again.")
                )

    async def test_entry_refusals_name_the_entry(self):
        status, data = await self.save(
            {
                "autotag_instructions": [
                    entry("0123456789ab", "Photo", "Photo tags."),
                    entry("0123456789ac", "photo", "x"),
                ]
            }
        )
        self.assertEqual(
            (status, data["error"]),
            (400, "Saved instructions: entry 2 name “photo” is already used."),
        )

    async def test_a_new_entry_can_become_a_default_in_one_save(self):
        photo = entry("0123456789ab", "Photo", "Photo tags.")
        status, data = await self.save(
            {
                "autotag_instructions": [photo],
                "autotag_default_instructions": {
                    "gguf": "0123456789ab",
                    "hf": "builtin",
                },
            }
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(self.disk()["template"], "Photo tags.")
        settings = (await self._get())["settings"]
        self.assertEqual(settings["autotag_template"], "Photo tags.")
        self.assertEqual(AutotagConfig.default_instructions_text("gguf"), "Photo tags.")
        self.assertEqual(AutotagConfig._LEGACY_TEMPLATE, "Photo tags.")


class TestLegacySingleFallback(LegacyTestCase):
    async def test_a_blank_prompt_uses_the_request_engines_default(self):
        saved = {attr: getattr(AutotagConfig, attr) for attr in ATTRS}
        self.addCleanup(
            lambda: [setattr(AutotagConfig, a, v) for a, v in saved.items()]
        )
        AutotagConfig.INSTRUCTIONS = (entry("0123456789ab", "Photo", "Photo tags."),)
        AutotagConfig.DEFAULT_INSTRUCTIONS = {"gguf": "builtin", "hf": "0123456789ab"}
        prompt = self.prompt("a prompt")
        image = self.link(prompt, self.image("a.png", (1, 2, 3), sees=["bird"]))
        for model_type, expected in (
            ("hf", "Photo tags."),
            ("gguf", DEFAULT_TAG_TEMPLATE),
        ):
            with self.subTest(model_type=model_type):
                status, data = await self.single(
                    {"image_id": image, "model_type": model_type, "prompt": " "}
                )
                self.assertEqual(status, 200, data)
                self.assertEqual(self.tagger.calls[-1]["prompt"], expected)


if __name__ == "__main__":
    import unittest

    unittest.main()
