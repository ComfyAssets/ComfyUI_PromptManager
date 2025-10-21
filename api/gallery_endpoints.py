"""
Gallery API Endpoints for PromptManager
Provides RESTful endpoints for gallery data operations
"""

import os
import json
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
import traceback

# Import flask/aiohttp based on what's available
try:
    from aiohttp import web
    USE_AIOHTTP = True
except ImportError:
    from flask import Flask, request, jsonify, send_file
    USE_AIOHTTP = False

from ..src.database import PromptDatabase
from ..src.components.unified_gallery import UnifiedGallery, FilterCriteria, ViewMode, SortOrder
from ..utils.comfyui_integration import get_comfyui_integration
from ..src.config import config
from ..loggers import get_logger

logger = get_logger(__name__)


class GalleryAPI:
    """Gallery API handler for PromptManager"""

    def __init__(self, db: Optional[PromptDatabase] = None):
        """Initialize gallery API with database connection"""
        self.db = db or PromptDatabase()
        self.gallery = UnifiedGallery(self.db)
        self.comfy_integration = get_comfyui_integration()

    async def get_gallery_items(self, request) -> Dict[str, Any]:
        """
        Get paginated gallery items with optional filtering

        Query parameters:
        - page: Page number (default: 1)
        - items_per_page: Items per page (default: 20)
        - sort_order: Sort order (date_desc, date_asc, prompt_asc, prompt_desc)
        - view_mode: View mode (grid, list, masonry)
        - tags: Comma-separated tags
        - search: Search text
        - date_from: Start date filter
        - date_to: End date filter
        - model: Model name filter
        """
        try:
            # Parse query parameters
            if USE_AIOHTTP:
                params = request.rel_url.query
            else:
                params = request.args

            page = int(params.get('page', 1))
            items_per_page = int(params.get('items_per_page', 20))
            sort_order = params.get('sort_order', 'date_desc')
            view_mode = params.get('view_mode', 'grid')

            # Build filter criteria
            filter_criteria = None
            if any(params.get(k) for k in ['tags', 'search', 'date_from', 'date_to', 'model']):
                filter_criteria = FilterCriteria()

                if tags_str := params.get('tags'):
                    filter_criteria.tags = [t.strip() for t in tags_str.split(',') if t.strip()]

                if search := params.get('search'):
                    filter_criteria.search_text = search

                if date_from := params.get('date_from'):
                    filter_criteria.date_range = (date_from, params.get('date_to', datetime.now().isoformat()))

                if model := params.get('model'):
                    filter_criteria.metadata_filter = {'model': model}

            # Update gallery settings
            self.gallery.items_per_page = items_per_page
            self.gallery.view_mode = ViewMode(view_mode)
            self.gallery.sort_order = SortOrder(sort_order.replace('_', '-'))

            # Get gallery data
            result = self.gallery.get_items(page, filter_criteria)

            # Convert any Path objects to strings
            for item in result.get('items', []):
                for image in item.get('images', []):
                    if 'src' in image and isinstance(image['src'], Path):
                        image['src'] = str(image['src'])
                    if 'thumb' in image and isinstance(image['thumb'], Path):
                        image['thumb'] = str(image['thumb'])

            return {
                'success': True,
                'data': result
            }

        except Exception as e:
            logger.error(f"Failed to get gallery items: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'Failed to retrieve gallery items'
            }

    async def get_gallery_models(self, request) -> Dict[str, Any]:
        """Get list of available models for filtering"""
        try:
            # Get unique models from database
            models = self.db.get_unique_models()

            # Format for frontend
            formatted_models = []
            for model in models:
                display_name = Path(model).stem if model else "Unknown"
                formatted_models.append({
                    'name': model,
                    'display_name': display_name,
                    'count': self.db.get_gallery_count(metadata_filter={'model': model})
                })

            return {
                'success': True,
                'models': formatted_models
            }

        except Exception as e:
            logger.error(f"Failed to get models: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def export_gallery_items(self, request) -> Dict[str, Any]:
        """Export selected gallery items"""
        try:
            # Parse request body
            if USE_AIOHTTP:
                data = await request.json()
            else:
                data = request.get_json()

            item_ids = data.get('item_ids', [])
            export_format = data.get('format', 'json')

            if not item_ids:
                return {
                    'success': False,
                    'error': 'No items selected for export'
                }

            # Export using gallery method
            export_path = self.gallery.export_selection(item_ids, export_format)

            if export_path:
                # Generate download URL with sanitized filename
                filename = Path(export_path).name
                safe_filename = filename.replace('"', '').replace("'", '').replace('\\', '')
                download_url = f"/api/promptmanager/gallery/download/{safe_filename}"

                return {
                    'success': True,
                    'export_path': str(export_path),
                    'download_url': download_url,
                    'item_count': len(item_ids)
                }
            else:
                return {
                    'success': False,
                    'error': 'Export failed'
                }

        except Exception as e:
            logger.error(f"Failed to export items: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def get_gallery_settings(self, request) -> Dict[str, Any]:
        """Get current gallery settings"""
        try:
            # Build settings from config
            settings = {
                'gallery': {
                    'itemsPerPage': config.ui.items_per_page,
                    'viewMode': config._extra_settings.get('gallery.viewMode', 'grid'),
                    'sortOrder': config._extra_settings.get('gallery.sortOrder', 'date_desc')
                },
                'viewer': self.gallery._viewer_config,
                'ui': {
                    'theme': config.ui.theme,
                    'compactMode': config.ui.compact_mode,
                    'galleryColumns': config.ui.gallery_columns
                },
                'lastUpdated': datetime.now().isoformat()
            }

            return {
                'success': True,
                'settings': settings
            }

        except Exception as e:
            logger.error(f"Failed to get settings: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def update_gallery_settings(self, request) -> Dict[str, Any]:
        """Update gallery settings"""
        try:
            # Parse request body
            if USE_AIOHTTP:
                settings = await request.json()
            else:
                settings = request.get_json()

            # Update config from settings
            if 'gallery' in settings:
                gallery_settings = settings['gallery']
                if 'itemsPerPage' in gallery_settings:
                    config.ui.items_per_page = gallery_settings['itemsPerPage']
                if 'viewMode' in gallery_settings:
                    config._extra_settings['gallery.viewMode'] = gallery_settings['viewMode']
                if 'sortOrder' in gallery_settings:
                    config._extra_settings['gallery.sortOrder'] = gallery_settings['sortOrder']
            
            if 'ui' in settings:
                ui_settings = settings['ui']
                if 'theme' in ui_settings:
                    config.ui.theme = ui_settings['theme']
                if 'compactMode' in ui_settings:
                    config.ui.compact_mode = ui_settings['compactMode']
                if 'galleryColumns' in ui_settings:
                    config.ui.gallery_columns = ui_settings['galleryColumns']

            # Update viewer configuration
            if 'viewer' in settings:
                self.gallery.update_viewer_config(settings['viewer'])
            
            # Persist all changes
            config.save()

            return {
                'success': True,
                'message': 'Settings updated successfully'
            }

        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def track_image_view(self, request) -> Dict[str, Any]:
        """Track when an image is viewed"""
        try:
            # Parse request body
            if USE_AIOHTTP:
                data = await request.json()
            else:
                data = request.get_json()

            item_id = data.get('item_id')
            timestamp = data.get('timestamp', datetime.now().isoformat())

            if item_id:
                # Update view count in database
                self.db.increment_view_count(item_id)

                # Log view event
                logger.info(f"Image {item_id} viewed at {timestamp}")

            return {
                'success': True,
                'tracked': True
            }

        except Exception as e:
            logger.warning(f"Failed to track view: {e}")
            return {
                'success': False,
                'error': str(e)
            }


def register_gallery_routes(app, db: Optional[PromptDatabase] = None):
    """Register gallery API routes with the application"""
    api = GalleryAPI(db)

    if USE_AIOHTTP:
        # aiohttp routes
        app.router.add_get('/api/promptmanager/gallery', api.get_gallery_items)
        app.router.add_get('/api/promptmanager/models', api.get_gallery_models)
        app.router.add_post('/api/promptmanager/gallery/export', api.export_gallery_items)
        app.router.add_get('/api/promptmanager/settings', api.get_gallery_settings)
        app.router.add_post('/api/promptmanager/settings', api.update_gallery_settings)
        app.router.add_post('/api/promptmanager/gallery/track-view', api.track_image_view)
    else:
        # Flask routes
        @app.route('/api/promptmanager/gallery', methods=['GET'])
        def get_gallery():
            return jsonify(api.get_gallery_items(request))

        @app.route('/api/promptmanager/models', methods=['GET'])
        def get_models():
            return jsonify(api.get_gallery_models(request))

        @app.route('/api/promptmanager/gallery/export', methods=['POST'])
        def export_gallery():
            return jsonify(api.export_gallery_items(request))

        @app.route('/api/promptmanager/settings', methods=['GET'])
        def get_settings():
            return jsonify(api.get_gallery_settings(request))

        @app.route('/api/promptmanager/settings', methods=['POST'])
        def update_settings():
            return jsonify(api.update_gallery_settings(request))

        @app.route('/api/promptmanager/gallery/track-view', methods=['POST'])
        def track_view():
            return jsonify(api.track_image_view(request))

    logger.info("Gallery API routes registered successfully")