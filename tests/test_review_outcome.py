"""Needs review takes only files whose final outcome is ambiguous and unlinked.

The maintainer's live scan (#284 / #288) queued every file the strict
resolver found ambiguous, also those the legacy browser-parser fallback then
linked and those already linked, so the queue (10,232) was far larger than
the scan's Needs review group. The rule now: a file is queued only when the
resolver finds it ambiguous, the fallback does not link it and no saved row
links it. A pending, undecided item whose file is linked (by the fallback, a
later resolution or the monitor) is removed on the next scan or monitor
pass; an item that carries a decision is never touched.
"""

import json
import logging
import os
import sqlite3
import unittest
from unittest.mock import patch

try:
    from tests.test_review import (
        DELIMITERS,
        NOW,
        ReviewTestCase,
        ambiguous_graph,
        resolved_graph,
        unlinkable_graph,
        write_png,
    )
except ImportError:  # discovered with tests/ as the top-level directory
    from test_review import (
        DELIMITERS,
        NOW,
        ReviewTestCase,
        ambiguous_graph,
        resolved_graph,
        unlinkable_graph,
        write_png,
    )

from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler
from utils.prompt_tracker import PromptTracker
from utils import review_capture


def nan_stamped(graph):
    """ComfyUI's ``is_changed: [NaN]`` stamp (written as a bare NaN literal)."""
    for node in graph.values():
        node["is_changed"] = [float("nan")]
    return graph


class OutcomeTestCase(ReviewTestCase):
    def _pending(self):
        return self.api.db.library_counts()["review_pending"]

    def _record(self, name):
        """An item as the pre-fix producers recorded it (any ambiguous file)."""
        outcome = review_capture.record_ambiguous_file(
            self.api.db, self.api._captures, self.output_dir, 0, name
        )
        self.assertIn(outcome, ("recorded", "limited"))

    def _decide(self, item, outcome):
        conn = self.api.db.model.get_connection()
        candidate = None
        if outcome == "selected":
            candidate = json.loads(item["evidence"])["candidates"][0]["hash"]
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state=?, revision=1 WHERE id=?",
                (outcome, item["id"]),
            )
            conn.execute(
                "INSERT INTO media_review_decisions(id,item_id,decision_revision,"
                "created_at,outcome,candidate_hash,evidence) VALUES(?,?,?,?,?,?,?)",
                (
                    f"00000000-0000-4000-8000-{len(self._decisions()) + 1:012d}",
                    item["id"],
                    1,
                    NOW,
                    outcome,
                    candidate,
                    '{"kind":"user-selection"}',
                ),
            )

    def _decisions(self):
        conn = self.api.db.model.get_connection()
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT id,item_id,outcome FROM media_review_decisions ORDER BY id"
            )
        ]

    def _link(self, path, text="already saved"):
        prompt_id = self.api.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text)
        )
        self.api.db.link_image_to_prompt(prompt_id, str(path))
        return prompt_id

    @staticmethod
    def _rows(job, group):
        return sorted(row["path"] for row in job["outcomes"].get(group, []))


class TestOnlyUnlinkedAmbiguousFilesAreQueued(OutcomeTestCase):
    async def test_nan_stamped_graph_the_fallback_links_is_not_queued(self):
        self._roots()
        path = self.output_dir / "nan.png"
        write_png(path, prompt=nan_stamped(ambiguous_graph("cat", "dog")))
        self.assertIn(b"NaN", path.read_bytes())

        job = await self._scan()

        self.assertEqual(job["counters"]["added"], 1, job["counters"])
        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_items"], 0)
        self.assertEqual(self._pending(), 0)

    async def test_fallback_link_to_a_known_prompt_is_not_queued(self):
        self._roots()
        write_png(self.output_dir / "a.png", prompt=ambiguous_graph("cat", "dog"))
        await self._scan()
        write_png(self.output_dir / "b.png", prompt=ambiguous_graph("cat", "dog"))

        job = await self._scan()

        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_items"], 0)

    async def test_already_linked_file_is_not_queued(self):
        self._roots()
        path = self.output_dir / "linked.png"
        write_png(path, prompt=unlinkable_graph("cat", "dog"))
        self._link(path)

        job = await self._scan()

        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_items"], 0)
        self.assertEqual(self._rows(job, "review"), [])
        self.assertEqual(self._rows(job, "known"), ["linked.png"])

    async def test_genuinely_ambiguous_file_is_queued(self):
        self._roots()
        path = self.output_dir / "two.png"
        write_png(path, prompt=nan_stamped(unlinkable_graph("cat", "dog")))
        # Beside it, a file the fallback links: only the first is queued.
        write_png(self.output_dir / "linked.png", prompt=ambiguous_graph("a", "b"))

        job = await self._scan()

        (item,) = self._items()
        self.assertEqual(os.path.basename(item["locator"]), "two.png")
        self.assertEqual(item["state"], "pending")
        self.assertEqual(item["candidate_count"], 3)
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertEqual(self._rows(job, "review"), ["two.png"])
        self.assertEqual(self._pending(), 1)
        self.assertIn("? 1 image added to Needs review", "\n".join(job["log"]))

    async def test_counter_and_queue_agree_with_the_review_group(self):
        """``review_items`` and the queue total equal the review group less
        its unresolved files, on the first scan and on a rescan."""
        self._roots()
        for name in ("u1.png", "u2.png"):
            write_png(self.output_dir / name, prompt=unlinkable_graph("cat", name))
        write_png(self.output_dir / "delims.png", parameters=DELIMITERS)
        write_png(self.output_dir / "linked.png", prompt=ambiguous_graph("a", "b"))
        unresolved = {"1": {"class_type": "KSampler", "inputs": {}}}
        write_png(self.output_dir / "none.png", prompt=unresolved)
        write_png(self.output_dir / "one.png", prompt=resolved_graph())

        for _ in range(2):
            job = await self._scan()
            review = self._rows(job, "review")
            unresolved_rows = sorted(
                row["path"]
                for row in job["outcomes"]["review"]
                if "could not be traced" in row["detail"]
            )
            queued = [name for name in review if name not in unresolved_rows]
            # delims.png has nothing to choose: "could not be traced".
            self.assertEqual(queued, ["u1.png", "u2.png"], job)
            self.assertIn("delims.png", unresolved_rows)
            self.assertEqual(job["scope"]["review_items"], 2)
            self.assertEqual(self._pending(), 2)
        self.assertIn("? 2 images already in Needs review", "\n".join(job["log"]))


