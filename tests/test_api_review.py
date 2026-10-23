"""Needs review decisions and queue reads (#249 E3).

The queue lists pending items oldest first behind an HMAC cursor bound to the
storage epoch; ``total`` is the exact pending count. A decision is explicit:
Apply links the chosen sampler's pair only under the ingester's compatible
rules and only for the current, re-hashed bytes of the reviewed file, in one
transaction with its decision row; Leave unresolved records the decision and
links nothing. Responses never carry a server path.
"""

import json
import ntpath
import os
import posixpath
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

try:
    from tests.test_review import (
        ReviewTestCase,
        ambiguous_graph,
        digest,
        write_png,
    )
except ImportError:  # discovered with tests/ as the top-level directory
    from test_review import ReviewTestCase, ambiguous_graph, digest, write_png

from database.review_schema import canonical_evidence, evidence_digest
from py.config import GalleryConfig
from utils import review_capture as review_capture_module
from utils.media_capture import (
    CaptureCapacity,
    CaptureChanged,
    CaptureStore,
    FileFacts,
)
from utils.hashing import generate_prompt_hash

try:  # guarded so a missing module fails assertions, not test loading
    from py.api import review as review_api
except ImportError:
    review_api = None

ITEMS_URL = "/prompt_manager/review/items"
# Every table a decision may touch; compared whole before and after.
WRITE_TABLES = (
    "prompts",
    "generated_images",
    "prompt_pairs",
    "image_prompt_pairs",
    "media_review_items",
    "media_review_decisions",
    "media_review_metadata",
)


class ReviewRoutesTestCase(ReviewTestCase):
    def _require_routes(self):
        self.assertIsNotNone(review_api, "py.api.review is not implemented")

    def _ambiguous(self, name="two.png", positives=("cat", "dog"), color=(255, 0, 0)):
        path = self.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_png(path, prompt=ambiguous_graph(*positives), color=color)
        return path

    def _record(self, path):
        self._roots()
        outcome = self.api.record_review_file(str(path))
        self.assertIn(outcome, ("recorded", "limited"), outcome)
        return outcome

    async def _json(self, method, url, **kwargs):
        resp = await self.client.request(method, url, **kwargs)
        data = await resp.json()
        self.responses.append(data)
        return resp.status, data

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.responses = []

    async def _list(self, query=""):
        status, data = await self._json("GET", ITEMS_URL + query)
        self.assertEqual(status, 200, data)
        return data

    async def _detail(self, item_id):
        status, data = await self._json("GET", f"{ITEMS_URL}/{item_id}")
        self.assertEqual(status, 200, data)
        return data

    async def _observe(self, target):
        status, data = await self._json(
            "POST", "/prompt_manager/image-annotations/observe", json={"target": target}
        )
        self.assertEqual(status, 200, data)
        return data

    async def _open(self):
        """The first pending item, its detail and a fresh capture of its file."""
        listed = await self._list()
        item = listed["items"][0]
        detail = await self._detail(item["id"])
        observed = await self._observe(item["target"])
        return item, detail, observed

    @staticmethod
    def _candidate(detail, positive):
        return next(
            c for c in detail["candidates"] if c["positive"]["text"] == positive
        )

    def _body(self, detail, observed, choice):
        return {
            "expected_epoch": observed["library_epoch"],
            "expected_revision": detail["revision"],
            "capture_token": observed["capture"]["token"],
            "sha256": observed["capture"]["sha256"],
            "dependency_fingerprint": detail["dependency_fingerprint"],
            "choice": choice,
        }

    async def _decide(self, item_id, body):
        return await self._json("POST", f"{ITEMS_URL}/{item_id}/decision", json=body)

    def _snapshot(self):
        conn = self.api.db.model.get_connection()
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in WRITE_TABLES
        }

    def _decisions(self):
        conn = self.api.db.model.get_connection()
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM media_review_decisions ORDER BY decision_revision"
            )
        ]

    def _no_paths(self):
        for data in self.responses:
            text = canonical_evidence({"r": data})
            self.assertNotIn(self.tmpdir, text)
            self.assertNotIn(self.tmpdir.replace(os.sep, "/"), text)
            self.assertNotIn(os.path.normcase(self.tmpdir), text)


