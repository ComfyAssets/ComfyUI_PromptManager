"""
Logging configuration for ComfyUI_PromptManager.

This module provides centralized logging configuration with support for:
- Multiple log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- File rotation to prevent logs from growing too large
- Console and file output
- Structured logging with timestamps and context
- Log viewer API integration
"""

import logging
import logging.handlers
import os
import json
import threading
import sys
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager.utils.core.logging_config", module)
sys.modules.setdefault("utils.core.logging_config", module)
sys.modules.setdefault("custom_nodes.promptmanager.utils.core.logging_config", module)


class PromptManagerLogger:
    """
    Centralized logging system for PromptManager.
    
    Features:
    - Configurable log levels
    - File rotation with size limits
    - Structured JSON logging for API consumption
    - Thread-safe operations
    - Memory buffer for recent logs
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Ensure singleton pattern with thread safety.
        
        Returns:
            The single instance of PromptManagerLogger
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the logging system if not already initialized.
        
        Sets up log directory, configuration, memory buffer, and all handlers.
        Uses _initialized flag to prevent duplicate initialization.
        """
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        # Use ComfyUI user directory when available
        logs_dir: Optional[Path] = None
        try:
            from importlib import import_module

            module_candidates = []
            base_module = __name__.rsplit(".", 1)[0]
            module_candidates.append(f"{base_module}.file_system")
            module_candidates.extend(
                [
                    "promptmanager.utils.core.file_system",
                    "utils.core.file_system",
                    "custom_nodes.promptmanager.utils.core.file_system",
                ]
            )

            fs_module = None
            for module_name in module_candidates:
                module = sys.modules.get(module_name)
                if module is None:
                    try:
                        module = import_module(module_name)
                    except Exception:
                        continue
                if module is not None:
                    fs_module = module
                    break

            if fs_module is not None:
                get_file_system = getattr(fs_module, "get_file_system", None)
                if callable(get_file_system):
                    fs_helper = get_file_system()
                else:
                    ComfyUIFileSystem = getattr(fs_module, "ComfyUIFileSystem", None)
                    fs_helper = ComfyUIFileSystem() if ComfyUIFileSystem else None

                if fs_helper is not None:
                    logs_dir = Path(fs_helper.get_logs_dir(create=True))
        except Exception:
            logs_dir = None

        if logs_dir is None:
            # Legacy detection by scanning for ComfyUI root
            comfyui_root = None
            parents = list(Path(__file__).parents)
            for i in range(min(7, len(parents))):
                test_path = parents[i]
                if (test_path / "ComfyUI" / "__init__.py").exists():
                    comfyui_root = test_path / "ComfyUI"
                    break
                if (
                    (test_path / "__init__.py").exists()
                    and test_path.name in {"ComfyUI", "ComfyUI-3.12", "ComfyUI-3_x_12"}
                ):
                    comfyui_root = test_path
                    break

            if comfyui_root:
                logs_dir = comfyui_root / "user" / "default" / "PromptManager" / "logs"
            else:
                logs_dir = Path.cwd() / "promptmanager_logs"

        self.log_dir = logs_dir
        self._file_logging_enabled = True
        self._log_dir_error: Optional[Exception] = None

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            self._file_logging_enabled = False
            self._log_dir_error = exc
        except Exception as exc:  # noqa: BLE001 - defensive against unexpected failures
            self._file_logging_enabled = False
            self._log_dir_error = exc
        
        # Configuration
        self.config = {
            'level': 'INFO',
            'max_file_size': 10 * 1024 * 1024,  # 10MB
            'backup_count': 5,
            'console_logging': True,
            'file_logging': True,
            'buffer_size': 1000  # Keep last 1000 log entries in memory
        }
        
        # Memory buffer for recent logs (for web viewer)
        self._log_buffer = []
        self._buffer_lock = threading.Lock()
        
        # Initialize loggers
        self._setup_loggers()
    
    def _setup_loggers(self):
        """Set up all loggers with appropriate handlers.
        
        Configures the main logger with console, file, and memory handlers.
        Sets up file rotation and safe encoding for cross-platform compatibility.
        """
        # Main logger
        self.logger = logging.getLogger('prompt_manager')
        self.logger.setLevel(getattr(logging, self.config['level']))
        
        # Clear existing handlers
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Custom formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console handler
        if self.config['console_logging']:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

        if self._log_dir_error is not None:
            self.logger.warning(
                "File logging disabled; falling back to console only: %s",
                self._log_dir_error,
            )

        # File handler with rotation and safe encoding for Windows
        if self.config['file_logging'] and self._file_logging_enabled:
            log_file = self.log_dir / "prompt_manager.log"
            try:
                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=self.config['max_file_size'],
                    backupCount=self.config['backup_count'],
                    encoding='utf-8',
                    errors='replace'  # Replace problematic characters instead of crashing
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except (OSError, PermissionError) as exc:
                self._file_logging_enabled = False
                self.logger.warning(
                    "File logging disabled; failed to access %s: %s",
                    log_file,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                self._file_logging_enabled = False
                self.logger.warning(
                    "Failed to configure file logging (%s); using console only",
                    exc,
                )

        # Memory handler for web viewer
        memory_handler = MemoryBufferHandler(self)
        memory_handler.setFormatter(formatter)
        self.logger.addHandler(memory_handler)
        
        # Component-specific loggers
        self._setup_component_loggers()
    
    def _setup_component_loggers(self):
        """Set up loggers for specific components.
        
        Creates child loggers for different PromptManager components.
        Child loggers inherit handlers from the main logger.
        """
        components = [
            'prompt_manager.database',
            'prompt_manager.api',
            'prompt_manager.image_monitor',
            'prompt_manager.prompt_tracker',
            'prompt_manager.web_ui'
        ]
        
        for component in components:
            comp_logger = logging.getLogger(component)
            comp_logger.setLevel(getattr(logging, self.config['level']))
            # Child loggers inherit handlers from parent
    
    def get_logger(self, name: str = 'prompt_manager') -> logging.Logger:
        """Get a logger instance for a specific component.
        
        Args:
            name: Logger name, typically in format 'prompt_manager.component'
            
        Returns:
            Configured logger instance
        """
        return logging.getLogger(name)
    
    def add_to_buffer(self, record: logging.LogRecord, formatted_message: str):
        """Add a log entry to the memory buffer for web viewer.
        
        Args:
            record: The LogRecord object from the logging system
            formatted_message: The formatted log message string
        """
        with self._buffer_lock:
            log_entry = {
                'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
                'formatted': formatted_message,
                'module': record.module,
                'filename': record.filename,
                'lineno': record.lineno,
                'thread': record.thread,
                'thread_name': record.threadName if hasattr(record, 'threadName') else '',
                'process': record.process
            }
            
            self._log_buffer.append(log_entry)
            
            # Maintain buffer size limit
            if len(self._log_buffer) > self.config['buffer_size']:
                self._log_buffer = self._log_buffer[-self.config['buffer_size']:]
    
    def get_recent_logs(self, limit: int = 100, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent log entries from memory buffer.
        
        Args:
            limit: Maximum number of log entries to return
            level: Optional log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            
        Returns:
            List of log entry dictionaries, most recent first
        """
        with self._buffer_lock:
            logs = self._log_buffer[:]
        
        # Filter by level if specified
        if level:
            level_num = getattr(logging, level.upper(), None)
            if level_num:
                logs = [log for log in logs if getattr(logging, log['level']) >= level_num]
        
        # Return most recent first
        return list(reversed(logs[-limit:]))
    
    def get_log_files(self) -> List[Dict[str, Any]]:
        """Get information about available log files.
        
        Returns:
            List of dictionaries containing file information:
            - filename: Name of the log file
            - path: Full path to the log file
            - size: File size in bytes
            - modified: ISO formatted modification timestamp
            - is_main: True if this is the main log file
        """
        log_files = []
        
        for log_file in self.log_dir.glob("*.log*"):
            try:
                stat = log_file.stat()
                log_files.append({
                    'filename': log_file.name,
                    'path': str(log_file),
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    'is_main': log_file.name == 'prompt_manager.log'
                })
            except Exception as e:
                self.logger.error(f"Error getting info for log file {log_file}: {e}")
        
        # Sort by modification time, newest first
        log_files.sort(key=lambda x: x['modified'], reverse=True)
        return log_files
    
    def read_log_file(self, filename: str, lines: int = 100) -> List[str]:
        """Read lines from a specific log file.
        
        Args:
            filename: Name of the log file to read (must be in log directory)
            lines: Number of lines to read from the end of the file (0 for all)
            
        Returns:
            List of line strings from the log file
            
        Raises:
            FileNotFoundError: If the log file doesn't exist
            ValueError: If the file path is outside the log directory
        """
        log_file = self.log_dir / filename
        
        if not log_file.exists() or not log_file.is_file():
            raise FileNotFoundError(f"Log file not found: {filename}")
        
        # Security check - ensure file is in log directory
        if not str(log_file.resolve()).startswith(str(self.log_dir.resolve())):
            raise ValueError("Invalid log file path")
        
        try:
            # Use UTF-8 encoding with error handling for Windows compatibility
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
                return all_lines[-lines:] if lines > 0 else all_lines
        except Exception as e:
            self.logger.error(f"Error reading log file {filename}: {e}")
            raise
    
    def truncate_logs(self) -> Dict[str, Any]:
        """Truncate all log files.
        
        Clears the main log file and deletes rotated log files.
        Also clears the memory buffer.
        
        Returns:
            Dictionary with:
            - truncated: List of successfully truncated files
            - errors: List of error messages for failed operations
        """
        results = {
            'truncated': [],
            'errors': []
        }
        
        for log_file in self.log_dir.glob("prompt_manager.log*"):
            try:
                if log_file.name == 'prompt_manager.log':
                    # For main log file, just clear it with safe encoding
                    with open(log_file, 'w', encoding='utf-8', errors='replace') as f:
                        f.write("")
                else:
                    # For rotated files, delete them
                    log_file.unlink()
                
                results['truncated'].append(log_file.name)
                self.logger.info(f"Truncated log file: {log_file.name}")
                
            except Exception as e:
                error_msg = f"Failed to truncate {log_file.name}: {str(e)}"
                results['errors'].append(error_msg)
                self.logger.error(error_msg)
        
        # Clear memory buffer
        with self._buffer_lock:
            self._log_buffer.clear()
        
        return results
    
    def update_config(self, new_config: Dict[str, Any]):
        """Update logging configuration.
        
        Args:
            new_config: Dictionary of configuration updates
                       (level, console_logging, file_logging, etc.)
        """
        self.config.update(new_config)
        
        # Reconfigure loggers if level changed
        if 'level' in new_config:
            level = getattr(logging, new_config['level'].upper(), logging.INFO)
            self.logger.setLevel(level)
            
            # Update all component loggers
            for logger_name in logging.Logger.manager.loggerDict:
                if logger_name.startswith('prompt_manager'):
                    logger = logging.getLogger(logger_name)
                    logger.setLevel(level)
        
        # Re-setup loggers if handlers changed
        if any(key in new_config for key in ['console_logging', 'file_logging', 'max_file_size', 'backup_count']):
            self._setup_loggers()
        
        self.logger.info(f"Updated logging configuration: {new_config}")
    
    def get_config(self) -> Dict[str, Any]:
        """Get current logging configuration.
        
        Returns:
            Copy of the current configuration dictionary
        """
        return self.config.copy()
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get logging statistics.
        
        Returns:
            Dictionary containing:
            - buffer_count: Number of entries in memory buffer
            - level_counts: Count of entries by log level
            - log_files_count: Number of log files
            - total_log_size: Total size of all log files in bytes
            - log_directory: Path to the log directory
            - current_level: Current logging level
        """
        with self._buffer_lock:
            buffer_count = len(self._log_buffer)
            level_counts = {}
            for entry in self._log_buffer:
                level = entry['level']
                level_counts[level] = level_counts.get(level, 0) + 1
        
        log_files = self.get_log_files()
        total_log_size = sum(f['size'] for f in log_files)
        
        return {
            'buffer_count': buffer_count,
            'level_counts': level_counts,
            'log_files_count': len(log_files),
            'total_log_size': total_log_size,
            'log_directory': str(self.log_dir),
            'current_level': self.config['level']
        }


class MemoryBufferHandler(logging.Handler):
    """Custom logging handler that stores logs in memory for web viewer.
    
    This handler extends the standard logging.Handler to capture log records
    and store them in the PromptManagerLogger's memory buffer. This enables
    the web interface to display recent log entries without reading from files.
    
    The handler is designed to be failure-safe - if any error occurs during
    log processing, it silently continues without disrupting the application.
    """
    
    def __init__(self, logger_manager: PromptManagerLogger):
        """Initialize the memory buffer handler.
        
        Args:
            logger_manager: The PromptManagerLogger instance to store logs in
        """
        super().__init__()
        self.logger_manager = logger_manager
    
    def emit(self, record: logging.LogRecord):
        """Handle a log record by adding it to the memory buffer.
        
        Args:
            record: The LogRecord to process and store
        """
        try:
            formatted_message = self.format(record)
            self.logger_manager.add_to_buffer(record, formatted_message)
        except Exception:
            # Don't let logging errors break the application
            pass


# Global logger instance
_logger_manager = None


def _register_module_aliases() -> None:
    """Expose this module under legacy import paths for ComfyUI runtime."""
    module = sys.modules.get(__name__)
    if module is None:
        return

    parts = __name__.split(".")
    if len(parts) >= 3:
        utils_pkg_name = ".".join(parts[:-2])
    else:
        utils_pkg_name = parts[0] if parts else "utils"
    core_pkg_name = ".".join(parts[:-1]) if len(parts) >= 2 else "utils.core"

    utils_module = sys.modules.get(utils_pkg_name)
    if utils_module is not None:
        existing_utils = sys.modules.get("utils")
        if existing_utils is not utils_module:
            if existing_utils and "promptmanager._original_utils" not in sys.modules:
                sys.modules["promptmanager._original_utils"] = existing_utils
            sys.modules["utils"] = utils_module

    core_module = sys.modules.get(core_pkg_name)
    if core_module is not None:
        existing_core = sys.modules.get("utils.core")
        if existing_core is not core_module:
            if existing_core and "promptmanager._original_utils_core" not in sys.modules:
                sys.modules["promptmanager._original_utils_core"] = existing_core
            sys.modules["utils.core"] = core_module

    sys.modules["utils.core.logging_config"] = module


_register_module_aliases()

def get_logger_manager() -> PromptManagerLogger:
    """Get the global logger manager instance.
    
    Returns:
        The singleton PromptManagerLogger instance, creating it if necessary
    """
    global _logger_manager
    if _logger_manager is None:
        _logger_manager = PromptManagerLogger()
    return _logger_manager

def get_logger(name: str = 'prompt_manager') -> logging.Logger:
    """Convenience function to get a logger.
    
    Args:
        name: Logger name, defaults to 'prompt_manager'
        
    Returns:
        Configured logger instance for the specified name
    """
    return get_logger_manager().get_logger(name)

# Initialize logging on import
get_logger_manager()
