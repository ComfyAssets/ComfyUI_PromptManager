"""Unit tests for image path security validation.

Tests the centralized _validate_image_path() method and its application
across all file serving endpoints to prevent path traversal attacks.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.api.routes import PromptManagerAPI


class TestPathValidation:
    """Test the _validate_image_path() security method."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        with tempfile.TemporaryDirectory() as allowed_dir, \
             tempfile.TemporaryDirectory() as forbidden_dir:
            allowed_path = Path(allowed_dir)
            forbidden_path = Path(forbidden_dir)

            # Create test files
            (allowed_path / "test_image.png").write_text("test")
            (forbidden_path / "secret.png").write_text("secret")

            yield allowed_path, forbidden_path

    @pytest.fixture
    def mock_api(self, temp_dirs):
        """Create a mock API instance with controlled allowed roots."""
        allowed_path, _ = temp_dirs

        api = Mock(spec=PromptManagerAPI)
        api.logger = Mock()

        # Bind the actual _validate_image_path method
        api._validate_image_path = PromptManagerAPI._validate_image_path.__get__(api, PromptManagerAPI)

        # Mock _get_allowed_image_roots to return our allowed dir
        api._get_allowed_image_roots = Mock(return_value=[allowed_path])

        return api

    def test_validate_allowed_path(self, mock_api, temp_dirs):
        """Test that paths within allowed roots are accepted."""
        allowed_path, _ = temp_dirs
        test_file = allowed_path / "test_image.png"

        is_valid, error_msg = mock_api._validate_image_path(test_file)

        assert is_valid is True
        assert error_msg is None

    def test_validate_forbidden_path(self, mock_api, temp_dirs):
        """Test that paths outside allowed roots are rejected."""
        _, forbidden_path = temp_dirs
        secret_file = forbidden_path / "secret.png"

        is_valid, error_msg = mock_api._validate_image_path(secret_file)

        assert is_valid is False
        assert "Access denied" in error_msg
        assert "outside allowed directories" in error_msg

    def test_validate_nonexistent_file(self, mock_api, temp_dirs):
        """Test that nonexistent files return appropriate error."""
        allowed_path, _ = temp_dirs
        nonexistent = allowed_path / "does_not_exist.png"

        is_valid, error_msg = mock_api._validate_image_path(nonexistent)

        assert is_valid is False
        assert "not found" in error_msg.lower()

    def test_validate_path_traversal_attack(self, mock_api, temp_dirs):
        """Test that path traversal attempts are blocked."""
        allowed_path, forbidden_path = temp_dirs

        # Try to escape using ../
        attack_path = allowed_path / f"../{forbidden_path.name}/secret.png"

        is_valid, error_msg = mock_api._validate_image_path(attack_path)

        # Should be rejected because resolved path is outside allowed roots
        assert is_valid is False
        assert "Access denied" in error_msg

    def test_validate_symlink_escape(self, mock_api, temp_dirs):
        """Test that symlinks escaping allowed roots are blocked."""
        allowed_path, forbidden_path = temp_dirs
        secret_file = forbidden_path / "secret.png"

        # Create symlink in allowed dir pointing to forbidden dir
        symlink = allowed_path / "link_to_secret.png"
        symlink.symlink_to(secret_file)

        is_valid, error_msg = mock_api._validate_image_path(symlink)

        # Should be rejected because resolved path is outside allowed roots
        assert is_valid is False
        assert "Access denied" in error_msg

    def test_validate_no_allowed_roots(self, mock_api, temp_dirs):
        """Test behavior when no allowed roots are configured."""
        allowed_path, _ = temp_dirs
        test_file = allowed_path / "test_image.png"

        # Mock no allowed roots
        mock_api._get_allowed_image_roots = Mock(return_value=[])

        is_valid, error_msg = mock_api._validate_image_path(test_file)

        # Should allow with warning (backward compatibility)
        assert is_valid is True
        assert error_msg is None
        mock_api.logger.warning.assert_called_once()

    def test_validate_subdirectory_allowed(self, mock_api, temp_dirs):
        """Test that files in subdirectories of allowed roots are accepted."""
        allowed_path, _ = temp_dirs

        # Create subdirectory
        subdir = allowed_path / "subfolder" / "deep"
        subdir.mkdir(parents=True)
        test_file = subdir / "image.png"
        test_file.write_text("test")

        is_valid, error_msg = mock_api._validate_image_path(test_file)

        assert is_valid is True
        assert error_msg is None

    def test_validate_invalid_path_type(self, mock_api):
        """Test handling of invalid path types."""
        # Test with string instead of Path
        with pytest.raises(AttributeError):
            mock_api._validate_image_path("/tmp/test.png")

    def test_validate_resolution_error(self, mock_api, temp_dirs):
        """Test handling of path resolution errors."""
        allowed_path, _ = temp_dirs

        # Create a path that will cause resolution error
        with patch.object(Path, 'resolve', side_effect=PermissionError("Access denied")):
            test_file = allowed_path / "test_image.png"

            is_valid, error_msg = mock_api._validate_image_path(test_file)

            assert is_valid is False
            assert "Invalid image path" in error_msg
            mock_api.logger.error.assert_called_once()