class TestQueueReads(ReviewRoutesTestCase):
    async def test_total_is_exact_pending_count(self):
        self._require_routes()
        for index in range(3):
            self._record(self._ambiguous(f"f{index}.png", color=(index, 0, 0)))
        page = await self._list("?limit=1")
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 1)
        seen = [page["items"][0]["id"]]
        while page["next_cursor"]:
            page = await self._list(f"?limit=1&cursor={page['next_cursor']}")
            self.assertEqual(page["total"], 3)
            seen.extend(item["id"] for item in page["items"])
        rows = self._items()
        self.assertEqual(seen, [row["id"] for row in rows])
        item = (await self._list())["items"][0]
        self.assertEqual(
            set(item),
            {
                "id",
                "state",
                "revision",
                "created_at",
                "filename",
                "target",
                "unavailable",
                "candidate_count",
                "limited",
                "url",
                "thumbnail_url",
                "is_video",
            },
        )
        self.assertEqual(
            (item["state"], item["revision"], item["filename"]),
            ("pending", 0, "f0.png"),
        )
        # The card's media URL, formatted like the Images listing's (no stat).
        scope = item["target"]["root_scope"]
        self.assertEqual(
            item["url"],
            f"/prompt_manager/images/serve/f0.png?root=0&root_scope={scope}",
        )
        self.assertIs(item["is_video"], False)
        self.assertEqual(item["candidate_count"], 2)
        self.assertIs(item["unavailable"], False)
        self.assertEqual(item["target"]["path"], "f0.png")
        self.assertEqual(item["target"]["root_index"], 0)
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='selected', revision=1 "
                "WHERE id=?",
                (seen[0],),
            )
            conn.execute(
                "UPDATE media_review_items SET state='unresolved', revision=1 "
                "WHERE id=?",
                (seen[1],),
            )
        page = await self._list()
        self.assertEqual(page["total"], 1)
        self.assertEqual([i["id"] for i in page["items"]], [seen[2]])
        self.assertIsNone(page["next_cursor"])
        self._no_paths()

    async def test_empty_queue_and_limit_validation(self):
        self._require_routes()
        page = await self._list()
        self.assertEqual(
            (page["success"], page["total"], page["items"], page["next_cursor"]),
            (True, 0, [], None),
        )
        self.assertEqual(page["library_epoch"], self.api.db.get_execution_epoch())
        for query in ("?limit=0", "?limit=41", "?limit=abc", "?limit=-1", "?limit=1.5"):
            status, data = await self._json("GET", ITEMS_URL + query)
            self.assertEqual(status, 400, query)
            self.assertFalse(data["success"])
        status, data = await self._json("GET", ITEMS_URL + "?cursor=" + "x" * 5000)
        self.assertEqual(status, 400)

    async def test_cursor_survives_queue_changes_and_is_invalidated_by_restore(self):
        self._require_routes()
        for index in range(3):
            self._record(self._ambiguous(f"f{index}.png", color=(index, 0, 0)))
        first = await self._list("?limit=1")
        cursor = first["next_cursor"]
        self.assertTrue(cursor)
        # Tampered: one character changed, or a cursor of another shape.
        broken = ("A" if cursor[5] != "A" else "B").join((cursor[:5], cursor[6:]))
        for token in ("!!!", "QUJD", cursor[:40]):
            status, data = await self._json("GET", f"{ITEMS_URL}?cursor={token}")
            self.assertEqual(status, 400, (token, data))
        # Well-formed but not this process's signature (tampered, or opened
        # before a ComfyUI restart): a recoverable conflict (M3).
        status, data = await self._json("GET", f"{ITEMS_URL}?cursor={broken}")
        self.assertEqual(
            (status, data["code"], data["total"]), (409, "review-conflict", 3)
        )
        with patch.object(review_api, "_CURSOR_SECRET", b"\x01" * 32):
            status, data = await self._json("GET", f"{ITEMS_URL}?cursor={cursor}")
        self.assertEqual((status, data["code"]), (409, "review-conflict"), data)
        self.assertEqual(data["library_epoch"], first["library_epoch"])
        # The cursor is bound to the storage epoch only (M3): a decision and a
        # new item (the monitor records one per generation) leave "load more"
        # working, because the (created_at, id) keyset is stable under both.
        item, detail, observed = await self._open()
        self.assertEqual(item["id"], first["items"][0]["id"])
        status, data = await self._decide(
            item["id"], {**self._body(detail, observed, "unresolved")}
        )
        self.assertEqual(status, 200, data)
        self._record(self._ambiguous("late.png", color=(9, 9, 9)))
        rows = self._items()
        status, data = await self._json("GET", f"{ITEMS_URL}?limit=1&cursor={cursor}")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["total"], 3)
        self.assertEqual([i["filename"] for i in data["items"]], ["f1.png"])
        seen = ["f1.png"]
        while data["next_cursor"]:
            data = await self._list(f"?limit=1&cursor={data['next_cursor']}")
            seen.extend(i["filename"] for i in data["items"])
        self.assertEqual(seen, ["f1.png", "f2.png", "late.png"])
        self.assertEqual(len(rows), 4)
        # A decision on an item after the cursor drops it from later pages.
        second = await self._list("?limit=1")
        self.assertEqual([i["filename"] for i in second["items"]], ["f1.png"])
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='selected', revision=1 "
                "WHERE id=(SELECT i.id FROM media_review_items i "
                "WHERE i.spelled_path LIKE ?)",
                ("%f2.png",),
            )
        data = await self._list(f"?limit=1&cursor={second['next_cursor']}")
        self.assertEqual(
            ([i["filename"] for i in data["items"]], data["total"]),
            (["late.png"], 2),
        )
        fresh = (await self._list("?limit=1"))["next_cursor"]
        conn = self.api.db.model.get_connection()
        with conn:  # a restore replaces the storage epoch
            conn.execute(
                "UPDATE execution_history_metadata SET "
                "storage_epoch='00000000-0000-4000-8000-000000000000'"
            )
        status, data = await self._json("GET", f"{ITEMS_URL}?limit=1&cursor={fresh}")
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "storage-changed")
        self.assertEqual(data["library_epoch"], "00000000-0000-4000-8000-000000000000")
        self._no_paths()

    async def test_detail_size_cap_and_limited_item_has_no_candidates(self):
        self._require_routes()
        self._roots()
        limited = self.output_dir / "limited.png"
        write_png(
            limited,
            parameters=(
                "cat\nNegative prompt: blur\nNegative prompt: noise\n"
                "Steps: 20, Sampler: Euler"
            ),
        )
        self.assertEqual(self.api.record_review_file(str(limited)), "limited")
        self._record(self._ambiguous("two.png"))
        items = (await self._list())["items"]
        self.assertEqual([i["limited"] for i in items], [True, False])
        detail = await self._detail(items[0]["id"])
        self.assertEqual((detail["limited"], detail["candidates"]), (True, []))
        self.assertEqual(detail["candidate_count"], 0)
        detail = await self._detail(items[1]["id"])
        self.assertEqual(len(detail["candidates"]), 2)
        candidate = self._candidate(detail, "cat")
        self.assertEqual(
            set(candidate),
            {"hash", "sampler_id", "state", "reason", "label", "positive", "negative"},
        )
        self.assertEqual(candidate["positive"], {"text": "cat"})
        self.assertEqual(candidate["negative"], {"text": "bad"})
        self.assertEqual(candidate["state"], "resolved")
        self.assertEqual(candidate["label"], "cat / bad")
        self.assertEqual(detail["sha256"], digest(self.output_dir / "two.png"))
        self.assertRegex(detail["dependency_fingerprint"], r"^[0-9a-f]{64}$")
        with patch.object(review_api, "MAX_DETAIL_BYTES", 200):
            capped = await self._detail(items[1]["id"])
        self.assertEqual((capped["limited"], capped["candidates"]), (True, []))
        self.assertEqual(capped["id"], items[1]["id"])
        for missing in ("00000000-0000-4000-8000-000000000000", "not-a-uuid"):
            status, data = await self._json("GET", f"{ITEMS_URL}/{missing}")
            self.assertEqual(status, 404, data)
            self.assertFalse(data["success"])
        self._no_paths()

    async def test_label_uses_library_title_of_a_saved_prompt(self):
        self._require_routes()
        prompt_id = self.api.db.save_prompt(
            "cat", prompt_hash=generate_prompt_hash("cat")
        )
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (prompt_id, "Dunes"),
            )
        self._record(self._ambiguous("two.png"))
        item = (await self._list())["items"][0]
        detail = await self._detail(item["id"])
        self.assertEqual(self._candidate(detail, "cat")["label"], "Dunes / bad")
        self.assertEqual(self._candidate(detail, "dog")["label"], "dog / bad")

    async def test_unavailable_when_no_current_root_lists_the_file(self):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        other = self.output_dir / "elsewhere"
        other.mkdir()
        from py.config import GalleryConfig

        GalleryConfig.MONITORING_DIRECTORIES = [self._root(other)]
        item = (await self._list())["items"][0]
        self.assertEqual(
            (item["unavailable"], item["target"], item["filename"]),
            (True, None, "two.png"),
        )
        self.assertEqual((item["url"], item["is_video"]), (None, False))
        self.assertIsNone(item["thumbnail_url"])
        detail = await self._detail(item["id"])
        self.assertIs(detail["unavailable"], True)
        self._no_paths()

    async def test_queue_cards_use_the_images_listing_thumbnail(self):
        # M10: a known thumbnail is the card's media, as on the Images page;
        # without one the card falls back to the original.
        self._require_routes()
        from py.api.images import _output_image_entry

        path = self._ambiguous("sub/f0.png")
        self._record(path)
        item = (await self._list())["items"][0]
        self.assertIsNone(item["thumbnail_url"])
        thumb = self.output_dir / "thumbnails" / "sub" / "f0_thumb.png"
        thumb.parent.mkdir(parents=True)
        write_png(thumb, prompt=ambiguous_graph("cat", "dog"), color=(1, 2, 3))
        item = (await self._list())["items"][0]
        scope = item["target"]["root_scope"]
        listed = _output_image_entry(path, self.output_dir, 0, root_scope=scope)
        self.assertIsNotNone(listed["thumbnail_url"])
        self.assertEqual(item["thumbnail_url"], listed["thumbnail_url"])
        self.assertEqual(item["url"], listed["url"])
        detail = await self._detail(item["id"])
        self.assertEqual(detail["thumbnail_url"], listed["thumbnail_url"])
        self._no_paths()

    def test_target_formatting_keeps_the_recorded_spelling_on_windows(self):
        self._require_routes()
        spelled = "C:\\Out\\Sub\\Image_0001.PNG"
        locator = ntpath.normcase(spelled)
        found = review_api.format_review_target(
            spelled, locator, [(0, "D:\\Other"), (1, "C:\\Out")], pathmod=ntpath
        )
        self.assertEqual(found, (1, "Sub/Image_0001.PNG"))
        self.assertIsNone(
            review_api.format_review_target(
                spelled, locator, [(0, "C:\\Outer")], pathmod=ntpath
            )
        )
        # A recorded spelling that no longer folds to the locator is refused.
        self.assertIsNone(
            review_api.format_review_target(
                spelled, "c:\\out\\sub\\other.png", [(0, "C:\\Out")], pathmod=ntpath
            )
        )
        self.assertEqual(
            review_api.format_review_target(
                "/out/a/b.png", "/out/a/b.png", [(0, "/out")], pathmod=posixpath
            ),
            (0, "a/b.png"),
        )


