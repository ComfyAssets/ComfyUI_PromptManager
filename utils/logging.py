"""Logging configuration for PromptManager.

Provides centralized logging setup with proper formatting,
rotation, level management, and unified logger naming.
"""

from __future__ import annotations

import builtins
import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from typing import Optional, Union

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager.utils.logging", module)
sys.modules.setdefault("utils.logging", module)
sys.modules.setdefault("custom_nodes.promptmanager.utils.logging", module)

_GLOBAL_INIT_FLAG = "_promptmanager_logging_truly_initialized"
if not hasattr(sys.modules.get("__main__", sys), _GLOBAL_INIT_FLAG):
    setattr(sys.modules.get("__main__", sys), _GLOBAL_INIT_FLAG, False)

_ORIGINAL_PRINT = builtins.print
_PRINT_REDIRECTED = False


def _normalize_logger_name(name: Optional[str]) -> Optional[str]:
    """Return a clean logger name without forcing a PromptManager prefix."""

    if name is None:
        return None

    normalized = name.strip().lstrip(".")
    return normalized or None


class LogConfig:
    """Centralized logging configuration."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    DEFAULT_LEVEL = INFO
    DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    import os  # noqa: E402

    try:
        from utils.core.file_system import get_file_system  # type: ignore

        _fs = get_file_system()
        LOG_DIR = Path(_fs.get_logs_dir(create=True))
    except Exception:
        comfy_root = None
        for candidate in Path.cwd().parents:
            if (candidate / "user" / "default" / "PromptManager").exists():
                comfy_root = candidate
                break
        if comfy_root:
            LOG_DIR = comfy_root / "user" / "default" / "PromptManager" / "logs"
        else:
            LOG_DIR = Path.cwd() / "promptmanager_logs"

    LOG_FILE = "promptmanager.log"
    MAX_BYTES = 10 * 1024 * 1024
    BACKUP_COUNT = 5

    _initialised = False
    _init_lock = threading.Lock()
    _file_handler: Optional[logging.handlers.RotatingFileHandler] = None

    @classmethod
    def _resolve_level(cls, level: Optional[Union[str, int]]) -> Optional[int]:
        if level is None:
            return cls.DEFAULT_LEVEL
        if isinstance(level, int):
            return level
        text = str(level).strip().upper()
        if not text:
            return cls.DEFAULT_LEVEL
        if text in {"OFF", "NONE", "DISABLED", "DISABLE"}:
            return None
        mapping = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARN": logging.WARNING,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return mapping.get(text, cls.DEFAULT_LEVEL)

    @classmethod
    def setup_logging(
        cls,
        level: Optional[Union[str, int]] = None,
        log_file: Union[bool, str, Path] = True,
        console: bool = True,
        format_string: Optional[str] = None,
        date_format: Optional[str] = None,
    ) -> None:
        with cls._init_lock:
            main_module = sys.modules.get("__main__", sys)
            global_flag = getattr(main_module, _GLOBAL_INIT_FLAG, False)
            if cls._initialised or global_flag:
                return

            setattr(main_module, _GLOBAL_INIT_FLAG, True)

            resolved_level = cls._resolve_level(level)
            format_string = format_string or cls.DEFAULT_FORMAT
            date_format = date_format or cls.DEFAULT_DATE_FORMAT

            formatter = logging.Formatter(format_string, date_format)
            root_logger = logging.getLogger()
            root_logger.setLevel(resolved_level or (logging.CRITICAL + 10))
            root_logger.handlers.clear()
            cls._file_handler = None

            if resolved_level is None:
                cls._initialised = True
                return

            if console:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(resolved_level)
                console_handler.setFormatter(formatter)
                root_logger.addHandler(console_handler)

            log_path = None
            file_enabled = bool(log_file)
            requested_path: Optional[Path] = None
            if isinstance(log_file, (str, Path)):
                requested_path = Path(log_file)
                file_enabled = True

            if file_enabled:
                try:
                    cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
                    candidate = requested_path or (cls.LOG_DIR / cls.LOG_FILE)
                    file_handler = logging.handlers.RotatingFileHandler(
                        candidate,
                        maxBytes=cls.MAX_BYTES,
                        backupCount=cls.BACKUP_COUNT,
                    )
                    file_handler.setLevel(resolved_level)
                    file_handler.setFormatter(formatter)
                    root_logger.addHandler(file_handler)
                    cls._file_handler = file_handler
                    log_path = candidate
                except (OSError, PermissionError) as exc:
                    root_logger.warning(
                        "promptmanager.logging file handler disabled: %s", exc
                    )
                except Exception as exc:  # noqa: BLE001
                    root_logger.warning(
                        "promptmanager.logging failed to configure file handler (%s)",
                        exc,
                    )

            cls._configure_module_loggers()

            logger = logging.getLogger("promptmanager")
            logger.info("Logging initialized")
            logger.info("Log level: %s", logging.getLevelName(resolved_level))
            if log_path:
                logger.info("Log file: %s", log_path)

            cls._initialised = True

    @classmethod
    def _configure_module_loggers(cls) -> None:
        logging.getLogger("PIL").setLevel(logging.WARNING)
        logging.getLogger("watchdog").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.getLogger("promptmanager").setLevel(cls.DEFAULT_LEVEL)

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        normalized = _normalize_logger_name(name) or name or "promptmanager"
        return logging.getLogger(normalized)

    @classmethod
    def set_level(cls, level: int, logger_name: Optional[str] = None) -> None:
        normalized = _normalize_logger_name(logger_name)
        target = logging.getLogger(normalized if normalized is not None else logger_name)
        target.setLevel(level)
        for handler in target.handlers:
            handler.setLevel(level)

    @classmethod
    def add_file_handler(
        cls,
        filename: str,
        level: int = None,
        format_string: str = None
    ) -> None:
        level = level or cls.DEFAULT_LEVEL
        format_string = format_string or cls.DEFAULT_FORMAT
        log_path = cls.LOG_DIR / filename
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=cls.MAX_BYTES,
            backupCount=cls.BACKUP_COUNT,
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(format_string, cls.DEFAULT_DATE_FORMAT))
        logging.getLogger().addHandler(handler)

    @classmethod
    def enable_debug(cls) -> None:
        cls.set_level(cls.DEBUG)
        logging.getLogger("promptmanager").debug("Debug logging enabled")

    @classmethod
    def disable_console(cls) -> None:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                root_logger.removeHandler(handler)

    @classmethod
    def enable_console(cls, level: int = None) -> None:
        root_logger = logging.getLogger()
        level = level or cls.DEFAULT_LEVEL
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(level)
                return
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(cls.DEFAULT_FORMAT, cls.DEFAULT_DATE_FORMAT))
        root_logger.addHandler(handler)

    @classmethod
    def get_log_files(cls) -> list:
        if not cls.LOG_DIR.exists():
            return []
        return sorted(cls.LOG_DIR.glob("*.log*"))

    @classmethod
    def clear_logs(cls) -> None:
        logger = logging.getLogger("promptmanager.logging")
        for log_file in cls.get_log_files():
            try:
                log_file.unlink()
                logger.info("Deleted log file %s", log_file.name)
            except Exception as exc:
                logger.error("Failed to delete %s: %s", log_file, exc)

    @classmethod
    def rotate_logs(cls) -> bool:
        handler = cls._file_handler
        if handler and hasattr(handler, "doRollover"):
            handler.doRollover()
            logging.getLogger("promptmanager.logging").info("Manual log rollover triggered")
            return True
        logging.getLogger("promptmanager.logging").warning("Log rollover requested but no file handler is active")
        return False


def redirect_print_to_logger(level: int = logging.INFO, logger_name: str = "promptmanager.stdout") -> None:
    global _PRINT_REDIRECTED
    if _PRINT_REDIRECTED:
        return

    logger = logging.getLogger(logger_name)

    def _redirected_print(*args, sep=' ', end='\n', file=None, flush=False):
        if file not in (None, sys.stdout, sys.stderr):
            return _ORIGINAL_PRINT(*args, sep=sep, end=end, file=file, flush=flush)
        message = sep.join(str(arg) for arg in args)
        if end and end != '\n':
            message = f"{message}{end}".rstrip() if message else end.rstrip()
        logger.log(level, message.rstrip())

    builtins.print = _redirected_print  # type: ignore[assignment]
    _PRINT_REDIRECTED = True


def restore_original_print() -> None:
    global _PRINT_REDIRECTED
    if _PRINT_REDIRECTED:
        builtins.print = _ORIGINAL_PRINT  # type: ignore[assignment]
        _PRINT_REDIRECTED = False


def get_logger(name: str) -> logging.Logger:
    if not LogConfig._initialised:
        LogConfig.setup_logging()
    return LogConfig.get_logger(name)


def setup_logging(**kwargs) -> None:
    LogConfig.setup_logging(**kwargs)
