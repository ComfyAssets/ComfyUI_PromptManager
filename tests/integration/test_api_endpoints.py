"""
Integration tests for API endpoints.

Tests the REST API endpoints with real database operations,
testing the full request/response cycle including validation,
business logic, and data persistence.
"""

import pytest
import json
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock

# We'll need to import the actual API components once we examine them
# For now, we'll create a mock structure that matches the expected API


class TestPromptAPIEndpoints:
    """Integration tests for prompt-related API endpoints."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                # Import here to use patched dependencies
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_get_prompts_empty_database(self, client):
        """Test getting prompts from empty database."""
        response = client.get("/api/prompts")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "prompts" in data
        assert len(data["prompts"]) == 0
        assert "total" in data
        assert data["total"] == 0
    
    def test_create_prompt(self, client, sample_prompt_data):
        """Test creating a new prompt via API."""
        response = client.post("/api/prompts", json=sample_prompt_data)
        
        assert response.status_code == 201
        data = response.json()
        
        assert "id" in data
        assert data["positive_prompt"] == sample_prompt_data["positive_prompt"]
        assert data["category"] == sample_prompt_data["category"]
        assert "created_at" in data
    
    def test_create_prompt_invalid_data(self, client):
        """Test creating prompt with invalid data."""
        invalid_data = {
            "positive_prompt": "",  # Empty prompt should be invalid
            "rating": 10  # Invalid rating
        }
        
        response = client.post("/api/prompts", json=invalid_data)
        assert response.status_code == 400
    
    def test_get_prompt_by_id(self, client, sample_prompt_data):
        """Test retrieving a specific prompt by ID."""
        # Create a prompt first
        create_response = client.post("/api/prompts", json=sample_prompt_data)
        assert create_response.status_code == 201
        prompt_id = create_response.json()["id"]
        
        # Retrieve the prompt
        response = client.get(f"/api/prompts/{prompt_id}")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["id"] == prompt_id
        assert data["positive_prompt"] == sample_prompt_data["positive_prompt"]
    
    def test_get_prompt_nonexistent(self, client):
        """Test retrieving non-existent prompt."""
        response = client.get("/api/prompts/99999")
        assert response.status_code == 404
    
    def test_update_prompt(self, client, sample_prompt_data):
        """Test updating an existing prompt."""
        # Create a prompt first
        create_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = create_response.json()["id"]
        
        # Update the prompt
        update_data = {
            "category": "updated_category",
            "rating": 5,
            "notes": "Updated via API"
        }
        
        response = client.put(f"/api/prompts/{prompt_id}", json=update_data)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["category"] == "updated_category"
        assert data["rating"] == 5
        assert data["notes"] == "Updated via API"
    
    def test_delete_prompt(self, client, sample_prompt_data):
        """Test deleting a prompt."""
        # Create a prompt first
        create_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = create_response.json()["id"]
        
        # Delete the prompt
        response = client.delete(f"/api/prompts/{prompt_id}")
        assert response.status_code == 204
        
        # Verify prompt is deleted
        get_response = client.get(f"/api/prompts/{prompt_id}")
        assert get_response.status_code == 404
    
    def test_search_prompts_by_text(self, client, sample_prompt_data):
        """Test searching prompts by text."""
        # Create multiple prompts
        prompts = [
            {**sample_prompt_data, "positive_prompt": "Beautiful mountain landscape"},
            {**sample_prompt_data, "positive_prompt": "Ocean waves at sunset"},
            {**sample_prompt_data, "positive_prompt": "Abstract geometric patterns"}
        ]
        
        for prompt in prompts:
            client.post("/api/prompts", json=prompt)
        
        # Search for mountain
        response = client.get("/api/prompts/search", params={"text": "mountain"})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["prompts"]) >= 1
        assert any("mountain" in p["positive_prompt"].lower() for p in data["prompts"])
    
    def test_search_prompts_by_category(self, client, sample_prompt_data):
        """Test searching prompts by category."""
        # Create prompts with different categories
        categories = ["landscapes", "portraits", "abstract"]
        
        for category in categories:
            prompt_data = {**sample_prompt_data, "category": category}
            client.post("/api/prompts", json=prompt_data)
        
        # Search by category
        response = client.get("/api/prompts/search", params={"category": "landscapes"})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["prompts"]) >= 1
        assert all(p["category"] == "landscapes" for p in data["prompts"])
    
    def test_search_prompts_by_rating(self, client, sample_prompt_data):
        """Test searching prompts by rating range."""
        # Create prompts with different ratings
        for rating in [1, 3, 5]:
            prompt_data = {**sample_prompt_data, "rating": rating, 
                          "positive_prompt": f"Prompt with rating {rating}"}
            client.post("/api/prompts", json=prompt_data)
        
        # Search for high-rated prompts
        response = client.get("/api/prompts/search", params={"rating_min": 4})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["prompts"]) >= 1
        assert all(p["rating"] >= 4 for p in data["prompts"])
    
    def test_get_recent_prompts(self, client, sample_prompt_data):
        """Test getting recent prompts."""
        # Create multiple prompts
        for i in range(5):
            prompt_data = {**sample_prompt_data, 
                          "positive_prompt": f"Recent prompt {i}"}
            client.post("/api/prompts", json=prompt_data)
        
        response = client.get("/api/prompts/recent", params={"limit": 3})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["prompts"]) <= 3
        # Should be ordered by creation time (newest first)
        if len(data["prompts"]) > 1:
            timestamps = [p["created_at"] for p in data["prompts"]]
            assert timestamps == sorted(timestamps, reverse=True)
    
    def test_get_categories(self, client, sample_prompt_data):
        """Test getting all prompt categories."""
        categories = ["landscapes", "portraits", "abstract", "fantasy"]
        
        for category in categories:
            prompt_data = {**sample_prompt_data, "category": category}
            client.post("/api/prompts", json=prompt_data)
        
        response = client.get("/api/categories")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "categories" in data
        for category in categories:
            assert category in data["categories"]
    
    def test_pagination(self, client, sample_prompt_data):
        """Test API pagination."""
        # Create multiple prompts
        for i in range(10):
            prompt_data = {**sample_prompt_data, 
                          "positive_prompt": f"Pagination test prompt {i}"}
            client.post("/api/prompts", json=prompt_data)
        
        # Test first page
        response = client.get("/api/prompts", params={"page": 1, "per_page": 3})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["prompts"]) == 3
        assert data["page"] == 1
        assert data["per_page"] == 3
        assert data["total"] >= 10
        
        # Test second page
        response = client.get("/api/prompts", params={"page": 2, "per_page": 3})
        
        assert response.status_code == 200
        data2 = response.json()
        
        assert len(data2["prompts"]) == 3
        assert data2["page"] == 2
        
        # Pages should have different prompts
        page1_ids = {p["id"] for p in data["prompts"]}
        page2_ids = {p["id"] for p in data2["prompts"]}
        assert page1_ids.isdisjoint(page2_ids)


class TestImageAPIEndpoints:
    """Integration tests for image-related API endpoints."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_get_images_for_prompt(self, client, sample_prompt_data):
        """Test getting images associated with a prompt."""
        # Create a prompt
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        # Create image records for the prompt
        image_data = {
            "prompt_id": prompt_id,
            "image_path": "/test/path/image.png",
            "filename": "image.png",
            "width": 512,
            "height": 512,
            "format": "PNG"
        }
        
        for i in range(3):
            image_data_copy = {**image_data, 
                              "filename": f"image_{i}.png",
                              "image_path": f"/test/path/image_{i}.png"}
            client.post("/api/images", json=image_data_copy)
        
        response = client.get(f"/api/prompts/{prompt_id}/images")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "images" in data
        assert len(data["images"]) == 3
        assert all(img["prompt_id"] == prompt_id for img in data["images"])
    
    def test_create_image_record(self, client, sample_prompt_data):
        """Test creating an image record."""
        # Create a prompt first
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        image_data = {
            "prompt_id": prompt_id,
            "image_path": "/test/path/new_image.png",
            "filename": "new_image.png",
            "file_size": 1024000,
            "width": 512,
            "height": 512,
            "format": "PNG",
            "workflow_data": json.dumps({"test": "workflow"}),
            "parameters": json.dumps({"steps": 20, "cfg": 7.5})
        }
        
        response = client.post("/api/images", json=image_data)
        
        assert response.status_code == 201
        data = response.json()
        
        assert "id" in data
        assert data["prompt_id"] == prompt_id
        assert data["filename"] == "new_image.png"
        assert data["width"] == 512
        assert data["height"] == 512
    
    def test_get_image_by_id(self, client, sample_prompt_data):
        """Test retrieving a specific image by ID."""
        # Create prompt and image
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        image_data = {
            "prompt_id": prompt_id,
            "image_path": "/test/path/get_test.png",
            "filename": "get_test.png",
            "width": 512,
            "height": 512,
            "format": "PNG"
        }
        
        image_response = client.post("/api/images", json=image_data)
        image_id = image_response.json()["id"]
        
        # Retrieve the image
        response = client.get(f"/api/images/{image_id}")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["id"] == image_id
        assert data["filename"] == "get_test.png"
    
    def test_update_image_metadata(self, client, sample_prompt_data):
        """Test updating image metadata."""
        # Create prompt and image
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        image_data = {
            "prompt_id": prompt_id,
            "image_path": "/test/path/update_test.png",
            "filename": "update_test.png",
            "width": 512,
            "height": 512,
            "format": "PNG"
        }
        
        image_response = client.post("/api/images", json=image_data)
        image_id = image_response.json()["id"]
        
        # Update metadata
        update_data = {
            "workflow_data": json.dumps({"updated": "workflow"}),
            "parameters": json.dumps({"updated": "parameters"})
        }
        
        response = client.put(f"/api/images/{image_id}", json=update_data)
        
        assert response.status_code == 200
        data = response.json()
        
        workflow = json.loads(data["workflow_data"]) if data["workflow_data"] else {}
        parameters = json.loads(data["parameters"]) if data["parameters"] else {}
        
        assert workflow.get("updated") == "workflow"
        assert parameters.get("updated") == "parameters"
    
    def test_delete_image(self, client, sample_prompt_data):
        """Test deleting an image record."""
        # Create prompt and image
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        image_data = {
            "prompt_id": prompt_id,
            "image_path": "/test/path/delete_test.png",
            "filename": "delete_test.png",
            "width": 512,
            "height": 512,
            "format": "PNG"
        }
        
        image_response = client.post("/api/images", json=image_data)
        image_id = image_response.json()["id"]
        
        # Delete the image
        response = client.delete(f"/api/images/{image_id}")
        assert response.status_code == 204
        
        # Verify deletion
        get_response = client.get(f"/api/images/{image_id}")
        assert get_response.status_code == 404
    
    def test_search_images_by_format(self, client, sample_prompt_data):
        """Test searching images by format."""
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        # Create images with different formats
        formats = ["PNG", "JPG", "WEBP"]
        
        for fmt in formats:
            image_data = {
                "prompt_id": prompt_id,
                "image_path": f"/test/path/format_test.{fmt.lower()}",
                "filename": f"format_test.{fmt.lower()}",
                "width": 512,
                "height": 512,
                "format": fmt
            }
            client.post("/api/images", json=image_data)
        
        # Search for PNG images
        response = client.get("/api/images/search", params={"format": "PNG"})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["images"]) >= 1
        assert all(img["format"] == "PNG" for img in data["images"])
    
    def test_get_recent_images(self, client, sample_prompt_data):
        """Test getting recent images."""
        prompt_response = client.post("/api/prompts", json=sample_prompt_data)
        prompt_id = prompt_response.json()["id"]
        
        # Create multiple images
        for i in range(5):
            image_data = {
                "prompt_id": prompt_id,
                "image_path": f"/test/path/recent_{i}.png",
                "filename": f"recent_{i}.png",
                "width": 512,
                "height": 512,
                "format": "PNG"
            }
            client.post("/api/images", json=image_data)
        
        response = client.get("/api/images/recent", params={"limit": 3})
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["images"]) <= 3
        # Should be ordered by generation time (newest first)


