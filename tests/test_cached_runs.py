"""Cached re-runs of runtime-linked prompt text count once per job (#347).

Jobs are simulated end to end: the queue hook (submit + dequeue), the
``execution_cached`` replay, the real node executions and ComfyUI's output
cache (fakes of ``execution`` and ``comfy_execution.caching``, as in
tests/test_execution_context_host.py).
"""

import logging
import os
import sys
import tempfile
import threading
import types
import unittest
from collections import namedtuple
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from utils import cached_runs as module
from utils.cached_runs import (
    CachedRuns,
    Facts,
    Role,
    Seen,
    graph_role,
    graph_seen,
    own_counts,
    register_cached_runs,
    stored_facts,
)
from utils.execution_context_host import cached_string_outputs
from utils.hashing import generate_prompt_hash
from utils.run_keys import prompt_node_key, upstream_closure, upstream_nodes
from utils.usage_tracking import (
    PendingFirstUse,
    QueuedRuns,
    find_pair_id,
    find_pair_id_by_hashes,
    handle_queued_prompt,
    on_dequeue,
)

Entry = namedtuple("CacheEntry", "ui outputs")
LLM = "a lighthouse the LLM wrote"


class _Cache:
    def __init__(self):
        self.entries = {}

    def get_local(self, node_id):
        return self.entries.get(node_id)


class HierarchicalCache(_Cache):
    pass


class LRUCache(_Cache):
    pass


class RAMPressureCache(LRUCache):
    pass


class NullCache(_Cache):
    pass


def host_modules():
    """Fake ``execution`` and ``comfy_execution.caching`` for the cache reader."""
    execution = types.ModuleType("execution")
    execution.CacheEntry = Entry

    class PromptExecutor:
        def add_message(self, event, data, broadcast):
            return None

    execution.PromptExecutor = PromptExecutor
    caching = types.ModuleType("comfy_execution.caching")
    for cls in (HierarchicalCache, LRUCache, RAMPressureCache, NullCache):
        setattr(caching, cls.__name__, cls)
    package = types.ModuleType("comfy_execution")
    package.caching = caching
    return {
        "execution": execution,
        "comfy_execution": package,
        "comfy_execution.caching": caching,
    }


def graph(
    kind="PromptManager",
    text=None,
    role="positive",
    sampler=True,
    negative_text="",
    seed=1,
):
    """PrimitiveString -> PreviewAny -> ``kind`` -> KSampler (runtime text).

    ``text`` set: typed text instead of the runtime link. ``role`` "negative":
    the node feeds the sampler's negative input. Pair: its negative editor holds
    ``negative_text`` and it feeds both sampler inputs.
    """
    g = {
        "str": {"class_type": "PrimitiveString", "inputs": {"value": "seed text"}},
        "llm": {"class_type": "PreviewAny", "inputs": {"source": ["str", 0]}},
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt": "m"}},
        "pm": {
            "class_type": kind,
            "inputs": {"text": ["llm", 0] if text is None else text},
        },
        "other": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["ckpt", 1]},
        },
        "save": {"class_type": "SaveImage", "inputs": {"images": ["ks", 0]}},
    }
    if kind != "PromptManagerText":
        g["pm"]["inputs"]["clip"] = ["ckpt", 1]
    if kind == "PromptManagerPair":
        g["pm"]["inputs"]["negative_text"] = negative_text
    source = ["pm", 0]
    if kind == "PromptManagerText":
        g["enc"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["pm", 0], "clip": ["ckpt", 1]},
        }
        source = ["enc", 0]
    positive, negative = (source, ["other", 0])
    if role == "negative":
        positive, negative = negative, positive
    if kind == "PromptManagerPair":
        positive, negative = ["pm", 0], ["pm", 1]
    if sampler:
        g["ks"] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["ckpt", 0],
                "positive": positive,
                "negative": negative,
                "seed": seed,
            },
        }
    else:
        g["save"]["inputs"]["images"] = positive
    return g


