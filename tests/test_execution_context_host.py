"""Read-only cached STRING evidence and cooperative executor notifications."""

from collections import namedtuple
from contextvars import Context
from dataclasses import replace
import gc
import sys
import weakref
import threading
import types
import unittest
from unittest.mock import patch

from test_execution_context_graph import transformed_input_graph

from utils.execution_context_budget import ContextBudget
from utils.execution_context_capture import ContextCapture
from utils.execution_history_events import RunStart
from utils.execution_history_writer import HistoryWriterStatus

try:
    from utils import execution_context_host as host
except ImportError:
    host = None

SESSION = "11111111-1111-4111-8111-111111111111"
EPOCH = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"
Entry = namedtuple("CacheEntry", "ui outputs")


class HostTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(host, "read-only host adapter is missing")
        self.queue = object()
        self.pool = ContextBudget()
        self.status = HistoryWriterStatus(EPOCH, True, 0, 0, True, 3)
        self.capture = ContextCapture(id(self.queue), lambda: self.status, self.pool)
        self.addCleanup(self.capture.revoke_all)
        self.calls = []
        calls = self.calls

        class BasicCache:
            def __init__(self):
                self.entries = {}

            def get_local(self, node_id):
                calls.append(("local", node_id))
                return self.entries.get(node_id)

            async def get(self, node_id):
                raise AssertionError("async get must never be called")

        class HierarchicalCache(BasicCache):
            pass

        class LRUCache(BasicCache):
            pass

        class RAMPressureCache(LRUCache):
            pass

        class NullCache:
            def get_local(self, node_id):
                raise AssertionError("null cache is not usable")

        class PromptExecutor:
            def add_message(self, event, data, broadcast):
                calls.append(("original", event, data, broadcast))
                if event == "raise":
                    raise RuntimeError("original")
                return "original-return"

        self.execution = types.ModuleType("execution")
        self.execution.PromptExecutor = PromptExecutor
        self.execution.CacheEntry = Entry
        self.caching = types.ModuleType("comfy_execution.caching")
        for cls in (
            BasicCache,
            HierarchicalCache,
            LRUCache,
            RAMPressureCache,
            NullCache,
        ):
            setattr(self.caching, cls.__name__, cls)
        package = types.ModuleType("comfy_execution")
        package.caching = self.caching
        self.patch = patch.dict(
            sys.modules,
            {
                "comfy_execution": package,
                "execution": self.execution,
                "comfy_execution.caching": self.caching,
            },
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.executor = PromptExecutor()
        self.executor.server = types.SimpleNamespace(prompt_queue=self.queue)
        self.executor.caches = types.SimpleNamespace(outputs=HierarchicalCache())

    def begin(
        self,
        kind="PromptManager",
        raw=" raw ",
        prepend=" pre ",
        append=" post ",
        negative="negative",
    ):
        g = {
            "p": {
                "class_type": kind,
                "inputs": {
                    "text": raw,
                    "negative_text": negative,
                    "prepend_text": prepend,
                    "append_text": append,
                },
            },
            "clip": {"class_type": "CLIPTextEncode", "inputs": {"text": ["p", 0]}},
            "s": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": (
                        ["clip", 0] if kind == "PromptManagerText" else ["p", 0]
                    ),
                    "negative": (
                        ["p", 1] if kind == "PromptManagerPair" else ["clip", 0]
                    ),
                },
            },
        }
        if any(type(value) is list for value in (raw, prepend, negative)):
            g["dynamic"] = {"class_type": "RuntimeGenerator", "inputs": {}}
            g["stateful"] = {
                "class_type": "PromptManagerText",
                "inputs": {"text": "looks literal"},
            }
        self.cap = self.capture.begin(
            RunStart(SESSION, SESSION, 1, JOB, EPOCH, 0), 3, g, None
        )
        return self.cap

    def observe(self, event, executor=None, **data):
        host.observe_cached_context(
            executor or self.executor, event, {"prompt_id": JOB, **data}
        )

    def seal(self):
        sealed = self.capture.seal(self.cap)
        if sealed.lease:
            self.addCleanup(sealed.lease.release)
        return sealed.bundle.contexts[0]

    def test_original_return_exception_and_wrapper_cleanup_preserved(self):
        cls = self.execution.PromptExecutor
        original = cls.add_message

        def earlier(executor, *args, **kwargs):
            self.calls.append("earlier")
            return original(executor, *args, **kwargs)

        cls.add_message = earlier
        seen = []
        stop_a = host.subscribe_executor_messages("a", lambda *args: seen.append(args))
        stop_b = host.subscribe_executor_messages("b", lambda *args: seen.append(args))
        wrapped = cls.add_message

        def later(executor, *args, **kwargs):
            self.calls.append("later")
            return wrapped(executor, *args, **kwargs)

        cls.add_message = later
        data = {"prompt_id": JOB}
        self.assertEqual(
            self.executor.add_message("ok", data, broadcast=False), "original-return"
        )
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0][0], self.executor)
        self.assertIs(seen[0][1][1], data)
        with self.assertRaisesRegex(RuntimeError, "original"):
            self.executor.add_message("raise", data, False)
        self.assertEqual(len(seen), 2)
        stop_a()
        self.executor.add_message("ok", data, False)
        self.assertEqual(len(seen), 3)
        stop_b()
        self.assertIs(cls.add_message, later)
        self.executor.add_message("ok", data, False)
        self.assertEqual(len(seen), 3)
        stop_c = host.subscribe_executor_messages("c", lambda *args: seen.append(args))
        self.executor.add_message("ok", data, False)
        self.assertEqual(len(seen), 4)
        stop_c()

    def test_cache_reads_only_local_string_slots(self):
        for mode in ("HierarchicalCache", "LRUCache", "RAMPressureCache"):
            for kind, values in (
                ("PromptManager", [object(), ["cached"]]),
                ("PromptManagerFocused", [object(), ["cached"]]),
                ("PromptManagerSplit", [object(), ["cached"]]),
                ("PromptManagerText", [["cached"]]),
                ("PromptManagerPair", [object(), object(), ["cached"], [""]]),
            ):
                with self.subTest(mode=mode, kind=kind):
                    self.begin(kind)
                    cache = getattr(self.caching, mode)()
                    entry = Entry({"secret": object()}, values)
                    cache.entries["p"] = entry
                    self.executor.caches.outputs = cache
                    self.observe("execution_start")
                    self.observe("execution_cached", nodes=["p"])
                    result = self.seal()
                    self.assertEqual(result.positive.effective.value, "cached")
                    self.assertEqual(
                        result.positive.effective.source, "host_cache_output"
                    )
                    if kind == "PromptManagerPair":
                        self.assertEqual(result.negative.effective.value, "")
                    self.assertIs(cache.entries["p"], entry)
                    self.assertEqual(set(cache.__dict__), {"entries"})
        self.assertTrue(all(call[0] == "local" for call in self.calls))

    def test_changed_trigger_configuration_keeps_cached_effective_text(self):
        self.begin()
        self.executor.caches.outputs.entries["p"] = Entry(
            None, [object(), ["old injected text"]]
        )
        self.observe("execution_start")
        self.observe("execution_cached", nodes=["p"])
        result = self.seal().positive
        self.assertEqual(result.effective.value, "old injected text")
        self.assertEqual(result.raw.value, " raw ")
        self.assertEqual(result.prepend.value, " pre ")
        self.assertEqual(result.append.value, " post ")
        self.assertEqual(result.raw.provenance, "inferred")

    def test_dynamic_cached_raw_is_unknown(self):
        for dynamic in (["dynamic", 0], ["stateful", 0]):
            for field in ("raw", "prepend"):
                with self.subTest(dynamic=dynamic, field=field):
                    self.begin(**{field: dynamic})
                    self.executor.caches.outputs.entries["p"] = Entry(
                        None, [object(), ["cached"]]
                    )
                    self.observe("execution_start")
                    self.observe("execution_cached", nodes=["p"])
                    result = self.seal().positive
                    self.assertEqual(result.effective.value, "cached")
                    self.assertIsNone(getattr(result, field).value)
                    self.assertEqual(result.append.value, " post ")
                    if field == "raw":
                        self.assertEqual(result.prepend.value, " pre ")
                    else:
                        self.assertEqual(result.raw.value, " raw ")

    def test_upstream_transform_preserves_final_pm_cached_effective_only(self):
        for field, snapshot_field in (
            ("text", "raw"),
            ("prepend_text", "prepend"),
            ("append_text", "append"),
        ):
            for unresolved in (False, True):
                with self.subTest(field=field, unresolved=unresolved):
                    g = transformed_input_graph(field, unresolved)
                    g["outer"] = {
                        "class_type": "StringConcatenate",
                        "inputs": {"string_a": ["p", 2], "string_b": " outer"},
                    }
                    g["clip"] = {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": ["outer", 0]},
                    }
                    g["s"]["inputs"]["negative"] = ["clip", 0]
                    self.cap = self.capture.begin(
                        RunStart(SESSION, SESSION, 1, JOB, EPOCH, 0),
                        3,
                        g,
                        None,
                    )
                    self.executor.caches.outputs.entries["p"] = Entry(
                        None, [object(), object(), [" cached injected "], [""]]
                    )
                    self.observe("execution_start")
                    self.observe("execution_cached", nodes=["upstream", "p"])
                    result = self.seal()
                    self.assertIsNone(result.negative.effective.value)
                    side = result.positive
                    self.assertEqual(side.effective.value, " cached injected ")
                    self.assertEqual(side.effective.provenance, "recorded")
                    self.assertEqual(side.effective.source, "host_cache_output")
                    for name, expected in (
                        ("raw", " cat "),
                        ("prepend", "pre"),
                        ("append", "post"),
                    ):
                        evidence = getattr(side, name)
                        if name == snapshot_field:
                            self.assertIsNone(evidence.value)
                            self.assertEqual(evidence.provenance, "unknown")
                        else:
                            self.assertEqual(evidence.value, expected)
                            self.assertEqual(evidence.provenance, "inferred")

    def test_provider_local_reset_eviction_and_unknown_modes_fail_closed(self):
        for mode in (
            "NullCache",
            "unknown",
            "reset",
            "evicted",
            "malformed",
            "provider-local",
        ):
            with self.subTest(mode=mode):
                self.begin()
                cache = self.caching.HierarchicalCache()
                if mode == "NullCache":
                    cache = self.caching.NullCache()
                elif mode == "unknown":
                    cache = types.SimpleNamespace(
                        get_local=lambda _: self.fail("unknown cache read")
                    )
                elif mode == "malformed":
                    cache.entries["p"] = Entry(None, [object(), [object()]])
                elif mode in ("reset", "provider-local"):
                    cache.entries["p"] = Entry(
                        None, [object(), ["resident provider output"]]
                    )
                self.executor.caches.outputs = cache
                self.observe("execution_start")
                if mode == "reset":
                    self.executor.caches.outputs = self.caching.HierarchicalCache()
                self.observe("execution_cached", nodes=["p"])
                result = self.seal().positive
                self.assertEqual(
                    result.effective.value,
                    "resident provider output" if mode == "provider-local" else None,
                )

    def test_worker_queue_job_executor_and_generation_fences(self):
        for failure in (
            "unbound",
            "thread",
            "queue",
            "job",
            "executor",
            "recursive",
            "generation",
            "no-start",
        ):
            with self.subTest(failure=failure):
                self.begin()
                self.executor.server.prompt_queue = self.queue
                self.executor.caches.outputs.entries["p"] = Entry(
                    None, [object(), ["cached"]]
                )
                if failure != "no-start":
                    self.observe("execution_start")
                target = self.executor
                if failure == "queue":
                    self.executor.server.prompt_queue = object()
                if failure in ("executor", "recursive"):
                    target = self.execution.PromptExecutor()
                    target.server = self.executor.server
                    target.caches = self.executor.caches
                    if failure == "recursive":
                        self.observe("execution_start", target)
                        target = self.executor
                data = {
                    "prompt_id": "wrong" if failure == "job" else JOB,
                    "nodes": ["p"],
                }
                if failure == "generation":
                    self.status = replace(self.status, capture_generation=4)

                def action():
                    host.observe_cached_context(target, "execution_cached", data)

                if failure == "unbound":
                    Context().run(action)
                elif failure == "thread":
                    thread = threading.Thread(target=action)
                    thread.start()
                    thread.join(2)
                else:
                    action()
                if failure == "generation":
                    self.assertIsNone(self.capture.seal(self.cap).bundle)
                    self.status = replace(self.status, capture_generation=3)
                else:
                    self.assertIsNone(self.seal().positive.effective.value)

    def test_missing_cache_preserves_known_affixes_when_raw_is_dynamic(self):
        self.begin(raw=["dynamic", 0])
        self.observe("execution_start")
        self.observe("execution_cached", nodes=["p"])
        result = self.seal().positive
        self.assertIsNone(result.raw.value)
        self.assertIsNone(result.effective.value)
        self.assertEqual(result.prepend.value, " pre ")
        self.assertEqual(result.append.value, " post ")

    def test_cache_entries_tensors_and_executor_are_not_retained(self):
        class Tensor:
            pass

        tensor = Tensor()
        tensor_ref = weakref.ref(tensor)
        self.begin()
        self.executor.caches.outputs.entries["p"] = Entry(tensor, [tensor, ["cached"]])
        self.observe("execution_start")
        self.observe("execution_cached", nodes=["p"])
        self.executor.caches.outputs.entries.clear()
        executor_ref = weakref.ref(self.executor)
        del tensor
        del self.executor
        gc.collect()
        self.assertIsNone(tensor_ref())
        self.assertIsNone(executor_ref())
        self.assertEqual(self.seal().positive.effective.value, "cached")

    def test_unknown_and_malformed_cache_payloads_are_bounded(self):
        for values in (["x"] * 65, ["x" * 65537], [object()], ["x", "x"]):
            with self.subTest(count=len(values)):
                self.begin()
                self.executor.caches.outputs.entries["p"] = Entry(
                    None, [object(), values]
                )
                self.observe("execution_start")
                self.observe("execution_cached", nodes=["p"])
                result = self.seal().positive
                self.assertIsNone(result.effective.value)
                self.assertEqual(
                    result.append.value, " post " if len(values) != 2 else None
                )

    def test_disposed_subscriptions_do_not_retain_dynamic_executor_classes(self):
        def install_and_remove():
            class TemporaryExecutor:
                def add_message(self, *args, **kwargs):
                    return None

            self.execution.PromptExecutor = TemporaryExecutor
            ref = weakref.ref(TemporaryExecutor)
            stop = host.subscribe_executor_messages("temporary", lambda *args: None)
            stop()
            del self.execution.PromptExecutor
            return ref

        ref = install_and_remove()
        gc.collect()
        self.assertIsNone(ref())

    # -- #403: a cached runtime text takes its fields from the reused execution

    class Receipts:
        """A fake ``CachedRuns.recorded_fields``: fixed fields, calls recorded."""

        def __init__(self, fields):
            self.fields, self.calls = fields, []

        def recorded_fields(self, prompt_id, node_id, values):
            self.calls.append((prompt_id, node_id, values))
            return self.fields

    def observe_with(self, receipts, outputs, **begin):
        self.begin(**begin)
        self.executor.caches.outputs.entries["p"] = Entry(None, outputs)
        self.observe("execution_start")
        host.observe_cached_context(
            self.executor,
            "execution_cached",
            {"prompt_id": JOB, "nodes": ["p"]},
            receipts=receipts,
        )
        return self.seal()

    def test_cached_runtime_raw_comes_from_the_matching_receipt(self):
        receipts = self.Receipts([(("LLM raw", " pre ", " post "),)])
        result = self.observe_with(
            receipts, [object(), ["cached"]], raw=["dynamic", 0]
        ).positive
        self.assertEqual(receipts.calls, [(JOB, "p", (("cached",),))])
        self.assertEqual(result.raw.value, "LLM raw")
        self.assertEqual(
            (result.raw.provenance, result.raw.source), ("inferred", "graph_semantics")
        )
        self.assertEqual(result.effective.value, "cached")
        self.assertEqual(result.effective.source, "host_cache_output")
        # Fields the graph already knows are never replaced
        self.assertEqual(result.prepend.value, " pre ")
        self.assertEqual(result.prepend.source, "graph_literal")
        self.assertEqual(result.append.value, " post ")

    def test_cached_runtime_prepend_comes_from_the_matching_receipt(self):
        receipts = self.Receipts([((" raw ", "LLM pre", " post "),)])
        result = self.observe_with(
            receipts, [object(), ["cached"]], prepend=["dynamic", 0]
        ).positive
        self.assertEqual(result.prepend.value, "LLM pre")
        self.assertEqual(result.prepend.source, "graph_semantics")
        self.assertEqual(result.raw.value, " raw ")
        self.assertEqual(result.raw.source, "graph_literal")

    def test_a_receipt_disagreeing_with_a_known_field_fills_nothing(self):
        receipts = self.Receipts([(("LLM raw", "other pre", " post "),)])
        result = self.observe_with(
            receipts, [object(), ["cached"]], raw=["dynamic", 0]
        ).positive
        self.assertIsNone(result.raw.value)
        self.assertEqual(result.prepend.value, " pre ")

    def test_pair_runtime_negative_takes_the_negative_fields(self):
        receipts = self.Receipts(
            [((" raw ", " pre ", " post "), ("LLM negative", "", ""))]
        )
        result = self.observe_with(
            receipts,
            [object(), object(), ["cached positive"], ["LLM negative"]],
            kind="PromptManagerPair",
            negative=["dynamic", 0],
        )
        self.assertEqual(result.negative.raw.value, "LLM negative")
        self.assertEqual(result.negative.raw.source, "graph_semantics")
        self.assertEqual(result.positive.raw.value, " raw ")
        self.assertEqual(result.positive.raw.source, "graph_literal")

    def test_known_typed_fields_are_never_replaced_by_a_receipt(self):
        receipts = self.Receipts([(("other", "other", "other"),)])
        result = self.observe_with(receipts, [object(), ["cached"]]).positive
        self.assertEqual(
            (result.raw.value, result.prepend.value, result.append.value),
            (" raw ", " pre ", " post "),
        )
        self.assertEqual(result.raw.source, "graph_literal")

    def test_no_matching_receipt_keeps_the_cached_raw_unknown(self):
        for fields in (None, [None], [], "bad", [(("a", "b"),)], [((1, "", ""),)]):
            with self.subTest(fields=fields):
                result = self.observe_with(
                    self.Receipts(fields), [object(), ["cached"]], raw=["dynamic", 0]
                ).positive
                self.assertIsNone(result.raw.value)
                self.assertEqual(result.effective.value, "cached")

    def test_a_failing_receipt_lookup_keeps_the_capture(self):
        class Broken:
            def recorded_fields(self, *args):
                raise RuntimeError("boom")

        result = self.observe_with(
            Broken(), [object(), ["cached"]], raw=["dynamic", 0]
        ).positive
        self.assertIsNone(result.raw.value)
        self.assertEqual(result.effective.value, "cached")

    def test_pair_slots_take_their_own_receipt_fields(self):
        receipts = self.Receipts(
            [(("LLM positive", " pre ", " post "), ("LLM negative", "", ""))]
        )
        result = self.observe_with(
            receipts,
            [object(), object(), ["cached positive"], ["cached negative"]],
            kind="PromptManagerPair",
            raw=["dynamic", 0],
        )
        self.assertEqual(
            receipts.calls[0], (JOB, "p", (("cached positive",), ("cached negative",)))
        )
        self.assertEqual(result.positive.raw.value, "LLM positive")
        self.assertEqual(result.positive.effective.value, "cached positive")
        # The negative editor is typed: the graph's value stays
        self.assertEqual(result.negative.raw.value, "negative")
        self.assertEqual(result.negative.raw.source, "graph_literal")

    def test_default_receipts_are_the_process_cached_runs(self):
        from utils import cached_runs

        receipts = self.Receipts([(("LLM raw", " pre ", " post "),)])
        with patch.object(cached_runs, "CACHED_RUNS", receipts):
            self.begin(raw=["dynamic", 0])
            self.executor.caches.outputs.entries["p"] = Entry(
                None, [object(), ["cached"]]
            )
            self.observe("execution_start")
            self.observe("execution_cached", nodes=["p"])
        self.assertEqual(self.seal().positive.raw.value, "LLM raw")
