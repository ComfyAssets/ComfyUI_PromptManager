"""#90 item 3: a node's ``save_to_library`` switch.

Off: PromptManager ignores the run completely (no prompt saved, no run count,
no image link), while the node still returns its normal outputs. On (the
default, and every workflow or image made before the switch existed): as before.
"""

import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_prompt_attribution import MonitorCase, NodeCase
    from tests.test_execution_contexts import load_module
except ImportError:  # discovered with tests/ as the top-level directory
    from test_prompt_attribution import MonitorCase, NodeCase
    from test_execution_contexts import load_module

from prompt_manager import PromptManager, PromptManagerFocused, PromptManagerSplit
from prompt_manager_pair import PromptManagerPair
from prompt_manager_text import PromptManagerText
from prompt_search_list import PromptSearchList
from utils.hashing import generate_prompt_hash
from utils.prompt_graph import (
    intrinsic_pair_nodes,
    positive_opt_outs,
    run_prompt_components,
    run_prompt_nodes,
    save_setting,
)
from utils.run_keys import prompt_node_key
from utils.usage_tracking import (
    PendingFirstUse,
    QueuedRuns,
    handle_queued_prompt,
    on_dequeue,
    queued_pair_runs,
    queued_prompt_runs,
)

POSITIVE = "a lighthouse at dusk"
NEGATIVE = "blurry"
NODE_CLASSES = (
    PromptManager,
    PromptManagerText,
    PromptManagerFocused,
    PromptManagerSplit,
    PromptManagerPair,
)


def run_graph(save=None, kind="PromptManager", negative_save=None):
    """A PromptManager positive feeding a KSampler; ``save`` None leaves it out."""
    inputs = {"text": POSITIVE, "clip": ["4", 1]}
    if save is not None:
        inputs["save_to_library"] = save
    negative_inputs = {"text": NEGATIVE, "clip": ["4", 1]}
    if negative_save is not None:
        negative_inputs["save_to_library"] = negative_save
    return {
        "1": {"class_type": kind, "inputs": inputs},
        "2": {"class_type": "PromptManager", "inputs": negative_inputs},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["1", 0],
                "negative": ["2", 0],
                "latent_image": ["6", 0],
            },
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1}},
    }


def pair_graph(save=None):
    inputs = {"text": POSITIVE, "negative_text": NEGATIVE, "clip": ["4", 1]}
    if save is not None:
        inputs["save_to_library"] = save
    return {
        "1": {"class_type": "PromptManagerPair", "inputs": inputs},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["1", 0],
                "negative": ["1", 1],
                "latent_image": ["6", 0],
            },
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1}},
    }


class TestInputDeclaration(unittest.TestCase):
    def test_every_prompt_manager_node_declares_a_boolean_defaulting_on(self):
        for cls in NODE_CLASSES:
            optional = cls.INPUT_TYPES()["optional"]
            kind, options = optional["save_to_library"]
            self.assertEqual(kind, "BOOLEAN", cls.__name__)
            self.assertIs(options["default"], True, cls.__name__)
            # Declared last: no saved widget value of an older workflow shifts
            self.assertEqual(list(optional)[-1], "save_to_library", cls.__name__)

    def test_search_list_has_no_switch(self):
        inputs = PromptSearchList.INPUT_TYPES()
        self.assertNotIn("save_to_library", inputs.get("optional", {}))
        self.assertNotIn("save_to_library", inputs.get("required", {}))


