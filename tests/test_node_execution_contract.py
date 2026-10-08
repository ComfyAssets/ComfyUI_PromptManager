"""
Tests for ComfyUI node execution contract.

Ensures all nodes are properly configured for ComfyUI's execution engine:
- OUTPUT_NODE = True so nodes are always included in the execution graph
- IS_CHANGED returns correct values for cache invalidation

Regression tests for: https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/120
"""

import itertools
import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_manager import PromptManager
from prompt_manager_text import PromptManagerText
from prompt_search_list import PromptSearchList

ALL_NODE_CLASSES = [PromptManager, PromptManagerText, PromptSearchList]


class TestOutputNode(unittest.TestCase):
    """OUTPUT_NODE = True is required so ComfyUI always includes these nodes
    in the execution graph. Without it, nodes with side effects (database
    writes, execution tracking) can be silently skipped when downstream
    nodes are cached."""

    def test_prompt_manager_is_output_node(self):
        self.assertIs(PromptManager.OUTPUT_NODE, True)

    def test_prompt_manager_text_is_output_node(self):
        self.assertIs(PromptManagerText.OUTPUT_NODE, True)

    def test_prompt_search_list_is_output_node(self):
        self.assertIs(PromptSearchList.OUTPUT_NODE, True)


class TestIsChangedPromptManager(unittest.TestCase):
    """IS_CHANGED for PromptManager should return a deterministic hash
    based on text inputs, so the node re-runs only when inputs change."""

    def test_same_inputs_return_same_hash(self):
        result1 = PromptManager.IS_CHANGED(
            clip=None, text="hello", prepend_text="pre", append_text="post"
        )
        result2 = PromptManager.IS_CHANGED(
            clip=None, text="hello", prepend_text="pre", append_text="post"
        )
        self.assertEqual(result1, result2)

    def test_different_text_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello")
        result2 = PromptManager.IS_CHANGED(clip=None, text="world")
        self.assertNotEqual(result1, result2)

    def test_different_prepend_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", prepend_text="a")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", prepend_text="b")
        self.assertNotEqual(result1, result2)

    def test_different_append_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", append_text="a")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", append_text="b")
        self.assertNotEqual(result1, result2)

    def test_returns_string(self):
        result = PromptManager.IS_CHANGED(clip=None, text="hello")
        self.assertIsInstance(result, str)

    def test_different_tags_return_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", tags="a, b")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", tags="a, c")
        self.assertNotEqual(result1, result2)

    def test_different_category_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", category="art")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", category="photo")
        self.assertNotEqual(result1, result2)


class TestIsChangedPromptManagerText(unittest.TestCase):
    """IS_CHANGED for PromptManagerText should behave identically to
    PromptManager — deterministic hash based on text inputs."""

    def test_same_inputs_return_same_hash(self):
        result1 = PromptManagerText.IS_CHANGED(
            text="hello", prepend_text="pre", append_text="post"
        )
        result2 = PromptManagerText.IS_CHANGED(
            text="hello", prepend_text="pre", append_text="post"
        )
        self.assertEqual(result1, result2)

    def test_different_text_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello")
        result2 = PromptManagerText.IS_CHANGED(text="world")
        self.assertNotEqual(result1, result2)

    def test_different_prepend_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello", prepend_text="a")
        result2 = PromptManagerText.IS_CHANGED(text="hello", prepend_text="b")
        self.assertNotEqual(result1, result2)

    def test_different_append_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello", append_text="a")
        result2 = PromptManagerText.IS_CHANGED(text="hello", append_text="b")
        self.assertNotEqual(result1, result2)

    def test_different_tags_or_category_return_different_hash(self):
        base = PromptManagerText.IS_CHANGED(text="hello", category="x", tags="a")
        self.assertNotEqual(
            base, PromptManagerText.IS_CHANGED(text="hello", category="x", tags="b")
        )
        self.assertNotEqual(
            base, PromptManagerText.IS_CHANGED(text="hello", category="y", tags="a")
        )

    def test_returns_string(self):
        result = PromptManagerText.IS_CHANGED(text="hello")
        self.assertIsInstance(result, str)


