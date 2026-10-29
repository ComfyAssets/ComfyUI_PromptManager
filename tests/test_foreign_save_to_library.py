"""#247 PR 2 x #90: the monitor's foreign path never stores switched-off text.

A file whose run's prompt has Save to library off is the opted-out path's,
not the foreign path's; a foreign graph whose ladder decision is
``not_saved`` (a switched-off node the #90 walk does not reach) stores
nothing at settle, at the re-check after the seal, or from recorded history,
and leaves no wait behind.
"""

import os
import unittest

from tests import saver_anchor_graphs as shapes
from tests import test_run_attribution as attribution
from tests import test_save_to_library as save_tests
from tests.test_pair_scan import sampled_graph
from utils.foreign_runs import ForeignRecheck
from utils.hashing import generate_prompt_hash
from py.config import GalleryConfig  # after tests: it stubs the host

LOGGER = "prompt_manager.image_monitor"
OFF_TEXT = "SECRET OFF TEXT"
SKIPPED = "Save to library is off for this run's prompt, skipping image"

# The class dict, not the class: a TestCase bound here would run again
Fixture = vars(attribution.TestMonitorForeignRuns)


def off_behind(middle="MyNode", key="value"):
    """A switched-off PromptManagerText reaching a core CLIPTextEncode through
    ``middle``'s ``key`` input: the #90 walk does not follow ``key``."""
    graph = save_tests.run_graph()
    graph["1"] = {
        "class_type": "PromptManagerText",
        "inputs": {"text": OFF_TEXT, "save_to_library": False},
    }
    graph["7"] = {"class_type": middle, "inputs": {key: ["1", 0]}}
    graph["8"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ["7", 0], "clip": ["4", 1]},
    }
    graph["3"]["inputs"]["positive"] = ["8", 0]
    return graph


def off_text_unwired():
    """Only history can decide; the legacy answer is a dangling switched-off
    node's own text (the ladder says ``not_saved`` once it may ask history)."""
    graph = sampled_graph("unused", "blurry")
    graph["1"]["inputs"]["text"] = ["30", 0]
    graph["30"] = {"class_type": "VendorPromptMaker", "inputs": {"prompt": OFF_TEXT}}
    graph["40"] = {
        "class_type": "PromptManagerText",
        "inputs": {"text": OFF_TEXT, "save_to_library": False},
    }
    return graph


