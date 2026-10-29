"""#443 (D152): the Library delete, archive and restore routes, and ingestion after."""

import json
import os
from unittest.mock import patch

from tests.test_api_admin import AdminAPITestCase  # first: it stubs ComfyUI
from database.execution_history_schema import rotate_history_epoch  # noqa: E402
from database.prompt_archive import TABLE as ARCHIVE  # noqa: E402
from database.safety_backup import SafetyBackupFailed  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

PREVIEW = "/prompt_manager/library/delete/preview"
DELETE = "/prompt_manager/library/delete"
RESTORE = "/prompt_manager/library/restore"


def graph(positive, negative="blur"):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


class LibraryDeleteRouteCase(AdminAPITestCase):
    def epoch(self):
        return self.api.db.get_library_options()["library_epoch"]

    def save(self, text, **metadata):
        return self.api.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def paired(self, positive, negative="blur"):
        pair = self.api.db.save_prompt_pair(positive, negative)
        return self.api.db.get_prompt_pair(pair)["positive_prompt_id"]

    def rows(self, sql, params=()):
        with self.api.db.model.get_connection() as conn:
            return [tuple(row) for row in conn.execute(sql, params).fetchall()]

    def ids(self):
        return sorted(row[0] for row in self.rows("SELECT id FROM prompts"))

    def archived(self):
        return sorted(row[0] for row in self.rows(f"SELECT prompt_id FROM {ARCHIVE}"))

    def backups(self):
        folder = os.path.dirname(self.api.db.model.db_path)
        return sorted(name for name in os.listdir(folder) if ".backup_" in name)

    async def post(self, url, body):
        response = await self.client.request("POST", url, json=body)
        return response.status, await response.json()

    async def review(self, ids):
        status, body = await self.post(
            PREVIEW, {"prompt_ids": ids, "expected_epoch": self.epoch()}
        )
        self.assertEqual(status, 200, body)
        return body

    async def confirm(self, review, backup=None):
        return await self.post(
            DELETE,
            {
                "archive_ids": review["archive"],
                "delete_ids": review["delete"],
                "expected_epoch": review["library_epoch"],
                "backup": backup or review["backup"],
            },
        )

    def policy(self, on):
        previous = PromptManagerConfig.SAFETY_BACKUP
        PromptManagerConfig.SAFETY_BACKUP = on
        self.addCleanup(setattr, PromptManagerConfig, "SAFETY_BACKUP", previous)


