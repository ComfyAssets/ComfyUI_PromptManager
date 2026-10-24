"""Store each workflow once: the migration and Reclaim space jobs (#249 S6).

Reviewed like every maintenance operation (``POST /prompt_manager/
maintenance/preview {operation:"workflow_storage"}``, a ``Plan`` with the
records to rewrite, the backup and the disk check) and applied with its token
(``POST /prompt_manager/maintenance/apply``). The apply starts:

1. **Store each workflow once**: kind ``maintenance``, admission ``records``,
   cancellable, groups ``changed``/``protected``/``errors``. It always takes
   a verified pre-migration backup, whatever the safety backup policy, named
   ``<db>.pre-workflow-storage_<stamp>`` outside the pruned ``<db>.backup_*``
   pool and kept until the user removes it (``backup-failed`` ends the job
   with nothing rewritten). It then rewrites old-form rows batch by batch
   (``database/workflow_storage.py``): one transaction per batch of at most
   ``BATCH_SECONDS``, then ``BATCH_PAUSE`` with the write lock free, progress
   after each, Cancel honoured between batches. A row that cannot be
   rewritten stays inline and is listed in ``errors``; any other failure
   rolls its batch back whole and fails the job. Every record still reads as
   before, and the next apply continues from the first old-form row.
2. **Reclaim space**, once every record was looked at: kind ``maintenance``,
   admission ``exclusive`` (it runs alone; VACUUM holds the write lock), not
   cancellable. It checks the disk for VACUUM's copies of the live pages
   (``disk_plan``), then sweeps, runs VACUUM and truncates the WAL. When
   another job holds the registry, it does not start and the finished
   migration says so and names Optimize database.

Only the database changes: no image or video file is opened.
"""

import asyncio
import sqlite3

from .jobs import MAINTENANCE_JOB_KIND, JobBusy
from .maintenance import (
    MAINTENANCE_GROUPS,
    PRE_MIGRATION_LABEL,
    SafetyBackupFailed,
    backup_failed_message,
    _fail_job,
    _megabytes,
    disk_plan,
    short_space_message,
    take_safety_backup,
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
ROW_KEPT = "could not be rewritten; it keeps its inline text and reads as before"


NOTHING_LEFT = "Every image record already stores its workflow once"


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

    async def _run_workflow_storage(self, job, db, _backup_on):
        # Always backed up first, whatever the policy: the way back. The first
        # apply keeps a pre-migration backup; later ones (after a Cancel, a
        # rerun) take a pooled safety backup, so only one copy is never pruned.
        counts = await self._run_in_executor(db.workflow_storage_status)
        if not counts["rows"]:
            job.log_line(NOTHING_LEFT)
            job.update(progress=100, status_line=NOTHING_LEFT)
            return
        kept = await self._run_in_executor(
            workflow_storage.pre_migration_backups, db.model.db_path
        )
        label = "backup" if kept else PRE_MIGRATION_LABEL
        backup = await self._backup_before_rewrite(job, db, label, kept)
        if backup is None:
            return
        try:
            done = await self._rewrite_workflows(job, db, backup)
        finally:
            if kept:
                await self._prune_backup_pool(job, db)
        if done:
            job.finish("finished")
            self._start_reclaim_space(job, db)

    async def _backup_before_rewrite(self, job, db, label, kept):
        """The verified backup's status note, or None (the job failed).

        ``label`` ``pre-workflow-storage``: kept until the user removes it.
        ``backup``: a pooled safety backup, while the earliest kept
        pre-migration backup (``kept[0]``) stays. A failure ends the job
        ``backup-failed``, nothing changed.
        """
        noun = "safety backup" if kept else "pre-migration backup"
        job.update(status_line=f"Taking the {noun}")
        job.log_line(f"Taking the {noun} of the database")
        try:
            name = await self._run_in_executor(take_safety_backup, db.model, label)
        except SafetyBackupFailed as error:
            self.logger.error("Backup before the rewrite failed", exc_info=True)
            _fail_job(job, "backup-failed", backup_failed_message(error))
            return None
        if kept:
            job.log_line(
                f"Safety backup {name} verified; the pre-migration backup "
                f"{kept[0]} is kept"
            )
            return f"Safety backup {name}"
        job.log_line(
            f"Pre-migration backup {name} verified; it is kept until you remove it"
        )
        return f"Pre-migration backup {name}"

    async def _rewrite_workflows(self, job, db, backup):
        """Rewrite every old-form row; True when every row was looked at."""
        counts = await self._run_in_executor(db.workflow_storage_status)
        job.update(total=counts["rows"], processed=0)
        job.update(status_line="Rewriting image records")
        job.log_line(f"{_plural(counts['rows'], 'image record')} to rewrite")
        rows = protected = errors = 0
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
            errors += len(batch["errors"])
            job.record_counted("changed", batch["rows"])
            job.record_counted("protected", batch["protected"])
            for image_id in batch["errors"]:
                job.record(
                    "errors",
                    {"path": "", "detail": f"Image record {image_id}: {ROW_KEPT}"},
                    f"! Image record {image_id}: {ROW_KEPT}",
                )
            total = max(job.total, rows)
            job.update(
                processed=rows,
                total=total,
                progress=100 * rows // total if total else 100,
                status_line=f"Rewrote {_plural(rows, 'image record')}",
            )
            job.publish({"type": "progress", "processed": rows, "total": total})
            # Leave the write lock free between batches (monitor, run counts).
            await asyncio.sleep(workflow_storage.BATCH_PAUSE)
        removed = await self._run_in_executor(db.sweep_workflow_documents)
        line = f"Rewrote {_plural(rows, 'image record')}"
        if protected:
            line += f"; {_plural(protected, 'stored value')} not text, kept as is"
        if errors:
            kept = "keeps its" if errors == 1 else "keep their"
            line += (
                f"; {_plural(errors, 'image record')} could not be rewritten and "
                f"{kept} inline text"
            )
        job.log_line(line)
        if removed:
            job.log_line(f"Removed {_plural(removed, 'unused workflow document')}")
        job.update(progress=100, status_line=f"{line} · {backup}")
        return True

    def _stopped(self, job, rows, backup):
        line = (
            f"Stopped after {_plural(rows, 'image record')}; "
            "run it again to continue"
        )
        job.log_line(line)
        job.update(status_line=f"{line} · {backup}")
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
