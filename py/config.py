"""Configuration module for ComfyUI PromptManager.

This module provides centralized configuration management for the PromptManager
extension, including gallery monitoring settings, database configuration,
web interface options, and performance tuning parameters.

The configuration is organized into two main classes:
- GalleryConfig: Settings for image monitoring and gallery functionality
- PromptManagerConfig: General settings for the PromptManager core features

Configuration can be loaded from and saved to JSON files for persistence.

Example:
    from config import PromptManagerConfig
    config = PromptManagerConfig.get_config()
    PromptManagerConfig.load_from_file('custom_config.json')
"""

# PromptManager/py/config.py

# Extension configuration
extension_name = "PromptManager"

# Get server instance and routes (same pattern as ComfyUI_Assets)
from server import PromptServer
server_instance = PromptServer.instance
routes = server_instance.routes

# Extension info
extension_uri = None  # Will be set in __init__.py

import os
from typing import Dict, Any, List

# Import logging system
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger

# Initialize logger for config operations
config_logger = get_logger('prompt_manager.config')


class GalleryConfig:
    """Configuration class for the gallery monitoring and image processing system.
    
    This class manages all settings related to automatic image monitoring,
    prompt tracking, database cleanup, web interface display, and performance
    optimization for the gallery functionality.
    
    All configuration values are class attributes that can be modified at runtime
    or loaded from external configuration files.
    
    Attributes:
        MONITORING_ENABLED (bool): Enable/disable automatic image monitoring
        MONITORING_DIRECTORIES (List[str]): Directories to monitor for new images
        SUPPORTED_EXTENSIONS (List[str]): Image file extensions to process
        PROCESSING_DELAY (float): Delay in seconds before processing new files
        PROMPT_TIMEOUT (int): Seconds to keep prompt context active
        CLEANUP_INTERVAL (int): Seconds between cleanup of expired prompts
        AUTO_CLEANUP_MISSING_FILES (bool): Automatically remove missing file records
        MAX_IMAGE_AGE_DAYS (int): Maximum age in days before cleaning up images
        IMAGES_PER_PAGE (int): Number of images to display per page in web UI
        THUMBNAIL_SIZE (int): Size in pixels for generated thumbnails
        ENABLE_SEARCH (bool): Enable search functionality in web interface
        ENABLE_METADATA_VIEW (bool): Enable metadata viewing for images
        MAX_CONCURRENT_PROCESSING (int): Maximum concurrent image processing tasks
        METADATA_EXTRACTION_TIMEOUT (int): Timeout for metadata extraction operations
    """
    
    # Image monitoring settings
    MONITORING_ENABLED = True
    MONITORING_DIRECTORIES = []  # Auto-detect if empty
    SUPPORTED_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
    PROCESSING_DELAY = 2.0  # Seconds to wait before processing new files
    
    # Prompt tracking settings
    PROMPT_TIMEOUT = 600  # Seconds to keep prompt context active (10 min for long generations)
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
        """Get the complete gallery configuration as a structured dictionary.
        
        Returns:
            Dict[str, Any]: A nested dictionary containing all gallery configuration
                sections: monitoring, tracking, database, web_interface, and performance.
                Each section contains the relevant configuration parameters as key-value pairs.
        
        Example:
            config = GalleryConfig.get_config()
            monitoring_enabled = config['monitoring']['enabled']
            images_per_page = config['web_interface']['images_per_page']
        """
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
    
    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        """Update gallery configuration attributes from a dictionary.
        
        Takes a nested dictionary with gallery configuration sections and updates
        the corresponding class attributes. Only updates attributes that are
        present in the input dictionary, leaving others unchanged.
        
        Args:
            new_config (Dict[str, Any]): Nested dictionary containing gallery
                configuration updates. Should follow the same structure as returned
                by get_config(). Valid top-level keys are: 'monitoring', 'tracking',
                'database', 'web_interface', 'performance'.
        
        Example:
            gallery_settings = {
                'monitoring': {'enabled': False},
                'web_interface': {'images_per_page': 50}
            }
            GalleryConfig.update_config(gallery_settings)
        """
        monitoring = new_config.get('monitoring', {})
        if 'enabled' in monitoring:
            cls.MONITORING_ENABLED = monitoring['enabled']
        if 'directories' in monitoring:
            cls.MONITORING_DIRECTORIES = monitoring['directories']
        if 'extensions' in monitoring:
            cls.SUPPORTED_EXTENSIONS = monitoring['extensions']
        if 'processing_delay' in monitoring:
            cls.PROCESSING_DELAY = monitoring['processing_delay']
        
        tracking = new_config.get('tracking', {})
        if 'prompt_timeout' in tracking:
            cls.PROMPT_TIMEOUT = tracking['prompt_timeout']
        if 'cleanup_interval' in tracking:
            cls.CLEANUP_INTERVAL = tracking['cleanup_interval']
        
        database = new_config.get('database', {})
        if 'auto_cleanup' in database:
            cls.AUTO_CLEANUP_MISSING_FILES = database['auto_cleanup']
        if 'max_image_age_days' in database:
            cls.MAX_IMAGE_AGE_DAYS = database['max_image_age_days']
        
        web_interface = new_config.get('web_interface', {})
        if 'images_per_page' in web_interface:
            cls.IMAGES_PER_PAGE = web_interface['images_per_page']
        if 'thumbnail_size' in web_interface:
            cls.THUMBNAIL_SIZE = web_interface['thumbnail_size']
        if 'enable_search' in web_interface:
            cls.ENABLE_SEARCH = web_interface['enable_search']
        if 'enable_metadata_view' in web_interface:
            cls.ENABLE_METADATA_VIEW = web_interface['enable_metadata_view']
        
        performance = new_config.get('performance', {})
        if 'max_concurrent_processing' in performance:
            cls.MAX_CONCURRENT_PROCESSING = performance['max_concurrent_processing']
        if 'metadata_extraction_timeout' in performance:
            cls.METADATA_EXTRACTION_TIMEOUT = performance['metadata_extraction_timeout']


