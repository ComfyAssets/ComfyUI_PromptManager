"""Too many candidates are collapsed, not dropped (#657 addendum D3).

The maintainer's ConsistentFace batch: 83 samplers, 81 distinct prompts, above
``MAX_CANDIDATES`` (16), stored a ``limited`` item without candidates, so the
card offered nothing to choose. Now the choosable candidates are grouped by
their (positive, negative) texts, the groups ordered by size (descending) then
first appearance, and the first 16 kept, one representative each (its first
occurrence, so its sampler and hash are real). The evidence is ordinary and
unlimited, with ``collapsed: {shown, total, paths}``. ``limited`` remains only
for evidence that cannot be projected or canonicalized. Apply is unchanged.
"""

import hashlib
import json
import unittest

try:
    from tests.test_api_review import ReviewRoutesTestCase
    from tests.test_review import unlinkable_graph, write_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_review import ReviewRoutesTestCase
    from test_review import unlinkable_graph, write_png

from database.review_schema import MAX_CANDIDATES
from py.api.review import _candidate_still_offered
from utils import review_capture

POPULAR = "a popular prompt"
SECOND = "a second prompt"


def _candidate(sampler, text, negative="bad"):
    if text is None:
        return {"sampler_id": sampler, "state": "unresolved", "positive": None}
    return {
        "sampler_id": sampler,
        "state": "resolved",
        "positive": {"text": text},
        "negative": {"text": negative} if negative is not None else None,
    }


def _resolution(texts):
    return {
        "state": "ambiguous",
        "reason": "Several samplers",
        "candidates": [_candidate(str(i), t) for i, t in enumerate(texts)],
    }


def batch_texts():
    """Twenty singles, one text five times, another three times, one
    unresolved branch: 29 distinct candidates, 22 distinct prompts."""
    singles = [f"prompt {n}" for n in range(20)]
    texts = [None, singles[0], POPULAR, singles[1], SECOND]
    texts += [POPULAR] * 4 + [SECOND] * 2 + singles[2:]
    return texts


