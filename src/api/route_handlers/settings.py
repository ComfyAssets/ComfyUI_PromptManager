"""Settings API route handlers."""

import logging
from aiohttp import web
from typing import Any, Dict

from src.services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class SettingsRouteHandler:
    """Handler for settings-related API routes."""

    def __init__(self, db_path: str):
        """Initialize settings route handler.

        Args:
            db_path: Path to the database
        """
        self.settings_service = SettingsService(db_path)

    async def get_setting(self, request: web.Request) -> web.Response:
        """Get a specific setting value.

        GET /api/prompt_manager/settings/{key}
        """
        try:
            key = request.match_info['key']
            value = self.settings_service.get(key)

            return web.json_response({
                'success': True,
                'key': key,
                'value': value
            })

        except Exception as e:
            logger.error(f"Error getting setting {key}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def set_setting(self, request: web.Request) -> web.Response:
        """Set a setting value.

        POST /api/prompt_manager/settings/{key}
        Body: { value: any, category?: string, description?: string }
        """
        try:
            key = request.match_info['key']
            data = await request.json()

            value = data.get('value')
            category = data.get('category', 'general')
            description = data.get('description')

            success = self.settings_service.set(key, value, category, description)

            return web.json_response({
                'success': success,
                'key': key,
                'value': value
            })

        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_category_settings(self, request: web.Request) -> web.Response:
        """Get all settings in a category.

        GET /api/prompt_manager/settings/category/{category}
        """
        try:
            category = request.match_info['category']
            settings = self.settings_service.get_category(category)

            return web.json_response({
                'success': True,
                'category': category,
                'settings': settings
            })

        except Exception as e:
            logger.error(f"Error getting category {category}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_all_settings(self, request: web.Request) -> web.Response:
        """Get all settings with metadata.

        GET /api/prompt_manager/settings
        """
        try:
            settings = self.settings_service.get_all_settings()

            return web.json_response({
                'success': True,
                'settings': settings
            })

        except Exception as e:
            logger.error(f"Error getting all settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def update_all_settings(self, request: web.Request) -> web.Response:
        """Update multiple settings at once.

        POST /api/prompt_manager/settings
        Body: { gallery: {...}, viewer: {...}, filmstrip: {...}, etc. }
        """
        try:
            settings_data = await request.json()
            updated_count = 0

            # Iterate through categories and update each setting
            for category, category_settings in settings_data.items():
                if isinstance(category_settings, dict):
                    for key, value in category_settings.items():
                        setting_key = f"{category}.{key}"
                        success = self.settings_service.set(setting_key, value, category)
                        if success:
                            updated_count += 1

            return web.json_response({
                'success': True,
                'updated': updated_count,
                'message': f'Updated {updated_count} settings'
            })

        except Exception as e:
            logger.error(f"Error updating all settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def generate_uuid(self, request: web.Request) -> web.Response:
        """Generate a new PromptManager UUID.

        POST /api/prompt_manager/settings/generate_uuid
        """
        try:
            new_uuid = self.settings_service.generate_uuid()

            if new_uuid:
                return web.json_response({
                    'success': True,
                    'uuid': new_uuid
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Failed to generate UUID'
                }, status=500)

        except Exception as e:
            logger.error(f"Error generating UUID: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_community_settings(self, request: web.Request) -> web.Response:
        """Get all community settings.

        GET /api/prompt_manager/settings/community
        """
        try:
            settings = self.settings_service.get_category('community')

            # Mask the API key for security
            if 'civitai_api_key' in settings and settings['civitai_api_key']:
                settings['civitai_api_key'] = '***hidden***'

            return web.json_response({
                'success': True,
                'settings': settings
            })

        except Exception as e:
            logger.error(f"Error getting community settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def export_settings(self, request: web.Request) -> web.Response:
        """Export all settings for backup.

        GET /api/prompt_manager/settings/export
        """
        try:
            export_data = self.settings_service.export_settings()

            return web.json_response({
                'success': True,
                'data': export_data
            })

        except Exception as e:
            logger.error(f"Error exporting settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def import_settings(self, request: web.Request) -> web.Response:
        """Import settings from backup.

        POST /api/prompt_manager/settings/import
        Body: { data: {category: {key: value}}, overwrite?: boolean }
        """
        try:
            data = await request.json()
            settings_data = data.get('data', {})
            overwrite = data.get('overwrite', False)

            count = self.settings_service.import_settings(settings_data, overwrite)

            return web.json_response({
                'success': True,
                'imported': count,
                'message': f'Imported {count} settings'
            })

        except Exception as e:
            logger.error(f"Error importing settings: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)