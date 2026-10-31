"""The per-file prompt decision shared by the scan and the monitor (#247).

One test per case, named by the ladder step of the design
(``docs/egm/247-foreign-workflows``). The ladder does no file or database
I/O: history comes in through ``lookup``.
"""

import json
import unittest
import unittest.mock as mock

from database.run_attribution import RecordedOutput
from tests.test_pair_scan import flux_graph, sampled_graph
from tests.test_prompt_attribution import issue_graph, pair_evidence
from tests import test_save_to_library as save_tests
from utils import file_prompt
from utils.file_prompt import Policy, decide_file_prompt, junk_text
from utils.prompt_inspector import InspectionResult

try:
    from tests import inspection_parity as parity
    from tests import saver_anchor_graphs as shapes
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    import saver_anchor_graphs as shapes

SHARED_NAME = "IMG_GEN_00371_.png"
LORA = "<lora:style:1.00>"


def _decide(
    metadata,
    *,
    policy=Policy.SCAN,
    lookup=None,
    in_output=True,
    file_name=SHARED_NAME,
    file_size=None,
):
    return decide_file_prompt(
        metadata,
        file_name=file_name,
        file_size=file_size,
        in_output=in_output,
        lookup=lookup,
        policy=policy,
    )


def _graph(graph):
    return {"prompt": json.dumps(graph)}


class _Lookup:
    """A history lookup that counts its calls."""

    def __init__(self, saver=None):
        self.calls = 0
        self.found = RecordedOutput(saver, {}) if saver is not None else None

    def __call__(self):
        self.calls += 1
        return self.found


def _inspected(state, malformed=False):
    resolution = {"state": state, "positive": None, "negative": None}
    return InspectionResult(resolution, malformed=malformed)


def _untraced_by_both():
    """A sampled graph whose positive no profile traces; the legacy parser does."""
    return mock.patch.object(
        file_prompt, "inspection_resolution", return_value=_inspected("unresolved")
    )


def _gated(graph):
    """``graph`` past the default profile's node gate, so it is unresolved."""
    graph = dict(graph)
    for index in range(510):
        graph[f"pad{index}"] = {"class_type": "Note", "inputs": {}}
    return graph


def _prompt_manager_branches(prepend="PRE", append="APP"):
    """Two KSampler branches, each fed by a PromptManagerText string."""
    graph = sampled_graph()
    del graph["1"]
    sampler = graph.pop("3")
    for index, text in enumerate(("a cat", "a dog"), start=1):
        base = index * 10
        graph[str(base)] = {
            "class_type": "PromptManagerText",
            "inputs": {"text": text, "prepend_text": prepend, "append_text": append},
        }
        graph[str(base + 1)] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": [str(base), 0]},
        }
        graph[str(base + 2)] = {
            **sampler,
            "inputs": {**sampler["inputs"], "positive": [str(base + 1), 0]},
        }
    return graph


def _numeric_first_branches():
    """Three branches; the legacy parser's answer (the first) is a number."""
    graph = sampled_graph("2048")
    for index, text in ((5, "a cat"), (6, "a dog")):
        graph[str(index * 10)] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": text},
        }
        graph[str(index * 10 + 1)] = {
            **graph["3"],
            "inputs": {**graph["3"]["inputs"], "positive": [str(index * 10), 0]},
        }
    return graph