class TestSaveSetting(unittest.TestCase):
    def test_missing_input_is_on(self):
        self.assertIs(save_setting(run_graph(), "1"), True)

    def test_literals_follow_comfyui_boolean_coercion(self):
        for value, expected in (
            (True, True),
            (False, False),
            ("", False),
            (0, False),
            ({"__value__": False}, False),
            ({"__value__": True}, True),
        ):
            with self.subTest(value=value):
                self.assertIs(save_setting(run_graph(save=value), "1"), expected)

    def test_link_to_primitive_boolean_is_read(self):
        for value in (True, False):
            graph = run_graph(save=["9", 0])
            graph["9"] = {"class_type": "PrimitiveBoolean", "inputs": {"value": value}}
            self.assertIs(save_setting(graph, "1"), value)

    def test_link_to_an_unknown_node_is_not_knowable(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        self.assertIsNone(save_setting(graph, "1"))
        self.assertIsNone(save_setting(run_graph(save=["missing", 0]), "1"))


class TestGraphRoles(unittest.TestCase):
    def test_opted_out_positive_is_not_a_run_prompt(self):
        graph = run_graph(save=False)
        self.assertEqual(run_prompt_nodes(graph), [])
        self.assertEqual(run_prompt_components(graph), [])
        self.assertEqual(positive_opt_outs(graph), ["1"])

    def test_enabled_positive_is_unchanged(self):
        for save in (None, True):
            graph = run_graph(save=save)
            self.assertEqual(run_prompt_nodes(graph), ["1"])
            self.assertEqual(positive_opt_outs(graph), [])

    def test_opted_out_negative_is_no_positive_opt_out(self):
        graph = run_graph(negative_save=False)
        self.assertEqual(run_prompt_nodes(graph), ["1"])
        self.assertEqual(positive_opt_outs(graph), [])

    def test_walk_stops_at_an_opted_out_node(self):
        graph = run_graph(save=False)
        graph["1"]["inputs"]["text"] = ["7", 0]
        graph["7"] = {"class_type": "PromptManagerText", "inputs": {"text": "x"}}
        self.assertEqual(run_prompt_nodes(graph), [])

    def test_sampler_less_fallback_skips_opted_out_nodes(self):
        graph = {
            "1": {
                "class_type": "PromptManagerText",
                "inputs": {"text": "a", "save_to_library": False},
            },
            "2": {"class_type": "PromptManagerText", "inputs": {"text": "b"}},
        }
        self.assertEqual(run_prompt_nodes(graph), ["2"])
        self.assertEqual(run_prompt_components(graph), [("2", "text")])
        self.assertEqual(positive_opt_outs(graph), ["1"])

    def test_opted_out_pair_is_not_an_intrinsic_pair(self):
        self.assertEqual(intrinsic_pair_nodes(pair_graph()), ["1"])
        self.assertEqual(intrinsic_pair_nodes(pair_graph(save=False)), [])


class TestQueueHook(unittest.TestCase):
    def test_opted_out_node_is_never_stashed(self):
        self.assertEqual(queued_prompt_runs(run_graph(save=False)), [])
        self.assertEqual(queued_pair_runs(pair_graph(save=False)), [])

    def test_unknown_setting_is_left_to_the_node(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        self.assertEqual(queued_prompt_runs(graph), [])
        pair = pair_graph(save=["9", 0])
        pair["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        self.assertEqual(queued_pair_runs(pair), [])

    def test_primitive_boolean_on_still_counts(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "PrimitiveBoolean", "inputs": {"value": True}}
        self.assertEqual(
            queued_prompt_runs(graph), [(generate_prompt_hash(POSITIVE), POSITIVE)]
        )

    def test_dequeue_counts_nothing_for_a_cached_opted_out_node(self):
        """A cached node never executes: only the queued graph can say it is off."""
        db = mock.Mock()
        db.get_prompt_by_hash.return_value = {"id": 5}
        queued, pending = QueuedRuns(), PendingFirstUse()
        request = {"prompt": run_graph(save=False), "prompt_id": "job-1"}
        handle_queued_prompt(request, queued, pair_queued=QueuedRuns())
        on_dequeue("job-1", queued, lambda: db, pending, pair_queued=QueuedRuns())
        db.record_prompt_use.assert_not_called()
        self.assertEqual(pending.consume(generate_prompt_hash(POSITIVE)), 0)


class TestNodesSaveNothing(NodeCase):
    def run_node(self, cls, graph, save):
        node = self.node(cls)
        node.record_pair = mock.Mock()
        kwargs = {"prompt": graph, "unique_id": "1", "save_to_library": save}
        if cls is PromptManagerText:
            return node, node.process_text(POSITIVE, **kwargs)
        if cls is PromptManagerPair:
            return node, node.encode_prompt(
                self.clip(), POSITIVE, negative_text=NEGATIVE, **kwargs
            )
        return node, node.encode_prompt(self.clip(), POSITIVE, **kwargs)

    def test_off_saves_counts_and_tracks_nothing_but_returns_outputs(self):
        for cls in NODE_CLASSES:
            with self.subTest(node=cls.__name__):
                graph = (
                    pair_graph(save=False)
                    if cls is PromptManagerPair
                    else run_graph(save=False, kind=cls.__name__)
                )
                node, result = self.run_node(cls, graph, False)
                self.assertEqual(self.count_rows("prompts"), 0)
                self.assertEqual(self.count_rows("prompt_pairs"), 0)
                self.assertEqual(self.tracker.keyed_snapshot(), {})
                self.assertEqual(self.tracker._prompt_queue, [])
                node.comfyui_integration.register_prompt.assert_not_called()
                if cls is PromptManagerText:
                    self.assertEqual(result, (POSITIVE,))
                elif cls is PromptManagerPair:
                    self.assertEqual(result[0], ("cond", POSITIVE))
                    self.assertEqual(result[1], ("cond", NEGATIVE))
                    self.assertEqual(result[2:], (POSITIVE, NEGATIVE))
                else:
                    self.assertEqual(result, (("cond", POSITIVE), POSITIVE))

    def test_off_without_graph_context_still_saves_nothing(self):
        node = self.node(PromptManager)
        node.encode_prompt(self.clip(), POSITIVE, save_to_library=False)
        self.assertEqual(self.count_rows("prompts"), 0)
        self.assertEqual(self.tracker._prompt_queue, [])

    def test_off_leaves_an_existing_prompt_uncounted(self):
        prompt_id = self.save(POSITIVE)
        self.run_node(PromptManager, run_graph(save=False), False)
        row = self.db.get_prompt_by_id(prompt_id)
        self.assertFalse(row.get("run_count") or 0)

    def test_on_is_unchanged(self):
        self.run_node(PromptManager, run_graph(save=True), True)
        self.assertIsNotNone(self.db.get_prompt_by_hash(generate_prompt_hash(POSITIVE)))
        self.assertIn(
            prompt_node_key(run_graph(save=True), "1"), self.tracker.keyed_snapshot()
        )

    def test_unknown_setting_on_at_run_time_counts_at_execution(self):
        """The hook could not read a wired switch, so the node counts its run."""
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        prompt_id = self.save(POSITIVE)
        self.run_node(PromptManager, graph, True)
        self.assertEqual(self.db.get_prompt_by_id(prompt_id)["run_count"], 1)


class TestWiredSwitchOffMarksItsRun(NodeCase):
    """Review finding: a wired switch off at run time keeps the node a run prompt."""

    def wired_graph(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        return graph

    def test_an_earlier_entry_of_the_key_never_stands_in(self):
        graph = self.wired_graph()
        key = prompt_node_key(graph, "1")
        node = self.node(PromptManager)
        node.encode_prompt(
            self.clip(), POSITIVE, prompt=graph, unique_id="1", save_to_library=True
        )
        node.encode_prompt(
            self.clip(), POSITIVE, prompt=graph, unique_id="1", save_to_library=False
        )
        latest, _run, _count = self.tracker.keyed_snapshot()[key]
        self.assertTrue(latest.get("placeholder"))

    def test_literal_off_marks_nothing(self):
        node = self.node(PromptManager)
        node.encode_prompt(
            self.clip(),
            POSITIVE,
            prompt=run_graph(save=False),
            unique_id="1",
            save_to_library=False,
        )
        self.assertEqual(self.tracker.keyed_snapshot(), {})


class TestMonitorLeavesOptedOutFilesUnlinked(MonitorCase):
    def test_existing_prompt_is_not_linked_from_metadata(self):
        self.save(POSITIVE)
        self.assertIsNone(self.process({"prompt": run_graph(save=False)}))

    def test_find_prompt_reports_opted_out(self):
        self.save(POSITIVE)
        state, prompt = self.handler._find_prompt_from_metadata(
            {"prompt": run_graph(save=False)}
        )
        self.assertEqual((state, prompt), ("opted_out", None))

    def test_mixed_positive_settings_leave_the_file_unlinked(self):
        self.save(POSITIVE)
        graph = run_graph(save=True)
        graph["8"] = {
            "class_type": "PromptManager",
            "inputs": {"text": "other", "save_to_library": False},
        }
        graph["5"] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["8", 0],
                "negative": ["2", 0],
                "latent_image": ["6", 0],
            },
        }
        self.assertIsNone(self.process({"prompt": graph}))

    def test_workflow_only_metadata_with_switch_off_is_opted_out(self):
        self.save(POSITIVE)
        metadata = {
            "text_encoder_nodes": [
                {
                    "type": "PromptManager",
                    "inputs": [],
                    "widgets_values": [POSITIVE, "", "", "", "", "", False],
                }
            ]
        }
        state, _prompt = self.handler._find_prompt_from_metadata(metadata)
        self.assertEqual(state, "opted_out")

    def test_workflow_only_ignores_muted_nodes_and_reads_subgraphs(self):
        from utils.prompt_graph import metadata_opted_out

        node = {
            "type": "PromptManager",
            "widgets_values": [POSITIVE, "", "", "", "", "", False],
        }
        self.assertFalse(
            metadata_opted_out({"workflow": {"nodes": [{**node, "mode": 4}]}})
        )
        self.assertTrue(
            metadata_opted_out(
                {
                    "workflow": {
                        "nodes": [],
                        "definitions": {"subgraphs": [{"nodes": [node]}]},
                    }
                }
            )
        )

    def test_unknown_setting_is_left_to_the_run(self):
        self.save(POSITIVE)
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        state, _prompt = self.handler._find_prompt_from_metadata({"prompt": graph})
        self.assertEqual(state, "unknown")

    def test_enabled_positive_still_links(self):
        prompt_id = self.save(POSITIVE)
        self.assertEqual(self.process({"prompt": run_graph(save=True)}), prompt_id)


class TestLinkKeepsOptedOutNegativeOut(MonitorCase):
    def setUp(self):
        super().setUp()
        # The real link, not the recording stub of MonitorCase
        del self.handler.link_image_to_prompt

    def test_opted_out_negative_is_never_stored_as_a_pair(self):
        prompt_id = self.save(POSITIVE)
        metadata = {"prompt": run_graph(save=True, negative_save=False)}
        self.handler.link_image_to_prompt(self.image, {"id": prompt_id}, metadata)
        self.assertIsNone(self.db.get_prompt_by_hash(generate_prompt_hash(NEGATIVE)))
        self.assertEqual(self.count_pairs(), 0)
        linked = (
            self.db.model.get_connection()
            .execute("SELECT prompt_id FROM generated_images")
            .fetchall()
        )
        self.assertEqual([row[0] for row in linked], [prompt_id])

    def test_opted_out_positive_links_nothing(self):
        prompt_id = self.save(POSITIVE)
        metadata = {"prompt": run_graph(save=False)}
        self.handler.link_image_to_prompt(self.image, {"id": prompt_id}, metadata)
        count = (
            self.db.model.get_connection()
            .execute("SELECT COUNT(*) FROM generated_images")
            .fetchone()[0]
        )
        self.assertEqual(count, 0)

    def count_pairs(self):
        conn = self.db.model.get_connection()
        return conn.execute("SELECT COUNT(*) FROM prompt_pairs").fetchone()[0]


class TestScanSkipsOptedOutFiles(NodeCase):
    def setUp(self):
        super().setUp()
        from py.api import admin

        self.mixin = admin.AdminRoutesMixin.__new__(admin.AdminRoutesMixin)
        self.mixin.db = self.db
        self.mixin.logger = logging.getLogger("test.save_to_library.scan")
        self.file = Path(self.tmp.name) / "out.png"

    def test_opted_out_file_stores_nothing(self):
        status = self.mixin._ingest_scanned_file(
            self.file, {"prompt": run_graph(save=False)}
        )
        self.assertEqual(status, "not_saved")
        self.assertEqual(self.count_rows("prompts"), 0)
        self.assertEqual(self.count_rows("generated_images"), 0)

    def test_opted_out_file_is_never_queued_for_review(self):
        ambiguous = []
        graph = run_graph(save=False)
        graph["5"] = dict(graph["3"])  # a second sampler: ambiguous otherwise
        graph["5"]["inputs"] = {**graph["3"]["inputs"], "positive": ["2", 0]}
        status = self.mixin._ingest_scanned_file(
            self.file, {"prompt": graph}, ambiguous=ambiguous
        )
        self.assertEqual(status, "not_saved")
        self.assertEqual(ambiguous, [])

    def test_opted_out_negative_stores_the_positive_only(self):
        status = self.mixin._ingest_scanned_file(
            self.file, {"prompt": run_graph(save=True, negative_save=False)}
        )
        self.assertEqual(status, "added")
        self.assertIsNotNone(self.db.get_prompt_by_hash(generate_prompt_hash(POSITIVE)))
        self.assertIsNone(self.db.get_prompt_by_hash(generate_prompt_hash(NEGATIVE)))
        self.assertEqual(self.count_rows("prompt_pairs"), 0)

    def test_wired_switch_is_left_to_the_live_run(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        status = self.mixin._ingest_scanned_file(self.file, {"prompt": graph})
        self.assertEqual(status, "not_saved_wired")
        self.assertEqual(self.count_rows("prompts"), 0)

    def test_status_has_a_scan_detail(self):
        from py.api import admin

        group, row, _line = admin._scan_outcome(self.file, [], "not_saved")
        self.assertEqual(group, "no_metadata")
        self.assertIn("does not save", row["detail"])


class TestHistoryRecordsNoText(unittest.TestCase):
    def setUp(self):
        self.m = load_module("execution_context_graph")

    def test_opted_out_role_has_no_sources_and_no_text(self):
        recipe = self.m.project_contexts(run_graph(save=False), None).recipes[0]
        self.assertEqual(recipe.positive.sources, ())
        self.assertIsNone(recipe.positive.inferred)
        self.assertEqual(recipe.positive.reason, "not_saved")
        # The enabled negative is recorded as before
        self.assertEqual(recipe.negative.inferred.raw.value, NEGATIVE)

    def test_unknown_setting_records_no_text_either(self):
        graph = run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        recipe = self.m.project_contexts(graph, None).recipes[0]
        self.assertIsNone(recipe.positive.inferred)
        self.assertEqual(recipe.positive.reason, "not_saved")

    def test_enabled_role_is_unchanged(self):
        recipe = self.m.project_contexts(run_graph(save=True), None).recipes[0]
        self.assertEqual(recipe.positive.inferred.raw.value, POSITIVE)
        self.assertEqual(len(recipe.positive.sources), 1)


if __name__ == "__main__":
    unittest.main()
