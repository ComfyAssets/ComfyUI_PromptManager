"""Database migration utilities for upgrading PromptManager databases.

This module encapsulates the detection, progress tracking, and execution logic
required to migrate legacy (v1) PromptManager SQLite databases to the new v2
schema described in ``docs/migration.md``.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .connection_helper import DatabaseConnection, get_db_connection
from ..repositories.generated_image_repository import GeneratedImageRepository
from ..utils.file_operations import (
    FileOperationError,
    checkpoint_wal_file,
    close_all_sqlite_connections,
    safe_rename_database,
)

try:  # pragma: no cover - fallback for standalone execution
    from promptmanager.loggers import get_logger
except ImportError:  # pragma: no cover - fall back to stdlib logging
    import logging

    def get_logger(name: str) -> "logging.Logger":
        """Return a standard library logger when promptmanager logger unavailable."""
        return logging.getLogger(name)


LOGGER = get_logger("promptmanager.database.migration")


class MigrationStatus(Enum):
    """High-level migration state exposed to the API."""

    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class MigrationPhase(Enum):
    """Internal migration phases for progress tracking."""

    IDLE = "idle"
    BACKING_UP = "backing_up"
    TRANSFORMING = "transforming"
    VERIFYING = "verifying"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(slots=True)
class MigrationPaths:
    """Paths related to migration for convenience."""

    comfyui_root: Path
    v1_db_path: Path
    v2_db_path: Path


class MigrationDetector:
    """Detects whether a migration from v1 to v2 is required."""

    def __init__(self, comfyui_root: str | Path | None = None) -> None:
        if not comfyui_root:
            raise ValueError(
                "comfyui_root is required. Cannot use current working directory. "
                "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
                "or provide the ComfyUI root path explicitly."
            )
        root = Path(comfyui_root)
        self.paths = MigrationPaths(
            comfyui_root=root,
            v1_db_path=root / "prompts.db",
            v2_db_path=root / "user" / "default" / "PromptManager" / "prompts.db",
        )
        # Cache to avoid repeated filesystem checks
        self._v1_exists_cache: Optional[bool] = None
        self._migration_status_cache: Optional[MigrationStatus] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 60.0  # Cache for 60 seconds

        LOGGER.info("MigrationDetector initialized with ComfyUI root: %s", self.paths.comfyui_root)
        LOGGER.debug("Looking for v1 database at: %s", self.paths.v1_db_path)

        # Load persistent cache from database on initialization
        self._load_persistent_cache()

    def _load_persistent_cache(self) -> None:
        """Load migration status from v2 database settings table for persistent caching."""
        if not self.paths.v2_db_path.exists():
            return

        try:
            with get_db_connection(str(self.paths.v2_db_path)) as connection:
                cursor = connection.execute(
                    "SELECT value FROM settings WHERE key = 'migration_status'"
                )
                result = cursor.fetchone()
                if result and result[0] in ['completed', 'fresh_start']:
                    # Set cache immediately to avoid filesystem checks
                    self._migration_status_cache = MigrationStatus.COMPLETED
                    self._cache_timestamp = time.time()
                    LOGGER.debug(
                        "Loaded persistent migration status from database: %s",
                        result[0]
                    )
        except sqlite3.Error:
            # Settings table might not exist yet, this is fine
            pass

    @property
    def comfyui_root(self) -> Path:
        return self.paths.comfyui_root

    @property
    def v1_db_path(self) -> Path:
        return self.paths.v1_db_path

    @property
    def v2_db_path(self) -> Path:
        return self.paths.v2_db_path

    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid based on TTL."""
        if self._cache_timestamp == 0.0:
            return False
        return (time.time() - self._cache_timestamp) < self._cache_ttl

    def _invalidate_cache(self) -> None:
        """Invalidate the cache to force fresh filesystem checks."""
        self._v1_exists_cache = None
        self._migration_status_cache = None
        self._cache_timestamp = 0.0

    def _migration_marker_exists(self) -> bool:
        return any(
            path.exists()
            for path in (
                self.v1_db_path.with_suffix(self.v1_db_path.suffix + ".migrated"),
                self.v1_db_path.with_suffix(self.v1_db_path.suffix + ".old"),
            )
        )

    def detect_v1_database(self) -> bool:
        """Return ``True`` if a legacy v1 database is present and needs migration."""
        if not self.v1_db_path.exists() or not self.v1_db_path.is_file():
            return False

        try:
            with get_db_connection(str(self.v1_db_path)) as connection:
                cursor = connection.execute("PRAGMA table_info(prompts)")
                columns = [row[1] for row in cursor.fetchall()]
        except sqlite3.Error as exc:  # pragma: no cover - corrupted file edge case
            LOGGER.error("Failed to inspect v1 schema: %s", exc)
            return False

        has_v1_schema = "text" in columns and "positive_prompt" not in columns
        if not has_v1_schema:
            return False

        if self._migration_marker_exists():
            LOGGER.info(
                "Legacy schema detected despite migration marker; assuming re-migration requested",
                extra={"v1_path": str(self.v1_db_path)},
            )

        return True

    def get_v1_database_info(self) -> Dict[str, Any]:
        """Return descriptive information about the legacy database if present."""
        info: Dict[str, Any] = {
            "path": str(self.v1_db_path),
            "exists": False,
            "size_bytes": 0,
            "size_mb": 0,
            "prompt_count": 0,
            "image_count": 0,
            "category_count": 0,
            "has_v1_schema": False,
        }

        if not self.v1_db_path.exists():
            # Debug level instead of warning - not having v1 database is a normal state
            LOGGER.debug(f"v1 database does not exist at {self.v1_db_path}")
            return info

        info["exists"] = True
        info["size_bytes"] = self.v1_db_path.stat().st_size
        info["size_mb"] = round(info["size_bytes"] / (1024 * 1024), 1)

        LOGGER.info(f"Reading v1 database info from {self.v1_db_path} (size: {info['size_mb']} MB)")

        try:
            with get_db_connection(str(self.v1_db_path)) as connection:
                connection.row_factory = sqlite3.Row
                cursor = connection.cursor()
                cursor.execute("PRAGMA table_info(prompts)")
                columns = [row[1] for row in cursor.fetchall()]
                info["has_v1_schema"] = "text" in columns and "positive_prompt" not in columns

                cursor.execute("SELECT COUNT(*) FROM prompts")
                info["prompt_count"] = int(cursor.fetchone()[0])

                cursor.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='generated_images'"
                )
                if cursor.fetchone()[0]:
                    cursor.execute("SELECT COUNT(*) FROM generated_images")
                    info["image_count"] = int(cursor.fetchone()[0])

                cursor.execute(
                    "SELECT COUNT(DISTINCT category) FROM prompts WHERE category IS NOT NULL AND category <> ''"
                )
                info["category_count"] = int(cursor.fetchone()[0])

                LOGGER.info(
                    f"Successfully read v1 database info: "
                    f"prompts={info['prompt_count']}, "
                    f"images={info['image_count']}, "
                    f"has_v1_schema={info['has_v1_schema']}"
                )
        except sqlite3.Error as exc:  # pragma: no cover - corrupted database
            LOGGER.error(f"Failed to read v1 database info: {exc}", exc_info=True)

        return info

    def _v2_database_has_data(self) -> bool:
        if not self.v2_db_path.exists():
            return False
        try:
            with get_db_connection(str(self.v2_db_path)) as connection:
                cursor = connection.execute("SELECT COUNT(*) FROM prompts")
                return int(cursor.fetchone()[0]) > 0
        except sqlite3.Error:
            return False

    def _v2_has_migration_marker(self) -> bool:
        """Check if v2 database has migration status marker in settings."""
        if not self.v2_db_path.exists():
            return False
        try:
            with get_db_connection(str(self.v2_db_path)) as connection:
                cursor = connection.execute(
                    "SELECT value FROM settings WHERE key = 'migration_status'"
                )
                result = cursor.fetchone()
                if result and result[0] in ['completed', 'fresh_start']:
                    LOGGER.info(f"Found migration marker in v2 database: {result[0]}")
                    return True
        except sqlite3.Error:
            # Table might not exist yet
            pass
        return False

    def check_migration_status(self) -> MigrationStatus:
        """Return the current migration status based on filesystem observations.

        Uses caching to avoid repeated filesystem checks. Cache is valid for 60 seconds.
        """
        # Return cached status if still valid
        if self._is_cache_valid() and self._migration_status_cache is not None:
            LOGGER.debug("Returning cached migration status: %s", self._migration_status_cache.value)
            return self._migration_status_cache

        # Check if v2 database exists and has migration markers
        if self._v2_has_migration_marker():
            LOGGER.info("Migration already completed - v2 database contains migration markers")
            status = MigrationStatus.COMPLETED
        # Check if v1 database exists and needs migration
        elif self.detect_v1_database():
            # Special case: v1 exists but is empty, and v2 already has data
            # This means someone created an empty v1 file or migration already happened
            v1_info = self.get_v1_database_info()
            if v1_info.get("prompt_count", 0) == 0 and self._v2_database_has_data():
                LOGGER.info(
                    "v1 database is empty but v2 has data - treating as already migrated"
                )
                status = MigrationStatus.COMPLETED
            else:
                status = MigrationStatus.PENDING
        # Check if there are old migration markers or v2 has data
        elif self._migration_marker_exists() or self._v2_database_has_data():
            status = MigrationStatus.COMPLETED
        else:
            status = MigrationStatus.NOT_NEEDED

        # Update cache
        self._migration_status_cache = status
        self._cache_timestamp = time.time()

        return status


