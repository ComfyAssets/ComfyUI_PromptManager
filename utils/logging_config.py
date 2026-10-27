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
import fnmatch
import logging
import logging.handlers
import os
import re
import stat
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

from . import library_location

ACTIVE_LOG_NAME = "prompt_manager.log"
# Rule R (#329): a rotated copy is prompt_manager.log.<n>, n >= 1, no leading 0.
_ROTATED_NAME = re.compile(r"prompt_manager\.log\.([1-9][0-9]*)")
# The legacy listing's pattern (GET /logs/files and /logs/stats).
_LISTED_PATTERN = "*.log*"
LOG_FILE_KINDS = ("active", "rotated", "link", "other")


class RotatedChanged(Exception):
    """A reviewed rotated copy changed since the review; nothing was deleted."""

    def __init__(self, name):
        super().__init__(f"{name} changed since the review")
        self.name = name


def _name_key(name: str) -> str:
    """How a name compares: case-folded where the filesystem folds it (Windows)."""
    return os.path.normcase(name)


def _rotated_number(name: str) -> Optional[int]:
    match = _ROTATED_NAME.fullmatch(_name_key(name))
    return int(match.group(1)) if match else None


# Windows reparse tags with the name-surrogate bit name another file or
# folder (symlinks, junctions, WSL symlinks); cloud placeholders do not.
_NAME_SURROGATE = 0x20000000


def _is_link(path: str, st: os.stat_result) -> bool:
    if getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE:
        return True
    return stat.S_ISLNK(st.st_mode) or os.path.isjunction(path)


class LogFilePath(type(Path())):
    """A listed log file's path, with the inode it had when it was listed."""

    listed_ino = 0


def _kind(name: str, path: str, st: os.stat_result) -> str:
    """Rule R: ``active``, ``rotated``, ``link`` or ``other`` (never deleted)."""
    if _is_link(path, st):
        return "link"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    if _name_key(name) == _name_key(ACTIVE_LOG_NAME):
        return "active"
    if _rotated_number(name) is not None:
        return "rotated"
    return "other"


def scan_log_folder(log_dir) -> List[Dict[str, Any]]:
    """Every direct entry of ``log_dir`` with its Rule R kind (blocking).

    Rows are ``{name, kind, size, mtime_ns, ino, modified}``, read with
    ``lstat`` so a link is described, never followed. An entry that cannot
    be read is left out. Never logs: the cleanup calls it while it holds the
    file handler's lock.
    """
    rows = []
    try:
        names = [entry.name for entry in os.scandir(log_dir)]
    except OSError:
        return rows
    for name in names:
        path = os.path.join(log_dir, name)
        try:
            st = os.lstat(path)
            kind = _kind(name, path, st)
        except OSError:
            continue
        try:
            modified = datetime.fromtimestamp(st.st_mtime).isoformat()
        except (OSError, OverflowError, ValueError):  # a time before 1970 on Windows
            modified = ""
        rows.append(
            {
                "name": name,
                "kind": kind,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "ino": st.st_ino,
                "modified": modified,
                "_dir": stat.S_ISDIR(st.st_mode) and kind != "link",
                "_regular": stat.S_ISREG(st.st_mode) and kind != "link",
            }
        )
    return rows


def public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """A scan row without its private keys."""
    return {key: value for key, value in row.items() if not key.startswith("_")}


def rotated_sort_key(row: Dict[str, Any]) -> int:
    return _rotated_number(row["name"]) or 0


def log_file_entry(log_dir, name) -> Path:
    """The path of a downloadable log file named ``name`` (blocking).

    Only a regular, non-link file listed directly in ``log_dir`` under that
    exact name; a separator, ``..``, a stream suffix or any unlisted name
    raises FileNotFoundError, as a missing file does.
    """
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise FileNotFoundError("Log file not found")
    if any(ch in name for ch in ("/", "\\", "\x00", ":")):
        raise FileNotFoundError("Log file not found")
    for row in scan_log_folder(log_dir):
        if row["name"] == name and row["kind"] in ("active", "rotated", "other"):
            if not row["_regular"]:  # a folder, FIFO, socket or device
                break
            entry = LogFilePath(log_dir, row["name"])
            entry.listed_ino = row["ino"]
            return entry
    raise FileNotFoundError("Log file not found")


def _stream_is(stream, ino: int) -> bool:
    """Whether ``stream`` is open on the file with inode ``ino`` (0: unknown)."""
    try:
        open_ino = os.fstat(stream.fileno()).st_ino
    except (OSError, ValueError, AttributeError):
        return False
    return not (ino and open_ino and open_ino != ino)


