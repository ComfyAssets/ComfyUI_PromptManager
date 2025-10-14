"""Configuration management for PromptManager.

This module handles all configuration settings for the application,
including database paths, API settings, and ComfyUI integration.
"""

import os
import ntpath
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
import json

from utils.core.file_system import get_file_system


_fs = get_file_system()

# Lazy evaluation - only compute when needed to avoid startup errors
def _get_default_user_dir():
    try:
        return _fs.get_user_dir()
    except Exception as e:
        # DO NOT create directories outside ComfyUI structure
        # Raise the error so user knows to fix their setup
        raise RuntimeError(
            f"Cannot determine ComfyUI user directory: {e}\n"
            "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
            "or set COMFYUI_PATH environment variable."
        )

def _get_default_db_path():
    try:
        return str((_fs.get_user_dir() / "prompts.db").resolve())
    except Exception as e:
        # DO NOT create database outside ComfyUI structure
        raise RuntimeError(
            f"Cannot determine database path: {e}\n"
            "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
            "or set COMFYUI_PATH environment variable."
        )

def _get_default_log_file():
    try:
        return str((_fs.get_logs_dir() / "promptmanager.log").resolve())
    except Exception as e:
        # DO NOT create log files outside ComfyUI structure
        raise RuntimeError(
            f"Cannot determine log file path: {e}\n"
            "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
            "or set COMFYUI_PATH environment variable."
        )


@dataclass
class DatabaseConfig:
    """Database configuration settings."""
    
    # Use user directory to avoid polluting ComfyUI installation
    path: str = field(default_factory=_get_default_db_path)
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 3600
    
    @property
    def url(self) -> str:
        """Get database URL."""
        return f"sqlite:///{self.path}"


@dataclass
class APIConfig:
    """API configuration settings."""
    
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False
    cors_origins: list = field(default_factory=lambda: ["*"])
    rate_limit: int = 100  # requests per minute
    cache_ttl: int = 300  # seconds
    max_page_size: int = 100
    default_page_size: int = 20


@dataclass
class WebSocketConfig:
    """WebSocket configuration settings."""
    
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8766
    ping_interval: int = 30
    ping_timeout: int = 10
    max_connections: int = 100
    message_queue_size: int = 1000


@dataclass
class ComfyUIConfig:
    """ComfyUI integration configuration."""
    
    enabled: bool = True
    server_address: str = "127.0.0.1:8188"
    client_id: str = "promptmanager"
    auto_track: bool = True
    track_metadata: bool = True
    extract_workflow: bool = True


@dataclass
class StorageConfig:
    """Storage configuration settings."""
    
    # Use user directory to avoid polluting ComfyUI installation
    base_path: str = field(default_factory=lambda: str(_get_default_user_dir()))
    images_path: str = "images"
    thumbnails_path: str = "thumbnails"
    exports_path: str = "exports"
    temp_path: str = "temp"
    
    def __post_init__(self):
        """Create storage directories if they don't exist."""
        base = Path(self.base_path)

        # Verify we're inside ComfyUI structure before creating directories
        # The base_path should already be validated by _get_default_user_dir()
        # but double-check to prevent accidental directory creation
        
        # Cross-platform absolute path check:
        # - os.path.isabs() for current platform
        # - ntpath.isabs() for Windows paths (works on Linux too)
        path_str = str(base)
        if not (os.path.isabs(path_str) or ntpath.isabs(path_str)):
            # Relative path - likely incorrect
            raise RuntimeError(
                f"Invalid storage base path: {base}. "
                "Must be an absolute path within ComfyUI structure."
            )

        # Only create directories if base_path looks valid
        # (contains 'ComfyUI' or 'user' in the path)
        path_str_lower = path_str.lower()
        if 'comfyui' not in path_str_lower and 'user' not in path_str_lower:
            raise RuntimeError(
                f"Storage path '{base}' doesn't appear to be within ComfyUI structure. "
                "Refusing to create directories in potentially incorrect location."
            )

        for subdir in [self.images_path, self.thumbnails_path,
                      self.exports_path, self.temp_path]:
            path = base / subdir
            path.mkdir(parents=True, exist_ok=True)
    
    def get_path(self, subdir: str) -> Path:
        """Get full path for subdirectory.
        
        Args:
            subdir: Subdirectory name
            
        Returns:
            Full path
        """
        return Path(self.base_path) / subdir


