"""The scan and Needs review anchor a file on the saver that wrote it (#390).

The workspace inspector (since #383) passes the file's name to the resolver,
which anchors the file on the one saver whose ``filename_prefix`` names it
(``_GraphResolver.saver_stages``). The scan and Needs review use the same
rule. Maintainer ruling: a file never falls through both the link and the
queue, so when only that anchor resolves a file the default profile and the
parser floor do not link, it is queued with the anchor's pair as its one
choice (never linked automatically). No match, several matches or an
unfinished walk anchor nothing, so such a file is queued as before. Apply
re-resolves with the file's name, so a choice the anchor contradicts is
refused as stale evidence.
"""

import json
from unittest.mock import patch

try:
    from tests.test_review import unlinkable_graph, write_png
    from tests.test_review_inspection import (
        PROMPTS,
        _counted,
        InspectionTestCase,
        qwen_edit_graph,
    )
except ImportError:  # discovered with tests/ as the top-level directory
    from test_review import unlinkable_graph, write_png
    from test_review_inspection import (
        PROMPTS,
        _counted,
        InspectionTestCase,
        qwen_edit_graph,
    )

from utils import review_capture


def branch_graph(*prompts, prefixes=None):
    """A Qwen-edit batch whose every SaveImage has its own literal prefix."""
    graph = qwen_edit_graph(*prompts)
    savers = [n for n in graph.values() if n["class_type"] == "SaveImage"]
    names = prefixes or [f"branch_{index}" for index in range(len(savers))]
    for saver, prefix in zip(savers, names):
        saver["inputs"]["filename_prefix"] = prefix
    return graph


def saved_unlinkable_graph(*positives):
    """``unlinkable_graph`` whose branches save under their own prefix.

    The default profile finds it ambiguous with a choice and the scan's
    browser-parser floor links nothing, so it is queued from the default
    profile unless the saver that wrote the file anchors its branch.
    """
    graph = unlinkable_graph(*positives)
    samplers = [
        node_id
        for node_id, node in graph.items()
        if node["class_type"] == "KSampler" and node_id != "3"
    ]
    for index, sampler in enumerate(samplers):
        graph[f"{sampler}1"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sampler, 0], "vae": ["1", 2]},
        }
        graph[f"{sampler}2"] = {
            "class_type": "SaveImage",
            "inputs": {
                "images": [f"{sampler}1", 0],
                "filename_prefix": f"side_{index}",
            },
        }
    return graph


def _without_name(real):
    """``resolve_inspection`` as it was before #390: the file name ignored."""

    def unnamed(metadata, file_name=None):
        return real(metadata)

    return unnamed


def _only_candidate(test, item):
    """The positive text of an anchored item's one candidate."""
    evidence = json.loads(item["evidence"])
    test.assertEqual(evidence.get("anchor"), "saver")
    test.assertEqual(evidence.get("profile"), "inspection")
    (candidate,) = evidence["candidates"]
    return candidate["positive"]["text"]


class TestInspectionItemsAnchor(InspectionTestCase):
    async def test_file_its_saver_names_is_queued_with_the_anchor_choice(self):
        self._roots()
        path = self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))

        job = await self._scan()

        (item,) = self._items()
        self.assertEqual(_only_candidate(self, item), "add a red hat")
        self.assertEqual(item["candidate_count"], 1)
        self.assertEqual(
            item["resolver_version"],
            review_capture.inspection_review_version(
                {"prompt": branch_graph(*PROMPTS)}
            ),
        )
        (row,) = job["outcomes"]["review"]
        self.assertEqual(row["path"], "branch_1_00001_.png")
        # Queued, never linked: ingestion stays on the default profile.
        self.assertIsNone(self._linked_text(path))

    async def test_anchored_item_stays_on_rescan(self):
        self._roots()
        self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))
        await self._scan()
        (item,) = self._items()

        job = await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], [item["id"]])
        self.assertEqual(job["scope"]["review_untraced"], 0)

    async def test_file_no_saver_names_stays_queued(self):
        self._roots()
        self._write("elsewhere_00001_.png", graph=branch_graph(*PROMPTS))

        await self._scan()

        (item,) = self._items()
        self.assertEqual(item["candidate_count"], len(PROMPTS))
        self.assertNotIn("anchor", json.loads(item["evidence"]))

    async def test_file_several_savers_name_stays_queued(self):
        self._roots()
        graph = branch_graph(*PROMPTS, prefixes=["same", "same", "other"])
        self._write("same_00001_.png", graph=graph)

        await self._scan()

        (item,) = self._items()
        self.assertEqual(item["candidate_count"], len(PROMPTS))

    async def test_old_rule_item_is_replaced_by_the_anchor_choice(self):
        self._roots()
        self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))
        real = review_capture.resolve_inspection
        with (
            patch.object(review_capture, "REVIEW_RULE_VERSION", 0),
            patch.object(
                review_capture, "resolve_inspection", side_effect=_without_name(real)
            ),
        ):
            await self._scan()
        (old,) = self._items()  # queued under the old rule with every branch
        self.assertEqual(old["candidate_count"], len(PROMPTS))

        await self._scan("all")

        (item,) = self._items()
        self.assertNotEqual(item["id"], old["id"])
        self.assertEqual(_only_candidate(self, item), "add a red hat")

    async def test_apply_links_the_anchor_choice(self):
        self._require_routes()
        self._roots()
        path = self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))
        await self._scan()

        _item, (status, data) = await self._apply("add a red hat")

        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(path), "add a red hat")

    async def test_apply_refuses_an_old_choice_the_anchor_contradicts(self):
        self._require_routes()
        self._roots()
        path = self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))
        real = review_capture.resolve_inspection
        with (
            patch.object(review_capture, "REVIEW_RULE_VERSION", 0),
            patch.object(
                review_capture, "resolve_inspection", side_effect=_without_name(real)
            ),
        ):
            await self._scan()
        item, detail, observed = await self._open()
        # branch_1 is the saver of "add a red hat"; choose another branch.
        wrong = self._candidate(detail, "make it night")

        status, data = await self._decide(
            item["id"], self._body(detail, observed, wrong["hash"])
        )

        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict", data)
        self.assertIsNone(self._linked_text(path))