class JobCase(unittest.TestCase):
    """A database, the queue hook stashes and one ``CachedRuns`` per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.db = PromptDatabase(os.path.join(self.tmp.name, "runs.db"))
        self.addCleanup(self.db.close_all)
        patcher = mock.patch.dict(sys.modules, host_modules())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.queued, self.pair_queued = QueuedRuns(), QueuedRuns()
        self.pending, self.pair_pending = PendingFirstUse(), PendingFirstUse()
        self.context = (None, None)
        self.runs = CachedRuns(context=lambda: self.context)
        self.queue = object()
        self.executor = types.SimpleNamespace(
            server=types.SimpleNamespace(prompt_queue=self.queue),
            caches=types.SimpleNamespace(outputs=HierarchicalCache()),
        )
        self.item_id = 0
        self.trackers = []

    # -- nodes ----------------------------------------------------------------

    def node(self, kind):
        if kind == "PromptManagerPair":
            from prompt_manager_pair import PromptManagerPair as cls
        elif kind == "PromptManagerText":
            from prompt_manager_text import PromptManagerText as cls
        else:
            import prompt_manager

            cls = getattr(prompt_manager, kind)
        node = cls.__new__(cls)
        node.db = self.db
        node.logger = logging.getLogger("test.cached_runs")
        node.prompt_tracker = mock.Mock()
        self.trackers.append(node.prompt_tracker)
        node.comfyui_integration = mock.Mock()
        node._inject_lora_trigger_words = lambda text: text
        node.pending_first_use = self.pending
        node.pending_pair_first_use = self.pair_pending
        node.cached_runs = self.runs
        return node

    def execute(self, g, node_id="pm", text=LLM, negative="", index=None):
        """Run the node as ComfyUI would; returns its STRING outputs."""
        kind = g[node_id]["class_type"]
        node = self.node(kind)
        self.context = (self.context[0], index)
        clip = mock.Mock()
        if kind == "PromptManagerText":
            return node.process_text(text, prompt=g, unique_id=node_id)
        if kind == "PromptManagerPair":
            return node.encode_prompt(
                clip, text, negative_text=negative, prompt=g, unique_id=node_id
            )
        return node.encode_prompt(clip, text, prompt=g, unique_id=node_id)

    def cache(self, node_id, *results):
        """Store executed results (one per list index) as ComfyUI's cache entry."""
        width = len(results[0])
        outputs = [[result[slot] for result in results] for slot in range(width)]
        self.executor.caches.outputs.entries[node_id] = Entry({}, outputs)

    # -- jobs -----------------------------------------------------------------

    def job(self, g, cached=(), run=None, outputs=("save",), prompt_id=None):
        """One queued job: hook, dequeue, ``execution_cached``, executions, done."""
        self.item_id += 1
        prompt_id = prompt_id or f"job-{self.item_id}"
        request = handle_queued_prompt(
            {"prompt": g, "prompt_id": prompt_id},
            self.queued,
            pair_queued=self.pair_queued,
        )
        on_dequeue(
            request["prompt_id"],
            self.queued,
            lambda: self.db,
            self.pending,
            pair_queued=self.pair_queued,
            pair_pending=self.pair_pending,
        )
        self.runs.begin_attempt(
            ((self.item_id, prompt_id, g, {}, list(outputs), {}), self.item_id)
        )
        self.context = (prompt_id, None)
        self.runs.replay(self.executor, prompt_id, list(cached), lambda: self.db)
        result = run() if run else None
        self.runs.end_attempt(self.item_id)
        self.context = (None, None)
        return result

    def first(self, g, text=LLM, negative="", node_id="pm"):
        """A job that executes the node and fills the cache."""
        result = self.job(
            g, run=lambda: self.execute(g, node_id, text=text, negative=negative)
        )
        self.cache(node_id, result)
        return result

    def count(self, text):
        row = self.db.get_prompt_by_hash(generate_prompt_hash(text))
        return row["run_count"] if row else None

    def pair_count(self, positive, negative):
        pair_id = find_pair_id(self.db, positive, negative)
        return self.db.get_prompt_pair(pair_id)["run_count"] if pair_id else None


