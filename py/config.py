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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import logging system
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger

# Initialize logger for config operations
config_logger = get_logger("prompt_manager.config")

# Environment variable listing extra directories (os.pathsep-separated) that
# may be used as gallery roots in addition to ComfyUI's own directories.
EXTRA_GALLERY_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"

# Environment variable overriding where config.json is read from and saved to.
CONFIG_PATH_ENV = "PROMPT_MANAGER_CONFIG_PATH"

# ComfyUI folder_paths getters whose directories are valid gallery parents.
_COMFYUI_DIRECTORY_GETTERS = (
    "get_output_directory",
    "get_input_directory",
    "get_temp_directory",
    "get_user_directory",
)


def _canonical_path(path: str) -> str:
    """Return a path normalised for comparison (realpath + normcase)."""
    return os.path.normcase(os.path.realpath(path))


def _is_filesystem_root(path: str) -> bool:
    """True for '/' on POSIX and drive roots such as 'C:\\' on Windows."""
    return os.path.dirname(path) == path


def _import_folder_paths():
    """ComfyUI's folder_paths module, or None outside ComfyUI."""
    try:
        import folder_paths
    except ImportError:
        return None
    return folder_paths


def _comfyui_base_directory() -> Optional[str]:
    """ComfyUI's base directory: folder_paths.base_path, else the output dir's parent."""
    folder_paths = _import_folder_paths()
    if folder_paths is None:
        return None
    base = getattr(folder_paths, "base_path", None)
    if isinstance(base, str) and base:
        return base
    getter = getattr(folder_paths, "get_output_directory", None)
    if getter is None:
        return None
    try:
        output_dir = getter()
    except Exception:
        return None
    if isinstance(output_dir, str) and output_dir:
        return os.path.dirname(os.path.realpath(output_dir))
    return None


def _comfyui_directories() -> List[str]:
    """Directories reported by ComfyUI's folder_paths module, if importable."""
    folder_paths = _import_folder_paths()
    if folder_paths is None:
        return []

    found = []
    for getter_name in _COMFYUI_DIRECTORY_GETTERS:
        getter = getattr(folder_paths, getter_name, None)
        if getter is None:
            continue
        try:
            directory = getter()
        except Exception:
            continue
        if isinstance(directory, str) and directory:
            found.append(directory)
    return found


