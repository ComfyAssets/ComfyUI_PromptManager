"""
API endpoint integration tests for PromptManager.

Uses aiohttp test client to test request/response contracts
without requiring a full ComfyUI server.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from utils.hashing import generate_prompt_hash


class APITestCase(AioHTTPTestCase):
    """Base class that stands up a test aiohttp app with PromptManager routes."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()

        app = web.Application()
        routes = web.RouteTableDef()

        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)

    # ── helpers ────────────────────────────────────────────────────────

    def _save_prompt(self, text="Test prompt", category=None, tags=None, rating=None):
        return self.api.db.save_prompt(
            text=text,
            category=category,
            tags=tags or [],
            rating=rating,
            prompt_hash=generate_prompt_hash(text),
        )


class TestHealthEndpoint(APITestCase):

    async def test_test_route(self):
        resp = await self.client.request("GET", "/prompt_manager/test")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertIn("timestamp", data)


class TestRecentPrompts(APITestCase):

    async def test_recent_empty(self):
        resp = await self.client.request("GET", "/prompt_manager/recent")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["pagination"]["total"], 0)
        self.assertEqual(len(data["results"]), 0)

    async def test_recent_with_data(self):
        self._save_prompt("First prompt")
        self._save_prompt("Second prompt")
        resp = await self.client.request("GET", "/prompt_manager/recent")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["total"], 2)
        self.assertEqual(len(data["results"]), 2)

    async def test_recent_pagination(self):
        for i in range(15):
            self._save_prompt(f"Prompt {i:02d}")
        resp = await self.client.request(
            "GET", "/prompt_manager/recent?limit=5&offset=0"
        )
        data = await resp.json()
        self.assertEqual(len(data["results"]), 5)
        self.assertEqual(data["pagination"]["total"], 15)
        self.assertTrue(data["pagination"]["has_more"])

    async def test_recent_last_page(self):
        for i in range(7):
            self._save_prompt(f"Prompt {i}")
        resp = await self.client.request(
            "GET", "/prompt_manager/recent?limit=5&offset=5"
        )
        data = await resp.json()
        self.assertEqual(len(data["results"]), 2)
        self.assertFalse(data["pagination"]["has_more"])


class TestSearch(APITestCase):

    async def test_search_by_text(self):
        self._save_prompt("Beautiful mountain landscape")
        self._save_prompt("City skyline at night")
        resp = await self.client.request("GET", "/prompt_manager/search?text=mountain")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("mountain", data["results"][0]["text"])

    async def test_search_by_category(self):
        self._save_prompt("Nature scene", category="nature")
        self._save_prompt("Urban view", category="urban")
        resp = await self.client.request(
            "GET", "/prompt_manager/search?category=nature"
        )
        data = await resp.json()
        self.assertEqual(len(data["results"]), 1)

    async def test_search_by_tag(self):
        self._save_prompt("Tagged prompt", tags=["landscape", "sunset"])
        self._save_prompt("Other prompt", tags=["portrait"])
        resp = await self.client.request("GET", "/prompt_manager/search?tags=landscape")
        data = await resp.json()
        self.assertEqual(len(data["results"]), 1)

    async def test_search_empty_result(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/search?text=nonexistent_xyz"
        )
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 0)