class TestCachedJobsCount(JobCase):
    def test_runtime_text_counts_every_cached_job_for_each_node_kind(self):
        for kind in (
            "PromptManager",
            "PromptManagerFocused",
            "PromptManagerSplit",
            "PromptManagerText",
        ):
            with self.subTest(kind=kind):
                text = f"{LLM} for {kind}"
                g = graph(kind)
                self.first(g, text=text)
                self.job(graph(kind, seed=2), cached=["pm"])
                self.job(graph(kind, seed=3), cached=["pm"])
                self.assertEqual(self.count(text), 3)

    def test_typed_text_still_counts_once_per_job(self):
        g = graph(text="typed castle")
        self.first(g, text="typed castle")
        self.job(g, cached=["pm"])
        self.job(g, cached=["pm"])
        self.assertEqual(self.count("typed castle"), 3)

    def test_rewired_to_negative_while_cached_counts_nothing(self):
        self.first(graph())
        self.job(graph(role="negative"), cached=["pm"])
        self.assertEqual(self.count(LLM), 1)
        # Rewired back to positive: the receipt still proves the text
        self.job(graph(), cached=["pm"])
        self.assertEqual(self.count(LLM), 2)

    def test_no_sampler_counts_every_node_on_replay(self):
        g = graph(sampler=False)
        self.first(g)
        self.job(g, cached=["pm"])
        self.assertEqual(self.count(LLM), 2)

    def test_empty_editor_replays_nothing(self):
        g = graph()
        self.first(g, text="")
        self.job(g, cached=["pm"])
        self.assertEqual(self.db.search_prompts(), [])

    def test_alternating_upstream_values_each_count_their_own_prompt(self):
        g = graph()
        self.first(g, text="value A")
        self.first(g, text="value B")
        self.cache("pm", self.execute_outputs("value A"))
        self.job(g, cached=["pm"])
        self.assertEqual((self.count("value A"), self.count("value B")), (2, 1))

    def execute_outputs(self, text):
        return (mock.ANY, text)

    def test_list_items_replay_each_item_in_order(self):
        g = graph()
        results = []

        def run():
            for index, text in enumerate(("one", "two", "three")):
                results.append(self.execute(g, text=text, index=index))

        self.job(g, run=run)
        self.cache("pm", *results)
        self.job(g, cached=["pm"])
        self.assertEqual([self.count(t) for t in ("one", "two", "three")], [2, 2, 2])

    def test_a_repeated_id_or_message_never_counts_a_node_twice(self):
        g = graph()
        self.first(g)
        self.job(g, cached=["pm", "pm"])
        self.assertEqual(self.count(LLM), 2)
        self.item_id += 1
        self.runs.begin_attempt(((1, "again", g, {}, ["save"], {}), self.item_id))
        for _ in range(2):
            self.runs.replay(self.executor, "again", ["pm"], lambda: self.db)
        self.assertEqual(self.count(LLM), 3)

    def test_a_shorter_list_keeps_its_receipt_after_a_longer_one(self):
        g = graph()
        self.first(g, text="alpha")
        results = []

        def run():
            for index, text in enumerate(("alpha", "beta")):
                results.append(self.execute(g, text=text, index=index))

        self.job(g, run=run)
        self.cache("pm", (mock.ANY, "alpha"))
        self.job(g, cached=["pm"])
        self.assertEqual((self.count("alpha"), self.count("beta")), (3, 1))

    def test_a_job_reusing_a_prompt_id_counts_and_executes_normally(self):
        g = graph()
        self.first(g)
        self.job(g, cached=["pm"], prompt_id="same")
        self.job(g, run=lambda: self.execute(g), prompt_id="same")
        self.job(g, cached=["pm"], prompt_id="same")
        self.assertEqual(self.count(LLM), 4)