def _extra_gallery_roots() -> List[str]:
    """Directories the user opted in through PROMPT_MANAGER_EXTRA_GALLERY_ROOTS."""
    raw = os.environ.get(EXTRA_GALLERY_ROOTS_ENV, "")
    return [entry.strip() for entry in raw.split(os.pathsep) if entry.strip()]


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
    SUPPORTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    PROCESSING_DELAY = 2.0  # Seconds to wait before processing new files

    # Prompt tracking settings
    PROMPT_TIMEOUT = (
        600  # Seconds to keep prompt context active (10 min for long generations)
    )
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
            "monitoring": {
                "enabled": cls.MONITORING_ENABLED,
                "directories": cls.MONITORING_DIRECTORIES,
                "extensions": cls.SUPPORTED_EXTENSIONS,
                "processing_delay": cls.PROCESSING_DELAY,
            },
            "tracking": {
                "prompt_timeout": cls.PROMPT_TIMEOUT,
                "cleanup_interval": cls.CLEANUP_INTERVAL,
            },
            "database": {
                "auto_cleanup": cls.AUTO_CLEANUP_MISSING_FILES,
                "max_image_age_days": cls.MAX_IMAGE_AGE_DAYS,
            },
            "web_interface": {
                "images_per_page": cls.IMAGES_PER_PAGE,
                "thumbnail_size": cls.THUMBNAIL_SIZE,
                "enable_search": cls.ENABLE_SEARCH,
                "enable_metadata_view": cls.ENABLE_METADATA_VIEW,
            },
            "performance": {
                "max_concurrent_processing": cls.MAX_CONCURRENT_PROCESSING,
                "metadata_extraction_timeout": cls.METADATA_EXTRACTION_TIMEOUT,
            },
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
        monitoring = new_config.get("monitoring", {})
        if "enabled" in monitoring:
            cls.MONITORING_ENABLED = monitoring["enabled"]
        if "directories" in monitoring:
            cls.MONITORING_DIRECTORIES = monitoring["directories"]
        if "extensions" in monitoring:
            cls.SUPPORTED_EXTENSIONS = monitoring["extensions"]
        if "processing_delay" in monitoring:
            cls.PROCESSING_DELAY = monitoring["processing_delay"]

        tracking = new_config.get("tracking", {})
        if "prompt_timeout" in tracking:
            cls.PROMPT_TIMEOUT = tracking["prompt_timeout"]
        if "cleanup_interval" in tracking:
            cls.CLEANUP_INTERVAL = tracking["cleanup_interval"]

        database = new_config.get("database", {})
        if "auto_cleanup" in database:
            cls.AUTO_CLEANUP_MISSING_FILES = database["auto_cleanup"]
        if "max_image_age_days" in database:
            cls.MAX_IMAGE_AGE_DAYS = database["max_image_age_days"]

        web_interface = new_config.get("web_interface", {})
        if "images_per_page" in web_interface:
            cls.IMAGES_PER_PAGE = web_interface["images_per_page"]
        if "thumbnail_size" in web_interface:
            cls.THUMBNAIL_SIZE = web_interface["thumbnail_size"]
        if "enable_search" in web_interface:
            cls.ENABLE_SEARCH = web_interface["enable_search"]
        if "enable_metadata_view" in web_interface:
            cls.ENABLE_METADATA_VIEW = web_interface["enable_metadata_view"]

        performance = new_config.get("performance", {})
        if "max_concurrent_processing" in performance:
            cls.MAX_CONCURRENT_PROCESSING = performance["max_concurrent_processing"]
        if "metadata_extraction_timeout" in performance:
            cls.METADATA_EXTRACTION_TIMEOUT = performance["metadata_extraction_timeout"]

    @classmethod
    def allowed_gallery_parents(cls) -> List[str]:
        """Canonical directories under which gallery roots may live.

        Filesystem roots are never allowed as parents, even when listed in
        the environment, because that would re-open the whole disk.
        """
        parents = []
        for candidate in _comfyui_directories() + _extra_gallery_roots():
            try:
                canonical = _canonical_path(candidate)
            except (OSError, ValueError):
                continue
            if _is_filesystem_root(canonical) or not os.path.isdir(canonical):
                continue
            if canonical not in parents:
                parents.append(canonical)
        return parents

    @classmethod
    def path_anchors(cls) -> List[str]:
        """Canonical directories that relative gallery paths are resolved against.

        The ComfyUI base directory comes first, followed by the parent of each
        directory listed in PROMPT_MANAGER_EXTRA_GALLERY_ROOTS, so an extra
        root is addressed by its own name (``gallery/sub``) rather than by an
        absolute path. The same anchors drive the public (relative) form of
        paths in API responses.
        """
        anchors = []
        base = _comfyui_base_directory()
        candidates = [base] if base else []
        candidates.extend(os.path.dirname(root) for root in _extra_gallery_roots())
        for candidate in candidates:
            try:
                canonical = _canonical_path(candidate)
            except (OSError, ValueError):
                continue
            if canonical not in anchors:
                anchors.append(canonical)
        return anchors

    @classmethod
    def resolve_gallery_root(cls, path: str) -> str:
        """Canonical absolute path for a gallery root given in any accepted form.

        Absolute paths are canonicalised as-is. Relative paths are tried
        against each :meth:`path_anchors` entry and the first existing match
        wins; otherwise the first anchor (or the current directory when there
        is none) is used, so the caller's existence check reports it.
        """
        path = path.strip()
        if os.path.isabs(path):
            return _canonical_path(path)
        anchors = cls.path_anchors()
        for anchor in anchors:
            candidate = os.path.join(anchor, path)
            if os.path.exists(candidate):
                return _canonical_path(candidate)
        return _canonical_path(os.path.join(anchors[0], path) if anchors else path)

    @classmethod
    def validate_gallery_root(cls, path: Any) -> Tuple[bool, str]:
        """Check whether ``path`` may be used as a gallery root.

        Returns:
            (True, "") when the directory lies inside one of
            :meth:`allowed_gallery_parents`; otherwise (False, reason).
        """
        if not isinstance(path, str) or not path.strip():
            return False, "Gallery root must be a non-empty path"

        try:
            canonical = cls.resolve_gallery_root(path)
        except (OSError, ValueError):
            return False, "Gallery root could not be resolved"

        if _is_filesystem_root(canonical):
            return False, "Gallery root cannot be a filesystem root"
        if canonical == _canonical_path(os.path.expanduser("~")):
            return False, "Gallery root cannot be the home directory"
        if not os.path.exists(canonical):
            return False, "Gallery root does not exist"
        if not os.path.isdir(canonical):
            return False, "Gallery root is not a directory"

        candidate = Path(canonical)
        for parent in cls.allowed_gallery_parents():
            if candidate.is_relative_to(Path(parent)):
                return True, ""

        return (
            False,
            "Gallery root must be inside a ComfyUI directory "
            f"(output, input, temp, user) or one listed in {EXTRA_GALLERY_ROOTS_ENV}",
        )


