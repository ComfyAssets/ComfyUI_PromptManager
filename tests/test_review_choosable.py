"""Needs review queues a file only when there is something to choose.

The maintainer's live queue (#284 / #288) held 39 items whose 11 candidates
all read "unresolved", "No positive / No negative": the ingestion resolver
found several sampler branches but could trace no prompt on any of them.
An ambiguous result is reviewable only when at least two candidates carry a
resolved prompt text. With none, the file could not be traced; with exactly
one, the other branches may still have made the file, so the one text is
not certain either (the resolver never flattens unknown semantics). Both
stay unresolved and are never queued, and a rescan removes the pending,
undecided items such files already have.
"""

import os
import unittest
from unittest.mock import patch

try:
    from tests.test_review import DELIMITERS, write_png
    from tests.test_review_outcome import OutcomeTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_review import DELIMITERS, write_png
    from test_review_outcome import OutcomeTestCase

from database.review_schema import evidence_offers_choice
from utils import review_capture


def opaque_graph(*opaque, traced=()):
    """Several KSamplers: ``opaque`` positives come from an encoder the
    ingestion resolver does not know, ``traced`` ones from CLIPTextEncode.

    The first sampler is opaque, so the scan's browser-parser fallback links
    nothing either.
    """
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
    }
    sides = [("OpaqueTextEncode", "prompt", t) for t in opaque]
    sides += [("CLIPTextEncode", "text", t) for t in traced]
    for index, (encoder_class, key, text) in enumerate(sides):
        encoder, sampler = str(10 + 2 * index), str(11 + 2 * index)
        graph[encoder] = {"class_type": encoder_class, "inputs": {key: text}}
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": ["2", 0],
            },
        }
    return graph


def _always_reviewable(_resolution):
    return True


class ChoosableTestCase(OutcomeTestCase):
    def _record_pre_fix(self, name):
        """An item as the pre-fix recorder stored it (no choosable test)."""
        with patch.object(review_capture, "reviewable", _always_reviewable):
            self._record(name)

    def _write(self, name, graph):
        write_png(self.output_dir / name, prompt=graph)
        return self.output_dir / name


class TestChoosableCandidates(unittest.TestCase):
    def _resolution(self, *states):
        candidates = []
        for index, state in enumerate(states):
            side = {"text": f"t{index}"} if state == "resolved" else None
            candidates.append(
                {"sampler_id": str(index), "state": state, "positive": side}
            )
        return {"state": "ambiguous", "candidates": candidates}

    def test_two_resolved_candidates_are_reviewable(self):
        resolution = self._resolution("resolved", "resolved", "unresolved")
        self.assertTrue(review_capture.reviewable(resolution))

    def test_none_or_one_resolved_candidate_is_not_reviewable(self):
        for states in ((), ("unresolved",) * 11, ("resolved", "unresolved")):
            with self.subTest(states=states):
                resolution = self._resolution(*states)
                self.assertFalse(review_capture.reviewable(resolution))

    def test_blank_or_missing_text_does_not_count(self):
        resolution = self._resolution("resolved", "resolved")
        resolution["candidates"][0]["positive"] = {"text": "  "}
        self.assertFalse(review_capture.reviewable(resolution))
        resolution["candidates"][0]["positive"] = None
        self.assertFalse(review_capture.reviewable(resolution))

    def test_nested_ambiguous_stage_counts_its_own_candidates(self):
        inner = self._resolution("resolved", "resolved")["candidates"]
        resolution = {
            "state": "ambiguous",
            "candidates": [{"state": "ambiguous", "candidates": inner}],
        }
        self.assertTrue(review_capture.reviewable(resolution))

    def test_a_result_that_is_not_ambiguous_is_not_reviewable(self):
        resolution = self._resolution("resolved", "resolved")
        resolution["state"] = "resolved"
        self.assertFalse(review_capture.reviewable(resolution))


