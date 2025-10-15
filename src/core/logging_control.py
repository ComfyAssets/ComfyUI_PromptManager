"""Global logging control system for PromptManager.

This module provides centralized control over all logging output based on user settings.
When logging is disabled, only essential startup messages are shown.
"""

import logging
from typing import Optional


class LoggingControl:
    """Singleton class to control all logging behavior."""

    _instance: Optional['LoggingControl'] = None
    _enabled: bool = True  # Default to enabled
    _settings_checked: bool = False

    def __new__(cls):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if logging is enabled.

        Returns:
            True if logging is enabled, False otherwise
        """
        instance = cls()

        # Check settings on first access
        if not instance._settings_checked:
            instance._check_settings()

        return instance._enabled

    @classmethod
    def set_enabled(cls, enabled: bool):
        """Set logging enabled state.

        Args:
            enabled: True to enable logging, False to disable
        """
        instance = cls()
        instance._enabled = enabled
        instance._apply_logging_level()

    def _check_settings(self):
        """Check settings database for logging preference."""
        self._settings_checked = True

        try:
            from pathlib import Path
            from src.services.settings_service import SettingsService
            from utils.core.file_system import get_file_system

            # Get database path
            fs = get_file_system()
            db_path = str(fs.get_database_path("prompts.db"))

            # Only check if database exists (don't try to read during initial creation)
            if not Path(db_path).exists():
                # Database doesn't exist yet, default to enabled
                self._enabled = True
                return

            # Check setting
            settings = SettingsService(db_path)
            enable_logging = settings.get('enable_logging', True)

            self._enabled = bool(enable_logging)
            self._apply_logging_level()

        except Exception:
            # If we can't check settings, default to enabled
            self._enabled = True

    def _apply_logging_level(self):
        """Apply logging level to all PromptManager loggers."""
        if self._enabled:
            level = logging.INFO
        else:
            # When disabled, set to CRITICAL so only critical errors show
            level = logging.CRITICAL

        # All possible logger name prefixes used in the codebase
        logger_prefixes = (
            'prompt_manager',
            'promptmanager',
            'PromptManager',
            'ComfyUI-PromptManager'
        )

        # Update all promptmanager loggers (including those that will be created later)
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if any(logger_name.startswith(prefix) for prefix in logger_prefixes):
                logger = logging.getLogger(logger_name)
                logger.setLevel(level)

                # Also update all handlers
                for handler in logger.handlers:
                    handler.setLevel(level)

        # Also update root logger if it has our handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            # Check if this is one of our handlers by checking the format
            if hasattr(handler, 'formatter') and handler.formatter:
                fmt_str = str(handler.formatter._fmt) if hasattr(handler.formatter, '_fmt') else ''
                if 'prompt_manager' in fmt_str or 'promptmanager' in fmt_str:
                    handler.setLevel(level)

        # CRITICAL: Also configure the logging system before it initializes
        # This prevents the LogConfig.setup_logging() from creating INFO-level loggers
        try:
            import sys
            # Set environment variable that LogConfig can check
            import os
            os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0' if self._enabled else '1'

            # If logging module was already loaded, reconfigure it
            if 'utils.logging' in sys.modules or 'promptmanager.utils.logging' in sys.modules:
                logging_module = sys.modules.get('utils.logging') or sys.modules.get('promptmanager.utils.logging')
                if logging_module and hasattr(logging_module, 'LogConfig'):
                    # Force reconfiguration
                    LogConfig = logging_module.LogConfig
                    LogConfig._initialised = False
                    if self._enabled:
                        LogConfig.setup_logging(level=logging.INFO)
                    else:
                        LogConfig.setup_logging(level=logging.CRITICAL)
        except Exception:
            pass


def is_logging_enabled() -> bool:
    """Check if logging is enabled globally.

    Returns:
        True if logging is enabled, False otherwise
    """
    return LoggingControl.is_enabled()


def set_logging_enabled(enabled: bool):
    """Enable or disable logging globally.

    Args:
        enabled: True to enable logging, False to disable
    """
    LoggingControl.set_enabled(enabled)


def get_logger(name: str) -> logging.Logger:
    """Get a logger that respects the global logging control.

    Args:
        name: Logger name

    Returns:
        Logger instance that respects global logging settings
    """
    logger = logging.getLogger(name)

    # Apply current logging level
    if LoggingControl.is_enabled():
        if logger.level == logging.NOTSET or logger.level == logging.CRITICAL:
            logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.CRITICAL)

    return logger
