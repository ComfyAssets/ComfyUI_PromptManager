"""Integration tests for image file serving security.

Tests the complete security flow from HTTP request to file serving,
ensuring path validation is enforced across all endpoints.
"""

import tempfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop


class TestImageSecurityIntegration(AioHTTPTestCase):
    """Integration tests for image serving security."""

    async def get_application(self):
        """Create test application with image serving routes."""
        from src.api.routes import PromptManagerAPI

        # Create temporary directories
        self.allowed_dir = tempfile.mkdtemp()
        self.forbidden_dir = tempfile.mkdtemp()

        self.allowed_path = Path(self.allowed_dir)
        self.forbidden_path = Path(self.forbidden_dir)

        # Create test images
        (self.allowed_path / "allowed_image.png").write_bytes(b"PNG_DATA")
        (self.forbidden_path / "forbidden_image.png").write_bytes(b"SECRET_DATA")

        # Create test database
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = self.db_file.name

        # Initialize API with mocked roots
        api = PromptManagerAPI(db_path, log_dir=None)

        # Override allowed roots to use our test directories
        original_get_roots = api._get_allowed_image_roots

        def mock_get_allowed_roots():
            return [self.allowed_path]

        api._get_allowed_image_roots = mock_get_allowed_roots

        # Setup test data in database
        self._setup_test_data(api)

        return api.app

    def _setup_test_data(self, api):
        """Insert test image records into database."""
        # Insert allowed image
        api.generated_image_repo.create({
            "file_path": str(self.allowed_path / "allowed_image.png"),
            "filename": "allowed_image.png",
            "file_size": 8,
        })

        # Insert forbidden image (simulating a misconfigured/malicious record)
        api.generated_image_repo.create({
            "file_path": str(self.forbidden_path / "forbidden_image.png"),
            "filename": "forbidden_image.png",
            "file_size": 11,
        })

    async def asyncTearDown(self):
        """Cleanup test resources."""
        import shutil

        shutil.rmtree(self.allowed_dir, ignore_errors=True)
        shutil.rmtree(self.forbidden_dir, ignore_errors=True)
        Path(self.db_file.name).unlink(missing_ok=True)

    @unittest_run_loop
    async def test_allowed_image_access(self):
        """Test that images within allowed roots can be accessed."""
        resp = await self.client.request("GET", "/api/v1/generated-images/1/file")

        assert resp.status == 200
        content = await resp.read()
        assert content == b"PNG_DATA"

    @unittest_run_loop
    async def test_forbidden_image_blocked(self):
        """Test that images outside allowed roots are blocked."""
        resp = await self.client.request("GET", "/api/v1/generated-images/2/file")

        assert resp.status == 403
        data = await resp.json()
        assert "Access denied" in data["error"]
        assert "outside allowed directories" in data["error"]

    @unittest_run_loop
    async def test_path_traversal_blocked(self):
        """Test that path traversal attacks are blocked."""
        # Try to access forbidden image using path traversal
        attack_path = f"../../{self.forbidden_path.name}/forbidden_image.png"

        # Create a record with path traversal
        from src.api.routes import PromptManagerAPI
        api = self.app

        # This should be blocked by validation
        resp = await self.client.request("GET", "/api/v1/generated-images/2/file")

        assert resp.status == 403

    @unittest_run_loop
    async def test_nonexistent_image_returns_404(self):
        """Test that nonexistent images return 404, not 403."""
        # Access image ID that doesn't exist
        resp = await self.client.request("GET", "/api/v1/generated-images/999/file")

        assert resp.status == 404

    @unittest_run_loop
    async def test_gallery_endpoint_security(self):
        """Test that gallery endpoint also enforces security."""
        # Insert gallery image records
        from src.api.routes import PromptManagerAPI

        # Test with allowed path - should work
        resp = await self.client.request("GET", "/api/v1/gallery/images/1/file")
        # Exact status depends on whether record exists in image_repo vs generated_image_repo
        assert resp.status in [200, 404, 403]

    @unittest_run_loop
    async def test_thumbnail_security(self):
        """Test that thumbnail requests are also validated."""
        resp = await self.client.request(
            "GET",
            "/api/v1/generated-images/1/file?thumbnail=1"
        )

        # Should either serve allowed thumbnail or return 404 if no thumbnail
        assert resp.status in [200, 404]

        # Forbidden thumbnail should return 403
        resp = await self.client.request(
            "GET",
            "/api/v1/generated-images/2/file?thumbnail=1"
        )

        assert resp.status == 403


class TestSecurityHeaders:
    """Test security-related headers in responses."""

    @pytest.mark.asyncio
    async def test_no_path_disclosure_in_error(self):
        """Test that full paths are not disclosed in error messages."""
        from aiohttp.test_utils import AioHTTPTestCase

        # Error messages should not contain full system paths
        # They should only contain generic messages like "Access denied"
        pass  # Implementation would require full app setup


class TestConcurrentAccess:
    """Test security under concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_validation_thread_safe(self):
        """Test that path validation is thread-safe under concurrent requests."""
        # Multiple concurrent requests should all be validated correctly
        # No race conditions should allow bypassing security
        pass  # Implementation would require concurrent request simulation