class TestCollapsedEvidence(unittest.TestCase):
    def test_groups_by_frequency_then_first_appearance(self):
        texts = batch_texts()

        evidence, count, limited = review_capture.review_evidence(_resolution(texts))

        self.assertFalse(limited)
        self.assertNotIn("limited", evidence)
        self.assertEqual(count, MAX_CANDIDATES)
        shown = [c["positive"]["text"] for c in evidence["candidates"]]
        singles = [f"prompt {n}" for n in range(14)]
        self.assertEqual(shown, [POPULAR, SECOND, *singles])
        self.assertEqual(
            evidence["collapsed"],
            {"shown": MAX_CANDIDATES, "total": 22, "paths": len(texts)},
        )

    def test_each_group_keeps_its_first_occurrence(self):
        evidence, _count, _limited = review_capture.review_evidence(
            _resolution(batch_texts())
        )
        by_text = {c["positive"]["text"]: c for c in evidence["candidates"]}
        self.assertEqual(by_text[POPULAR]["sampler_id"], "2")
        self.assertEqual(by_text[SECOND]["sampler_id"], "4")
        for candidate in evidence["candidates"]:
            projected = {k: v for k, v in candidate.items() if k != "hash"}
            text = json.dumps(projected, sort_keys=True, separators=(",", ":"))
            self.assertEqual(
                candidate["hash"], hashlib.sha256(text.encode("ascii")).hexdigest()
            )

    def test_negative_text_separates_groups(self):
        texts = [f"p{n}" for n in range(17)]
        resolution = _resolution(texts)
        resolution["candidates"].append(_candidate("x", "p0", negative="other"))
        resolution["candidates"].append(_candidate("y", "p0", negative=None))

        evidence, _count, _limited = review_capture.review_evidence(resolution)

        self.assertEqual(evidence["collapsed"]["total"], 19)
        pairs = [
            (c["positive"]["text"], (c["negative"] or {}).get("text"))
            for c in evidence["candidates"][:3]
        ]
        self.assertEqual(pairs, [("p0", "bad"), ("p1", "bad"), ("p2", "bad")])

    def test_sixteen_or_fewer_candidates_are_not_collapsed(self):
        texts = [f"p{n}" for n in range(MAX_CANDIDATES)]
        evidence, count, limited = review_capture.review_evidence(_resolution(texts))
        self.assertFalse(limited)
        self.assertEqual(count, MAX_CANDIDATES)
        self.assertNotIn("collapsed", evidence)

    def test_many_paths_to_few_prompts_are_not_collapsed(self):
        """Distinct samplers, three texts and unresolved branches (#657
        review): three groups fit, so the item stays ordinary. One candidate
        per text, and the run-time branches stay listed."""
        texts = [None, POPULAR, SECOND, "third"] * 5
        evidence, count, limited = review_capture.review_evidence(_resolution(texts))
        self.assertFalse(limited)
        self.assertNotIn("collapsed", evidence)
        shown = [
            (c["state"], (c["positive"] or {}).get("text"))
            for c in evidence["candidates"]
        ]
        self.assertEqual(
            shown[:4],
            [
                ("unresolved", None),
                ("resolved", POPULAR),
                ("resolved", SECOND),
                ("resolved", "third"),
            ],
        )
        self.assertEqual(count, 8)
        self.assertEqual(sum(1 for state, _ in shown if state == "resolved"), 3)

    def test_unresolved_branches_fill_up_to_the_bound_after_every_text(self):
        texts = [None] * 20 + [POPULAR, SECOND, "third"] * 2
        evidence, count, limited = review_capture.review_evidence(_resolution(texts))
        self.assertFalse(limited)
        self.assertNotIn("collapsed", evidence)
        self.assertEqual(count, MAX_CANDIDATES)
        resolved = [
            c["positive"]["text"] for c in evidence["candidates"] if c["positive"]
        ]
        self.assertEqual(resolved, [POPULAR, SECOND, "third"])
        sampler_ids = [c["sampler_id"] for c in evidence["candidates"]]
        self.assertEqual(sampler_ids, sorted(sampler_ids, key=int))

    def test_nothing_choosable_is_still_limited(self):
        texts = [None] * (MAX_CANDIDATES + 1)
        evidence, count, limited = review_capture.review_evidence(_resolution(texts))
        self.assertTrue(limited)
        self.assertEqual((count, evidence["candidates"]), (0, []))

    def test_unprojectable_evidence_is_still_limited(self):
        resolution = _resolution(batch_texts())
        resolution["candidates"][3]["positive"] = {"no": "text"}
        evidence, count, limited = review_capture.review_evidence(resolution)
        self.assertTrue(limited)
        self.assertNotIn("collapsed", evidence)

    def test_apply_accepts_any_offered_candidate_after_a_reorder(self):
        """Apply checks the chosen hash against every candidate the bytes
        offer, not against a fresh collapse (#657 review): a reorder that
        changes the representatives, or a text past the first 16, is still
        offered."""
        resolution = _resolution(batch_texts())
        evidence, _count, _limited = review_capture.review_evidence(resolution)
        chosen = {c["positive"]["text"]: c["hash"] for c in evidence["candidates"]}
        reordered = dict(resolution, candidates=resolution["candidates"][::-1])
        self.assertTrue(_candidate_still_offered(reordered, chosen[POPULAR]))
        tail = _resolution(batch_texts())["candidates"][-1]
        tail_hash = review_capture.candidate_hashes({"candidates": [tail]})
        (only,) = tail_hash
        self.assertTrue(_candidate_still_offered(resolution, only))
        self.assertFalse(_candidate_still_offered(resolution, "0" * 64))

    def test_apply_still_finds_a_representative(self):
        resolution = _resolution(batch_texts())
        evidence, _count, _limited = review_capture.review_evidence(resolution)
        for candidate in evidence["candidates"]:
            self.assertTrue(_candidate_still_offered(resolution, candidate["hash"]))


