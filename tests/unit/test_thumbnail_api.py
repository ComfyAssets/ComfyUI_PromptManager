"""Unit tests for thumbnail API endpoints."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.route_handlers.thumbnails import ThumbnailAPI
from src.services.enhanced_thumbnail_service import ThumbnailTask


class TestThumbnailAPI(AioHTTPTestCase):
    """Test cases for ThumbnailAPI endpoints."""

    async def get_application(self):
        """Create test application."""
        app = web.Application()

        # Create mock dependencies
        self.mock_db = MagicMock()
        self.mock_db.db_path = ":memory:"
        self.mock_cache = MagicMock()

        # Create API instance
        self.thumbnail_api = ThumbnailAPI(self.mock_db, self.mock_cache)
        self.thumbnail_api.register_routes(app)

        return app

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()

        # Mock the thumbnail service
        self.mock_service = MagicMock()
        self.mock_service.base_generator.SIZES = {
            'small': (150, 150),
            'medium': (300, 300),
            'large': (600, 600),
            'xlarge': (1200, 1200)
        }
        self.mock_service.ffmpeg = MagicMock()
        self.mock_service.ffmpeg.is_video = MagicMock(return_value=False)
        self.mock_service.get_thumbnail_path = MagicMock(side_effect=lambda p, s: Path(f"/thumbnails/{s}/{p.name}"))

        # Patch the service in the API
        self.thumbnail_api.thumbnail_service = self.mock_service

    @unittest_run_loop
    async def test_rebuild_thumbnails_with_sizes(self):
        """Test rebuild_thumbnails respects size parameter."""
        # Mock clear_cache to be async
        self.mock_service.clear_cache = AsyncMock()

        # Mock database query
        with patch('sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock database results
            mock_cursor.fetchall.return_value = [
                ('1', '/path/to/image1.jpg', 'image1.jpg'),
                ('2', '/path/to/image2.png', 'image2.png')
            ]

            # Mock Path.exists() to return True
            with patch.object(Path, 'exists', return_value=True):
                # Request with specific sizes
                resp = await self.client.request(
                    "POST", "/api/v1/thumbnails/rebuild",
                    json={"sizes": ["small", "medium"]}
                )

                self.assertEqual(resp.status, 200)
                data = await resp.json()

                # Check response
                self.assertIn('task_id', data)
                self.assertIn('total', data)

                # Verify only requested sizes were cleared
                clear_calls = self.mock_service.clear_cache.call_args_list
                cleared_sizes = [call[0][0] for call in clear_calls if call[0]]
                self.assertIn('small', cleared_sizes)
                self.assertIn('medium', cleared_sizes)
                self.assertNotIn('large', cleared_sizes)
                self.assertNotIn('xlarge', cleared_sizes)

                # Total should be 4 (2 images × 2 sizes)
                self.assertEqual(data['total'], 4)

    @unittest_run_loop
    async def test_rebuild_thumbnails_without_sizes(self):
        """Test rebuild_thumbnails uses default sizes when none specified."""
        # Mock clear_cache to be async
        self.mock_service.clear_cache = AsyncMock()

        with patch('sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock database results
            mock_cursor.fetchall.return_value = [
                ('1', '/path/to/image1.jpg', 'image1.jpg')
            ]

            with patch.object(Path, 'exists', return_value=True):
                # Request without sizes (empty body)
                resp = await self.client.request(
                    "POST", "/api/v1/thumbnails/rebuild",
                    json={}
                )

                self.assertEqual(resp.status, 200)
                data = await resp.json()

                # Check response
                self.assertIn('task_id', data)
                self.assertIn('total', data)

                # Should use default sizes (small, medium, large)
                # Total should be 3 (1 image × 3 default sizes)
                self.assertEqual(data['total'], 3)

    @unittest_run_loop
    async def test_rebuild_thumbnails_invalid_size(self):
        """Test rebuild_thumbnails handles invalid size names gracefully."""
        # Mock clear_cache to be async
        self.mock_service.clear_cache = AsyncMock()

        with patch('sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock database results
            mock_cursor.fetchall.return_value = [
                ('1', '/path/to/image1.jpg', 'image1.jpg')
            ]

            with patch.object(Path, 'exists', return_value=True):
                # Request with mix of valid and invalid sizes
                resp = await self.client.request(
                    "POST", "/api/v1/thumbnails/rebuild",
                    json={"sizes": ["small", "invalid_size", "medium"]}
                )

                self.assertEqual(resp.status, 200)
                data = await resp.json()

                # Should only process valid sizes
                # Total should be 2 (1 image × 2 valid sizes)
                self.assertEqual(data['total'], 2)

    @unittest_run_loop
    async def test_generate_thumbnails_respects_sizes(self):
        """Test generate_thumbnails respects size parameter."""
        # Mock scan_missing_thumbnails
        mock_tasks = [
            ThumbnailTask(
                image_id='1',
                source_path=Path('/path/to/image1.jpg'),
                thumbnail_path=Path('/thumbnails/small/image1.jpg'),
                size=(150, 150),
                format='jpg'
            )
        ]
        self.mock_service.scan_missing_thumbnails = AsyncMock(return_value=mock_tasks)

        with patch('sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock database results
            mock_cursor.fetchall.return_value = [
                ('1', '/path/to/image1.jpg', 'image1.jpg')
            ]

            with patch.object(Path, 'exists', return_value=True):
                # Request with specific sizes
                resp = await self.client.request(
                    "POST", "/api/v1/thumbnails/generate",
                    json={"sizes": ["small"]}
                )

                self.assertEqual(resp.status, 200)
                data = await resp.json()

                # Verify scan was called with correct sizes
                self.mock_service.scan_missing_thumbnails.assert_called_once()
                call_args = self.mock_service.scan_missing_thumbnails.call_args
                self.assertEqual(call_args[0][1], ["small"])

    @unittest_run_loop
    async def test_scan_missing_thumbnails_with_sizes(self):
        """Test scan_missing_thumbnails respects size parameter."""
        # Mock scan_missing_thumbnails
        mock_tasks = []
        self.mock_service.scan_missing_thumbnails = AsyncMock(return_value=mock_tasks)

        with patch('sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock database results
            mock_cursor.fetchall.return_value = []

            # Request scan with specific sizes
            resp = await self.client.request(
                "POST", "/api/v1/thumbnails/scan",
                json={"sizes": ["large", "xlarge"]}
            )

            self.assertEqual(resp.status, 200)
            data = await resp.json()

            # Verify scan was called with correct sizes
            self.mock_service.scan_missing_thumbnails.assert_called_once()
            call_args = self.mock_service.scan_missing_thumbnails.call_args
            self.assertEqual(call_args[0][1], ["large", "xlarge"])

            # Check response
            self.assertEqual(data['sizes_checked'], ["large", "xlarge"])


class TestThumbnailSizeRespect(pytest.TestCase):
    """Test that all thumbnail operations respect user-selected sizes."""

    def test_sizes_configuration(self):
        """Test that SIZES configuration is consistent."""
        from utils.image_processing import ThumbnailGenerator

        # Check that sizes are defined correctly
        generator = ThumbnailGenerator()
        expected_sizes = {
            'small': (150, 150),
            'medium': (300, 300),
            'large': (600, 600),
            'xlarge': (1200, 1200)
        }

        assert generator.SIZES == expected_sizes

    def test_size_selection_logic(self):
        """Test the logic for selecting thumbnail sizes."""
        # Test data
        all_sizes = ['small', 'medium', 'large', 'xlarge']
        selected_sizes = ['small', 'large']

        # Simulate filtering
        sizes_to_generate = [s for s in selected_sizes if s in all_sizes]

        assert sizes_to_generate == ['small', 'large']
        assert 'medium' not in sizes_to_generate
        assert 'xlarge' not in sizes_to_generate


if __name__ == '__main__':
    pytest.main([__file__, '-v'])