class TestFailClosed(JobCase):
    def setUp(self):
        super().setUp()
        self.g = graph()
        self.first(self.g)

    def assertNotCounted(self):
        self.assertEqual(self.count(LLM), 1)

    def test_changed_cached_text_is_not_counted(self):
        self.cache("pm", (mock.ANY, LLM + " edited"))
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_no_receipt_in_this_process_is_not_counted(self):
        self.runs = CachedRuns(context=lambda: self.context)
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_missing_cache_entry_is_not_counted(self):
        self.executor.caches.outputs.entries.clear()
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_unsupported_cache_is_not_counted(self):
        entries = self.executor.caches.outputs.entries
        self.executor.caches.outputs = NullCache()
        self.executor.caches.outputs.entries = entries
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_more_list_values_than_the_bound_are_not_counted(self):
        self.cache("pm", *[(mock.ANY, LLM)] * 65)
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_missing_list_index_is_not_counted(self):
        self.cache("pm", (mock.ANY, LLM), (mock.ANY, LLM))
        self.job(self.g, cached=["pm"])
        self.assertNotCounted()

    def test_node_the_job_does_not_run_is_not_counted(self):
        self.job(self.g, cached=["pm"], outputs=("other",))
        self.assertNotCounted()

    def test_message_for_another_job_is_not_counted(self):
        self.item_id += 1
        self.runs.begin_attempt(((1, "this", self.g, {}, ["save"], {}), self.item_id))
        self.runs.replay(self.executor, "another", ["pm"], lambda: self.db)
        self.assertNotCounted()

    def test_unkeyable_node_is_not_counted(self):
        g = graph()
        g["pm"]["inputs"]["extra"] = ["missing", 0]
        self.job(g, cached=["pm"])
        self.assertIsNone(prompt_node_key(g, "pm"))
        self.assertNotCounted()

    def test_deleted_prompt_is_skipped(self):
        self.db.delete_prompt(
            self.db.get_prompt_by_hash(generate_prompt_hash(LLM))["id"]
        )
        self.job(self.g, cached=["pm"])
        self.assertIsNone(self.count(LLM))

    def test_database_errors_never_raise(self):
        def broken():
            raise RuntimeError("database unavailable")

        self.item_id += 1
        self.runs.begin_attempt(((1, "j", self.g, {}, ["save"], {}), self.item_id))
        self.runs.replay(self.executor, "j", ["pm"], broken)
        self.assertNotCounted()

    def test_malformed_messages_and_results_are_ignored(self):
        self.runs.begin_attempt(None)
        self.runs.begin_attempt((("x",), "not an int"))
        self.runs.replay(self.executor, None, ["pm"], lambda: self.db)
        self.item_id += 1
        self.runs.begin_attempt(((1, "j", self.g, {}, ["save"], {}), self.item_id))
        self.runs.replay(self.executor, "j", "pm", lambda: self.db)
        self.runs.replay(self.executor, "j", ["pm"] * 501, lambda: self.db)
        self.runs.replay(self.executor, "j", [7, None], lambda: self.db)
        self.assertNotCounted()


class TestReplayThenExecution(JobCase):
    def test_execution_after_replay_in_the_same_job_is_not_counted_again(self):
        # RAM-pressure frees the entry after execution_cached: the node executes
        g = graph()
        self.first(g)
        self.job(g, cached=["pm"], run=lambda: self.execute(g))
        self.assertEqual(self.count(LLM), 2)
        # The suppressed execution still leaves a receipt: the next job counts
        self.job(g, cached=["pm"])
        self.assertEqual(self.count(LLM), 3)

    def test_suppression_keeps_the_pending_catch_up_and_the_tracker_queue(self):
        g = graph()
        self.first(g)
        prompt_id = self.db.get_prompt_by_hash(generate_prompt_hash(LLM))["id"]

        def run():
            # The replay counted, then the prompt was deleted and re-saved
            self.db.delete_prompt(prompt_id)
            self.pending.add(generate_prompt_hash(LLM))
            return self.execute(g)

        self.job(g, cached=["pm"], run=run)
        self.assertEqual(self.count(LLM), 1)  # the catch-up, not the own count
        push = self.trackers[-1].set_current_prompt.call_args.kwargs["push_to_queue"]
        self.assertTrue(push)

    def test_without_host_context_the_node_counts_as_before(self):
        g = graph()
        self.runs = CachedRuns(context=lambda: (None, None))
        self.first(g)
        self.job(g, cached=["pm"])
        self.job(g, run=lambda: self.execute(g))
        self.assertEqual(self.count(LLM), 2)


