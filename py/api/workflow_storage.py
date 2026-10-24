"""Store each workflow once: the migration and Reclaim space jobs (#249 S6).

Reviewed like every maintenance operation (``POST /prompt_manager/
maintenance/preview {operation:"workflow_storage"}``, a ``Plan`` with the
records to rewrite, the backup and the disk check) and applied with its token
(``POST /prompt_manager/maintenance/apply``). The apply starts:

1. **Store each workflow once**: kind ``maintenance``, admission ``records``,
   cancellable, groups ``changed``/``protected``/``errors``. It takes the
   promised verified safety backup (``backup-failed`` ends it with nothing
   rewritten), then rewrites old-form rows batch by batch
   (``database/workflow_storage.py``): one transaction per batch, progress
   after each, Cancel honoured between batches. A failed batch rolls back
   whole and fails the job; every record still reads as before. Stopped or
   failed, the next apply continues from the first old-form row.
2. **Reclaim space**, once every record was looked at: kind ``maintenance``,
   admission ``exclusive`` (it runs alone; VACUUM holds the write lock), not
   cancellable. It checks the disk for VACUUM's copies of the live pages
   (``disk_plan``), then sweeps, runs VACUUM and truncates the WAL. When
   another job holds the registry, it does not start and the finished
   migration says so and names Optimize database.

Only the database changes: no image or video file is opened.
"""

import sqlite3

from .jobs import MAINTENANCE_JOB_KIND, JobBusy
from .maintenance import (
    MAINTENANCE_GROUPS,
    _fail_job,
    _megabytes,
    backup_note,
    disk_plan,
    short_space_message,
)

try:
    from ...database import workflow_storage
    from ...database.health import readonly_uri
except ImportError:
    from database import workflow_storage
    from database.health import readonly_uri

OPERATION = workflow_storage.OPERATION
TITLE = "Store each workflow once"
RECLAIM_TITLE = "Reclaim space"
REWRITE_FAILED = (
    "Storing workflows once stopped on an error. The batch in progress was "
    "rolled back; every record reads as before. Run it again to continue."
)
RECLAIM_FAILED = "Reclaim space failed; the database is unchanged."


def _plural(count, one, many=None):
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def live_bytes(db_path):
    """The pages VACUUM copies: page_count minus the free list (blocking)."""
    conn = sqlite3.connect(readonly_uri(db_path), uri=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return page_size * (pages - free)
    finally:
        conn.close()


class WorkflowStorageRoutesMixin:
    """Mixin for the maintenance routes: the workflow storage jobs."""

    def _start_workflow_storage(self, db, backup):
        """Start the migration job (raises JobBusy); the token is the caller's."""
        return self._jobs.start(
            MAINTENANCE_JOB_KIND,
            TITLE,
            admission="records",
            cancellable=True,
            scope={"operation": OPERATION},
            groups=MAINTENANCE_GROUPS,
            runner=lambda job: self._run_workflow_storage(job, db, backup),
        )

    async def _run_workflow_storage(self, job, db, backup_on):
        ok, backup = await self._safety_backup_step(job, db, backup_on)
        if not ok:
            return
        try:
            done = await self._rewrite_workflows(job, db, backup)
        finally:
            if backup:
                await self._prune_backup_pool(job, db)
        if done:
            job.finish("finished")
            self._start_reclaim_space(job, db)

    async def _rewrite_workflows(self, job, db, backup):
        """Rewrite every old-form row; True when every row was looked at."""
        counts = await self._run_in_executor(db.workflow_storage_status)
        job.update(total=counts["rows"], processed=0)
        job.update(status_line="Rewriting image records")
        job.log_line(f"{_plural(counts['rows'], 'image record')} to rewrite")
        rows = protected = 0
        last_id = 0
        while True:
            if job.cancelled:
                return self._stopped(job, rows, backup)
            try:
                batch = await self._run_in_executor(
                    db.migrate_workflow_batch,
                    last_id,
                    workflow_storage.BATCH_ROWS,
                    workflow_storage.BATCH_BYTES,
                )
            except Exception:
                self.logger.exception("Storing workflows once failed")
                _fail_job(job, None, REWRITE_FAILED)
                return False
            if batch["done"]:
                break
            last_id = batch["last_id"]
            rows += batch["rows"]
            protected += batch["protected"]
            job.record_counted("changed", batch["rows"])
            job.record_counted("protected", batch["protected"])
            total = max(job.total, rows)
            job.update(
                processed=rows,
                total=total,
                progress=100 * rows // total if total else 100,
                status_line=f"Rewrote {_plural(rows, 'image record')}",
            )
            job.publish({"type": "progress", "processed": rows, "total": total})
        removed = await self._run_in_executor(db.sweep_workflow_documents)
        line = f"Rewrote {_plural(rows, 'image record')}"
        if protected:
            line += f"; {_plural(protected, 'stored value')} not text, kept as is"
        job.log_line(line)
        if removed:
            job.log_line(f"Removed {_plural(removed, 'unused workflow document')}")
        job.update(progress=100, status_line=f"{line} · {backup_note(backup)}")
        return True

    def _stopped(self, job, rows, backup):
        line = (
            f"Stopped after {_plural(rows, 'image record')}; "
            "run it again to continue"
        )
        job.log_line(line)
        job.update(status_line=f"{line} · {backup_note(backup)}")
        job.finish("cancelled")
        return False

    def _start_reclaim_space(self, finished, db):
        """Start Reclaim space after the migration ``finished``, or say why not."""
        try:
            self._jobs.start(
                MAINTENANCE_JOB_KIND,
                RECLAIM_TITLE,
                admission="exclusive",
                cancellable=False,
                scope={"operation": OPERATION},
                groups=MAINTENANCE_GROUPS,
                runner=lambda job: self._run_reclaim_space(job, db),
            )
        except JobBusy as busy:
            line = (
                f"Reclaim space did not start: {busy.running.title} is running. "
                "Run Optimize database to give the space back."
            )
            finished.log_line(line)
            finished.update(status_line=f"{finished.status_line} · {line}")
            return
        finished.log_line("Reclaim space started")

    async def _run_reclaim_space(self, job, db):
        job.update(status_line="Checking free disk space")
        try:
            live = await self._run_in_executor(live_bytes, db.model.db_path)
            _disk, short, _size = await self._run_in_executor(
                disk_plan,
                db.model.db_path,
                backup=False,
                vacuum=True,
                vacuum_bytes=live,
            )
        except (OSError, sqlite3.Error):
            self.logger.exception("Could not measure the disk space for Reclaim space")
            return _fail_job(job, None, RECLAIM_FAILED)
        if short is not None:
            return _fail_job(job, "insufficient-space", short_space_message(short))
        job.update(status_line="Reclaiming space (the database is locked)")
        job.log_line("Rebuilding the database file (VACUUM)")
        try:
            result = await self._run_in_executor(db.reclaim_space)
        except Exception:
            self.logger.exception("Reclaim space failed")
            return _fail_job(job, None, RECLAIM_FAILED)
        saved = max(0, result["before_bytes"] - result["after_bytes"])
        line = (
            f"Reclaimed {_megabytes(saved)} · the database is now "
            f"{_megabytes(result['after_bytes'])}"
        )
        if result["checkpoint_busy"]:
            line += " (the rest after the next restart)"
        job.record_counted("changed", 1)
        job.log_line(line)
        job.update(progress=100, status_line=line)