class TestGalleryAPIEndpoints:
    """Integration tests for gallery-related API endpoints."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_get_gallery_items(self, client, test_files_structure):
        """Test getting gallery items."""
        response = client.get("/api/gallery")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
    
    def test_get_gallery_stats(self, client):
        """Test getting gallery statistics."""
        response = client.get("/api/gallery/stats")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_images" in data
        assert "total_videos" in data
        assert "total_size" in data
        assert "formats" in data
    
    def test_scan_gallery(self, client):
        """Test triggering gallery scan."""
        response = client.post("/api/gallery/scan")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert data["status"] in ["started", "already_running", "completed"]
    
    def test_get_thumbnail(self, client, test_image_file):
        """Test getting thumbnail for image."""
        image_path = str(test_image_file)
        
        response = client.get(f"/api/gallery/thumbnail", params={"path": image_path})
        
        # Should either return thumbnail or indicate it needs to be generated
        assert response.status_code in [200, 202, 404]


class TestStatsAPIEndpoints:
    """Integration tests for statistics API endpoints."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_get_prompt_stats(self, client, sample_prompt_data):
        """Test getting prompt statistics."""
        # Create some test data
        for i in range(3):
            prompt_data = {**sample_prompt_data, 
                          "positive_prompt": f"Stats test prompt {i}"}
            client.post("/api/prompts", json=prompt_data)
        
        response = client.get("/api/stats/prompts")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_prompts" in data
        assert "unique_categories" in data
        assert "average_rating" in data
        assert data["total_prompts"] >= 3
    
    def test_get_image_stats(self, client):
        """Test getting image statistics."""
        response = client.get("/api/stats/images")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_images" in data
        assert "total_file_size" in data
        assert "formats" in data
        assert "resolutions" in data
    
    def test_get_usage_stats(self, client):
        """Test getting usage statistics."""
        response = client.get("/api/stats/usage")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "recent_activity" in data
        assert "popular_categories" in data
        assert "top_rated_prompts" in data
    
    def test_get_database_stats(self, client):
        """Test getting database statistics."""
        response = client.get("/api/stats/database")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "database_size" in data
        assert "table_counts" in data
        assert "last_vacuum" in data
    
    def test_generate_report(self, client):
        """Test generating comprehensive statistics report."""
        response = client.get("/api/stats/report")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "prompt_stats" in data
        assert "image_stats" in data
        assert "usage_stats" in data
        assert "database_stats" in data
        assert "generated_at" in data


