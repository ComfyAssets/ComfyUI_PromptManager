"""
Integration tests for Tag API endpoints.

Tests the tag REST API endpoints with real database operations,
testing the full request/response cycle including validation,
business logic, and data persistence.
"""

import pytest
import json
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from unittest.mock import patch, Mock

from src.api.routes import PromptManagerAPI
from src.database.models import PromptModel


class TestTagAPIEndpoints(AioHTTPTestCase):
    """Integration tests for tag-related API endpoints."""

    async def get_application(self):
        """Create the aiohttp application for testing."""
        # Create temporary database
        import tempfile
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.db_path = self.temp_db.name
        self.temp_db.close()

        # Initialize database model
        self.db_model = PromptModel(self.db_path)

        # Create API instance
        self.api = PromptManagerAPI(self.db_path)

        # Create app and add routes
        app = web.Application()
        self.api.add_routes(app.router)

        return app

    async def tearDown(self):
        """Clean up test resources."""
        await super().tearDown()

        import os
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @unittest_run_loop
    async def test_list_tags_empty(self):
        """Test listing tags from empty database."""
        resp = await self.client.request("GET", "/api/v1/tags")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert 'data' in data
        assert len(data['data']) == 0
        assert data['total'] == 0

    @unittest_run_loop
    async def test_create_tag(self):
        """Test creating a new tag."""
        tag_data = {'name': 'portrait'}

        resp = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )

        assert resp.status == 201
        data = await resp.json()

        assert data['success'] is True
        assert 'data' in data
        assert data['data']['name'] == 'portrait'
        assert data['data']['usage_count'] == 0
        assert 'id' in data['data']

    @unittest_run_loop
    async def test_create_tag_empty_name(self):
        """Test creating tag with empty name."""
        tag_data = {'name': ''}

        resp = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )

        assert resp.status == 400
        data = await resp.json()

        assert data['success'] is False
        assert 'error' in data

    @unittest_run_loop
    async def test_create_duplicate_tag(self):
        """Test creating a tag that already exists."""
        tag_data = {'name': 'landscape'}

        # Create first tag
        resp1 = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )
        assert resp1.status == 201

        # Try to create duplicate
        resp2 = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )
        assert resp2.status == 201

        # Should return existing tag
        data = await resp2.json()
        assert data['data']['name'] == 'landscape'

    @unittest_run_loop
    async def test_get_tag_by_id(self):
        """Test getting a specific tag by ID."""
        # Create a tag first
        tag_data = {'name': 'fantasy'}
        create_resp = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )
        created_tag = await create_resp.json()
        tag_id = created_tag['data']['id']

        # Get the tag
        resp = await self.client.request("GET", f"/api/v1/tags/{tag_id}")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert data['data']['id'] == tag_id
        assert data['data']['name'] == 'fantasy'

    @unittest_run_loop
    async def test_get_tag_nonexistent(self):
        """Test getting a tag that doesn't exist."""
        resp = await self.client.request("GET", "/api/v1/tags/99999")

        assert resp.status == 404
        data = await resp.json()

        assert data['success'] is False
        assert 'error' in data

    @unittest_run_loop
    async def test_delete_tag(self):
        """Test deleting a tag."""
        # Create a tag first
        tag_data = {'name': 'to_delete'}
        create_resp = await self.client.request(
            "POST",
            "/api/v1/tags",
            json=tag_data
        )
        created_tag = await create_resp.json()
        tag_id = created_tag['data']['id']

        # Delete the tag
        resp = await self.client.request("DELETE", f"/api/v1/tags/{tag_id}")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert 'message' in data

        # Verify tag is deleted
        get_resp = await self.client.request("GET", f"/api/v1/tags/{tag_id}")
        assert get_resp.status == 404

    @unittest_run_loop
    async def test_delete_nonexistent_tag(self):
        """Test deleting a tag that doesn't exist."""
        resp = await self.client.request("DELETE", "/api/v1/tags/99999")

        assert resp.status == 404
        data = await resp.json()

        assert data['success'] is False

    @unittest_run_loop
    async def test_list_tags_with_data(self):
        """Test listing tags with multiple tags present."""
        # Create multiple tags
        tags = ['portrait', 'landscape', 'fantasy', 'cyberpunk']

        for tag_name in tags:
            await self.client.request(
                "POST",
                "/api/v1/tags",
                json={'name': tag_name}
            )

        # List tags
        resp = await self.client.request("GET", "/api/v1/tags")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert len(data['data']) == 4
        assert data['total'] == 4

        # Verify all tags are present
        tag_names = {tag['name'] for tag in data['data']}
        assert tag_names == set(tags)

    @unittest_run_loop
    async def test_list_tags_with_limit(self):
        """Test listing tags with limit parameter."""
        # Create multiple tags
        for i in range(10):
            await self.client.request(
                "POST",
                "/api/v1/tags",
                json={'name': f'tag_{i}'}
            )

        # List with limit
        resp = await self.client.request("GET", "/api/v1/tags?limit=5")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert len(data['data']) == 5

    @unittest_run_loop
    async def test_search_tags(self):
        """Test searching tags by partial name."""
        # Create tags
        tags = ['portrait', 'landscape', 'land_art', 'fantasy']

        for tag_name in tags:
            await self.client.request(
                "POST",
                "/api/v1/tags",
                json={'name': tag_name}
            )

        # Search for 'land'
        resp = await self.client.request("GET", "/api/v1/tags/search?q=land")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert len(data['data']) == 2  # landscape and land_art
        assert data['query'] == 'land'

        # Verify results contain search term
        for tag in data['data']:
            assert 'land' in tag['name']

    @unittest_run_loop
    async def test_search_tags_no_query(self):
        """Test search without query parameter."""
        resp = await self.client.request("GET", "/api/v1/tags/search")

        assert resp.status == 400
        data = await resp.json()

        assert data['success'] is False
        assert 'error' in data

    @unittest_run_loop
    async def test_search_tags_no_results(self):
        """Test search with no matching tags."""
        resp = await self.client.request("GET", "/api/v1/tags/search?q=nonexistent")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert len(data['data']) == 0

    @unittest_run_loop
    async def test_get_popular_tags(self):
        """Test getting popular tags."""
        # Create tags with different usage counts by creating prompts
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            # Insert tags with different usage counts
            tags = [
                ('portrait', 15),
                ('landscape', 10),
                ('fantasy', 5),
            ]

            for name, usage_count in tags:
                conn.execute("""
                    INSERT INTO tags (name, usage_count, created_at, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (name, usage_count))

            conn.commit()

        # Get popular tags
        resp = await self.client.request("GET", "/api/v1/tags/popular?limit=2")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert len(data['data']) == 2

        # Should be ordered by usage_count descending
        assert data['data'][0]['name'] == 'portrait'
        assert data['data'][0]['usage_count'] == 15

    @unittest_run_loop
    async def test_sync_tags_from_prompts(self):
        """Test synchronizing tags from prompts table."""
        import sqlite3

        # Insert test prompts with tags
        with sqlite3.connect(self.db_path) as conn:
            test_prompts = [
                ('prompt1', 'portrait,fantasy,detailed'),
                ('prompt2', 'landscape,nature'),
                ('prompt3', 'portrait,landscape'),
            ]

            for prompt, tags in test_prompts:
                conn.execute("""
                    INSERT INTO prompts (prompt, negative_prompt, tags)
                    VALUES (?, '', ?)
                """, (prompt, tags))

            conn.commit()

        # Sync tags
        resp = await self.client.request("POST", "/api/v1/tags/sync")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert 'count' in data
        assert data['count'] == 5  # portrait, fantasy, detailed, landscape, nature

    @unittest_run_loop
    async def test_update_tag_usage_counts(self):
        """Test recalculating usage counts."""
        import sqlite3

        # Create tags and prompts
        with sqlite3.connect(self.db_path) as conn:
            # Insert tags with incorrect counts
            conn.execute("""
                INSERT INTO tags (name, usage_count)
                VALUES ('portrait', 0)
            """)

            # Insert prompts with tags
            test_prompts = [
                ('prompt1', 'portrait,fantasy'),
                ('prompt2', 'portrait,landscape'),
            ]

            for prompt, tags in test_prompts:
                conn.execute("""
                    INSERT INTO prompts (prompt, negative_prompt, tags)
                    VALUES (?, '', ?)
                """, (prompt, tags))

            conn.commit()

        # Update counts
        resp = await self.client.request("POST", "/api/v1/tags/update-counts")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert 'count' in data

        # Verify portrait count was updated
        get_resp = await self.client.request("GET", "/api/v1/tags/search?q=portrait")
        tag_data = await get_resp.json()
        assert tag_data['data'][0]['usage_count'] == 2

    @unittest_run_loop
    async def test_cleanup_unused_tags(self):
        """Test cleaning up tags with zero usage."""
        import sqlite3

        # Create tags with various usage counts
        with sqlite3.connect(self.db_path) as conn:
            tags = [
                ('portrait', 10),
                ('landscape', 5),
                ('unused1', 0),
                ('unused2', 0),
            ]

            for name, usage_count in tags:
                conn.execute("""
                    INSERT INTO tags (name, usage_count)
                    VALUES (?, ?)
                """, (name, usage_count))

            conn.commit()

        # Cleanup unused tags
        resp = await self.client.request("POST", "/api/v1/tags/cleanup")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert data['count'] == 2  # unused1 and unused2

        # Verify unused tags are deleted
        list_resp = await self.client.request("GET", "/api/v1/tags")
        list_data = await list_resp.json()

        tag_names = {tag['name'] for tag in list_data['data']}
        assert 'unused1' not in tag_names
        assert 'unused2' not in tag_names
        assert 'portrait' in tag_names
        assert 'landscape' in tag_names

    @unittest_run_loop
    async def test_cleanup_with_threshold(self):
        """Test cleanup with custom threshold."""
        import sqlite3

        # Create tags with various usage counts
        with sqlite3.connect(self.db_path) as conn:
            tags = [
                ('high_usage', 20),
                ('medium_usage', 5),
                ('low_usage', 2),
            ]

            for name, usage_count in tags:
                conn.execute("""
                    INSERT INTO tags (name, usage_count)
                    VALUES (?, ?)
                """, (name, usage_count))

            conn.commit()

        # Cleanup tags with usage <= 5
        resp = await self.client.request("POST", "/api/v1/tags/cleanup?threshold=5")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert data['count'] == 2  # medium_usage and low_usage

    @unittest_run_loop
    async def test_get_tag_stats(self):
        """Test getting tag statistics."""
        import sqlite3

        # Create tags
        with sqlite3.connect(self.db_path) as conn:
            tags = [
                ('portrait', 15),
                ('landscape', 10),
                ('fantasy', 8),
            ]

            for name, usage_count in tags:
                conn.execute("""
                    INSERT INTO tags (name, usage_count)
                    VALUES (?, ?)
                """, (name, usage_count))

            conn.commit()

        # Get stats
        resp = await self.client.request("GET", "/api/v1/tags/stats")

        assert resp.status == 200
        data = await resp.json()

        assert data['success'] is True
        assert 'data' in data
        assert 'total_tags' in data['data']
        assert 'total_usage' in data['data']
        assert 'top_tags' in data['data']

        assert data['data']['total_tags'] == 3
        assert len(data['data']['top_tags']) <= 10

    @unittest_run_loop
    async def test_invalid_json(self):
        """Test handling of invalid JSON in requests."""
        resp = await self.client.request(
            "POST",
            "/api/v1/tags",
            data="invalid json",
            headers={"content-type": "application/json"}
        )

        assert resp.status == 400
        data = await resp.json()

        assert data['success'] is False
        assert 'error' in data

    @unittest_run_loop
    async def test_invalid_tag_id(self):
        """Test handling of invalid tag ID."""
        resp = await self.client.request("GET", "/api/v1/tags/invalid")

        assert resp.status == 400
        data = await resp.json()

        assert data['success'] is False
        assert 'error' in data


@pytest.mark.asyncio
class TestMultiTagFilteringIntegration:
    """Integration tests for multi-tag AND filtering with PromptRepository."""

    async def test_multi_tag_and_filtering(self, db_model):
        """Test filtering prompts with multiple tags (AND logic)."""
        import sqlite3
        from src.repositories.prompt_repository import PromptRepository

        # Create repository
        repo = PromptRepository(db_model.db_path)

        # Create prompts with different tag combinations
        prompts_data = [
            {
                'positive_prompt': 'Cyberpunk portrait',
                'tags': json.dumps(['portrait', 'cyberpunk', 'detailed']),
            },
            {
                'positive_prompt': 'Fantasy landscape',
                'tags': json.dumps(['landscape', 'fantasy', 'detailed']),
            },
            {
                'positive_prompt': 'Portrait with fantasy elements',
                'tags': json.dumps(['portrait', 'fantasy']),
            },
            {
                'positive_prompt': 'Simple portrait',
                'tags': json.dumps(['portrait']),
            },
        ]

        # Insert prompts
        with sqlite3.connect(db_model.db_path) as conn:
            for data in prompts_data:
                conn.execute("""
                    INSERT INTO prompts (prompt, negative_prompt, tags)
                    VALUES (?, '', ?)
                """, (data['positive_prompt'], data['tags']))
            conn.commit()

        # Test single tag filter
        results = repo.list(tags='portrait')
        assert len(results) == 3  # All prompts with 'portrait' tag

        # Test multi-tag AND filter
        results = repo.list(tags=['portrait', 'fantasy'])
        assert len(results) == 1  # Only 'Portrait with fantasy elements'
        assert 'fantasy' in results[0]['prompt'].lower()

        # Test multi-tag AND filter with no results
        results = repo.list(tags=['portrait', 'landscape'])
        assert len(results) == 0  # No prompt has both portrait and landscape

        # Test three-tag AND filter
        results = repo.list(tags=['portrait', 'cyberpunk', 'detailed'])
        assert len(results) == 1  # Only 'Cyberpunk portrait'
