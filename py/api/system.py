"""System routes: the read-only health check, the Overview and usage statistics.

- ``POST /prompt_manager/system/health`` starts the health check
  (``database/health.py``) off-loop as a ``read`` job of kind ``health``: one
  at a time, beside a scan, not cancellable and with no file outcomes. The
  API keeps the last report for this session; ``GET`` returns it or null.
- ``GET /prompt_manager/system/overview`` is the System › Overview aggregate.
  Every value is real or null: there is no backup status until backups report
  one, no health result until a check ran, and no Needs-review count until
  the review queue exists.
- ``GET /prompt_manager/system/statistics`` lists runs and ratings per
  category, with the legacy ``/stats`` totals.

New routes answer errors with ``{success:false, code, error}``.
"""

import asyncio
import os

from aiohttp import web

from .job_routes import busy_response
from .jobs import HEALTH_JOB_KIND, JobBusy

HEALTH_JOB_TITLE = "Health check"
_HEALTH_STATUS_LINES = {
    "passed": "Health check passed",
    "issues": "Health check found issues",
    "incomplete": "Health check incomplete",
}


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

    async def start_health_check(self, request):
        """Start the health check job (202), or 409 naming the running check."""
        roots = self._health_roots()

        async def run(job):
            job.update(status_line="Checking the database and image links")
            report = await self._run_in_executor(self.db.run_health_checks, roots)
            self._health_report = report
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
        return web.json_response({"success": True, "report": self._health_report})

    # ── overview ──────────────────────────────────────────────────────

    def _missing_thumbnails(self, output_dirs):
        """Pending thumbnails as ``/images/thumbnails/status`` counts them."""
        if not output_dirs:
            return None
        try:
            plan = self._plan_gallery_thumbnails(output_dirs)
        except OSError:
            self.logger.warning("Overview could not plan thumbnails", exc_info=True)
            return None
        return len(plan["targets"]) + plan["remaining"]

    async def get_system_overview(self, request):
        from ..config import GalleryConfig

        try:
            output_dirs, auto_detected = self._output_dirs_with_origin()
            counts, missing = await asyncio.gather(
                self._run_in_executor(self.db.library_counts),
                self._run_in_executor(self._missing_thumbnails, output_dirs),
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
                "images": {"indexed": counts["images"]},
                "health": health_summary(self._health_report),
                "backup": None,  # no verified backup status exists yet (S8)
                "needs_review": {"pending": None},  # no review queue yet (Q6)
                "sources": {
                    "configured": len(GalleryConfig.MONITORING_DIRECTORIES),
                    "effective": len(output_dirs),
                    "auto_detected": auto_detected,
                },
                "thumbnails": {"missing": missing},
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── statistics ────────────────────────────────────────────────────

    async def get_system_statistics(self, request):
        try:
            by_category, totals = await asyncio.gather(
                self._run_in_executor(self.db.usage_by_category),
                self._run_in_executor(self.db.get_statistics),
            )
        except Exception:
            self.logger.exception("System statistics failed")
            return _failure("Failed to read statistics")
        return web.json_response(
            {"success": True, "by_category": by_category, "totals": totals},
            headers={"Cache-Control": "no-store"},
        )