class TestDefaultItemsAnchor(InspectionTestCase):
    async def test_anchor_resolves_and_nothing_links_so_it_is_queued(self):
        """Maintainer ruling: never unlinked and unqueued. The anchor resolves
        the file, the default profile and the parser floor link nothing, so
        it is queued with the anchor's pair as its choice."""
        self._roots()
        path = self.output_dir / "side_0_00001_.png"
        graph = saved_unlinkable_graph("cat", "dog")
        write_png(path, prompt=graph)
        self.assertEqual(
            review_capture.resolve_prompt_pair({"prompt": graph})["state"], "ambiguous"
        )

        job = await self._scan()

        (item,) = self._items()
        self.assertEqual(_only_candidate(self, item), "cat")
        (row,) = job["outcomes"]["review"]
        self.assertIn("more than one prompt could match", row["detail"])
        self.assertIsNone(self._linked_text(path))

    async def test_default_ambiguous_file_no_saver_names_stays_queued(self):
        self._roots()
        write_png(
            self.output_dir / "unsaved_00001_.png",
            prompt=saved_unlinkable_graph("cat", "dog"),
        )

        await self._scan()

        (item,) = self._items()
        evidence = json.loads(item["evidence"])
        self.assertNotIn("profile", evidence)
        self.assertNotIn("anchor", evidence)
        self.assertEqual(item["resolver_version"], review_capture.RESOLVER_VERSION)

    async def test_file_the_parser_floor_links_is_not_queued(self):
        """Already linked by the default path: unchanged by the anchor."""
        self._roots()
        graph = saved_unlinkable_graph("cat", "dog")
        del graph["3"]  # the parser floor now reads a real sampler
        path = self.output_dir / "side_0_00001_.png"
        write_png(path, prompt=graph)

        await self._scan()

        self.assertEqual(self._items(), [])
        self.assertIsNotNone(self._linked_text(path))

    async def test_apply_refuses_an_old_default_choice_the_anchor_contradicts(self):
        self._require_routes()
        self._roots()
        path = self.output_dir / "side_0_00001_.png"
        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"))
        with patch.object(review_capture, "_captured_inspection", return_value=None):
            await self._scan()  # queued from the default profile, as before #390
        item, detail, observed = await self._open()
        # side_0 is the saver of "cat"; choose the other branch.
        wrong = self._candidate(detail, "dog")

        status, data = await self._decide(
            item["id"], self._body(detail, observed, wrong["hash"])
        )

        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict", data)
        self.assertIsNone(self._linked_text(path))

    async def test_old_default_item_is_replaced_by_the_anchor_choice(self):
        self._roots()
        write_png(
            self.output_dir / "side_0_00001_.png",
            prompt=saved_unlinkable_graph("cat", "dog"),
        )
        with patch.object(review_capture, "_captured_inspection", return_value=None):
            await self._scan()  # queued from the default profile, as before #390
        (old,) = self._items()
        self.assertEqual(old["resolver_version"], review_capture.RESOLVER_VERSION)

        await self._scan("all")

        (item,) = self._items()
        self.assertNotEqual(item["id"], old["id"])
        self.assertEqual(_only_candidate(self, item), "cat")

        with _counted(review_capture, "resolve_inspection") as calls:
            await self._scan("all")  # now queued under its version: no re-read
        self.assertEqual(calls, [])
        self.assertEqual([i["id"] for i in self._items()], [item["id"]])

    async def test_decided_anchored_item_is_kept(self):
        self._require_routes()
        self._roots()
        write_png(
            self.output_dir / "side_0_00001_.png",
            prompt=saved_unlinkable_graph("cat", "dog"),
        )
        await self._scan()
        item, detail, observed = await self._open()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "unresolved")
        )
        self.assertEqual(status, 200, data)
        before = (self._items(), self._decisions())

        await self._scan("all")

        self.assertEqual((self._items(), self._decisions()), before)

    async def test_apply_accepts_an_old_default_choice_the_anchor_names(self):
        self._require_routes()
        self._roots()
        path = self.output_dir / "side_0_00001_.png"
        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"))
        with patch.object(review_capture, "_captured_inspection", return_value=None):
            await self._scan()

        _item, (status, data) = await self._apply("cat")

        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(path), "cat")


class TestSaverAnchored(InspectionTestCase):
    def test_only_the_anchor_counts(self):
        anchored = {"prompt": saved_unlinkable_graph("cat", "dog")}
        self.assertTrue(review_capture.saver_anchored(anchored, "side_1_00001_.png"))
        self.assertFalse(review_capture.saver_anchored(anchored, "nope_00001_.png"))
        self.assertFalse(review_capture.saver_anchored(anchored, None))
        # One sampler resolves without any name: the anchor decides nothing.
        single = {"prompt": branch_graph("make it night")}
        self.assertFalse(review_capture.saver_anchored(single, "branch_0_00001_.png"))