class MigrationProgress:
    """Tracks in-memory migration progress for UI consumption."""

    def __init__(self) -> None:
        self.start_time: Optional[float] = None
        self.current_phase: MigrationPhase = MigrationPhase.IDLE
        self.phase_progress: float = 0.0
        self.overall_progress: float = 0.0
        self.message: str = ""
        self.items_processed: int = 0
        self.items_total: int = 0
        self._phase_completions: Dict[MigrationPhase, float] = {}

        self._phase_weights: Dict[MigrationPhase, float] = {
            MigrationPhase.BACKING_UP: 0.2,
            MigrationPhase.TRANSFORMING: 0.5,
            MigrationPhase.VERIFYING: 0.2,
            MigrationPhase.FINALIZING: 0.1,
        }

    def start(self) -> None:
        """Reset tracking and mark the migration as started."""
        self.start_time = time.time()
        self.current_phase = MigrationPhase.BACKING_UP
        self.phase_progress = 0.0
        self.overall_progress = 0.0
        self.message = "Starting migration..."
        self.items_processed = 0
        self.items_total = 0
        self._phase_completions = {phase: 0.0 for phase in self._phase_weights}

    def update_phase(self, phase: MigrationPhase, progress: float, message: str) -> None:
        """Update the current phase and compute overall progress."""
        progress = max(0.0, min(1.0, progress))
        if phase != self.current_phase and self.current_phase in self._phase_weights:
            self._phase_completions[self.current_phase] = 1.0
        self.current_phase = phase
        self.phase_progress = progress
        if phase in self._phase_weights:
            self._phase_completions[phase] = progress
        self.message = message
        self.overall_progress = self._calculate_overall_progress()

    def _calculate_overall_progress(self) -> float:
        total = 0.0
        for phase in (
            MigrationPhase.BACKING_UP,
            MigrationPhase.TRANSFORMING,
            MigrationPhase.VERIFYING,
            MigrationPhase.FINALIZING,
        ):
            weight = self._phase_weights[phase]
            completion = self._phase_completions.get(phase, 0.0)
            total += weight * min(1.0, max(0.0, completion))
        return min(1.0, total)

    def get_status(self) -> Dict[str, Any]:
        """Return a serialisable snapshot of the current progress state."""
        elapsed = 0
        remaining = 0
        if self.start_time is not None:
            elapsed = int(time.time() - self.start_time)
            if 0 < self.overall_progress < 1:
                estimated_total = elapsed / self.overall_progress
                remaining = max(0, int(estimated_total - elapsed))

        return {
            "phase": self.current_phase.value,
            "phase_progress": self.phase_progress,
            "overall_progress": self.overall_progress,
            "message": self.message,
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": remaining,
            "items_processed": self.items_processed,
            "items_total": self.items_total,
        }


