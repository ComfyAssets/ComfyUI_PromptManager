# PromptManager/py/config.py

# Extension configuration
extension_name = "PromptManager"

# Get server instance and routes (same pattern as ComfyUI_Assets)
from server import PromptServer
server_instance = PromptServer.instance
routes = server_instance.routes

# Extension info
extension_uri = None  # Will be set in __init__.py

"""
Configuration settings for PromptManager gallery and monitoring system.
"""

import os
from typing import Dict, Any, List


class GalleryConfig:
    """Configuration for the gallery monitoring system."""
    
    # Image monitoring settings
    MONITORING_ENABLED = True
    MONITORING_DIRECTORIES = []  # Auto-detect if empty
    SUPPORTED_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
    PROCESSING_DELAY = 2.0  # Seconds to wait before processing new files
    
    # Prompt tracking settings
    PROMPT_TIMEOUT = 120  # Seconds to keep prompt context active
    CLEANUP_INTERVAL = 300  # Seconds between cleanup of expired prompts
    
    # Database settings
    AUTO_CLEANUP_MISSING_FILES = True
    MAX_IMAGE_AGE_DAYS = 365  # Clean up images older than this
    
    # Web interface settings
    IMAGES_PER_PAGE = 20
    THUMBNAIL_SIZE = 256
    ENABLE_SEARCH = True
    ENABLE_METADATA_VIEW = True
    
    # Performance settings
    MAX_CONCURRENT_PROCESSING = 3
    METADATA_EXTRACTION_TIMEOUT = 10  # Seconds
    
    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get the complete configuration as a dictionary."""
        return {
            'monitoring': {
                'enabled': cls.MONITORING_ENABLED,
                'directories': cls.MONITORING_DIRECTORIES,
                'extensions': cls.SUPPORTED_EXTENSIONS,
                'processing_delay': cls.PROCESSING_DELAY
            },
            'tracking': {
                'prompt_timeout': cls.PROMPT_TIMEOUT,
                'cleanup_interval': cls.CLEANUP_INTERVAL
            },
            'database': {
                'auto_cleanup': cls.AUTO_CLEANUP_MISSING_FILES,
                'max_image_age_days': cls.MAX_IMAGE_AGE_DAYS
            },
            'web_interface': {
                'images_per_page': cls.IMAGES_PER_PAGE,
                'thumbnail_size': cls.THUMBNAIL_SIZE,
                'enable_search': cls.ENABLE_SEARCH,
                'enable_metadata_view': cls.ENABLE_METADATA_VIEW
            },
            'performance': {
                'max_concurrent_processing': cls.MAX_CONCURRENT_PROCESSING,
                'metadata_extraction_timeout': cls.METADATA_EXTRACTION_TIMEOUT
            }
        }


class PromptManagerConfig:
    """General configuration for PromptManager."""
    
    # Database settings
    DEFAULT_DB_PATH = "prompts.db"
    ENABLE_DUPLICATE_DETECTION = True
    ENABLE_AUTO_SAVE = True
    
    # Web UI settings
    RESULT_TIMEOUT = 5  # Seconds to auto-hide results in ComfyUI node
    SHOW_TEST_BUTTON = False  # Show API test button in node UI
    WEBUI_DISPLAY_MODE = 'popup'  # 'popup' or 'newtab'
    
    # Performance settings
    MAX_SEARCH_RESULTS = 100
    ENABLE_FUZZY_SEARCH = False  # Requires fuzzywuzzy
    AUTO_BACKUP_INTERVAL = 24  # Hours
    
    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get the complete configuration as a dictionary."""
        return {
            'database': {
                'default_path': cls.DEFAULT_DB_PATH,
                'enable_duplicate_detection': cls.ENABLE_DUPLICATE_DETECTION,
                'enable_auto_save': cls.ENABLE_AUTO_SAVE
            },
            'web_ui': {
                'result_timeout': cls.RESULT_TIMEOUT,
                'show_test_button': cls.SHOW_TEST_BUTTON,
                'webui_display_mode': cls.WEBUI_DISPLAY_MODE
            },
            'performance': {
                'max_search_results': cls.MAX_SEARCH_RESULTS,
                'enable_fuzzy_search': cls.ENABLE_FUZZY_SEARCH,
                'auto_backup_interval': cls.AUTO_BACKUP_INTERVAL
            },
            'gallery': GalleryConfig.get_config()
        }
    
    @classmethod
    def load_from_file(cls, config_path: str):
        """Load configuration from a JSON file."""
        import json
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                cls.update_config(config)
                print(f"[PromptManager] Loaded configuration from {config_path}")
            except Exception as e:
                print(f"[PromptManager] Error loading config from {config_path}: {e}")
        else:
            print(f"[PromptManager] Config file not found: {config_path}, using defaults")
    
    @classmethod
    def save_to_file(cls, config_path: str):
        """Save current configuration to a JSON file."""
        import json
        
        try:
            config = cls.get_config()
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            print(f"[PromptManager] Saved configuration to {config_path}")
        except Exception as e:
            print(f"[PromptManager] Error saving config to {config_path}: {e}")
    
    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        """Update configuration from a dictionary."""
        database = new_config.get('database', {})
        if 'default_path' in database:
            cls.DEFAULT_DB_PATH = database['default_path']
        if 'enable_duplicate_detection' in database:
            cls.ENABLE_DUPLICATE_DETECTION = database['enable_duplicate_detection']
        if 'enable_auto_save' in database:
            cls.ENABLE_AUTO_SAVE = database['enable_auto_save']
        
        web_ui = new_config.get('web_ui', {})
        if 'result_timeout' in web_ui:
            cls.RESULT_TIMEOUT = web_ui['result_timeout']
        if 'show_test_button' in web_ui:
            cls.SHOW_TEST_BUTTON = web_ui['show_test_button']
        if 'webui_display_mode' in web_ui:
            cls.WEBUI_DISPLAY_MODE = web_ui['webui_display_mode']
        
        performance = new_config.get('performance', {})
        if 'max_search_results' in performance:
            cls.MAX_SEARCH_RESULTS = performance['max_search_results']
        if 'enable_fuzzy_search' in performance:
            cls.ENABLE_FUZZY_SEARCH = performance['enable_fuzzy_search']
        if 'auto_backup_interval' in performance:
            cls.AUTO_BACKUP_INTERVAL = performance['auto_backup_interval']
        
        # Update gallery config
        if 'gallery' in new_config:
            GalleryConfig.update_config(new_config['gallery'])


# Load configuration on import
try:
    config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_file = os.path.join(config_dir, 'config.json')
    PromptManagerConfig.load_from_file(config_file)
except Exception as e:
    print(f"[PromptManager] Error during config initialization: {e}")