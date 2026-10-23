"""System routes: the read-only health check, the Overview and usage statistics.

- ``POST /prompt_manager/system/health`` starts the health check
  (``database/health.py``) off-loop as a ``read`` job of kind ``health``: one
  at a time, beside a scan, not cancellable and with no file outcomes. The
  API keeps the last report for this session; ``GET`` returns it or null.
  A report describes the database it was taken from: replacing the database
  (a restore) forgets it, and a check that started before the swap is not
  kept.
- ``GET /prompt_manager/system/overview`` is the System › Overview aggregate.
  Every value is real or null: there is no backup status until backups report
  one and no health result until a check ran; ``needs_review.pending`` is the
  Needs review queue's pending total. The missing-thumbnail count is not part
  of it: the page reads ``/images/thumbnails/status``, which walks every
  source.
- ``GET /prompt_manager/system/statistics`` lists runs and ratings per
  category, with the legacy ``/stats`` totals and the Overview's count of
  rated images (images whose linked prompt is rated).

New routes answer errors with ``{success:false, code, error}``.
"""

import asyncio
import os
from collections import namedtuple

from aiohttp import web

from .job_routes import busy_response
from .jobs import HEALTH_JOB_KIND, JobBusy

HEALTH_JOB_TITLE = "Health check"
_HEALTH_STATUS_LINES = {
    "passed": "Health check passed",
    "issues": "Health check found issues",
    "incomplete": "Health check incomplete",
}


# The last health report and the database generation it was taken from.
_StoredReport = namedtuple("_StoredReport", "generation report")


async def _gather_settled(*reads):
    """Await every read, then raise the first failure.

    Unlike a plain gather, a failure never answers while the other executor
    reads still run against the database.
    """
    results = await asyncio.gather(*reads, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def _failure(message, status=500, code=None):
    return web.json_response(
        {"success": False, "code": code, "error": message}, status=status
    )


def health_summary(report):
    """What the Overview shows of a report: when, the outcome, checks with issues."""
    if report is None:
        return None
    return {
        "checked_at": report["checked_at"],
        "status": report["status"],
        "issues": sum(1 for check in report["checks"] if check["status"] == "issues"),
    }


class SystemRoutesMixin:
    """Mixin providing the /prompt_manager/system endpoints."""

    def _register_system_routes(self, routes):
        @routes.post("/prompt_manager/system/health")
        async def start_health_check_route(request):
            return await self.start_health_check(request)

        @routes.get("/prompt_manager/system/health")
        async def health_report_route(request):
            return await self.get_health_report(request)

        @routes.get("/prompt_manager/system/overview")
        async def system_overview_route(request):
            return await self.get_system_overview(request)

        @routes.get("/prompt_manager/system/statistics")
        async def system_statistics_route(request):
            return await self.get_system_statistics(request)

    # ── health ────────────────────────────────────────────────────────

    def _health_roots(self):
        """The sources missing files are judged under.

        The effective sources, plus every configured source that is not an
        existing directory (an unmounted drive): the check then reports
        missing files as unavailable instead of counting that source's links.
        """
        from ..config import GalleryConfig

        roots = [str(path) for path in self._get_all_output_dirs()]
        for configured in GalleryConfig.MONITORING_DIRECTORIES:
            try:
                resolved = GalleryConfig.resolve_gallery_root(configured)
            except (OSError, TypeError, ValueError):
                continue
            if not os.path.isdir(resolved):
                roots.append(resolved)
        return roots

    def _database_replaced(self):
        """Forget what described the previous database (call after swapping ``db``)."""
        self._db_generation += 1
        self._health_report = None

    def _current_health_report(self):
        """The last report, or None when none was kept for the current database."""
        stored = self._health_report
        generation = self._db_generation
        if isinstance(stored, _StoredReport) and stored.generation == generation:
            return stored.report
        return None

    async def start_health_check(self, request):
        """Start the health check job (202), or 409 naming the running check."""
        generation = self._db_generation
        db = self.db
        try:
            roots = await self._run_in_executor(self._health_roots)
        except Exception:
            self.logger.exception("Health check could not resolve the image sources")
            return _failure("Failed to start the health check")

        async def run(job):
            job.update(status_line="Checking the database and image links")
            report = await self._run_in_executor(db.run_health_checks, roots)
            if generation == self._db_generation:
                self._health_report = _StoredReport(generation, report)
            else:
                self.logger.info("Health report dropped: the database was replaced")
            job.update(progress=100, status_line=_HEALTH_STATUS_LINES[report["status"]])

        try:
            job = self._jobs.start(
                HEALTH_JOB_KIND,
                HEALTH_JOB_TITLE,
                admission="read",
                cancellable=False,
                scope={},
                runner=run,
            )
        except JobBusy as busy:
            return busy_response(busy)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def get_health_report(self, request):
        return web.json_response(
            {"success": True, "report": self._current_health_report()}
        )

    # ── overview ──────────────────────────────────────────────────────

    async def get_system_overview(self, request):
        from ..config import GalleryConfig

        try:
            (output_dirs, auto_detected), counts = await _gather_settled(
                self._run_in_executor(self._output_dirs_with_origin),
                self._run_in_executor(self.db.library_counts),
            )
        except Exception:
            self.logger.exception("System overview failed")
            return _failure("Failed to read the overview")
        return web.json_response(
            {
                "success": True,
                "library": {
                    "positive_prompts": counts["positive_prompts"],
                    "negative_variants": counts["negative_variants"],
                },
                "images": {
                    "indexed": counts["images"],
                    "rated": counts["rated_images"],
                },
                "health": health_summary(self._current_health_report()),
                "backup": None,  # no verified backup status exists yet (S8)
                "needs_review": {"pending": counts["review_pending"]},
                "sources": {
                    "configured": len(GalleryConfig.MONITORING_DIRECTORIES),
                    "effective": len(output_dirs),
                    "auto_detected": auto_detected,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── statistics ────────────────────────────────────────────────────

    async def get_system_statistics(self, request):
        try:
            by_category, totals, counts = await _gather_settled(
                self._run_in_executor(self.db.usage_by_category),
                self._run_in_executor(self.db.get_statistics),
                self._run_in_executor(self.db.library_counts),
            )
        except Exception:
            self.logger.exception("System statistics failed")
            return _failure("Failed to read statistics")
        return web.json_response(
            {
                "success": True,
                "by_category": by_category,
                "totals": totals,
                "rated_images": counts["rated_images"],
            },
            headers={"Cache-Control": "no-store"},
        )