class TestDecisions(ReviewRoutesTestCase):
    async def test_apply_candidate_links_pair_and_records_decision_atomically(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        self._record(path)
        revision = self._review_revision()
        item, detail, observed = await self._open()
        cat = self._candidate(detail, "cat")
        before = self._snapshot()
        # A failure after the link rolls the link back with everything else.
        with patch.object(
            type(self.api.db),
            "_insert_review_decision",
            side_effect=sqlite3.OperationalError("disk I/O error at /secret/path"),
        ):
            status, data = await self._decide(
                item["id"], self._body(detail, observed, cat["hash"])
            )
        self.assertEqual(status, 500, data)
        self.assertEqual(data["code"], "decision-failed")
        self.assertNotIn("secret", data["error"])
        self.assertEqual(self._snapshot(), before)

        # A failed decision keeps its capture for the retry.
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["success"])
        self.assertEqual(data["total"], 0)
        self.assertEqual(
            (data["item"]["id"], data["item"]["state"], data["item"]["revision"]),
            (item["id"], "selected", 1),
        )
        conn = self.api.db.model.get_connection()
        image = conn.execute(
            "SELECT gi.id, p.text FROM generated_images gi "
            "JOIN prompts p ON p.id=gi.prompt_id WHERE gi.image_path=?",
            (str(path),),
        ).fetchone()
        self.assertEqual(image[1], "cat")
        pair = conn.execute(
            "SELECT pp.id, p.text, n.text FROM prompt_pairs pp "
            "JOIN prompts p ON p.id=pp.positive_prompt_id "
            "JOIN prompts n ON n.id=pp.negative_prompt_id"
        ).fetchall()
        self.assertEqual([tuple(row)[1:] for row in pair], [("cat", "bad")])
        self.assertEqual(data["pair"]["id"], pair[0][0])
        self.assertEqual(data["pair"]["image_id"], image[0])
        links = self.api.db.get_image_prompt_pairs(image[0])
        self.assertEqual(len(links), 1)
        provenance = links[0]["provenance"]
        self.assertEqual(provenance["kind"], "user-selection")
        self.assertEqual(provenance["candidate_hash"], cat["hash"])
        self.assertEqual(provenance["reviewed_digest"], digest(path))
        self.assertEqual(provenance["resolver_version"], 1)
        self.assertEqual(provenance["positive"]["text"], "cat")
        (decision,) = self._decisions()
        self.assertEqual(provenance["decision_id"], decision["id"])
        self.assertEqual(
            (
                decision["item_id"],
                decision["decision_revision"],
                decision["outcome"],
                decision["candidate_hash"],
                decision["pair_id"],
                decision["image_id"],
            ),
            (item["id"], 1, "selected", cat["hash"], pair[0][0], image[0]),
        )
        self.assertEqual(self._review_revision(), revision + 1)
        self.assertEqual((await self._list())["total"], 0)
        self.assertEqual(self._capture_files(), [])
        self._no_paths()

    async def test_apply_conflicting_legacy_owner_returns_association_conflict_with_zero_writes(  # noqa: E501
        self,
    ):
        self._require_routes()
        path = self._ambiguous("two.png")
        # The monitor's fallback link: the file is linked to "cat" already.
        prompt_id = self.api.db.save_prompt(
            "cat", prompt_hash=generate_prompt_hash("cat")
        )
        image_id = self.api.db.link_image_to_prompt(prompt_id, str(path))
        self._record(path)
        item, detail, observed = await self._open()
        before = self._snapshot()
        dog = self._candidate(detail, "dog")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, dog["hash"])
        )
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "association-conflict")
        self.assertIn("linked", data["error"])
        self.assertEqual(self._snapshot(), before)
        # The compatible choice reuses the existing image row.
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["pair"]["image_id"], image_id)
        conn = self.api.db.model.get_connection()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM generated_images").fetchone()[0], 1
        )
        self._no_paths()

    async def test_changed_dependencies_are_an_association_conflict(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        self._record(path)
        item, detail, observed = await self._open()
        # Another writer links the file after the detail was read.
        prompt_id = self.api.db.save_prompt(
            "cat", prompt_hash=generate_prompt_hash("cat")
        )
        self.api.db.link_image_to_prompt(prompt_id, str(path))
        before = self._snapshot()
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "association-conflict")
        self.assertNotEqual(
            data["dependency_fingerprint"], detail["dependency_fingerprint"]
        )
        self.assertEqual(self._snapshot(), before)

    async def test_leave_unresolved_records_decision_and_leaves_pending_list(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        self._record(path)
        self._record(self._ambiguous("other.png", color=(0, 0, 9)))
        item, detail, _observed = await self._open()
        body = {
            "expected_epoch": self.api.db.get_execution_epoch(),
            "expected_revision": 0,
            "capture_token": None,
            "sha256": detail["sha256"],
            "dependency_fingerprint": detail["dependency_fingerprint"],
            "choice": "unresolved",
        }
        status, data = await self._decide(item["id"], body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["item"]["state"], "unresolved")
        self.assertEqual(data["total"], 1)
        self.assertNotIn("pair", data)
        (decision,) = self._decisions()
        self.assertEqual(
            (decision["outcome"], decision["candidate_hash"], decision["pair_id"]),
            ("unresolved", None, None),
        )
        conn = self.api.db.model.get_connection()
        for table in ("prompt_pairs", "image_prompt_pairs", "generated_images"):
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table
            )
        listed = await self._list()
        self.assertEqual(listed["total"], 1)
        self.assertNotIn(item["id"], [i["id"] for i in listed["items"]])
        # The item still answers its detail, now decided.
        self.assertEqual((await self._detail(item["id"]))["state"], "unresolved")
        self._no_paths()

    async def test_changed_bytes_or_evidence_reject_apply(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        self._record(path)
        item, detail, observed = await self._open()
        cat = self._candidate(detail, "cat")
        before = self._snapshot()

        # The file is rewritten after the capture: the re-hash refuses it.
        write_png(path, prompt=ambiguous_graph("cat", "dog"), color=(0, 255, 0))
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual((status, data["code"]), (409, "file-changed"), data)
        self.assertEqual(self._snapshot(), before)

        # A fresh capture of the new bytes is not the reviewed item's bytes.
        observed = await self._observe(item["target"])
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual((status, data["code"]), (409, "file-changed"), data)
        self.assertEqual(
            self._snapshot()["media_review_items"], before["media_review_items"]
        )

        # Back to the reviewed bytes, but the evidence they yield has changed.
        write_png(path, prompt=ambiguous_graph("cat", "dog"))
        self.assertEqual(digest(path), detail["sha256"])
        observed = await self._observe(item["target"])
        changed = {
            "state": "ambiguous",
            "reason": "multiple samplers",
            "candidates": [],
        }
        with patch("utils.review_capture.resolve_prompt_pair", return_value=changed):
            status, data = await self._decide(
                item["id"], self._body(detail, observed, cat["hash"])
            )
        self.assertEqual((status, data["code"]), (409, "review-conflict"), data)

        # A choice that is not one of the item's candidates is refused.
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "f" * 64)
        )
        self.assertEqual(status, 400, data)
        # A restore since the capture.
        status, data = await self._decide(
            item["id"],
            {
                **self._body(detail, observed, cat["hash"]),
                "expected_epoch": "00000000-0000-4000-8000-000000000000",
            },
        )
        self.assertEqual((status, data["code"]), (409, "storage-changed"), data)
        # A stale revision.
        status, data = await self._decide(
            item["id"],
            {**self._body(detail, observed, cat["hash"]), "expected_revision": 3},
        )
        self.assertEqual((status, data["code"]), (409, "review-conflict"), data)
        # Apply needs a capture; Leave unresolved does not.
        status, data = await self._decide(
            item["id"],
            {**self._body(detail, observed, cat["hash"]), "capture_token": None},
        )
        self.assertEqual(status, 400, data)
        # A capture of another file.
        other = self._ambiguous("other.png", color=(1, 2, 3))
        self._record(other)
        target = {**item["target"], "path": "other.png"}
        foreign = await self._observe(target)
        status, data = await self._decide(
            item["id"],
            {
                **self._body(detail, observed, cat["hash"]),
                "capture_token": foreign["capture"]["token"],
                "sha256": foreign["capture"]["sha256"],
            },
        )
        self.assertEqual((status, data["code"]), (409, "capture-expired"), data)
        self.assertEqual(self._decisions(), [])
        self.assertEqual(self._items()[0]["state"], "pending")
        self._no_paths()

    async def test_decision_body_validation(self):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        item, detail, observed = await self._open()
        good = self._body(detail, observed, "unresolved")
        cases = [
            {**good, "extra": 1},
            {k: v for k, v in good.items() if k != "choice"},
            {**good, "expected_epoch": "nope"},
            {**good, "expected_revision": -1},
            {**good, "expected_revision": True},
            {**good, "capture_token": "bad token!"},
            {**good, "sha256": "A" * 64},
            {**good, "dependency_fingerprint": "x"},
            {**good, "choice": "maybe"},
            {**good, "choice": 7},
            {**good, "sha256": "0" * 64},
        ]
        for body in cases:
            with self.subTest(body=body):
                status, data = await self._decide(item["id"], body)
                self.assertEqual(status, 400, data)
                self.assertFalse(data["success"])
        status, data = await self._json(
            "POST", f"{ITEMS_URL}/{item['id']}/decision", data=b"[1"
        )
        self.assertEqual(status, 400, data)
        status, data = await self._decide("00000000-0000-4000-8000-000000000000", good)
        self.assertEqual(status, 404, data)
        self.assertEqual(self._decisions(), [])

    async def test_duplicate_apply_after_commit_is_review_conflict_with_current_state(
        self,
    ):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        item, detail, observed = await self._open()
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 200, data)
        after = self._snapshot()
        observed = await self._observe(item["target"])
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict")
        self.assertEqual(
            (data["item"]["state"], data["item"]["revision"]), ("selected", 1)
        )
        self.assertEqual(data["total"], 0)
        self.assertEqual(self._snapshot(), after)
        self.assertEqual(len(self._decisions()), 1)

    async def test_tag_save_never_changes_review_item(self):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        item, _detail, observed = await self._open()
        before = self._snapshot()
        status, data = await self._json(
            "PUT",
            "/prompt_manager/image-annotations/tags",
            json={
                "target": item["target"],
                "capture_token": observed["capture"]["token"],
                "sha256": observed["capture"]["sha256"],
                "expected_epoch": observed["library_epoch"],
                "expected_tag_revision": observed["tag_revision"],
                "tags": ["sunset"],
            },
        )
        self.assertEqual(status, 200, data)
        after = self._snapshot()
        for table in (
            "media_review_items",
            "media_review_decisions",
            "media_review_metadata",
            "generated_images",
            "image_prompt_pairs",
        ):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual((await self._list())["total"], 1)

    async def test_run_and_usage_counts_unchanged(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        prompt_id = self.api.db.save_prompt(
            "cat", prompt_hash=generate_prompt_hash("cat")
        )
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE prompts SET run_count=5, last_used_at='2026-01-01T00:00:00Z' "
                "WHERE id=?",
                (prompt_id,),
            )
        self._record(path)
        executions = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'execution_%'"
        ).fetchall()
        counts = {
            row[0]: conn.execute(f"SELECT COUNT(*) FROM {row[0]}").fetchone()[0]
            for row in executions
        }
        item, detail, observed = await self._open()
        status, data = await self._decide(
            item["id"],
            self._body(detail, observed, self._candidate(detail, "cat")["hash"]),
        )
        self.assertEqual(status, 200, data)
        rows = conn.execute(
            "SELECT text, run_count, last_used_at FROM prompts"
        ).fetchall()
        self.assertIn(("cat", 5, "2026-01-01T00:00:00Z"), [tuple(r) for r in rows])
        self.assertEqual({tuple(r)[1] for r in rows if r[0] != "cat"}, {0})
        self.assertEqual(
            {
                name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in counts
            },
            counts,
        )
        (pair,) = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(pair["run_count"], 0)

    async def test_no_absolute_paths_in_responses(self):
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        item, detail, observed = await self._open()
        await self._json("GET", f"{ITEMS_URL}?cursor=bad")
        await self._decide(item["id"], {**self._body(detail, observed, "f" * 64)})
        with patch.object(
            type(self.api.db),
            "review_queue_page",
            side_effect=sqlite3.OperationalError(f"no such table at {self.tmpdir}"),
        ):
            status, data = await self._json("GET", ITEMS_URL)
        self.assertEqual((status, data["code"]), (500, "review-failed"))
        with patch.object(
            type(self.api.db),
            "review_item",
            side_effect=sqlite3.OperationalError(f"locked {self.tmpdir}"),
        ):
            status, data = await self._json("GET", f"{ITEMS_URL}/{item['id']}")
        self.assertEqual((status, data["code"]), (500, "review-failed"))
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "unresolved")
        )
        self.assertEqual(status, 200, data)
        self._no_paths()