class TestStoredEvidenceOffersChoice(unittest.TestCase):
    def test_unlimited_evidence_needs_two_choosable_candidates(self):
        side = {"text": "cat"}
        two = [{"state": "resolved", "positive": side}] * 2
        self.assertTrue(evidence_offers_choice({"candidates": two}))
        one = two[:1] + [{"state": "unresolved", "positive": None}]
        self.assertFalse(evidence_offers_choice({"candidates": one}))

    def test_limited_evidence_offers_a_choice_only_with_two_candidates(self):
        self.assertTrue(
            evidence_offers_choice({"limited": True, "candidate_total": 18})
        )
        for total in (0, 1, None, "18", True):
            with self.subTest(total=total):
                evidence = {"limited": True, "candidate_total": total}
                self.assertFalse(evidence_offers_choice(evidence))

    def test_malformed_evidence_offers_nothing(self):
        for evidence in (None, [], {"candidates": "x"}, {}):
            with self.subTest(evidence=evidence):
                self.assertFalse(evidence_offers_choice(evidence))


class TestNothingToChooseIsNotQueued(ChoosableTestCase):
    async def test_no_candidate_with_text_could_not_be_traced(self):
        self._roots()
        self._write("none.png", opaque_graph("cat", "dog"))

        job = await self._scan()

        self.assertEqual(self._items(), [])
        self.assertEqual(self._pending(), 0)
        self.assertEqual(job["scope"]["review_items"], 0)
        (row,) = job["outcomes"]["review"]
        self.assertEqual(row["path"], "none.png")
        self.assertIn("could not be traced", row["detail"])
        self.assertEqual(job["counters"]["review"], 1, job["counters"])
        self.assertEqual(self._capture_files(), [])

    async def test_exactly_one_candidate_with_text_stays_unresolved(self):
        """One traced branch beside untraced ones is not certain: no link."""
        self._roots()
        self._write("one.png", opaque_graph("cat", traced=("dog",)))

        job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertIn("could not be traced", row["detail"])
        self.assertEqual(job["counters"].get("added", 0), 0, job["counters"])
        self.assertEqual(job["counters"].get("known", 0), 0, job["counters"])

    async def test_two_candidates_with_text_are_queued(self):
        self._roots()
        self._write("two.png", opaque_graph("x", traced=("cat", "dog")))

        job = await self._scan()

        (item,) = self._items()
        self.assertEqual(os.path.basename(item["locator"]), "two.png")
        self.assertEqual(item["candidate_count"], 3)
        self.assertEqual(job["scope"]["review_items"], 1)
        (row,) = job["outcomes"]["review"]
        self.assertIn("more than one prompt could match", row["detail"])

    async def test_parameters_delimiter_ambiguity_is_not_queued(self):
        """Two A1111 negative delimiters offer no candidate to choose."""
        self._roots()
        write_png(self.output_dir / "delims.png", parameters=DELIMITERS)

        job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertIn("could not be traced", row["detail"])

    def test_recorder_reports_untraced_and_stores_nothing(self):
        self._roots()
        self._write("none.png", opaque_graph("cat", "dog"))
        self._write("one.png", opaque_graph("cat", traced=("dog",)))
        for name in ("none.png", "one.png"):
            with self.subTest(name=name):
                outcome = review_capture.record_ambiguous_file(
                    self.api.db, self.api._captures, self.output_dir, 0, name
                )
                self.assertEqual(outcome, "untraced")
        self.assertEqual(self._items(), [])
        self.assertEqual(self._capture_files(), [])

    def test_monitor_does_not_queue_an_untraced_output(self):
        self._roots()
        path = self._write("none.png", opaque_graph("cat", "dog"))

        self.assertEqual(self.api.record_review_file(str(path)), "untraced")
        self.assertEqual(self._items(), [])