class TestStepsBeforeResolving(unittest.TestCase):
    def test_step_1_no_metadata_tries_the_recorded_run(self):
        for metadata in ({}, None):
            decision = _decide(metadata)
            self.assertEqual(decision.kind, "never")
            self.assertTrue(decision.try_recorded_run)
            self.assertIsNone(decision.unlinked_status)
            self.assertEqual(decision.canonical, {})

    def test_step_3_neither_prompt_nor_parameters_tries_the_recorded_run(self):
        decision = _decide({"workflow": json.dumps({"nodes": []})})
        self.assertEqual(decision.kind, "never")
        self.assertTrue(decision.try_recorded_run)
        self.assertIsNone(decision.unlinked_status)
        self.assertEqual(decision.canonical, {"workflow": {"nodes": []}})

    def test_strings_are_parsed_and_malformed_ones_kept(self):
        decision = _decide({"prompt": "{not json", "workflow": "{}"})
        self.assertEqual(decision.canonical["prompt"], "{not json")
        self.assertEqual(decision.canonical["workflow"], {})

    def test_the_file_size_is_recorded_unless_the_metadata_has_one(self):
        meta = _graph(sampled_graph())
        self.assertEqual(
            _decide(meta, file_size=42).canonical["file_info"], {"size": 42}
        )
        meta["file_info"] = {"size": 7, "width": 3}
        self.assertEqual(
            _decide(meta, file_size=42).canonical["file_info"], {"size": 7, "width": 3}
        )
        self.assertNotIn("file_info", _decide(_graph(sampled_graph())).canonical)

    def test_a_file_size_reader_is_called_only_for_prompt_metadata(self):
        reads = []

        def size():
            reads.append(1)
            return 9

        for metadata in ({}, {"workflow": "{}"}):
            _decide(metadata, file_size=size)
        self.assertEqual(reads, [])
        decision = _decide(_graph(sampled_graph()), file_size=size)
        self.assertEqual((reads, decision.canonical["file_info"]), ([1], {"size": 9}))


def _off_behind(middle, key):
    """A switched-off PromptManagerText reaching the encoder through
    ``middle``'s ``key`` input."""
    graph = save_tests.run_graph()
    graph["1"] = {
        "class_type": "PromptManagerText",
        "inputs": {"text": "SECRET OFF TEXT", "save_to_library": False},
    }
    graph["7"] = {"class_type": middle, "inputs": {key: ["1", 0]}}
    graph["8"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ["7", 0], "clip": ["4", 1]},
    }
    graph["3"]["inputs"]["positive"] = ["8", 0]
    return graph


