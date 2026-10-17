"""Embedded actual encoding evidence is used only for its traced paired node."""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from database.operations import PromptDatabase
from prompt_manager_pair import PromptManagerPair
from utils.metadata_extractor import ComfyUIMetadataExtractor
from utils.prompt_pair_resolver import resolve_prompt_pair

KEY = "comfyassets_prompt_pairs"


def graph(positive="cat", negative="blur"):
    return {
        "1": {
            "class_type": "PromptManagerPair",
            "inputs": {"text": positive, "negative_text": negative},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["1", 1]},
        },
    }


class TestPairExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PromptDatabase(str(Path(self.tmp.name) / "test.db"))
        self.addCleanup(self.db.close_all)
        self.node = PromptManagerPair.__new__(PromptManagerPair)
        self.node.db = self.db
        self.node.logger = logging.getLogger("test.execution")
        self.node.prompt_tracker = Mock()
        self.node.comfyui_integration = Mock()
        self.node._inject_lora_trigger_words = lambda text: text + " LORA"
        self.extra = {"workflow": {"unchanged": True}, "sibling": {"keep": True}}

    def execute(self, text="cat", negative="blur", **kwargs):
        return self.node.encode_prompt(
            Mock(),
            text,
            negative_text=negative,
            unique_id="1",
            extra_pnginfo=self.extra,
            **kwargs,
        )

    def resolve(self, source=None, extra=None):
        return resolve_prompt_pair(
            {"prompt": source or graph(), **(self.extra if extra is None else extra)}
        )

    def test_schema_requests_extra_pnginfo_and_actual_positive_lora_is_preserved(self):
        self.assertEqual(
            PromptManagerPair.INPUT_TYPES()["hidden"]["extra_pnginfo"], "EXTRA_PNGINFO"
        )
        self.execute(prepend_text="trigger")
        source = graph()
        source["1"]["inputs"]["prepend_text"] = "trigger"
        result = self.resolve(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["positive"]["text"], "trigger cat LORA")
        self.assertEqual(result["positive"]["storage_text"], "cat")
        self.assertEqual(result["positive"]["provenance"]["text_source"], "execution")
        self.assertEqual(result["negative"]["text"], "blur")
        self.assertEqual(self.extra["workflow"], {"unchanged": True})
        self.assertEqual(self.extra["sibling"], {"keep": True})

    def test_one_unknown_linked_execution_is_recoverable_without_fifo_guess(self):
        source = graph(positive=["2", 0])
        source["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.execute(text="actual batch cat")
        result = self.resolve(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["positive"]["storage_text"], "actual batch cat")
        self.assertEqual(result["positive"]["text"], "actual batch cat LORA")

    def test_distinct_batch_records_are_unresolved_instead_of_using_last_execution(
        self,
    ):
        source = graph(positive=["2", 0])
        source["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.execute(text="first")
        self.execute(text="second")
        result = self.resolve(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertIn("multiple", result["reason"].lower())

    def test_empty_negative_runtime_record_remains_explicit(self):
        self.execute(negative="")
        result = self.resolve(graph(negative=""))
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["negative"]["text"], "")

    def test_cached_graph_without_execution_evidence_is_explicitly_inferred(self):
        result = self.resolve(extra={})
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(
            result["positive"]["provenance"]["text_source"], "graph-inferred"
        )

    def test_stale_or_malformed_evidence_is_not_accepted(self):
        self.execute(text="stale")
        self.assertEqual(self.resolve()["state"], "unresolved")
        for value in (
            "not json",
            [],
            {"version": 900, "nodes": {}},
            {"version": 1, "nodes": {"1": {"records": [{"bad": True}]}}},
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.resolve(extra={KEY: value})["state"], "unresolved"
                )

    def test_unrelated_node_evidence_does_not_override_plain_encoders(self):
        source = graph()
        source["1"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "plain positive"},
        }
        source["2"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "plain negative"},
        }
        source["5"]["inputs"]["negative"] = ["2", 0]
        result = self.resolve(source, extra={KEY: "broken"})
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["positive"]["text"], "plain positive")

    def test_png_extractor_preserves_namespace_for_live_and_raw_scan_readers(self):
        self.execute()
        path = Path(self.tmp.name) / "image.png"
        info = PngInfo()
        info.add_text("prompt", json.dumps(graph()))
        info.add_text(KEY, json.dumps(self.extra[KEY]))
        Image.new("RGB", (2, 2)).save(path, pnginfo=info)
        parsed = ComfyUIMetadataExtractor().extract_metadata(str(path))
        self.assertEqual(resolve_prompt_pair(parsed)["positive"]["text"], "cat LORA")
        with Image.open(path) as image:
            raw = dict(image.text)
        raw["prompt"] = json.loads(raw["prompt"])
        self.assertEqual(resolve_prompt_pair(raw)["positive"]["text"], "cat LORA")

    def test_recording_limits_fail_closed_and_repeat_identical_values_are_safe(self):
        from utils.pair_execution import MAX_EXECUTION_RECORDS

        self.execute()
        self.execute()
        self.assertEqual(self.resolve()["state"], "resolved")
        for index in range(MAX_EXECUTION_RECORDS + 1):
            self.execute(text=str(index))
        self.assertEqual(self.resolve()["state"], "unresolved")

    def test_live_metadata_lookup_uses_unique_execution_for_unknown_source(self):
        from utils.image_monitor import ImageGenerationHandler

        source = graph(positive=["2", 0])
        source["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.execute(text="actual")
        handler = ImageGenerationHandler.__new__(ImageGenerationHandler)
        handler.db_manager = self.db
        handler.logger = logging.getLogger("test.execution")
        state, context = handler._find_prompt_from_metadata(
            {"prompt": source, **self.extra}
        )
        self.assertEqual(state, "linked")
        expected = self.db.list_prompt_pairs()["pairs"][0]["positive_prompt_id"]
        self.assertEqual(context["id"], expected)

    def test_submission_clears_old_execution_evidence_without_changing_workflow(self):
        from utils.usage_tracking import handle_queued_prompt, QueuedRuns

        self.execute()
        request = {"prompt": graph(), "extra_data": {"extra_pnginfo": self.extra}}
        handle_queued_prompt(request, QueuedRuns(), pair_queued=QueuedRuns())
        self.assertNotIn(KEY, self.extra)
        self.assertEqual(self.extra["workflow"], {"unchanged": True})
        self.assertEqual(
            self.resolve()["positive"]["provenance"]["text_source"], "graph-inferred"
        )

    def test_known_invalid_output_slot_or_cycles_cannot_be_overridden(self):
        self.execute()
        source = graph(positive=["2", 4])
        source["2"] = {"class_type": "PrimitiveString", "inputs": {"value": "cat"}}
        self.assertEqual(self.resolve(source)["state"], "unresolved")
        source["2"] = {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["2", 0], "string_b": "cat"},
        }
        source["1"]["inputs"]["text"] = ["2", 0]
        self.assertEqual(self.resolve(source)["state"], "unresolved")

    def test_live_image_association_keeps_actual_encoding_and_repeated_scan_is_stable(
        self,
    ):
        from utils.image_monitor import ImageGenerationHandler
        from database.pair_ingestion import ingest_resolved_pair

        self.execute()
        metadata = {"prompt": graph(), **self.extra}
        handler = ImageGenerationHandler.__new__(ImageGenerationHandler)
        handler.db_manager = self.db
        handler.logger = logging.getLogger("test.execution")
        _, context = handler._find_prompt_from_metadata(metadata)
        path = str(Path(self.tmp.name) / "actual.png")
        handler.link_image_to_prompt(path, context, metadata)
        pair_context = self.db.get_image_prompt_info(path)["pair_context"]
        self.assertEqual(pair_context["positive_text"], "cat LORA")
        repeated = ingest_resolved_pair(
            self.db, path, metadata, resolve_prompt_pair(metadata)
        )
        self.assertEqual(repeated["status"], "unchanged")

    def test_unknown_batch_with_multiple_executions_is_not_exactly_linked(self):
        from utils.image_monitor import ImageGenerationHandler

        source = graph(positive=["2", 0])
        source["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.execute(text="first")
        self.execute(text="second")
        handler = ImageGenerationHandler.__new__(ImageGenerationHandler)
        handler.db_manager = self.db
        handler.logger = logging.getLogger("test.execution")
        state, context = handler._find_prompt_from_metadata(
            {"prompt": source, **self.extra}
        )
        self.assertEqual(state, "unknown")
        self.assertIsNone(context)

    def test_execution_evidence_bounds_and_malformed_structures_fail_closed(self):
        from utils import pair_execution as evidence
        from unittest.mock import patch

        kwargs = dict(
            text="cat",
            negative_text="blur",
            positive_text="cat LORA",
            negative_effective_text="blur",
        )
        evidence.record_pair_execution(None, "1", **kwargs)
        envelope = {}
        evidence.record_pair_execution(envelope, None, **kwargs)
        self.assertEqual(envelope, {})
        evidence.record_pair_execution(envelope, "1", **kwargs)
        self.assertIsNone(evidence.read_pair_execution(envelope, "untraced"))
        malformed = (
            {"version": 1, "nodes": []},
            {"version": 1, "truncated": True, "nodes": {}},
            {"version": 1, "nodes": {"1": []}},
            {"version": 1, "nodes": {"1": {"records": []}}},
            {"version": 1, "nodes": {"1": {"records": "bad"}}},
            {
                "version": 1,
                "nodes": {
                    "1": {
                        "records": [
                            {
                                **kwargs,
                                "prepend_text": "",
                                "append_text": "",
                                "negative_effective_text": "changed",
                            }
                        ]
                    }
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(evidence.ExecutionEvidenceError):
                    evidence.read_pair_execution({KEY: value}, "1")
        oversized = {}
        evidence.record_pair_execution(
            oversized,
            "1",
            **{**kwargs, "text": "x" * (evidence.MAX_EXECUTION_TEXT + 1)},
        )
        with self.assertRaises(evidence.ExecutionEvidenceError):
            evidence.read_pair_execution(oversized, "1")
        with patch.object(evidence, "MAX_EXECUTION_JSON", 10):
            with self.assertRaises(evidence.ExecutionEvidenceError):
                evidence.read_pair_execution({KEY: json.dumps(envelope[KEY])}, "1")
            limited = {}
            evidence.record_pair_execution(limited, "1", **kwargs)
            with self.assertRaises(evidence.ExecutionEvidenceError):
                evidence.read_pair_execution(limited, "1")
        with patch.object(evidence, "MAX_EXECUTION_NODES", 1):
            evidence.record_pair_execution(envelope, "2", **kwargs)
            evidence.record_pair_execution(envelope, "3", **kwargs)
            with self.assertRaises(evidence.ExecutionEvidenceError):
                evidence.read_pair_execution(envelope, "1")

    def test_corrupted_existing_namespace_cannot_break_valid_encoding(self):
        from utils.pair_execution import (
            record_pair_execution,
            read_pair_execution,
            ExecutionEvidenceError,
        )

        kwargs = dict(
            text="cat",
            negative_text="blur",
            positive_text="cat LORA",
            negative_effective_text="blur",
        )
        for nodes in ([], {"1": {"records": "bad"}}, {"other": {"bad": object()}}):
            extra = {KEY: {"version": 1, "nodes": nodes}}
            record_pair_execution(extra, "1", **kwargs)
            with self.assertRaises(ExecutionEvidenceError):
                read_pair_execution(extra, "1")
