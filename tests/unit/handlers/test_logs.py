"""Unit tests for logs handlers."""

import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock, mock_open
from datetime import datetime

from aiohttp import web

from src.api.handlers.logs import LogsHandlers


@pytest.fixture
def mock_api():
    """Mock PromptManagerAPI instance."""
    api = Mock()
    api.logger = Mock()
    api.logs_dir = Path("/tmp/logs")
    api.log_file = "/tmp/logs/promptmanager.log"
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return LogsHandlers(mock_api)


class TestListLogs:
    """Test list_logs endpoint."""

    @pytest.mark.asyncio
    async def test_list_success(self, handler, mock_api):
        """Test successful log listing."""
        # Arrange
        mock_files = [
            {"name": "promptmanager.log", "size": 1024, "modified_at": "2025-01-01T12:00:00"},
            {"name": "promptmanager.log.1", "size": 2048, "modified_at": "2025-01-01T11:00:00"}
        ]
        handler._collect_log_files = Mock(return_value=mock_files)
        handler._determine_active_log = Mock(return_value="promptmanager.log")
        handler._resolve_log_path = Mock(return_value=Path("/tmp/logs/promptmanager.log"))
        handler._read_log_tail = Mock(return_value="log content")

        request = Mock()
        request.query = {"tail": "100"}

        # Act
        response = await handler.list_logs(request)

        # Assert
        assert response.status == 200
        handler._collect_log_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_with_requested_file(self, handler, mock_api):
        """Test listing with specific file requested."""
        # Arrange
        mock_files = [
            {"name": "promptmanager.log", "size": 1024, "modified_at": "2025-01-01T12:00:00"},
            {"name": "promptmanager.log.1", "size": 2048, "modified_at": "2025-01-01T11:00:00"}
        ]
        handler._collect_log_files = Mock(return_value=mock_files)
        handler._determine_active_log = Mock(return_value="promptmanager.log.1")
        handler._resolve_log_path = Mock(return_value=Path("/tmp/logs/promptmanager.log.1"))
        handler._read_log_tail = Mock(return_value="old log content")

        request = Mock()
        request.query = {"file": "promptmanager.log.1", "tail": "50"}

        # Act
        response = await handler.list_logs(request)

        # Assert
        assert response.status == 200
        handler._determine_active_log.assert_called_once_with("promptmanager.log.1", mock_files)

    @pytest.mark.asyncio
    async def test_list_collection_error(self, handler, mock_api):
        """Test error handling when log collection fails."""
        # Arrange
        handler._collect_log_files = Mock(side_effect=Exception("Permission denied"))
        request = Mock()
        request.query = {}

        # Act
        response = await handler.list_logs(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_list_invalid_tail_parameter(self, handler, mock_api):
        """Test handling of invalid tail parameter."""
        # Arrange
        handler._collect_log_files = Mock(return_value=[])
        handler._determine_active_log = Mock(return_value=None)

        request = Mock()
        request.query = {"tail": "invalid"}

        # Act
        response = await handler.list_logs(request)

        # Assert
        assert response.status == 200
        # Should use default tail value

    @pytest.mark.asyncio
    async def test_list_missing_active_log(self, handler, mock_api):
        """Test when active log file is missing."""
        # Arrange
        mock_files = [{"name": "test.log", "size": 100, "modified_at": "2025-01-01T12:00:00"}]
        handler._collect_log_files = Mock(return_value=mock_files)
        handler._determine_active_log = Mock(return_value="test.log")
        handler._resolve_log_path = Mock(return_value=None)

        request = Mock()
        request.query = {}

        # Act
        response = await handler.list_logs(request)

        # Assert
        assert response.status == 200
        mock_api.logger.warning.assert_called()


class TestGetLogFile:
    """Test get_log_file endpoint."""

    @pytest.mark.asyncio
    async def test_get_success(self, handler, mock_api):
        """Test successful log file retrieval."""
        # Arrange
        handler._resolve_log_path = Mock(return_value=Path("/tmp/logs/test.log"))
        handler._read_log_tail = Mock(return_value="log content")

        request = Mock()
        request.match_info = {"name": "test.log"}
        request.query = {"tail": "100"}

        # Act
        response = await handler.get_log_file(request)

        # Assert
        assert response.status == 200
        handler._resolve_log_path.assert_called_once_with("test.log")

    @pytest.mark.asyncio
    async def test_get_missing_name(self, handler, mock_api):
        """Test error when log name is missing."""
        # Arrange
        request = Mock()
        request.match_info = {}

        # Act
        response = await handler.get_log_file(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_get_file_not_found(self, handler, mock_api):
        """Test 404 when log file doesn't exist."""
        # Arrange
        handler._resolve_log_path = Mock(return_value=None)

        request = Mock()
        request.match_info = {"name": "nonexistent.log"}

        # Act
        response = await handler.get_log_file(request)

        # Assert
        assert response.status == 404

    @pytest.mark.asyncio
    async def test_get_read_error(self, handler, mock_api):
        """Test error handling when reading fails."""
        # Arrange
        handler._resolve_log_path = Mock(return_value=Path("/tmp/logs/test.log"))
        handler._read_log_tail = Mock(side_effect=Exception("Read error"))

        request = Mock()
        request.match_info = {"name": "test.log"}
        request.query = {}

        # Act
        response = await handler.get_log_file(request)

        # Assert
        assert response.status == 500


class TestClearLogs:
    """Test clear_logs endpoint."""

    @pytest.mark.asyncio
    @patch('src.api.handlers.logs.LogConfig')
    async def test_clear_success(self, mock_log_config, handler, mock_api):
        """Test successful log clearing."""
        # Arrange
        mock_log_config.clear_logs = Mock()
        request = Mock()

        # Act
        response = await handler.clear_logs(request)

        # Assert
        assert response.status == 200
        mock_log_config.clear_logs.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.api.handlers.logs.LogConfig')
    async def test_clear_error(self, mock_log_config, handler, mock_api):
        """Test error handling during log clearing."""
        # Arrange
        mock_log_config.clear_logs = Mock(side_effect=Exception("Permission denied"))
        request = Mock()

        # Act
        response = await handler.clear_logs(request)

        # Assert
        assert response.status == 500


class TestRotateLogs:
    """Test rotate_logs endpoint."""

    @pytest.mark.asyncio
    @patch('src.api.handlers.logs.LogConfig')
    async def test_rotate_success(self, mock_log_config, handler, mock_api):
        """Test successful log rotation."""
        # Arrange
        mock_log_config.rotate_logs = Mock(return_value=True)
        request = Mock()

        # Act
        response = await handler.rotate_logs(request)

        # Assert
        assert response.status == 200
        mock_log_config.rotate_logs.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.api.handlers.logs.LogConfig')
    async def test_rotate_no_active_file(self, mock_log_config, handler, mock_api):
        """Test rotation when no active log file."""
        # Arrange
        mock_log_config.rotate_logs = Mock(return_value=False)
        request = Mock()

        # Act
        response = await handler.rotate_logs(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    @patch('src.api.handlers.logs.LogConfig')
    async def test_rotate_error(self, mock_log_config, handler, mock_api):
        """Test error handling during rotation."""
        # Arrange
        mock_log_config.rotate_logs = Mock(side_effect=Exception("Rotation failed"))
        request = Mock()

        # Act
        response = await handler.rotate_logs(request)

        # Assert
        assert response.status == 500


class TestDownloadLog:
    """Test download_log endpoint."""

    @pytest.mark.asyncio
    async def test_download_success(self, handler, mock_api):
        """Test successful log download."""
        # Arrange
        test_path = Path("/tmp/logs/test.log")
        handler._resolve_log_path = Mock(return_value=test_path)

        request = Mock()
        request.match_info = {"name": "test.log"}

        with patch('aiohttp.web.FileResponse') as mock_file_response:
            mock_response = Mock()
            mock_file_response.return_value = mock_response

            # Act
            response = await handler.download_log(request)

            # Assert
            mock_file_response.assert_called_once()
            call_args = mock_file_response.call_args
            assert call_args[0][0] == test_path
            assert "Content-Disposition" in call_args[1]["headers"]

    @pytest.mark.asyncio
    async def test_download_missing_name(self, handler, mock_api):
        """Test error when log name is missing."""
        # Arrange
        request = Mock()
        request.match_info = {}

        # Act
        response = await handler.download_log(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_download_file_not_found(self, handler, mock_api):
        """Test 404 when log file doesn't exist."""
        # Arrange
        handler._resolve_log_path = Mock(return_value=None)

        request = Mock()
        request.match_info = {"name": "nonexistent.log"}

        # Act
        response = await handler.download_log(request)

        # Assert
        assert response.status == 404


class TestHelperMethods:
    """Test helper methods."""

    def test_parse_tail_default(self, handler):
        """Test default tail parsing."""
        result = handler._parse_tail(None)
        assert result == 500

    def test_parse_tail_valid(self, handler):
        """Test valid tail parsing."""
        result = handler._parse_tail("100")
        assert result == 100

    def test_parse_tail_invalid(self, handler):
        """Test invalid tail parsing."""
        result = handler._parse_tail("invalid")
        assert result == 500

    def test_parse_tail_negative(self, handler):
        """Test negative tail values are clamped to 0."""
        result = handler._parse_tail("-50")
        assert result == 0

    def test_collect_log_files_no_logs_dir(self, handler):
        """Test collection when logs_dir is None."""
        handler.logs_dir = None
        result = handler._collect_log_files()
        assert result == []

    def test_collect_log_files_dir_not_exists(self, handler):
        """Test collection when directory doesn't exist."""
        handler.logs_dir = Path("/nonexistent")
        result = handler._collect_log_files()
        assert result == []

    def test_determine_active_log_requested(self, handler):
        """Test determining active log with requested file."""
        files = [
            {"name": "a.log"},
            {"name": "b.log"}
        ]
        result = handler._determine_active_log("b.log", files)
        assert result == "b.log"

    def test_determine_active_log_primary(self, handler):
        """Test determining active log with primary log file."""
        handler.log_file = "/tmp/logs/promptmanager.log"
        files = [
            {"name": "promptmanager.log"},
            {"name": "other.log"}
        ]
        result = handler._determine_active_log(None, files)
        assert result == "promptmanager.log"

    def test_determine_active_log_first(self, handler):
        """Test determining active log falls back to first file."""
        handler.log_file = None
        files = [{"name": "first.log"}]
        result = handler._determine_active_log(None, files)
        assert result == "first.log"

    def test_determine_active_log_empty(self, handler):
        """Test determining active log with no files."""
        result = handler._determine_active_log(None, [])
        assert result is None

    def test_resolve_log_path_security(self, handler):
        """Test path resolution prevents directory traversal."""
        handler.logs_dir = Path("/tmp/logs")
        result = handler._resolve_log_path("../../etc/passwd")
        # Should sanitize to just "passwd" and look in logs_dir
        # But since it won't exist, should return None
        assert result is None

    def test_read_log_tail_full_file(self, handler):
        """Test reading full log file."""
        test_content = "line1\nline2\nline3\n"

        with patch('pathlib.Path.read_text', return_value=test_content):
            mock_path = Mock(spec=Path)
            mock_path.read_text = Mock(return_value=test_content)

            result = handler._read_log_tail(mock_path, None)
            assert result == test_content

    def test_read_log_tail_with_limit(self, handler):
        """Test reading last N lines from log file."""
        mock_path = Mock(spec=Path)
        mock_path.open = mock_open(read_data="line1\nline2\nline3\nline4\nline5\n")

        result = handler._read_log_tail(mock_path, 2)
        assert "line4" in result
        assert "line5" in result