class TestSaveToLibrarySteps(unittest.TestCase):
    """#90: a PromptManager node with Save to library off stores nothing."""

    def test_step_2_an_opted_out_file_is_not_saved(self):
        lookup = _Lookup("9")
        graph = save_tests.run_graph(save=False)
        graph["5"] = {**graph["3"], "inputs": {**graph["3"]["inputs"]}}
        graph["5"]["inputs"]["positive"] = ["2", 0]  # ambiguous otherwise
        for policy in Policy:
            decision = _decide(_graph(graph), policy=policy, lookup=lookup)
            self.assertEqual(decision.kind, "not_saved")
            self.assertFalse(decision.try_recorded_run)
            self.assertIsNone(decision.review)
        self.assertEqual(lookup.calls, 0)

    def test_step_2_a_wired_switch_is_not_saved_wired(self):
        graph = save_tests.run_graph(save=["9", 0])
        graph["9"] = {"class_type": "SomeCustomSwitch", "inputs": {}}
        lookup = _Lookup("9")
        decision = _decide(_graph(graph), lookup=lookup)
        self.assertEqual(decision.kind, "not_saved_wired")
        self.assertFalse(decision.try_recorded_run)
        self.assertIsNone(decision.review)
        self.assertEqual(lookup.calls, 0)

    def test_step_8_1_an_off_node_behind_a_pass_through_is_not_saved(self):
        """The #90 graph walk does not follow ``anything`` or ``value``; the
        inspector does, so its positive is checked against the switch."""
        for middle, key in (("easy showAnything", "anything"), ("MyNode", "value")):
            with self.subTest(middle=middle):
                graph = _off_behind(middle, key)
                for policy in Policy:
                    decision = _decide(_graph(graph), policy=policy, lookup=_Lookup())
                    self.assertEqual(decision.kind, "not_saved")
                    self.assertIsNone(decision.text)

    def test_step_8_4_a_legacy_answer_from_an_off_node_is_not_saved(self):
        graph = _off_behind("MyNode", "value")
        graph["9"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "on text"}}
        graph["5"] = {**graph["3"], "inputs": {**graph["3"]["inputs"]}}
        graph["5"]["inputs"]["positive"] = ["9", 0]
        decision = _decide(_graph(graph), lookup=_Lookup())
        self.assertEqual(decision.kind, "not_saved")
        self.assertIsNone(decision.text)

    def test_step_2_an_off_node_on_any_positive_input_is_not_saved(self):
        """Any input name upstream of a positive, not only prompt-like ones."""
        guider = _off_behind("MyNode", "value")
        guider["3"] = {
            "class_type": "BasicGuider",
            "inputs": {"model": ["4", 0], "conditioning": ["8", 0]},
        }
        prepended = _off_behind("MyNode", "value")
        prepended["1"]["inputs"]["prepend_text"] = "pre"
        pair = _off_behind("MyNode", "value")
        pair["1"] = {
            "class_type": "PromptManagerPair",
            "inputs": {"text": "x", "negative_text": "y", "save_to_library": False},
        }
        pair["2"]["inputs"]["text"] = ["1", 1]
        cases = {"sampler-less guider": guider, "prepend": prepended, "pair": pair}
        for name, graph in cases.items():
            with self.subTest(name=name):
                lookup = _Lookup("9")
                decision = _decide(_graph(graph), lookup=lookup)
                self.assertEqual(decision.kind, "not_saved")
                self.assertFalse(decision.try_recorded_run)
                self.assertEqual(lookup.calls, 0)

    def test_step_2_a_wired_switch_behind_a_pass_through_is_not_saved_wired(self):
        graph = _off_behind("MyNode", "value")
        graph["1"]["inputs"]["save_to_library"] = ["20", 0]
        graph["20"] = {"class_type": "SomeBoolNode", "inputs": {}}
        self.assertEqual(_decide(_graph(graph)).kind, "not_saved_wired")

    def test_a_switched_on_node_behind_a_pass_through_is_stored(self):
        """Regression guard: on, or no switch at all (older files)."""
        for value in (True, None):
            with self.subTest(value=value):
                graph = _off_behind("MyNode", "value")
                if value is None:
                    del graph["1"]["inputs"]["save_to_library"]
                else:
                    graph["1"]["inputs"]["save_to_library"] = value
                decision = _decide(_graph(graph), lookup=_Lookup())
                self.assertEqual(decision.kind, "positive")
                self.assertEqual(decision.text, "SECRET OFF TEXT")

    def test_an_off_node_on_a_negative_input_name_never_blocks(self):
        """The walk skips negative inputs: #90's own rule (``neg``,
        ``neg_text``, ``text_neg``) and any name containing "negative"."""
        for key in (
            "neg",
            "neg_text",
            "text_neg",
            "negative_text",
            "negative2",
            "negativeprompt",
            "NegativePrompt",
        ):
            with self.subTest(key=key):
                graph = {
                    "1": {
                        "class_type": "PromptManagerText",
                        "inputs": {"text": "bad hands", "save_to_library": False},
                    },
                    "9": {
                        "class_type": "PromptManagerText",
                        "inputs": {"text": "a cat", "save_to_library": True},
                    },
                    "7": {
                        "class_type": "PipeEncode",
                        "inputs": {"pos_text": ["9", 0], key: ["1", 0]},
                    },
                    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
                    "3": {
                        "class_type": "KSampler",
                        "inputs": {
                            "model": ["4", 0],
                            "positive": ["7", 0],
                            "negative": ["7", 1],
                        },
                    },
                }
                self.assertIsNone(file_prompt._switched_off_upstream(graph))
                self.assertNotIn(
                    _decide(_graph(graph)).kind, ("not_saved", "not_saved_wired")
                )

    def test_the_guards_accept_malformed_nodes(self):
        graph = {
            "1": {"class_type": "PromptManagerText", "inputs": None},
            "2": {"class_type": "PromptManagerText"},
        }
        self.assertIsNone(file_prompt._off_text(graph, "x"))
        side = {"provenance": ["not", "a", "dict"]}
        with mock.patch.object(file_prompt, "side_opted_out", return_value=True):
            self.assertIsNone(file_prompt._side_switch(graph, side))

    def test_the_off_text_guard_reads_a_nodes_resolved_text(self):
        graph = {
            "1": {
                "class_type": "PromptManagerText",
                "inputs": {"text": ["30", 0], "save_to_library": False},
            },
            "30": {
                "class_type": "PrimitiveStringMultiline",
                "inputs": {"value": "SECRET OFF TEXT"},
            },
            "2": {
                "class_type": "PromptManagerText",
                "inputs": {"text": "WIRED", "save_to_library": ["9", 0]},
            },
            "9": {"class_type": "SomeBoolNode", "inputs": {}},
        }
        self.assertEqual(file_prompt._off_text(graph, " SECRET OFF TEXT"), "not_saved")
        self.assertEqual(file_prompt._off_text(graph, "WIRED"), "not_saved_wired")
        self.assertIsNone(file_prompt._off_text(graph, "other"))

    def test_steps_5_and_7_check_the_resolved_positive_too(self):
        """A resolved positive through an off node the #90 walk missed."""
        cases = {
            "pair": save_tests.run_graph(save=False),
            "negative off": save_tests.run_graph(save=False, negative_save=False),
        }
        for name, graph in cases.items():
            with (
                self.subTest(name=name),
                mock.patch.object(
                    file_prompt, "metadata_opted_out", return_value=False
                ),
            ):
                self.assertEqual(_decide(_graph(graph)).kind, "not_saved")

    def test_step_5_a_junk_positive_is_never_stored(self):
        """Pinned: the positive alone still passes the junk gate, and a blank
        positive (never storable) stores nothing either."""
        graph = save_tests.run_graph(save=True, negative_save=False)
        graph["1"]["inputs"]["text"] = "2048"
        decision = _decide(_graph(graph))
        self.assertEqual((decision.kind, decision.source), ("never", "junk:number"))
        self.assertEqual(decision.unlinked_status, "found")
        graph["1"]["inputs"]["text"] = ""
        decision = _decide(_graph(graph))
        self.assertEqual((decision.kind, decision.text), ("never", None))

    def test_step_2_a_workflow_only_opt_out_comes_before_step_3(self):
        workflow = {
            "nodes": [{"id": 1, "type": "PromptManager", "widgets_values": [False]}]
        }
        decision = _decide({"workflow": json.dumps(workflow)})
        self.assertEqual(decision.kind, "not_saved")

    def test_step_5_an_opted_out_negative_stores_the_positive_alone(self):
        graph = save_tests.run_graph(save=True, negative_save=False)
        decision = _decide(_graph(graph))
        self.assertEqual(decision.kind, "positive")
        self.assertEqual(decision.source, "opted-out-negative")
        self.assertEqual(decision.text, save_tests.POSITIVE)