class TestForeignPathKeepsSaveToLibrary(attribution.RecordedRuns, unittest.TestCase):
    setUp = Fixture["setUp"]
    close_databases = Fixture["close_databases"]
    wait_for = Fixture["wait_for"]
    image = Fixture["image"]
    linked_text = Fixture["linked_text"]
    key = Fixture["key"]
    prompt_count = Fixture["prompt_count"]
    settled = Fixture["settled"]

    def reference(self, path):
        return {
            "folder_type": "output",
            "subfolder": "dataset",
            "filename": os.path.basename(path),
        }

    def seal(self, path, positives=(), nodes=("9",)):
        """Record one run reporting ``path`` (a new run id for every call)."""
        self.runs = getattr(self, "runs", -1) + 1
        self.record_run(
            self.db,
            self.output,
            [("dataset", os.path.basename(path))],
            list(positives),
            item=self.runs,
            nodes=list(nodes),
        )

    def recheck(self, path):
        """The re-check the seal schedules, run through the monitor's entry."""
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.handler.process_new_image(path, ForeignRecheck(self.reference(path)))
        return "\n".join(logs.output)

    def assert_nothing_stored(self, path):
        self.assertIsNone(self.linked_text(path))
        self.assertEqual(self.prompt_count(), 0)
        self.assertIsNone(self.db.get_prompt_by_hash(generate_prompt_hash(OFF_TEXT)))
        self.assertEqual(self.handler.awaiting_foreign(), [])
        self.assertEqual(list(self.handler._foreign_due), [])

    def test_q1_the_only_prompt_manager_node_off_is_opted_out_not_foreign(self):
        graph = save_tests.run_graph(save=False)
        # The switched-off positive is the only PromptManager node
        graph["2"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}}
        sampler_less = {
            "1": {
                "class_type": "PromptManagerText",
                "inputs": {"text": OFF_TEXT, "save_to_library": False},
            }
        }
        for name, graph in (("sampled", graph), ("sampler-less", sampler_less)):
            with self.subTest(name=name):
                self.metadata = {"prompt": graph}
                state, _ = self.handler._find_prompt_from_metadata(self.metadata)
                self.assertEqual(state, "opted_out")
                path = self.image(f"IMG_GEN_{name}_00001_.png")
                with self.assertLogs(LOGGER, "INFO") as logs:
                    self.handler.process_new_image(path)
                self.assertIn(SKIPPED, "\n".join(logs.output))
                self.seal(path)
                self.assertTrue(self.settled())
                self.assertIn(SKIPPED, self.recheck(path))
                self.assert_nothing_stored(path)

    def test_q2_an_off_text_node_behind_a_pass_through_stores_nothing(self):
        for middle, key in (("MyNode", "value"), ("easy showAnything", "anything")):
            with self.subTest(middle=middle):
                self.metadata = {"prompt": off_behind(middle, key)}
                state, _ = self.handler._find_prompt_from_metadata(self.metadata)
                self.assertEqual(state, "foreign")
                path = self.image(f"IMG_GEN_{key}_00001_.png")
                with self.assertLogs(LOGGER, "INFO") as logs:
                    self.handler.process_new_image(path)
                self.assertIn(SKIPPED, "\n".join(logs.output))
                self.assert_nothing_stored(path)
                self.seal(path)
                self.assertTrue(self.settled())
                self.assertIn(SKIPPED, self.recheck(path))
                self.assert_nothing_stored(path)

    def test_q2_a_wired_switch_behind_a_pass_through_stores_nothing(self):
        graph = off_behind()
        graph["1"]["inputs"]["save_to_library"] = ["20", 0]
        graph["20"] = {"class_type": "SomeBoolNode", "inputs": {}}
        self.metadata = {"prompt": graph}
        path = self.image("IMG_GEN_wired_00001_.png")
        wired = "Save to library is wired from a node the image cannot show"
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.handler.process_new_image(path)
        self.assertIn(wired, "\n".join(logs.output))
        self.seal(path)
        self.assertTrue(self.settled())
        self.assertIn(wired, self.recheck(path))
        self.assert_nothing_stored(path)

    def test_q3_an_undecided_not_saved_waits_for_the_record_then_skips(self):
        """No record yet: a not_saved of the undecided step is not final (a
        recorded saver may resolve another branch), so the file waits for its
        run; the decision with the record stores nothing and logs the skip."""
        wired = off_text_unwired()
        wired["40"]["inputs"]["save_to_library"] = ["20", 0]
        wired["20"] = {"class_type": "SomeBoolNode", "inputs": {}}
        cases = (
            ("off", off_text_unwired(), SKIPPED),
            ("wired", wired, "Save to library is wired from a node"),
        )
        for name, graph, line in cases:
            with self.subTest(name=name):
                self.metadata = {"prompt": graph}
                path = self.image(f"vendor_{name}_00001_.png")
                self.handler.process_new_image(path)
                self.assertIsNone(self.linked_text(path))
                self.assertEqual(self.handler.awaiting_foreign(), [self.key(path)])
                with self.assertLogs(LOGGER, "INFO") as logs:
                    self.seal(path)
                    self.assertTrue(
                        self.wait_for(lambda: not self.handler.awaiting_foreign())
                    )
                    self.assertTrue(self.settled())
                self.assertIn(line, "\n".join(logs.output))
                self.assert_nothing_stored(path)

    def test_q3_an_early_not_saved_never_waits_even_with_a_saver_branch(self):
        """Review: a switched-off node feeding one branch's positive is the
        ladder's early, final not_saved (PR 1's #90 rule covers every
        sampler's positive), so the file never waits and nothing is stored,
        not even the plain branch the recorded saver names."""
        graph = shapes.shared_prefix()
        graph["20"]["inputs"]["text"] = ["50", 0]
        graph["50"] = {
            "class_type": "PromptManagerText",
            "inputs": {"text": OFF_TEXT, "save_to_library": False},
        }
        self.metadata = {"prompt": graph}
        path = self.image("IMG_GEN_00001_.png")
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.handler.process_new_image(path)
        self.assertIn(SKIPPED, "\n".join(logs.output))
        self.assert_nothing_stored(path)
        self.seal(path, nodes=("13",))
        self.assertTrue(self.settled())
        self.assertIn(SKIPPED, self.recheck(path))
        self.assert_nothing_stored(path)
        self.assertIsNone(
            self.db.get_prompt_by_hash(generate_prompt_hash(shapes.BRANCH_TEXTS[0]))
        )

    def test_q3_an_off_node_in_the_recorded_saver_branch_is_never_foreign(self):
        """PR 3's parity case: a switched-off node feeds the very branch the
        recorded saver names (saver "23" is branch 2). A literal switch is the
        opted-out path's; a wired one is the node's own run's (#90), never the
        foreign path's. Nothing is stored at settle or after the run, and no
        file waits for a foreign re-check."""
        cases = (
            ("off", False, "opted_out", SKIPPED),
            ("wired", ["52", 0], "unknown", "No certain prompt for"),
        )
        for name, save, state, line in cases:
            with self.subTest(name=name):
                graph = shapes.shared_prefix()
                graph["50"] = {
                    "class_type": "PromptManagerText",
                    "inputs": {"text": OFF_TEXT, "save_to_library": save},
                }
                graph["20"]["inputs"]["text"] = ["50", 0]
                if isinstance(save, list):
                    graph["52"] = {"class_type": "SomeBoolNode", "inputs": {}}
                self.metadata = {"prompt": graph}
                found, _ = self.handler._find_prompt_from_metadata(self.metadata)
                self.assertEqual(found, state)
                path = self.image(f"IMG_GEN_{name}_00001_.png")
                with self.assertLogs(LOGGER, "INFO") as logs:
                    self.handler.process_new_image(path)
                self.assertIn(line, "\n".join(logs.output))
                self.assert_nothing_stored(path)
                self.seal(path, positives=[OFF_TEXT], nodes=("23",))
                self.assertTrue(self.settled())
                self.handler.process_new_image(path)
                self.assert_nothing_stored(path)
                self.assertIsNone(
                    self.db.get_prompt_by_hash(
                        generate_prompt_hash(shapes.BRANCH_TEXTS[1])
                    )
                )

    def test_q4_an_off_node_on_the_negative_only_still_links_the_positive(self):
        positive = "a lighthouse on a cliff at dusk"
        plain = sampled_graph(positive, "unused")
        plain["2"] = {
            "class_type": "PromptManager",
            "inputs": {"text": OFF_TEXT, "save_to_library": False},
        }
        behind = sampled_graph(positive, "unused")
        behind["2"]["inputs"]["text"] = ["7", 0]
        behind["7"] = {
            "class_type": "PromptManagerText",
            "inputs": {"text": OFF_TEXT, "save_to_library": False},
        }
        for name, graph in (("direct", plain), ("behind an encoder", behind)):
            with self.subTest(name=name):
                self.metadata = {"prompt": graph}
                state, _ = self.handler._find_prompt_from_metadata(self.metadata)
                self.assertEqual(state, "foreign")
                path = self.image(f"plain_{len(name)}_00001_.png")
                self.handler.process_new_image(path)
                self.assertEqual(self.linked_text(path), positive)
                self.assertIsNone(
                    self.db.get_prompt_by_hash(generate_prompt_hash(OFF_TEXT))
                )
                pairs = self.db.list_prompt_pairs()["pairs"]
                self.assertEqual(pairs, [])
                self.assertEqual(self.handler.awaiting_foreign(), [])

    def test_q5_recorded_history_never_brings_back_switched_off_text(self):
        """Even a recorded run whose contexts hold the off text (an older
        history row) cannot name it, at settle or at the re-check."""
        cases = {"pass-through": off_behind(), "dangling": off_text_unwired()}
        for name, graph in cases.items():
            with self.subTest(name=name):
                self.metadata = {"prompt": graph}
                path = self.image(f"IMG_GEN_{len(name)}_00001_.png")
                self.seal(path, positives=[OFF_TEXT], nodes=("9",))
                self.handler.process_new_image(path)
                self.assert_nothing_stored(path)
                self.assertIn(SKIPPED, self.recheck(path))
                self.assert_nothing_stored(path)

    def test_q6_with_foreign_saving_off_save_to_library_is_unchanged(self):
        GalleryConfig.SAVE_FOREIGN_RUNS = False
        opted_out = save_tests.run_graph(save=False)
        self.metadata = {"prompt": opted_out}
        skipped = self.image("IMG_GEN_off_00001_.png")
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.handler.process_new_image(skipped)
        self.assertIn(SKIPPED, "\n".join(logs.output))
        self.metadata = {"prompt": off_behind()}
        foreign = self.image("IMG_GEN_foreign_00001_.png")
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.handler.process_new_image(foreign)
        self.assertIn("No PromptManager prompt in workflow", "\n".join(logs.output))
        for path in (skipped, foreign):
            self.assert_nothing_stored(path)
        # A switched-on PromptManager positive still links by its metadata
        stored = self.db.save_prompt(
            text=save_tests.POSITIVE,
            prompt_hash=generate_prompt_hash(save_tests.POSITIVE),
        )
        self.metadata = {"prompt": save_tests.run_graph(save=True)}
        on = self.image("IMG_GEN_on_00001_.png")
        self.handler.process_new_image(on)
        self.assertEqual(self.linked_text(on), save_tests.POSITIVE)
        self.assertEqual(
            self.db.get_prompt_by_hash(generate_prompt_hash(save_tests.POSITIVE))["id"],
            stored,
        )

    def test_the_shared_prefix_dataset_still_links_after_the_seal(self):
        """Regression guard (passes before the fix): the wait still works."""
        self.metadata = {"prompt": shapes.shared_prefix()}
        path = self.image("IMG_GEN_00001_.png")
        self.handler.process_new_image(path)
        self.assertEqual(self.handler.awaiting_foreign(), [self.key(path)])
        self.seal(path, nodes=("13",))
        self.assertTrue(self.wait_for(lambda: self.linked_text(path)))
        self.assertTrue(self.settled())
        self.assertEqual(self.linked_text(path), shapes.BRANCH_TEXTS[0])


if __name__ == "__main__":
    unittest.main()
