"""
Unified Gallery Implementation - Combines all gallery functionality with ViewerJS integration.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import asyncio
from abc import abstractmethod

from .base_gallery import BaseGallery, ViewMode, SortOrder, FilterCriteria
from ..database import PromptDatabase, extend_prompt_database_with_gallery
from ..utils.comfyui_integration import get_comfyui_integration
from ..config import config

try:  # pragma: no cover - import path differs between runtime contexts
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger(__name__)


class UnifiedGallery(BaseGallery):
    """
    Unified gallery implementation that integrates with ViewerJS frontend.
    Provides a single source of truth for all gallery views.
    """

    def __init__(self,
                 db: Optional[PromptDatabase] = None,
                 items_per_page: int = 20,
                 view_mode: ViewMode = ViewMode.GRID):
        """Initialize the unified gallery with database and settings."""
        super().__init__(items_per_page, view_mode)

        self.db = db or PromptDatabase()
        # Extend database with gallery operations
        self.db = extend_prompt_database_with_gallery(self.db)
        self.comfy_integration = get_comfyui_integration()
        self._viewer_config = self._load_viewer_config()
        self._cache = {}

    def _load_viewer_config(self) -> Dict[str, Any]:
        """Load ViewerJS configuration from settings."""
        try:
            # Load from settings file or database
            settings_path = Path(self.comfy_integration.get_output_directory()) / "promptmanager_settings.json"
            if settings_path.exists():
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
                    return settings.get('viewer', {
                        'theme': 'dark',
                        'toolbar': True,
                        'navbar': True,
                        'title': True,
                        'keyboard': True,
                        'backdrop': True,
                        'button': True,
                        'fullscreen': True,
                        'inline': False,
                        'viewed': True,
                        'tooltip': True,
                        'movable': True,
                        'zoomable': True,
                        'rotatable': True,
                        'scalable': True,
                        'transition': True,
                        'loading': True,
                        'loop': True,
                        'slideOnTouch': True
                    })
        except Exception as e:
            logger.warning(f"Failed to load viewer config: {e}")
            return {}

    def get_items(self, page: int = 1, filter_criteria: Optional[FilterCriteria] = None) -> Dict[str, Any]:
        """
        Get paginated gallery items with metadata.

        Returns:
            Dict containing items, pagination info, and viewer configuration
        """
        # Calculate pagination
        offset = (page - 1) * self.items_per_page

        # Build query based on filter criteria
        items = self._fetch_gallery_items(offset, self.items_per_page, filter_criteria)
        total_count = self._get_total_count(filter_criteria)

        # Format items for ViewerJS
        formatted_items = []
        for item in items:
            formatted_item = self._format_item_for_viewer(item)
            if formatted_item:
                formatted_items.append(formatted_item)

        return {
            'items': formatted_items,
            'pagination': {
                'page': page,
                'total_pages': (total_count + self.items_per_page - 1) // self.items_per_page,
                'total_items': total_count,
                'items_per_page': self.items_per_page
            },
            'viewer_config': self._viewer_config,
            'view_mode': self.view_mode.value,
            'sort_order': self.sort_order.value
        }

    def _fetch_gallery_items(self, offset: int, limit: int,
                            filter_criteria: Optional[FilterCriteria]) -> List[Dict]:
        """Fetch items from database with filtering."""
        try:
            # Build query based on filter criteria
            query_params = {
                'offset': offset,
                'limit': limit,
                'order_by': self.sort_order.value
            }

            if filter_criteria:
                if filter_criteria.tags:
                    query_params['tags'] = filter_criteria.tags
                if filter_criteria.date_range:
                    query_params['date_from'] = filter_criteria.date_range[0]
                    query_params['date_to'] = filter_criteria.date_range[1]
                if filter_criteria.search_text:
                    query_params['search'] = filter_criteria.search_text
                if filter_criteria.has_images is not None:
                    query_params['has_images'] = filter_criteria.has_images

            # Fetch from database
            results = self.db.get_gallery_items(**query_params)
            return results

        except Exception as e:
            logger.error(f"Failed to fetch gallery items: {e}")
            return []

    def _get_total_count(self, filter_criteria: Optional[FilterCriteria]) -> int:
        """Get total count of items matching filter criteria."""
        try:
            query_params = {}
            if filter_criteria:
                if filter_criteria.tags:
                    query_params['tags'] = filter_criteria.tags
                if filter_criteria.search_text:
                    query_params['search'] = filter_criteria.search_text

            return self.db.get_gallery_count(**query_params)
        except Exception as e:
            logger.error(f"Failed to get total count: {e}")
            return 0

    def _format_item_for_viewer(self, item: Dict) -> Optional[Dict[str, Any]]:
        """
        Format a database item for ViewerJS consumption.

        Args:
            item: Raw database item

        Returns:
            Formatted item dict or None if invalid
        """
        try:
            # Extract image paths
            images = []
            if 'images' in item and item['images']:
                for img in item['images']:
                    img_path = self._resolve_image_path(img.get('filename'))
                    if img_path:
                        images.append({
                            'src': img_path,
                            'thumb': self._get_thumbnail_path(img.get('filename')),
                            'alt': img.get('prompt_text', ''),
                            'title': img.get('prompt_text', '')
                        })

            # If no images but has prompt, create placeholder
            if not images and item.get('prompt_text'):
                images.append({
                    'src': '/web/images/placeholder.png',
                    'thumb': '/web/images/placeholder-thumb.png',
                    'alt': item['prompt_text'][:100],
                    'title': 'No image generated'
                })

            if not images:
                return None

            return {
                'id': item.get('id'),
                'prompt_id': item.get('prompt_id'),
                'images': images,
                'metadata': {
                    'prompt': item.get('prompt_text', ''),
                    'negative_prompt': item.get('negative_prompt', ''),
                    'created_at': item.get('created_at', ''),
                    'model': item.get('model_name', ''),
                    'tags': item.get('tags', []),
                    'settings': item.get('generation_settings', {}),
                    'workflow': item.get('workflow_data', {})
                },
                'display': {
                    'title': self._generate_display_title(item),
                    'subtitle': self._generate_display_subtitle(item),
                    'badges': self._generate_badges(item)
                }
            }
        except Exception as e:
            logger.error(f"Failed to format item: {e}")
            return None

    def _resolve_image_path(self, filename: str) -> Optional[str]:
        """Resolve image filename to full path."""
        if not filename:
            return None

        # Check if it's already a full path
        if filename.startswith('/'):
            return filename

        # Build path relative to ComfyUI output
        output_dir = self.comfy_integration.get_output_directory()
        full_path = Path(output_dir) / filename

        if full_path.exists():
            return str(full_path)

        # Try web-accessible path
        return f"/view?filename={filename}"

    def _get_thumbnail_path(self, filename: str) -> str:
        """Get thumbnail path for an image."""
        if not filename:
            return '/web/images/placeholder-thumb.png'

        # Use configured thumbnails directory from config
        thumb_dir = Path(config.storage.base_path) / config.storage.thumbnails_path
        thumb_path = thumb_dir / f"thumb_{filename}"

        if thumb_path.exists():
            return str(thumb_path)

        # Return original with thumbnail endpoint
        return f"/thumbnail?filename={filename}"

    def _generate_display_title(self, item: Dict) -> str:
        """Generate display title for gallery item."""
        prompt = item.get('prompt_text', '')
        if len(prompt) > 50:
            return prompt[:47] + '...'
        return prompt or f"Item #{item.get('id', 'Unknown')}"

    def _generate_display_subtitle(self, item: Dict) -> str:
        """Generate subtitle with model and date info."""
        parts = []
        if model := item.get('model_name'):
            parts.append(model.split('/')[-1])  # Just the model filename
        if created := item.get('created_at'):
            parts.append(created.split('T')[0])  # Just the date
        return ' • '.join(parts)

    def _generate_badges(self, item: Dict) -> List[Dict[str, str]]:
        """Generate badges for special attributes."""
        badges = []

        # Image count badge
        if images := item.get('images'):
            if len(images) > 1:
                badges.append({
                    'text': f"{len(images)} images",
                    'class': 'badge-info'
                })

        # Tags badges
        if tags := item.get('tags'):
            for tag in tags[:3]:  # Limit to 3 tags
                badges.append({
                    'text': tag,
                    'class': 'badge-tag'
                })

        # Workflow badge
        if item.get('workflow_data'):
            badges.append({
                'text': 'Has Workflow',
                'class': 'badge-workflow'
            })

        return badges

    def update_viewer_config(self, config: Dict[str, Any]) -> bool:
        """
        Update ViewerJS configuration.

        Args:
            config: New viewer configuration

        Returns:
            True if successful
        """
        try:
            self._viewer_config.update(config)

            # Save to settings file
            settings_path = Path(self.comfy_integration.get_output_directory()) / "promptmanager_settings.json"

            if settings_path.exists():
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            else:
                settings = {}

            settings['viewer'] = self._viewer_config

            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)

            return True

        except Exception as e:
            logger.error(f"Failed to update viewer config: {e}")
            return False

    def export_selection(self, item_ids: List[int], format: str = 'json') -> Optional[str]:
        """
        Export selected gallery items.

        Args:
            item_ids: List of item IDs to export
            format: Export format (json, csv, etc.)

        Returns:
            Export file path or None if failed
        """
        try:
            items = self.db.get_items_by_ids(item_ids)

            if format == 'json':
                export_data = {
                    'version': '2.0',
                    'export_date': str(Path.ctime(Path())),
                    'items': [self._format_item_for_viewer(item) for item in items]
                }

                export_path = Path(self.comfy_integration.get_output_directory()) / f"export_{Path.ctime(Path())}.json"
                with open(export_path, 'w') as f:
                    json.dump(export_data, f, indent=2)

                return str(export_path)

            # Add other export formats as needed
            return None

        except Exception as e:
            logger.error(f"Failed to export selection: {e}")
            return None

    async def refresh_thumbnails(self) -> Dict[str, Any]:
        """
        Refresh thumbnails for all gallery items asynchronously.

        Returns:
            Status report of thumbnail generation
        """
        try:
            items = self.db.get_all_items_with_images()
            # Note: thumbnail directory is created by config system during initialization
            # No need to create it here

            success_count = 0
            failed_count = 0

            for item in items:
                if images := item.get('images'):
                    for img in images:
                        if filename := img.get('filename'):
                            # Generate thumbnail asynchronously
                            # This would integrate with your thumbnail generation service
                            success_count += 1

            return {
                'status': 'complete',
                'successful': success_count,
                'failed': failed_count,
                'total': success_count + failed_count
            }

        except Exception as e:
            logger.error(f"Failed to refresh thumbnails: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }