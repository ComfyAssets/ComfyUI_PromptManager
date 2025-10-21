"""Unit tests for gallery handlers."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from aiohttp import web

from src.api.handlers.gallery import GalleryHandlers


@pytest.fixture
def mock_api(tmp_path):
    """Mock PromptManagerAPI instance."""
    # Create a temporary test database
    db_path = tmp_path / "test.db"

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_images (
            id INTEGER PRIMARY KEY,
            filename TEXT,
            file_path TEXT,
            file_size INTEGER,
            generation_time TEXT,
            prompt_id INTEGER,
            thumbnail_path TEXT,
            thumbnail_small_path TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY,
            category TEXT,
            positive_prompt TEXT,
            tags TEXT,
            rating INTEGER
        )
    """)
    conn.commit()
    conn.close()

    api = Mock()
    api.image_repo = Mock()
    api.image_repo.db_path = str(db_path)
    api.generated_image_repo = Mock()
    api.generated_image_repo.db_path = str(db_path)
    api.prompt_repo = Mock()
    api.gallery = Mock()
    api.metadata_extractor = Mock()
    api.logger = Mock()
    api._format_prompt = Mock(side_effect=lambda x: x)
    api._sanitize_for_json = Mock(side_effect=lambda x: x)
    api._get_allowed_image_roots = Mock(return_value=[])
    api._validate_image_path = Mock(return_value=(True, None))
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return GalleryHandlers(mock_api)


class TestListGalleryImages:
    """Test list_gallery_images endpoint."""

    @pytest.mark.asyncio
    async def test_list_success_with_defaults(self, handler, mock_api, tmp_path):
        """Test successful listing with default pagination."""
        # Arrange - Insert test data
        import sqlite3
        db_path = mock_api.image_repo.db_path
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO generated_images (id, filename, file_path, generation_time) VALUES (1, 'test.png', '/test/test.png', '2024-01-01 12:00:00')")
            conn.commit()

        request = Mock()
        request.query = {"page": "1", "limit": "50"}

        # Act
        response = await handler.list_gallery_images(request)

        # Assert
        assert response.status == 200
        data = json.loads(response.text)
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["filename"] == "test.png"
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["limit"] == 50

    @pytest.mark.asyncio
    async def test_list_with_custom_pagination(self, handler, mock_api, tmp_path):
        """Test listing with custom page and limit."""
        # Arrange
        request = Mock()
        request.query = {"page": "3", "limit": "100"}

        # Act
        response = await handler.list_gallery_images(request)

        # Assert
        assert response.status == 200
        data = json.loads(response.text)
        assert data["pagination"]["page"] == 3
        assert data["pagination"]["limit"] == 100

    @pytest.mark.asyncio
    async def test_list_with_invalid_page_defaults_to_1(self, handler, mock_api, tmp_path):
        """Test invalid page parameter defaults to 1."""
        # Arrange
        request = Mock()
        request.query = {"page": "invalid", "limit": "50"}

        # Act
        response = await handler.list_gallery_images(request)

        # Assert
        assert response.status == 200
        data = json.loads(response.text)
        assert data["pagination"]["page"] == 1

    @pytest.mark.asyncio
    async def test_list_caps_limit_at_1000(self, handler, mock_api, tmp_path):
        """Test limit is capped at 1000."""
        # Arrange
        request = Mock()
        request.query = {"page": "1", "limit": "2000"}

        # Act
        response = await handler.list_gallery_images(request)

        # Assert
        assert response.status == 200
        data = json.loads(response.text)
        assert data["pagination"]["limit"] == 1000

    @pytest.mark.asyncio
    async def test_list_error_handling(self, handler, mock_api, tmp_path):
        """Test error handling in list endpoint."""
        # Arrange - Set db_path to invalid path to trigger error
        original_db_path = mock_api.generated_image_repo.db_path
        mock_api.generated_image_repo.db_path = "/nonexistent/path.db"
        request = Mock()
        request.query = {}

        # Act
        response = await handler.list_gallery_images(request)

        # Assert
        assert response.status == 500

        # Cleanup
        mock_api.generated_image_repo.db_path = original_db_path


