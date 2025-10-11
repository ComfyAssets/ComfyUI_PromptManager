"""Windows-safe file operation utilities.

This module provides robust file operations with proper handling for
Windows file locking, SQLite WAL files, and retry logic.
"""

from __future__ import annotations

import gc
import os
import platform
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

try:
    from promptmanager.loggers import get_logger
except ImportError:
    import logging

    def get_logger(name: str) -> "logging.Logger":
        return logging.getLogger(name)


LOGGER = get_logger("promptmanager.utils.file_operations")
IS_WINDOWS = platform.system() == "Windows"


class FileOperationError(Exception):
    """Raised when file operations fail after retries."""

    pass


def close_all_sqlite_connections(db_path: Path) -> None:
    """Force close any dangling SQLite connections.

    This helps on Windows where file handles may linger.

    Args:
        db_path: Path to the database file
    """
    # Force garbage collection to close any lingering connection objects
    gc.collect()
    time.sleep(0.1)  # Brief pause for OS to release handles


def checkpoint_wal_file(db_path: Path) -> bool:
    """Checkpoint WAL file to merge changes back into main database.

    This reduces file lock contention by consolidating WAL changes.

    Args:
        db_path: Path to the database file

    Returns:
        True if checkpoint succeeded, False otherwise
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            # TRUNCATE mode: checkpoint and delete WAL file
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
            LOGGER.debug("WAL checkpoint completed for %s", db_path)
            return True
        finally:
            conn.close()
    except sqlite3.Error as exc:
        LOGGER.warning("WAL checkpoint failed for %s: %s", db_path, exc)
        return False


def safe_rename_with_retry(
    src: Path,
    dst: Path,
    max_retries: int = 5,
    retry_delay: float = 0.5,
    exponential_backoff: bool = True,
) -> bool:
    """Safely rename a file with retry logic for Windows file locks.

    On Windows, SQLite databases may have file locks from:
    - Active connections
    - WAL files
    - Antivirus scanners
    - Background indexing services

    Args:
        src: Source path
        dst: Destination path
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries in seconds
        exponential_backoff: Whether to double delay after each retry

    Returns:
        True if rename succeeded, False otherwise

    Raises:
        FileOperationError: If rename fails after all retries
    """
    if not src.exists():
        raise FileOperationError(f"Source file does not exist: {src}")

    # Remove destination if it exists
    if dst.exists():
        try:
            dst.unlink()
        except OSError as exc:
            LOGGER.warning("Could not remove existing destination %s: %s", dst, exc)

    # Try checkpoint if this is a SQLite database
    if src.suffix in ('.db', '.sqlite', '.sqlite3'):
        checkpoint_wal_file(src)
        close_all_sqlite_connections(src)

    current_delay = retry_delay
    last_error = None

    for attempt in range(max_retries):
        try:
            src.rename(dst)
            LOGGER.info("Successfully renamed %s to %s", src, dst)
            return True
        except PermissionError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                LOGGER.warning(
                    "File locked (attempt %d/%d): %s - retrying in %.2fs",
                    attempt + 1,
                    max_retries,
                    exc,
                    current_delay,
                )
                time.sleep(current_delay)

                # Force cleanup between retries
                close_all_sqlite_connections(src)

                if exponential_backoff:
                    current_delay *= 2
            else:
                LOGGER.error(
                    "Failed to rename after %d attempts: %s",
                    max_retries,
                    exc,
                )
        except OSError as exc:
            last_error = exc
            LOGGER.error("Rename failed with OS error: %s", exc)
            break

    raise FileOperationError(
        f"Failed to rename {src} to {dst} after {max_retries} attempts: {last_error}"
    )


def copy_with_verify(
    src: Path,
    dst: Path,
    verify: bool = True,
) -> bool:
    """Copy a file with optional verification.

    Args:
        src: Source path
        dst: Destination path
        verify: Whether to verify file sizes match

    Returns:
        True if copy succeeded and verified

    Raises:
        FileOperationError: If copy fails or verification fails
    """
    if not src.exists():
        raise FileOperationError(f"Source file does not exist: {src}")

    try:
        # Ensure parent directory exists
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Copy file
        shutil.copy2(src, dst)

        # Verify if requested
        if verify:
            src_size = src.stat().st_size
            dst_size = dst.stat().st_size
            if src_size != dst_size:
                raise FileOperationError(
                    f"Copy verification failed: size mismatch "
                    f"(src={src_size}, dst={dst_size})"
                )

        LOGGER.debug("Successfully copied %s to %s", src, dst)
        return True
    except OSError as exc:
        raise FileOperationError(f"Failed to copy {src} to {dst}: {exc}")


def safe_delete_with_retry(
    path: Path,
    max_retries: int = 3,
    retry_delay: float = 0.5,
) -> bool:
    """Safely delete a file with retry logic.

    Args:
        path: Path to delete
        max_retries: Maximum retry attempts
        retry_delay: Delay between retries

    Returns:
        True if deletion succeeded, False if file doesn't exist

    Raises:
        FileOperationError: If deletion fails after retries
    """
    if not path.exists():
        return False

    # Close any connections if it's a database
    if path.suffix in ('.db', '.sqlite', '.sqlite3'):
        close_all_sqlite_connections(path)

    last_error = None
    for attempt in range(max_retries):
        try:
            path.unlink()
            LOGGER.debug("Successfully deleted %s", path)
            return True
        except PermissionError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                LOGGER.warning(
                    "Delete failed (attempt %d/%d): %s - retrying",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                time.sleep(retry_delay)
                close_all_sqlite_connections(path)
            else:
                LOGGER.error("Failed to delete after %d attempts: %s", max_retries, exc)
        except OSError as exc:
            last_error = exc
            LOGGER.error("Delete failed with OS error: %s", exc)
            break

    raise FileOperationError(
        f"Failed to delete {path} after {max_retries} attempts: {last_error}"
    )


def safe_rename_database(
    src: Path,
    dst: Path,
    use_copy_fallback: bool = True,
) -> bool:
    """Safely rename a SQLite database with fallback strategy.

    Strategy:
    1. Try to checkpoint and rename directly (fast path)
    2. If that fails, copy-verify-delete (slower but more reliable)

    Args:
        src: Source database path
        dst: Destination database path
        use_copy_fallback: Whether to use copy fallback if rename fails

    Returns:
        True if operation succeeded

    Raises:
        FileOperationError: If all strategies fail
    """
    if not src.exists():
        raise FileOperationError(f"Source database does not exist: {src}")

    # Also handle WAL and SHM files
    wal_src = src.with_suffix(src.suffix + "-wal")
    shm_src = src.with_suffix(src.suffix + "-shm")

    # Strategy 1: Try direct rename with retry
    try:
        LOGGER.info("Attempting direct rename of database...")
        safe_rename_with_retry(src, dst)

        # Rename WAL and SHM files if they exist
        if wal_src.exists():
            wal_dst = dst.with_suffix(dst.suffix + "-wal")
            try:
                safe_rename_with_retry(wal_src, wal_dst, max_retries=2)
            except FileOperationError:
                LOGGER.warning("Could not rename WAL file, continuing anyway")

        if shm_src.exists():
            shm_dst = dst.with_suffix(dst.suffix + "-shm")
            try:
                safe_rename_with_retry(shm_src, shm_dst, max_retries=2)
            except FileOperationError:
                LOGGER.warning("Could not rename SHM file, continuing anyway")

        return True
    except FileOperationError as exc:
        if not use_copy_fallback:
            raise

        LOGGER.warning(
            "Direct rename failed: %s - falling back to copy strategy",
            exc,
        )

    # Strategy 2: Copy-verify-delete fallback
    try:
        LOGGER.info("Using copy-verify-delete fallback strategy...")

        # Copy main database file
        copy_with_verify(src, dst, verify=True)

        # Copy WAL file if it exists
        if wal_src.exists():
            wal_dst = dst.with_suffix(dst.suffix + "-wal")
            try:
                copy_with_verify(wal_src, wal_dst, verify=True)
            except FileOperationError:
                LOGGER.warning("Could not copy WAL file, continuing anyway")

        # Copy SHM file if it exists
        if shm_src.exists():
            shm_dst = dst.with_suffix(dst.suffix + "-shm")
            try:
                copy_with_verify(shm_src, shm_dst, verify=True)
            except FileOperationError:
                LOGGER.warning("Could not copy SHM file, continuing anyway")

        # Verify the copy by connecting to it
        try:
            conn = sqlite3.connect(str(dst), timeout=5.0)
            conn.execute("PRAGMA integrity_check")
            conn.close()
        except sqlite3.Error as exc:
            raise FileOperationError(f"Copied database failed integrity check: {exc}")

        # Delete original files
        LOGGER.info("Copy verified, deleting original files...")
        try:
            safe_delete_with_retry(src)
        except FileOperationError as exc:
            LOGGER.error(
                "Failed to delete original database after successful copy: %s",
                exc,
            )
            # Don't fail the operation - the new database is good
            LOGGER.warning(
                "Original database could not be deleted, "
                "but migration was successful. "
                "You may need to manually delete: %s",
                src,
            )

        # Try to delete WAL and SHM files (non-critical)
        for aux_file in [wal_src, shm_src]:
            if aux_file.exists():
                try:
                    safe_delete_with_retry(aux_file, max_retries=2)
                except FileOperationError:
                    LOGGER.warning("Could not delete auxiliary file: %s", aux_file)

        LOGGER.info("Copy-verify-delete strategy completed successfully")
        return True
    except FileOperationError as exc:
        LOGGER.error("Copy fallback strategy failed: %s", exc)
        raise
