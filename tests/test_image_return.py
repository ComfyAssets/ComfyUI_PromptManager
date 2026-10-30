"""Show removed images and Return to library (#498).

``GET /prompt_manager/images/output?mode=workspace&removed=1`` lists only the
files a removal mark hides; every workspace page carries ``removed_total``.
``POST /prompt_manager/images/removed/return {items | filter}`` starts a
``scan`` job titled "Return images to the library": it deletes the marks of
the files that are still hidden, then scans exactly those files, so their
prompt links come back from their metadata. Image tags, category,
collections and favorites do not come back.
"""

import asyncio
import hashlib
import os
import sqlite3
from unittest.mock import patch

try:
    from tests.disk_waits import DISK_BOUND_SECONDS
    from tests.test_api_admin import _write_png
    from tests.test_image_delete_routes import LISTING, ImageDeleteTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from disk_waits import DISK_BOUND_SECONDS
    from test_api_admin import _write_png
    from test_image_delete_routes import LISTING, ImageDeleteTestCase

RETURN = "/prompt_manager/images/removed/return"


class _ReturnCase(ImageDeleteTestCase):
    async def page(self, status=200, **query):
        params = {"mode": "workspace", **query}
        response = await self.client.get(LISTING, params=params)
        self.assertEqual(response.status, status, await response.text())
        return await response.json()

    async def removed_paths(self):
        page = await self.page(removed="1")
        return [image["path"] for image in page["images"]]

    async def give_back(self, body, status=202):
        answer = await self.post(RETURN, body, status)
        if status != 202:
            return answer
        job = self.api._jobs.get(answer["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        job.answer = answer
        return job

    def png(self, name, text):
        path = self.output_dir / name
        _write_png(path, text)
        return path

    def marks(self):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM image_removals").fetchone()[0]
        finally:
            conn.close()


class TestRemovedView(_ReturnCase):
    async def test_the_view_lists_only_hidden_files_and_both_views_count_them(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        self.write("b.png", b"bbb", mtime=2_000_000)
        self.write("c.png", b"ccc", mtime=3_000_000)
        await self.delete(["a.png", "b.png"], "library")
        shown = await self.page()
        self.assertEqual([i["path"] for i in shown["images"]], ["c.png"])
        self.assertEqual(shown["removed_total"], 2)
        removed = await self.page(removed="1")
        self.assertEqual(
            sorted(i["path"] for i in removed["images"]), ["a.png", "b.png"]
        )
        self.assertEqual(removed["total"], 2)
        self.assertEqual(removed["removed_total"], 2)
        # A search applies inside the view.
        self.assertEqual(
            [i["path"] for i in (await self.page(removed="1", q="b."))["images"]],
            ["b.png"],
        )

    async def test_no_marks_count_zero(self):
        self.write("a.png", b"aaa")
        self.assertEqual((await self.page())["removed_total"], 0)
        self.assertEqual((await self.page(removed="1"))["images"], [])

    async def test_refused_combinations(self):
        for query in (
            {"removed": "2"},
            {"removed": "1", "favorites": "1"},
            {"removed": "1", "collection": "1"},
            {"removed": "1", "category": "x"},
            {"removed": "1", "tags": "x"},
            {"removed": "1", "sort": "rating"},
        ):
            with self.subTest(query=query):
                await self.page(status=400, **query)


class TestReturn(_ReturnCase):
    async def test_return_shows_the_file_and_rescans_its_links_but_not_its_tags(self):
        path = self.png("linked.png", "a returned prompt")
        prompt_id = self.save("a returned prompt")
        self.api.db.link_image_to_prompt(str(prompt_id), str(path))
        target = self.target("linked.png")
        info = os.stat(path)
        self.api.db.add_image_tags(
            locator=os.path.normcase(os.path.realpath(path)),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            expected_epoch=self.api.db.get_execution_epoch(),
            tags=["kept-before"],
        )
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_image_tags"), 1)
        await self.delete(["linked.png"], "library")
        self.assertEqual(await self.removed_paths(), ["linked.png"])
        before = os.stat(path)
        job = await self.give_back({"items": [target]})
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(self.marks(), 0)
        self.assertEqual(await self.listed(), ["linked.png"])
        self.assertEqual(await self.removed_paths(), [])
        # The links come back from the file's metadata; the tags do not.
        self.assertEqual(
            self.count(
                "SELECT COUNT(*) FROM generated_images WHERE prompt_id IS NOT NULL"
            ),
            1,
        )
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_image_tags"), 0)
        after = os.stat(path)
        self.assertEqual(
            (before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns)
        )
        self.assertEqual(self.fake.moved, [])
        self.assertEqual(job.kind, "scan")
        self.assertEqual(job.title, "Return images to the library")
        self.assertEqual(job.answer["returned"], 1)

    async def test_a_file_that_is_not_hidden_is_not_changed(self):
        self.write("shown.png", b"shown")
        self.write("hidden.png", b"hidden", mtime=1_000_000)
        await self.delete(["hidden.png"], "library")
        job = await self.give_back(
            {"items": [self.target("shown.png"), self.target("hidden.png")]}
        )
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(job.answer["returned"], 1)
        self.assertEqual(job.answer["not_removed"], 1)
        self.assertEqual(self.marks(), 0)

    async def test_nothing_hidden_is_refused(self):
        self.write("shown.png", b"shown")
        body = await self.give_back({"items": [self.target("shown.png")]}, 400)
        self.assertEqual(body["code"], "no-images")

    async def test_return_after_a_cancelled_scan_links_the_file(self):
        path = self.png("again.png", "linked again")
        await self.delete(["again.png"], "library")
        cancelled = self.api._start_scan_job()
        self.api._jobs.cancel(cancelled.id)
        await asyncio.wait_for(asyncio.shield(cancelled.task), DISK_BOUND_SECONDS)
        self.assertTrue(cancelled.cancelled)
        job = await self.give_back({"items": [self.target("again.png")]})
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(
            self.count(
                "SELECT COUNT(*) FROM generated_images "
                "WHERE image_path LIKE '%again.png'"
            ),
            1,
        )
        del path

    async def test_other_bulk_routes_refuse_the_removed_filter(self):
        self.write("hidden.png", b"hidden", mtime=1_000_000)
        await self.delete(["hidden.png"], "library")
        await self.post(
            "/prompt_manager/images/delete/preview", {"filter": {"removed": "1"}}, 400
        )
        self.assertEqual(self.marks(), 1)

    async def test_select_all_matching_in_the_view(self):
        for n in range(3):
            self.write(f"{n}.png", f"bytes {n}".encode(), mtime=1_000_000 + n)
        await self.delete(["0.png", "1.png"], "library")
        job = await self.give_back({"filter": {"removed": "1"}})
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(sorted(await self.listed()), ["0.png", "1.png", "2.png"])

    async def test_busy_beside_a_scan_changes_nothing(self):
        self.write("hidden.png", b"hidden", mtime=1_000_000)
        await self.delete(["hidden.png"], "library")
        gate = asyncio.Event()

        async def held(job):
            await gate.wait()
            job.finish("finished")

        running = self.api._jobs.start(
            "scan", "Scan for metadata", record_changing=True, runner=held
        )
        try:
            body = await self.give_back({"items": [self.target("hidden.png")]}, 409)
            self.assertEqual(body["code"], "busy")
            self.assertEqual(self.marks(), 1)
        finally:
            gate.set()
            await asyncio.wait_for(asyncio.shield(running.task), DISK_BOUND_SECONDS)

    async def test_body_shapes(self):
        await self.post(RETURN, {}, 400)
        await self.post(RETURN, {"items": []}, 400)
        too_many = [self.target(f"{n}.png") for n in range(501)]
        await self.post(RETURN, {"items": too_many}, 400)


class TestMarksCacheRace(_ReturnCase):
    async def test_a_listing_read_during_the_clear_does_not_keep_the_file_hidden(self):
        from database import image_removal, image_removal_schema

        self.write("raced.png", b"raced", mtime=1_000_000)
        await self.delete(["raced.png"], "library")
        real_clear = image_removal_schema.clear_marks
        stale = dict(self.api.db.removal_marks())

        def clear_then_read(conn, locators):
            removed = real_clear(conn, locators)
            # Another thread's connection reads the marks before this
            # transaction commits: it still sees them, under the new generation.
            key = (
                self.api._database_identity(self.api.db),
                image_removal_schema.generation(),
            )
            self.api._marks_cache = (key, stale)
            return removed

        with patch.object(image_removal, "clear_marks", side_effect=clear_then_read):
            job = await self.give_back({"items": [self.target("raced.png")]})
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(await self.listed(), ["raced.png"])
