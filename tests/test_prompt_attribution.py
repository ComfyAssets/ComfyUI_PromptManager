"""#342: an image is linked only to a prompt that provably produced it.

The maintainer's run: an LLM writes the prompt into a PromptManagerPair's
``prepend_text`` while its ``text`` editor is empty. Prepend and append text are
never stored as the prompt (product decision), so nothing is saved, and the
image must stay unlinked instead of taking the newest prompt in the database or
a stale tracker entry.
"""

import copy
import logging
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from tests.background_stops import close_handler  # noqa: E402
from database.operations import PromptDatabase
from prompt_manager import PromptManager
from prompt_manager_pair import PromptManagerPair
from prompt_manager_text import PromptManagerText
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler
from utils.prompt_tracker import PromptTracker
from utils.run_keys import image_run_keys, prompt_node_key
from utils.usage_tracking import (
    PendingFirstUse,
    queued_pair_runs,
    queued_prompt_runs,
)

LLM_TEXT = "Photorealistic aerial landscape of misty mountains at dawn"
EVIDENCE_KEY = "comfyassets_prompt_pairs"


def issue_graph(text="", seed=1):
    """The workflow from #342 (subgraph ids kept), PromptManagerPair positive."""
    return {
        "18:35": {
            "class_type": "PromptManagerPair",
            "inputs": {
                "text": text,
                "negative_text": "",
                "prepend_text": ["18:13", 0],
                "append_text": "",
                "category": "",
                "tags": "",
                "clip": ["4", 1],
            },
        },
        "18:13": {"class_type": "PreviewAny", "inputs": {"source": ["18:16", 0]}},
        "18:16": {
            "class_type": "ComfySwitchNode",
            "inputs": {
                "switch": True,
                "on_false": ["18:17", 0],
                "on_true": ["18:11", 0],
            },
        },
        "18:11": {
            "class_type": "TextGenerate",
            "inputs": {"prompt": ["18:17", 0], "seed": 7},
        },
        "18:17": {
            "class_type": "PrimitiveStringMultiline",
            "inputs": {"value": "Describe an aerial mountain landscape"},
        },
        "18:5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["4", 1]},
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "moss.safetensors"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["18:35", 0],
                "negative": ["18:5", 0],
                "latent_image": ["6", 0],
                "seed": seed,
            },
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1}},
    }


def llm_text_graph(seed=1):
    """An LLM feeding a PromptManager's ``text``: stored, but not in the metadata."""
    graph = issue_graph(seed=seed)
    graph["18:35"] = {
        "class_type": "PromptManager",
        "inputs": {"text": ["18:13", 0], "clip": ["4", 1]},
    }
    return graph


def pair_evidence(text="", prepend=LLM_TEXT):
    """The execution record PromptManagerPair embeds in the saved image."""
    return {
        "version": 1,
        "nodes": {
            "18:35": {
                "records": [
                    {
                        "text": text,
                        "negative_text": "",
                        "positive_text": " ".join(p for p in (prepend, text) if p),
                        "negative_effective_text": "",
                        "prepend_text": prepend,
                        "append_text": "",
                    }
                ]
            }
        },
    }


def make_tracker(db):
    """A real tracker without its cleanup thread or config import."""
    with mock.patch.object(PromptTracker, "__init__", lambda self, db: None):
        tracker = PromptTracker.__new__(PromptTracker)
    tracker.logger = logging.getLogger("test.prompt_attribution.tracker")
    tracker.db_manager = db
    tracker._local = threading.local()
    tracker.active_prompts = {}
    tracker.lock = threading.Lock()
    tracker._prompt_queue = []
    tracker._queue_lock = threading.Lock()
    tracker.cleanup_interval = 300
    tracker.prompt_timeout = 600
    return tracker


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.db = PromptDatabase(os.path.join(self.tmp.name, "prompts.db"))
        self.addCleanup(self.db.close_all)
        self.tracker = make_tracker(self.db)

    def save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))


