"""
Logging configuration for ComfyUI_PromptManager.

This module provides centralized logging configuration with support for:
- Multiple log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- File rotation to prevent logs from growing too large
- Console and file output
- Structured logging with timestamps and context
- Log viewer API integration
"""

import collections
import logging
import logging.handlers
import os
import json
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path


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
        self.log_dir = Path(__file__).parent.parent / "logs"
        self.log_dir.mkdir(exist_ok=True)
        
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
        self._log_buffer = collections.deque(maxlen=self.config['buffer_size'])
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
        
        # File handler with rotation and safe encoding for Windows
        if self.config['file_logging']:
            log_file = self.log_dir / "prompt_manager.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=self.config['max_file_size'],
                backupCount=self.config['backup_count'],
                encoding='utf-8',
                errors='replace'  # Replace problematic characters instead of crashing
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        
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
    
    def get_recent_logs(self, limit: int = 100, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent log entries from memory buffer.
        
        Args:
            limit: Maximum number of log entries to return
            level: Optional log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            
        Returns:
            List of log entry dictionaries, most recent first
        """
        with self._buffer_lock:
            logs = list(self._log_buffer)

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