"""Image gallery implementation extending BaseGallery.

This module implements image-specific gallery functionality,
inheriting all common functionality from BaseGallery.
"""

import os
from typing import Any, Dict, List, Optional
from datetime import datetime

from src.components.base_gallery import BaseGallery, ViewMode
from src.services.image_service import ImageService

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.components.image_gallery")


class ImageGallery(BaseGallery):
    """Gallery for displaying and managing generated images.
    
    Extends BaseGallery with image-specific rendering and functionality.
    All common gallery operations are inherited - only domain-specific logic added.
    """
    
    def __init__(self, service: ImageService = None):
        """Initialize image gallery.
        
        Args:
            service: ImageService instance (creates default if None)
        """
        if service is None:
            from src.services.image_service import ImageService
            service = ImageService()
        super().__init__(service)
        
        # Image-specific settings
        self.thumbnail_size = (256, 256)
        self.show_metadata = True
        self.show_prompt = True
        self.show_generation_info = True
        self.lazy_load = True  # Load images as they come into view
    
    def load_items(self):
        """Load items from service."""
        result = self.service.list(
            page=self.current_page,
            per_page=self.per_page,
            sort_by=self.sort_by,
            sort_desc=self.sort_desc,
            **self.filter_params
        )
        
        self.items = result.get("items", [])
        self.total_items = result.get("total", 0)
        self.total_pages = result.get("pages", 1)
    
    def render_item(self, item: Dict[str, Any]) -> str:
        """Render a single item based on current view mode."""
        if self.view_mode == ViewMode.GRID:
            return self.render_item_grid(item)
        elif self.view_mode == ViewMode.LIST:
            return self.render_item_list(item)
        elif self.view_mode == ViewMode.COMPACT:
            return self.render_item_compact(item)
        elif self.view_mode == ViewMode.DETAILED:
            return self.render_item_detailed(item)
        else:
            return self.render_item_list(item)
    
    def get_item_thumbnail(self, item: Dict[str, Any]) -> Optional[str]:
        """Get thumbnail for an item."""
        return self._get_thumbnail_url(item)
    
    def get_item_metadata(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Get metadata for an item."""
        return {
            "id": item.get("id"),
            "filename": item.get("filename"),
            "width": item.get("width"),
            "height": item.get("height"),
            "file_size": item.get("file_size"),
            "checkpoint": item.get("checkpoint"),
            "prompt_text": item.get("prompt_text"),
            "negative_prompt": item.get("negative_prompt"),
            "sampler": item.get("sampler"),
            "steps": item.get("steps"),
            "cfg_scale": item.get("cfg_scale"),
            "seed": item.get("seed"),
            "created_at": item.get("created_at")
        }
    
    def render_item_grid(self, item: Dict[str, Any]) -> str:
        """Render an image in grid view.
        
        Args:
            item: Image data
            
        Returns:
            HTML string for grid item
        """
        # Use thumbnail for grid view
        image_src = self._get_thumbnail_url(item)
        
        html = f"""
        <div class="image-grid-item" data-id="{item['id']}">
            <div class="image-container">
                {self._render_lazy_image(image_src, item.get('filename', ''))}
                <div class="image-overlay">
                    <div class="image-info">
                        <span class="dimensions">{item.get('width', 0)}x{item.get('height', 0)}</span>
                        <span class="size">{self._format_file_size(item.get('file_size', 0))}</span>
                    </div>
                </div>
            </div>
            <div class="image-meta">
                <span class="checkpoint">{self._truncate_text(item.get('checkpoint', 'Unknown'), 20)}</span>
                <span class="date">{self._format_date(item.get('created_at', ''))}</span>
            </div>
        </div>
        """
        return html
    
    def render_item_list(self, item: Dict[str, Any]) -> str:
        """Render an image in list view.
        
        Args:
            item: Image data
            
        Returns:
            HTML string for list item
        """
        image_src = self._get_thumbnail_url(item)
        
        html = f"""
        <div class="image-list-item" data-id="{item['id']}">
            <div class="image-thumbnail">
                {self._render_lazy_image(image_src, item.get('filename', ''), size="small")}
            </div>
            <div class="image-details">
                <div class="image-name">{item.get('filename', 'Untitled')}</div>
                {self._render_prompt_preview(item.get('prompt_text', ''))}
                <div class="image-info">
                    <span class="checkpoint">{item.get('checkpoint', 'Unknown')}</span>
                    <span class="separator">•</span>
                    <span class="dimensions">{item.get('width', 0)}x{item.get('height', 0)}</span>
                    <span class="separator">•</span>
                    <span class="size">{self._format_file_size(item.get('file_size', 0))}</span>
                </div>
            </div>
            <div class="image-actions">
                <button class="btn-view" data-id="{item['id']}">View</button>
                <button class="btn-metadata" data-id="{item['id']}">Metadata</button>
                <button class="btn-delete" data-id="{item['id']}">Delete</button>
            </div>
        </div>
        """
        return html
    
    def render_item_compact(self, item: Dict[str, Any]) -> str:
        """Render an image in compact view.
        
        Args:
            item: Image data
            
        Returns:
            HTML string for compact item
        """
        html = f"""
        <div class="image-compact-item" data-id="{item['id']}">
            <span class="image-icon">🖼️</span>
            <span class="image-name">{self._truncate_text(item.get('filename', ''), 30)}</span>
            <span class="image-size">{self._format_file_size(item.get('file_size', 0))}</span>
            <span class="image-date">{self._format_date_short(item.get('created_at', ''))}</span>
        </div>
        """
        return html
    
    def render_item_detailed(self, item: Dict[str, Any]) -> str:
        """Render an image in detailed view.
        
        Args:
            item: Image data
            
        Returns:
            HTML string for detailed item
        """
        image_src = self._get_full_image_url(item)
        
        html = f"""
        <div class="image-detailed-item" data-id="{item['id']}">
            <div class="image-header">
                <h3>{item.get('filename', 'Untitled')}</h3>
                <div class="image-actions">
                    <button class="btn-download" data-id="{item['id']}">Download</button>
                    <button class="btn-regenerate" data-id="{item['id']}">Regenerate</button>
                    <button class="btn-compare" data-id="{item['id']}">Compare</button>
                    <button class="btn-delete" data-id="{item['id']}">Delete</button>
                </div>
            </div>
            
            <div class="image-content">
                <div class="image-viewer">
                    <img src="{image_src}" alt="{item.get('filename', '')}" 
                         data-full-src="{image_src}"
                         onclick="this.classList.toggle('zoomed')">
                </div>
                
                <div class="image-information">
                    {self._render_generation_section(item)}
                    {self._render_prompt_section(item)}
                    {self._render_technical_section(item)}
                    {self._render_metadata_section(item)}
                </div>
            </div>
            
            <div class="image-footer">
                <span class="created">Created: {item.get('created_at', '')}</span>
                <span class="hash">Hash: {item.get('image_hash', '')[:8]}...</span>
            </div>
        </div>
        """
        return html
    
    # Image-specific rendering helpers
    
    def _get_thumbnail_url(self, item: Dict[str, Any]) -> str:
        """Get thumbnail URL for an image.
        
        Args:
            item: Image data
            
        Returns:
            Thumbnail URL
        """
        # Check if thumbnail exists
        if item.get("thumbnail_path"):
            return f"/api/images/thumbnail/{item['id']}"
        # Fall back to full image
        return self._get_full_image_url(item)
    
    def _get_full_image_url(self, item: Dict[str, Any]) -> str:
        """Get full image URL.
        
        Args:
            item: Image data
            
        Returns:
            Full image URL
        """
        return f"/api/images/full/{item['id']}"
    
    def _render_lazy_image(self, src: str, alt: str, size: str = "medium") -> str:
        """Render lazy-loading image element.
        
        Args:
            src: Image source URL
            alt: Alt text
            size: Size class (small, medium, large)
            
        Returns:
            HTML string for image
        """
        if self.lazy_load:
            return f"""
            <img class="lazy-image {size}" 
                 data-src="{src}" 
                 alt="{alt}"
                 loading="lazy">
            """
        else:
            return f'<img class="{size}" src="{src}" alt="{alt}">'
    
    def _render_prompt_preview(self, prompt: str) -> str:
        """Render prompt preview for list view.
        
        Args:
            prompt: Prompt text
            
        Returns:
            HTML string or empty
        """
        if not self.show_prompt or not prompt:
            return ""
        
        preview = self._truncate_text(prompt, 100)
        return f'<div class="prompt-preview">{preview}</div>'
    
    def _render_generation_section(self, item: Dict[str, Any]) -> str:
        """Render generation information section.
        
        Args:
            item: Image data
            
        Returns:
            HTML string
        """
        if not self.show_generation_info:
            return ""
        
        return f"""
        <div class="info-section">
            <h4>Generation Settings</h4>
            <div class="info-grid">
                <div class="info-item">
                    <label>Checkpoint:</label>
                    <span>{item.get('checkpoint', 'Unknown')}</span>
                </div>
                <div class="info-item">
                    <label>Sampler:</label>
                    <span>{item.get('sampler', 'Unknown')}</span>
                </div>
                <div class="info-item">
                    <label>Steps:</label>
                    <span>{item.get('steps', 'Unknown')}</span>
                </div>
                <div class="info-item">
                    <label>CFG Scale:</label>
                    <span>{item.get('cfg_scale', 'Unknown')}</span>
                </div>
                <div class="info-item">
                    <label>Seed:</label>
                    <span>{item.get('seed', 'Unknown')}</span>
                </div>
                <div class="info-item">
                    <label>Model Hash:</label>
                    <span>{self._truncate_text(item.get('model_hash', 'Unknown'), 12)}</span>
                </div>
            </div>
        </div>
        """
    
    def _render_prompt_section(self, item: Dict[str, Any]) -> str:
        """Render prompt section.
        
        Args:
            item: Image data
            
        Returns:
            HTML string
        """
        if not self.show_prompt:
            return ""
        
        prompt = item.get('prompt_text', '')
        negative = item.get('negative_prompt', '')
        
        if not prompt and not negative:
            return ""
        
        sections = []
        
        if prompt:
            sections.append(f"""
            <div class="prompt-subsection">
                <label>Prompt:</label>
                <div class="prompt-text">{prompt}</div>
            </div>
            """)
        
        if negative:
            sections.append(f"""
            <div class="prompt-subsection">
                <label>Negative Prompt:</label>
                <div class="negative-prompt-text">{negative}</div>
            </div>
            """)
        
        return f"""
        <div class="info-section">
            <h4>Prompts</h4>
            {''.join(sections)}
        </div>
        """
    
    def _render_technical_section(self, item: Dict[str, Any]) -> str:
        """Render technical information section.
        
        Args:
            item: Image data
            
        Returns:
            HTML string
        """
        return f"""
        <div class="info-section">
            <h4>Technical Details</h4>
            <div class="info-grid">
                <div class="info-item">
                    <label>Dimensions:</label>
                    <span>{item.get('width', 0)}x{item.get('height', 0)}</span>
                </div>
                <div class="info-item">
                    <label>File Size:</label>
                    <span>{self._format_file_size(item.get('file_size', 0))}</span>
                </div>
                <div class="info-item">
                    <label>Format:</label>
                    <span>{item.get('format', 'PNG')}</span>
                </div>
                <div class="info-item">
                    <label>Color Space:</label>
                    <span>{item.get('color_space', 'RGB')}</span>
                </div>
                <div class="info-item">
                    <label>Bit Depth:</label>
                    <span>{item.get('bit_depth', '8')}</span>
                </div>
                <div class="info-item">
                    <label>DPI:</label>
                    <span>{item.get('dpi', '72')}</span>
                </div>
            </div>
        </div>
        """
    
    def _render_metadata_section(self, item: Dict[str, Any]) -> str:
        """Render metadata section.
        
        Args:
            item: Image data
            
        Returns:
            HTML string
        """
        if not self.show_metadata:
            return ""
        
        metadata = item.get('metadata', {})
        if not metadata:
            return ""
        
        # Format metadata as JSON for display
        import json
        metadata_json = json.dumps(metadata, indent=2)
        
        return f"""
        <div class="info-section">
            <h4>Metadata</h4>
            <pre class="metadata-json">{metadata_json}</pre>
        </div>
        """
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable form.
        
        Args:
            size_bytes: Size in bytes
            
        Returns:
            Formatted size string
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    
    def _format_date(self, date_str: str) -> str:
        """Format date string.
        
        Args:
            date_str: ISO date string
            
        Returns:
            Formatted date
        """
        if not date_str:
            return ""
        
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M")
        except:
            return date_str
    
    def _format_date_short(self, date_str: str) -> str:
        """Format date string in short form.
        
        Args:
            date_str: ISO date string
            
        Returns:
            Short formatted date
        """
        if not date_str:
            return ""
        
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%m/%d")
        except:
            return date_str[:10] if len(date_str) > 10 else date_str
    
    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to maximum length.
        
        Args:
            text: Text to truncate
            max_length: Maximum length
            
        Returns:
            Truncated text
        """
        if len(text) <= max_length:
            return text
        return text[:max_length - 3] + "..."
    
    # Gallery-specific methods
    
    def filter_by_checkpoint(self, checkpoint: str):
        """Filter gallery by checkpoint.
        
        Args:
            checkpoint: Checkpoint name
        """
        self.filter_params["checkpoint"] = checkpoint
        self.current_page = 1
        self.load_items()
    
    def filter_by_date_range(self, start_date: str, end_date: str):
        """Filter gallery by date range.
        
        Args:
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
        """
        self.filter_params["start_date"] = start_date
        self.filter_params["end_date"] = end_date
        self.current_page = 1
        self.load_items()
    
    def filter_by_dimensions(self, min_width: int = None, min_height: int = None):
        """Filter gallery by minimum dimensions.
        
        Args:
            min_width: Minimum width in pixels
            min_height: Minimum height in pixels
        """
        if min_width:
            self.filter_params["min_width"] = min_width
        if min_height:
            self.filter_params["min_height"] = min_height
        self.current_page = 1
        self.load_items()
    
    def get_checkpoints(self) -> List[str]:
        """Get list of unique checkpoints.
        
        Returns:
            List of checkpoint names
        """
        return self.service.get_unique_checkpoints()
    
    def regenerate_image(self, image_id: int) -> Optional[Dict[str, Any]]:
        """Regenerate an image with same settings.
        
        Args:
            image_id: Image ID
            
        Returns:
            New image data or None
        """
        original = self.service.get(image_id)
        if not original:
            return None
        
        # This would trigger ComfyUI to regenerate with same settings
        return self.service.regenerate(original)
    
    def compare_images(self, image_ids: List[int]) -> Dict[str, Any]:
        """Compare multiple images side by side.
        
        Args:
            image_ids: List of image IDs to compare
            
        Returns:
            Comparison data
        """
        images = [self.service.get(img_id) for img_id in image_ids]
        images = [img for img in images if img]  # Filter out None
        
        return {
            "images": images,
            "count": len(images),
            "comparison_id": f"compare_{'_'.join(map(str, image_ids))}"
        }
    
    def download_image(self, image_id: int) -> Optional[str]:
        """Get download path for an image.
        
        Args:
            image_id: Image ID
            
        Returns:
            Download path or None
        """
        image = self.service.get(image_id)
        if image and image.get("file_path"):
            return image["file_path"]
        return None
    
    def batch_download(self, image_ids: List[int]) -> List[str]:
        """Get download paths for multiple images.
        
        Args:
            image_ids: List of image IDs
            
        Returns:
            List of download paths
        """
        paths = []
        for img_id in image_ids:
            path = self.download_image(img_id)
            if path:
                paths.append(path)
        return paths
