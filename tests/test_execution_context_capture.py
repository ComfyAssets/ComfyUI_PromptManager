"""Attempt fencing and bounded runtime evidence, without a running host."""

from contextvars import copy_context
from dataclasses import replace
import gc
import sys
import threading
import types
import unittest
import weakref
from unittest.mock import patch

from utils.execution_context_budget import ContextBudget
from utils.execution_context_graph import SourceRef
from utils.execution_contexts import PromptSnapshot, TextEvidence
from utils.execution_history_events import RunStart
from utils.execution_history_writer import HistoryWriterStatus

try:
    from utils import execution_context_capture as capture
except ImportError:
    capture = None

SESSION = "11111111-1111-4111-8111-111111111111"
EPOCH = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"


def graph():
    return {
        "p": {
            "class_type": "PromptManagerPair",
            "inputs": {"text": "raw", "negative_text": "negative"},
        },
        "s": {
            "class_type": "KSampler",
            "inputs": {"positive": ["p", 0], "negative": ["p", 1]},
        },
    }


def snapshot(raw=" raw ", effective="injected", prepend=" pre ", append=" post "):
    return PromptSnapshot(
        *(
            TextEvidence(v, "recorded", "runtime_return", None)
            for v in (raw, effective, prepend, append)
        )
    )


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(capture, "attempt capture API is missing")
        self.pool = ContextBudget()
        self.status = HistoryWriterStatus(EPOCH, True, 0, 0, True, 3)
        self.manager = capture.ContextCapture(10, lambda: self.status, self.pool)
        self.addCleanup(self.manager.revoke_all)
        self.start = RunStart(SESSION, SESSION, 1, JOB, EPOCH, 0)

    def begin(self, item=1, g=None):
        return self.manager.begin(
            replace(self.start, queue_item_id=item),
            3,
            graph() if g is None else g,
            None,
        )

    def seal(self, cap):
        result = self.manager.seal(cap)
        if result.lease:
            self.addCleanup(result.lease.release)
        return result

    def test_runtime_keeps_raw_affix_whitespace_and_injected_effective_text(self):
        cap = self.begin()
        self.manager.observe(cap, SourceRef("p", "PromptManagerPair", 0), 0, snapshot())
        result = self.seal(cap)
        side = result.bundle.contexts[0].positive
        self.assertEqual(
            [
                side.raw.value,
                side.effective.value,
                side.prepend.value,
                side.append.value,
            ],
            [" raw ", "injected", " pre ", " post "],
        )
        self.assertIsNone(result.bundle.contexts[0].negative.effective.value)
        self.assertEqual(result.state, "partial")
        self.assertLess(self.pool.charged_bytes, 1048576)

    def test_reused_uuid_late_async_and_same_epoch_generation_are_fenced(self):
        a = self.begin()
        inherited = copy_context()
        b = self.begin(2)
        inherited.run(
            self.manager.observe,
            a,
            SourceRef("p", "PromptManagerPair", 0),
            0,
            snapshot(),
        )
        self.assertIsNone(self.seal(a).bundle)
        self.status = replace(self.status, capture_generation=4)
        self.manager.observe(b, SourceRef("p", "PromptManagerPair", 0), 0, snapshot())
        self.assertIsNone(self.seal(b).bundle)
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_unbound_thread_and_forged_metadata_cannot_record(self):
        cap = self.begin()
        host = types.ModuleType("comfy_execution.utils")
        host.get_executing_context = lambda: types.SimpleNamespace(
            prompt_id=JOB, node_id="p", list_index=0
        )
        with patch.dict(sys.modules, {"comfy_execution.utils": host}):
            thread = threading.Thread(
                target=capture.record_runtime_context,
                args=("PromptManagerPair", "bad", "", "", "bad"),
                kwargs={"role": "positive"},
            )
            thread.start()
            thread.join(2)
            self.manager.observe(
                replace(cap, nonce="forged"),
                SourceRef("p", "PromptManagerPair", 0),
                0,
                snapshot(),
            )
            host.get_executing_context = lambda: types.SimpleNamespace(
                prompt_id="forged", node_id="p", list_index=0
            )
            capture.record_runtime_context(
                "PromptManagerPair", "bad", "", "", "bad", role="positive"
            )
        self.assertIsNone(self.seal(cap).bundle.contexts[0].positive.effective.value)

    def test_new_dequeue_revokes_unfinished_attempt(self):
        a = self.begin()
        b = self.begin(2)
        self.assertEqual(capture.active_capture(), (self.manager, b))
        self.manager.revoke(a)
        self.assertEqual(capture.active_capture(), (self.manager, b))
        self.manager.revoke_all()
        self.assertIsNone(capture.active_capture())
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_equal_distinct_invocations_are_ambiguous_and_replay_is_idempotent(self):
        for count, state in ((1, "partial"), (64, "ambiguous"), (65, "ambiguous")):
            cap = self.begin(count)
            for i in range(count):
                self.manager.observe(
                    cap, SourceRef("p", "PromptManagerPair", 0), i, snapshot()
                )
                self.manager.observe(
                    cap, SourceRef("p", "PromptManagerPair", 0), i, snapshot()
                )
            result = self.seal(cap)
            self.assertEqual(result.state, state)
            if count > 1:
                self.assertIsNone(result.bundle.contexts[0].positive.effective.value)

    def test_projection_never_retains_original_containers_and_budget_denial(self):
        class Envelope(dict):
            pass

        g = Envelope(graph())
        ref = weakref.ref(g)
        cap = self.begin(g=g)
        del g
        gc.collect()
        self.assertIsNone(ref())
        self.manager.revoke(cap)
        small = capture.ContextCapture(10, lambda: self.status, ContextBudget(1))
        cap = small.begin(self.start, 3, graph(), None)
        self.assertEqual(small.seal(cap).reason, "budget_exhausted")

    def test_crossed_roles_and_multiple_anchors_use_exact_slots(self):
        g = graph()
        g["t"] = {
            "class_type": "KSampler",
            "inputs": {"positive": ["p", 1], "negative": ["p", 0]},
        }
        cap = self.begin(g=g)
        self.manager.observe(
            cap, SourceRef("p", "PromptManagerPair", 0), 0, snapshot(effective="POS")
        )
        self.manager.observe(
            cap, SourceRef("p", "PromptManagerPair", 1), 0, snapshot(effective="NEG")
        )
        result = self.seal(cap)
        self.assertEqual(result.state, "complete")
        self.assertEqual(
            [
                (r.positive.effective.value, r.negative.effective.value)
                for r in result.bundle.contexts
            ],
            [("POS", "NEG"), ("NEG", "POS")],
        )

    def test_transformed_sources_cannot_replace_composed_text(self):
        g = graph()
        g["text"] = {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["p", 2], "string_b": " suffix"},
        }
        g["clip"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["text", 0]}}
        g["s"]["inputs"]["positive"] = ["clip", 0]
        cap = self.begin(g=g)
        self.manager.observe(
            cap,
            SourceRef("p", "PromptManagerPair", 2),
            0,
            snapshot(effective="TRIGGER"),
        )
        result = self.seal(cap)
        self.assertIsNone(result.bundle.contexts[0].positive.effective.value)
        self.assertNotEqual(result.state, "complete")

    def test_unsupported_graph_has_no_supported_context(self):
        result = self.seal(self.begin(g={}))
        self.assertEqual(result.reason, "no_supported_context")

    def host_context(self, kind="PromptManagerPair", index=0, job=JOB, node_id="p"):
        host = types.ModuleType("comfy_execution.utils")
        host.get_executing_context = lambda: types.SimpleNamespace(
            prompt_id=job, node_id=node_id, list_index=index
        )
        stack = self.enter_stack()
        stack.enter_context(patch.dict(sys.modules, {"comfy_execution.utils": host}))
        return host

    def enter_stack(self):
        from contextlib import ExitStack

        stack = ExitStack()
        self.addCleanup(stack.close)
        return stack

    def test_nodes_record_only_successful_returns_and_save_failure_preserves_text(self):
        import logging
        from unittest.mock import Mock
        from prompt_manager import (
            PromptManager,
            PromptManagerFocused,
            PromptManagerSplit,
        )
        from prompt_manager_text import PromptManagerText
        from prompt_manager_pair import PromptManagerPair

        for cls in (
            PromptManager,
            PromptManagerFocused,
            PromptManagerSplit,
            PromptManagerText,
            PromptManagerPair,
        ):
            kind = cls.__name__
            g = graph()
            g["p"]["class_type"] = kind
            if kind != "PromptManagerPair":
                g["s"]["inputs"]["negative"] = ["p", 0]
            if kind == "PromptManagerText":
                g["c"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["p", 0]}}
                g["s"]["inputs"] = {"positive": ["c", 0], "negative": ["c", 0]}
            cap = self.begin(g=g)
            self.host_context(kind)
            node = cls.__new__(cls)
            node.db = Mock()
            node.db.get_prompt_by_hash.return_value = None
            node.db.save_prompt.side_effect = RuntimeError("offline")
            node.db.save_prompt_pair.side_effect = RuntimeError("offline")
            node.logger = logging.getLogger("test.capture.nodes")
            node.prompt_tracker = Mock()
            node.comfyui_integration = Mock()
            node._inject_lora_trigger_words = lambda text: text + " TRIGGER"
            clip = Mock()
            clip.tokenize.side_effect = lambda text: text
            clip.encode_from_tokens_scheduled.side_effect = lambda text: (
                "encoded",
                text,
            )
            kwargs = dict(text=" raw ", prepend_text=" pre ", append_text=" post ")
            if kind != "PromptManagerText":
                kwargs["clip"] = clip
            if kind == "PromptManagerPair":
                kwargs["negative_text"] = " neg "
            output = (
                node.process_text(**kwargs)
                if kind == "PromptManagerText"
                else node.encode_prompt(**kwargs)
            )
            result = self.seal(cap)
            side = result.bundle.contexts[0].positive
            self.assertEqual(side.effective.provenance, "recorded", kind)
            self.assertEqual(side.raw.value, " raw ")
            self.assertEqual(side.prepend.value, " pre ")
            self.assertEqual(side.append.value, " post ")
            self.assertEqual(
                side.effective.value,
                output[-2] if kind == "PromptManagerPair" else output[-1],
            )
            if kind == "PromptManagerPair":
                self.assertEqual(
                    result.bundle.contexts[0].negative.effective.value, " neg "
                )
            if kind != "PromptManagerText":
                cap = self.begin(g=g)
                clip.encode_from_tokens_scheduled.side_effect = RuntimeError(
                    "encode failed"
                )
                with self.assertRaises(RuntimeError):
                    node.encode_prompt(**kwargs)
                self.assertIsNone(
                    self.seal(cap).bundle.contexts[0].positive.effective.value
                )

    def test_oversized_projection_and_observation_release_all_reservations(self):
        g = graph()
        g["p"]["inputs"].update(
            text="x" * 65536, prepend_text=" " * 65536, append_text=" " * 65536
        )
        cap = self.begin(g=g)
        self.manager.revoke(cap)
        self.assertEqual(self.pool.charged_bytes, 0)
        cap = self.begin()
        self.manager.observe(
            cap,
            SourceRef("p", "PromptManagerPair", 0),
            0,
            snapshot(effective="x" * 65537),
        )
        self.assertIsNone(self.seal(cap).bundle.contexts[0].positive.effective.value)

    def test_recorder_rejects_wrong_class_slot_and_noninteger_invocation(self):
        for node_type, role, index, node_id in (
            ("PromptManager", None, 0, "p"),
            ("PromptManagerPair", "invalid", 0, "p"),
            ("PromptManagerPair", "positive", True, "p"),
            ("PromptManagerPair", "positive", -1, "p"),
            ("PromptManagerPair", "positive", 0, "other"),
        ):
            cap = self.begin()
            self.host_context(index=index, node_id=node_id)
            capture.record_runtime_context(node_type, "bad", "", "", "bad", role=role)
            self.assertIsNone(
                self.seal(cap).bundle.contexts[0].positive.effective.value
            )

    def test_async_inherited_capability_after_await_cannot_record_new_attempt(self):
        import asyncio

        async def scenario():
            old = self.begin()
            entered, release = asyncio.Event(), asyncio.Event()

            async def late():
                entered.set()
                await release.wait()
                capture.record_runtime_context(
                    "PromptManagerPair", "late", "", "", "late", role="positive"
                )

            self.host_context()
            task = asyncio.create_task(late())
            await entered.wait()
            new = self.begin(2)
            release.set()
            await task
            self.assertIsNone(self.seal(old).bundle)
            self.assertIsNone(
                self.seal(new).bundle.contexts[0].positive.effective.value
            )

        asyncio.run(scenario())

    def test_oversized_runtime_field_is_unknown_without_erasing_other_fields(self):
        cap = self.begin()
        self.host_context()
        capture.record_runtime_context(
            "PromptManagerPair",
            " actual ",
            " pre ",
            " post ",
            "x" * 65537,
            role="positive",
        )
        result = self.seal(cap)
        side = result.bundle.contexts[0].positive
        self.assertEqual(side.raw.value, " actual ")
        self.assertEqual(side.prepend.value, " pre ")
        self.assertIsNone(side.effective.value)
        self.assertEqual(side.effective.reason, "limit_reached")
        self.assertEqual(result.reason, "field_limit")

    def test_mixed_cache_provenance_cannot_promote_inferred_pm_effective(self):
        cap = self.begin()
        field = TextEvidence("literal", "inferred", "graph_literal", None)
        cached = TextEvidence("cached TRIGGER", "recorded", "host_cache_output", None)
        self.manager.observe(
            cap,
            SourceRef("p", "PromptManagerPair", 0),
            0,
            PromptSnapshot(field, cached, field, field),
        )
        side = self.seal(cap).bundle.contexts[0].positive
        self.assertEqual(side.raw.provenance, "inferred")
        self.assertEqual(side.effective.value, "cached TRIGGER")
        cap = self.begin(2)
        self.manager.observe(
            cap,
            SourceRef("p", "PromptManagerPair", 0),
            0,
            PromptSnapshot(field, field, field, field),
        )
        self.assertIsNone(self.seal(cap).bundle.contexts[0].positive.effective.value)

    def test_restore_and_close_release_weakly_bound_payload_immediately(self):
        cap = self.begin()
        binding = copy_context()
        self.manager.revoke_all()
        self.assertEqual(self.pool.charged_bytes, 0)
        self.assertIsNone(binding.run(capture.active_capture))
        self.manager.observe(cap, SourceRef("p", "PromptManagerPair", 0), 0, snapshot())
        self.assertIsNone(self.seal(cap).bundle)
        owner = capture.ContextCapture(11, lambda: self.status, self.pool)
        owner.begin(self.start, 3, graph(), None)
        binding = copy_context()
        owner.revoke_all()
        ref = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNone(ref())
        self.assertIsNone(binding.run(capture.active_capture))

    def test_conflicting_replay_stays_ambiguous_after_limited_other_source(self):
        cap = self.begin()
        source = SourceRef("p", "PromptManagerPair", 0)
        self.manager.observe(cap, source, 0, snapshot(effective="first"))
        self.manager.observe(cap, source, 0, snapshot(effective="different"))
        self.manager.observe(
            cap,
            SourceRef("p", "PromptManagerPair", 1),
            0,
            snapshot(effective="x" * 65537),
        )
        sealed = self.seal(cap)
        self.assertEqual(sealed.state, "ambiguous")
        self.assertIsNone(sealed.bundle.contexts[0].positive.effective.value)

    def test_capture_owner_bounds_unavailable_attempts_to_128(self):
        owner = capture.ContextCapture(12, lambda: self.status, ContextBudget(0))
        self.addCleanup(owner.revoke_all)
        capabilities = [
            copy_context().run(
                owner.begin, replace(self.start, queue_item_id=i), 3, {}, None
            )
            for i in range(129)
        ]
        self.assertEqual(sum(owner.valid(cap) for cap in capabilities), 128)

    def test_revocation_discards_owned_samples_before_releasing_budget(self):
        cap = self.begin()
        value = snapshot()
        ref = weakref.ref(value.raw)
        self.manager.observe(cap, SourceRef("p", "PromptManagerPair", 0), 0, value)
        del value
        lease = self.manager._attempts[cap.nonce].lease
        original = lease.release

        def release():
            self.assertIsNone(ref(), "sample was uncharged while still owned")
            original()

        with patch.object(lease, "release", release):
            self.manager.revoke(cap)
