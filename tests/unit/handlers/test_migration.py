"""Unit tests for migration handlers."""

import pytest
from unittest.mock import Mock, AsyncMock

from aiohttp import web

from src.api.handlers.migration import MigrationHandlers


@pytest.fixture
def mock_api():
    """Mock PromptManagerAPI instance."""
    api = Mock()
    api.migration_service = Mock()
    api.logger = Mock()
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return MigrationHandlers(mock_api)


class TestGetMigrationInfo:
    """Test get_migration_info endpoint."""

    @pytest.mark.asyncio
    async def test_get_info_success(self, handler, mock_api):
        """Test successful migration info retrieval."""
        # Arrange
        mock_api.migration_service.get_migration_info.return_value = {
            "status": "ready",
            "v1_path": "/path/to/v1.db",
            "v2_path": "/path/to/v2.db",
            "needs_migration": True
        }
        request = Mock()

        # Act
        response = await handler.get_migration_info(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.get_migration_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_info_error(self, handler, mock_api):
        """Test error handling."""
        # Arrange
        mock_api.migration_service.get_migration_info.side_effect = Exception("Service error")
        request = Mock()

        # Act
        response = await handler.get_migration_info(request)

        # Assert
        assert response.status == 500


class TestGetMigrationStatus:
    """Test get_migration_status endpoint."""

    @pytest.mark.asyncio
    async def test_get_status_success(self, handler, mock_api):
        """Test successful status retrieval."""
        # Arrange
        mock_api.migration_service.get_migration_info.return_value = {
            "status": "completed"
        }
        request = Mock()

        # Act
        response = await handler.get_migration_status(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.get_migration_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_status_ready(self, handler, mock_api):
        """Test status when migration is ready."""
        # Arrange
        mock_api.migration_service.get_migration_info.return_value = {
            "status": "ready"
        }
        request = Mock()

        # Act
        response = await handler.get_migration_status(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_status_error(self, handler, mock_api):
        """Test error handling."""
        # Arrange
        mock_api.migration_service.get_migration_info.side_effect = Exception("Status error")
        request = Mock()

        # Act
        response = await handler.get_migration_status(request)

        # Assert
        assert response.status == 500


class TestGetMigrationProgress:
    """Test get_migration_progress endpoint."""

    @pytest.mark.asyncio
    async def test_get_progress_success(self, handler, mock_api):
        """Test successful progress retrieval."""
        # Arrange
        mock_api.migration_service.get_progress.return_value = {
            "current_step": 5,
            "total_steps": 10,
            "percentage": 50,
            "message": "Migrating prompts..."
        }
        request = Mock()

        # Act
        response = await handler.get_migration_progress(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.get_progress.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_progress_not_started(self, handler, mock_api):
        """Test progress when migration not started."""
        # Arrange
        mock_api.migration_service.get_progress.return_value = {
            "current_step": 0,
            "total_steps": 0,
            "percentage": 0
        }
        request = Mock()

        # Act
        response = await handler.get_migration_progress(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_progress_completed(self, handler, mock_api):
        """Test progress when migration completed."""
        # Arrange
        mock_api.migration_service.get_progress.return_value = {
            "current_step": 10,
            "total_steps": 10,
            "percentage": 100,
            "message": "Migration complete"
        }
        request = Mock()

        # Act
        response = await handler.get_migration_progress(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_progress_error(self, handler, mock_api):
        """Test error handling."""
        # Arrange
        mock_api.migration_service.get_progress.side_effect = Exception("Progress error")
        request = Mock()

        # Act
        response = await handler.get_migration_progress(request)

        # Assert
        assert response.status == 500


class TestStartMigration:
    """Test start_migration endpoint."""

    @pytest.mark.asyncio
    async def test_start_migration_success(self, handler, mock_api):
        """Test successful migration start."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {
            "status": "started",
            "message": "Migration initiated"
        }
        request = Mock()
        request.json = AsyncMock(return_value={"action": "migrate"})

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.start_migration.assert_called_once_with("migrate")

    @pytest.mark.asyncio
    async def test_start_migration_default_action(self, handler, mock_api):
        """Test migration with default action."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {"status": "started"}
        request = Mock()
        request.json = AsyncMock(return_value={})

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.start_migration.assert_called_once_with("migrate")

    @pytest.mark.asyncio
    async def test_start_migration_fresh_start(self, handler, mock_api):
        """Test migration with fresh_start action."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {"status": "started"}
        request = Mock()
        request.json = AsyncMock(return_value={"action": "fresh_start"})

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.start_migration.assert_called_once_with("fresh_start")

    @pytest.mark.asyncio
    async def test_start_migration_invalid_json(self, handler, mock_api):
        """Test migration with invalid JSON."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {"status": "started"}
        request = Mock()
        request.json = AsyncMock(side_effect=Exception("Invalid JSON"))

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 200
        # Should use default action
        mock_api.migration_service.start_migration.assert_called_once_with("migrate")

    @pytest.mark.asyncio
    async def test_start_migration_value_error(self, handler, mock_api):
        """Test migration with invalid action value."""
        # Arrange
        mock_api.migration_service.start_migration.side_effect = ValueError("Invalid action")
        request = Mock()
        request.json = AsyncMock(return_value={"action": "invalid"})

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_start_migration_error(self, handler, mock_api):
        """Test general error handling."""
        # Arrange
        mock_api.migration_service.start_migration.side_effect = Exception("Migration failed")
        request = Mock()
        request.json = AsyncMock(return_value={"action": "migrate"})

        # Act
        response = await handler.start_migration(request)

        # Assert
        assert response.status == 500


class TestTriggerMigration:
    """Test trigger_migration endpoint."""

    @pytest.mark.asyncio
    async def test_trigger_success(self, handler, mock_api):
        """Test successful migration trigger."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {
            "status": "started",
            "message": "Migration triggered"
        }
        request = Mock()

        # Act
        response = await handler.trigger_migration(request)

        # Assert
        assert response.status == 200
        mock_api.migration_service.start_migration.assert_called_once_with("migrate")

    @pytest.mark.asyncio
    async def test_trigger_already_running(self, handler, mock_api):
        """Test trigger when migration already running."""
        # Arrange
        mock_api.migration_service.start_migration.return_value = {
            "status": "already_running"
        }
        request = Mock()

        # Act
        response = await handler.trigger_migration(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_trigger_error(self, handler, mock_api):
        """Test error handling."""
        # Arrange
        mock_api.migration_service.start_migration.side_effect = Exception("Trigger failed")
        request = Mock()

        # Act
        response = await handler.trigger_migration(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_trigger_service_unavailable(self, handler, mock_api):
        """Test when migration service is unavailable."""
        # Arrange
        mock_api.migration_service.start_migration.side_effect = AttributeError("Service unavailable")
        request = Mock()

        # Act
        response = await handler.trigger_migration(request)

        # Assert
        assert response.status == 500


class TestMigrationServiceIntegration:
    """Test integration scenarios."""

    @pytest.mark.asyncio
    async def test_complete_migration_flow(self, handler, mock_api):
        """Test complete migration workflow."""
        # Arrange
        mock_api.migration_service.get_migration_info.return_value = {
            "status": "ready",
            "needs_migration": True
        }
        mock_api.migration_service.start_migration.return_value = {"status": "started"}
        mock_api.migration_service.get_progress.return_value = {
            "percentage": 50
        }

        request = Mock()
        request.json = AsyncMock(return_value={"action": "migrate"})

        # Act - Check info
        info_response = await handler.get_migration_info(request)
        assert info_response.status == 200

        # Act - Start migration
        start_response = await handler.start_migration(request)
        assert start_response.status == 200

        # Act - Check progress
        progress_response = await handler.get_migration_progress(request)
        assert progress_response.status == 200

    @pytest.mark.asyncio
    async def test_status_polling_scenario(self, handler, mock_api):
        """Test status polling during migration."""
        # Arrange - simulate status changes
        status_sequence = ["ready", "running", "running", "completed"]
        mock_api.migration_service.get_migration_info.side_effect = [
            {"status": status} for status in status_sequence
        ]

        request = Mock()

        # Act - Poll status multiple times
        for expected_status in status_sequence:
            response = await handler.get_migration_status(request)
            assert response.status == 200