class TestIsChangedPromptSearchList(unittest.TestCase):
    """IS_CHANGED for PromptSearchList should always return a unique value
    so the node re-runs every time (database contents may have changed)."""

    def test_returns_different_value_on_successive_calls(self):
        # Mock the clock instead of sleeping: the Windows clock ticks every
        # 15.6 ms, so two real time.time() reads 10 ms apart can be equal.
        ticks = itertools.count(1_700_000_000.0, 1.0)
        with mock.patch("time.time", side_effect=lambda: next(ticks)):
            result1 = PromptSearchList.IS_CHANGED()
            result2 = PromptSearchList.IS_CHANGED()
        self.assertNotEqual(result1, result2)

    def test_returns_numeric(self):
        result = PromptSearchList.IS_CHANGED()
        self.assertIsInstance(result, float)


import logging  # noqa: E402
import tempfile  # noqa: E402
import types  # noqa: E402

from database.operations import PromptDatabase  # noqa: E402
from prompt_manager_base import PromptManagerBase  # noqa: E402
import prompt_manager_base  # noqa: E402


class FakeClip:
    """The two CLIP calls the node makes, recorded."""

    def __init__(self):
        self.tokenized = []

    def tokenize(self, text):
        self.tokenized.append(text)
        return ("tokens", text)

    def encode_from_tokens_scheduled(self, tokens):
        return [("cond", tokens[1])]


class NodeTestCase(unittest.TestCase):
    """A node with a real temporary database and no monitor or tracker."""

    node_cls = PromptManagerBase

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = PromptDatabase(self.db_path)
        self.node = self.node_cls.__new__(self.node_cls)
        self.node.db = self.db
        self.node.logger = logging.getLogger("test.node")
        self.node.prompt_tracker = mock.Mock()
        self.node.image_monitor = mock.Mock()
        self.node.comfyui_integration = mock.Mock()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.unlink(self.db_path + suffix)


class TestBaseSaveAndSearch(NodeTestCase):
    def test_init_wires_database_tracker_monitor_and_integration(self):
        with (
            mock.patch.object(prompt_manager_base, "PromptDatabase") as db_cls,
            mock.patch.object(prompt_manager_base, "get_prompt_tracker") as tracker,
            mock.patch.object(prompt_manager_base, "get_image_monitor") as monitor,
            mock.patch.object(
                prompt_manager_base, "get_comfyui_integration"
            ) as integration,
        ):
            node = PromptManagerBase()
        self.assertIs(node.db, db_cls.return_value)
        tracker.assert_called_once_with(db_cls.return_value)
        monitor.assert_called_once_with(db_cls.return_value, tracker.return_value)
        self.assertIs(node.comfyui_integration, integration.return_value)
        monitor.return_value.start_monitoring.assert_called_once_with()

    def test_saving_an_existing_prompt_updates_its_metadata(self):
        first = self.node._save_prompt_to_database("a cat")
        second = self.node._save_prompt_to_database(
            "a cat", category="animals", tags=["cute"]
        )
        self.assertEqual(first, second)
        saved = self.db.get_prompt_by_id(first)
        self.assertEqual(saved["category"], "animals")
        self.assertIn("cute", saved["tags"])
        self.assertEqual(saved["run_count"], 0)

    def test_count_run_on_an_existing_prompt(self):
        prompt_id = self.node._save_prompt_to_database("a cat")
        self.node._save_prompt_to_database("a cat", count_run=True)
        self.assertEqual(self.db.get_prompt_by_id(prompt_id)["run_count"], 1)

    def test_database_errors_are_logged_and_return_none(self):
        self.node.db = mock.Mock()
        self.node.db.get_prompt_by_hash.side_effect = RuntimeError("locked")
        with self.assertLogs("test.node", level="ERROR"):
            self.assertIsNone(self.node._save_prompt_to_database("a cat"))

    def test_record_use_failure_is_a_warning(self):
        self.node.db = mock.Mock()
        self.node.db.record_prompt_use.side_effect = RuntimeError("locked")
        with self.assertLogs("test.node", level="WARNING"):
            self.node._record_use(1)

    def test_parse_tags(self):
        self.assertIsNone(self.node._parse_tags(""))
        self.assertIsNone(self.node._parse_tags(" , ,"))
        self.assertEqual(self.node._parse_tags(" a, b ,,c"), ["a", "b", "c"])

    def test_search_prompts_and_api_wrappers(self):
        self.node._save_prompt_to_database("a cat on a mat")
        self.assertEqual(self.node._search_prompts(""), [])
        self.assertEqual(self.node._search_prompts("   "), [])
        self.assertEqual(len(self.node.search_prompts_api("cat")), 1)
        self.assertEqual(len(self.node.get_recent_prompts_api(limit=5)["prompts"]), 1)

    def test_search_and_recent_errors_return_empty_lists(self):
        self.node.db = mock.Mock()
        self.node.db.search_prompts.side_effect = RuntimeError("locked")
        self.node.db.get_recent_prompts.side_effect = RuntimeError("locked")
        with self.assertLogs("test.node", level="ERROR"):
            self.assertEqual(self.node._search_prompts("cat"), [])
        with self.assertLogs("test.node", level="ERROR"):
            self.assertEqual(self.node.get_recent_prompts_api(), [])

    def test_gallery_system_start_failure_is_logged(self):
        self.node.image_monitor.start_monitoring.side_effect = OSError("no inotify")
        with self.assertLogs("test.node", level="ERROR"):
            self.node._start_gallery_system()
        self.node.image_monitor.get_status.return_value = {"running": False}
        self.node.prompt_tracker.get_status.return_value = {"active_prompts_count": 0}
        self.assertEqual(
            self.node.get_gallery_status(),
            {
                "image_monitor": {"running": False},
                "prompt_tracker": {"active_prompts_count": 0},
            },
        )
        self.node.cleanup_gallery_system()
        self.node.image_monitor.stop_monitoring.assert_not_called()


