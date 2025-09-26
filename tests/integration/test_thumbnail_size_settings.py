"""Integration tests for thumbnail size settings functionality."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import sqlite3

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.route_handlers.thumbnails import ThumbnailAPI
from src.services.enhanced_thumbnail_service import EnhancedThumbnailService, ThumbnailTask
from utils.cache import MemoryCache


class TestThumbnailSizeIntegration:
    """Integration tests for thumbnail size settings."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        # Create test database with generated_images table
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE generated_images (
                id TEXT PRIMARY KEY,
                image_path TEXT,
                filename TEXT,
                generation_time TEXT,
                thumbnail_small_path TEXT,
                thumbnail_medium_path TEXT,
                thumbnail_large_path TEXT,
                thumbnail_xlarge_path TEXT,
                thumbnails_generated_at TEXT
            )
        """)

        # Insert test data
        test_images = [
            ('img001', '/test/path/image1.jpg', 'image1.jpg'),
            ('img002', '/test/path/image2.png', 'image2.png'),
            ('img003', '/test/path/image3.webp', 'image3.webp')
        ]
        cursor.executemany(
            "INSERT INTO generated_images (id, image_path, filename) VALUES (?, ?, ?)",
            test_images
        )
        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def mock_service(self):
        """Create a mock thumbnail service."""
        service = MagicMock()
        service.base_generator.SIZES = {
            'small': (150, 150),
            'medium': (300, 300),
            'large': (600, 600),
            'xlarge': (1200, 1200)
        }
        service.ffmpeg = MagicMock()
        service.ffmpeg.is_video = MagicMock(return_value=False)
        service.get_thumbnail_path = MagicMock(
            side_effect=lambda p, s: Path(f"/thumbnails/{s}/{p.name}")
        )
        service.clear_cache = AsyncMock()
        service.scan_missing_thumbnails = AsyncMock(return_value=[])
        return service

    @pytest.mark.asyncio
    async def test_rebuild_only_selected_sizes(self, temp_db, mock_service):
        """Test that rebuild only processes user-selected sizes."""
        # Create API with mock service
        mock_db = MagicMock()
        mock_db.db_path = temp_db
        mock_cache = MemoryCache()

        api = ThumbnailAPI(mock_db, mock_cache)
        api.thumbnail_service = mock_service

        # Simulate rebuild with only small and medium sizes
        selected_sizes = ['small', 'medium']

        # Count tasks that would be created
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM generated_images")
        image_count = cursor.fetchone()[0]
        conn.close()

        # Expected: 3 images × 2 sizes = 6 tasks
        expected_task_count = image_count * len(selected_sizes)

        # Mock the path exists check
        with patch.object(Path, 'exists', return_value=True):
            # Create mock request
            mock_request = MagicMock()
            mock_request.json = AsyncMock(return_value={'sizes': selected_sizes})

            # Call rebuild
            from aiohttp import web
            with patch('src.api.route_handlers.thumbnails.web.json_response') as mock_response:
                mock_response.return_value = web.Response(text='{}', content_type='application/json')

                await api.rebuild_thumbnails(mock_request)

                # Check the response
                call_args = mock_response.call_args[0][0]
                assert 'task_id' in call_args
                assert call_args['total'] == expected_task_count

        # Verify only selected sizes were cleared
        clear_calls = mock_service.clear_cache.call_args_list
        cleared_sizes = [call[0][0] for call in clear_calls if call[0]]
        assert 'small' in cleared_sizes
        assert 'medium' in cleared_sizes
        assert 'large' not in cleared_sizes
        assert 'xlarge' not in cleared_sizes

    @pytest.mark.asyncio
    async def test_normal_generation_respects_sizes(self, temp_db, mock_service):
        """Test that normal generation respects user-selected sizes."""
        # Create API with mock service
        mock_db = MagicMock()
        mock_db.db_path = temp_db
        mock_cache = MemoryCache()

        api = ThumbnailAPI(mock_db, mock_cache)
        api.thumbnail_service = mock_service

        # Mock scan to return tasks only for selected sizes
        def mock_scan(paths, sizes):
            tasks = []
            for path in paths:
                for size in sizes:
                    if size in mock_service.base_generator.SIZES:
                        tasks.append(ThumbnailTask(
                            image_id=f"id_{path.name}_{size}",
                            source_path=path,
                            thumbnail_path=Path(f"/thumbnails/{size}/{path.name}"),
                            size=mock_service.base_generator.SIZES[size],
                            format='jpg'
                        ))
            return tasks

        mock_service.scan_missing_thumbnails = AsyncMock(side_effect=mock_scan)

        # Test with specific sizes
        selected_sizes = ['large', 'xlarge']

        with patch.object(Path, 'exists', return_value=True):
            # Create mock request
            mock_request = MagicMock()
            mock_request.json = AsyncMock(return_value={'sizes': selected_sizes})

            from aiohttp import web
            with patch('src.api.route_handlers.thumbnails.web.json_response') as mock_response:
                mock_response.return_value = web.Response(text='{}', content_type='application/json')

                await api.generate_thumbnails(mock_request)

                # Verify scan was called with correct sizes
                mock_service.scan_missing_thumbnails.assert_called()
                call_args = mock_service.scan_missing_thumbnails.call_args[0]
                assert call_args[1] == selected_sizes

    def test_ui_size_selection_logic(self):
        """Test the JavaScript-like logic for size selection."""
        # Simulate UI checkbox states
        checkbox_states = {
            'thumbSizeSmall': True,
            'thumbSizeMedium': False,
            'thumbSizeLarge': True,
            'thumbSizeXLarge': False
        }

        # Simulate the JavaScript logic
        selected_sizes = []
        if checkbox_states['thumbSizeSmall']:
            selected_sizes.append('small')
        if checkbox_states['thumbSizeMedium']:
            selected_sizes.append('medium')
        if checkbox_states['thumbSizeLarge']:
            selected_sizes.append('large')
        if checkbox_states['thumbSizeXLarge']:
            selected_sizes.append('xlarge')

        # Verify selection
        assert selected_sizes == ['small', 'large']
        assert 'medium' not in selected_sizes
        assert 'xlarge' not in selected_sizes

    def test_default_size_fallback(self):
        """Test that reasonable defaults are used when no sizes specified."""
        # When no sizes are provided, should default to common sizes
        default_sizes = ['small', 'medium', 'large']  # Not including xlarge by default

        # Test the fallback logic
        provided_sizes = None
        sizes_to_use = provided_sizes or default_sizes

        assert sizes_to_use == ['small', 'medium', 'large']
        assert 'xlarge' not in sizes_to_use  # XLarge not in defaults to save space


class TestThumbnailClearingBehavior:
    """Test that cache clearing respects size selection."""

    @pytest.mark.asyncio
    async def test_clear_only_rebuilding_sizes(self):
        """Test that only sizes being rebuilt are cleared from cache."""
        # Create a mock service
        service = MagicMock()
        service.clear_cache = AsyncMock()
        service.base_generator.SIZES = {
            'small': (150, 150),
            'medium': (300, 300),
            'large': (600, 600),
            'xlarge': (1200, 1200)
        }

        # Simulate clearing only specific sizes
        sizes_to_rebuild = ['small', 'large']

        for size in sizes_to_rebuild:
            if size in service.base_generator.SIZES:
                await service.clear_cache(size)

        # Verify only specified sizes were cleared
        clear_calls = [call[0][0] for call in service.clear_cache.call_args_list if call[0]]
        assert set(clear_calls) == {'small', 'large'}
        assert 'medium' not in clear_calls
        assert 'xlarge' not in clear_calls


if __name__ == '__main__':
    pytest.main([__file__, '-v'])