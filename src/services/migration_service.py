"""High-level migration service orchestrating detector, progress, and migrator."""

from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Dict, Optional

from ..database.migration import (
    DatabaseMigrator,
    MigrationDetector,
    MigrationPhase,
    MigrationProgress,
    MigrationStatus,
)

try:  # pragma: no cover - fall back during standalone execution
    from promptmanager.loggers import get_logger
except ImportError:  # pragma: no cover
    import logging

    def get_logger(name: str) -> "logging.Logger":
        return logging.getLogger(name)


LOGGER = get_logger("promptmanager.services.migration")

MigratorFactory = Callable[[MigrationDetector, MigrationProgress], DatabaseMigrator]


class MigrationService:
    """Facade for migration APIs exposed to ComfyUI and the REST layer."""

    def __init__(
        self,
        detector: Optional[MigrationDetector] = None,
        progress: Optional[MigrationProgress] = None,
        migrator_factory: Optional[MigratorFactory] = None,
    ) -> None:
        self.detector = detector or MigrationDetector()
        self.progress = progress or MigrationProgress()
        self._progress_provided = progress is not None
        self._migrator_factory: MigratorFactory = migrator_factory or (
            lambda detector, progress: DatabaseMigrator(detector=detector, progress=progress)
        )
        self._lock = Lock()
        self._last_result: Dict[str, Any] = {}

    def get_migration_info(self) -> Dict[str, Any]:
        """Return a snapshot describing the current migration requirement."""
        status = self.detector.check_migration_status()
        info = self.detector.get_v1_database_info()
        LOGGER.info(
            "Migration status checked",
            extra={"status": status.value, "needed": status == MigrationStatus.PENDING},
        )
        return {
            "needed": status == MigrationStatus.PENDING,
            "status": status.value,
            "v1_info": info,
        }

    def get_progress(self) -> Dict[str, Any]:
        """Expose progress tracking information for polling endpoints."""
        return self.progress.get_status()

    def start_migration(self, action: str) -> Dict[str, Any]:
        """Execute the requested migration flow.

        Args:
            action: Either ``"migrate"`` for full migration or ``"fresh"`` to archive legacy data.

        Returns:
            A dictionary containing ``success`` flag, ``status`` enum value, and optional stats.

        Raises:
            ValueError: If the requested action is not supported.
        """
        normalised = action.strip().lower()
        if normalised not in {"migrate", "fresh"}:
            LOGGER.warning("Unsupported migration action requested", extra={"action": action})
            raise ValueError(f"Unsupported migration action: {action}")

        with self._lock:
            if not self._progress_provided:
                self.progress = MigrationProgress()
            migrator = self._migrator_factory(self.detector, self.progress)

            LOGGER.info("Starting migration action", extra={"action": normalised})
            if normalised == "migrate":
                # Check if migration is actually needed before attempting
                migration_status = self.detector.check_migration_status()
                if migration_status != MigrationStatus.PENDING:
                    LOGGER.warning(
                        f"Migration requested but status is {migration_status.value}. "
                        f"No v1 database found or migration already completed."
                    )
                    success = False
                    stats = {
                        "error": f"Migration not needed. Status: {migration_status.value}",
                        "status": migration_status.value,
                    }
                else:
                    success, stats = migrator.migrate()
            else:
                self.progress.start()
                success = migrator.start_fresh()
                if success:
                    self.progress.update_phase(
                        MigrationPhase.FINALIZING,
                        1.0,
                        "Legacy database archived; ready for fresh start",
                    )
                    self.progress.update_phase(
                        MigrationPhase.COMPLETED,
                        1.0,
                        "Fresh database initialised",
                    )
                    stats = getattr(migrator, "migration_stats", {})
                else:
                    stats = {"error": "Failed to archive legacy database"}

            status = MigrationStatus.COMPLETED if success else MigrationStatus.FAILED
            result = {
                "success": success,
                "status": status.value,
                "stats": stats,
            }
            self._last_result = result
            LOGGER.info(
                "Migration action completed",
                extra={
                    "action": normalised,
                    "status": status.value,
                    "success": success,
                    "prompts_migrated": stats.get("prompts_migrated"),
                    "images_migrated": stats.get("images_migrated"),
                },
            )
            return result