class TestQueueSelfHeals(OutcomeTestCase):
    async def test_pending_item_of_a_fallback_linked_file_is_removed(self):
        """The maintainer's case: queued by the old rule, linked on rescan."""
        self._roots()
        path = self.output_dir / "nan.png"
        write_png(path, prompt=nan_stamped(ambiguous_graph("cat", "dog")))
        self._record("nan.png")
        revision = self._review_revision()
        self.assertEqual(self._pending(), 1)

        job = await self._scan()

        self.assertEqual(self._items(), [])
        self.assertEqual(self._pending(), 0)
        self.assertGreater(self._review_revision(), revision)
        self.assertEqual(job["scope"]["review_settled"], 1)
        self.assertIn("= 1 image left Needs review · now linked", "\n".join(job["log"]))

    async def test_pending_item_of_an_already_linked_file_is_removed(self):
        self._roots()
        path = self.output_dir / "linked.png"
        write_png(path, prompt=unlinkable_graph("cat", "dog"))
        self._record("linked.png")
        self._link(path)

        await self._scan()

        self.assertEqual(self._items(), [])
        self.assertEqual(self._pending(), 0)

    async def test_pending_item_of_a_skipped_known_file_is_removed(self):
        """A file the "new" scope skips as known still settles its old item."""
        self._roots()
        path = self.output_dir / "one.png"
        write_png(path, prompt=unlinkable_graph("cat", "dog"))
        self._record("one.png")
        write_png(path, prompt=resolved_graph())
        first = await self._scan()
        self.assertEqual(self._rows(first, "added"), ["one.png"])
        # An item a pre-fix scan left for the file's old bytes.
        self.assertEqual(self._items(), [])
        write_png(path, prompt=unlinkable_graph("cat", "dog"), color=(0, 9, 0))
        self._record("one.png")
        write_png(path, prompt=resolved_graph())
        second = await self._scan()
        self.assertEqual(self._rows(second, "known"), ["one.png"])
        self.assertEqual(self._items(), [])

    async def test_a_failed_settle_is_logged_and_counted_not_fatal(self):
        self._roots()
        write_png(self.output_dir / "nan.png", prompt=ambiguous_graph("cat", "dog"))
        self._record("nan.png")

        with (
            patch.object(
                self.api.db,
                "settle_linked_review_items",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            self.assertLogs("prompt_manager.api", logging.WARNING) as logs,
        ):
            job = await self._scan()

        self.assertEqual(job["state"], "finished")
        self.assertEqual(job["scope"]["review_errors"], 1)
        self.assertEqual(job["scope"]["review_settled"], 0)
        self.assertTrue(any("could not be removed" in line for line in logs.output))
        self.assertEqual(self._pending(), 1)
        # The next scan settles it.
        await self._scan()
        self.assertEqual(self._pending(), 0)

    async def test_still_unlinked_pending_item_stays(self):
        self._roots()
        write_png(self.output_dir / "two.png", prompt=unlinkable_graph("cat", "dog"))
        await self._scan()
        (item,) = self._items()

        job = await self._scan()

        self.assertEqual([i["id"] for i in self._items()], [item["id"]])
        self.assertEqual(job["scope"].get("review_settled", 0), 0)

    async def test_decided_items_are_never_touched(self):
        self._roots()
        for name in ("sel.png", "unres.png"):
            write_png(self.output_dir / name, prompt=ambiguous_graph("cat", name))
            self._record(name)
        items = {os.path.basename(i["locator"]): i for i in self._items()}
        self._decide(items["sel.png"], "selected")
        self._decide(items["unres.png"], "unresolved")
        before = (self._items(), self._decisions())
        # A pending neighbour the fallback links: it alone leaves the queue.
        write_png(self.output_dir / "pending.png", prompt=ambiguous_graph("x", "y"))
        self._record("pending.png")
        self.assertEqual(self._pending(), 1)

        job = await self._scan("all")

        self.assertEqual((self._items(), self._decisions()), before)
        self.assertEqual(self._pending(), 0)
        self.assertEqual(job["scope"]["review_settled"], 1)


class TestMonitorRule(OutcomeTestCase):
    def _handler(self):
        handler = ImageGenerationHandler(self.api.db, PromptTracker(self.api.db))
        handler.review_recorder = self.api.record_review_file
        return handler

    def _context(self):
        prompt_id = self.api.db.save_prompt(
            text="latest", prompt_hash=generate_prompt_hash("latest")
        )
        return {"id": prompt_id, "text": "latest", "fallback": True}

    async def test_monitor_linked_ambiguous_output_is_not_queued(self):
        self._roots()
        path = self.output_dir / "two.png"
        graph = unlinkable_graph("cat", "dog")
        write_png(path, prompt=graph)

        self._handler().link_image_to_prompt(
            str(path), self._context(), {"prompt": graph}
        )

        self.assertEqual(self._items(), [])

    async def test_monitor_link_removes_the_files_pending_item(self):
        self._roots()
        path = self.output_dir / "two.png"
        graph = unlinkable_graph("cat", "dog")
        write_png(path, prompt=graph)
        self._record("two.png")
        self.assertEqual(self._pending(), 1)

        self._handler().link_image_to_prompt(
            str(path), self._context(), {"prompt": graph}
        )

        self.assertEqual(self._items(), [])
        self.assertEqual(self._pending(), 0)

    async def test_monitor_never_touches_a_decided_item(self):
        self._roots()
        path = self.output_dir / "two.png"
        graph = unlinkable_graph("cat", "dog")
        write_png(path, prompt=graph)
        self._record("two.png")
        (item,) = self._items()
        self._decide(item, "unresolved")
        before = (self._items(), self._decisions())
        other = self.output_dir / "other.png"
        write_png(other, prompt=graph, color=(0, 0, 9))
        self._record("other.png")
        handler, context = self._handler(), self._context()

        handler.link_image_to_prompt(str(path), context, {"prompt": graph})
        handler.link_image_to_prompt(str(other), context, {"prompt": graph})

        self.assertEqual((self._items(), self._decisions()), before)
        self.assertEqual(self._pending(), 0)

    async def test_record_review_file_only_clears_what_it_links(self):
        """Only scans add items: the monitor's pass skips every unlinked file
        and settles the items of a linked one."""
        self._roots()
        write_png(self.output_dir / "fallback.png", prompt=ambiguous_graph("a", "b"))
        write_png(self.output_dir / "two.png", prompt=unlinkable_graph("cat", "dog"))
        linked = self.output_dir / "linked.png"
        write_png(linked, prompt=unlinkable_graph("cat", "dog"))
        self._link(linked)

        record = self.api.record_review_file
        self.assertEqual(record(str(self.output_dir / "fallback.png")), "skipped")
        self.assertEqual(record(str(linked)), "linked")
        self.assertEqual(record(str(self.output_dir / "two.png")), "skipped")
        self.assertEqual(self._items(), [])


if __name__ == "__main__":
    unittest.main()