class TestPairReplay(JobCase):
    def test_runtime_positive_counts_positive_and_pair_every_job(self):
        g = graph("PromptManagerPair", negative_text="blurry")
        self.first(g, negative="blurry")
        self.job(graph("PromptManagerPair", negative_text="blurry", seed=2), ["pm"])
        self.job(graph("PromptManagerPair", negative_text="blurry", seed=3), ["pm"])
        self.assertEqual(self.count(LLM), 3)
        self.assertEqual(self.pair_count(LLM, "blurry"), 3)
        self.assertEqual(self.count("blurry"), 0)

    def test_empty_negative_pair_replays_its_pair_count(self):
        g = graph("PromptManagerPair")
        self.first(g)
        self.job(g, cached=["pm"])
        self.assertEqual(self.pair_count(LLM, ""), 2)
        self.assertEqual(self.count(LLM), 2)

    def test_pair_execution_after_replay_is_not_counted_again(self):
        g = graph("PromptManagerPair", negative_text="blurry")
        self.first(g, negative="blurry")
        self.job(g, ["pm"], run=lambda: self.execute(g, negative="blurry"))
        self.assertEqual(self.count(LLM), 2)
        self.assertEqual(self.pair_count(LLM, "blurry"), 2)

    def test_pair_role_follows_the_current_graph(self):
        g = graph("PromptManagerPair", negative_text="blurry")
        self.first(g, negative="blurry")
        rewired = graph("PromptManagerPair", negative_text="blurry")
        rewired["ks"]["inputs"]["positive"] = ["other", 0]
        self.job(rewired, cached=["pm"])
        self.assertEqual(self.count(LLM), 1)
        self.assertEqual(self.pair_count(LLM, "blurry"), 1)


class TestOwnCounts(unittest.TestCase):
    """The node path and replay decide through the same function."""

    def test_single_node_table(self):
        facts = Facts("p")
        for used, seen, expected in (
            ({"text"}, False, [("prompt", "p")]),
            ({"text"}, True, []),
            (set(), False, []),
        ):
            with self.subTest(used=used, seen=seen):
                counts = own_counts(
                    "PromptManager", facts, Role(frozenset(used)), Seen(text=seen)
                )
                self.assertEqual(counts, expected)
        self.assertEqual(
            own_counts("PromptManager", Facts(None), Role(frozenset({"text"})), Seen()),
            [],
        )

    def test_pair_table(self):
        both = Role(frozenset({"text", "negative_text"}), True)
        self.assertEqual(
            own_counts("PromptManagerPair", Facts("p", "n"), both, Seen()),
            [("prompt", "p"), ("prompt", "n"), ("pair", "p", "n")],
        )
        self.assertEqual(
            own_counts("PromptManagerPair", Facts("p", "p"), both, Seen()),
            [("prompt", "p"), ("pair", "p", "p")],
        )
        self.assertEqual(
            own_counts("PromptManagerPair", Facts("p"), both, Seen(text=True)),
            [("pair", "p", None)],
        )
        self.assertEqual(
            own_counts(
                "PromptManagerPair", Facts("p", "n"), both, Seen(True, True, True)
            ),
            [],
        )

    def test_node_and_replay_agree_on_role_and_seen(self):
        for g, node_id in (
            (graph(), "pm"),
            (graph(text="typed"), "pm"),
            (graph(role="negative"), "pm"),
            (graph(sampler=False), "pm"),
            (graph("PromptManagerPair", negative_text="n"), "pm"),
            (graph("PromptManagerPair", text="typed", negative_text="n"), "pm"),
        ):
            kind = g[node_id]["class_type"]
            text = "typed" if isinstance(g[node_id]["inputs"]["text"], str) else LLM
            facts = stored_facts(text, g[node_id]["inputs"].get("negative_text"))
            with self.subTest(kind=kind, inputs=g[node_id]["inputs"]):
                replay = own_counts(
                    kind,
                    facts,
                    graph_role(g, node_id, kind),
                    graph_seen(g, node_id, kind, facts),
                )
                self.assertEqual(replay, self.node_counts(g, node_id, kind, text))

    @staticmethod
    def node_counts(g, node_id, kind, text):
        """What the executing node counted itself, from a recording database."""
        db = mock.Mock()
        db.get_prompt_by_hash.side_effect = lambda h: {"id": h}
        db.save_prompt_pair.return_value = 1
        negative = g[node_id]["inputs"].get("negative_text", "")
        db.get_prompt_pair.return_value = {
            "positive_prompt_id": generate_prompt_hash(text),
            "negative_prompt_id": (
                generate_prompt_hash(negative) if negative.strip() else None
            ),
        }
        case = JobCase()
        case.db = db
        case.pending, case.pair_pending = PendingFirstUse(), PendingFirstUse()
        case.runs = CachedRuns(context=lambda: (None, None))
        case.trackers = []
        case.context = (None, None)
        if kind == "PromptManagerPair":
            node = case.node(kind)
            node.encode_prompt(
                mock.Mock(), text, negative_text=negative, prompt=g, unique_id=node_id
            )
        else:
            node = case.node(kind)
            node._track_prompt_execution(text, text, None, None, {}, g, node_id)
        counts = [("prompt", c.args[0]) for c in db.record_prompt_use.call_args_list]
        if kind == "PromptManagerPair":
            counts += [
                ("pair", generate_prompt_hash(text), db.get_prompt_pair()[k])
                for k in ["negative_prompt_id"]
                if db.record_prompt_pair_use.called
            ]
        return counts


