"""Unit tests for metadata handlers."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from aiohttp import web

from src.api.handlers.metadata import MetadataHandlers


@pytest.fixture
def mock_api():
    """Mock PromptManagerAPI instance."""
    api = Mock()
    api.image_repo = Mock()
    api.metadata_extractor = Mock()
    api.logger = Mock()
    api._sanitize_for_json = Mock(side_effect=lambda x: x)
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return MetadataHandlers(mock_api)


class TestGetImageMetadata:
    """Test get_image_metadata endpoint."""

    @pytest.mark.asyncio
    async def test_get_metadata_success(self, handler, mock_api):
        """Test successful metadata retrieval."""
        # Arrange
        mock_api.image_repo.find_by_filename.return_value = {
            "id": 1,
            "filename": "test.png",
            "image_path": "/tmp/test.png"
        }
        mock_api.metadata_extractor.extract_from_file.return_value = {
            "width": 512,
            "height": 512,
            "model": "test_model"
        }
        request = Mock()
        request.match_info = {"filename": "test.png"}

        # Act
        response = await handler.get_image_metadata(request)

        # Assert
        assert response.status == 200
        mock_api.image_repo.find_by_filename.assert_called_once_with("test.png")
        mock_api.metadata_extractor.extract_from_file.assert_called_once_with("/tmp/test.png")

    @pytest.mark.asyncio
    async def test_get_metadata_image_not_found(self, handler, mock_api):
        """Test 404 when image not found in database."""
        # Arrange
        mock_api.image_repo.find_by_filename.return_value = None
        request = Mock()
        request.match_info = {"filename": "nonexistent.png"}

        # Act
        response = await handler.get_image_metadata(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_metadata_file_not_found(self, handler, mock_api):
        """Test 404 when image file doesn't exist on filesystem."""
        # Arrange
        mock_api.image_repo.find_by_filename.return_value = {
            "image_path": "/nonexistent/test.png"
        }
        mock_api.metadata_extractor.extract_from_file.side_effect = FileNotFoundError()
        request = Mock()
        request.match_info = {"filename": "test.png"}

        # Act
        response = await handler.get_image_metadata(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_metadata_extraction_error(self, handler, mock_api):
        """Test error handling during metadata extraction."""
        # Arrange
        mock_api.image_repo.find_by_filename.return_value = {
            "image_path": "/tmp/corrupt.png"
        }
        mock_api.metadata_extractor.extract_from_file.side_effect = Exception("Corrupt file")
        request = Mock()
        request.match_info = {"filename": "corrupt.png"}

        # Act
        response = await handler.get_image_metadata(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_get_metadata_with_complex_metadata(self, handler, mock_api):
        """Test metadata with nested structures."""
        # Arrange
        mock_api.image_repo.find_by_filename.return_value = {
            "image_path": "/tmp/test.png"
        }
        mock_api.metadata_extractor.extract_from_file.return_value = {
            "width": 1024,
            "height": 1024,
            "model": "sd_xl",
            "prompt": {"positive": "test", "negative": "bad"},
            "parameters": {"steps": 20, "cfg": 7.0}
        }
        request = Mock()
        request.match_info = {"filename": "test.png"}

        # Act
        response = await handler.get_image_metadata(request)

        # Assert
        assert response.status == 200


class TestExtractMetadata:
    """Test extract_metadata endpoint."""

    @pytest.mark.asyncio
    async def test_extract_success(self, handler, mock_api):
        """Test successful metadata extraction from uploaded file."""
        # Arrange
        mock_field = AsyncMock()
        mock_field.name = "file"
        mock_field.read = AsyncMock(return_value=b"fake_image_data")

        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(return_value=mock_field)

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        mock_api.metadata_extractor.extract_from_bytes.return_value = {
            "format": "PNG",
            "width": 512,
            "height": 512
        }

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 200
        mock_api.metadata_extractor.extract_from_bytes.assert_called_once_with(b"fake_image_data")

    @pytest.mark.asyncio
    async def test_extract_missing_file_field(self, handler, mock_api):
        """Test error when no file field in multipart data."""
        # Arrange
        mock_field1 = AsyncMock()
        mock_field1.name = "other_field"

        mock_reader = AsyncMock()
        # First call returns non-file field, second returns None
        mock_reader.next = AsyncMock(side_effect=[mock_field1, None])

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_extract_empty_multipart(self, handler, mock_api):
        """Test error when multipart payload is empty."""
        # Arrange
        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(return_value=None)

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_extract_extraction_error(self, handler, mock_api):
        """Test error handling during metadata extraction."""
        # Arrange
        mock_field = AsyncMock()
        mock_field.name = "file"
        mock_field.read = AsyncMock(return_value=b"corrupt_data")

        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(return_value=mock_field)

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        mock_api.metadata_extractor.extract_from_bytes.side_effect = Exception("Invalid image")

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_extract_with_sanitization(self, handler, mock_api):
        """Test metadata sanitization is applied."""
        # Arrange
        mock_field = AsyncMock()
        mock_field.name = "file"
        mock_field.read = AsyncMock(return_value=b"image_data")

        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(return_value=mock_field)

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        raw_metadata = {"key": "value", "nested": {"data": "test"}}
        mock_api.metadata_extractor.extract_from_bytes.return_value = raw_metadata
        mock_api._sanitize_for_json.return_value = {"sanitized": "data"}

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 200
        mock_api._sanitize_for_json.assert_called_once_with(raw_metadata)

    @pytest.mark.asyncio
    async def test_extract_skips_non_file_fields(self, handler, mock_api):
        """Test that non-file fields are skipped."""
        # Arrange
        mock_field1 = AsyncMock()
        mock_field1.name = "other"

        mock_field2 = AsyncMock()
        mock_field2.name = "metadata"

        mock_field3 = AsyncMock()
        mock_field3.name = "file"
        mock_field3.read = AsyncMock(return_value=b"data")

        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(side_effect=[mock_field1, mock_field2, mock_field3])

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        mock_api.metadata_extractor.extract_from_bytes.return_value = {}

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 200
        # Verify we called next() 3 times (skipped 2, processed 1)
        assert mock_reader.next.call_count == 3

    @pytest.mark.asyncio
    async def test_extract_logs_file_size(self, handler, mock_api):
        """Test that file size is logged."""
        # Arrange
        test_data = b"x" * 1024  # 1KB of data

        mock_field = AsyncMock()
        mock_field.name = "file"
        mock_field.read = AsyncMock(return_value=test_data)

        mock_reader = AsyncMock()
        mock_reader.next = AsyncMock(return_value=mock_field)

        request = Mock()
        request.remote = "127.0.0.1"
        request.multipart = AsyncMock(return_value=mock_reader)

        mock_api.metadata_extractor.extract_from_bytes.return_value = {}

        # Act
        response = await handler.extract_metadata(request)

        # Assert
        assert response.status == 200
        # Verify logger was called with size info
        assert any("1024" in str(call) for call in mock_api.logger.info.call_args_list)
