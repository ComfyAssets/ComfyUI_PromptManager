"""Image service implementation extending BaseService.

This module implements image-specific business logic,
inheriting all common functionality from BaseService.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.base_service import BaseService
from src.core.validators import Validators, ValidationError
from src.repositories.image_repository import ImageRepository

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore



class ImageService(BaseService):
    """Service for image management.
    
    Extends BaseService with image-specific business logic.
    All CRUD operations are inherited - only domain-specific logic added.
    """
    
    def __init__(self, repository: ImageRepository = None):
        """Initialize image service.
        
        Args:
            repository: ImageRepository instance (creates default if None)
        """
        if repository is None:
            from src.repositories.image_repository import ImageRepository
            repository = ImageRepository()
        super().__init__(repository)
        
        # Image-specific settings
        self.validate_on_save = True
        self.generate_thumbnails = True
        self.extract_metadata = True
    
    def validate_create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate image data before creation.
        
        Args:
            data: Image data
            
        Returns:
            Validated and normalized data
            
        Raises:
            ValidationError: If validation fails
        """
        # Validate file path
        file_path = data.get("file_path", "")
        if not file_path:
            raise ValidationError("file_path", "File path is required")
        
        file_path = Validators.validate_file_path(file_path)
        
        # Validate dimensions
        width = data.get("width", 0)
        height = data.get("height", 0)
        if width <= 0 or height <= 0:
            raise ValidationError("dimensions", "Invalid image dimensions")
        
        # Build validated data
        validated = {
            "file_path": file_path,
            "filename": data.get("filename", file_path.split("/")[-1]),
            "width": width,
            "height": height,
            "file_size": data.get("file_size", 0),
            "format": data.get("format", "PNG"),
            "checkpoint": data.get("checkpoint", ""),
            "prompt_id": data.get("prompt_id"),
            "prompt_text": data.get("prompt_text", ""),
            "negative_prompt": data.get("negative_prompt", ""),
            "sampler": data.get("sampler", ""),
            "steps": data.get("steps", 0),
            "cfg_scale": data.get("cfg_scale", 0.0),
            "seed": data.get("seed", -1),
            "model_hash": data.get("model_hash", ""),
            "metadata": data.get("metadata", {}),
            "workflow": data.get("workflow", {}),
            "image_hash": data.get("image_hash", "")
        }
        
        # Check for duplicates by hash
        if validated["image_hash"]:
            existing = self.repository.find_by_hash(validated["image_hash"])
            if existing:
                raise ValidationError(
                    "image",
                    f"Duplicate image found (ID: {existing['id']})",
                    file_path
                )
        
        return validated
    
    def validate_update(self, id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate image data before update.
        
        Args:
            id: Image ID
            data: Update data
            
        Returns:
            Validated and normalized data
            
        Raises:
            ValidationError: If validation fails
        """
        validated = {}
        
        # Validate file path if provided
        if "file_path" in data:
            validated["file_path"] = Validators.validate_file_path(data["file_path"])
        
        # Validate dimensions if provided
        if "width" in data or "height" in data:
            width = data.get("width", 0)
            height = data.get("height", 0)
            if width <= 0 or height <= 0:
                raise ValidationError("dimensions", "Invalid image dimensions")
            validated["width"] = width
            validated["height"] = height
        
        # Pass through other fields
        for key in ["filename", "checkpoint", "prompt_text", "negative_prompt",
                    "sampler", "steps", "cfg_scale", "seed", "model_hash",
                    "metadata", "workflow"]:
            if key in data:
                validated[key] = data[key]
        
        return validated
    
    def _get_searchable_fields(self) -> List[str]:
        """Get default searchable fields.
        
        Returns:
            List of field names
        """
        return ["filename", "prompt_text", "negative_prompt", "checkpoint"]
    
    # Image-specific methods
    
    def get_by_prompt(self, prompt_id: int) -> List[Dict[str, Any]]:
        """Get images by prompt ID.
        
        Args:
            prompt_id: Prompt ID
            
        Returns:
            List of images
        """
        return self.repository.get_by_prompt(prompt_id)
    
    def get_by_checkpoint(self, checkpoint: str) -> List[Dict[str, Any]]:
        """Get images by checkpoint.
        
        Args:
            checkpoint: Checkpoint name
            
        Returns:
            List of images
        """
        return self.repository.get_by_checkpoint(checkpoint)
    
    def get_unique_checkpoints(self) -> List[str]:
        """Get list of unique checkpoints.
        
        Returns:
            List of checkpoint names
        """
        return self.repository.get_unique_checkpoints()
    
    def regenerate(self, image_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Regenerate an image with same settings.
        
        Args:
            image_data: Original image data
            
        Returns:
            New image data or None
        """
        # This would interface with ComfyUI to regenerate
        # For now, return None as placeholder
        logger.info(f"Regenerate requested for image {image_data.get('id')}")
        return None
    
    def extract_metadata_from_file(self, file_path: str) -> Dict[str, Any]:
        """Extract metadata from image file.
        
        Args:
            file_path: Path to image file
            
        Returns:
            Metadata dictionary
        """
        # This would use PNG chunk extraction
        # Placeholder for now
        return {}
    
    def generate_thumbnail(self, image_id: int) -> bool:
        """Generate thumbnail for an image.
        
        Args:
            image_id: Image ID
            
        Returns:
            True if successful
        """
        # This would generate actual thumbnail
        # Placeholder for now
        return True
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get image statistics.
        
        Returns:
            Statistics dictionary
        """
        return self.repository.get_statistics()
    
    def cleanup_orphaned(self) -> int:
        """Clean up orphaned image records.
        
        Returns:
            Number of records cleaned
        """
        return self.repository.cleanup_orphaned()