def fake_integration_config(enabled, trigger_words=True, path="/loras"):
    module = types.ModuleType("py.config")
    module.IntegrationConfig = types.SimpleNamespace(
        LORA_MANAGER_ENABLED=enabled,
        LORA_TRIGGER_WORDS_ENABLED=trigger_words,
        LORA_MANAGER_PATH=path,
    )
    return module


class TestLoraTriggerWords(NodeTestCase):
    def test_disabled_integration_returns_text_unchanged(self):
        with mock.patch.dict(
            sys.modules, {"py.config": fake_integration_config(False)}
        ):
            self.assertEqual(self.node._inject_lora_trigger_words("a cat"), "a cat")
        with mock.patch.dict(
            sys.modules, {"py.config": fake_integration_config(True, False)}
        ):
            self.assertEqual(self.node._inject_lora_trigger_words("a cat"), "a cat")

    def test_missing_config_or_lora_utils_returns_text_unchanged(self):
        with mock.patch.dict(sys.modules, {"py.config": None}):
            self.assertEqual(self.node._inject_lora_trigger_words("a cat"), "a cat")
        with mock.patch.dict(
            sys.modules,
            {"py.config": fake_integration_config(True), "py.lora_utils": None},
        ):
            self.assertEqual(self.node._inject_lora_trigger_words("a cat"), "a cat")

    def test_enabled_integration_loads_the_cache_and_injects(self):
        cache = mock.Mock(is_loaded=False)
        lora_utils = types.ModuleType("py.lora_utils")
        lora_utils.get_trigger_cache = lambda: cache
        lora_utils.inject_trigger_words = lambda text, c: (text + " trig", ["trig"])
        with mock.patch.dict(
            sys.modules,
            {"py.config": fake_integration_config(True), "py.lora_utils": lora_utils},
        ):
            self.assertEqual(
                self.node._inject_lora_trigger_words("a cat"), "a cat trig"
            )
        cache.load.assert_called_once_with("/loras")