class TestUntracedItemsLeaveTheQueue(ChoosableTestCase):
    async def test_rescan_removes_pending_items_without_a_choice(self):
        """The maintainer's 39 items: queued before the rule, gone on rescan."""
        self._roots()
        self._write("none.png", opaque_graph("cat", "dog"))
        self._write("one.png", opaque_graph("cat", traced=("dog",)))
        self._write("two.png", opaque_graph("x", traced=("cat", "dog")))
        for name in ("none.png", "one.png", "two.png"):
            self._record_pre_fix(name)
        revision = self._review_revision()
        self.assertEqual(self._pending(), 3)

        job = await self._scan("all")

        (item,) = self._items()
        self.assertEqual(os.path.basename(item["locator"]), "two.png")
        self.assertEqual(self._pending(), 1)
        self.assertGreater(self._review_revision(), revision)
        self.assertEqual(job["scope"]["review_untraced"], 2)
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertIn(
            "= 2 images left Needs review · nothing to choose", "\n".join(job["log"])
        )

    async def test_decided_items_of_untraced_files_are_never_touched(self):
        self._roots()
        for name in ("sel.png", "unres.png"):
            self._write(name, opaque_graph("cat", traced=(name, name + "2")))
            self._record_pre_fix(name)
        items = {os.path.basename(i["locator"]): i for i in self._items()}
        self._decide(items["sel.png"], "selected")
        self._decide(items["unres.png"], "unresolved")
        before = (self._items(), self._decisions())
        # The files now carry graphs without a choice, queued pre-fix.
        for name in ("sel.png", "unres.png", "pending.png"):
            self._write(name, opaque_graph("a cow", "dog"))
            self._record_pre_fix(name)
        self.assertEqual(self._pending(), 3)

        job = await self._scan("all")

        self.assertEqual((self._items(), self._decisions()), before)
        self.assertEqual(self._pending(), 0)
        self.assertEqual(job["scope"]["review_untraced"], 3)

    async def test_an_item_with_a_choice_survives_beside_an_untraced_one(self):
        """Removal judges each item by its own evidence: an item that offers
        a choice is not removed although the file's new bytes offer none."""
        self._roots()
        self._write("f.png", opaque_graph("x", traced=("cat", "dog")))
        self._record_pre_fix("f.png")
        (choosable,) = self._items()
        self._write("f.png", opaque_graph("a cow", "dog"))
        self._record_pre_fix("f.png")
        self.assertEqual(self._pending(), 2)

        job = await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], [choosable["id"]])
        self.assertEqual(job["scope"]["review_untraced"], 1)

    async def test_limited_items_go_only_without_enough_candidates(self):
        """A pre-fix delimiter item (no candidate) goes; a limited item of 18
        candidates may hold a choice and stays, though the bytes now offer
        none."""
        self._roots()
        write_png(self.output_dir / "delims.png", parameters=DELIMITERS)
        self._record_pre_fix("delims.png")
        many = [f"prompt {n}" for n in range(17)]
        self._write("many.png", opaque_graph("x", traced=many))
        self._record_pre_fix("many.png")
        kept = [i["id"] for i in self._items() if "many" in i["locator"]]
        self._write("many.png", opaque_graph("a cow", "dog"))

        job = await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], kept)
        self.assertEqual(job["scope"]["review_untraced"], 1)

    async def test_choosable_pending_item_stays(self):
        self._roots()
        self._write("two.png", opaque_graph("x", traced=("cat", "dog")))
        await self._scan()
        (item,) = self._items()

        job = await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], [item["id"]])
        self.assertEqual(job["scope"]["review_untraced"], 0)

    async def test_a_failed_removal_is_logged_and_counted_not_fatal(self):
        import logging
        import sqlite3

        self._roots()
        self._write("none.png", opaque_graph("cat", "dog"))
        self._record_pre_fix("none.png")
        with (
            patch.object(
                self.api.db,
                "settle_untraced_review_items",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            self.assertLogs("prompt_manager.api", logging.WARNING) as logs,
        ):
            job = await self._scan("all")

        self.assertEqual(job["state"], "finished")
        self.assertEqual(job["scope"]["review_errors"], 1)
        self.assertTrue(any("could not be removed" in line for line in logs.output))
        self.assertEqual(self._pending(), 1)
        await self._scan("all")
        self.assertEqual(self._pending(), 0)

    def test_monitor_removes_the_pending_item_of_an_untraced_output(self):
        self._roots()
        path = self._write("none.png", opaque_graph("cat", "dog"))
        self._record_pre_fix("none.png")
        # A pre-fix item of other bytes keeps the stat shortcut out of the way.
        self._write("none.png", opaque_graph("a cow", "dog"))

        self.assertEqual(self.api.record_review_file(str(path)), "untraced")
        self.assertEqual(self._items(), [])


if __name__ == "__main__":
    unittest.main()