class PromptManagerConfig:
    """Main configuration class for PromptManager core functionality.
    
    This class manages configuration for database operations, web UI behavior,
    performance settings, and integrates gallery configuration. It provides
    methods for loading and saving configuration from/to JSON files.
    
    The configuration is organized into logical sections:
    - Database: Settings for SQLite operations and data management
    - Web UI: User interface behavior and display options
    - Performance: Optimization and resource management settings
    - Gallery: Embedded gallery configuration (via GalleryConfig)
    
    Attributes:
        DEFAULT_DB_PATH (str): Default path for the SQLite database file
        ENABLE_DUPLICATE_DETECTION (bool): Enable automatic duplicate detection
        ENABLE_AUTO_SAVE (bool): Enable automatic saving of prompts
        RESULT_TIMEOUT (int): Auto-hide timeout for results in ComfyUI node
        SHOW_TEST_BUTTON (bool): Show API test button in node interface
        WEBUI_DISPLAY_MODE (str): Display mode for web UI ('popup' or 'newtab')
        MAX_SEARCH_RESULTS (int): Maximum number of search results to return
        ENABLE_FUZZY_SEARCH (bool): Enable fuzzy search capabilities
        AUTO_BACKUP_INTERVAL (int): Hours between automatic database backups
    """
    
    # Database settings
    DEFAULT_DB_PATH = "prompts.db"
    ENABLE_DUPLICATE_DETECTION = True
    ENABLE_AUTO_SAVE = True
    
    # Web UI settings
    RESULT_TIMEOUT = 5  # Seconds to auto-hide results in ComfyUI node
    SHOW_TEST_BUTTON = False  # Show API test button in node UI
    WEBUI_DISPLAY_MODE = 'newtab'  # 'popup' or 'newtab'
    
    # Performance settings
    MAX_SEARCH_RESULTS = 100
    ENABLE_FUZZY_SEARCH = False  # Requires fuzzywuzzy
    AUTO_BACKUP_INTERVAL = 24  # Hours
    
    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get the complete PromptManager configuration as a structured dictionary.
        
        Returns:
            Dict[str, Any]: A nested dictionary containing all configuration sections:
                - database: Database-related settings
                - web_ui: Web interface configuration
                - performance: Performance and optimization settings
                - gallery: Complete gallery configuration (from GalleryConfig)
        
        Example:
            config = PromptManagerConfig.get_config()
            db_path = config['database']['default_path']
            max_results = config['performance']['max_search_results']
        """
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
        """Load configuration settings from a JSON file.
        
        Reads configuration from the specified JSON file and updates the current
        configuration attributes. If the file doesn't exist or contains invalid
        JSON, logs an appropriate message and continues with default values.
        
        Args:
            config_path (str): Path to the JSON configuration file to load.
                            Can be relative or absolute path.
        
        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them, ensuring the system continues with defaults.
        
        Example:
            PromptManagerConfig.load_from_file('custom_config.json')
            PromptManagerConfig.load_from_file('/path/to/config.json')
        """
        import json
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                cls.update_config(config)
                config_logger.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                config_logger.error(f"Error loading config from {config_path}: {e}")
        else:
            config_logger.info(f"Config file not found: {config_path}, using defaults")
    
    @classmethod
    def save_to_file(cls, config_path: str):
        """Save the current configuration to a JSON file.
        
        Serializes the complete configuration (including gallery settings) to
        a JSON file. Creates the directory structure if it doesn't exist.
        
        Args:
            config_path (str): Path where the JSON configuration file should be saved.
                            Parent directories will be created if they don't exist.
        
        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them.
        
        Example:
            PromptManagerConfig.save_to_file('backup_config.json')
            PromptManagerConfig.save_to_file('/etc/comfyui/prompt_manager.json')
        """
        import json
        
        try:
            config = cls.get_config()
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            config_logger.info(f"Saved configuration to {config_path}")
        except Exception as e:
            config_logger.error(f"Error saving config to {config_path}: {e}")
    
    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        """Update configuration attributes from a dictionary.
        
        Takes a nested dictionary with configuration sections and updates
        the corresponding class attributes. Only updates attributes that
        are present in the input dictionary, leaving others unchanged.
        
        Args:
            new_config (Dict[str, Any]): Nested dictionary containing configuration
                updates. Should follow the same structure as returned by get_config().
                Valid top-level keys are: 'database', 'web_ui', 'performance', 'gallery'.
        
        Example:
            new_settings = {
                'database': {'default_path': 'custom.db'},
                'performance': {'max_search_results': 50}
            }
            PromptManagerConfig.update_config(new_settings)
        """
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
    config_logger.error(f"Error during config initialization: {e}")