def many_prompts():
    """Unlinkable: the parser's sampler is unresolved, three samplers share
    ``POPULAR`` and seventeen have a prompt each (18 prompts, 21 paths)."""
    return unlinkable_graph(*([POPULAR] * 3 + [f"prompt {n}" for n in range(17)]))


class TestCollapsedItems(ReviewRoutesTestCase):
    def _write_many(self, name="many.png"):
        path = self.output_dir / name
        write_png(path, prompt=many_prompts())
        return path

    async def test_scan_records_collapsed_choices_and_apply_links(self):
        self._require_routes()
        self._roots()
        path = self._write_many()

        job = await self._scan()

        self.assertEqual(job["scope"]["review_items"], 1)
        (stored,) = self._items()
        self.assertFalse(stored["limited"])
        self.assertEqual(stored["candidate_count"], MAX_CANDIDATES)
        item, detail, observed = await self._open()
        self.assertFalse(detail["limited"])
        self.assertEqual(
            detail["collapsed"], {"shown": MAX_CANDIDATES, "total": 18, "paths": 21}
        )
        self.assertEqual(detail["candidates"][0]["positive"]["text"], POPULAR)
        popular = detail["candidates"][0]

        status, data = await self._decide(
            item["id"], self._body(detail, observed, popular["hash"])
        )

        self.assertEqual(status, 200, data)
        conn = self.api.db.model.get_connection()
        linked = conn.execute(
            "SELECT p.text FROM generated_images gi "
            "JOIN prompts p ON p.id=gi.prompt_id WHERE gi.image_path=?",
            (str(path),),
        ).fetchone()
        self.assertEqual(linked[0], POPULAR)

    async def test_detail_of_an_ordinary_item_has_no_collapsed(self):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        _item, detail, _observed = await self._open()
        self.assertIsNone(detail["collapsed"])

    async def test_an_old_limited_item_is_replaced_on_the_next_scan(self):
        """Items recorded before D3 carry the previous item version: the
        next scan records the collapsed item and it supersedes the pending,
        undecided limited one."""
        self._roots()
        path = self._write_many()
        old = self._old_limited_item(path)

        job = await self._scan("all")

        (item,) = self._items()
        self.assertNotEqual(item["id"], old)
        self.assertFalse(item["limited"])
        self.assertIn("collapsed", json.loads(item["evidence"]))
        self.assertEqual(job["scope"]["review_items"], 1)

    async def test_a_decided_old_limited_item_is_kept_and_not_requeued(self):
        self._roots()
        path = self._write_many()
        old = self._old_limited_item(path)
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='unresolved', revision=1 "
                "WHERE id=?",
                (old,),
            )
            conn.execute(
                "INSERT INTO media_review_decisions(id,item_id,decision_revision,"
                "created_at,outcome,candidate_hash,evidence) VALUES(?,?,?,?,?,?,?)",
                (
                    "00000000-0000-4000-8000-0000000000d3",
                    old,
                    1,
                    "2026-11-20T00:00:00+00:00",
                    "unresolved",
                    None,
                    '{"kind":"user-selection"}',
                ),
            )

        await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], [old])

    def _old_limited_item(self, path):
        """A limited item as the recorder stored it before D3 (version 1)."""
        facts = path.stat()
        locator, spelled = self._locator(path)
        evidence = {
            "resolver_version": 1,
            "reason": "Several samplers",
            "limited": True,
            "candidate_total": 21,
            "candidates": [],
        }
        stored = self.api.db.record_review_item(
            locator=locator,
            spelled_path=spelled,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=facts.st_size,
            mtime_ns=facts.st_mtime_ns,
            epoch=self.api.db.get_execution_epoch(),
            resolver_version=1,
            evidence=evidence,
            candidate_count=0,
            limited=True,
        )
        self.assertTrue(stored)
        (item,) = self._items()
        return item["id"]

    def _locator(self, path):
        import os

        relative = path.relative_to(self.output_dir).parts
        spelled = os.path.join(os.path.realpath(self.output_dir), *relative)
        return os.path.normcase(spelled), spelled


if __name__ == "__main__":
    unittest.main()