class TestResolvedSteps(unittest.TestCase):
    def test_step_6_a_blank_positive_counts_as_unresolved(self):
        meta = {
            "prompt": json.dumps(issue_graph()),
            "comfyassets_prompt_pairs": json.dumps(pair_evidence()),
        }
        decision = _decide(meta, policy=Policy.MONITOR, in_output=False)
        self.assertEqual(decision.kind, "never")
        self.assertEqual(decision.resolution["state"], "unresolved")

    def test_step_7_a_resolved_pair(self):
        lookup = _Lookup("9")
        decision = _decide(_graph(sampled_graph("cat", "blur")), lookup=lookup)
        self.assertEqual(decision.kind, "pair")
        self.assertEqual(decision.source, "resolver")
        self.assertEqual(decision.text, "cat")
        self.assertEqual(decision.resolution["negative"]["text"], "blur")
        self.assertIsNone(decision.review)
        self.assertEqual(lookup.calls, 0, "decided before step 8: history not asked")

    def test_step_7_a_blank_negative_stays_a_pair(self):
        decision = _decide(_graph(sampled_graph("cat", "")))
        self.assertEqual(decision.kind, "pair")
        self.assertEqual(decision.resolution["negative"]["text"], "")


class TestUndecidedGraph(unittest.TestCase):
    def test_step_8_1_the_inspector_alone_decides(self):
        decision = _decide({"prompt": parity.build("f03")["prompt"]})
        self.assertEqual(decision.kind, "positive")
        self.assertEqual(decision.source, "inspector")
        self.assertEqual(decision.text, parity.POS)
        self.assertEqual(decision.resolution["state"], "unresolved")

    def test_step_8_1_the_recorded_saver_names_its_branch(self):
        lookup = _Lookup("23")
        decision = _decide(_graph(shapes.shared_prefix()), lookup=lookup)
        self.assertEqual(decision.kind, "positive")
        self.assertEqual(decision.source, "recorded-run")
        self.assertEqual(decision.text, shapes.BRANCH_TEXTS[1])
        self.assertEqual(decision.review, "ambiguous")
        self.assertEqual(lookup.calls, 1)

    def test_step_8_1_stores_the_storage_text_without_prepend_or_append(self):
        graph = shapes.shared_prefix()
        graph["20"]["inputs"]["text"] = ["25", 1]
        graph["25"] = {
            "class_type": "PromptManagerSplit",
            "inputs": {
                "text": shapes.BRANCH_TEXTS[1],
                "prepend_text": "trigger",
                "append_text": "detail",
            },
        }
        decision = _decide(_graph(graph), lookup=_Lookup("23"))
        self.assertEqual(decision.source, "recorded-run")
        self.assertEqual(decision.text, shapes.BRANCH_TEXTS[1])

    def test_step_8_1_a_file_name_naming_another_saver_anchors_nothing(self):
        graph = shapes.branch_bench()
        for index in (1, 3, 4):
            graph[str(index * 10 + 3)]["inputs"]["filename_prefix"] = "bench/shared"
        decision = _decide(
            _graph(graph), lookup=_Lookup("43"), file_name="s45_branch_2_00001_.png"
        )
        # The name contradicts history's branch 4: no recorded anchor.
        self.assertEqual(decision.source, "legacy-parser")

    def test_step_8_1_the_file_name_anchor_alone_never_decides(self):
        """#390: the file-name anchor never decides a link. The scan's legacy
        floor may still link the file (as before #247), and a file nothing
        links is queued with the anchor's choice per #390; the monitor
        stores nothing."""
        name = "s45_branch_2_00001_.png"
        scan = _decide(_graph(shapes.branch_bench()), lookup=_Lookup(), file_name=name)
        self.assertEqual((scan.kind, scan.source), ("positive", "legacy-parser"))
        self.assertEqual(scan.review, "ambiguous")
        monitor = _decide(
            _graph(shapes.branch_bench()),
            policy=Policy.MONITOR,
            lookup=_Lookup(),
            file_name=name,
        )
        self.assertEqual(monitor.kind, "never")
        recorded = _decide(
            _graph(shapes.branch_bench()), lookup=_Lookup("43"), file_name=name
        )
        # The recorded saver still decides: history says branch 4, but the
        # file name names branch 2, which contradicts it.
        self.assertNotEqual(recorded.source, "recorded-run")
        recorded = _decide(_graph(shapes.branch_bench()), lookup=_Lookup("43"))
        self.assertEqual(recorded.source, "recorded-run")
        self.assertEqual(recorded.text, shapes.BRANCH_TEXTS[3])

    def test_step_8_2_the_monitor_waits_for_history_inside_the_output_folder(self):
        decision = _decide(_graph(shapes.shared_prefix()), policy=Policy.MONITOR)
        self.assertEqual(decision.kind, "history")

    def test_step_8_2_outside_the_output_folder_steps_8_3_and_8_4_apply(self):
        decision = _decide(
            _graph(shapes.shared_prefix()), policy=Policy.MONITOR, in_output=False
        )
        self.assertEqual(decision.kind, "never")
        with _untraced_by_both():
            decision = _decide(
                _graph(_gated(sampled_graph())), policy=Policy.MONITOR, in_output=False
            )
        self.assertEqual(
            (decision.kind, decision.source), ("positive", "legacy-parser")
        )

    def test_step_8_3_an_ambiguous_file_is_left_unlinked_by_the_monitor(self):
        lookup = _Lookup("missing")
        decision = _decide(
            _graph(shapes.shared_prefix()), policy=Policy.MONITOR, lookup=lookup
        )
        self.assertEqual(decision.kind, "never")
        self.assertEqual(decision.unlinked_status, "ambiguous")
        self.assertFalse(decision.try_recorded_run)
        self.assertEqual(lookup.calls, 1)

    def test_step_8_4_joined_prompt_manager_text_is_never_the_legacy_answer(self):
        """The legacy parser returns prepend + text + append; the stored text
        of a PromptManager node cannot be told from it."""
        decision = _decide(_graph(_prompt_manager_branches()), lookup=_Lookup())
        self.assertEqual(decision.kind, "never")
        self.assertEqual(decision.unlinked_status, "ambiguous")
        self.assertEqual(decision.review, "ambiguous")
        self.assertTrue(decision.try_recorded_run)
        # A linked part the parser does not follow: its answer is the node's
        # own text, which is what the node stores (#170 parity).
        linked = _prompt_manager_branches(prepend=["99", 0], append="")
        decision = _decide(_graph(linked), lookup=_Lookup())
        self.assertEqual((decision.kind, decision.text), ("positive", "a cat"))

    def test_step_8_4_prompt_manager_text_without_parts_keeps_the_floor(self):
        """Regression guard (passes on the base): no prepend or append."""
        graph = _prompt_manager_branches(prepend="", append=" ")
        decision = _decide(_graph(graph), lookup=_Lookup())
        self.assertEqual((decision.kind, decision.text), ("positive", "a cat"))

    def test_step_8_4_an_ambiguous_file_gets_the_legacy_answer_in_the_scan(self):
        decision = _decide(_graph(shapes.shared_prefix()), lookup=_Lookup())
        self.assertEqual(decision.kind, "positive")
        self.assertEqual(decision.source, "legacy-parser")
        self.assertEqual(decision.text, shapes.BRANCH_TEXTS[0])
        self.assertEqual(decision.review, "ambiguous")

    def test_step_8_4_an_untraceable_file_gets_the_legacy_answer_in_both(self):
        for policy in Policy:
            for inspected in (_inspected("unresolved"), _inspected("x", True)):
                with (
                    self.subTest(policy=policy, malformed=inspected.malformed),
                    mock.patch.object(
                        file_prompt, "inspection_resolution", return_value=inspected
                    ),
                ):
                    decision = _decide(
                        _graph(_gated(sampled_graph("cat"))),
                        policy=policy,
                        lookup=_Lookup(),
                    )
                    self.assertEqual(decision.kind, "positive")
                    self.assertEqual(decision.source, "legacy-parser")
                    self.assertEqual(decision.text, "cat")

    def test_step_8_4_nothing_traced_tries_the_recorded_run(self):
        graph = sampled_graph()
        graph["5"] = {**graph["3"]}
        for sampler in ("3", "5"):
            graph[sampler]["inputs"]["positive"] = ["9", 0]
        graph["9"] = {"class_type": "BasicGuider", "inputs": {"width": 2048}}
        decision = _decide(_graph(graph), lookup=_Lookup())
        self.assertEqual(decision.kind, "never")
        self.assertTrue(decision.try_recorded_run)
        # Ambiguous without a choice is reported unresolved.
        self.assertEqual(decision.unlinked_status, "unresolved")
        self.assertEqual(decision.review, "untraced")