class TestSaveAndDelete(APITestCase):

    async def test_save_prompt(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/save",
            json={
                "text": "New prompt via API",
                "category": "test",
                "tags": ["api", "test"],
            },
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertIn("prompt_id", data)

    async def test_save_empty_text(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/save", json={"text": ""}
        )
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_delete_prompt(self):
        pid = self._save_prompt("To delete")
        resp = await self.client.request("DELETE", f"/prompt_manager/delete/{pid}")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_delete_nonexistent(self):
        resp = await self.client.request("DELETE", "/prompt_manager/delete/99999")
        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertFalse(data["success"])


class TestUpdatePrompt(APITestCase):

    async def test_update_text(self):
        pid = self._save_prompt("Original text")
        resp = await self.client.request(
            "PUT",
            f"/prompt_manager/prompts/{pid}",
            json={"text": "Updated text"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_update_rating(self):
        pid = self._save_prompt("Ratable")
        resp = await self.client.request(
            "PUT",
            f"/prompt_manager/prompts/{pid}/rating",
            json={"rating": 4},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_add_tag(self):
        pid = self._save_prompt("Taggable", tags=["old"])
        resp = await self.client.request(
            "POST",
            f"/prompt_manager/prompts/{pid}/tags",
            json={"tag": "new_tag"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])


class TestTags(APITestCase):

    async def test_get_all_tags(self):
        self._save_prompt("P1", tags=["alpha", "beta"])
        self._save_prompt("P2", tags=["beta", "gamma"])
        resp = await self.client.request("GET", "/prompt_manager/tags")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertIn("tags", data)

    async def test_get_tag_stats(self):
        self._save_prompt("P1", tags=["common", "rare"])
        self._save_prompt("P2", tags=["common"])
        resp = await self.client.request("GET", "/prompt_manager/tags/stats")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_rename_tag(self):
        self._save_prompt("P1", tags=["old_name"])
        resp = await self.client.request(
            "PUT",
            "/prompt_manager/tags/old_name",
            json={"new_name": "new_name"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_delete_tag(self):
        self._save_prompt("P1", tags=["removable"])
        resp = await self.client.request("DELETE", "/prompt_manager/tags/removable")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])

    async def test_merge_tags(self):
        self._save_prompt("P1", tags=["target"])
        self._save_prompt("P2", tags=["source"])
        resp = await self.client.request(
            "POST",
            "/prompt_manager/tags/merge",
            json={"source_tags": ["source"], "target_tag": "target"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])


class TestCategories(APITestCase):

    async def test_get_categories(self):
        self._save_prompt("P1", category="nature")
        self._save_prompt("P2", category="urban")
        resp = await self.client.request("GET", "/prompt_manager/categories")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertIn("categories", data)


class TestStats(APITestCase):

    async def test_get_stats(self):
        self._save_prompt("P1", category="test", tags=["t1"], rating=4)
        resp = await self.client.request("GET", "/prompt_manager/stats")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertIn("stats", data)


class TestExport(APITestCase):

    async def test_export_json(self):
        self._save_prompt("Exportable", category="test", tags=["export"])
        resp = await self.client.request("GET", "/prompt_manager/export?format=json")
        self.assertEqual(resp.status, 200)
        # Export returns raw JSON file download, not a success envelope
        self.assertIn("Content-Disposition", resp.headers)
        text = await resp.text()
        data = json.loads(text)
        self.assertIn("prompts", data)
        self.assertEqual(len(data["prompts"]), 1)


class TestResponseEnvelope(APITestCase):
    """Verify all responses follow the {success: bool, ...} envelope."""

    async def test_success_responses_have_success_true(self):
        self._save_prompt("Envelope test")
        endpoints = [
            ("GET", "/prompt_manager/test"),
            ("GET", "/prompt_manager/recent"),
            ("GET", "/prompt_manager/search"),
            ("GET", "/prompt_manager/categories"),
            ("GET", "/prompt_manager/tags"),
            ("GET", "/prompt_manager/stats"),
        ]
        for method, path in endpoints:
            resp = await self.client.request(method, path)
            data = await resp.json()
            self.assertIn("success", data, f"Missing 'success' key in {method} {path}")
            self.assertTrue(
                data["success"], f"Expected success=True for {method} {path}"
            )

    async def test_error_responses_have_success_false(self):
        resp = await self.client.request("DELETE", "/prompt_manager/delete/99999")
        data = await resp.json()
        self.assertIn("success", data)
        self.assertFalse(data["success"])


if __name__ == "__main__":
    unittest.main()
