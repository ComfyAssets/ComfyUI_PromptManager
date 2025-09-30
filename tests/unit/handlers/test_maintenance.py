"""Unit tests for maintenance handlers."""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from aiohttp import web

from src.api.handlers.maintenance import MaintenanceHandlers


@pytest.fixture
def mock_api():
    """Mock PromptManagerAPI instance."""
    api = Mock()
    api.logger = Mock()
    api.db_path = "/tmp/test.db"
    api.realtime = Mock()
    api.realtime.send_toast = AsyncMock()
    return api


@pytest.fixture
def handler(mock_api):
    """Create handler instance with mocked API."""
    return MaintenanceHandlers(mock_api)


class TestGetMaintenanceStats:
    """Test get_maintenance_stats endpoint."""

    @pytest.mark.asyncio
    async def test_get_stats_success(self, handler, mock_api):
        """Test successful stats retrieval."""
        # Arrange
        mock_service = Mock()
        mock_service.get_statistics.return_value = {
            "total_prompts": 100,
            "orphaned_images": 5,
            "duplicates": 3
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.get_maintenance_stats(request)

            # Assert
            assert response.status == 200
            mock_service.get_statistics.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_stats_error(self, handler, mock_api):
        """Test stats error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.get_statistics.side_effect = Exception("Stats error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.get_maintenance_stats(request)

            # Assert
            assert response.status == 500


class TestRemoveDuplicates:
    """Test remove_duplicates endpoint."""

    @pytest.mark.asyncio
    async def test_remove_success(self, handler, mock_api):
        """Test successful duplicate removal."""
        # Arrange
        mock_service = Mock()
        mock_service.remove_duplicates.return_value = {
            "success": True,
            "message": "Removed 3 duplicates",
            "removed": 3
        }
        mock_api.sse = True
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.remove_duplicates(request)

            # Assert
            assert response.status == 200
            mock_api.realtime.send_toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_error(self, handler, mock_api):
        """Test duplicate removal error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.remove_duplicates.side_effect = Exception("Remove error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.remove_duplicates(request)

            # Assert
            assert response.status == 500


class TestCleanOrphans:
    """Test clean_orphans endpoint."""

    @pytest.mark.asyncio
    async def test_clean_success(self, handler, mock_api):
        """Test successful orphan cleaning."""
        # Arrange
        mock_service = Mock()
        mock_service.clean_orphans.return_value = {
            "success": True,
            "message": "Cleaned 5 orphans",
            "cleaned": 5
        }
        mock_api.sse = True
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.clean_orphans(request)

            # Assert
            assert response.status == 200
            mock_api.realtime.send_toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_clean_error(self, handler, mock_api):
        """Test orphan cleaning error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.clean_orphans.side_effect = Exception("Clean error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.clean_orphans(request)

            # Assert
            assert response.status == 500


class TestValidatePaths:
    """Test validate_paths endpoint."""

    @pytest.mark.asyncio
    async def test_validate_success(self, handler, mock_api):
        """Test successful path validation."""
        # Arrange
        mock_service = Mock()
        mock_service.validate_paths.return_value = {
            "success": True,
            "message": "Validated 100 paths",
            "valid": 95,
            "invalid": 5
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.validate_paths(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_validate_error(self, handler, mock_api):
        """Test path validation error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.validate_paths.side_effect = Exception("Validation error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.validate_paths(request)

            # Assert
            assert response.status == 500


class TestOptimizeDatabase:
    """Test optimize_database endpoint."""

    @pytest.mark.asyncio
    async def test_optimize_success(self, handler, mock_api):
        """Test successful database optimization."""
        # Arrange
        mock_service = Mock()
        mock_service.optimize_database.return_value = {
            "success": True,
            "message": "Database optimized"
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.optimize_database(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_optimize_error(self, handler, mock_api):
        """Test optimization error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.optimize_database.side_effect = Exception("Optimize error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.optimize_database(request)

            # Assert
            assert response.status == 500


class TestCreateBackup:
    """Test create_backup endpoint."""

    @pytest.mark.asyncio
    async def test_backup_success(self, handler, mock_api):
        """Test successful backup creation."""
        # Arrange
        mock_service = Mock()
        mock_service.create_backup.return_value = {
            "success": True,
            "message": "Backup created",
            "path": "/backups/backup_20250930.db"
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.create_backup(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_backup_error(self, handler, mock_api):
        """Test backup creation error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.create_backup.side_effect = Exception("Backup error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.create_backup(request)

            # Assert
            assert response.status == 500


class TestFixBrokenLinks:
    """Test fix_broken_links endpoint."""

    @pytest.mark.asyncio
    async def test_fix_success(self, handler, mock_api):
        """Test successful link fixing."""
        # Arrange
        mock_service = Mock()
        mock_service.fix_broken_links.return_value = {
            "success": True,
            "message": "Fixed 10 broken links",
            "fixed": 10
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.fix_broken_links(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_fix_error(self, handler, mock_api):
        """Test link fixing error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.fix_broken_links.side_effect = Exception("Fix error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.fix_broken_links(request)

            # Assert
            assert response.status == 500


class TestRemoveMissingFiles:
    """Test remove_missing_files endpoint."""

    @pytest.mark.asyncio
    async def test_remove_success(self, handler, mock_api):
        """Test successful missing file removal."""
        # Arrange
        mock_service = Mock()
        mock_service.remove_missing_files.return_value = {
            "success": True,
            "message": "Removed 8 missing files",
            "removed": 8
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.remove_missing_files(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_remove_error(self, handler, mock_api):
        """Test missing file removal error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.remove_missing_files.side_effect = Exception("Remove error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.remove_missing_files(request)

            # Assert
            assert response.status == 500


class TestRefreshFileMetadata:
    """Test refresh_file_metadata endpoint."""

    @pytest.mark.asyncio
    async def test_refresh_success_default_batch(self, handler, mock_api):
        """Test successful metadata refresh with default batch size."""
        # Arrange
        mock_service = Mock()
        mock_service.refresh_file_metadata.return_value = {
            "success": True,
            "message": "Refreshed 500 files",
            "updated": 500
        }
        request = Mock()
        request.query = {}

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.refresh_file_metadata(request)

            # Assert
            assert response.status == 200
            mock_service.refresh_file_metadata.assert_called_once_with(batch_size=500)

    @pytest.mark.asyncio
    async def test_refresh_success_custom_batch(self, handler, mock_api):
        """Test successful metadata refresh with custom batch size."""
        # Arrange
        mock_service = Mock()
        mock_service.refresh_file_metadata.return_value = {
            "success": True,
            "message": "Refreshed 100 files"
        }
        request = Mock()
        request.query = {"batch_size": "100"}

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.refresh_file_metadata(request)

            # Assert
            assert response.status == 200
            mock_service.refresh_file_metadata.assert_called_once_with(batch_size=100)

    @pytest.mark.asyncio
    async def test_refresh_invalid_batch_size(self, handler, mock_api):
        """Test metadata refresh with invalid batch size."""
        # Arrange
        mock_service = Mock()
        mock_service.refresh_file_metadata.return_value = {"success": True}
        request = Mock()
        request.query = {"batch_size": "invalid"}

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.refresh_file_metadata(request)

            # Assert
            assert response.status == 200
            # Should use default 500
            mock_service.refresh_file_metadata.assert_called_once_with(batch_size=500)

    @pytest.mark.asyncio
    async def test_refresh_error(self, handler, mock_api):
        """Test metadata refresh error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.refresh_file_metadata.side_effect = Exception("Refresh error")
        request = Mock()
        request.query = {}

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.refresh_file_metadata(request)

            # Assert
            assert response.status == 500


class TestCheckIntegrity:
    """Test check_integrity endpoint."""

    @pytest.mark.asyncio
    async def test_check_success(self, handler, mock_api):
        """Test successful integrity check."""
        # Arrange
        mock_service = Mock()
        mock_service.check_integrity.return_value = {
            "success": True,
            "message": "Integrity check passed",
            "issues": []
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.check_integrity(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_check_error(self, handler, mock_api):
        """Test integrity check error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.check_integrity.side_effect = Exception("Check error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.check_integrity(request)

            # Assert
            assert response.status == 500


class TestReindexDatabase:
    """Test reindex_database endpoint."""

    @pytest.mark.asyncio
    async def test_reindex_success(self, handler, mock_api):
        """Test successful database reindexing."""
        # Arrange
        mock_service = Mock()
        mock_service.reindex_database.return_value = {
            "success": True,
            "message": "Database reindexed"
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.reindex_database(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_reindex_error(self, handler, mock_api):
        """Test reindexing error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.reindex_database.side_effect = Exception("Reindex error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.reindex_database(request)

            # Assert
            assert response.status == 500


class TestTagMissingImages:
    """Test tag_missing_images endpoint."""

    @pytest.mark.asyncio
    async def test_tag_success(self, handler, mock_api):
        """Test successful missing image tagging."""
        # Arrange
        mock_tagger = Mock()
        mock_tagger.tag_missing_images.return_value = {
            "tagged": 10,
            "total_missing": 15
        }
        request = Mock()
        request.body_exists = True
        request.json = AsyncMock(return_value={"action": "tag"})

        with patch('src.services.missing_images_tagger.MissingImagesTagger', return_value=mock_tagger):
            # Act
            response = await handler.tag_missing_images(request)

            # Assert
            assert response.status == 200
            mock_tagger.tag_missing_images.assert_called_once()

    @pytest.mark.asyncio
    async def test_untag_success(self, handler, mock_api):
        """Test successful missing image untagging."""
        # Arrange
        mock_tagger = Mock()
        mock_tagger.remove_missing_tag.return_value = {
            "tag_removed": 5
        }
        request = Mock()
        request.body_exists = True
        request.json = AsyncMock(return_value={"action": "untag"})

        with patch('src.services.missing_images_tagger.MissingImagesTagger', return_value=mock_tagger):
            # Act
            response = await handler.tag_missing_images(request)

            # Assert
            assert response.status == 200
            mock_tagger.remove_missing_tag.assert_called_once()

    @pytest.mark.asyncio
    async def test_summary_success(self, handler, mock_api):
        """Test successful missing image summary."""
        # Arrange
        mock_tagger = Mock()
        mock_tagger.get_missing_images_summary.return_value = {
            "total_missing": 15,
            "tagged": 10,
            "untagged": 5
        }
        request = Mock()
        request.body_exists = True
        request.json = AsyncMock(return_value={"action": "summary"})

        with patch('src.services.missing_images_tagger.MissingImagesTagger', return_value=mock_tagger):
            # Act
            response = await handler.tag_missing_images(request)

            # Assert
            assert response.status == 200
            mock_tagger.get_missing_images_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_tag_default_action(self, handler, mock_api):
        """Test missing image tagging with default action."""
        # Arrange
        mock_tagger = Mock()
        mock_tagger.tag_missing_images.return_value = {"tagged": 5, "total_missing": 5}
        request = Mock()
        request.body_exists = False

        with patch('src.services.missing_images_tagger.MissingImagesTagger', return_value=mock_tagger):
            # Act
            response = await handler.tag_missing_images(request)

            # Assert
            assert response.status == 200
            mock_tagger.tag_missing_images.assert_called_once()

    @pytest.mark.asyncio
    async def test_tag_error(self, handler, mock_api):
        """Test missing image tagging error handling."""
        # Arrange
        mock_tagger = Mock()
        mock_tagger.tag_missing_images.side_effect = Exception("Tag error")
        request = Mock()
        request.body_exists = False

        with patch('src.services.missing_images_tagger.MissingImagesTagger', return_value=mock_tagger):
            # Act
            response = await handler.tag_missing_images(request)

            # Assert
            assert response.status == 500


class TestExportBackup:
    """Test export_backup endpoint."""

    @pytest.mark.asyncio
    async def test_export_success(self, handler, mock_api):
        """Test successful backup export."""
        # Arrange
        mock_service = Mock()
        mock_service.export_backup.return_value = {
            "success": True,
            "message": "Backup exported",
            "path": "/exports/backup.zip"
        }
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.export_backup(request)

            # Assert
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_export_error(self, handler, mock_api):
        """Test backup export error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.export_backup.side_effect = Exception("Export error")
        request = Mock()

        with patch('src.services.maintenance_service.MaintenanceService', return_value=mock_service):
            # Act
            response = await handler.export_backup(request)

            # Assert
            assert response.status == 500


class TestCalculateEpicStats:
    """Test calculate_epic_stats endpoint."""

    @pytest.mark.asyncio
    async def test_calculate_success(self, handler, mock_api):
        """Test successful epic stats calculation."""
        # Arrange
        mock_calculator = Mock()
        mock_calculator.calculate_all_stats.return_value = {
            "total_prompts": 1000,
            "top_models": ["model1", "model2"]
        }
        request = Mock()

        with patch('src.services.epic_stats_calculator.EpicStatsCalculator', return_value=mock_calculator):
            # Act
            response = await handler.calculate_epic_stats(request)

            # Assert
            assert response.status == 200
            mock_calculator.calculate_all_stats.assert_called_once_with(progress_callback=None)

    @pytest.mark.asyncio
    async def test_calculate_error(self, handler, mock_api):
        """Test epic stats calculation error handling."""
        # Arrange
        mock_calculator = Mock()
        mock_calculator.calculate_all_stats.side_effect = Exception("Calculation error")
        request = Mock()

        with patch('src.services.epic_stats_calculator.EpicStatsCalculator', return_value=mock_calculator):
            # Act
            response = await handler.calculate_epic_stats(request)

            # Assert
            assert response.status == 500


class TestCalculateWordCloud:
    """Test calculate_word_cloud endpoint."""

    @pytest.mark.asyncio
    async def test_word_cloud_success(self, handler, mock_api):
        """Test successful word cloud calculation."""
        # Arrange
        mock_service = Mock()
        mock_service.calculate_word_frequencies.return_value = {
            "cloud": 100,
            "ai": 85,
            "photo": 60
        }
        mock_service.get_metadata.return_value = {
            "total_words": 1000,
            "unique_words": 250
        }
        request = Mock()

        with patch('src.services.word_cloud_service.WordCloudService', return_value=mock_service):
            # Act
            response = await handler.calculate_word_cloud(request)

            # Assert
            assert response.status == 200
            mock_service.calculate_word_frequencies.assert_called_once_with(limit=100)
            mock_service.get_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_word_cloud_error(self, handler, mock_api):
        """Test word cloud calculation error handling."""
        # Arrange
        mock_service = Mock()
        mock_service.calculate_word_frequencies.side_effect = Exception("Word cloud error")
        request = Mock()

        with patch('src.services.word_cloud_service.WordCloudService', return_value=mock_service):
            # Act
            response = await handler.calculate_word_cloud(request)

            # Assert
            assert response.status == 500
