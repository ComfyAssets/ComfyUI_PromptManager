"""Inspect's ``review`` field (#659, design Part 5, contract C4).

The Inspect response names the file's Needs review item when a role is
ambiguous and the file is unlinked: ``{"state": "pending", "item_id": ...}``
for an item that waits, ``{"state": "decided"}`` once the user decided it,
``null`` otherwise. It is looked up by the file's locator and the stat the
inspection took, never by hashing the file.
"""

import hashlib
import json
import os
import sqlite3
from unittest.mock import patch

try:
    from test_api_images import ImageAPITestCase
    from test_api_prompt_inspector import graph, png
except ImportError:
    from tests.test_api_images import ImageAPITestCase
    from tests.test_api_prompt_inspector import graph, png

NOW = "2026-11-19T00:00:00+00:00"


def two_samplers(first="golden dunes", second="harbor lights", negative="blur"):
    """Two samplers, each with its own positive encoder: the inspector is ambiguous."""
    nodes = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
    }
    for index, text in enumerate((first, second)):
        encoder, sampler = str(10 + 2 * index), str(11 + 2 * index)
        nodes[encoder] = {"class_type": "CLIPTextEncode", "inputs": {"text": text}}
        nodes[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": ["2", 0],
            },
        }
    return nodes


class ReviewItemsMixin:
    """Queue items for a file under the output root, as the recorder stores them."""

    def locator(self, path):
        relative = path.relative_to(self.output_dir).parts
        spelled = os.path.join(os.path.realpath(self.output_dir), *relative)
        return os.path.normcase(spelled), spelled

    def queue(self, path, *, sha256=None, resolver_version=1, evidence=None):
        """Store one pending item for the bytes at ``path``'s current stat."""
        facts = path.stat()
        locator, spelled = self.locator(path)
        stored = self.api.db.record_review_item(
            locator=locator,
            spelled_path=spelled,
            sha256=sha256 or hashlib.sha256(path.read_bytes()).hexdigest(),
            size=facts.st_size,
            mtime_ns=facts.st_mtime_ns,
            epoch=self.api.db.get_execution_epoch(),
            resolver_version=resolver_version,
            evidence=evidence or {"candidates": []},
            candidate_count=0,
            limited=True,
        )
        self.assertTrue(stored)
        return self.api.db.review_items_for(locator)[-1]["id"]

    def decide(self, item_id):
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='unresolved', revision=1 "
                "WHERE id=?",
                (item_id,),
            )
            conn.execute(
                "INSERT INTO media_review_decisions(id,item_id,decision_revision,"
                "created_at,outcome,candidate_hash,evidence) VALUES(?,?,?,?,?,?,?)",
                (
                    "00000000-0000-4000-8000-0000000000d1",
                    item_id,
                    1,
                    NOW,
                    "unresolved",
                    None,
                    '{"kind":"user-selection"}',
                ),
            )