class TestReceipts(unittest.TestCase):
    def setUp(self):
        self.context = ("j1", None)
        self.runs = CachedRuns(
            context=lambda: self.context, max_keys=2, max_receipts=2, max_items=3
        )
        self.g = {
            name: {"class_type": "PromptManager", "inputs": {"text": name}}
            for name in ("a", "b", "c")
        }

    def begin(self, item_id, prompt_id="j1"):
        self.context = (prompt_id, None)
        self.runs.begin_attempt(
            ((item_id, prompt_id, self.g, {}, ["a", "b", "c"], {}), item_id)
        )

    def keys(self):
        return list(self.runs._receipts)

    def test_least_recently_used_run_keys_are_dropped(self):
        self.begin(1)
        for name in ("a", "b", "c"):
            self.runs.record_item(name, (name,), Facts("h"))
        self.assertEqual(self.keys(), [prompt_node_key(self.g, n) for n in ("b", "c")])

    def test_receipts_per_key_are_bounded_and_equal_outputs_replace(self):
        key = prompt_node_key(self.g, "a")
        for item_id, text in ((1, "x"), (2, "y"), (3, "z"), (4, "y")):
            self.begin(item_id)
            self.runs.record_item("a", (text,), Facts(text))
            self.runs.end_attempt(item_id)
        receipts = self.runs._receipts[key]
        self.assertEqual([r.item_id for r in receipts], [4, 3])

    def test_list_indices_past_the_bound_are_not_recorded(self):
        self.begin(1)
        self.context = ("j1", 3)
        self.runs.record_item("a", ("x",), Facts("x"))
        self.assertEqual(self.keys(), [])

    def test_non_string_outputs_and_unknown_nodes_are_not_recorded(self):
        self.begin(1)
        self.runs.record_item("a", (None,), Facts("x"))
        self.runs.record_item("zzz", ("x",), Facts("x"))
        self.assertEqual(self.keys(), [])

    def test_task_done_clears_only_its_own_attempt(self):
        self.begin(1)
        self.runs.end_attempt(2)
        self.runs.record_item("a", ("x",), Facts("x"))
        self.runs.end_attempt(1)
        self.runs.record_item("b", ("x",), Facts("x"))
        self.assertEqual(self.keys(), [prompt_node_key(self.g, "a")])

    def test_job_nodes_follow_the_outputs_to_execute(self):
        self.runs.begin_attempt(((1, "j1", self.g, {}, ["b"], {}), 1))
        self.assertEqual(list(self.runs._attempt.nodes), ["b"])


class TestCacheReader(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, host_modules())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = LRUCache()
        self.executor = types.SimpleNamespace(
            caches=types.SimpleNamespace(outputs=self.cache)
        )

    def read(self, outputs, kind="PromptManager"):
        self.cache.entries["n"] = Entry({}, outputs)
        return cached_string_outputs(self.executor, "n", kind)

    def test_reads_each_string_slot_in_order(self):
        self.assertEqual(self.read([["c"], ["a", "b"]]), (("a", "b"),))
        self.assertEqual(self.read([["t"]], "PromptManagerText"), (("t",),))
        self.assertEqual(
            self.read([[1], [2], ["p"], ["n"]], "PromptManagerPair"), (("p",), ("n",))
        )

    def test_fails_closed(self):
        for outputs, kind in (
            ([["c"], ["a", 1]], "PromptManager"),
            ([["c"]], "PromptManager"),
            ([["c"], ["a"] * 65], "PromptManager"),
            ([[1], [2], ["p", "q"], ["n"]], "PromptManagerPair"),
            ([["c"], "text"], "PromptManager"),
            ([["c"], ["a"]], "CLIPTextEncode"),
            ([[]] * 5, "PromptManager"),
        ):
            with self.subTest(outputs=outputs, kind=kind):
                self.assertIsNone(self.read(outputs, kind))
        self.cache.entries["m"] = ({}, [["c"], ["a"]])  # not a CacheEntry
        self.assertIsNone(cached_string_outputs(self.executor, "m", "PromptManager"))
        self.executor.caches.outputs = NullCache()
        self.assertIsNone(self.read([["c"], ["a"]]))


