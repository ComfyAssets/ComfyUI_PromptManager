"""Vocabulary read routes page with signed, epoch-scoped cursors."""

import hashlib
import json
import os
import unittest
from unittest.mock import patch

from database.execution_history_schema import rotate_history_epoch

try:
    from test_api_images import ImageAPITestCase
except ImportError:
    from tests.test_api_images import ImageAPITestCase


class TestVocabularyAPI(ImageAPITestCase):
    async def get(self, path, **params):
        response = await self.client.get(
            "/prompt_manager/vocabulary/" + path, params=params
        )
        return response, await response.json()

    def add_unused(self, *names):
        with self.api.db.model.get_connection() as conn:
            for name in names:
                conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))

    async def test_tag_and_category_envelopes_and_continuation(self):
        self._save_prompt("Cat", category="Animal", tags=["cat"])
        self.api.db.save_prompt_pair("Dog", "Noise", category="Pet", tags=["dog"])
        self.add_unused("unused")
        epoch = self.api.db.get_execution_epoch()
        response, first = await self.get("tags", limit=2)
        self.assertEqual(response.status, 200)
        self.assertEqual(
            set(first),
            {"success", "library_epoch", "tags", "total", "has_more", "next_cursor"},
        )
        self.assertEqual(first["library_epoch"], epoch)
        self.assertEqual(first["total"], 3)
        self.assertTrue(first["has_more"])
        _, second = await self.get("tags", limit=2, cursor=first["next_cursor"])
        self.assertEqual(
            [(row["name"], row["prompt_count"]) for row in first["tags"]]
            + [(row["name"], row["prompt_count"]) for row in second["tags"]],
            [("cat", 1), ("dog", 1), ("unused", 0)],
        )
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        response, categories = await self.get("categories", limit=1)
        self.assertEqual(response.status, 200)
        self.assertEqual(
            set(categories),
            {
                "success",
                "library_epoch",
                "categories",
                "total",
                "has_more",
                "next_cursor",
            },
        )
        self.assertEqual(
            categories["categories"], [{"value": "Animal", "prompt_count": 1}]
        )
        _, rest = await self.get(
            "categories", limit=1, cursor=categories["next_cursor"]
        )
        self.assertEqual(rest["categories"], [{"value": "Pet", "prompt_count": 1}])
        self.assertFalse(rest["has_more"])

    async def test_vocabulary_cursor_is_signed_epoch_scoped(self):
        self.add_unused("a", "b", "c")
        self._save_prompt("One", category="One")
        self._save_prompt("Two", category="Two")
        _, tags = await self.get("tags", limit=1)
        _, categories = await self.get("categories", limit=1)
        for path, params in (
            ("tags", {"cursor": "tampered"}),
            ("tags", {"cursor": tags["next_cursor"][:-2] + "AA"}),
            ("tags", {"cursor": categories["next_cursor"]}),
            ("categories", {"cursor": tags["next_cursor"]}),
            ("categories", {"cursor": "tampered"}),
            ("tags", {"limit": "0"}),
            ("tags", {"limit": "201"}),
            ("tags", {"limit": "x"}),
            ("categories", {"limit": "0"}),
            ("categories", {"limit": "201"}),
            ("categories", {"limit": "x"}),
        ):
            response, result = await self.get(path, **params)
            self.assertEqual(response.status, 400, (path, params))
            self.assertEqual(result["success"], False)
            self.assertIsInstance(result["error"], str)
            self.assertNotIn(str(self.comfy_dir), json.dumps(result))
        rotate_history_epoch(self.api.db.model.get_connection())
        for path, cursor in (
            ("tags", tags["next_cursor"]),
            ("categories", categories["next_cursor"]),
        ):
            response, result = await self.get(path, limit=1, cursor=cursor)
            self.assertEqual(response.status, 400, path)
            self.assertIn("refresh", result["error"])
        _, fresh = await self.get("tags", limit=1)
        self.assertNotEqual(fresh["library_epoch"], tags["library_epoch"])
        response, _ = await self.get("tags", limit=1, cursor=fresh["next_cursor"])
        self.assertEqual(response.status, 200)

    async def test_read_failures_keep_the_read_envelope(self):
        with patch.object(
            self.api.db,
            "_vocabulary_tags",
            create=True,
            side_effect=RuntimeError("secret path"),
        ):
            response, result = await self.get("tags")
        self.assertEqual(response.status, 500)
        self.assertEqual(result["success"], False)
        self.assertNotIn("secret", result["error"])

    async def test_vocabulary_tags_query_budget(self):
        for index in range(150):
            self._save_prompt(f"Prompt {index}", tags=[f"tag-{index}", "shared"])
        self.api.db.save_prompt_pair("Pair", "Noise", tags=["pair"])
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        for path in ("tags", "categories"):
            statements.clear()
            with patch.object(
                self.api.db.model, "get_connection", side_effect=trace_connection
            ):
                response, result = await self.get(path, limit=200)
            self.assertEqual(response.status, 200)
            selects = [
                query
                for query in statements
                if query.lstrip().upper().startswith(("SELECT", "WITH"))
            ]
            self.assertLessEqual(len(selects), 4, selects)
            self.assertEqual(
                [query for query in selects if "storage_epoch" in query],
                selects[:1],
            )
        _, tags = await self.get("tags", limit=200)
        self.assertEqual(len(tags["tags"]), 152)
        counts = {row["name"]: row["prompt_count"] for row in tags["tags"]}
        self.assertEqual(counts["shared"], 150)
        self.assertEqual(counts["pair"], 1)

    async def send(self, method, path, body):
        response = await self.client.request(
            method, "/prompt_manager/vocabulary/" + path, json=body
        )
        return response, await response.json()

    def tag_rows(self):
        with self.api.db.model.get_connection() as conn:
            return [
                conn.execute("SELECT * FROM tags ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM prompt_tags ORDER BY 1,2").fetchall(),
                conn.execute("SELECT * FROM prompt_pair_tags ORDER BY 1,2").fetchall(),
            ]

    async def test_tag_command_routes_return_receipts(self):
        epoch = self.api.db.get_execution_epoch()
        parent = self._save_prompt("Parent", tags=["moody"])
        pair = self.api.db.save_prompt_pair("Pair", "Noise", tags=["moody"])
        response, created = await self.send(
            "POST", "tags", {"expected_epoch": epoch, "name": " sunset "}
        )
        self.assertEqual(response.status, 200, created)
        self.assertEqual(
            created,
            {
                "success": True,
                "library_epoch": epoch,
                "created": True,
                "tag": {
                    "id": created["tag"]["id"],
                    "name": "sunset",
                    "prompt_count": 0,
                    "image_count": 0,
                },
            },
        )
        _, page = await self.get("tags")
        self.assertIn("sunset", [row["name"] for row in page["tags"]])
        moody = next(row for row in page["tags"] if row["name"] == "moody")
        response, impact = await self.get(f"tags/{moody['id']}/impact")
        self.assertEqual(response.status, 200, impact)
        self.assertEqual(
            impact,
            {
                "success": True,
                "library_epoch": epoch,
                "impact": {
                    "positive_prompts": 1,
                    "other_prompts": 0,
                    "negative_variants": 1,
                    "archived_negative_variants": 0,
                    "images": 0,
                },
            },
        )
        response, renamed = await self.send(
            "PATCH",
            f"tags/{moody['id']}",
            {"expected_epoch": epoch, "name": "moody", "new_name": "sunset"},
        )
        self.assertEqual(response.status, 200, renamed)
        self.assertEqual(
            renamed,
            {
                "success": True,
                "library_epoch": epoch,
                "merged": True,
                "tag": {
                    "id": created["tag"]["id"],
                    "name": "sunset",
                    "prompt_count": 2,
                    "image_count": 0,
                },
                "components_changed": 1,
                "pairs_changed": 1,
                "images_changed": 0,
            },
        )
        self.assertEqual(self.api.db.get_prompt_by_id(parent)["tags"], ["sunset"])
        self.assertEqual(self.api.db.get_prompt_pair(pair)["tags"], ["sunset"])
        response, deleted = await self.send(
            "POST",
            f"tags/{created['tag']['id']}/delete",
            {"expected_epoch": epoch, "name": "sunset"},
        )
        self.assertEqual(response.status, 200, deleted)
        self.assertEqual(
            deleted,
            {
                "success": True,
                "library_epoch": epoch,
                "deleted": True,
                "components_changed": 1,
                "pairs_changed": 1,
                "images_changed": 0,
            },
        )
        self.assertEqual(self.api.db.get_all_tags(), [])
        response, missing = await self.get(f"tags/{created['tag']['id']}/impact")
        self.assertEqual(response.status, 404)
        self.assertEqual(missing, {"success": False, "error": "Tag not found"})
        for bad in ("0", "x", str(2**63)):
            response, result = await self.get(f"tags/{bad}/impact")
            self.assertEqual(response.status, 400, bad)
            self.assertNotIn("code", result)

    def image(self, name, tags, digest="a", mtime_ns=1):
        """Tag the bytes ``digest`` of the file ``name``; return its locator."""
        locator = os.path.normcase(str(self.output_dir / name))
        self.api.db.replace_image_tags(
            locator=locator,
            sha256=hashlib.sha256(digest.encode()).hexdigest(),
            size=1,
            mtime_ns=mtime_ns,
            expected_epoch=self.api.db.get_execution_epoch(),
            expected_tag_revision=0,
            tags=tags,
        )
        return locator

    async def test_tag_rows_report_image_count_by_locator(self):
        self._save_prompt("Prompt only", tags=["prompt-only", "shared"])
        self.image("one.png", ["twice", "shared"], digest="old")
        self.image("one.png", ["twice"], digest="new", mtime_ns=2)
        self.image("two.png", ["shared"], digest="new")
        self.image("three.png", ["prior-only"], digest="first")
        self.image("three.png", [], digest="second", mtime_ns=2)
        response, page = await self.get("tags")
        self.assertEqual(response.status, 200, page)
        rows = {row["name"]: row for row in page["tags"]}
        self.assertEqual(
            {
                name: (row["prompt_count"], row["image_count"])
                for name, row in rows.items()
            },
            {
                "prompt-only": (1, 0),
                "shared": (1, 2),
                "twice": (0, 1),
                "prior-only": (0, 1),
            },
        )
        row = rows["shared"]
        self.assertEqual(row["image_count"], 2)
        self.assertEqual(set(row), {"id", "name", "prompt_count", "image_count"})

    async def test_impact_and_delete_counts_include_images(self):
        epoch = self.api.db.get_execution_epoch()
        self._save_prompt("Parent", tags=["moody"])
        self.image("a.png", ["moody", "keep"])
        self.image("b.png", ["moody"])
        _, page = await self.get("tags")
        moody = next(row for row in page["tags"] if row["name"] == "moody")
        self.assertEqual(moody["image_count"], 2)
        response, impact = await self.get(f"tags/{moody['id']}/impact")
        self.assertEqual(response.status, 200, impact)
        self.assertEqual(
            impact["impact"],
            {
                "positive_prompts": 1,
                "other_prompts": 0,
                "negative_variants": 0,
                "archived_negative_variants": 0,
                "images": 2,
            },
        )
        response, deleted = await self.send(
            "POST",
            f"tags/{moody['id']}/delete",
            {"expected_epoch": epoch, "name": "moody"},
        )
        self.assertEqual(response.status, 200, deleted)
        self.assertEqual(
            deleted,
            {
                "success": True,
                "library_epoch": epoch,
                "deleted": True,
                "components_changed": 1,
                "pairs_changed": 0,
                "images_changed": 2,
            },
        )
        keep = next(
            row for row in (await self.get("tags"))[1]["tags"] if row["name"] == "keep"
        )
        response, renamed = await self.send(
            "PATCH",
            f"tags/{keep['id']}",
            {"expected_epoch": epoch, "name": "keep", "new_name": "kept"},
        )
        self.assertEqual(response.status, 200, renamed)
        self.assertEqual(renamed["images_changed"], 1)
        self.assertEqual(renamed["tag"]["image_count"], 1)

    async def test_create_route_validates_names(self):
        epoch = self.api.db.get_execution_epoch()
        before = self.tag_rows()
        bodies = [
            {"expected_epoch": epoch, "name": name}
            for name in ("", "  ", "x" * 51, "bad\x01", "a,b", 7, None, ["a"])
        ] + [
            {"expected_epoch": epoch},
            {"name": "ok"},
            {"expected_epoch": epoch, "name": "ok", "extra": 1},
            {"expected_epoch": "not-a-uuid", "name": "ok"},
            ["ok"],
        ]
        for body in bodies:
            response, result = await self.send("POST", "tags", body)
            self.assertEqual(response.status, 400, body)
            self.assertEqual(result["success"], False)
            self.assertIsNone(result["code"])
            self.assertIsInstance(result["error"], str)
        self.assertEqual(self.tag_rows(), before)
        tag_id = self.api.db.create_vocabulary_tag("ok", expected_epoch=epoch)["tag"][
            "id"
        ]
        before = self.tag_rows()
        for path, method, body in (
            (f"tags/{tag_id}", "PATCH", {"expected_epoch": epoch, "name": "ok"}),
            (
                f"tags/{tag_id}",
                "PATCH",
                {"expected_epoch": epoch, "name": "ok", "new_name": "a,b"},
            ),
            (
                f"tags/{tag_id}",
                "PATCH",
                {"expected_epoch": epoch, "name": "ok", "new_name": "ok"},
            ),
            (
                "tags/x",
                "PATCH",
                {"expected_epoch": epoch, "name": "ok", "new_name": "new"},
            ),
            (f"tags/{tag_id}/delete", "POST", {"expected_epoch": epoch}),
            (
                f"tags/{tag_id}/delete",
                "POST",
                {"expected_epoch": epoch, "name": "ok", "new_name": "x"},
            ),
        ):
            response, result = await self.send(method, path, body)
            self.assertEqual(response.status, 400, (path, body))
            self.assertIsNone(result["code"])
        self.assertEqual(self.tag_rows(), before)

    async def test_command_conflicts_use_command_envelope(self):
        epoch = self.api.db.get_execution_epoch()
        self._save_prompt("Owner", tags=["moody"])
        tag_id = self.tag_rows()[0][0]["id"]
        before = self.tag_rows()
        conflict = "This tag or category changed since it was loaded; review it first"
        for path, method, body in (
            (
                f"tags/{tag_id}",
                "PATCH",
                {"expected_epoch": epoch, "name": "Moody", "new_name": "x"},
            ),
            (
                f"tags/{tag_id + 9}",
                "PATCH",
                {"expected_epoch": epoch, "name": "moody", "new_name": "x"},
            ),
            (f"tags/{tag_id}/delete", "POST", {"expected_epoch": epoch, "name": "x"}),
            (
                f"tags/{tag_id + 9}/delete",
                "POST",
                {"expected_epoch": epoch, "name": "moody"},
            ),
        ):
            response, result = await self.send(method, path, body)
            self.assertEqual(response.status, 409, (path, body))
            self.assertEqual(
                result,
                {
                    "success": False,
                    "code": "vocabulary-conflict",
                    "error": conflict,
                    "library_epoch": epoch,
                },
            )
        stale = "00000000-0000-4000-8000-000000000000"
        for path, method, body in (
            ("tags", "POST", {"expected_epoch": stale, "name": "fresh"}),
            (
                f"tags/{tag_id}",
                "PATCH",
                {"expected_epoch": stale, "name": "moody", "new_name": "x"},
            ),
            (
                f"tags/{tag_id}/delete",
                "POST",
                {"expected_epoch": stale, "name": "moody"},
            ),
        ):
            response, result = await self.send(method, path, body)
            self.assertEqual(response.status, 409, (path, body))
            self.assertEqual(result["code"], "storage-changed")
            self.assertEqual(result["library_epoch"], epoch)
        self.assertEqual(self.tag_rows(), before)
        with patch.object(
            self.api.db,
            "create_vocabulary_tag",
            create=True,
            side_effect=RuntimeError("secret path"),
        ):
            response, result = await self.send(
                "POST", "tags", {"expected_epoch": epoch, "name": "fresh"}
            )
        self.assertEqual(response.status, 500)
        self.assertIsNone(result["code"])
        self.assertNotIn("secret", result["error"])

    def category_rows(self):
        with self.api.db.model.get_connection() as conn:
            return [
                conn.execute("SELECT * FROM prompts ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM prompt_pairs ORDER BY id").fetchall(),
            ]

    async def test_category_routes_preview_then_apply(self):
        epoch = self.api.db.get_execution_epoch()
        parent = self._save_prompt("Parent", category="Scenery")
        pair = self.api.db.save_prompt_pair("Pair", "Noise", category="Scenery")
        body = {"expected_epoch": epoch, "source": "Scenery", "target": " Land "}
        response, preview = await self.send("POST", "categories/preview", body)
        self.assertEqual(response.status, 200, preview)
        impact = {
            "positive_prompts": 1,
            "other_prompts": 0,
            "negative_variants": 1,
            "archived_negative_variants": 0,
        }
        self.assertEqual(
            preview,
            {
                "success": True,
                "library_epoch": epoch,
                "source": "Scenery",
                "target": "Land",
                "target_exists": False,
                "impact": impact,
                "impact_token": preview["impact_token"],
            },
        )
        response, applied = await self.send(
            "POST",
            "categories/apply",
            {**body, "impact_token": preview["impact_token"]},
        )
        self.assertEqual(response.status, 200, applied)
        self.assertEqual(
            applied,
            {
                "success": True,
                "library_epoch": epoch,
                "source": "Scenery",
                "target": "Land",
                "consolidated": False,
                "impact": impact,
            },
        )
        self.assertEqual(self.api.db.get_prompt_by_id(parent)["category"], "Land")
        self.assertEqual(self.api.db.get_prompt_pair(pair)["category"], "Land")
        response, replay = await self.send(
            "POST",
            "categories/apply",
            {**body, "impact_token": preview["impact_token"]},
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(replay["code"], "vocabulary-conflict")

    async def test_preview_validation(self):
        epoch = self.api.db.get_execution_epoch()
        self._save_prompt("Owner", category="Scenery")
        conflict = "This tag or category changed since it was loaded; review it first"
        _, preview = await self.send(
            "POST",
            "categories/preview",
            {"expected_epoch": epoch, "source": "Scenery", "target": "Land"},
        )
        token = preview["impact_token"]
        before = self.category_rows()
        response, result = await self.send(
            "POST",
            "categories/preview",
            {"expected_epoch": epoch, "source": "Missing", "target": "Land"},
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(
            result,
            {
                "success": False,
                "code": "vocabulary-conflict",
                "error": conflict,
                "library_epoch": epoch,
            },
        )
        bad_previews = [
            {"expected_epoch": epoch, "source": "Scenery", "target": target}
            for target in ("", "   ", "x" * 101, "bad\x01", "Scenery", 7, None)
        ] + [
            {"expected_epoch": epoch, "source": 7, "target": "Land"},
            {"expected_epoch": epoch, "source": "Scenery"},
            {"expected_epoch": epoch, "source": "Scenery", "target": "L", "x": 1},
            {"expected_epoch": "nope", "source": "Scenery", "target": "Land"},
            ["Scenery"],
        ]
        for body in bad_previews:
            response, result = await self.send("POST", "categories/preview", body)
            self.assertEqual(response.status, 400, body)
            self.assertEqual(result["success"], False)
            self.assertIsNone(result["code"])
        apply_body = {"expected_epoch": epoch, "source": "Scenery", "target": "Land"}
        for body in (
            apply_body,
            {**apply_body, "impact_token": token, "extra": 1},
            {**apply_body, "impact_token": "A" * 64},
            {**apply_body, "impact_token": 7},
            {**apply_body, "target": "Scenery", "impact_token": token},
        ):
            response, result = await self.send("POST", "categories/apply", body)
            self.assertEqual(response.status, 400, body)
            self.assertIsNone(result["code"])
        stale = "00000000-0000-4000-8000-000000000000"
        for path, extra in (
            ("categories/preview", {}),
            ("categories/apply", {"impact_token": token}),
        ):
            response, result = await self.send(
                "POST", path, {**apply_body, "expected_epoch": stale, **extra}
            )
            self.assertEqual(response.status, 409, path)
            self.assertEqual(result["code"], "storage-changed")
            self.assertEqual(result["library_epoch"], epoch)
        self.assertEqual(self.category_rows(), before)

    async def test_blank_category_source_is_rejected_without_writing(self):
        epoch = self.api.db.get_execution_epoch()
        self._save_prompt("Owner", category="Scenery")
        before = self.category_rows()
        token = "0" * 64
        for source in ("", "   ", "\t\n"):
            for path, extra in (
                ("categories/preview", {}),
                ("categories/apply", {"impact_token": token}),
            ):
                body = {
                    "expected_epoch": epoch,
                    "source": source,
                    "target": "Land",
                    **extra,
                }
                response, result = await self.send("POST", path, body)
                self.assertEqual(response.status, 400, (path, source))
                self.assertFalse(result["success"])
                self.assertIsNone(result["code"])
        self.assertEqual(self.category_rows(), before)


if __name__ == "__main__":
    unittest.main()