class NodeCase(DatabaseCase):
    def node(self, cls):
        node = cls.__new__(cls)
        node.db = self.db
        node.logger = logging.getLogger("test.prompt_attribution.node")
        node.prompt_tracker = self.tracker
        node.comfyui_integration = mock.Mock()
        node._inject_lora_trigger_words = lambda text: text
        node.pending_first_use = PendingFirstUse()
        node.pending_pair_first_use = PendingFirstUse()
        return node

    def clip(self):
        clip = mock.Mock()
        clip.tokenize.side_effect = lambda text: text
        clip.encode_from_tokens_scheduled.side_effect = lambda t: ("cond", t)
        return clip

    def count_rows(self, table):
        conn = self.db.model.get_connection()
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestEmptyEditorSavesNothing(NodeCase):
    """Prepend/append are never the stored prompt; an empty editor saves nothing."""

    def test_pair_with_only_prepend_text_saves_nothing_and_logs_info(self):
        graph = issue_graph()
        with self.assertLogs("test.prompt_attribution.node", "INFO") as logs:
            result = self.node(PromptManagerPair).encode_prompt(
                self.clip(),
                "",
                negative_text="",
                prepend_text=LLM_TEXT,
                prompt=graph,
                unique_id="18:35",
            )
        self.assertEqual(result[2], LLM_TEXT, "the composed text is still encoded")
        self.assertEqual(self.count_rows("prompts"), 0)
        self.assertEqual(self.count_rows("prompt_pairs"), 0)
        self.assertFalse(
            any(record.levelno >= logging.WARNING for record in logs.records),
            [record.getMessage() for record in logs.records],
        )
        self.assertTrue(any("Prompt text is empty" in line for line in logs.output))

    def test_prompt_manager_and_text_nodes_with_only_prepend_save_nothing(self):
        graph = issue_graph()
        graph["18:35"] = {
            "class_type": "PromptManager",
            "inputs": {"text": "", "prepend_text": ["18:13", 0], "clip": ["4", 1]},
        }
        self.node(PromptManager).encode_prompt(
            self.clip(), "", prepend_text=LLM_TEXT, prompt=graph, unique_id="18:35"
        )
        graph["18:35"]["class_type"] = "PromptManagerText"
        self.node(PromptManagerText).process_text(
            "", prepend_text=LLM_TEXT, prompt=graph, unique_id="18:35"
        )
        self.assertEqual(self.count_rows("prompts"), 0)

    def test_empty_editor_marks_its_run_key_so_no_older_entry_is_used(self):
        graph = issue_graph()
        key = prompt_node_key(graph, "18:35")
        older = self.save("a woman holding a coffee cup")
        self.tracker.set_current_prompt(
            "a woman holding a coffee cup",
            {"prompt_id": older},
            push_to_queue=True,
            run_key=key,
            run_id="run-1",
        )
        self.node(PromptManagerPair).encode_prompt(
            self.clip(), "", prepend_text=LLM_TEXT, prompt=graph, unique_id="18:35"
        )
        latest, _run, _count = self.tracker.keyed_snapshot()[key]
        self.assertTrue(latest.get("placeholder"))
        self.assertIsNone(self.tracker.pop_for_key(key, "another run"))
        popped = self.tracker.pop_for_key(key)
        self.assertEqual(popped["id"], older, "an earlier queued item stays first")
        self.assertTrue(self.tracker.pop_for_key(key).get("placeholder"))

    def test_empty_negative_node_is_not_marked(self):
        graph = issue_graph(text="a typed positive")
        graph["18:5"] = {
            "class_type": "PromptManager",
            "inputs": {"text": "", "clip": ["4", 1]},
        }
        self.node(PromptManager).encode_prompt(
            self.clip(), "", prompt=graph, unique_id="18:5"
        )
        self.assertEqual(self.tracker.keyed_snapshot(), {})

    def test_queue_hook_counts_nothing_for_an_empty_editor(self):
        graph = issue_graph()
        self.assertEqual(queued_prompt_runs(graph), [])
        self.assertEqual(queued_pair_runs(graph), [])

    def test_typed_text_with_prepend_still_stores_only_the_editor_text(self):
        graph = issue_graph(text="mountains")
        self.node(PromptManagerPair).encode_prompt(
            self.clip(),
            "mountains",
            prepend_text=LLM_TEXT,
            prompt=graph,
            unique_id="18:35",
        )
        self.assertIsNotNone(
            self.db.get_prompt_by_hash(generate_prompt_hash("mountains"))
        )
        self.assertIsNone(self.db.get_prompt_by_hash(generate_prompt_hash(LLM_TEXT)))
        context, _run, _count = self.tracker.keyed_snapshot()[
            prompt_node_key(graph, "18:35")
        ]
        self.assertEqual(context["text"], f"{LLM_TEXT} mountains")


class MonitorCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        fd, self.image = tempfile.mkstemp(suffix=".png", dir=self.tmp.name)
        os.close(fd)
        self.handler = ImageGenerationHandler(self.db, self.tracker)
        self.addCleanup(close_handler, self, self.handler)
        self.linked = []
        self.handler.link_image_to_prompt = lambda path, prompt, meta: (
            self.linked.append(prompt["id"])
        )

    def process(self, metadata, snapshot=None):
        before = len(self.linked)
        with mock.patch.object(
            self.handler.metadata_extractor, "extract_metadata", return_value=metadata
        ):
            self.handler.process_new_image(self.image, prompt_snapshot=snapshot)
        return self.linked[-1] if len(self.linked) > before else None


class TestIssueReproduction(MonitorCase):
    def test_llm_prepend_image_is_left_unlinked_not_given_the_newest_prompt(self):
        """Failing before #342: linked to the newest prompt in the database."""
        self.save("a woman holding a coffee cup")
        metadata = {"prompt": issue_graph(), EVIDENCE_KEY: pair_evidence()}
        with self.assertLogs("prompt_manager.image_monitor", "INFO") as logs:
            self.assertIsNone(self.process(metadata))
        self.assertTrue(any("left unlinked" in line for line in logs.output))

    def test_a_stale_entry_of_another_workflow_is_never_used(self):
        """Failing before #342: the live tracker's entry was taken."""
        stale = self.save("a woman holding a coffee cup")
        other = issue_graph(text="a woman holding a coffee cup")
        self.tracker.set_current_prompt(
            "a woman holding a coffee cup",
            {"prompt_id": stale},
            push_to_queue=True,
            run_key=prompt_node_key(other, "18:35"),
            run_id="run-0",
        )
        metadata = {"prompt": issue_graph(), EVIDENCE_KEY: pair_evidence()}
        self.assertIsNone(self.process(metadata, self.tracker.keyed_snapshot()))
        self.assertIsNone(self.process(metadata))
        self.assertEqual(len(self.tracker._prompt_queue), 1, "not consumed")

    def test_an_entry_without_a_run_key_is_never_used(self):
        stale = self.save("previous run")
        self.tracker.set_current_prompt("previous run", {"prompt_id": stale})
        self.assertIsNone(self.process({"prompt": llm_text_graph()}))

    def test_an_image_without_metadata_is_left_unlinked(self):
        """Failing before #342: popped the queue, else took the newest prompt."""
        self.save("latest")
        self.assertIsNone(self.process(None))

    def test_the_recency_fallback_is_gone(self):
        self.assertFalse(hasattr(self.handler, "_get_fallback_prompt"))


