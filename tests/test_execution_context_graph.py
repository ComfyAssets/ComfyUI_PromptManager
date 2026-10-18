"""Projection must retain declared roles without claiming sampler consumption."""

import unittest
from test_execution_contexts import load_module


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def graph(kind="KSampler", source="PromptManagerPair"):
    return {
        "p": node(
            source,
            text=" cat ",
            negative_text="",
            prepend_text="pre",
            append_text="post",
        ),
        "s": node(kind, positive=["p", 0], negative=["p", 1]),
    }


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.m = load_module("execution_context_graph")

    def test_projection_preserves_roles(self):
        for kind in ("KSampler", "KSamplerAdvanced", "SamplerCustom"):
            g = graph(kind)
            result = self.m.project_contexts(g, None)
            self.assertEqual(len(result.recipes), 1)
            recipe = result.recipes[0]
            self.assertEqual(recipe.anchor_id, "s")
            self.assertEqual(recipe.positive.inferred.raw.value, " cat ")
            self.assertEqual(recipe.positive.inferred.effective.value, "pre  cat  post")
            self.assertEqual(recipe.negative.inferred.effective.value, "")
            self.assertEqual(recipe.positive.sources[0].output_slot, 0)
            g["s"]["inputs"]["positive"], g["s"]["inputs"]["negative"] = ["p", 1], [
                "p",
                0,
            ]
            crossed = self.m.project_contexts(g, None).recipes[0]
            self.assertEqual(crossed.positive.inferred.raw.value, "")
            self.assertEqual(crossed.negative.sources[0].output_slot, 0)
        for source in ("PromptManager", "PromptManagerFocused", "PromptManagerSplit"):
            g = graph(source=source)
            g["clip"] = node("CLIPTextEncode", text=["p", 1])
            g["s"]["inputs"]["negative"] = ["clip", 0]
            recipe = self.m.project_contexts(g, None).recipes[0]
            self.assertEqual(recipe.negative.inferred.raw.value, " cat ")
            self.assertEqual(recipe.negative.sources[0].output_slot, 1)
        for source, slot in (
            ("PromptManagerText", 0),
            ("PromptManagerPair", 2),
            ("PromptManagerPair", 3),
        ):
            g = graph(source=source)
            g["clip"] = node("CLIPTextEncode", text=["p", slot])
            g["s"]["inputs"] = {"positive": ["clip", 0], "negative": ["clip", 0]}
            self.assertEqual(
                self.m.project_contexts(g, None)
                .recipes[0]
                .positive.sources[0]
                .output_slot,
                slot,
            )
        g = graph()
        g["p"] = node("CLIPTextEncode", text=["literal", 0])
        g["literal"] = node("PrimitiveString", value=" exact ")
        g["s"]["inputs"]["negative"] = ["p", 0]
        self.assertEqual(
            self.m.project_contexts(g, None)
            .recipes[0]
            .positive.inferred.effective.value,
            " exact ",
        )
        g["p"] = node("UnknownTransform", text="secret")
        role = self.m.project_contexts(g, None).recipes[0].positive
        self.assertIsNone(role.inferred)
        self.assertTrue(role.reason)

    def test_output_selection_and_scalar_isolation(self):
        g = graph()
        g["other"] = node("KSampler", positive=["p", 0], negative=["p", 1])
        g["decode"] = node("VAEDecode", samples=["s", 0])
        g["save"] = node("SaveImage", images=["decode", 0])
        result = self.m.project_contexts(g, ["save"])
        self.assertEqual(tuple(r.anchor_id for r in result.recipes), ("s",))
        g["p"]["inputs"]["text"] = "changed"
        self.assertEqual(result.recipes[0].positive.inferred.raw.value, " cat ")
        self.assertEqual(self.m.project_contexts(g, []).recipes, ())
        for outputs in (["missing"], [object()], "save"):
            result = self.m.project_contexts(g, outputs)
            self.assertEqual(result.recipes, ())
            self.assertTrue(result.reason)

    def test_projection_limits(self):
        for count, succeeds in ((500, True), (501, False)):
            g = graph()
            g.update({str(i): node("Unused") for i in range(count - 2)})
            self.assertEqual(bool(self.m.project_contexts(g, None).recipes), succeeds)
        for count, succeeds in ((16, True), (17, False)):
            g = {"p": graph()["p"]}
            g.update({str(i): graph()["s"] for i in range(count)})
            self.assertEqual(bool(self.m.project_contexts(g, None).recipes), succeeds)
        for depth, succeeds in ((64, True), (65, False)):
            g = graph()
            # The CLIP plus reroutes plus literal give exactly depth visited links.
            g["p"] = node("CLIPTextEncode", text=["0", 0])
            for i in range(depth - 2):
                g[str(i)] = node("Reroute", value=[str(i + 1), 0])
            g[str(depth - 2)] = node("PrimitiveString", value="known")
            role = self.m.project_contexts(g, None).recipes[0].positive
            self.assertEqual(role.inferred is not None, succeeds)
        g = graph()
        g["s" * 129] = g.pop("s")
        self.assertFalse(self.m.project_contexts(g, None).recipes)

    def test_dynamic_pm_inputs_preserve_observable_sources(self):
        for kind, positive_slot, negative_slot in (
            ("PromptManagerText", 0, 0),
            ("PromptManagerPair", 2, 3),
            ("PromptManager", 1, 1),
            ("PromptManagerFocused", 1, 1),
            ("PromptManagerSplit", 1, 1),
            ("PromptManagerPair", 0, 1),
        ):
            with self.subTest(kind=kind, slots=(positive_slot, negative_slot)):
                g = graph(source=kind)
                g["generator"] = node("UnknownGenerator")
                g["p"]["inputs"]["text"] = ["generator", 0]
                g["p"]["inputs"]["negative_text"] = ["generator", 0]
                g["pos"] = node("CLIPTextEncode", text=["p", positive_slot])
                g["neg"] = node("CLIPTextEncode", text=["p", negative_slot])
                g["s"]["inputs"] = {"positive": ["pos", 0], "negative": ["neg", 0]}
                if kind == "PromptManagerPair" and positive_slot == 0:
                    g["s"]["inputs"] = {"positive": ["p", 1], "negative": ["p", 0]}
                    positive_slot, negative_slot = 1, 0
                result = self.m.project_contexts(g, None)
                recipe = result.recipes[0]
                for role, slot in (
                    (recipe.positive, positive_slot),
                    (recipe.negative, negative_slot),
                ):
                    self.assertEqual(
                        tuple(ref.output_slot for ref in role.sources), (slot,)
                    )
                    self.assertIsNone(role.inferred)
                    self.assertTrue(role.reason)
                self.assertTrue(result.sources)

    def test_graph_evidence_distinguishes_literals_from_semantics(self):
        result = self.m.project_contexts(graph(), None)
        evidence = result.recipes[0].positive.inferred
        self.assertEqual(evidence.raw.provenance, "inferred")
        self.assertEqual(evidence.raw.source, "graph_literal")
        self.assertEqual(evidence.effective.source, "graph_semantics")

    def test_derived_raw_and_composition_preserve_field_sources(self):
        g = graph(source="PromptManagerText")
        g["join"] = node(
            "Text Concatenate",
            text_a=" a ",
            text_b=" b ",
            delimiter="-",
            clean_whitespace="true",
        )
        g["p"]["inputs"]["text"] = ["join", 0]
        g["clip"] = node("CLIPTextEncode", text=["p", 0])
        g["s"]["inputs"] = {"positive": ["clip", 0], "negative": ["clip", 0]}
        role = self.m.project_contexts(g, None).recipes[0].positive
        self.assertIsNotNone(role.inferred)
        self.assertEqual(role.inferred.raw.value, "a-b")
        self.assertEqual(role.inferred.raw.source, "graph_semantics")
        self.assertEqual(role.inferred.prepend.source, "graph_literal")

    def test_selected_custom_outputs_and_multistage_ancestry(self):
        g = graph()
        g["earlier"] = node("SamplerCustom", positive=["p", 0], negative=["p", 1])
        g["s"]["inputs"]["latent_image"] = ["earlier", 0]
        g["adapter"] = node("UnknownImageAdapter", source=["s", 0])
        g["custom"] = node("CustomOutput", images=["adapter", 0])
        result = self.m.project_contexts(g, ["custom"])
        self.assertEqual(tuple(r.anchor_id for r in result.recipes), ("s", "earlier"))

    def test_total_resolution_visits_are_bounded(self):
        # Sixteen recipes each resolving two independent chains of length 16
        # exceed 500 total visits even though the graph has fewer than 500 nodes.
        g = {"v": node("PrimitiveString", value="known")}
        for i in range(15):
            g[str(i)] = node("Reroute", value=[str(i + 1), 0] if i < 14 else ["v", 0])
        g["clip"] = node("CLIPTextEncode", text=["0", 0])
        for i in range(16):
            g["s" + str(i)] = node(
                "KSampler", positive=["clip", 0], negative=["clip", 0]
            )
        result = self.m.project_contexts(g, None)
        self.assertTrue(result.reason)
        self.assertFalse(
            result.recipes
            and all(
                r.positive.inferred is not None and r.negative.inferred is not None
                for r in result.recipes
            )
        )

    def test_known_concatenation_remains_inferred(self):
        g = graph(source="PromptManagerText")
        g["join"] = node(
            "StringConcatenate", string_a=["p", 0], string_b="!", delimiter=""
        )
        g["clip"] = node("CLIPTextEncode", text=["join", 0])
        g["s"]["inputs"] = {"positive": ["clip", 0], "negative": ["clip", 0]}
        role = self.m.project_contexts(g, None).recipes[0].positive
        self.assertIsNotNone(role.inferred)
        self.assertEqual(role.inferred.effective.value, "pre  cat  post!")
        self.assertEqual(role.inferred.effective.provenance, "inferred")
        self.assertEqual(role.sources[0].node_id, "p")
        self.assertEqual(role.reason, "source-transformed")

    def test_malformed_inputs_do_not_stringify(self):
        class HostValue:
            def __str__(self):
                raise AssertionError("host object stringified")

        for g in (
            {HostValue(): node("KSampler")},
            {"s": node(HostValue())},
            {"s": node("KSampler", positive=HostValue())},
        ):
            self.m.project_contexts(g, None)

    def test_composed_affixes_and_retained_projection_are_bounded(self):
        g = graph()
        g["p"]["inputs"].update(
            text="x" * 65536, prepend_text="y" * 65536, append_text="z" * 65536
        )
        result = self.m.project_contexts(g, None)
        self.assertIsNone(result.recipes[0].positive.inferred)
        g = graph()
        g["p"]["inputs"]["text"] = "x" * 60000
        for i in range(15):
            g[str(i)] = node("KSampler", positive=["p", 0], negative=["p", 0])
        result = self.m.project_contexts(g, None)
        self.assertEqual(result.recipes, ())

    def test_partial_source_snapshots_keep_literal_fields_without_stateful_substitution(
        self,
    ):
        for upstream in ("RuntimeGenerator", "PromptManagerText"):
            g = graph()
            g["dynamic"] = node(upstream, text="looks literal")
            g["p"]["inputs"]["text"] = ["dynamic", 0]
            projection = self.m.project_contexts(g, None)
            positive = next(
                snapshot
                for ref, snapshot in projection.source_snapshots
                if ref.output_slot == 0
            )
            self.assertIsNone(positive.raw.value)
            self.assertIsNone(positive.effective.value)
            self.assertEqual(positive.prepend.value, "pre")
            self.assertEqual(positive.append.value, "post")
            self.assertEqual(positive.append.provenance, "inferred")