class TestStepsWithoutAnUndecidedGraph(unittest.TestCase):
    def test_step_9_ambiguous_parameters_only(self):
        meta = {
            "parameters": (
                "cat\nNegative prompt: blur\nNegative prompt: noise"
                "\nSteps: 20, Sampler: Euler"
            )
        }
        decision = _decide(meta)
        self.assertEqual(decision.kind, "never")
        self.assertFalse(decision.try_recorded_run)
        self.assertEqual(decision.unlinked_status, "unresolved")

    def test_step_10_unresolved_without_a1111_text(self):
        decision = _decide({"prompt": {}, "parameters": 5})
        self.assertEqual(decision.kind, "never")
        self.assertFalse(decision.try_recorded_run)
        self.assertEqual(decision.unlinked_status, "unresolved")

    def test_step_11_sampler_less_with_one_text(self):
        decision = _decide(_graph(flux_graph()))
        self.assertEqual((decision.kind, decision.source), ("positive", "sampler-less"))
        self.assertEqual(decision.text, "a red fox")

    def test_step_11_sampler_less_with_several_texts(self):
        graph = flux_graph()
        graph["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "clip": ["2", 0], "text": "a lora note"},
        }
        graph["22"]["inputs"]["model"] = ["50", 0]
        graph["6"]["inputs"]["clip"] = ["50", 1]
        decision = _decide(_graph(graph))
        self.assertEqual(decision.kind, "never")
        self.assertTrue(decision.try_recorded_run)
        self.assertEqual(decision.unlinked_status, "unresolved")
        meta = {**_graph(graph), "parameters": "a red fox\nNegative prompt: x"}
        decision = _decide(meta)
        self.assertEqual((decision.kind, decision.source), ("positive", "a1111"))
        self.assertEqual(decision.text, "a red fox")

    def test_step_11_sampler_less_without_a_text(self):
        graph = {"40": {"class_type": "easy showAnything", "inputs": {"text": "x"}}}
        decision = _decide(_graph(graph))
        self.assertEqual(decision.kind, "never")
        self.assertTrue(decision.try_recorded_run)
        self.assertEqual(decision.unlinked_status, "found")

    def test_step_12_a1111_only(self):
        decision = _decide({"parameters": "a cat"})
        self.assertEqual((decision.kind, decision.source), ("positive", "a1111"))
        self.assertEqual(decision.text, "a cat")
        decision = _decide({"parameters": "Negative prompt: blur"})
        self.assertEqual((decision.kind, decision.unlinked_status), ("never", "found"))
        self.assertFalse(decision.try_recorded_run)

    def test_step_12_parsed_metadata_never_raises(self):
        # The monitor hands parsed values: a dict prompt, non-string parameters.
        decision = _decide({"prompt": {}, "parameters": "a cat"})
        self.assertEqual(decision.text, "a cat")
        decision = _decide({"parameters": 5})
        self.assertEqual((decision.kind, decision.unlinked_status), ("never", "found"))