class TestLibraryDeleteRoutes(LibraryDeleteRouteCase):
    async def test_preview_then_delete_archives_deletes_and_backs_up(self):
        self.policy(True)
        plain = self.save("Plain")
        guarded = self.save("Guarded", tags=["__protected__"])
        positive = self.paired("Cat")
        review = await self.review([plain, guarded, positive, 99999])
        self.assertEqual(
            {
                key: review[key]
                for key in ("delete", "archive", "protected", "not_found")
            },
            {
                "delete": [plain],
                "archive": [positive],
                "protected": [guarded],
                "not_found": 1,
            },
        )
        self.assertEqual(review["backup"], "on")
        self.assertTrue(review["disk"]["sufficient"])
        status, body = await self.confirm(review)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            (body["deleted"], body["archived"], body["changed"]), (1, 1, 0)
        )
        self.assertEqual(self.backups(), [body["backup"]])
        self.assertNotIn(os.sep, body["backup"])
        self.assertNotIn(plain, self.ids())
        self.assertEqual(self.archived(), [positive])
        # The backup still holds the deleted prompt.
        import sqlite3

        path = os.path.join(os.path.dirname(self.api.db.model.db_path), body["backup"])
        with sqlite3.connect(path) as copy:
            self.assertEqual(
                copy.execute(
                    "SELECT COUNT(*) FROM prompts WHERE id=?", (plain,)
                ).fetchone()[0],
                1,
            )
        copy.close()
        # The hold ended: nothing is listed as running.
        self.assertEqual(self.api._jobs.running(), [])

    async def test_archive_only_takes_no_backup(self):
        self.policy(True)
        positive = self.paired("Cat")
        shared = self.save("Shared")
        self.api.db.save_prompt_pair("Dog", "Shared")  # archive-only (#482)
        self.api.db.link_image_to_prompt(shared, "/tmp/shared-443.png")
        # Nothing could be deleted: no disk plan. (#482: a deletable prompt has one.)
        self.assertIsNone((await self.review([shared]))["disk"])
        review = await self.review([positive])
        self.assertIsNotNone(review["disk"])
        status, body = await self.confirm(review)
        self.assertEqual((status, body["archived"], body["backup"]), (200, 1, None))
        self.assertEqual(self.backups(), [])

    async def test_policy_off_takes_none_unless_the_review_promised_one(self):
        plain = self.save("Plain")
        other = self.save("Other")
        self.policy(False)
        review = await self.review([plain])
        self.assertEqual((review["backup"], review["disk"]), ("off", None))
        status, body = await self.confirm(review)
        self.assertEqual((status, body["deleted"], body["backup"]), (200, 1, None))
        # Reviewed with the policy on, turned off before the confirm: kept.
        review = await self.review([other])
        status, body = await self.confirm(review, backup="on")
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["backup"])

    async def test_a_failed_backup_changes_nothing(self):
        self.policy(True)
        plain = self.save("Plain")
        positive = self.paired("Cat")
        review = await self.review([plain, positive])
        with patch(
            "py.api.maintenance.take_safety_backup",
            side_effect=SafetyBackupFailed("x"),
        ):
            status, body = await self.confirm(review)
        self.assertEqual((status, body["code"]), (500, "backup-failed"))
        self.assertIn("nothing was changed", body["error"])
        self.assertIn(plain, self.ids())
        self.assertEqual(self.archived(), [])

    async def test_a_short_disk_refuses_before_any_change(self):
        self.policy(True)
        plain = self.save("Plain")
        from py.api import maintenance

        with patch.object(maintenance, "_free_bytes", return_value=0):
            review = await self.review([plain])
            self.assertFalse(review["disk"]["sufficient"])
            self.assertIn("Nothing was changed", review["disk"]["message"])
            status, body = await self.confirm(review)
        self.assertEqual((status, body["code"]), (507, "insufficient-space"))
        self.assertIn(plain, self.ids())

    async def test_busy_beside_a_record_changing_job(self):
        plain = self.save("Plain")
        review = await self.review([plain])
        async with self.api._jobs.hold("scan", "Scan", admission="records"):
            status, body = await self.confirm(review)
        self.assertEqual((status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["title"], "Scan")
        self.assertIn(plain, self.ids())

    async def test_restored_storage_answers_409_on_every_route(self):
        plain = self.save("Plain")
        review = await self.review([plain])
        rotate_history_epoch(self.api.db.model.get_connection())
        status, body = await self.confirm(review)
        self.assertEqual((status, body["code"]), (409, "storage-changed"))
        for url in (PREVIEW, RESTORE):
            status, body = await self.post(
                url, {"prompt_ids": [plain], "expected_epoch": review["library_epoch"]}
            )
            self.assertEqual((status, body["code"]), (409, "storage-changed"))
        self.assertIn(plain, self.ids())

    async def test_restore_route(self):
        positive = self.paired("Cat")
        await self.confirm(await self.review([positive]))
        status, body = await self.post(
            RESTORE, {"prompt_ids": [positive, 99999], "expected_epoch": self.epoch()}
        )
        self.assertEqual(status, 200)
        self.assertEqual((body["restored"], body["not_found"]), (1, 1))
        self.assertEqual(self.archived(), [])

    async def test_bodies_are_validated(self):
        plain = self.save("Plain")
        epoch = self.epoch()
        selection = {"prompt_ids": [plain], "expected_epoch": epoch}
        for url, body in (
            (PREVIEW, {**selection, "extra": 1}),
            (PREVIEW, {"prompt_ids": [], "expected_epoch": epoch}),
            (PREVIEW, {"prompt_ids": [True], "expected_epoch": epoch}),
            (PREVIEW, {"prompt_ids": list(range(1, 1002)), "expected_epoch": epoch}),
            (PREVIEW, {"prompt_ids": [plain], "expected_epoch": "nope"}),
            (RESTORE, {"prompt_ids": [plain]}),
            (DELETE, {**selection}),
            (
                DELETE,
                {
                    "archive_ids": [],
                    "delete_ids": [],
                    "expected_epoch": epoch,
                    "backup": "on",
                },
            ),
            (
                DELETE,
                {
                    "archive_ids": [plain],
                    "delete_ids": [plain],
                    "expected_epoch": epoch,
                    "backup": "on",
                },
            ),
            (
                DELETE,
                {
                    "archive_ids": [],
                    "delete_ids": [plain],
                    "expected_epoch": epoch,
                    "backup": "maybe",
                },
            ),
            (
                DELETE,
                {
                    "archive_ids": list(range(1, 600)),
                    "delete_ids": list(range(600, 1100)),
                    "expected_epoch": epoch,
                    "backup": "off",
                },
            ),
        ):
            with self.subTest(url=url, body=body):
                status, answer = await self.post(url, body)
                self.assertEqual(status, 400, answer)
                self.assertFalse(answer["success"])
        for url in (PREVIEW, DELETE, RESTORE):
            response = await self.client.request(
                "POST", url, data="[1]", headers={"Content-Type": "application/json"}
            )
            self.assertEqual(response.status, 400)
        self.assertIn(plain, self.ids())

    async def test_the_legacy_bulk_delete_is_unchanged(self):
        plain = self.save("Plain")
        positive = self.paired("Cat")
        response = await self.client.request(
            "POST", "/prompt_manager/bulk/delete", json={"prompt_ids": [positive]}
        )
        self.assertEqual(response.status, 409)
        response = await self.client.request(
            "POST", "/prompt_manager/bulk/delete", json={"prompt_ids": [plain]}
        )
        self.assertEqual((await response.json())["deleted_count"], 1)
        self.assertEqual(self.archived(), [])

    async def test_shelf_parameter_and_cursor_scope(self):
        kept = [self.paired(f"Kept {i}") for i in range(3)]
        gone = [self.paired(f"Gone {i}") for i in range(3)]
        await self.confirm(await self.review(gone))
        response = await self.client.request(
            "GET", "/prompt_manager/library?limit=2&shelf=active"
        )
        page = await response.json()
        self.assertEqual(page["archived_total"], 3)
        self.assertTrue(set(item["id"] for item in page["items"]) <= set(kept))
        response = await self.client.request(
            "GET",
            "/prompt_manager/library?limit=2&shelf=archived"
            f"&cursor={page['next_cursor']}",
        )
        self.assertEqual(response.status, 400)
        response = await self.client.request(
            "GET", "/prompt_manager/library?shelf=archived"
        )
        archived = await response.json()
        self.assertEqual(sorted(item["id"] for item in archived["items"]), gone)
        self.assertTrue(all(item["archived"] for item in archived["items"]))
        response = await self.client.request("GET", "/prompt_manager/library?shelf=all")
        self.assertEqual(response.status, 400)


class TestPairedDeleteRoutes(LibraryDeleteRouteCase):
    """#482 (D160): Delete permanently with a prompt's own saved pairs."""

    async def confirm_paired(self, review, ids, backup=None, digests=None):
        return await self.post(
            DELETE,
            {
                "archive_ids": review["archive"],
                "delete_ids": review["delete"],
                "expected_epoch": review["library_epoch"],
                "backup": backup or review["backup"],
                "delete_paired_ids": ids,
                "paired_digests": digests
                or {str(pid): review["review_digests"][str(pid)] for pid in ids},
            },
        )

    async def test_preview_reports_deletable_and_a_paired_only_delete_backs_up(self):
        self.policy(True)
        cat = self.paired("Cat")
        shared = self.save("Shared")
        self.api.db.save_prompt_pair("Dog", "Shared")
        self.api.db.link_image_to_prompt(shared, "/tmp/shared-482.png")
        review = await self.review([cat, shared])
        self.assertEqual(review["archive"], [cat, shared])
        self.assertEqual(review["deletable"], [cat])
        self.assertEqual(list(review["review_digests"]), [str(cat)])
        self.assertEqual(
            review["paired_counts"],
            {"pairs": 1, "archived_pairs": 0, "variants": 1, "kept_negatives": 1},
        )
        self.assertEqual(review["paired_image_links"], 0)
        # A selection with nothing in the delete class still checks the disk.
        self.assertTrue(review["disk"]["sufficient"])
        status, body = await self.confirm_paired(review, [cat])
        self.assertEqual(status, 200, body)
        self.assertEqual(
            (body["deleted"], body["archived"], body["pairs_removed"]), (1, 1, 1)
        )
        self.assertEqual(self.backups(), [body["backup"]])
        self.assertNotIn(cat, self.ids())
        self.assertEqual(self.archived(), [shared])

    async def test_a_body_without_the_new_keys_is_the_443_delete(self):
        cat = self.paired("Cat")
        review = await self.review([cat])
        status, body = await self.confirm(review)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["deleted"], body["archived"]), (0, 1))
        self.assertIn(cat, self.ids())

    async def test_paired_bodies_are_validated(self):
        cat = self.paired("Cat")
        plain = self.save("Plain")
        review = await self.review([cat, plain])
        digest = review["review_digests"][str(cat)]
        base = {
            "archive_ids": review["archive"],
            "delete_ids": review["delete"],
            "expected_epoch": review["library_epoch"],
            "backup": "off",
        }
        cases = [
            {"delete_paired_ids": [cat]},  # without its digests
            {"delete_paired_ids": [plain], "paired_digests": {str(plain): digest}},
            {"delete_paired_ids": [cat], "paired_digests": {}},
            {"delete_paired_ids": [cat], "paired_digests": {str(cat): "x"}},
            {"delete_paired_ids": "all", "paired_digests": {}},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                status, body = await self.post(DELETE, {**base, **extra})
                self.assertEqual((status, body["code"]), (400, "invalid-request"))
        self.assertIn(cat, self.ids())

    async def test_a_short_disk_refuses_a_paired_only_delete(self):
        self.policy(True)
        cat = self.paired("Cat")
        from py.api import maintenance

        with patch.object(maintenance, "_free_bytes", return_value=0):
            review = await self.review([cat])
            self.assertFalse(review["disk"]["sufficient"])
            status, body = await self.confirm_paired(review, [cat])
        self.assertEqual((status, body["code"]), (507, "insufficient-space"))
        self.assertIn(cat, self.ids())


class TestArchivedShelfDeleteRoutes(LibraryDeleteRouteCase):
    """#492: Delete… on the archived shelf (``shelf: "archived"``)."""

    def archive(self, *ids):
        with self.api.db.model.get_connection() as conn:
            conn.executemany(
                f"INSERT INTO {ARCHIVE}(prompt_id, archived_at) VALUES (?, ?)",
                [(pid, "2026-10-29T00:00:00Z") for pid in ids],
            )
            conn.commit()

    async def shelf_review(self, ids):
        status, body = await self.post(
            PREVIEW,
            {"prompt_ids": ids, "expected_epoch": self.epoch(), "shelf": "archived"},
        )
        self.assertEqual(status, 200, body)
        return body

    async def shelf_confirm(self, review):
        paired = review["deletable"]
        return await self.post(
            DELETE,
            {
                "archive_ids": paired,
                "delete_ids": review["delete"],
                "expected_epoch": review["library_epoch"],
                "backup": review["backup"],
                "delete_paired_ids": paired,
                "paired_digests": {
                    str(pid): review["review_digests"][str(pid)] for pid in paired
                },
                "shelf": "archived",
            },
        )

    async def test_an_archived_paired_prompt_is_deleted_after_a_backup(self):
        self.policy(True)
        cat = self.paired("Cat")
        shown = self.save("Shown")
        self.api.db.link_image_to_prompt(shown, "/tmp/shown-492.png")
        self.archive(cat)
        review = await self.shelf_review([cat, shown])
        self.assertEqual(
            (review["deletable"], review["delete"], review["not_archived"]),
            ([cat], [], 1),
        )
        self.assertTrue(review["disk"]["sufficient"])
        status, body = await self.shelf_confirm(review)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            (body["deleted"], body["archived"], body["pairs_removed"]), (1, 0, 1)
        )
        # The held write classified the archived shelf too, so it backed up.
        self.assertEqual(self.backups(), [body["backup"]])
        self.assertNotIn(cat, self.ids())
        self.assertEqual(self.archived(), [])

    async def test_an_archived_unpaired_prompt_is_deleted_after_a_backup(self):
        self.policy(True)
        loose = self.save("Loose")
        self.api.db.link_image_to_prompt(loose, "/tmp/loose-492.png")
        self.archive(loose)
        review = await self.shelf_review([loose])
        self.assertEqual(review["delete"], [loose])
        status, body = await self.shelf_confirm(review)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["deleted"], body["changed"]), (1, 0))
        self.assertEqual(self.backups(), [body["backup"]])

    async def test_the_shelf_is_validated_and_optional(self):
        cat = self.paired("Cat")
        status, body = await self.post(
            PREVIEW,
            {"prompt_ids": [cat], "expected_epoch": self.epoch(), "shelf": "trash"},
        )
        self.assertEqual((status, body["code"]), (400, "invalid-request"))
        review = await self.review([cat])
        base = {
            "archive_ids": review["archive"],
            "delete_ids": review["delete"],
            "expected_epoch": review["library_epoch"],
            "backup": "off",
        }
        status, body = await self.post(DELETE, {**base, "shelf": 1})
        self.assertEqual((status, body["code"]), (400, "invalid-request"))
        # The active shelf named explicitly is #443's delete.
        status, body = await self.post(DELETE, {**base, "shelf": "active"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.archived(), [cat])


class TestIngestionAfterArchive(LibraryDeleteRouteCase):
    def scan(self, name, positive, negative="blur"):
        meta = {"prompt": json.dumps(graph(positive, negative))}
        path = self.output_dir / name
        path.write_bytes(b"png")
        return self.api._ingest_scanned_file(path, meta)

    async def test_a_scan_after_archive_creates_no_duplicate_and_keeps_it_archived(
        self,
    ):
        self.assertEqual(self.scan("first.png", "Sunset"), "added")
        positive = self.api.db.get_prompt_by_hash(generate_prompt_hash("Sunset"))["id"]
        await self.confirm(await self.review([positive]))
        before = self.ids()
        self.scan("second.png", "Sunset")
        self.scan("third.png", "  sunset ", "other negative")
        self.assertEqual(
            [i for i in self.ids() if i not in before],
            [
                self.api.db.get_prompt_by_hash(generate_prompt_hash("other negative"))[
                    "id"
                ]
            ],
        )
        self.assertEqual(self.archived(), [positive])
        linked = self.rows(
            "SELECT COUNT(*) FROM generated_images WHERE prompt_id=?", (positive,)
        )
        self.assertEqual(linked, [(3,)])
        response = await self.client.request("GET", "/prompt_manager/library")
        self.assertNotIn(
            positive, [item["id"] for item in (await response.json())["items"]]
        )

    async def test_a_scan_after_delete_creates_the_prompt_again(self):
        plain = self.save("Lonely sunset")
        await self.confirm(await self.review([plain]))
        self.assertEqual(self.scan("again.png", "Lonely sunset"), "added")
        again = self.api.db.get_prompt_by_hash(generate_prompt_hash("Lonely sunset"))
        self.assertIsNotNone(again)
        self.assertNotEqual(again["id"], plain)
        self.assertEqual(self.archived(), [])
