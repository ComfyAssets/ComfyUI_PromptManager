"""Image gallery implementation using base gallery class.

Provides functionality for displaying, filtering, and managing
generated images with their associated prompts.
"""

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from src.core.base import BaseGallery
from src.repositories.generated_image_repository import GeneratedImageRepository


def _sanitize_for_json(value: Any) -> Any:
    """Recursively replace NaN/Inf values so JSON serialization succeeds."""

    if isinstance(value, dict):
        return {key: _sanitize_for_json(sub_value) for key, sub_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


class ImageGallery(BaseGallery):
    """Gallery for displaying generated images.
    
    Inherits common gallery functionality and implements
    specific logic for loading and rendering images.
    """

    def __init__(self, db_path: str = "prompts.db"):
        """Initialize image gallery.

        Args:
            db_path: Path to database file
        """
        super().__init__("generated_images")
        self.image_repo = GeneratedImageRepository(db_path)
        # Override default sort key to match v2 schema
        self._sort_key = "generation_time"
        self.output_dir = None  # Will be set based on ComfyUI config
        # Extended to support video and audio formats
        self.supported_image_formats = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
        self.supported_video_formats = {".mp4", ".avi", ".mov", ".webm", ".mkv", ".gif"}  # gif can be animated
        self.supported_audio_formats = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}
        self.supported_formats = self.supported_image_formats | self.supported_video_formats | self.supported_audio_formats

    def set_output_directory(self, path: str) -> None:
        """Set the directory to scan for images.
        
        Args:
            path: Path to ComfyUI output directory
        """
        self.output_dir = Path(path)
        if not self.output_dir.exists():
            self.logger.warning(f"Output directory does not exist: {path}")

    def load_items(self, **kwargs) -> List[Dict[str, Any]]:
        """Load gallery items from database.

        Args:
            **kwargs: Additional load parameters

        Returns:
            List of image records
        """
        # Build ORDER BY clause with direction using dedicated repository fields
        sort_key = self._sort_key or "generation_time"
        sort_dir = "DESC" if self._sort_reverse else "ASC"
        order_by_clause = f"{sort_key} {sort_dir}".strip()

        filters = {"order_by": order_by_clause}

        # Apply any additional filters (but not order_dir)
        for key, value in kwargs.items():
            if key not in ["order_by", "order_dir"]:
                filters[key] = value

        records = self.image_repo.list(**filters)

        enriched: List[Dict[str, Any]] = []
        columns = set()
        if hasattr(self.image_repo, "_get_table_columns"):
            try:
                columns = set(self.image_repo._get_table_columns())  # type: ignore[attr-defined]
            except Exception:
                columns = set()

        for record in records:
            path = record.get("image_path") or record.get("file_path")

            needs_size = "file_size" in columns and record.get("file_size") in (None, 0)
            needs_width = "width" in columns and record.get("width") in (None, 0)
            needs_height = "height" in columns and record.get("height") in (None, 0)

            if path and any([needs_size, needs_width, needs_height]):
                metadata = self.image_repo.update_file_metadata(record["id"], path)  # type: ignore[arg-type]
                if metadata:
                    if metadata.size is not None:
                        record["file_size"] = metadata.size
                    if metadata.width is not None:
                        record["width"] = metadata.width
                    if metadata.height is not None:
                        record["height"] = metadata.height
                    if metadata.format and "format" in columns:
                        record.setdefault("format", metadata.format)

            enriched.append(record)

        return enriched

    def render_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Render image item for frontend display.

        Args:
            item: Image record from database

        Returns:
            Rendered item data for frontend
        """
        # Build display data with v2 field names
        # Handle both file_path and image_path for compatibility
        image_path = item.get("image_path") or item.get("file_path", "")
        filename = item.get("filename") or item.get("file_name") or Path(image_path).name if image_path else "unknown"
        image_id = item.get("id")

        # Build API-safe URLs so we never expose raw filesystem paths downstream.
        image_url: Optional[str] = None
        thumbnail_url: Optional[str] = None
        if image_id is not None:
            base_url = f"/api/v1/gallery/images/{image_id}/file"
            image_url = base_url

            if item.get("thumbnail_medium_path"):
                thumbnail_url = f"/api/v1/thumbnails/{image_id}/medium"
            elif item.get("thumbnail_small_path"):
                thumbnail_url = f"/api/v1/thumbnails/{image_id}/small"
            elif item.get("thumbnail_large_path"):
                thumbnail_url = f"/api/v1/thumbnails/{image_id}/large"

            if not thumbnail_url:
                thumbnail_url = f"{base_url}?thumbnail=1"
        elif image_path:
            image_url = image_path
            thumbnail_url = item.get("thumbnail_medium_path") or item.get("thumbnail_small_path") or image_path

        if not thumbnail_url:
            thumbnail_url = (
                thumbnail_variants.get('medium')
                or thumbnail_variants.get('small')
                or thumbnail_variants.get('large')
                or image_url
            )

        format_value = item.get("format")
        if not format_value and filename:
            format_value = Path(filename).suffix.upper().lstrip(".")

        media_type = item.get("media_type") or "image"
        if media_type == "audio":
            thumbnail_url = "/prompt_manager/images/wave-form.svg"

        thumbnail_variants: Dict[str, Optional[str]] = {}
        if image_id is not None:
            if item.get('thumbnail_small_path'):
                thumbnail_variants['small'] = f"/api/v1/thumbnails/{image_id}/small"
            if item.get('thumbnail_medium_path'):
                thumbnail_variants['medium'] = f"/api/v1/thumbnails/{image_id}/medium"
            if item.get('thumbnail_large_path'):
                thumbnail_variants['large'] = f"/api/v1/thumbnails/{image_id}/large"
            if item.get('thumbnail_xlarge_path'):
                thumbnail_variants['xlarge'] = f"/api/v1/thumbnails/{image_id}/xlarge"

        rendered = {
            "id": item.get("id"),
            "filename": filename,
            "path": image_url,  # kept for backward compatibility but now points to the API endpoint
            "thumbnail": thumbnail_url,
            "thumbnail_url": thumbnail_url,
            "image_url": image_url,
            "has_thumbnail": any(thumbnail_variants.values()),
            "thumbnail_variants": {key: value for key, value in thumbnail_variants.items() if value},
            "generation_time": item.get("generation_time") or item.get("created_at", ""),
            "dimensions": f"{item.get('width', 0)}x{item.get('height', 0)}",
            "size": self._format_file_size(item.get("file_size", 0)),
            "format": (format_value or "").upper(),
            "has_prompt": item.get("prompt_id") is not None,
            "media_type": media_type,
        }
        
        # Add metadata if available
        if item.get("prompt_metadata"):
            metadata_value = item["prompt_metadata"]
            if isinstance(metadata_value, str):
                try:
                    metadata_value = json.loads(metadata_value)
                except json.JSONDecodeError:
                    logger = getattr(self, "logger", None)
                    if logger:
                        logger.debug("Unable to parse prompt_metadata for image %s", item.get("id"))
            metadata_value = _sanitize_for_json(metadata_value)
            rendered["metadata"] = metadata_value

        # Add workflow data if available
        if item.get("workflow_data"):
            workflow_value = item["workflow_data"]
            if isinstance(workflow_value, str):
                try:
                    workflow_value = json.loads(workflow_value)
                except json.JSONDecodeError:
                    logger = getattr(self, "logger", None)
                    if logger:
                        logger.debug("Unable to parse workflow_data for image %s", item.get("id"))
            workflow_value = _sanitize_for_json(workflow_value)
            rendered["workflow"] = workflow_value
        
        # Format generation time
        if rendered["generation_time"]:
            try:
                dt = datetime.fromisoformat(rendered["generation_time"])
                rendered["generation_time_display"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                rendered["generation_time_relative"] = self._format_relative_time(dt)
            except:
                rendered["generation_time_display"] = rendered["generation_time"]
                rendered["generation_time_relative"] = ""
        
        return _sanitize_for_json(rendered)

    def scan_directory(self, directory: str = None) -> List[Dict[str, Any]]:
        """Scan directory for new images.
        
        Args:
            directory: Directory to scan (uses output_dir if not provided)
            
        Returns:
            List of new image data ready for database insertion
        """
        scan_dir = Path(directory) if directory else self.output_dir
        
        if not scan_dir or not scan_dir.exists():
            self.logger.warning(f"Invalid scan directory: {scan_dir}")
            return []
        
        new_images = []
        existing_files = set()
        
        # Get existing filenames from database
        for image in self.image_repo.list():
            existing_files.add(image["filename"])
        
        # Scan for new images
        for file_path in scan_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in self.supported_formats:
                filename = file_path.name
                
                # Skip if already in database
                if filename in existing_files:
                    continue
                
                # Get image info
                image_data = self._extract_image_info(file_path)
                
                if image_data:
                    new_images.append(image_data)
                    self.logger.info(f"Found new image: {filename}")
        
        return new_images

    def _extract_image_info(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Extract information from image file.
        
        Args:
            file_path: Path to image file
            
        Returns:
            Image data dictionary or None if extraction fails
        """
        try:
            # Get file stats
            stat = file_path.stat()
            
            # Open image to get dimensions
            with Image.open(file_path) as img:
                width, height = img.size
                format_name = img.format or file_path.suffix[1:].upper()
            
            return {
                "image_path": str(file_path.absolute()),
                "filename": file_path.name,
                "file_size": stat.st_size,
                "width": width,
                "height": height,
                "format": format_name,
                "generation_time": datetime.fromtimestamp(stat.st_mtime).isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error extracting image info from {file_path}: {e}")
            return None

    def create_thumbnail(self, image_path: str, thumbnail_size: tuple = (256, 256)) -> Optional[str]:
        """Create thumbnail for image.
        
        Args:
            image_path: Path to original image
            thumbnail_size: Maximum thumbnail dimensions
            
        Returns:
            Path to thumbnail or None if creation fails
        """
        try:
            source_path = Path(image_path)
            
            # Create thumbnails directory
            thumb_dir = source_path.parent / "thumbnails"
            thumb_dir.mkdir(exist_ok=True)
            
            # Generate thumbnail path
            thumb_path = thumb_dir / f"thumb_{source_path.name}"
            
            # Skip if thumbnail already exists
            if thumb_path.exists():
                return str(thumb_path)
            
            # Create thumbnail
            with Image.open(source_path) as img:
                # Convert RGBA to RGB if needed
                if img.mode in ("RGBA", "LA", "P"):
                    rgb_img = Image.new("RGB", img.size, (0, 0, 0))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    img = rgb_img
                
                # Create thumbnail
                img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                img.save(thumb_path, quality=85, optimize=True)

            self.logger.debug(f"Created thumbnail: {thumb_path}")

            # Ensure the original image metadata is up to date (file size/dimensions)
            if self.image_repo:
                try:
                    record = self.image_repo.find_by_path(str(source_path))
                    if record:
                        self.image_repo.update_file_metadata(record["id"], str(source_path))
                except Exception:  # pragma: no cover - defensive logging
                    self.logger.debug(
                        "Unable to refresh file metadata for %s", source_path, exc_info=True
                    )
            return str(thumb_path)
            
        except Exception as e:
            self.logger.error(f"Error creating thumbnail for {image_path}: {e}")
            return None

    def _format_file_size(self, size_bytes: Optional[int]) -> str:
        """Format file size for display."""

        if size_bytes is None:
            return "0 B"

        try:
            size_value = int(size_bytes)
        except (TypeError, ValueError):
            size_value = 0

        if size_value < 1024:
            return f"{size_value} B"
        if size_value < 1024 * 1024:
            return f"{size_value / 1024:.1f} KB"
        if size_value < 1024 * 1024 * 1024:
            return f"{size_value / (1024 * 1024):.1f} MB"
        return f"{size_value / (1024 * 1024 * 1024):.2f} GB"

    def _format_relative_time(self, dt: datetime) -> str:
        """Format datetime as relative time.
        
        Args:
            dt: Datetime to format
            
        Returns:
            Relative time string (e.g., "2 hours ago")
        """
        now = datetime.now()
        delta = now - dt
        
        if delta.days > 365:
            years = delta.days // 365
            return f"{years} year{'s' if years > 1 else ''} ago"
        elif delta.days > 30:
            months = delta.days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"
        elif delta.days > 0:
            return f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
        elif delta.seconds > 3600:
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif delta.seconds > 60:
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "just now"

    def get_gallery_stats(self) -> Dict[str, Any]:
        """Get gallery statistics.
        
        Returns:
            Dictionary with gallery statistics
        """
        images = self.image_repo.list()
        
        if not images:
            return {
                "total_images": 0,
                "total_size": 0,
                "average_size": 0,
                "formats": {},
                "recent_count": 0
            }
        
        total_size = sum(img.get("file_size", 0) for img in images)
        
        # Count by format
        formats = {}
        for img in images:
            fmt = img.get("format", "Unknown")
            formats[fmt] = formats.get(fmt, 0) + 1
        
        # Count recent images (last 24 hours)
        now = datetime.now()
        recent_count = 0
        for img in images:
            try:
                gen_time = datetime.fromisoformat(img["generation_time"])
                if (now - gen_time).days < 1:
                    recent_count += 1
            except:
                pass
        
        return {
            "total_images": len(images),
            "total_size": total_size,
            "total_size_display": self._format_file_size(total_size),
            "average_size": total_size // len(images) if images else 0,
            "average_size_display": self._format_file_size(total_size // len(images)) if images else "0 B",
            "formats": formats,
            "recent_count": recent_count
        }