class TestPromptManagerEncoding(NodeTestCase):
    node_cls = PromptManager

    def setUp(self):
        super().setUp()
        self.node._inject_lora_trigger_words = lambda text: text

    def test_missing_clip_is_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "clip input is invalid"):
            self.node.encode_prompt(None, "a cat")

    def test_encodes_combined_text_and_saves_the_main_text(self):
        clip = FakeClip()
        conditioning, final = self.node.encode_prompt(
            clip, "a cat", prepend_text=" best ", append_text="8k ", tags="x"
        )
        self.assertEqual(final, "best a cat 8k")
        self.assertEqual(clip.tokenized, ["best a cat 8k"])
        self.assertEqual(conditioning, [("cond", "best a cat 8k")])
        saved = self.db.get_recent_prompts(limit=1)["prompts"][0]
        self.assertEqual(saved["text"], "a cat")
        self.assertIn("prepend:best", saved["tags"])
        self.assertIn("append:8k", saved["tags"])
        self.node.comfyui_integration.register_prompt.assert_called_once()

    def test_tracking_failure_does_not_break_encoding(self):
        self.node._track_prompt_execution = mock.Mock(side_effect=RuntimeError("db"))
        with self.assertLogs("test.node", level="WARNING"):
            _, final = self.node.encode_prompt(FakeClip(), "a cat")
        self.assertEqual(final, "a cat")

    def test_empty_text_is_encoded_but_not_saved(self):
        _, final = self.node.encode_prompt(FakeClip(), "   ")
        self.assertEqual(final.strip(), "")
        self.assertEqual(self.db.get_recent_prompts(limit=1)["prompts"], [])


class TestPromptManagerTextProcessing(NodeTestCase):
    node_cls = PromptManagerText

    def setUp(self):
        super().setUp()
        self.node._inject_lora_trigger_words = lambda text: text

    def test_tracking_failure_does_not_break_processing(self):
        self.node._track_prompt_execution = mock.Mock(side_effect=RuntimeError("db"))
        with self.assertLogs("test.node", level="WARNING"):
            result = self.node.process_text("a castle", prepend_text="big")
        self.assertEqual(result[0], "big a castle")


class TestPromptSearchListSearch(NodeTestCase):
    node_cls = PromptSearchList

    def _save(self, text, **kwargs):
        from utils.hashing import generate_prompt_hash

        return self.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text), **kwargs
        )

    def test_init_opens_the_database(self):
        with mock.patch("prompt_search_list.PromptDatabase") as db_cls:
            node = PromptSearchList()
        self.assertIs(node.db, db_cls.return_value)

    def test_no_results_returns_a_single_empty_item(self):
        result = self.node.search(text="nothing here")
        self.assertEqual(result["result"], ([""], "No results found", 0))
        self.assertEqual(result["ui"], {"text": ["No results found"]})

    def test_filters_by_text_category_tags_and_rating(self):
        self._save("a red cat", category="animals", tags=["cute"])
        self._save("a blue car", category="vehicles", tags=["fast"])
        self.assertEqual(self.node.search(text="cat")["result"][0], ["a red cat"])
        self.assertEqual(
            self.node.search(category="vehicles")["result"][0], ["a blue car"]
        )
        self.assertEqual(self.node.search(tags=" cute , ")["result"][0], ["a red cat"])
        self.assertEqual(self.node.search(min_rating=5)["result"][2], 0)
        self.assertEqual(self.node.search(limit=1)["result"][2], 1)

    def test_multipart_and_lora_only_prompts_are_skipped(self):
        self._save("Clip_1: a cat Clip_2: a dog")
        self._save("<lora:style:0.8> <lora:other:1>")
        self._save("plain prompt")
        self.assertEqual(self.node.search()["result"][0], ["plain prompt"])
        result = self.node.search(skip_multipart=False)["result"]
        self.assertEqual(
            sorted(result[0]), ["Clip_1: a cat Clip_2: a dog", "plain prompt"]
        )

    def test_newlines_collapse_and_preview_is_numbered_and_truncated(self):
        self._save("line one\n   line two")
        self._save("x" * 200)
        result = self.node.search()
        prompts, preview, count = result["result"]
        self.assertEqual(count, 2)
        self.assertIn("line one line two", prompts)
        lines = preview.splitlines()
        self.assertTrue(lines[0].startswith("[1] "))
        self.assertTrue(any(line.endswith("...") and len(line) < 140 for line in lines))
        self.assertEqual(result["ui"], {"text": ["Found 2 prompts"]})

    def test_database_errors_are_returned_not_raised(self):
        self.node.db = mock.Mock()
        self.node.db.search_prompts.side_effect = RuntimeError("locked")
        result = self.node.search(text="cat")
        self.assertEqual(result["result"], ([""], "Error: locked", 0))
        self.assertEqual(result["ui"], {"text": ["Search error: locked"]})


if __name__ == "__main__":
    unittest.main()