class TestGetGalleryImage:
    """Test get_gallery_image endpoint."""

    @pytest.mark.asyncio
    async def test_get_success(self, handler, mock_api):
        """Test successful image retrieval."""
        # Arrange
        mock_api.image_repo.read.return_value = {
            "id": 1,
            "name": "test.png",
            "prompt_id": None
        }
        request = Mock()
        request.match_info = {"id": "1"}

        # Act
        response = await handler.get_gallery_image(request)

        # Assert
        assert response.status == 200
        mock_api.image_repo.read.assert_called_once_with("1")

    @pytest.mark.asyncio
    async def test_get_with_prompt(self, handler, mock_api):
        """Test image retrieval with associated prompt."""
        # Arrange
        mock_api.image_repo.read.return_value = {
            "id": 1,
            "prompt_id": 5
        }
        mock_api.prompt_repo.read.return_value = {
            "id": 5,
            "text": "test prompt"
        }
        request = Mock()
        request.match_info = {"id": "1"}

        # Act
        response = await handler.get_gallery_image(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.read.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_get_not_found(self, handler, mock_api):
        """Test 404 when image not found."""
        # Arrange
        mock_api.image_repo.read.return_value = None
        request = Mock()
        request.match_info = {"id": "999"}

        # Act
        response = await handler.get_gallery_image(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_error_handling(self, handler, mock_api):
        """Test error handling."""
        # Arrange
        mock_api.image_repo.read.side_effect = Exception("Test error")
        request = Mock()
        request.match_info = {"id": "1"}

        # Act
        response = await handler.get_gallery_image(request)

        # Assert
        assert response.status == 500


class TestGetGalleryImageFile:
    """Test get_gallery_image_file endpoint."""

    @pytest.mark.asyncio
    async def test_get_file_success(self, handler, mock_api):
        """Test successful file retrieval."""
        # Arrange
        mock_api.image_repo.read.return_value = {
            "file_path": "/tmp/test.png"
        }
        request = Mock()
        request.match_info = {"id": "1"}
        request.query = {}

        with patch('pathlib.Path.expanduser') as mock_expand:
            mock_path = Mock()
            mock_path.exists.return_value = True
            mock_expand.return_value = mock_path

            with patch('aiohttp.web.FileResponse') as mock_file_response:
                mock_file_response.return_value = Mock()

                # Act
                response = await handler.get_gallery_image_file(request)

                # Assert
                mock_file_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_file_invalid_id(self, handler, mock_api):
        """Test invalid image ID."""
        # Arrange
        request = Mock()
        request.match_info = {"id": "invalid"}

        # Act
        response = await handler.get_gallery_image_file(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_get_file_not_found(self, handler, mock_api):
        """Test 404 when image not found."""
        # Arrange
        mock_api.image_repo.read.return_value = None
        request = Mock()
        request.match_info = {"id": "1"}

        # Act
        response = await handler.get_gallery_image_file(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_thumbnail(self, handler, mock_api):
        """Test thumbnail retrieval."""
        # Arrange
        mock_api.image_repo.read.return_value = {
            "thumbnail_path": "/tmp/thumb.png"
        }
        request = Mock()
        request.match_info = {"id": "1"}
        request.query = {"thumbnail": "1"}

        with patch('pathlib.Path.expanduser') as mock_expand:
            mock_path = Mock()
            mock_path.exists.return_value = True
            mock_expand.return_value = mock_path

            with patch('aiohttp.web.FileResponse') as mock_file_response:
                mock_file_response.return_value = Mock()

                # Act
                response = await handler.get_gallery_image_file(request)

                # Assert
                mock_file_response.assert_called_once()


class TestScanForImages:
    """Test scan_for_images endpoint."""

    @pytest.mark.asyncio
    async def test_scan_success(self, handler, mock_api):
        """Test successful directory scan."""
        # Arrange
        mock_api.gallery.scan_directory.return_value = [
            {"name": "img1.png"},
            {"name": "img2.png"}
        ]
        request = Mock()
        request.json = AsyncMock(return_value={"directory": "/tmp"})

        with patch('os.path.exists', return_value=True):
            # Act
            response = await handler.scan_for_images(request)

            # Assert
            assert response.status == 200
            mock_api.gallery.scan_directory.assert_called_once_with("/tmp")
            assert mock_api.image_repo.create.call_count == 2

    @pytest.mark.asyncio
    async def test_scan_invalid_directory(self, handler, mock_api):
        """Test scan with invalid directory."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"directory": ""})

        # Act
        response = await handler.scan_for_images(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_scan_nonexistent_directory(self, handler, mock_api):
        """Test scan with non-existent directory."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"directory": "/nonexistent"})

        with patch('os.path.exists', return_value=False):
            # Act
            response = await handler.scan_for_images(request)

            # Assert
            assert response.status == 400


class TestScanComfyUIImages:
    """Test scan_comfyui_images endpoint."""

    @pytest.mark.asyncio
    async def test_scan_comfyui_success(self, handler, mock_api):
        """Test successful ComfyUI scan."""
        # Arrange
        request = Mock()

        async def mock_generator():
            yield {"type": "progress", "processed": 10, "found": 5}
            yield {"type": "complete", "processed": 20, "found": 10, "added": 8, "linked": 15}

        with patch('src.services.image_scanner.ImageScanner') as mock_scanner:
            mock_scanner.return_value.scan_images_generator.return_value = mock_generator()

            # Act
            response = await handler.scan_comfyui_images(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_scan_comfyui_error(self, handler, mock_api):
        """Test ComfyUI scan error handling."""
        # Arrange
        request = Mock()

        async def mock_generator():
            yield {"type": "error", "message": "Test error"}

        with patch('src.services.image_scanner.ImageScanner') as mock_scanner:
            mock_scanner.return_value.scan_images_generator.return_value = mock_generator()

            # Act
            response = await handler.scan_comfyui_images(request)

            # Assert
            assert response.status == 500

    @pytest.mark.asyncio
    async def test_scan_comfyui_exception(self, handler, mock_api):
        """Test exception handling in ComfyUI scan."""
        # Arrange
        request = Mock()

        with patch('src.services.image_scanner.ImageScanner', side_effect=Exception("Test error")):
            # Act
            response = await handler.scan_comfyui_images(request)

            # Assert
            assert response.status == 500


class TestGetGeneratedImageMetadata:
    """Test get_generated_image_metadata endpoint."""

    @pytest.mark.asyncio
    async def test_get_metadata_success(self, handler, mock_api):
        """Test successful metadata retrieval."""
        # Arrange
        mock_api.generated_image_repo.read.return_value = {
            "id": 1,
            "prompt_id": 5,
            "metadata": {"model": "test_model"},
            "file_path": "/tmp/test.png"
        }
        request = Mock()
        request.match_info = {"image_id": "1"}

        # Act
        response = await handler.get_generated_image_metadata(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_metadata_not_found(self, handler, mock_api):
        """Test 404 when image not found."""
        # Arrange
        mock_api.generated_image_repo.read.return_value = None
        request = Mock()
        request.match_info = {"image_id": "999"}

        # Act
        response = await handler.get_generated_image_metadata(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_metadata_invalid_id(self, handler, mock_api):
        """Test invalid image ID."""
        # Arrange
        request = Mock()
        request.match_info = {"image_id": "invalid"}

        # Act
        response = await handler.get_generated_image_metadata(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_get_metadata_repo_unavailable(self, handler, mock_api):
        """Test when generated_image_repo is unavailable."""
        # Arrange
        handler.generated_image_repo = None
        request = Mock()
        request.match_info = {"image_id": "1"}

        # Act
        response = await handler.get_generated_image_metadata(request)

        # Assert
        assert response.status == 500