class TestProvenRuns(MonitorCase):
    """Entries the image's own graph proves still link (LLM into ``text``)."""

    def track(self, graph, text, run_id="run-1", push=True):
        prompt_id = (
            self.save(text)
            if not self.db.get_prompt_by_hash(generate_prompt_hash(text))
            else self.db.get_prompt_by_hash(generate_prompt_hash(text))["id"]
        )
        self.tracker.set_current_prompt(
            text,
            {"prompt_id": prompt_id},
            push_to_queue=push,
            run_key=prompt_node_key(graph, "18:35"),
            run_id=run_id,
        )
        return prompt_id

    def test_queued_entry_of_the_same_run_key_links(self):
        graph = llm_text_graph()
        prompt_id = self.track(graph, LLM_TEXT)
        self.assertEqual(self.process({"prompt": graph}), prompt_id)

    def test_cached_node_rerun_links_through_the_snapshot(self):
        graph = llm_text_graph(seed=1)
        prompt_id = self.track(graph, LLM_TEXT)
        self.assertEqual(self.process({"prompt": graph}), prompt_id)
        # Only the sampler seed changed: the node was cached and did not run,
        # but its upstream (the run key) is unchanged.
        rerun = llm_text_graph(seed=2)
        self.assertEqual(
            prompt_node_key(rerun, "18:35"), prompt_node_key(graph, "18:35")
        )
        self.assertEqual(
            self.process({"prompt": rerun}, self.tracker.keyed_snapshot()), prompt_id
        )

    def test_snapshot_needs_a_known_run(self):
        graph = llm_text_graph()
        self.track(graph, LLM_TEXT, run_id=None, push=False)
        self.assertIsNone(self.process({"prompt": graph}))

    def test_batch_items_link_in_order_and_the_latest_alone_proves_nothing(self):
        graph = llm_text_graph()
        first = self.track(graph, "item one")
        second = self.track(graph, "item two")
        self.assertEqual(self.process({"prompt": graph}), first)
        self.assertEqual(self.process({"prompt": graph}), second)
        # Queue drained: two entries in one run, so the latest is not proof
        self.assertIsNone(self.process({"prompt": graph}))

    def test_an_empty_batch_item_keeps_the_queue_aligned(self):
        graph = llm_text_graph()
        key = prompt_node_key(graph, "18:35")
        first = self.track(graph, "item one")
        self.tracker.mark_empty(key, "run-1", push_to_queue=True)
        third = self.track(graph, "item three")
        self.assertEqual(self.process({"prompt": graph}), first)
        self.assertIsNone(self.process({"prompt": graph}))
        self.assertEqual(self.process({"prompt": graph}), third)

    def test_a_newer_run_drops_stale_entries_of_the_same_key(self):
        graph = llm_text_graph()
        # run-1 saved no image, so its entry was never taken
        self.track(graph, "earlier run text", run_id="run-1")
        newer = self.track(graph, "newer run text", run_id="run-2")
        self.assertEqual(self.process({"prompt": graph}), newer)
        self.assertEqual(self.tracker._prompt_queue, [])

    def test_a_later_run_queued_before_processing_keeps_its_own_entry(self):
        # The image appears (snapshot), the next queued run executes the same
        # node again before the monitor's delay is over, then the image is
        # processed: it takes its own run's entry, the later run keeps its own.
        graph = llm_text_graph()
        own = self.track(graph, "first run text", run_id="run-1")
        snapshot = self.tracker.keyed_snapshot()
        later = self.track(graph, "second run text", run_id="run-2")
        self.assertEqual(self.process({"prompt": graph}, snapshot), own)
        self.assertEqual(self.process({"prompt": graph}), later)

    def test_no_entry_when_the_image_appeared_means_no_queue_match(self):
        graph = llm_text_graph()
        empty_snapshot = self.tracker.keyed_snapshot()
        self.track(graph, "queued after the image appeared")
        self.assertIsNone(self.process({"prompt": graph}, empty_snapshot))
        self.assertEqual(len(self.tracker._prompt_queue), 1)

    def test_hosts_without_run_ids_still_link_queued_entries_in_order(self):
        graph = llm_text_graph()
        first = self.track(graph, "item one", run_id=None)
        second = self.track(graph, "item two", run_id=None)
        self.assertEqual(self.process({"prompt": graph}), first)
        self.assertEqual(self.process({"prompt": graph}), second)
        self.assertIsNone(self.process({"prompt": graph}))

    def test_other_keys_stay_queued_for_their_own_images(self):
        mine, other = llm_text_graph(), copy.deepcopy(llm_text_graph())
        other["18:17"]["inputs"]["value"] = "Describe a beach"
        theirs = self.track(other, "a beach")
        prompt_id = self.track(mine, LLM_TEXT)
        self.assertEqual(self.process({"prompt": mine}), prompt_id)
        self.assertEqual(self.process({"prompt": other}), theirs)

    def test_two_positive_prompt_nodes_are_not_guessed(self):
        graph = llm_text_graph()
        self.track(graph, LLM_TEXT)
        graph["30"] = {
            "class_type": "PromptManager",
            "inputs": {"text": ["18:13", 0], "clip": ["4", 1]},
        }
        graph["31"] = dict(graph["3"])
        graph["31"]["inputs"] = {**graph["3"]["inputs"], "positive": ["30", 0]}
        self.assertEqual(len(image_run_keys({"prompt": graph})), 2)
        self.assertIsNone(self.process({"prompt": graph}))

    def test_metadata_still_wins_and_leaves_the_queue_alone(self):
        typed = self.save("a typed prompt")
        graph = issue_graph(text="a typed prompt")
        self.tracker.set_current_prompt(
            "x",
            {"prompt_id": typed},
            run_key=prompt_node_key(graph, "18:35"),
            run_id="r",
        )
        self.assertEqual(self.process({"prompt": graph}), typed)
        self.assertEqual(len(self.tracker._prompt_queue), 1)