@dataclass
class LoggingConfig:
    """Logging configuration settings."""
    
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = field(default_factory=_get_default_log_file)
    console: bool = True
    max_bytes: int = 10485760  # 10MB
    backup_count: int = 5


@dataclass
class UIConfig:
    """UI configuration settings."""

    theme: str = "dark"
    language: str = "en"
    items_per_page: int = 20
    gallery_columns: int = 4
    thumbnail_size: tuple = (256, 256)
    lazy_load: bool = True
    show_metadata: bool = True
    compact_mode: bool = False


class Config:
    """Main configuration class."""
    
    def __init__(self, config_file: Optional[str] = None):
        """Initialize configuration.
        
        Args:
            config_file: Path to configuration file
        """
        self.config_file = config_file or self._find_config_file()
        self._extra_settings: Dict[str, Any] = {}
        
        # Initialize sub-configs with defaults
        self.database = DatabaseConfig()
        self.api = APIConfig()
        self.websocket = WebSocketConfig()
        self.comfyui = ComfyUIConfig()
        self.storage = StorageConfig()
        self.logging = LoggingConfig()
        self.ui = UIConfig()
        
        # Load from file if exists
        if self.config_file and os.path.exists(self.config_file):
            self.load(self.config_file)

        # Override with environment variables
        self._load_from_env()

        if not hasattr(self, '_initialized_once'):
            self._initialized_once = True
    
    def _find_config_file(self) -> Optional[str]:
        """Find configuration file in standard locations.
        
        Returns:
            Path to config file or None
        """
        search_paths = [
            "promptmanager.json",
            "config.json",
            os.path.expanduser("~/.promptmanager/config.json"),
            "/etc/promptmanager/config.json"
        ]
        
        for path in search_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def load(self, config_file: str):
        """Load configuration from file.
        
        Args:
            config_file: Path to configuration file
        """
        with open(config_file, 'r') as f:
            data = json.load(f)

        # Load database config
        if "database" in data:
            self.database = DatabaseConfig(**data["database"])

        # Load API config
        if "api" in data:
            self.api = APIConfig(**data["api"])

        # Load WebSocket config
        if "websocket" in data:
            self.websocket = WebSocketConfig(**data["websocket"])

        # Load ComfyUI config
        if "comfyui" in data:
            self.comfyui = ComfyUIConfig(**data["comfyui"])

        # Load storage config
        if "storage" in data:
            self.storage = StorageConfig(**data["storage"])

        # Load logging config
        if "logging" in data:
            self.logging = LoggingConfig(**data["logging"])

        # Load UI config
        if "ui" in data:
            self.ui = UIConfig(**data["ui"])

        settings = data.get("settings", {})
        if isinstance(settings, dict):
            self._extra_settings = dict(settings)

    def save(self, config_file: Optional[str] = None):
        """Save configuration to file.
        
        Args:
            config_file: Path to configuration file
        """
        config_file = config_file or self.config_file
        if not config_file:
            config_file = "promptmanager.json"
        
        data = {
            "database": {
                "path": self.database.path,
                "echo": self.database.echo,
                "pool_size": self.database.pool_size
            },
            "api": {
                "host": self.api.host,
                "port": self.api.port,
                "debug": self.api.debug,
                "cors_origins": self.api.cors_origins,
                "rate_limit": self.api.rate_limit
            },
            "websocket": {
                "enabled": self.websocket.enabled,
                "host": self.websocket.host,
                "port": self.websocket.port
            },
            "comfyui": {
                "enabled": self.comfyui.enabled,
                "server_address": self.comfyui.server_address,
                "auto_track": self.comfyui.auto_track
            },
            "storage": {
                "base_path": self.storage.base_path,
                "images_path": self.storage.images_path,
                "thumbnails_path": self.storage.thumbnails_path
            },
            "logging": {
                "level": self.logging.level,
                "file": self.logging.file,
                "console": self.logging.console
            },
            "ui": {
                "theme": self.ui.theme,
                "items_per_page": self.ui.items_per_page,
                "gallery_columns": self.ui.gallery_columns,
                "compact_mode": self.ui.compact_mode
            },
            "settings": dict(self._extra_settings)
        }
        
        with open(config_file, 'w') as f:
            json.dump(data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve configuration values using dotted paths."""
        if not key:
            return default

        if key in self._extra_settings:
            return self._extra_settings[key]

        parts = [part for part in key.split('.') if part]
        if not parts:
            return default

        current: Any = self
        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return default
                current = current[part]
                continue

            if hasattr(current, part):
                current = getattr(current, part)
                continue

            return default

        return current

    def set(self, key: str, value: Any) -> None:
        """Set configuration values using dotted paths."""
        if not key:
            return

        parts = [part for part in key.split('.') if part]
        if not parts:
            return

        target: Any = self
        for part in parts[:-1]:
            if isinstance(target, dict):
                next_target = target.get(part)
                if not isinstance(next_target, dict):
                    next_target = {}
                    target[part] = next_target
                target = next_target
                continue

            if hasattr(target, part):
                target = getattr(target, part)
                continue

            self._extra_settings[key] = value
            return

        last = parts[-1]

        if isinstance(target, dict):
            target[last] = value
            self._extra_settings.pop(key, None)
            return

        if hasattr(target, last):
            setattr(target, last, value)
            self._extra_settings.pop(key, None)
            return

        self._extra_settings[key] = value

    def _load_from_env(self):
        """Load configuration from environment variables."""
        # Database
        if "PROMPTMANAGER_DB_PATH" in os.environ:
            self.database.path = os.environ["PROMPTMANAGER_DB_PATH"]
        
        # API
        if "PROMPTMANAGER_API_HOST" in os.environ:
            self.api.host = os.environ["PROMPTMANAGER_API_HOST"]
        if "PROMPTMANAGER_API_PORT" in os.environ:
            self.api.port = int(os.environ["PROMPTMANAGER_API_PORT"])
        if "PROMPTMANAGER_DEBUG" in os.environ:
            self.api.debug = os.environ["PROMPTMANAGER_DEBUG"].lower() == "true"
        
        # WebSocket
        if "PROMPTMANAGER_WS_ENABLED" in os.environ:
            self.websocket.enabled = os.environ["PROMPTMANAGER_WS_ENABLED"].lower() == "true"
        if "PROMPTMANAGER_WS_PORT" in os.environ:
            self.websocket.port = int(os.environ["PROMPTMANAGER_WS_PORT"])
        
        # ComfyUI
        if "COMFYUI_ADDRESS" in os.environ:
            self.comfyui.server_address = os.environ["COMFYUI_ADDRESS"]
        if "COMFYUI_AUTO_TRACK" in os.environ:
            self.comfyui.auto_track = os.environ["COMFYUI_AUTO_TRACK"].lower() == "true"
        
        # Storage
        if "PROMPTMANAGER_DATA_PATH" in os.environ:
            self.storage.base_path = os.environ["PROMPTMANAGER_DATA_PATH"]
        
        # Logging
        if "PROMPTMANAGER_LOG_LEVEL" in os.environ:
            self.logging.level = os.environ["PROMPTMANAGER_LOG_LEVEL"]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary.
        
        Returns:
            Configuration dictionary
        """
        return {
            "database": self.database.__dict__,
            "api": self.api.__dict__,
            "websocket": self.websocket.__dict__,
            "comfyui": self.comfyui.__dict__,
            "storage": {
                "base_path": self.storage.base_path,
                "images_path": self.storage.images_path,
                "thumbnails_path": self.storage.thumbnails_path,
                "exports_path": self.storage.exports_path,
                "temp_path": self.storage.temp_path
            },
            "logging": self.logging.__dict__,
            "ui": self.ui.__dict__,
            "settings": dict(self._extra_settings)
        }


# Global configuration instance
config = Config()