def _error_text(error: BaseException) -> str:
    """An OSError's reason and file name, without the folder it is in."""
    if isinstance(error, OSError):
        parts = [error.strerror or type(error).__name__]
        if error.filename:
            parts.append(os.path.basename(str(error.filename)))
        return ": ".join(parts)
    return str(error) or type(error).__name__


def _matches_review(row: Optional[Dict[str, Any]], reviewed: Dict[str, Any]) -> bool:
    if row is None or row["kind"] != "rotated":
        return False
    if row["size"] != reviewed.get("size") or row["mtime_ns"] != reviewed.get(
        "mtime_ns"
    ):
        return False
    ino = reviewed.get("ino") or 0
    return not (ino and row["ino"] and row["ino"] != ino)


class _OwnedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Keep a retired owned sink closed when an in-flight record resumes."""

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle and FileHandler.close hold the same handler lock.
        if not self._closed:
            super().emit(record)


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
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        # The library's data folder (#249 S6b): the node folder for a library
        # that was not moved, else ComfyUI's user/__promptmanager.
        self.log_dir = Path(
            library_location.logs_dir(extension_root=str(Path(__file__).parent.parent))
        )
        library_location.ensure_private_dir(str(self.log_dir))

        # Configuration
        self.config = {
            "level": "INFO",
            "max_file_size": 10 * 1024 * 1024,  # 10MB
            "backup_count": 5,
            "console_logging": True,
            "file_logging": True,
            "buffer_size": 1000,  # Keep last 1000 log entries in memory
        }

        # Memory buffer for recent logs (for web viewer)
        self._log_buffer = collections.deque(maxlen=self.config["buffer_size"])
        self._buffer_lock = threading.Lock()
        # Held while a handler is replaced or log files are deleted (#329):
        # taken before any file handler's own lock, never the other way round.
        self._files_lock = threading.Lock()

        # Initialize loggers
        self._setup_loggers()

    def _setup_loggers(self):
        """Set up all loggers with appropriate handlers.

        Configures the main logger with console, file, and memory handlers.
        Sets up file rotation and safe encoding for cross-platform compatibility.
        """
        # Main logger
        self.logger = logging.getLogger("prompt_manager")
        self.logger.setLevel(getattr(logging, self.config["level"]))
        # ComfyUI's root handler would print every record a second time.
        self.logger.propagate = False

        # External handlers are detached but retain their caller-owned resources.
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            if getattr(handler, "_prompt_manager_owned", False) is True:
                handler.close()

        # Custom formatter
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - "
            "%(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Console handler
        if self.config["console_logging"]:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(self._console_level())
            console_handler.setFormatter(formatter)
            console_handler._prompt_manager_owned = True
            self.logger.addHandler(console_handler)

        # File handler with rotation and safe encoding for Windows
        if self.config["file_logging"]:
            log_file = self.log_dir / "prompt_manager.log"
            file_handler = _OwnedRotatingFileHandler(
                log_file,
                maxBytes=self.config["max_file_size"],
                backupCount=self.config["backup_count"],
                encoding="utf-8",
                errors="replace",  # Replace problematic characters instead of crashing
            )
            file_handler.setFormatter(formatter)
            file_handler._prompt_manager_owned = True
            self.logger.addHandler(file_handler)

        # Memory handler for web viewer
        memory_handler = MemoryBufferHandler(self)
        memory_handler.setFormatter(formatter)
        memory_handler._prompt_manager_owned = True
        self.logger.addHandler(memory_handler)

        # Component-specific loggers
        self._setup_component_loggers()

    def _console_level(self) -> int:
        """Console threshold: everything at DEBUG, otherwise warnings only.

        INFO still reaches the log file and the web log viewer.
        """
        if str(self.config["level"]).upper() == "DEBUG":
            return logging.DEBUG
        return logging.WARNING

    def _setup_component_loggers(self):
        """Set up loggers for specific components.

        Creates child loggers for different PromptManager components.
        Child loggers inherit handlers from the main logger.
        """
        components = [
            "prompt_manager.database",
            "prompt_manager.api",
            "prompt_manager.image_monitor",
            "prompt_manager.prompt_tracker",
            "prompt_manager.web_ui",
        ]

        for component in components:
            comp_logger = logging.getLogger(component)
            comp_logger.setLevel(getattr(logging, self.config["level"]))
            # Child loggers inherit handlers from parent

    def get_logger(self, name: str = "prompt_manager") -> logging.Logger:
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
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "formatted": formatted_message,
                "module": record.module,
                "filename": record.filename,
                "lineno": record.lineno,
                "thread": record.thread,
                "thread_name": (
                    record.threadName if hasattr(record, "threadName") else ""
                ),
                "process": record.process,
            }

            self._log_buffer.append(log_entry)

    def get_recent_logs(
        self, limit: int = 100, level: Optional[str] = None
    ) -> List[Dict[str, Any]]:
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
                logs = [
                    log for log in logs if getattr(logging, log["level"]) >= level_num
                ]

        # Return most recent first
        return list(reversed(logs[-limit:]))

    def scan_log_folder(self) -> List[Dict[str, Any]]:
        """Every entry of the log folder with its Rule R kind (blocking)."""
        return [public_row(row) for row in scan_log_folder(self.log_dir)]

    def log_file_entry(self, name: str) -> Path:
        """A downloadable (regular, non-link) log file; FileNotFoundError else."""
        return log_file_entry(self.log_dir, name)

    def get_log_files(self) -> List[Dict[str, Any]]:
        """Get information about available log files.

        Returns:
            List of dictionaries containing file information:
            - filename: Name of the log file
            - path: Full path to the log file
            - size: File size in bytes (a link's own size: links are not followed)
            - modified: ISO formatted modification timestamp
            - is_main: True if this is the main log file
            - kind: ``active``, ``rotated``, ``link`` or ``other`` (Rule R)
        """
        log_files = []
        for row in scan_log_folder(self.log_dir):
            if row["_dir"]:
                continue
            if not fnmatch.fnmatchcase(_name_key(row["name"]), _LISTED_PATTERN):
                continue
            log_files.append(
                {
                    "filename": row["name"],
                    "path": str(self.log_dir / row["name"]),
                    "size": row["size"],
                    "modified": row["modified"],
                    "is_main": row["kind"] == "active",
                    "kind": row["kind"],
                }
            )

        # Sort by modification time, newest first
        log_files.sort(key=lambda x: x["modified"], reverse=True)
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
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                return all_lines[-lines:] if lines > 0 else all_lines
        except Exception as e:
            self.logger.error(f"Error reading log file {filename}: {e}")
            raise

    def _file_handlers(self) -> List[logging.Handler]:
        return [h for h in self.logger.handlers if isinstance(h, logging.FileHandler)]

    def _acquire_file_handlers(self) -> List[logging.Handler]:
        """Take every file handler's lock: no rollover renames a file meanwhile.

        The caller holds ``_files_lock`` (so no handler is replaced) and must
        not log until it released them: the lock is re-entrant, so a record
        logged here could roll the log over under the caller's feet.
        """
        held = []
        try:
            for handler in self._file_handlers():
                handler.acquire()
                held.append(handler)
        except BaseException:
            self._release_handlers(held)
            raise
        return held

    @staticmethod
    def _release_handlers(held) -> None:
        for handler in reversed(held):
            handler.release()

    def _truncate_main_log(self, log_file: Path, expected_ino: int = 0) -> None:
        """Empty the main log through the handler that writes to it.

        Opening the file a second time with ``"w"`` leaves the handler's own
        stream at its old offset: later records land past the new end
        (zero-padded on POSIX) and ``tell()`` keeps reporting the old size, so
        the next record rolls the file over. Truncating the handler's stream
        keeps position and size in step. Without a handler on it (file logging
        off) the existing file is emptied in place, never through a link.

        A handler whose open stream is not the listed file (``expected_ino``:
        the user renamed the log, which the handler still writes to) is left
        alone: the file under the active name is emptied instead.
        """
        handled = False
        target = os.path.normcase(os.path.realpath(str(log_file)))
        for handler in self.logger.handlers:
            stream = getattr(handler, "stream", None)
            base = getattr(handler, "baseFilename", None)
            if stream is None or base is None:
                continue
            if os.path.normcase(os.path.realpath(base)) != target:
                continue
            if not _stream_is(stream, expected_ino):
                continue
            handler.acquire()
            try:
                stream.flush()
                stream.seek(0)
                stream.truncate()
                handled = True
            finally:
                handler.release()
        if not handled:
            flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(str(log_file), flags))

    def delete_rotated(self, expected: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Delete exactly the reviewed rotated copies, or none of them (blocking).

        ``expected`` are scan rows (``name``, ``size``, ``mtime_ns``,
        ``ino``). Under ``_files_lock`` and every file handler's lock the
        folder is scanned again; when any expected file is missing, is no
        longer a rotated copy (Rule R) or differs in size, ``mtime_ns`` or a
        non-zero ``ino``, RotatedChanged is raised with nothing deleted.
        Otherwise each is unlinked in ``<n>`` order.

        Returns ``{"removed": [names], "failed": [(name, OSError)]}``. One
        summary line is logged once both locks are released.
        """
        removed, failed = [], []
        changed = None
        with self._files_lock:
            held = self._acquire_file_handlers()
            try:
                current = {
                    _name_key(row["name"]): row for row in scan_log_folder(self.log_dir)
                }
                targets = []
                for reviewed in sorted(expected, key=rotated_sort_key):
                    row = current.get(_name_key(str(reviewed.get("name", ""))))
                    if not _matches_review(row, reviewed):
                        changed = str(reviewed.get("name", ""))
                        break
                    targets.append(row["name"])
                if changed is None:
                    for name in targets:
                        try:
                            os.unlink(os.path.join(self.log_dir, name))
                            removed.append(name)
                        except OSError as error:
                            failed.append((name, error))
            finally:
                self._release_handlers(held)
        if changed is not None:
            self.logger.warning(
                "Rotated log cleanup refused: %s changed since the review", changed
            )
            raise RotatedChanged(changed)
        self.logger.info(
            "Deleted %d rotated log copies%s",
            len(removed),
            f", {len(failed)} could not be deleted" if failed else "",
        )
        return {"removed": removed, "failed": failed}

    def truncate_logs(self) -> Dict[str, Any]:
        """Empty the active log and delete its rotated copies (Rule R).

        Every other file in the log folder, and every link, is kept. Also
        clears the memory buffer. Runs under ``_files_lock`` and the file
        handlers' locks, so no rollover recreates a just-deleted copy, and
        logs only once they are released.

        Returns:
            Dictionary with:
            - truncated: List of successfully truncated files
            - errors: List of error messages for failed operations
        """
        results = {"truncated": [], "errors": []}

        with self._files_lock:
            held = self._acquire_file_handlers()
            try:
                rows = scan_log_folder(self.log_dir)
                active = [row for row in rows if row["kind"] == "active"]
                rotated = sorted(
                    (row for row in rows if row["kind"] == "rotated"),
                    key=rotated_sort_key,
                )
                for row in active + rotated:
                    path = os.path.join(self.log_dir, row["name"])
                    try:
                        if row["kind"] == "active":
                            self._truncate_main_log(Path(path), row["ino"])
                        else:
                            os.unlink(path)
                        results["truncated"].append(row["name"])
                    except OSError as error:
                        results["errors"].append(
                            f"Failed to truncate {row['name']}: {_error_text(error)}"
                        )
                with self._buffer_lock:
                    self._log_buffer.clear()
            finally:
                self._release_handlers(held)

        if results["truncated"]:
            self.logger.info("Truncated log files: %s", ", ".join(results["truncated"]))
        for error_msg in results["errors"]:
            self.logger.error(error_msg)

        return results

    def update_config(self, new_config: Dict[str, Any]):
        """Update logging configuration.

        Args:
            new_config: Dictionary of configuration updates
                       (level, console_logging, file_logging, etc.)
        """
        self.config.update(new_config)

        # Reconfigure loggers if level changed
        if "level" in new_config:
            level = getattr(logging, new_config["level"].upper(), logging.INFO)
            self.logger.setLevel(level)

            # Update all component loggers
            for logger_name in logging.Logger.manager.loggerDict:
                if logger_name.startswith("prompt_manager"):
                    logger = logging.getLogger(logger_name)
                    logger.setLevel(level)

            for handler in self.logger.handlers:
                if type(handler) is logging.StreamHandler:
                    handler.setLevel(self._console_level())

        # Re-setup loggers if handlers changed
        if any(
            key in new_config
            for key in [
                "console_logging",
                "file_logging",
                "max_file_size",
                "backup_count",
            ]
        ):
            # No cleanup may hold the old handler while it is replaced.
            with self._files_lock:
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
                level = entry["level"]
                level_counts[level] = level_counts.get(level, 0) + 1

        # A link's target is not a log file of this folder: neither counted
        # nor sized.
        log_files = [f for f in self.get_log_files() if f["kind"] != "link"]
        total_log_size = sum(f["size"] for f in log_files)

        return {
            "buffer_count": buffer_count,
            "level_counts": level_counts,
            "log_files_count": len(log_files),
            "total_log_size": total_log_size,
            "log_directory": str(self.log_dir),
            "current_level": self.config["level"],
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


def get_logger(name: str = "prompt_manager") -> logging.Logger:
    """Convenience function to get a logger.

    Args:
        name: Logger name, defaults to 'prompt_manager'

    Returns:
        Configured logger instance for the specified name
    """
    return get_logger_manager().get_logger(name)


# Initialize logging on import
get_logger_manager()