class TestRunKeys(unittest.TestCase):
    def test_key_covers_the_upstream_and_ignores_downstream_and_titles(self):
        graph = issue_graph()
        key = prompt_node_key(graph, "18:35")
        changed = copy.deepcopy(graph)
        changed["3"]["inputs"]["seed"] = 99
        changed["18:35"]["_meta"] = {"title": "renamed"}
        self.assertEqual(prompt_node_key(changed, "18:35"), key)
        changed["18:11"]["inputs"]["seed"] = 8
        self.assertNotEqual(prompt_node_key(changed, "18:35"), key)

    def test_unknowable_keys_are_none(self):
        graph = issue_graph()
        self.assertIsNone(prompt_node_key(None, "1"))
        self.assertIsNone(prompt_node_key(graph, None))
        self.assertIsNone(prompt_node_key(graph, "missing"))
        broken = copy.deepcopy(graph)
        broken["18:13"]["inputs"]["source"] = ["gone", 0]
        self.assertIsNone(prompt_node_key(broken, "18:35"))
        broken = copy.deepcopy(graph)
        broken["18:17"]["inputs"]["value"] = object()
        self.assertIsNone(prompt_node_key(broken, "18:35"))

    def test_image_keys_read_string_graphs_and_reject_garbage(self):
        import json

        graph = issue_graph()
        self.assertEqual(
            image_run_keys({"prompt": json.dumps(graph)}),
            {"18:35": prompt_node_key(graph, "18:35")},
        )
        self.assertEqual(image_run_keys({"prompt": "{not json"}), {})
        self.assertEqual(image_run_keys(None), {})
        self.assertEqual(image_run_keys({"prompt": []}), {})

    def test_cyclic_and_huge_closures_have_no_key(self):
        from utils.prompt_graph import MAX_VISITS

        cyclic = {
            "1": {"class_type": "A", "inputs": {"x": ["2", 0]}},
            "2": {"class_type": "B", "inputs": {"x": ["1", 0]}},
        }
        self.assertIsNotNone(prompt_node_key(cyclic, "1"))
        chain = {
            str(i): {"class_type": "N", "inputs": {"x": [str(i + 1), 0]}}
            for i in range(MAX_VISITS + 1)
        }
        chain[str(MAX_VISITS + 1)] = {"class_type": "N", "inputs": {}}
        self.assertIsNone(prompt_node_key(chain, "0"))


