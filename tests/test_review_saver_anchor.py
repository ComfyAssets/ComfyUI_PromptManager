"""The scan and Needs review anchor a file on the saver that wrote it (#390).

The workspace inspector (since #383) passes the file's name to the resolver,
which anchors the file on the one saver whose ``filename_prefix`` names it
(``_GraphResolver.saver_stages``). The scan and Needs review use the same
rule: a file whose branch that anchor resolves is never queued, and its
pending, undecided items leave the queue on the next scan. No match, several
matches or an unfinished walk anchor nothing, so such a file stays queued as
before. Apply re-resolves an inspection item with the file's name, so a
choice the anchor now contradicts is refused as stale evidence.
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
        InspectionTestCase,
        _counted,
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


class TestInspectionItemsAnchor(InspectionTestCase):
    async def test_file_its_saver_names_is_not_queued(self):
        self._roots()
        path = self._write("branch_1_00001_.png", graph=branch_graph(*PROMPTS))

        job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertEqual(row["path"], "branch_1_00001_.png")
        self.assertNotIn("more than one prompt could match", row["detail"])
        # Never queued is not linked: ingestion stays on the default profile.
        self.assertIsNone(self._linked_text(path))

    async def test_file_no_saver_names_stays_queued(self):
        self._roots()
        self._write("elsewhere_00001_.png", graph=branch_graph(*PROMPTS))

        await self._scan()

        (item,) = self._items()
        self.assertEqual(item["candidate_count"], len(PROMPTS))

    async def test_file_several_savers_name_stays_queued(self):
        self._roots()
        graph = branch_graph(*PROMPTS, prefixes=["same", "same", "other"])
        self._write("same_00001_.png", graph=graph)

        await self._scan()

        (item,) = self._items()
        self.assertEqual(item["candidate_count"], len(PROMPTS))

    async def test_pending_item_of_an_anchored_file_leaves_on_rescan(self):
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
        self.assertEqual(len(self._items()), 1)  # queued under the old rule

        job = await self._scan("all")

        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_untraced"], 1)

    async def test_apply_refuses_a_choice_the_anchor_contradicts(self):
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
    async def test_default_ambiguous_file_its_saver_names_is_not_queued(self):
        self._roots()
        path = self.output_dir / "side_0_00001_.png"
        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"))

        job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertNotIn("more than one prompt could match", row["detail"])
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
        self.assertEqual(item["resolver_version"], review_capture.RESOLVER_VERSION)

    async def test_pending_default_item_of_an_anchored_file_leaves_on_rescan(self):
        self._roots()
        write_png(
            self.output_dir / "side_0_00001_.png",
            prompt=saved_unlinkable_graph("cat", "dog"),
        )
        with patch.object(review_capture, "saver_anchored", return_value=False):
            await self._scan()
        self.assertEqual(len(self._items()), 1)  # queued under the old rule
        self.api._unanchored_inspections = {}  # a restart onto the new rule

        job = await self._scan("all")

        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_untraced"], 1)

    async def test_apply_refuses_a_default_choice_the_anchor_contradicts(self):
        self._require_routes()
        self._roots()
        path = self.output_dir / "side_0_00001_.png"
        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"))
        with patch.object(review_capture, "saver_anchored", return_value=False):
            await self._scan()
        item, detail, observed = await self._open()
        # side_0 is the saver of "cat"; choose the other branch.
        wrong = self._candidate(detail, "dog")

        status, data = await self._decide(
            item["id"], self._body(detail, observed, wrong["hash"])
        )

        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict", data)
        self.assertIsNone(self._linked_text(path))

    async def test_unanchored_file_is_not_inspected_again_until_it_changes(self):
        self._roots()
        path = self.output_dir / "unsaved_00001_.png"
        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"))
        with _counted(review_capture, "saver_anchored") as first:
            await self._scan()
        self.assertEqual(len(first), 1)

        with _counted(review_capture, "saver_anchored") as again:
            await self._scan("all")
        self.assertEqual(again, [])

        write_png(path, prompt=saved_unlinkable_graph("cat", "dog"), color=(0, 9, 0))
        with _counted(review_capture, "saver_anchored") as changed:
            await self._scan("all")
        self.assertEqual(len(changed), 1)

    async def test_decided_default_item_of_an_anchored_file_is_kept(self):
        self._require_routes()
        self._roots()
        write_png(
            self.output_dir / "side_0_00001_.png",
            prompt=saved_unlinkable_graph("cat", "dog"),
        )
        with patch.object(review_capture, "saver_anchored", return_value=False):
            await self._scan()
        item, detail, observed = await self._open()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "unresolved")
        )
        self.assertEqual(status, 200, data)
        before = (self._items(), self._decisions())

        await self._scan("all")

        self.assertEqual((self._items(), self._decisions()), before)


class TestSaverAnchored(InspectionTestCase):
    def test_only_the_anchor_counts(self):
        anchored = {"prompt": saved_unlinkable_graph("cat", "dog")}
        self.assertTrue(review_capture.saver_anchored(anchored, "side_1_00001_.png"))
        self.assertFalse(review_capture.saver_anchored(anchored, "nope_00001_.png"))
        self.assertFalse(review_capture.saver_anchored(anchored, None))
        # One sampler resolves without any name: the anchor decides nothing.
        single = {"prompt": branch_graph("make it night")}
        self.assertFalse(review_capture.saver_anchored(single, "branch_0_00001_.png"))
