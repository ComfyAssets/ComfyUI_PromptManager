"""Application settings service for managing key-value configuration."""

import sqlite3
import json
import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class SettingsService:
    """Service for managing application settings in key-value storage."""

    def __init__(self, db_path: str):
        """Initialize the settings service.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key.

        Args:
            key: Setting key
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
                result = cursor.fetchone()

                if result:
                    # Try to parse as JSON for complex types
                    try:
                        return json.loads(result[0])
                    except (json.JSONDecodeError, TypeError):
                        return result[0]

                return default

        except Exception as e:
            logger.error(f"Error getting setting {key}: {e}")
            return default

    def set(self, key: str, value: Any, category: str = 'general', description: str = None) -> bool:
        """Set a setting value.

        Args:
            key: Setting key
            value: Setting value (will be JSON serialized if complex type)
            category: Setting category for organization
            description: Optional description of the setting

        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert value to string (JSON for complex types)
            if isinstance(value, (dict, list, bool)):
                value_str = json.dumps(value)
            else:
                value_str = str(value)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Use INSERT OR REPLACE to handle both insert and update
                cursor.execute("""
                    INSERT OR REPLACE INTO app_settings (key, value, category, description)
                    VALUES (?, ?, ?, COALESCE(?,
                        (SELECT description FROM app_settings WHERE key = ?)))
                """, (key, value_str, category, description, key))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            return False

    def get_category(self, category: str) -> Dict[str, Any]:
        """Get all settings in a category.

        Args:
            category: Setting category

        Returns:
            Dictionary of key-value pairs
        """
        settings = {}

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT key, value FROM app_settings WHERE category = ?",
                    (category,)
                )

                for key, value in cursor.fetchall():
                    try:
                        settings[key] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        settings[key] = value

        except Exception as e:
            logger.error(f"Error getting category {category}: {e}")

        return settings

    def delete(self, key: str) -> bool:
        """Delete a setting.

        Args:
            key: Setting key to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM app_settings WHERE key = ?", (key,))
                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error deleting setting {key}: {e}")
            return False

    def generate_uuid(self) -> str:
        """Generate and save a new PromptManager UUID.

        Returns:
            Generated UUID string
        """
        new_uuid = str(uuid.uuid4())

        if self.set('promptmanager_uuid', new_uuid, 'community', 'Unique identifier for PromptManager instance'):
            logger.info(f"Generated new PromptManager UUID: {new_uuid}")
            return new_uuid
        else:
            logger.error("Failed to save generated UUID")
            return ""

    def get_or_generate_uuid(self) -> str:
        """Get existing UUID or generate a new one if not exists.

        Returns:
            UUID string
        """
        existing_uuid = self.get('promptmanager_uuid', '')

        if not existing_uuid:
            return self.generate_uuid()

        return existing_uuid

    def set_civitai_api_key(self, api_key: str) -> bool:
        """Set the CivitAI API key.

        Args:
            api_key: CivitAI API key

        Returns:
            True if successful, False otherwise
        """
        return self.set(
            'civitai_api_key',
            api_key,
            'community',
            'CivitAI API key for model downloads and metadata'
        )

    def get_civitai_api_key(self) -> str:
        """Get the CivitAI API key.

        Returns:
            API key string or empty string if not set
        """
        return self.get('civitai_api_key', '')

    def get_all_settings(self) -> List[Dict[str, Any]]:
        """Get all settings with metadata.

        Returns:
            List of setting dictionaries
        """
        settings = []

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT key, value, category, description, updated_at
                    FROM app_settings
                    ORDER BY category, key
                """)

                for row in cursor.fetchall():
                    setting = dict(row)
                    # Try to parse value as JSON
                    try:
                        setting['value'] = json.loads(setting['value'])
                    except (json.JSONDecodeError, TypeError):
                        pass

                    settings.append(setting)

        except Exception as e:
            logger.error(f"Error getting all settings: {e}")

        return settings

    def export_settings(self) -> Dict[str, Any]:
        """Export all settings for backup.

        Returns:
            Dictionary of all settings grouped by category
        """
        export = {}

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT key, value, category
                    FROM app_settings
                    ORDER BY category, key
                """)

                for key, value, category in cursor.fetchall():
                    if category not in export:
                        export[category] = {}

                    try:
                        export[category][key] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        export[category][key] = value

        except Exception as e:
            logger.error(f"Error exporting settings: {e}")

        return export

    def import_settings(self, settings: Dict[str, Any], overwrite: bool = False) -> int:
        """Import settings from backup.

        Args:
            settings: Dictionary of settings grouped by category
            overwrite: Whether to overwrite existing settings

        Returns:
            Number of settings imported
        """
        count = 0

        try:
            for category, category_settings in settings.items():
                for key, value in category_settings.items():
                    if overwrite or not self.get(key):
                        if self.set(key, value, category):
                            count += 1

        except Exception as e:
            logger.error(f"Error importing settings: {e}")

        return count