class TestReviewFindings(MonitorCase, NodeCase):
    """Final-review findings M1-M4 (#344)."""

    def test_m1_a_positive_node_without_a_key_makes_the_image_unkeyable(self):
        graph = llm_text_graph()
        graph["30"] = {
            "class_type": "PromptManager",
            "inputs": {"text": ["missing", 0], "clip": ["4", 1]},
        }
        graph["31"] = {
            "class_type": "KSampler",
            "inputs": {**graph["3"]["inputs"], "positive": ["30", 0]},
        }
        self.assertIsNone(prompt_node_key(graph, "30"))
        self.assertEqual(image_run_keys({"prompt": graph}), {})
        prompt_id = self.save(LLM_TEXT)
        self.tracker.set_current_prompt(
            LLM_TEXT,
            {"prompt_id": prompt_id},
            run_key=prompt_node_key(graph, "18:35"),
            run_id="run-1",
        )
        self.assertIsNone(self.process({"prompt": graph}))

    def test_m2_links_are_exactly_comfyui_is_link(self):
        from utils.run_keys import _links

        inputs = {
            "string_slot_int": ["7", 0],
            "string_slot_float": ["7", 0.0],
            "string_slot_bool": ["7", True],  # bool is an int to ComfyUI too
            "int_source": [7, 0],
            "tuple": ("7", 0),
            "three": ["7", 0, 1],
            "slot_text": ["7", "0"],
        }
        self.assertEqual(sorted(_links(inputs)), ["7", "7", "7"])
        widget = issue_graph()
        widget["18:17"]["inputs"]["size"] = [512, 768]  # a value, not a link
        self.assertIsNotNone(prompt_node_key(widget, "18:35"))
        floated = issue_graph()
        floated["18:35"]["inputs"]["prepend_text"] = ["18:13", 0.0]
        changed = copy.deepcopy(floated)
        changed["18:17"]["inputs"]["value"] = "another instruction"
        self.assertNotEqual(
            prompt_node_key(floated, "18:35"), prompt_node_key(changed, "18:35")
        )

    def _failing_save(self, node):
        def explode(*args, **kwargs):
            raise RuntimeError("database is locked")

        node.db = mock.Mock(wraps=self.db)
        for name in ("save_prompt", "save_prompt_pair", "get_prompt_by_hash"):
            setattr(node.db, name, mock.Mock(side_effect=explode))
        return node

    def test_m3_a_failed_save_takes_over_its_run_key(self):
        graph = llm_text_graph()
        key = prompt_node_key(graph, "18:35")
        older = self.save("previous run text")
        self.tracker.set_current_prompt(
            "previous run text", {"prompt_id": older}, run_key=key, run_id="run-1"
        )
        node = self._failing_save(self.node(PromptManager))
        with mock.patch("prompt_manager_base.executing_run_id", return_value="run-2"):
            node.encode_prompt(
                self.clip(), "new run text", prompt=graph, unique_id="18:35"
            )
        latest, run_id, _count = self.tracker.keyed_snapshot()[key]
        self.assertTrue(latest.get("placeholder"))
        self.assertEqual(run_id, "run-2")
        self.assertIsNone(self.process({"prompt": graph}))

    def test_m3_a_failed_pair_save_takes_over_its_run_key(self):
        graph = issue_graph(text="typed")
        key = prompt_node_key(graph, "18:35")
        node = self._failing_save(self.node(PromptManagerPair))
        with self.assertLogs("test.prompt_attribution.node", "WARNING"):
            node.encode_prompt(self.clip(), "typed", prompt=graph, unique_id="18:35")
        self.assertTrue(self.tracker.keyed_snapshot()[key][0].get("placeholder"))

    def test_m3_a_failed_text_node_save_takes_over_its_run_key(self):
        graph = issue_graph()
        graph["18:35"] = {
            "class_type": "PromptManagerText",
            "inputs": {"text": "typed"},
        }
        graph["40"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["18:35", 0], "clip": ["4", 1]},
        }
        graph["3"]["inputs"]["positive"] = ["40", 0]
        node = self._failing_save(self.node(PromptManagerText))
        with mock.patch.object(
            node, "_track_prompt_execution", side_effect=RuntimeError("boom")
        ):
            node.process_text("typed", prompt=graph, unique_id="18:35")
        key = prompt_node_key(graph, "18:35")
        self.assertTrue(self.tracker.keyed_snapshot()[key][0].get("placeholder"))

    def test_m4_a_placeholder_never_falls_through_to_history(self):
        graph = llm_text_graph()
        key = prompt_node_key(graph, "18:35")
        self.tracker.mark_empty(key, "run-1", push_to_queue=False)
        with mock.patch.object(
            self.handler, "_history_prompt", return_value=99
        ) as history:
            self.assertIsNone(self.process({"prompt": graph}))  # snapshot form
            self.tracker.mark_empty(key, "run-2", push_to_queue=True)
            self.assertIsNone(self.process({"prompt": graph}))  # popped form
        history.assert_not_called()


class TestRereviewFindings(MonitorCase, NodeCase):
    """Re-review findings R4 and R5 (#344)."""

    def test_r4_a_logging_failure_after_tracking_never_doubles_a_queue_slot(self):
        graph = llm_text_graph()
        key = prompt_node_key(graph, "18:35")
        node = self.node(PromptManager)
        logger = mock.Mock(wraps=self.tracker.logger)
        logger.debug.side_effect = RuntimeError("log handler broke")
        self.tracker.logger = logger
        node.encode_prompt(self.clip(), LLM_TEXT, prompt=graph, unique_id="18:35")
        queued = [ctx for ctx in self.tracker._prompt_queue if ctx["run_key"] == key]
        self.assertEqual(len(queued), 1)

    def test_r5_a_keyless_image_outside_the_output_folder_does_not_wait(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, True))
        stray = os.path.join(outside, "copied.png")
        with open(stray, "wb") as handle:
            handle.write(b"png")
        with mock.patch(
            "utils.image_monitor.host_output_directory", return_value=self.tmp.name
        ):
            with mock.patch.object(
                self.handler.metadata_extractor, "extract_metadata", return_value=None
            ):
                self.handler.process_new_image(stray)
                self.handler.process_new_image(self.image)  # inside: waits
        self.assertEqual(
            self.handler.awaiting_history(),
            [os.path.normcase(os.path.realpath(self.image))],
        )


if __name__ == "__main__":
    unittest.main()