class TestEndpointSecurity:
    """Test that all file serving endpoints use path validation."""

    @pytest.fixture
    def mock_handler(self):
        """Create a mock handler with API instance."""
        from src.api.handlers.gallery import GalleryHandlers

        api = Mock(spec=PromptManagerAPI)
        api.logger = Mock()
        api._validate_image_path = Mock(return_value=(True, None))

        # Create mock repositories
        api.image_repo = Mock()
        api.generated_image_repo = Mock()
        api.prompt_repo = Mock()
        api.gallery = Mock()
        api.metadata_extractor = Mock()

        handler = GalleryHandlers(api)
        return handler

    @pytest.mark.asyncio
    async def test_get_generated_image_file_validates_path(self, mock_handler):
        """Test that get_generated_image_file() calls path validation."""
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        # Setup mock record
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.png"
            test_file.write_text("test")

            mock_handler.generated_image_repo.read = Mock(return_value={
                "file_path": str(test_file)
            })

            # Create mock request
            request = make_mocked_request('GET', '/api/v1/generated-images/1/file')
            request.match_info = {'image_id': '1'}

            # Call endpoint
            response = await mock_handler.get_generated_image_file(request)

            # Verify validation was called
            mock_handler.api._validate_image_path.assert_called_once()
            called_path = mock_handler.api._validate_image_path.call_args[0][0]
            assert called_path == test_file

    @pytest.mark.asyncio
    async def test_get_generated_image_file_rejects_invalid_path(self, mock_handler):
        """Test that get_generated_image_file() rejects invalid paths."""
        from aiohttp.test_utils import make_mocked_request

        # Setup validation to fail
        mock_handler.api._validate_image_path = Mock(
            return_value=(False, "Access denied: path outside allowed directories")
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.png"
            test_file.write_text("test")

            mock_handler.generated_image_repo.read = Mock(return_value={
                "file_path": str(test_file)
            })

            request = make_mocked_request('GET', '/api/v1/generated-images/1/file')
            request.match_info = {'image_id': '1'}

            response = await mock_handler.get_generated_image_file(request)

            # Should return 403
            assert response.status == 403
            body = await response.json()
            assert "Access denied" in body["error"]

    @pytest.mark.asyncio
    async def test_get_gallery_image_file_validates_path(self, mock_handler):
        """Test that get_gallery_image_file() calls path validation."""
        from aiohttp.test_utils import make_mocked_request

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.png"
            test_file.write_text("test")

            mock_handler.image_repo.read = Mock(return_value={
                "file_path": str(test_file)
            })

            request = make_mocked_request('GET', '/api/v1/gallery/images/1/file')
            request.match_info = {'id': '1'}

            response = await mock_handler.get_gallery_image_file(request)

            # Verify validation was called
            mock_handler.api._validate_image_path.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_image_file_validates_thumbnail_path(self, mock_handler):
        """Test that thumbnail paths are also validated."""
        from aiohttp.test_utils import make_mocked_request

        with tempfile.TemporaryDirectory() as tmpdir:
            thumb_file = Path(tmpdir) / "thumb.png"
            thumb_file.write_text("thumb")

            mock_handler.image_repo.read = Mock(return_value={
                "thumbnail_path": str(thumb_file)
            })

            request = make_mocked_request('GET', '/api/v1/gallery/images/1/file?thumbnail=1')
            request.match_info = {'id': '1'}

            response = await mock_handler.get_gallery_image_file(request)

            # Verify validation was called for thumbnail
            mock_handler.api._validate_image_path.assert_called_once()
            called_path = mock_handler.api._validate_image_path.call_args[0][0]
            assert called_path == thumb_file


class TestSecurityLogging:
    """Test that security violations are properly logged."""

    @pytest.fixture
    def api_with_roots(self):
        """Create API instance with actual validation logic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir) / "allowed"
            allowed.mkdir()

            api = Mock(spec=PromptManagerAPI)
            api.logger = Mock()
            api._get_allowed_image_roots = Mock(return_value=[allowed])
            api._validate_image_path = PromptManagerAPI._validate_image_path.__get__(api, PromptManagerAPI)

            yield api, allowed

    def test_security_violation_logged(self, api_with_roots):
        """Test that security violations are logged with appropriate level."""
        api, allowed = api_with_roots

        # Try to access path outside allowed roots
        forbidden = Path("/tmp/forbidden/secret.png")

        # Create the file so it exists (to pass FileNotFoundError check)
        forbidden.parent.mkdir(parents=True, exist_ok=True)
        forbidden.write_text("secret")

        try:
            is_valid, error_msg = api._validate_image_path(forbidden)

            assert is_valid is False
            # Verify warning was logged
            api.logger.warning.assert_called()
            log_message = api.logger.warning.call_args[0][0]
            assert "Security" in log_message
            assert "Access denied" in log_message
        finally:
            # Cleanup
            if forbidden.exists():
                forbidden.unlink()
            if forbidden.parent.exists():
                forbidden.parent.rmdir()

    def test_no_roots_warning_logged(self, api_with_roots):
        """Test that missing roots configuration triggers warning."""
        api, _ = api_with_roots

        # Mock no allowed roots
        api._get_allowed_image_roots = Mock(return_value=[])

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            test_file = Path(tmp.name)

            is_valid, error_msg = api._validate_image_path(test_file)

            assert is_valid is True
            # Verify warning was logged
            api.logger.warning.assert_called()
            log_message = api.logger.warning.call_args[0][0]
            assert "No allowed image roots configured" in log_message
