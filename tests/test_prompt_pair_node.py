"""Paired node encoding preserves recipe storage and output roles."""

import logging
import os
import tempfile
import unittest
from unittest.mock import Mock

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from prompt_manager_pair import PromptManagerPair
from utils.usage_tracking import PendingFirstUse


def pair_graph(text="cat", negative="blur", positive_slot=0, negative_slot=1):
    return {
        "1": {
            "class_type": "PromptManagerPair",
            "inputs": {"text": text, "negative_text": negative},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["9", 0],
                "positive": ["1", positive_slot],
                "negative": ["1", negative_slot],
            },
        },
    }


class TestPromptPairNode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.db = PromptDatabase(os.path.join(self.tmp.name, "pairs.db"))
        self.addCleanup(self.db.close_all)
        self.node = PromptManagerPair.__new__(PromptManagerPair)
        self.node.db = self.db
        self.node.logger = logging.getLogger("test.pair_node")
        self.node.prompt_tracker = Mock()
        self.node.comfyui_integration = Mock()
        self.node._inject_lora_trigger_words = Mock(
            side_effect=lambda text: text + " LORA"
        )
        self.node.pending_first_use = PendingFirstUse()
        self.node.pending_pair_first_use = PendingFirstUse()
        self.clip = Mock()
        self.clip.encode_from_tokens_scheduled.side_effect = lambda tokens: (
            "conditioning",
            tokens,
        )
        self.clip.tokenize.side_effect = lambda text: text

    def test_schema_has_positive_composition_and_four_ordered_outputs(self):
        schema = PromptManagerPair.INPUT_TYPES()
        self.assertEqual(list(schema["required"]), ["text", "negative_text", "clip"])
        self.assertTrue(schema["required"]["negative_text"][1]["multiline"])
        self.assertEqual(schema["required"]["negative_text"][1]["default"], "")
        for name in ("prepend_text", "append_text"):
            self.assertTrue(schema["optional"][name][1]["forceInput"])
        self.assertEqual(
            PromptManagerPair.RETURN_TYPES,
            ("CONDITIONING", "CONDITIONING", "STRING", "STRING"),
        )
        self.assertNotEqual(
            PromptManagerPair.IS_CHANGED(None, "cat", negative_text="blur"),
            PromptManagerPair.IS_CHANGED(None, "cat", negative_text="watermark"),
        )

    def test_encodes_each_side_once_positive_composition_and_lora_only(self):
        graph = pair_graph()
        result = self.node.encode_prompt(
            self.clip,
            "cat",
            negative_text=" blur ",
            prepend_text=" trigger ",
            append_text=" detail ",
            category="Animals",
            tags="studio",
            prompt=graph,
            unique_id="1",
        )
        self.assertEqual(
            result,
            (
                ("conditioning", "trigger cat detail LORA"),
                ("conditioning", " blur "),
                "trigger cat detail LORA",
                " blur ",
            ),
        )
        self.node._inject_lora_trigger_words.assert_called_once_with(
            "trigger cat detail"
        )
        self.assertEqual(self.clip.tokenize.call_count, 2)
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(
            (
                pair["positive_text"],
                pair["negative_text"],
                pair["category"],
                pair["tags"],
            ),
            ("cat", "blur", "Animals", ["studio"]),
        )
        self.assertIsNone(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["category"]
        )
        tracked = self.node.prompt_tracker.set_current_prompt.call_args.kwargs
        self.assertEqual(tracked["additional_data"]["pair_id"], pair["id"])
        self.assertEqual(tracked["additional_data"]["negative_text"], " blur ")
        self.assertEqual(tracked["additional_data"]["positive_storage_text"], "cat")

    def test_empty_negative_encodes_empty_text_and_stores_null_component(self):
        result = self.node.encode_prompt(self.clip, "cat", negative_text="")
        self.assertEqual(result[3], "")
        self.assertIsNone(self.db.list_prompt_pairs()["pairs"][0]["negative_prompt_id"])

    def test_invalid_clip_cannot_save_or_count(self):
        with self.assertRaises(RuntimeError):
            self.node.encode_prompt(None, "cat", negative_text="blur")
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        self.node.prompt_tracker.set_current_prompt.assert_not_called()

    def test_persistence_failure_does_not_block_conditioning(self):
        self.node.db = Mock()
        self.node.db.save_prompt_pair.side_effect = RuntimeError("unavailable")
        result = self.node.encode_prompt(self.clip, "cat", negative_text="blur")
        self.assertEqual(result[2:], ("cat LORA", "blur"))
        self.node.prompt_tracker.set_current_prompt.assert_not_called()

    def test_negative_output_used_as_positive_does_not_claim_positive_usage(self):
        self.node.encode_prompt(
            self.clip,
            "cat",
            negative_text="blur",
            prompt=pair_graph(positive_slot=1, negative_slot=0),
            unique_id="1",
        )
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(pair["run_count"], 0)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 0
        )
        self.node.prompt_tracker.set_current_prompt.assert_not_called()

    def test_invalid_text_does_not_write_and_integration_failures_do_not_block(self):
        with self.assertRaises(ValueError):
            self.node.encode_prompt(self.clip, [], negative_text="blur")
        self.node.comfyui_integration.register_prompt.side_effect = RuntimeError(
            "metadata unavailable"
        )
        result = self.node.encode_prompt(self.clip, "cat", negative_text="blur")
        self.assertEqual(result[2:], ("cat LORA", "blur"))

    def test_failed_negative_encoding_cannot_publish_positive_runtime_proof(self):
        from contextlib import ExitStack
        import sys
        import types
        from utils.execution_context_capture import ContextCapture
        from utils.execution_context_budget import ContextBudget
        from utils.execution_history_events import RunStart
        from utils.execution_history_writer import HistoryWriterStatus
        from unittest.mock import patch

        identity = "11111111-1111-4111-8111-111111111111"
        state = HistoryWriterStatus(identity, True, 0, 0, True, 1)
        budget = ContextBudget()
        manager = ContextCapture(1, lambda: state, budget)
        self.addCleanup(manager.revoke_all)
        cap = manager.begin(
            RunStart(identity, identity, 1, identity, identity, 0),
            1,
            pair_graph(),
            None,
        )
        host = types.ModuleType("comfy_execution.utils")
        host.get_executing_context = lambda: types.SimpleNamespace(
            prompt_id=identity, node_id="1", list_index=0
        )
        self.clip.encode_from_tokens_scheduled.side_effect = [
            ("conditioning", "cat LORA"),
            RuntimeError("negative failed"),
        ]
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(sys.modules, {"comfy_execution.utils": host})
            )
            with self.assertRaisesRegex(RuntimeError, "negative failed"):
                self.node.encode_prompt(self.clip, "cat", negative_text="blur")
        sealed = manager.seal(cap)
        if sealed.lease:
            self.addCleanup(sealed.lease.release)
        self.assertIsNone(sealed.bundle.contexts[0].positive.effective.value)
        self.assertIsNone(sealed.bundle.contexts[0].negative.effective.value)
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