class TestGraphHelpers(unittest.TestCase):
    def test_upstream_nodes_and_closure(self):
        g = graph()
        self.assertEqual(
            set(upstream_nodes(g, ["save"])),
            {"save", "ks", "pm", "llm", "str", "ckpt", "other"},
        )
        self.assertEqual(upstream_nodes(g, ["missing"]), [])
        self.assertEqual(upstream_nodes(None, ["save"]), [])
        self.assertIsNone(upstream_closure(g, ["save"], limit=2))
        self.assertIsNone(upstream_closure({"a": {"inputs": {"x": ["b", 0]}}}, ["a"]))

    def test_find_pair_by_hashes_matches_the_text_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = PromptDatabase(os.path.join(tmp, "p.db"))
            try:
                pair_id = db.save_prompt_pair("cat", "")
                self.assertEqual(
                    find_pair_id_by_hashes(db, generate_prompt_hash("cat"), None),
                    pair_id,
                )
                self.assertIsNone(
                    find_pair_id_by_hashes(
                        db, generate_prompt_hash("cat"), generate_prompt_hash("x")
                    )
                )
            finally:
                db.close_all()
            assert_closed(self, tmp)


class TestRegistration(unittest.TestCase):
    def setUp(self):
        self.modules = host_modules()
        patcher = mock.patch.dict(sys.modules, self.modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        flag = mock.patch.object(module, "_registered", threading.Event())
        flag.start()
        self.addCleanup(flag.stop)

    class Queue:
        def __init__(self):
            self.done = []

        def get(self, timeout=None):
            return self.result

        def task_done(self, item_id, *args, **kwargs):
            self.done.append(item_id)

    def test_registers_once_and_follows_the_job(self):
        queue = self.Queue()
        server = types.SimpleNamespace(prompt_queue=queue)
        runs = mock.Mock()
        self.assertTrue(register_cached_runs(server, lambda: "db", runs))
        self.assertFalse(register_cached_runs(server, lambda: "db", runs))
        queue.result = (("item",), 5)
        queue.get()
        runs.begin_attempt.assert_called_once_with((("item",), 5))
        executor = self.modules["execution"].PromptExecutor()
        executor.server = server
        executor.add_message("execution_start", {"prompt_id": "j"}, broadcast=False)
        executor.add_message(
            "execution_cached", {"prompt_id": "j", "nodes": ["n"]}, broadcast=False
        )
        runs.replay.assert_called_once()
        self.assertEqual(runs.replay.call_args.args[1:3], ("j", ["n"]))
        other = self.modules["execution"].PromptExecutor()
        other.server = types.SimpleNamespace(prompt_queue=object())
        other.add_message("execution_cached", {"prompt_id": "j", "nodes": []}, False)
        self.assertEqual(runs.replay.call_count, 1)
        queue.task_done(5, None, status=None)
        runs.end_attempt.assert_called_once_with(5)

    def test_hosts_without_the_interfaces_register_nothing(self):
        class NoTaskDone:
            def get(self):
                return None

        self.assertFalse(
            register_cached_runs(
                types.SimpleNamespace(prompt_queue=NoTaskDone()), lambda: None
            )
        )
        self.assertFalse(register_cached_runs(types.SimpleNamespace(), lambda: None))
        del self.modules["execution"].PromptExecutor.add_message
        self.assertFalse(
            register_cached_runs(
                types.SimpleNamespace(prompt_queue=self.Queue()), lambda: None
            )
        )


if __name__ == "__main__":
    unittest.main()