class DatabaseMigrator:
    """Executes the migration workflow end-to-end."""

    def __init__(self, detector: Optional[MigrationDetector] = None, progress: Optional[MigrationProgress] = None) -> None:
        self.detector = detector or MigrationDetector()
        self.progress = progress or MigrationProgress()
        self.migration_stats: Dict[str, Any] = {}
        self.backup_path: Optional[Path] = None

    def migrate(self) -> Tuple[bool, Dict[str, Any]]:
        """Run the migration workflow, returning success and statistics."""
        LOGGER.info("Starting full migration run")
        if not self.detector.detect_v1_database():
            message = "Legacy database not found or already migrated"
            LOGGER.warning(message)
            self.progress.update_phase(MigrationPhase.ERROR, 0.0, message)
            return False, {"error": message, "status": MigrationStatus.FAILED.value}

        # Capture v1 counts BEFORE migration starts for verification
        v1_info = self.detector.get_v1_database_info()
        self.migration_stats["expected_prompts"] = v1_info.get("prompt_count", 0)
        self.migration_stats["expected_images"] = v1_info.get("image_count", 0)

        LOGGER.info(
            f"Captured v1 database counts before migration: "
            f"expected_prompts={self.migration_stats['expected_prompts']}, "
            f"expected_images={self.migration_stats['expected_images']}"
        )

        self.progress.start()

        try:
            self.progress.update_phase(MigrationPhase.BACKING_UP, 0.0, "Creating backup of v1 database")
            if not self.create_backup():
                raise RuntimeError("Failed to create backup")
            self.progress.update_phase(MigrationPhase.BACKING_UP, 1.0, "Backup complete")

            self.progress.update_phase(MigrationPhase.TRANSFORMING, 0.0, "Transforming data to v2 schema")
            if not self.transform_data():
                raise RuntimeError("Failed to transform data")
            self.progress.update_phase(MigrationPhase.TRANSFORMING, 1.0, "Data transformation complete")
            LOGGER.info(
                "Data transformed",
                extra={
                    "prompts_migrated": self.migration_stats.get("prompts_migrated"),
                    "images_migrated": self.migration_stats.get("images_migrated"),
                },
            )

            self.progress.update_phase(MigrationPhase.VERIFYING, 0.0, "Verifying migrated data")
            if not self.verify_migration():
                raise RuntimeError("Data verification failed")
            self.progress.update_phase(MigrationPhase.VERIFYING, 1.0, "Verification complete")
            LOGGER.info("Verification succeeded")

            self.progress.update_phase(MigrationPhase.FINALIZING, 0.0, "Finalising migration")
            if not self.finalize_migration():
                raise RuntimeError("Failed to finalise migration")
            self.progress.update_phase(MigrationPhase.FINALIZING, 1.0, "Migration finalised")

            self.progress.update_phase(MigrationPhase.COMPLETED, 1.0, "Migration completed successfully")
            LOGGER.info("Migration workflow completed", extra=self.migration_stats)
            return True, self.migration_stats
        except Exception as exc:  # pragma: no cover - caught for robustness
            LOGGER.error("Migration failed", exc_info=exc)
            self.progress.update_phase(MigrationPhase.ERROR, 0.0, f"Migration failed: {exc}")
            self.rollback()
            self.migration_stats.setdefault("error", str(exc))
            self.migration_stats["status"] = MigrationStatus.FAILED.value
            return False, self.migration_stats

    def _enable_wal_mode_on_v1(self) -> bool:
        """Enable WAL mode on v1 database for concurrent access during migration.

        Note: connection_helper already enables WAL mode and busy_timeout,
        so this is just a validation step.
        """
        try:
            with get_db_connection(str(self.detector.v1_db_path)) as conn:
                # Connection helper already sets WAL mode and busy_timeout
                # Just verify it's enabled
                result = conn.execute("PRAGMA journal_mode").fetchone()
                LOGGER.info(f"V1 database journal mode: {result[0] if result else 'unknown'}")
                return True
        except sqlite3.Error as exc:
            LOGGER.warning("Could not verify WAL mode on v1 database: %s", exc)
            # Not a critical error - migration can still proceed
            return False

    def create_backup(self) -> bool:
        """Create a timestamped backup of the legacy database."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.detector.v1_db_path.name}.backup_{timestamp}"
        self.backup_path = self.detector.v1_db_path.parent / backup_name

        # Enable WAL mode first to allow concurrent access
        self._enable_wal_mode_on_v1()

        try:
            # Copy the main database file
            shutil.copy2(self.detector.v1_db_path, self.backup_path)

            # Also copy WAL and SHM files if they exist
            wal_file = self.detector.v1_db_path.with_suffix(self.detector.v1_db_path.suffix + "-wal")
            shm_file = self.detector.v1_db_path.with_suffix(self.detector.v1_db_path.suffix + "-shm")

            if wal_file.exists():
                shutil.copy2(wal_file, self.backup_path.with_suffix(self.backup_path.suffix + "-wal"))
            if shm_file.exists():
                shutil.copy2(shm_file, self.backup_path.with_suffix(self.backup_path.suffix + "-shm"))

        except OSError as exc:
            LOGGER.error("Failed to create migration backup", exc_info=exc)
            return False
        self.migration_stats["backup_path"] = str(self.backup_path)
        LOGGER.info(
            "Migration backup created",
            extra={"backup_path": str(self.backup_path)},
        )
        return True

    def _ensure_v2_schema(self, connection: sqlite3.Connection) -> None:
        cursor = connection.cursor()
        cursor.executescript(
            """
            PRAGMA foreign_keys = OFF;
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY,
                positive_prompt TEXT NOT NULL,
                negative_prompt TEXT DEFAULT '',
                category TEXT,
                tags TEXT,
                rating INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
                notes TEXT,
                hash TEXT UNIQUE,
                model_hash TEXT,
                sampler_settings TEXT,
                generation_params TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS generated_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                generation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_size INTEGER,
                width INTEGER,
                height INTEGER,
                format TEXT,
                workflow_data TEXT,
                prompt_metadata TEXT,
                parameters TEXT,
                thumbnail_small_path TEXT,
                thumbnail_medium_path TEXT,
                thumbnail_large_path TEXT,
                thumbnail_xlarge_path TEXT,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analytics_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_type TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                metric_value TEXT NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(metric_type, metric_key)
            );
            CREATE TABLE IF NOT EXISTS analytics_tracking (
                id INTEGER PRIMARY KEY,
                table_name TEXT UNIQUE NOT NULL,
                last_processed_at TIMESTAMP,
                last_processed_id INTEGER,
                processing_status TEXT DEFAULT 'idle'
            );
            CREATE TABLE IF NOT EXISTS prompt_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                node_id TEXT,
                workflow_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_analytics_cache_type ON analytics_cache(metric_type);
            CREATE INDEX IF NOT EXISTS idx_analytics_tracking_table ON analytics_tracking(table_name);
            CREATE INDEX IF NOT EXISTS idx_prompt_tracking_session ON prompt_tracking(session_id);
            CREATE INDEX IF NOT EXISTS idx_prompt_tracking_created ON prompt_tracking(created_at);
            PRAGMA foreign_keys = ON;
            """
        )
        connection.commit()

    def transform_data(self) -> bool:
        """Copy data from the v1 schema into the new v2 schema."""
        v1_path = self.detector.v1_db_path
        v2_path = self.detector.v2_db_path
        v2_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize connections as None for proper cleanup
        v1_conn = None
        v2_conn = None

        try:
            # Open v1 database (connection_helper provides WAL mode and busy_timeout)
            v1_conn = DatabaseConnection.get_connection(str(v1_path))
            v1_conn.row_factory = sqlite3.Row

            # Open v2 database (connection_helper provides WAL mode and busy_timeout)
            v2_conn = DatabaseConnection.get_connection(str(v2_path))
            v2_conn.row_factory = sqlite3.Row

            self._ensure_v2_schema(v2_conn)

            # Clear existing data in V2 database instead of deleting file
            # This works even if other processes have the database open (WAL mode)
            if v2_path.exists():
                LOGGER.info("Clearing existing v2 database tables for clean migration")
                try:
                    # Disable foreign keys temporarily for deletion
                    v2_conn.execute("PRAGMA foreign_keys = OFF")

                    # Clear all tables in correct order (respecting dependencies)
                    # Delete child tables first to avoid foreign key violations
                    v2_conn.execute("DELETE FROM collection_items")
                    v2_conn.execute("DELETE FROM generated_images")
                    v2_conn.execute("DELETE FROM prompt_tracking")
                    v2_conn.execute("DELETE FROM collections")
                    v2_conn.execute("DELETE FROM prompts")
                    v2_conn.execute("DELETE FROM app_settings")

                    # Reset autoincrement counters
                    v2_conn.execute("""
                        DELETE FROM sqlite_sequence
                        WHERE name IN ('generated_images', 'prompts', 'prompt_tracking',
                                       'collections', 'collection_items')
                    """)

                    v2_conn.commit()
                    v2_conn.execute("PRAGMA foreign_keys = ON")

                    LOGGER.info("Successfully cleared v2 database tables")
                except sqlite3.Error as exc:
                    LOGGER.warning("Failed to clear some v2 database tables: %s", exc)
                    # Continue anyway - tables might not exist yet or already be empty
                    try:
                        v2_conn.rollback()
                    except sqlite3.Error:
                        pass
                    v2_conn.execute("PRAGMA foreign_keys = ON")

            # Tables are now clear and ready for migration

            prompts = v1_conn.execute(
                "SELECT * FROM prompts ORDER BY id"
            ).fetchall()
            images = []
            try:
                image_columns = {
                    row[1] for row in v1_conn.execute("PRAGMA table_info(generated_images)")
                }
            except sqlite3.Error as exc:  # pragma: no cover - diagnostics aid
                LOGGER.warning("Unable to inspect generated_images table", exc_info=exc)
                image_columns = set()

            if image_columns:
                try:
                    images = v1_conn.execute("SELECT * FROM generated_images").fetchall()
                except sqlite3.Error as exc:  # pragma: no cover - defensive
                    LOGGER.warning(
                        "Failed to read generated_images table, continuing without images",
                        exc_info=exc,
                    )
                    images = []

            total_items = len(prompts) + len(images)
            self.progress.items_total = total_items
            self.progress.items_processed = 0

            categories = set()
            for index, prompt in enumerate(prompts, start=1):
                record = dict(prompt)
                positive_prompt = (
                    record.get("text")
                    or record.get("prompt")
                    or record.get("positive_prompt")
                    or ""
                )
                negative_prompt = record.get("negative_prompt") or ""
                category = record.get("category")
                tags = record.get("tags")
                if tags is None:
                    tags = "[]"
                # Convert invalid ratings (0 or outside 1-5 range) to NULL for v2 compatibility
                rating = record.get("rating")
                if rating is not None and (rating < 1 or rating > 5):
                    rating = None
                notes = record.get("notes")
                params = (
                    record.get("id"),
                    positive_prompt,
                    negative_prompt,
                    category,
                    tags,
                    rating,
                    notes,
                    record.get("hash"),
                    record.get("model_hash"),
                    record.get("sampler_settings"),
                    record.get("generation_params"),
                    record.get("created_at"),
                    record.get("updated_at"),
                )
                v2_conn.execute(
                    """
                    INSERT OR REPLACE INTO prompts (
                        id, positive_prompt, negative_prompt, category, tags, rating, notes,
                        hash, model_hash, sampler_settings, generation_params, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
                categories.add(category)
                self.migration_stats["prompts_migrated"] = index
                self.progress.items_processed += 1
                progress_value = self.progress.items_processed / total_items if total_items else 1.0
                self.progress.update_phase(
                    MigrationPhase.TRANSFORMING,
                    progress_value,
                    f"Migrated {self.progress.items_processed}/{total_items} records",
                )

            migrated_images = 0
            for image in images:
                try:
                    prompt_id = int(image["prompt_id"]) if image["prompt_id"] is not None else None
                except (TypeError, ValueError):
                    continue
                if prompt_id is None:
                    continue

                # Normalise legacy column names
                image_record = dict(image)
                file_path = next(
                    (
                        image_record.get(key)
                        for key in ("file_path", "image_path", "path", "filepath")
                        if image_record.get(key)
                    ),
                    None,
                )
                file_name = next(
                    (
                        image_record.get(key)
                        for key in ("file_name", "filename", "name")
                        if image_record.get(key)
                    ),
                    None,
                )
                if not file_name and file_path:
                    file_name = Path(file_path).name

                metadata = next(
                    (
                        image_record.get(key)
                        for key in ("metadata", "meta", "raw_metadata")
                        if key in image_record
                    ),
                    None,
                )

                if file_path is None and file_name is None:
                    LOGGER.debug(
                        "Skipping generated image record without file information",
                        extra={"prompt_id": prompt_id},
                    )
                    continue

                v2_conn.execute(
                    """
                    INSERT INTO generated_images (prompt_id, image_path, filename, parameters)
                    VALUES (?, ?, ?, ?)
                    """,
                    (prompt_id, file_path, file_name, metadata),
                )
                migrated_images += 1
                self.progress.items_processed += 1
                progress_value = self.progress.items_processed / total_items if total_items else 1.0
                self.progress.update_phase(
                    MigrationPhase.TRANSFORMING,
                    progress_value,
                    f"Migrated {self.progress.items_processed}/{total_items} records",
                )

            v2_conn.commit()
            self.migration_stats.setdefault("prompts_migrated", len(prompts))
            self.migration_stats["images_migrated"] = migrated_images
            self.migration_stats["images_linked"] = migrated_images
            self.migration_stats["categories_migrated"] = len({c for c in categories if c})

            # Populate missing metadata (file size, dimensions) in the new database
            repo = GeneratedImageRepository(str(v2_path))
            metadata_stats = repo.populate_missing_file_metadata()
            self.migration_stats["file_metadata_updated"] = metadata_stats.get("updated", 0)
        except sqlite3.Error as exc:
            LOGGER.error("Data transformation failed", exc_info=exc)
            return False
        finally:
            # Ensure connections are properly closed
            if v2_conn:
                try:
                    v2_conn.close()
                except Exception:
                    pass
            if v1_conn:
                try:
                    v1_conn.close()
                except Exception:
                    pass

        return True

    def verify_migration(self) -> bool:
        """Ensure the migrated database matches expected record counts."""
        # Use counts captured BEFORE migration started (stored in migration_stats)
        expected_prompts = self.migration_stats.get("expected_prompts", 0)
        expected_images = self.migration_stats.get("expected_images", 0)
        v2_path = self.detector.v2_db_path

        LOGGER.info(
            f"Starting migration verification: "
            f"expected_prompts={expected_prompts}, "
            f"expected_images={expected_images}"
        )

        try:
            with get_db_connection(str(v2_path)) as connection:
                cursor = connection.execute("SELECT COUNT(*) FROM prompts")
                prompt_count = int(cursor.fetchone()[0])
                cursor = connection.execute("SELECT COUNT(*) FROM generated_images")
                image_count = int(cursor.fetchone()[0])
        except sqlite3.Error as exc:
            LOGGER.error("Verification failed", exc_info=exc)
            return False

        LOGGER.info(
            f"Comparing migration counts: "
            f"expected_prompts={expected_prompts}, actual_prompts={prompt_count}, "
            f"expected_images={expected_images}, actual_images={image_count}"
        )

        if prompt_count != expected_prompts:
            LOGGER.error(
                "Prompt count mismatch during migration verification",
                extra={"expected_prompts": expected_prompts, "actual_prompts": prompt_count},
            )
            return False
        if expected_images and image_count < expected_images:
            LOGGER.error(
                "Image count mismatch during migration verification",
                extra={"expected_min_images": expected_images, "actual_images": image_count},
            )
            return False
        self.migration_stats.setdefault("prompts_migrated", prompt_count)
        self.migration_stats["images_migrated"] = image_count
        self.migration_stats["images_linked"] = image_count
        return True

    def finalize_migration(self) -> bool:
        """Rename the original database to mark migration completion.

        Uses Windows-safe file operations with retry logic and copy fallback.
        """
        migrated_path = self.detector.v1_db_path.with_suffix(
            self.detector.v1_db_path.suffix + ".migrated"
        )

        # Force close any lingering connections before rename
        close_all_sqlite_connections(self.detector.v1_db_path)

        # Checkpoint WAL file to reduce lock contention
        checkpoint_wal_file(self.detector.v1_db_path)

        try:
            # Use Windows-safe rename with copy fallback
            safe_rename_database(
                self.detector.v1_db_path,
                migrated_path,
                use_copy_fallback=True,
            )

            self.migration_stats["original_renamed_to"] = str(migrated_path)
            if self.progress.start_time is not None:
                self.migration_stats["duration_seconds"] = int(
                    time.time() - self.progress.start_time
                )
            completed_at = datetime.now(UTC).isoformat()
            self.migration_stats["completed_at"] = completed_at
            self.migration_stats["status"] = MigrationStatus.COMPLETED.value

            LOGGER.info(
                "Migration finalised",
                extra={
                    "backup_path": self.migration_stats.get("backup_path"),
                    "migrated_path": str(migrated_path),
                    "duration_seconds": self.migration_stats.get("duration_seconds"),
                },
            )

            self._persist_migration_settings(
                {
                    "migration_status": MigrationStatus.COMPLETED.value,
                    "migration_completed_at": completed_at,
                    "migration_backup_path": self.migration_stats.get("backup_path", ""),
                    "migration_original_path": str(migrated_path),
                    "migration_prompts": str(self.migration_stats.get("prompts_migrated", 0)),
                    "migration_images": str(self.migration_stats.get("images_migrated", 0)),
                    "migration_categories": str(self.migration_stats.get("categories_migrated", 0)),
                }
            )

            # Invalidate cache so future checks get fresh migration status
            self.detector._invalidate_cache()

            return True
        except FileOperationError as exc:
            LOGGER.error("Failed to finalise migration", exc_info=exc)
            return False

    def rollback(self) -> None:
        """Attempt to restore the system to its pre-migration state."""
        LOGGER.info("Rolling back migration changes")
        with contextlib.suppress(OSError):
            if self.detector.v2_db_path.exists():
                self.detector.v2_db_path.unlink()
        if self.backup_path and self.backup_path.exists() and not self.detector.v1_db_path.exists():
            try:
                shutil.copy2(self.backup_path, self.detector.v1_db_path)
                self.migration_stats["status"] = MigrationStatus.ROLLED_BACK.value
                LOGGER.info("Restored original database from backup", extra={"backup_path": str(self.backup_path)})
            except OSError as exc:  # pragma: no cover - very rare
                LOGGER.error("Failed to restore backup", exc_info=exc)

    def start_fresh(self) -> bool:
        """Archive the legacy database so the application can start with a clean slate.

        Uses Windows-safe file operations with retry logic.
        """
        if not self.detector.v1_db_path.exists():
            return True

        old_path = self.detector.v1_db_path.with_suffix(
            self.detector.v1_db_path.suffix + ".old"
        )

        # Force close any lingering connections
        close_all_sqlite_connections(self.detector.v1_db_path)
        checkpoint_wal_file(self.detector.v1_db_path)

        try:
            # Use Windows-safe rename with copy fallback
            safe_rename_database(
                self.detector.v1_db_path,
                old_path,
                use_copy_fallback=True,
            )

            self.migration_stats["original_renamed_to"] = str(old_path)
            self.migration_stats["status"] = "fresh_start"

            self._persist_migration_settings(
                {
                    "migration_status": "fresh_start",
                    "migration_completed_at": datetime.now(UTC).isoformat(),
                    "migration_original_path": str(old_path),
                }
            )

            # Invalidate cache so future checks get fresh migration status
            self.detector._invalidate_cache()

            return True
        except FileOperationError as exc:
            LOGGER.error("Failed to archive legacy database", exc_info=exc)
            return False

    def _persist_migration_settings(self, settings_map: Dict[str, str]) -> None:
        """Persist migration metadata into the v2 settings table."""
        if not settings_map:
            return
        try:
            with get_db_connection(str(self.detector.v2_db_path)) as conn:
                conn.executemany(
                    """
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    list(settings_map.items()),
                )
                conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover - best-effort only
            LOGGER.warning("Unable to persist migration metadata", exc_info=exc)