class TestPhysicalIdentity(ReviewRoutesTestCase):
    """Apply sees a legacy row of the same file under any spelling (I1).

    The roots are real paths, but the monitor links a file under the
    directory it watches: a symlink, a Windows junction, ``subst`` or a
    mapped drive reaches the same file under another spelling.
    """

    def _monitor_link(self, text, spelled):
        prompt_id = self.api.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text)
        )
        return self.api.db.link_image_to_prompt(prompt_id, str(spelled))

    def _rows(self):
        conn = self.api.db.model.get_connection()
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT gi.image_path, p.text FROM generated_images gi "
                "JOIN prompts p ON p.id=gi.prompt_id ORDER BY gi.id"
            )
        ]

    async def _conflict_then_compatible(self, alias_path):
        image_id = self._monitor_link("cat", alias_path)
        item, detail, observed = await self._open()
        before = self._snapshot()
        dog = self._candidate(detail, "dog")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, dog["hash"])
        )
        self.assertEqual((status, data.get("code")), (409, "association-conflict"))
        self.assertEqual(self._snapshot(), before)
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 200, data)
        # The monitor's row is enriched; no second row for the same file.
        self.assertEqual(data["pair"]["image_id"], image_id)
        self.assertEqual(self._rows(), [(str(alias_path), "cat")])
        self._no_paths()

    def _symlinked_root(self):
        alias = Path(self.tmpdir) / "alias-output"
        try:
            os.symlink(str(self.output_dir), str(alias), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        return alias

    async def test_symlinked_root_legacy_row_is_an_association_conflict(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        alias = self._symlinked_root()
        self._record(path)
        await self._conflict_then_compatible(alias / "two.png")

    async def test_junction_or_mapped_drive_legacy_row_is_seen(self):
        """A portable fake of a junction: its realpath is the real root."""
        self._require_routes()
        path = self._ambiguous("two.png")
        self._record(path)
        junction = os.path.join(self.tmpdir, "Junction")
        real = os.path.realpath

        def fake_realpath(value, *args, **kwargs):
            text = os.fspath(value)
            if os.path.normcase(text).startswith(os.path.normcase(junction)):
                text = str(self.output_dir) + text[len(junction) :]
            return real(text, *args, **kwargs)

        with patch.object(review_api, "_realpath", side_effect=fake_realpath):
            await self._conflict_then_compatible(os.path.join(junction, "two.png"))

    async def test_two_rows_under_two_spellings_are_an_association_conflict(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        alias = self._symlinked_root()
        self._monitor_link("cat", alias / "two.png")
        self._monitor_link("cat", path)
        self._record(path)
        item, detail, observed = await self._open()
        before = self._snapshot()
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual((status, data.get("code")), (409, "association-conflict"))
        self.assertEqual(self._snapshot(), before)

    async def test_same_name_in_another_folder_is_not_the_file(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        other = self.output_dir / "elsewhere" / "two.png"
        other.parent.mkdir(parents=True)
        write_png(other, prompt=ambiguous_graph("cat", "dog"), color=(9, 9, 9))
        self._monitor_link("dog", other)
        self._record(path)
        item, detail, observed = await self._open()
        cat = self._candidate(detail, "cat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            sorted(self._rows()), sorted([(str(other), "dog"), (str(path), "cat")])
        )

    async def test_a_new_alias_row_after_the_check_is_a_changed_conflict(self):
        self._require_routes()
        path = self._ambiguous("two.png")
        alias = self._symlinked_root()
        self._record(path)
        item, detail, observed = await self._open()
        cat = self._candidate(detail, "cat")
        # The monitor links the file under its alias after the reviewer opened it.
        self._monitor_link("cat", alias / "two.png")
        before = self._snapshot()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, cat["hash"])
        )
        self.assertEqual((status, data.get("code")), (409, "association-conflict"))
        self.assertNotEqual(
            data["dependency_fingerprint"], detail["dependency_fingerprint"]
        )
        self.assertEqual(self._snapshot(), before)


class TestApplyRefusals(ReviewRoutesTestCase):
    """Every Apply refusal answers a code and writes nothing (I2, M1, M4)."""

    async def _opened(self):
        path = self._ambiguous("two.png")
        self._record(path)
        item, detail, observed = await self._open()
        return path, item, detail, observed, self._candidate(detail, "cat")

    async def _refused(self, item, body, status, code=None):
        before = self._snapshot()
        got, data = await self._decide(item["id"], body)
        self.assertEqual(got, status, data)
        if code is not None:
            self.assertEqual(data["code"], code, data)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._decisions(), [])
        return data

    async def test_resolver_change_keeps_the_chosen_candidate_applicable(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        real = review_capture_module.resolve_prompt_pair

        def reworded(metadata):
            resolution = real(metadata)
            return {
                **resolution,
                "reason": "a reworded reason from a newer resolver",
                "candidates": list(reversed(resolution["candidates"])),
            }

        with (
            patch.object(
                review_capture_module, "resolve_prompt_pair", side_effect=reworded
            ),
            patch.object(review_capture_module, "RESOLVER_VERSION", 2),
        ):
            status, data = await self._decide(
                item["id"], self._body(detail, observed, cat["hash"])
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["item"]["state"], "selected")

    async def test_chosen_candidate_no_longer_offered_is_a_review_conflict(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        real = review_capture_module.resolve_prompt_pair

        def without_cat(metadata):
            resolution = real(metadata)
            kept = [
                c
                for c in resolution["candidates"]
                if (c.get("positive") or {}).get("text") != "cat"
            ]
            return {**resolution, "candidates": kept * 2}

        with patch.object(
            review_capture_module, "resolve_prompt_pair", side_effect=without_cat
        ):
            await self._refused(
                item,
                self._body(detail, observed, cat["hash"]),
                409,
                "review-conflict",
            )

    async def test_decision_passes_the_verified_digest(self):
        self._require_routes()
        path, item, detail, observed, cat = await self._opened()
        real = type(self.api.db).decide_review_item
        seen = []

        def spy(db, **kwargs):
            seen.append(kwargs["sha256"])
            return real(db, **kwargs)

        with patch.object(type(self.api.db), "decide_review_item", spy):
            status, data = await self._decide(
                item["id"], self._body(detail, observed, cat["hash"])
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(seen, [digest(path)])

    async def test_expired_capture(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        self.api._captures.release(observed["capture"]["token"])
        await self._refused(
            item, self._body(detail, observed, cat["hash"]), 409, "capture-expired"
        )

    async def test_a_candidate_that_cannot_be_applied(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        conn = self.api.db.model.get_connection()
        (text,) = conn.execute(
            "SELECT evidence FROM media_review_items WHERE id=?", (item["id"],)
        ).fetchone()
        evidence = json.loads(text)
        for candidate in evidence["candidates"]:
            if candidate["hash"] == cat["hash"]:
                candidate["state"] = "unresolved"
        text = canonical_evidence(evidence)
        with conn:
            conn.execute(
                "UPDATE media_review_items SET evidence=?, evidence_hash=? WHERE id=?",
                (text, evidence_digest(text), item["id"]),
            )
        data = await self._refused(item, self._body(detail, observed, cat["hash"]), 400)
        self.assertIn("cannot be applied", data["error"])

    async def test_leave_unresolved_with_another_digest(self):
        self._require_routes()
        _path, item, detail, observed, _cat = await self._opened()
        body = {
            **self._body(detail, observed, "unresolved"),
            "capture_token": None,
            "sha256": "0" * 64,
        }
        data = await self._refused(item, body, 400)
        self.assertIn("does not match the item", data["error"])

    async def test_root_no_longer_configured_is_source_changed(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        # Another root takes index 0.
        elsewhere = self.output_dir / "elsewhere"
        elsewhere.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(elsewhere)]
        await self._refused(
            item, self._body(detail, observed, cat["hash"]), 409, "source-changed"
        )

    async def test_file_no_longer_under_its_root_is_source_changed(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        moved = os.path.join(self.tmpdir, "moved-root")
        real = os.path.realpath

        def other_root(value, *args, **kwargs):
            if os.path.normcase(os.fspath(value)) == os.path.normcase(
                str(self.output_dir)
            ):
                return moved
            return real(value, *args, **kwargs)

        with patch.object(review_api, "_realpath", side_effect=other_root):
            await self._refused(
                item, self._body(detail, observed, cat["hash"]), 409, "source-changed"
            )

    async def test_verify_failures(self):
        self._require_routes()
        _path, item, detail, observed, cat = await self._opened()
        store = self.api._captures
        body = self._body(detail, observed, cat["hash"])
        with patch.object(store, "verify", side_effect=CaptureChanged("gone")):
            await self._refused(item, body, 409, "file-changed")
        other = FileFacts(item_locator := self._items()[0]["locator"], "1" * 64, 1, 1)
        with patch.object(store, "verify", return_value=other):
            await self._refused(item, body, 409, "file-changed")
        foreign = FileFacts(item_locator + "x", detail["sha256"], 1, 1)
        with patch.object(store, "verify", return_value=foreign):
            data = await self._refused(item, body, 409, "capture-expired")
        self.assertIn("another file", data["error"])


class TestInteractiveCapacity(ReviewRoutesTestCase):
    async def test_background_recording_leaves_a_worker_for_the_tag_dialog(self):
        """A scan or monitor recording in flight never starves an observe (I4)."""
        self._require_routes()
        self._record(self._ambiguous("two.png"))
        busy = self._ambiguous("busy.png", color=(4, 5, 6))
        item = (await self._list())["items"][0]
        store = self.api._captures
        entered, proceed = threading.Event(), threading.Event()
        original = CaptureStore._read
        outcome = {}

        def blocking(store_, stream, size):
            if threading.current_thread() is recorder and not entered.is_set():
                entered.set()
                proceed.wait(30)
            return original(store_, stream, size)

        def record():
            outcome["value"] = self.api.record_review_file(str(busy))

        recorder = threading.Thread(target=record)
        with patch.object(CaptureStore, "_read", blocking):
            recorder.start()
            try:
                self.assertTrue(entered.wait(30))
                self.assertEqual(store.stats()["workers"], 1)
                # Another background recording finds no worker it may use...
                with patch("utils.media_capture.BACKGROUND_WAIT_SECONDS", 0.01):
                    with self.assertRaises(CaptureCapacity):
                        self.api.record_review_file(str(busy))
                # ...while the interactive observe still gets the last one.
                observed = await self._observe(item["target"])
                self.assertEqual(
                    observed["capture"]["sha256"], digest(self.output_dir / "two.png")
                )
            finally:
                proceed.set()
                recorder.join(30)
        self.assertFalse(recorder.is_alive())
        self.assertEqual(outcome["value"], "recorded")
