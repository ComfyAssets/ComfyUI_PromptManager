"""Image API endpoints.

This module implements REST API endpoints for image management,
extending the BaseController with image-specific operations.
"""

import os
from typing import Any, Dict, List, Tuple
from datetime import datetime

from ..base_controller import BaseController
from ..middleware import (
    RateLimiter,
    ValidationMiddleware,
    CacheMiddleware
)
from ..services.image_service import ImageService

try:  # pragma: no cover - metadata import path differs in tests
    from promptmanager.utils.metadata_extractor import MetadataExtractor  # type: ignore
except ImportError:  # pragma: no cover
    from utils.metadata_extractor import MetadataExtractor  # type: ignore

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.api.endpoints.image")


class ImageEndpoints(BaseController):
    """API endpoints for image management."""
    
    def __init__(self, service: ImageService = None):
        """Initialize image endpoints.
        
        Args:
            service: ImageService instance
        """
        if service is None:
            service = ImageService()
        super().__init__(service)
        
        # Endpoint-specific settings
        self.rate_limiter = RateLimiter(requests_per_minute=60)
        self.cache = CacheMiddleware(ttl=300)  # 5 minute cache for images
    
    # Standard CRUD endpoints
    
    def list_images(self, request) -> Tuple[Dict[str, Any], int]:
        """List images with pagination and filtering.
        
        GET /api/images
        Query params: page, per_page, sort_by, sort_desc, checkpoint, start_date, end_date
        """
        # Parse parameters
        page, per_page = self.validate_pagination(
            request.args.get("page", 1),
            request.args.get("per_page", 20)
        )
        
        sort_by = self.validate_sort(
            request.args.get("sort_by"),
            ["created_at", "file_size", "width", "height"]
        )
        
        sort_desc = request.args.get("sort_desc", "true").lower() == "true"
        
        # Parse filters
        filters = self.parse_filters(request.args, [
            "checkpoint", "start_date", "end_date", "min_width", "min_height"
        ])
        
        # Get images
        return self.list_items(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_desc=sort_desc,
            **filters
        )
    
    def get_image(self, image_id: int) -> Tuple[Dict[str, Any], int]:
        """Get single image metadata by ID.
        
        GET /api/images/{id}
        """
        return self.get_item(image_id)
    
    def get_image_file(self, image_id: int) -> Tuple[Any, int]:
        """Get actual image file.
        
        GET /api/images/{id}/file
        """
        image = self.service.get(image_id)
        
        if not image:
            return self.error_response(
                f"Image not found: {image_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        file_path = image.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return self.error_response(
                "Image file not found",
                status=self.HTTP_NOT_FOUND
            )
        
        # Return file path for serving (actual implementation would stream file)
        return self.success_response({
            "file_path": file_path,
            "content_type": f"image/{image.get('format', 'png').lower()}"
        })
    
    def get_image_thumbnail(self, image_id: int) -> Tuple[Any, int]:
        """Get image thumbnail.
        
        GET /api/images/{id}/thumbnail
        """
        image = self.service.get(image_id)
        
        if not image:
            return self.error_response(
                f"Image not found: {image_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        thumbnail_path = image.get("thumbnail_path")
        
        if not thumbnail_path:
            # Generate thumbnail if needed
            self.service.generate_thumbnail(image_id)
            image = self.service.get(image_id)
            thumbnail_path = image.get("thumbnail_path")
        
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            # Fall back to full image
            return self.get_image_file(image_id)
        
        return self.success_response({
            "file_path": thumbnail_path,
            "content_type": f"image/{image.get('format', 'png').lower()}"
        })
    
    @ValidationMiddleware.validate_content_type("application/json")
    def create_image(self, request) -> Tuple[Dict[str, Any], int]:
        """Create new image record.
        
        POST /api/images
        Body: {file_path, width, height, checkpoint?, prompt_text?, ...}
        """
        data = self.parse_json_body(request.body)
        
        # Validate required fields
        required = ["file_path", "width", "height"]
        missing = [f for f in required if f not in data]
        
        if missing:
            return self.error_response(
                f"Missing required fields: {', '.join(missing)}",
                status=self.HTTP_BAD_REQUEST
            )
        
        return self.create_item(data)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def update_image(self, image_id: int, request) -> Tuple[Dict[str, Any], int]:
        """Update image metadata.
        
        PUT /api/images/{id}
        Body: {checkpoint?, prompt_text?, negative_prompt?, ...}
        """
        data = self.parse_json_body(request.body)
        return self.update_item(image_id, data)
    
    def delete_image(self, image_id: int) -> Tuple[Dict[str, Any], int]:
        """Delete image record (not the file).
        
        DELETE /api/images/{id}
        """
        return self.delete_item(image_id)
    
    # Image-specific endpoints
    
    def get_images_by_prompt(self, prompt_id: int) -> Tuple[Dict[str, Any], int]:
        """Get images generated from a specific prompt.
        
        GET /api/images/prompt/{prompt_id}
        """
        images = self.service.get_by_prompt(prompt_id)
        return self.success_response(images)
    
    def get_images_by_checkpoint(self, checkpoint: str) -> Tuple[Dict[str, Any], int]:
        """Get images by checkpoint model.
        
        GET /api/images/checkpoint/{checkpoint}
        """
        images = self.service.get_by_checkpoint(checkpoint)
        return self.success_response(images)
    
    def get_checkpoints(self, request) -> Tuple[Dict[str, Any], int]:
        """Get list of unique checkpoints.
        
        GET /api/images/checkpoints
        """
        checkpoints = self.service.get_unique_checkpoints()
        return self.success_response({
            "checkpoints": checkpoints,
            "count": len(checkpoints)
        })
    
    def get_image_statistics(self, request) -> Tuple[Dict[str, Any], int]:
        """Get image collection statistics.
        
        GET /api/images/statistics
        """
        stats = self.service.get_statistics()
        return self.success_response(stats)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def extract_metadata(self, request) -> Tuple[Dict[str, Any], int]:
        """Extract metadata from PNG file.
        
        POST /api/images/extract-metadata
        Body: {file_path}
        """
        data = self.parse_json_body(request.body)
        
        if "file_path" not in data:
            return self.error_response(
                "File path required",
                status=self.HTTP_BAD_REQUEST
            )
        
        file_path = data["file_path"]
        
        if not os.path.exists(file_path):
            return self.error_response(
                "File not found",
                status=self.HTTP_NOT_FOUND
            )
        
        metadata = MetadataExtractor.extract_from_file(file_path)
        
        # Also extract generation info
        gen_info = MetadataExtractor.extract_generation_info(file_path)
        
        return self.success_response({
            "metadata": metadata,
            "generation": gen_info
        })
    
    @ValidationMiddleware.validate_content_type("application/json")
    def regenerate_image(self, image_id: int, request) -> Tuple[Dict[str, Any], int]:
        """Regenerate image with same settings.
        
        POST /api/images/{id}/regenerate
        Body: {seed?: override seed}
        """
        image = self.service.get(image_id)
        
        if not image:
            return self.error_response(
                f"Image not found: {image_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        data = self.parse_json_body(request.body)
        
        # Override seed if provided
        if "seed" in data:
            image["seed"] = data["seed"]
        
        # Trigger regeneration (placeholder)
        new_image = self.service.regenerate(image)
        
        if new_image:
            return self.success_response(new_image, status=self.HTTP_CREATED)
        else:
            return self.error_response(
                "Regeneration not available",
                status=self.HTTP_NOT_IMPLEMENTED
            )
    
    @ValidationMiddleware.validate_content_type("application/json")
    def compare_images(self, request) -> Tuple[Dict[str, Any], int]:
        """Compare multiple images.
        
        POST /api/images/compare
        Body: {image_ids: [1, 2, 3]}
        """
        data = self.parse_json_body(request.body)
        
        if "image_ids" not in data or not isinstance(data["image_ids"], list):
            return self.error_response(
                "image_ids array required",
                status=self.HTTP_BAD_REQUEST
            )
        
        if len(data["image_ids"]) < 2:
            return self.error_response(
                "At least 2 images required for comparison",
                status=self.HTTP_BAD_REQUEST
            )
        
        images = []
        for img_id in data["image_ids"]:
            image = self.service.get(img_id)
            if image:
                images.append(image)
        
        if len(images) < 2:
            return self.error_response(
                "Not enough valid images found",
                status=self.HTTP_NOT_FOUND
            )
        
        return self.success_response({
            "images": images,
            "count": len(images)
        })
    
    def cleanup_orphaned(self, request) -> Tuple[Dict[str, Any], int]:
        """Clean up orphaned image records.
        
        POST /api/images/cleanup
        """
        count = self.service.cleanup_orphaned()
        
        return self.success_response({
            "cleaned": count
        }, message=f"Cleaned {count} orphaned records")
    
    @ValidationMiddleware.validate_content_type("application/json")
    def bulk_update_checkpoint(self, request) -> Tuple[Dict[str, Any], int]:
        """Update checkpoint for multiple images.
        
        POST /api/images/bulk-checkpoint
        Body: {image_ids: [], checkpoint: "model_name"}
        """
        data = self.parse_json_body(request.body)
        
        if "image_ids" not in data or "checkpoint" not in data:
            return self.error_response(
                "image_ids and checkpoint required",
                status=self.HTTP_BAD_REQUEST
            )
        
        count = 0
        for img_id in data["image_ids"]:
            if self.service.update(img_id, {"checkpoint": data["checkpoint"]}):
                count += 1
        
        return self.success_response({
            "updated": count,
            "total": len(data["image_ids"])
        })
    
    @ValidationMiddleware.validate_content_type("multipart/form-data")
    def upload_image(self, request) -> Tuple[Dict[str, Any], int]:
        """Upload and register new image.
        
        POST /api/images/upload
        Form data: file, checkpoint?, prompt_text?, ...
        """
        # This would handle file upload
        # Placeholder for now
        return self.error_response(
            "Upload not implemented",
            status=self.HTTP_NOT_IMPLEMENTED
        )
    
    def download_image(self, image_id: int) -> Tuple[Dict[str, Any], int]:
        """Download image file.
        
        GET /api/images/{id}/download
        """
        image = self.service.get(image_id)
        
        if not image:
            return self.error_response(
                f"Image not found: {image_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        file_path = image.get("file_path")
        
        if not file_path or not os.path.exists(file_path):
            return self.error_response(
                "Image file not found",
                status=self.HTTP_NOT_FOUND
            )
        
        # Return download info
        return self.success_response({
            "file_path": file_path,
            "filename": image.get("filename", "image.png"),
            "content_type": f"image/{image.get('format', 'png').lower()}",
            "content_disposition": f"attachment; filename=\"{image.get('filename', 'image.png')}\""
        })
    
    @ValidationMiddleware.validate_content_type("application/json")
    def batch_download(self, request) -> Tuple[Dict[str, Any], int]:
        """Prepare batch download of multiple images.
        
        POST /api/images/batch-download
        Body: {image_ids: [1, 2, 3]}
        """
        data = self.parse_json_body(request.body)
        
        if "image_ids" not in data or not isinstance(data["image_ids"], list):
            return self.error_response(
                "image_ids array required",
                status=self.HTTP_BAD_REQUEST
            )
        
        files = []
        for img_id in data["image_ids"]:
            image = self.service.get(img_id)
            if image and image.get("file_path"):
                if os.path.exists(image["file_path"]):
                    files.append({
                        "id": img_id,
                        "path": image["file_path"],
                        "filename": image.get("filename", f"image_{img_id}.png")
                    })
        
        if not files:
            return self.error_response(
                "No valid images found",
                status=self.HTTP_NOT_FOUND
            )
        
        # This would create a zip file for download
        return self.success_response({
            "files": files,
            "count": len(files),
            "format": "zip"
        })
    
    # Route registration helper
    
    def register_routes(self, app):
        """Register all image endpoints with the application.
        
        Args:
            app: Application instance
        """
        # Standard CRUD
        app.route("/api/images", methods=["GET"])(self.list_images)
        app.route("/api/images", methods=["POST"])(self.create_image)
        app.route("/api/images/<int:image_id>", methods=["GET"])(self.get_image)
        app.route("/api/images/<int:image_id>", methods=["PUT"])(self.update_image)
        app.route("/api/images/<int:image_id>", methods=["DELETE"])(self.delete_image)
        
        # File operations
        app.route("/api/images/<int:image_id>/file", methods=["GET"])(self.get_image_file)
        app.route("/api/images/<int:image_id>/thumbnail", methods=["GET"])(self.get_image_thumbnail)
        app.route("/api/images/<int:image_id>/download", methods=["GET"])(self.download_image)
        app.route("/api/images/upload", methods=["POST"])(self.upload_image)
        app.route("/api/images/batch-download", methods=["POST"])(self.batch_download)
        
        # Filtering and search
        app.route("/api/images/prompt/<int:prompt_id>", methods=["GET"])(self.get_images_by_prompt)
        app.route("/api/images/checkpoint/<checkpoint>", methods=["GET"])(self.get_images_by_checkpoint)
        app.route("/api/images/checkpoints", methods=["GET"])(self.get_checkpoints)
        app.route("/api/images/statistics", methods=["GET"])(self.get_image_statistics)
        
        # Image operations
        app.route("/api/images/extract-metadata", methods=["POST"])(self.extract_metadata)
        app.route("/api/images/<int:image_id>/regenerate", methods=["POST"])(self.regenerate_image)
        app.route("/api/images/compare", methods=["POST"])(self.compare_images)
        
        # Maintenance
        app.route("/api/images/cleanup", methods=["POST"])(self.cleanup_orphaned)
        app.route("/api/images/bulk-checkpoint", methods=["POST"])(self.bulk_update_checkpoint)
        
        logger.info("Image endpoints registered")
