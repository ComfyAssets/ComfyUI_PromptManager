"""Needs review: link an image to a library prompt by hand (#536).

The link is the reviewer's choice, never file evidence: it needs the same
capture of the same bytes as Apply, keeps the one-prompt-per-file rules, and
records provenance that cannot pose as what the file says.
"""

import json

try:
    from tests.test_api_review import ITEMS_URL, ReviewRoutesTestCase
    from tests.test_review import digest, unlinkable_graph, write_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_review import ITEMS_URL, ReviewRoutesTestCase
    from test_review import digest, unlinkable_graph, write_png

from utils.hashing import generate_prompt_hash


class LinkTestCase(ReviewRoutesTestCase):
    def _pair(self, positive="harbor", negative="blurry"):
        pair_id = self.api.db.save_prompt_pair(positive, negative)
        pair = self.api.db.get_prompt_pair(pair_id)
        return pair["positive_prompt_id"], pair_id

    def _link_body(self, detail, observed, positive_id, pair_id):
        return {
            "expected_epoch": observed["library_epoch"],
            "expected_revision": detail["revision"],
            "capture_token": observed["capture"]["token"],
            "sha256": observed["capture"]["sha256"],
            "dependency_fingerprint": detail["dependency_fingerprint"],
            "positive_id": positive_id,
            "pair_id": pair_id,
        }

    async def _link(self, item_id, body):
        return await self._json("POST", f"{ITEMS_URL}/{item_id}/link", json=body)

    async def _queued(self):
        self._record(self._ambiguous("two.png"))
        return await self._open()

    async def _refused(self, item, body, status, code):
        before = self._snapshot()
        got, data = await self._link(item["id"], body)
        self.assertEqual(got, status, data)
        self.assertEqual(data.get("code"), code, data)
        self.assertEqual(self._snapshot(), before)
        return data


class TestLink(LinkTestCase):
    async def test_links_the_chosen_recipe_and_records_a_manual_decision(self):
        positive_id, pair_id = self._pair()
        path = self.output_dir / "two.png"
        item, detail, observed = await self._queued()
        revision = self._review_revision()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, positive_id, pair_id)
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["item"]["state"], "selected")
        self.assertEqual(data["pair"]["id"], pair_id)
        conn = self.api.db.model.get_connection()
        image = conn.execute("SELECT id, prompt_id FROM generated_images").fetchone()
        self.assertEqual(image[1], positive_id)
        (link,) = self.api.db.get_image_prompt_pairs(image[0])
        (decision,) = self._decisions()
        self.assertEqual(link["pair_id"], pair_id)
        self.assertEqual(
            link["provenance"],
            {
                "kind": "manual-link",
                "decision_id": decision["id"],
                "reviewed_digest": digest(path),
                "resolver_version": detail_version(self, item),
                "positive_id": positive_id,
                "pair_id": pair_id,
            },
        )
        self.assertEqual(decision["outcome"], "selected")
        self.assertRegex(decision["candidate_hash"], r"^[0-9a-f]{64}$")
        self.assertNotIn(
            decision["candidate_hash"], [c["hash"] for c in detail["candidates"]]
        )
        self.assertEqual(decision["pair_id"], pair_id)
        evidence = json.loads(decision["evidence"])
        self.assertEqual(evidence["kind"], "manual-link")
        self.assertIsNone(evidence["candidate"])
        self.assertEqual(
            evidence["manual"], {"positive_id": positive_id, "pair_id": pair_id}
        )
        self.assertEqual(self._review_revision(), revision + 1)
        self._no_paths()

    async def test_a_manual_link_does_not_pose_as_file_evidence(self):
        positive_id, pair_id = self._pair()
        path = self.output_dir / "two.png"
        item, detail, observed = await self._queued()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, positive_id, pair_id)
        )
        self.assertEqual(status, 200, data)
        saved = self.api.db.get_image_prompt_info(str(path))
        self.assertEqual(saved["text"], "harbor")
        self.assertIsNone(saved.get("pair_context"))

    async def test_no_negative_links_a_pair_without_one(self):
        positive_id, _pair_id = self._pair()
        item, detail, observed = await self._queued()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, positive_id, None)
        )
        self.assertEqual(status, 200, data)
        pair = self.api.db.get_prompt_pair(data["pair"]["id"])
        self.assertEqual(pair["positive_prompt_id"], positive_id)
        self.assertIsNone(pair["negative_prompt_id"])
        decision = self._decisions()[0]
        self.assertEqual(
            json.loads(decision["evidence"])["manual"]["pair_id"], data["pair"]["id"]
        )

    async def test_a_prompt_without_recipes_can_be_linked(self):
        prompt_id = self.api.db.save_prompt(
            "lone", prompt_hash=generate_prompt_hash("lone")
        )
        item, detail, observed = await self._queued()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, prompt_id, None)
        )
        self.assertEqual(status, 200, data)

    async def test_an_existing_row_of_the_same_prompt_is_reused(self):
        positive_id, pair_id = self._pair()
        path = self._ambiguous("two.png")
        image_id = self.api.db.link_image_to_prompt(positive_id, str(path))
        self._record(path)
        item, detail, observed = await self._open()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, positive_id, pair_id)
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["pair"]["image_id"], image_id)

    async def test_a_candidates_own_item_can_still_be_linked_after_a_refusal(self):
        positive_id, pair_id = self._pair()
        item, detail, observed = await self._queued()
        body = self._link_body(detail, observed, positive_id, pair_id)
        await self._refused(
            item, {**body, "pair_id": pair_id + 99}, 409, "prompt-missing"
        )
        status, data = await self._link(item["id"], body)
        self.assertEqual(status, 200, data)

    async def test_a_later_scan_leaves_the_link_and_does_not_queue_it(self):
        positive_id, pair_id = self._pair()
        path = self.output_dir / "two.png"
        item, detail, observed = await self._queued()
        status, data = await self._link(
            item["id"], self._link_body(detail, observed, positive_id, pair_id)
        )
        self.assertEqual(status, 200, data)
        before = self._snapshot()
        self.assertIn(self._record_raw(path), ("decided", "duplicate"))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual((await self._list())["total"], 0)


