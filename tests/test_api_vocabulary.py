"""Vocabulary read routes page with signed, epoch-scoped cursors."""

import json
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


if __name__ == "__main__":
    unittest.main()