class TestJunkGate(unittest.TestCase):
    JUNK = {
        "blank": "  ",
        "number": "2048",
        "lora-only": f"{LORA}, <lora:detail:0.5> |",
        "model-file": "models/checkpoints/Flux-Dev.SafeTensors",
    }

    def test_each_junk_class_is_named(self):
        for name, text in self.JUNK.items():
            with self.subTest(name=name):
                self.assertEqual(junk_text(text), name)
        for ending in ("ckpt", "pt", "pth", "bin", "gguf", "sft"):
            self.assertEqual(junk_text(f"model.{ending}"), "model-file")

    def test_near_misses_are_not_junk(self):
        for text in (
            f"a red fox {LORA}",
            "a.safetensors b.safetensors",
            "nan",
            "a portrait saved as final.safetensors",
            "4k",
            "C:\\My Models\\x.safetensors",
        ):
            with self.subTest(text=text):
                self.assertIsNone(junk_text(text))

    def test_step_13_junk_from_any_step_is_never_stored(self):
        cases = {
            "resolver": _graph(sampled_graph("2048", "blur")),
            "sampler-less": _graph(flux_graph(text=LORA)),
            "a1111": {"parameters": "model.safetensors"},
        }
        for step, meta in cases.items():
            with self.subTest(step=step):
                decision = _decide(meta)
                self.assertEqual(decision.kind, "never")
                self.assertTrue(decision.source.startswith("junk:"), decision.source)
                self.assertTrue(decision.try_recorded_run)
                self.assertEqual(decision.unlinked_status, "found")

    def test_step_13_a_junk_legacy_answer_is_never_stored(self):
        with _untraced_by_both():
            decision = _decide(_graph(_gated(sampled_graph(LORA))), lookup=_Lookup())
        self.assertEqual(decision.source, "junk:lora-only")
        self.assertEqual(decision.kind, "never")
        self.assertEqual(decision.unlinked_status, "unresolved")

    def test_step_13_junk_at_step_8_keeps_that_steps_status_and_review(self):
        # An ambiguous file whose legacy answer is a number is still a
        # Needs review case: its status stays "ambiguous".
        decision = _decide(_graph(_numeric_first_branches()), lookup=_Lookup())
        self.assertEqual((decision.kind, decision.source), ("never", "junk:number"))
        self.assertEqual(decision.unlinked_status, "ambiguous")
        self.assertEqual(decision.review, "ambiguous")
        self.assertTrue(decision.try_recorded_run)


class TestParseComfyuiPrompt(unittest.TestCase):
    def test_a_parsed_prompt_and_non_string_values_are_kept(self):
        parsed = file_prompt.parse_comfyui_prompt(
            {"prompt": {"1": {}}, "workflow": {"nodes": []}, "steps": 20}
        )
        self.assertEqual(parsed["prompt"], {"1": {}})
        self.assertEqual(parsed["workflow"], {"nodes": []})
        self.assertEqual(parsed["parameters"]["steps"], 20)
        self.assertIsNone(parsed["positive_prompt"])

    def test_non_string_parameters_are_ignored(self):
        parsed = file_prompt.parse_comfyui_prompt({"parameters": {"a": 1}})
        self.assertIsNone(parsed["positive_prompt"])


if __name__ == "__main__":
    unittest.main()