class IntegrationConfig:
    """Configuration for third-party extension integrations.

    Manages opt-in integration settings for extensions like LoraManager.
    All integrations are disabled by default so PromptManager works standalone.
    """

    # LoraManager integration
    LORA_MANAGER_ENABLED = False
    LORA_MANAGER_PATH = ""  # Auto-detected if empty
    LORA_TRIGGER_WORDS_ENABLED = False  # Auto-inject trigger words into prompts
    CIVITAI_API_KEY = ""  # Required to download NSFW example images

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        return {
            "lora_manager": {
                "enabled": cls.LORA_MANAGER_ENABLED,
                "path": cls.LORA_MANAGER_PATH,
                "trigger_words_enabled": cls.LORA_TRIGGER_WORDS_ENABLED,
                "civitai_api_key": cls.CIVITAI_API_KEY,
            },
        }

    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        lora = new_config.get("lora_manager", {})
        if "enabled" in lora:
            cls.LORA_MANAGER_ENABLED = lora["enabled"]
        if "path" in lora:
            cls.LORA_MANAGER_PATH = lora["path"]
        if "trigger_words_enabled" in lora:
            cls.LORA_TRIGGER_WORDS_ENABLED = lora["trigger_words_enabled"]
        if "civitai_api_key" in lora:
            cls.CIVITAI_API_KEY = lora["civitai_api_key"]


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
    WEBUI_DISPLAY_MODE = "newtab"  # 'popup' or 'newtab'

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
            "database": {
                "default_path": cls.DEFAULT_DB_PATH,
                "enable_duplicate_detection": cls.ENABLE_DUPLICATE_DETECTION,
                "enable_auto_save": cls.ENABLE_AUTO_SAVE,
            },
            "web_ui": {
                "result_timeout": cls.RESULT_TIMEOUT,
                "show_test_button": cls.SHOW_TEST_BUTTON,
                "webui_display_mode": cls.WEBUI_DISPLAY_MODE,
            },
            "performance": {
                "max_search_results": cls.MAX_SEARCH_RESULTS,
                "enable_fuzzy_search": cls.ENABLE_FUZZY_SEARCH,
                "auto_backup_interval": cls.AUTO_BACKUP_INTERVAL,
            },
            "gallery": GalleryConfig.get_config(),
            "integrations": IntegrationConfig.get_config(),
        }

    @classmethod
    def get_config_path(cls) -> str:
        """Path of the persisted config.json.

        Honours ``PROMPT_MANAGER_CONFIG_PATH`` when set; otherwise the file
        lives at the repository root next to ``pyproject.toml``.
        """
        override = os.environ.get(CONFIG_PATH_ENV, "").strip()
        if override:
            return override
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo_root, "config.json")

    @classmethod
    def load_from_file(cls, config_path: Optional[str] = None):
        """Load configuration settings from a JSON file.

        Reads configuration from the specified JSON file and updates the current
        configuration attributes. If the file doesn't exist or contains invalid
        JSON, logs an appropriate message and continues with default values.

        Args:
            config_path (str): Path to the JSON configuration file to load.
                            Can be relative or absolute path. Defaults to
                            :meth:`get_config_path`.

        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them, ensuring the system continues with defaults.

        Example:
            PromptManagerConfig.load_from_file('custom_config.json')
            PromptManagerConfig.load_from_file('/path/to/config.json')
        """
        import json

        if config_path is None:
            config_path = cls.get_config_path()

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                cls.update_config(config)
                config_logger.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                config_logger.error(f"Error loading config from {config_path}: {e}")
        else:
            config_logger.info(f"Config file not found: {config_path}, using defaults")

    @classmethod
    def save_to_file(cls, config_path: Optional[str] = None):
        """Save the current configuration to a JSON file.

        Serializes the complete configuration (including gallery settings) to
        a JSON file. Creates the directory structure if it doesn't exist.

        Args:
            config_path (str): Path where the JSON configuration file should be saved.
                            Parent directories will be created if they don't exist.
                            Defaults to :meth:`get_config_path`.

        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them.

        Example:
            PromptManagerConfig.save_to_file('backup_config.json')
            PromptManagerConfig.save_to_file('/etc/comfyui/prompt_manager.json')
        """
        import json

        if config_path is None:
            config_path = cls.get_config_path()

        try:
            config = cls.get_config()
            parent_dir = os.path.dirname(config_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            with open(config_path, "w") as f:
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
        database = new_config.get("database", {})
        if "default_path" in database:
            cls.DEFAULT_DB_PATH = database["default_path"]
        if "enable_duplicate_detection" in database:
            cls.ENABLE_DUPLICATE_DETECTION = database["enable_duplicate_detection"]
        if "enable_auto_save" in database:
            cls.ENABLE_AUTO_SAVE = database["enable_auto_save"]

        web_ui = new_config.get("web_ui", {})
        if "result_timeout" in web_ui:
            cls.RESULT_TIMEOUT = web_ui["result_timeout"]
        if "show_test_button" in web_ui:
            cls.SHOW_TEST_BUTTON = web_ui["show_test_button"]
        if "webui_display_mode" in web_ui:
            cls.WEBUI_DISPLAY_MODE = web_ui["webui_display_mode"]

        performance = new_config.get("performance", {})
        if "max_search_results" in performance:
            cls.MAX_SEARCH_RESULTS = performance["max_search_results"]
        if "enable_fuzzy_search" in performance:
            cls.ENABLE_FUZZY_SEARCH = performance["enable_fuzzy_search"]
        if "auto_backup_interval" in performance:
            cls.AUTO_BACKUP_INTERVAL = performance["auto_backup_interval"]

        # Update gallery config
        if "gallery" in new_config:
            GalleryConfig.update_config(new_config["gallery"])

        # Update integration config
        if "integrations" in new_config:
            IntegrationConfig.update_config(new_config["integrations"])


# Load configuration on import
try:
    PromptManagerConfig.load_from_file()
except Exception as e:
    config_logger.error(f"Error during config initialization: {e}")