class TestReviewItemAt(ReviewItemsMixin, ImageAPITestCase):
    """``database/review.py::review_item_at``."""

    def stat(self, path):
        facts = path.stat()
        return self.locator(path)[0], facts.st_size, facts.st_mtime_ns

    async def test_no_item_is_none(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.assertIsNone(self.api.db.review_item_at(*self.stat(path)))

    async def test_pending_item_at_the_stat(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        item_id = self.queue(path)
        self.assertEqual(
            self.api.db.review_item_at(*self.stat(path)),
            {"state": "pending", "item_id": item_id},
        )

    async def test_any_version_is_pending(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        item_id = self.queue(path, resolver_version=7)
        found = self.api.db.review_item_at(*self.stat(path))
        self.assertEqual(found, {"state": "pending", "item_id": item_id})

    async def test_another_stat_is_none(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.queue(path)
        locator, size, mtime_ns = self.stat(path)
        self.assertIsNone(self.api.db.review_item_at(locator, size + 1, mtime_ns))
        self.assertIsNone(self.api.db.review_item_at(locator, size, mtime_ns + 1))
        other = self.locator(self.output_dir / "b.png")[0]
        self.assertIsNone(self.api.db.review_item_at(other, size, mtime_ns))

    async def test_two_pending_items_at_the_stat_give_the_newest(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        # Two byte records at one stat (rewritten bytes, mtime restored).
        self.queue(path, sha256="a" * 64)
        newest = self.queue(path, sha256="b" * 64)
        self.assertEqual(
            self.api.db.review_item_at(*self.stat(path)),
            {"state": "pending", "item_id": newest},
        )

    async def test_decided_item_is_decided(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.decide(self.queue(path))
        self.assertEqual(
            self.api.db.review_item_at(*self.stat(path)), {"state": "decided"}
        )

    async def test_pending_wins_over_a_decided_item_of_other_bytes(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.decide(self.queue(path, sha256="a" * 64))
        pending = self.queue(path, sha256="b" * 64)
        self.assertEqual(
            self.api.db.review_item_at(*self.stat(path)),
            {"state": "pending", "item_id": pending},
        )

    async def test_invalid_locator_is_refused(self):
        with self.assertRaises(ValueError):
            self.api.db.review_item_at("", 1, 1)


class TestInspectReviewField(ReviewItemsMixin, ImageAPITestCase):
    """``GET /prompt_manager/images/inspect/...`` carries ``review``."""

    async def inspect(self, relative="a.png"):
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{relative}", params={"root": "0"}
        )
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        return body

    async def test_unqueued_ambiguous_file_is_null(self):
        png(self.output_dir / "a.png", prompt=two_samplers())
        body = await self.inspect()
        self.assertEqual(body["prompts"]["positive"]["status"], "ambiguous")
        self.assertIn("review", body)
        self.assertIsNone(body["review"])

    async def test_pending_item_names_its_id(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        item_id = self.queue(path)
        body = await self.inspect()
        self.assertEqual(body["review"], {"state": "pending", "item_id": item_id})

    async def test_pending_item_in_a_subfolder(self):
        path = png(self.output_dir / "batch" / "a.png", prompt=two_samplers())
        item_id = self.queue(path)
        body = await self.inspect("batch/a.png")
        self.assertEqual(body["review"], {"state": "pending", "item_id": item_id})

    async def test_decided_item_is_decided(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.decide(self.queue(path))
        body = await self.inspect()
        self.assertEqual(body["review"], {"state": "decided"})

    async def test_changed_bytes_are_null(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.queue(path)
        facts = path.stat()
        os.utime(path, ns=(facts.st_atime_ns, facts.st_mtime_ns + 10**9))
        body = await self.inspect()
        self.assertIsNone(body["review"])

    async def test_unambiguous_file_is_null_even_when_queued(self):
        path = png(self.output_dir / "a.png", prompt=graph("portrait", "blurry"))
        self.queue(path)
        body = await self.inspect()
        self.assertEqual(body["prompts"]["positive"]["status"], "resolved")
        self.assertIsNone(body["review"])

    async def test_linked_file_is_null_and_never_reads_the_queue(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.queue(path)
        self._link_image(str(path), "golden dunes")
        with patch.object(
            type(self.api.db), "review_item_at", side_effect=AssertionError
        ):
            body = await self.inspect()
        self.assertEqual(body["library"]["status"], "linked")
        self.assertIsNone(body["review"])

    async def test_read_failure_is_null_and_logged(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.queue(path)
        with (
            self.assertLogs(self.api.logger, "WARNING") as logs,
            patch.object(
                type(self.api.db),
                "review_item_at",
                side_effect=sqlite3.OperationalError("locked"),
            ),
        ):
            body = await self.inspect()
        self.assertIsNone(body["review"])
        self.assertEqual(body["prompts"]["positive"]["status"], "ambiguous")
        self.assertNotIn(str(self.output_dir), "\n".join(logs.output))

    async def test_lora_root_has_no_review_reader(self):
        """A root past the output folders (a LoRA root) is never a scan root."""
        outputs = [self.output_dir]
        reader = self.api._inspection_review(0, outputs, self.output_dir, ("a.png",))
        self.assertTrue(callable(reader))
        lora = self.comfy_dir / "loras"
        self.assertIsNone(self.api._inspection_review(1, outputs, lora, ("a.png",)))

    async def test_response_is_valid_json_with_the_field(self):
        path = png(self.output_dir / "a.png", prompt=two_samplers())
        self.queue(path)
        response = await self.client.get(
            "/prompt_manager/images/inspect/a.png", params={"root": "0"}
        )
        text = await response.text()
        self.assertIn('"review":', text)
        self.assertEqual(json.loads(text)["review"]["state"], "pending")
