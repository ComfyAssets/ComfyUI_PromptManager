"""Unit tests for system handlers."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from pathlib import Path

from aiohttp import web

from src.api.handlers.system import SystemHandlers


@pytest.fixture
def mock_api():
    """Mock PromptManagerAPI instance."""
    api = Mock()
    api.prompt_repo = Mock()
    api.image_repo = Mock()
    api.generated_image_repo = Mock()
    api.logger = Mock()
    api.db_path = "/tmp/test.db"
    api.stats_service = None
    api.incremental_stats = None
    api._format_prompt = Mock(side_effect=lambda p, **kwargs: p)
    api._format_generated_image = Mock(side_effect=lambda img: img)
    api._build_settings_payload = Mock(return_value={"theme": "dark"})
    api._refresh_repositories = Mock()
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return SystemHandlers(mock_api)


class TestHealthCheck:
    """Test health_check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, handler, mock_api):
        """Test successful health check."""
        # Arrange
        mock_api.prompt_repo.health_check = Mock(return_value=True)
        request = Mock()

        with patch('shutil.disk_usage') as mock_disk:
            mock_stat = Mock()
            mock_stat.free = 100 * (1024**3)  # 100 GB
            mock_disk.return_value = mock_stat

            # Act
            response = await handler.health_check(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_health_check_degraded(self, handler, mock_api):
        """Test health check with database issues."""
        # Arrange
        mock_api.prompt_repo.health_check = Mock(return_value=False)
        request = Mock()

        with patch('shutil.disk_usage') as mock_disk:
            mock_stat = Mock()
            mock_stat.free = 10 * (1024**3)
            mock_disk.return_value = mock_stat

            # Act
            response = await handler.health_check(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_health_check_error(self, handler, mock_api):
        """Test health check error handling."""
        # Arrange
        mock_api.prompt_repo.health_check = Mock(side_effect=Exception("DB error"))
        request = Mock()

        # Act
        response = await handler.health_check(request)

        # Assert
        assert response.status == 500


class TestGetStatistics:
    """Test get_statistics endpoint."""

    @pytest.mark.asyncio
    async def test_get_statistics_success(self, handler, mock_api):
        """Test successful statistics retrieval."""
        # Arrange
        mock_api.prompt_repo.get_statistics.return_value = {
            "total_prompts": 100,
            "categories": 5
        }
        mock_api.generated_image_repo.count.return_value = 500
        request = Mock()

        # Act
        response = await handler.get_statistics(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.get_statistics.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_statistics_with_fallback(self, mock_api):
        """Test statistics with image repo fallback."""
        # Arrange
        mock_api.prompt_repo.get_statistics.return_value = {"total_prompts": 50}
        mock_api.generated_image_repo = None
        mock_api.image_repo.count.return_value = 200
        
        # Create handler after setting generated_image_repo to None
        handler = SystemHandlers(mock_api)
        request = Mock()

        # Act
        response = await handler.get_statistics(request)

        # Assert
        assert response.status == 200
        mock_api.image_repo.count.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_statistics_error(self, handler, mock_api):
        """Test statistics error handling."""
        # Arrange
        mock_api.prompt_repo.get_statistics.side_effect = Exception("Stats error")
        request = Mock()

        # Act
        response = await handler.get_statistics(request)

        # Assert
        assert response.status == 500


class TestGetStatsOverview:
    """Test get_stats_overview endpoint."""

    @pytest.mark.asyncio
    async def test_overview_with_hybrid_service(self, handler, mock_api):
        """Test overview using hybrid stats service."""
        # Arrange
        request = Mock()
        request.query = {}

        mock_hybrid = Mock()
        mock_hybrid.get_overview.return_value = {"prompts": 100, "images": 500}

        with patch('src.services.hybrid_stats_service.HybridStatsService', return_value=mock_hybrid):
            # Act
            response = await handler.get_stats_overview(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_overview_with_cache_fallback(self, handler, mock_api):
        """Test overview falling back to cache service."""
        # Arrange
        request = Mock()
        request.query = {}

        mock_cache = Mock()
        mock_cache.get_overview.return_value = {"prompts": 50}

        with patch('src.services.hybrid_stats_service.HybridStatsService', side_effect=ImportError):
            with patch('src.services.stats_cache_service.StatsCacheService', return_value=mock_cache):
                # Act
                response = await handler.get_stats_overview(request)

                # Assert
                assert response.status == 200

    @pytest.mark.asyncio
    async def test_overview_with_incremental_stats(self, mock_api):
        """Test overview using incremental stats."""
        # Arrange
        mock_api.incremental_stats = Mock()
        mock_api.incremental_stats.calculate_incremental_stats.return_value = {"prompts": 75}
        handler = SystemHandlers(mock_api)
        request = Mock()
        request.query = {}

        with patch('src.services.hybrid_stats_service.HybridStatsService', side_effect=ImportError):
            with patch('src.services.stats_cache_service.StatsCacheService', side_effect=Exception):
                # Act
                response = await handler.get_stats_overview(request)

                # Assert
                assert response.status == 200

    @pytest.mark.asyncio
    async def test_overview_service_unavailable(self, mock_api):
        """Test overview when no service available."""
        # Arrange
        mock_api.incremental_stats = None
        mock_api.stats_service = None
        handler = SystemHandlers(mock_api)
        request = Mock()
        request.query = {}

        with patch('src.services.hybrid_stats_service.HybridStatsService', side_effect=ImportError):
            with patch('src.services.stats_cache_service.StatsCacheService', side_effect=Exception):
                # Act
                response = await handler.get_stats_overview(request)

                # Assert
                assert response.status == 503


class TestVacuumDatabase:
    """Test vacuum_database endpoint."""

    @pytest.mark.asyncio
    async def test_vacuum_success(self, handler, mock_api):
        """Test successful database vacuum."""
        # Arrange
        mock_api.prompt_repo.vacuum = Mock()
        request = Mock()

        # Act
        response = await handler.vacuum_database(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.vacuum.assert_called_once()

    @pytest.mark.asyncio
    async def test_vacuum_error(self, handler, mock_api):
        """Test vacuum error handling."""
        # Arrange
        mock_api.prompt_repo.vacuum = Mock(side_effect=Exception("Vacuum failed"))
        request = Mock()

        # Act
        response = await handler.vacuum_database(request)

        # Assert
        assert response.status == 500


class TestBackupDatabase:
    """Test backup_database endpoint."""

    @pytest.mark.asyncio
    async def test_backup_success_with_path(self, handler, mock_api):
        """Test successful backup with provided path."""
        # Arrange
        mock_api.prompt_repo.backup = Mock(return_value=True)
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/backup/test.db"})

        # Act
        response = await handler.backup_database(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.backup.assert_called_once_with("/backup/test.db")

    @pytest.mark.asyncio
    async def test_backup_success_auto_path(self, handler, mock_api):
        """Test successful backup with auto-generated path."""
        # Arrange
        mock_api.prompt_repo.backup = Mock(return_value=True)
        request = Mock()
        request.json = AsyncMock(return_value={})

        # Act
        response = await handler.backup_database(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.backup.assert_called_once()

    @pytest.mark.asyncio
    async def test_backup_failed(self, handler, mock_api):
        """Test backup failure."""
        # Arrange
        mock_api.prompt_repo.backup = Mock(return_value=False)
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/backup/test.db"})

        # Act
        response = await handler.backup_database(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_backup_error(self, handler, mock_api):
        """Test backup error handling."""
        # Arrange
        mock_api.prompt_repo.backup = Mock(side_effect=Exception("Backup error"))
        request = Mock()
        request.json = AsyncMock(return_value={})

        # Act
        response = await handler.backup_database(request)

        # Assert
        assert response.status == 500


class TestDatabasePathOperations:
    """Test database path verification, application, and migration."""

    @pytest.mark.asyncio
    async def test_verify_path_success(self, handler, mock_api):
        """Test successful path verification."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/data/prompts.db"})

        mock_fs = Mock()
        mock_fs.verify_database_path.return_value = {"resolved": "/data/prompts.db", "exists": True}

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            # Act
            response = await handler.verify_database_path(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_verify_path_missing(self, handler, mock_api):
        """Test verification with missing path."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={})

        # Act
        response = await handler.verify_database_path(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_verify_path_error(self, handler, mock_api):
        """Test verification error handling."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/invalid/path"})

        mock_fs = Mock()
        mock_fs.verify_database_path.side_effect = Exception("Invalid path")

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            # Act
            response = await handler.verify_database_path(request)

            # Assert
            assert response.status == 500

    @pytest.mark.asyncio
    async def test_apply_path_custom(self, handler, mock_api):
        """Test applying custom database path."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/custom/prompts.db"})

        mock_fs = Mock()
        mock_fs.verify_database_path.return_value = {"resolved": "/custom/prompts.db"}
        mock_fs.set_custom_database_path.return_value = Path("/custom/prompts.db")

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            with patch('src.config.config') as mock_config:
                with patch('pathlib.Path.exists', return_value=True):
                    # Act
                    response = await handler.apply_database_path(request)

                    # Assert
                    assert response.status == 200
                    mock_api._refresh_repositories.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_path_reset_to_default(self, handler, mock_api):
        """Test resetting to default database path."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={})

        mock_fs = Mock()
        mock_fs.set_custom_database_path.return_value = Path("/default/prompts.db")

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            with patch('src.config.config'):
                # Act
                response = await handler.apply_database_path(request)

                # Assert
                assert response.status == 200
                mock_api._refresh_repositories.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_path_not_found(self, handler, mock_api):
        """Test applying path that doesn't exist."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/missing/prompts.db"})

        mock_fs = Mock()
        mock_fs.verify_database_path.return_value = {"resolved": "/missing/prompts.db"}

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            with patch('pathlib.Path.exists', return_value=False):
                # Act
                response = await handler.apply_database_path(request)

                # Assert
                assert response.status == 400

    @pytest.mark.asyncio
    async def test_migrate_path_move(self, handler, mock_api):
        """Test migrating database with move."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/new/location", "mode": "move"})

        mock_fs = Mock()
        mock_fs.move_database_file.return_value = {"new_path": "/new/location/prompts.db"}
        mock_fs.get_database_path.return_value = Path("/new/location/prompts.db")

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            with patch('src.config.config'):
                # Act
                response = await handler.migrate_database_path(request)

                # Assert
                assert response.status == 200
                mock_api._refresh_repositories.assert_called_once()

    @pytest.mark.asyncio
    async def test_migrate_path_copy(self, handler, mock_api):
        """Test migrating database with copy."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={"path": "/backup/location", "mode": "copy"})

        mock_fs = Mock()
        mock_fs.move_database_file.return_value = {"new_path": "/backup/location/prompts.db"}

        with patch('src.utils.file_system.get_file_system', return_value=mock_fs):
            with patch('src.config.config'):
                # Act
                response = await handler.migrate_database_path(request)

                # Assert
                assert response.status == 200
                mock_fs.move_database_file.assert_called_once_with("/backup/location", copy=True)

    @pytest.mark.asyncio
    async def test_migrate_path_missing(self, handler, mock_api):
        """Test migration with missing path."""
        # Arrange
        request = Mock()
        request.json = AsyncMock(return_value={})

        # Act
        response = await handler.migrate_database_path(request)

        # Assert
        assert response.status == 400


class TestCategories:
    """Test category endpoints."""

    @pytest.mark.asyncio
    async def test_get_categories_success(self, handler, mock_api):
        """Test successful category retrieval."""
        # Arrange
        mock_api.prompt_repo.get_categories.return_value = ["art", "photography", "3d"]
        request = Mock()

        # Act
        response = await handler.get_categories(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_categories_empty(self, handler, mock_api):
        """Test categories when none exist."""
        # Arrange
        mock_api.prompt_repo.get_categories.return_value = []
        request = Mock()

        # Act
        response = await handler.get_categories(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_categories_error(self, handler, mock_api):
        """Test category error handling."""
        # Arrange
        mock_api.prompt_repo.get_categories.side_effect = Exception("DB error")
        request = Mock()

        # Act
        response = await handler.get_categories(request)

        # Assert
        assert response.status == 500

    @pytest.mark.asyncio
    async def test_get_prompt_categories_alias(self, handler, mock_api):
        """Test prompt categories alias."""
        # Arrange
        mock_api.prompt_repo.get_categories.return_value = ["art"]
        request = Mock()

        # Act
        response = await handler.get_prompt_categories(request)

        # Assert
        assert response.status == 200


class TestRecentAndPopularPrompts:
    """Test recent and popular prompt endpoints."""

    @pytest.mark.asyncio
    async def test_get_recent_prompts_success(self, handler, mock_api):
        """Test successful recent prompts retrieval."""
        # Arrange
        mock_prompts = [{"id": 1, "text": "test"}]
        mock_api.prompt_repo.get_recent.return_value = mock_prompts
        request = Mock()
        request.query = {"limit": "5"}

        # Act
        response = await handler.get_recent_prompts(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.get_recent.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_get_recent_prompts_default_limit(self, handler, mock_api):
        """Test recent prompts with default limit."""
        # Arrange
        mock_api.prompt_repo.get_recent.return_value = []
        request = Mock()
        request.query = {}

        # Act
        response = await handler.get_recent_prompts(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.get_recent.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_get_recent_prompts_with_images(self, handler, mock_api):
        """Test recent prompts including images."""
        # Arrange
        mock_prompts = [{"id": 1}]
        mock_api.prompt_repo.get_recent.return_value = mock_prompts
        request = Mock()
        request.query = {"include_images": "1", "image_limit": "2"}

        # Act
        response = await handler.get_recent_prompts(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_popular_prompts_success(self, handler, mock_api):
        """Test successful popular prompts retrieval."""
        # Arrange
        mock_prompts = [{"id": 2, "usage_count": 10}]
        mock_api.prompt_repo.get_popular.return_value = mock_prompts
        request = Mock()
        request.query = {"limit": "20"}

        # Act
        response = await handler.get_popular_prompts(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.get_popular.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_get_popular_prompts_invalid_limit(self, handler, mock_api):
        """Test popular prompts with invalid limit."""
        # Arrange
        mock_api.prompt_repo.get_popular.return_value = []
        request = Mock()
        request.query = {"limit": "invalid"}

        # Act
        response = await handler.get_popular_prompts(request)

        # Assert
        assert response.status == 200
        mock_api.prompt_repo.get_popular.assert_called_once_with(10)


class TestGetPromptImages:
    """Test get_prompt_images endpoint."""

    @pytest.mark.asyncio
    async def test_get_images_success(self, handler, mock_api):
        """Test successful image retrieval."""
        # Arrange
        mock_images = [{"id": 1, "path": "/img1.png"}]
        mock_api.generated_image_repo.list_for_prompt.return_value = mock_images
        mock_api.generated_image_repo.count.return_value = 10
        request = Mock()
        request.match_info = {"prompt_id": "123"}
        request.query = {"limit": "5", "order": "desc"}

        # Act
        response = await handler.get_prompt_images(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_images_no_repo(self, mock_api):
        """Test images when repo not available."""
        # Arrange
        mock_api.generated_image_repo = None
        handler = SystemHandlers(mock_api)
        request = Mock()

        # Act
        response = await handler.get_prompt_images(request)

        # Assert
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_get_images_invalid_id(self, handler, mock_api):
        """Test images with invalid prompt ID."""
        # Arrange
        request = Mock()
        request.match_info = {"prompt_id": "invalid"}

        # Act
        response = await handler.get_prompt_images(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_get_images_invalid_limit(self, handler, mock_api):
        """Test images with invalid limit."""
        # Arrange
        request = Mock()
        request.match_info = {"prompt_id": "123"}
        request.query = {"limit": "invalid"}

        # Act
        response = await handler.get_prompt_images(request)

        # Assert
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_get_images_error(self, handler, mock_api):
        """Test image retrieval error handling."""
        # Arrange
        mock_api.generated_image_repo.list_for_prompt.side_effect = Exception("DB error")
        request = Mock()
        request.match_info = {"prompt_id": "123"}
        request.query = {}

        # Act
        response = await handler.get_prompt_images(request)

        # Assert
        assert response.status == 500


class TestGetSystemSettings:
    """Test get_system_settings endpoint."""

    @pytest.mark.asyncio
    async def test_get_settings_success(self, handler, mock_api):
        """Test successful settings retrieval."""
        # Arrange
        mock_api._build_settings_payload.return_value = {
            "theme": "dark",
            "features": ["webhooks", "analytics"]
        }
        request = Mock()

        # Act
        response = await handler.get_system_settings(request)

        # Assert
        assert response.status == 200
        mock_api._build_settings_payload.assert_called_once()
