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
        self, kind="PromptManager", raw=" raw ", prepend=" pre ", append=" post "
    ):
        g = {
            "p": {
                "class_type": kind,
                "inputs": {
                    "text": raw,
                    "negative_text": "negative",
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
        if type(raw) is list or type(prepend) is list:
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