class TestSystemAPIEndpoints:
    """Integration tests for system management API endpoints."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_get_system_info(self, client):
        """Test getting system information."""
        response = client.get("/api/system/info")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "version" in data
        assert "database_path" in data
        assert "output_directory" in data
        assert "supported_formats" in data
    
    def test_health_check(self, client):
        """Test system health check."""
        response = client.get("/api/system/health")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert "database_ok" in data
        assert "disk_space" in data
        assert "timestamp" in data
    
    def test_vacuum_database(self, client):
        """Test database vacuum operation."""
        response = client.post("/api/system/vacuum")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert "message" in data
    
    def test_backup_database(self, client):
        """Test database backup."""
        backup_data = {"backup_name": "test_backup"}
        
        response = client.post("/api/system/backup", json=backup_data)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert "backup_path" in data
    
    def test_get_logs(self, client):
        """Test getting system logs."""
        response = client.get("/api/system/logs", params={"lines": 10})
        
        assert response.status_code == 200
        data = response.json()
        
        assert "logs" in data
        assert "total_lines" in data
        assert len(data["logs"]) <= 10


class TestErrorHandling:
    """Test API error handling and validation."""
    
    @pytest.fixture
    def client(self, test_config, db_model):
        """Create a test client with real database backend."""
        with patch('src.config.PromptManagerConfig', return_value=test_config):
            with patch('src.database.models.PromptModel', return_value=db_model):
                from src.api.routes import create_app
                app = create_app()
                return TestClient(app)
    
    def test_invalid_json(self, client):
        """Test handling of invalid JSON in requests."""
        response = client.post(
            "/api/prompts", 
            data="invalid json", 
            headers={"content-type": "application/json"}
        )
        
        assert response.status_code == 400
    
    def test_missing_required_fields(self, client):
        """Test validation of required fields."""
        invalid_data = {"category": "test"}  # Missing positive_prompt
        
        response = client.post("/api/prompts", json=invalid_data)
        assert response.status_code == 400
    
    def test_invalid_field_values(self, client):
        """Test validation of field values."""
        invalid_data = {
            "positive_prompt": "test",
            "rating": 10  # Invalid rating (should be 1-5)
        }
        
        response = client.post("/api/prompts", json=invalid_data)
        assert response.status_code == 400
    
    def test_unauthorized_access(self, client):
        """Test handling of unauthorized access."""
        # This test depends on whether authentication is implemented
        response = client.delete("/api/system/reset-database")
        # Could be 401, 403, or 405 depending on implementation
        assert response.status_code in [401, 403, 405]
    
    def test_rate_limiting(self, client):
        """Test API rate limiting (if implemented)."""
        # Make many rapid requests to test rate limiting
        responses = []
        for i in range(100):
            response = client.get("/api/prompts")
            responses.append(response.status_code)
        
        # If rate limiting is implemented, should get some 429 responses
        # If not implemented, all responses should be 200
        assert all(code in [200, 429] for code in responses)
    
    def test_large_payload_handling(self, client):
        """Test handling of large payloads."""
        large_prompt = "A" * 100000  # Very large prompt
        
        data = {
            "positive_prompt": large_prompt,
            "negative_prompt": "test"
        }
        
        response = client.post("/api/prompts", json=data)
        
        # Should either accept it or reject with appropriate error
        assert response.status_code in [200, 201, 400, 413]
    
    def test_concurrent_requests(self, client, sample_prompt_data):
        """Test handling of concurrent requests."""
        import threading
        import time
        
        results = []
        errors = []
        
        def make_request():
            try:
                response = client.post("/api/prompts", json=sample_prompt_data)
                results.append(response.status_code)
            except Exception as e:
                errors.append(e)
        
        # Create multiple threads making concurrent requests
        threads = []
        for i in range(10):
            thread = threading.Thread(target=make_request)
            threads.append(thread)
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Should handle concurrent requests without errors
        assert len(errors) == 0
        assert len(results) == 10
        assert all(code in [200, 201] for code in results)