class TestLinkRefusals(LinkTestCase):
    async def test_another_positive_on_the_file_is_an_association_conflict(self):
        positive_id, pair_id = self._pair()
        path = self._ambiguous("two.png")
        other = self.api.db.save_prompt(
            "other", prompt_hash=generate_prompt_hash("other")
        )
        self.api.db.link_image_to_prompt(other, str(path))
        self._record(path)
        item, detail, observed = await self._open()
        body = self._link_body(detail, observed, positive_id, pair_id)
        data = await self._refused(item, body, 409, "association-conflict")
        self.assertIn("linked", data["error"])

    async def test_a_prior_link_with_another_negative_is_an_association_conflict(self):
        positive_id, pair_id = self._pair()
        _same, other_pair = self._pair(negative="grainy")
        path = self._ambiguous("two.png")
        image_id = self.api.db.link_image_to_prompt(positive_id, str(path))
        self.api.db.link_image_to_pair(other_pair, image_id, {"kind": "test"})
        self._record(path)
        item, detail, observed = await self._open()
        body = self._link_body(detail, observed, positive_id, pair_id)
        await self._refused(item, body, 409, "association-conflict")

    async def test_changed_links_since_opening_are_refused(self):
        positive_id, pair_id = self._pair()
        path = self.output_dir / "two.png"
        item, detail, observed = await self._queued()
        other = self.api.db.save_prompt(
            "other", prompt_hash=generate_prompt_hash("other")
        )
        self.api.db.link_image_to_prompt(other, str(path))
        body = self._link_body(detail, observed, positive_id, pair_id)
        await self._refused(item, body, 409, "association-conflict")

    async def test_unusable_choices_are_refused_with_zero_writes(self):
        positive_id, pair_id = self._pair()
        foreign_positive, foreign_pair = self._pair("elsewhere", "bad")
        negative_only = self.api.db.get_prompt_pair(pair_id)["negative_prompt_id"]
        archived_positive, archived_pair = self._pair("old", "stale")
        self.api.db.archive_prompt_pair(archived_pair)
        item, detail, observed = await self._queued()
        for chosen_positive, chosen_pair in (
            (positive_id, foreign_pair),
            (positive_id, pair_id + 99),
            (positive_id + 99, None),
            (negative_only, None),
            (archived_positive, archived_pair),
        ):
            body = self._link_body(detail, observed, chosen_positive, chosen_pair)
            await self._refused(item, body, 409, "prompt-missing")
        self.assertTrue(foreign_positive)

    async def test_an_archived_prompt_is_refused(self):
        positive_id, pair_id = self._pair()
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompt_archive(prompt_id,archived_at) "
                "VALUES(?,'2026-01-01T00:00:00Z')",
                (positive_id,),
            )
        item, detail, observed = await self._queued()
        body = self._link_body(detail, observed, positive_id, pair_id)
        await self._refused(item, body, 409, "prompt-missing")

    async def test_a_stale_stored_hash_never_links_another_row(self):
        positive_id, _pair_id = self._pair()
        other = self.api.db.save_prompt(
            "other", prompt_hash=generate_prompt_hash("other")
        )
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE prompts SET hash=? WHERE id=?",
                (generate_prompt_hash("elsewhere"), other),
            )
            conn.execute(
                "UPDATE prompts SET hash=? WHERE id=?",
                (generate_prompt_hash("other"), positive_id),
            )
        item, detail, observed = await self._queued()
        body = self._link_body(detail, observed, other, None)
        await self._refused(item, body, 409, "prompt-missing")

    async def test_an_archived_bare_pair_is_refused(self):
        positive_id, pair_id = self._pair()
        bare = self.api.db.save_prompt_pair("harbor", "")
        self.api.db.archive_prompt_pair(bare)
        item, detail, observed = await self._queued()
        body = self._link_body(detail, observed, positive_id, None)
        await self._refused(item, body, 409, "prompt-missing")
        self.assertTrue(pair_id)

    async def test_body_validation(self):
        positive_id, pair_id = self._pair()
        item, detail, observed = await self._queued()
        good = self._link_body(detail, observed, positive_id, pair_id)
        before = self._snapshot()
        for bad in (
            {**good, "extra": 1},
            {k: v for k, v in good.items() if k != "pair_id"},
            {**good, "positive_id": "1"},
            {**good, "positive_id": True},
            {**good, "positive_id": 0},
            {**good, "pair_id": -1},
            {**good, "capture_token": None},
            {**good, "expected_revision": -1},
            {**good, "sha256": "x"},
        ):
            status, data = await self._link(item["id"], bad)
            self.assertEqual(status, 400, (bad, data))
        self.assertEqual(self._snapshot(), before)

    async def test_stale_revision_and_decided_item_are_refused(self):
        positive_id, pair_id = self._pair()
        item, detail, observed = await self._queued()
        good = self._link_body(detail, observed, positive_id, pair_id)
        await self._refused(
            item, {**good, "expected_revision": 5}, 409, "review-conflict"
        )
        status, data = await self._link(item["id"], good)
        self.assertEqual(status, 200, data)
        again = await self._observe(item["target"])
        await self._refused(
            item,
            {**good, "capture_token": again["capture"]["token"]},
            409,
            "review-conflict",
        )

    async def test_missing_item_expired_capture_and_changed_file_are_refused(self):
        positive_id, pair_id = self._pair()
        item, detail, observed = await self._queued()
        good = self._link_body(detail, observed, positive_id, pair_id)
        status, data = await self._link("00000000-0000-4000-8000-000000000000", good)
        self.assertEqual((status, data["code"]), (404, "review-missing"))
        await self._refused(
            item, {**good, "capture_token": "a" * 43}, 409, "capture-expired"
        )
        write_png(
            self.output_dir / "two.png", prompt=unlinkable_graph(), color=(1, 2, 3)
        )
        with_fresh = await self._observe(item["target"])
        await self._refused(
            item,
            {
                **good,
                "capture_token": with_fresh["capture"]["token"],
                "sha256": with_fresh["capture"]["sha256"],
            },
            409,
            "file-changed",
        )

    async def test_restored_storage_is_refused(self):
        positive_id, pair_id = self._pair()
        item, detail, observed = await self._queued()
        body = {
            **self._link_body(detail, observed, positive_id, pair_id),
            "expected_epoch": "00000000-0000-4000-8000-000000000000",
        }
        await self._refused(item, body, 409, "storage-changed")


def detail_version(case, item):
    conn = case.api.db.model.get_connection()
    return conn.execute(
        "SELECT resolver_version FROM media_review_items WHERE id=?", (item["id"],)
    ).fetchone()[0]
