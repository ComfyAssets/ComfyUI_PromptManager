"""Session job routes (Activity): list, inspect, follow and cancel jobs.

The registry itself is ``py/api/jobs.py``; the scan job's runner is in
``py/api/admin.py``. Every response leaves out absolute server paths: job
rows already carry root-relative paths and public root labels.
"""

import contextlib
import json

from aiohttp import web

from .jobs import JobBusy


def _not_found():
    return web.json_response({"success": False, "error": "Job not found"}, status=404)


def _summary_event(job):
    return f"data: {json.dumps(job.summary())}\n\n".encode("utf-8")


class JobRoutesMixin:
    """Mixin providing the /prompt_manager/jobs endpoints."""

    def _register_job_routes(self, routes):
        @routes.get("/prompt_manager/jobs")
        async def list_jobs_route(request):
            return await self.list_jobs(request)

        @routes.post("/prompt_manager/jobs/scan")
        async def start_scan_job_route(request):
            return await self.start_scan_job(request)

        @routes.get("/prompt_manager/jobs/{job_id}")
        async def job_detail_route(request):
            return await self.job_detail(request)

        @routes.get("/prompt_manager/jobs/{job_id}/events")
        async def job_events_route(request):
            return await self.job_events(request)

        @routes.post("/prompt_manager/jobs/{job_id}/cancel")
        async def cancel_job_route(request):
            return await self.cancel_job(request)

    async def list_jobs(self, request):
        """This session's jobs: running first, then finished, newest first."""
        jobs = [job.summary() for job in self._jobs.list()]
        return web.json_response({"success": True, "jobs": jobs})

    async def job_detail(self, request):
        job = self._jobs.get(request.match_info["job_id"])
        if job is None:
            return _not_found()
        return web.json_response({"success": True, "job": job.detail()})

    async def cancel_job(self, request):
        """Stop a job at its next file boundary; a finished job is a no-op."""
        job_id = request.match_info["job_id"]
        if self._jobs.get(job_id) is None:
            return _not_found()
        requested = self._jobs.cancel(job_id)
        return web.json_response({"success": True, "cancellation_requested": requested})

    async def start_scan_job(self, request):
        """Start the metadata scan as a job (202), or 409 naming the busy job."""
        try:
            job = self._start_scan_job()
        except JobBusy as busy:
            running = busy.running
            return web.json_response(
                {
                    "success": False,
                    "code": "busy",
                    "error": str(busy),
                    "running": {"id": running.id, "title": running.title},
                },
                status=409,
            )
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    async def job_events(self, request):
        """Stream the job's summary as SSE: now, after each event, and at the end."""
        job = self._jobs.get(request.match_info["job_id"])
        if job is None:
            return _not_found()
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        try:
            await response.write(_summary_event(job))
            async with contextlib.aclosing(job.events()) as events:
                async for _payload in events:
                    await response.write(_summary_event(job))
            # The last event is published before the job takes its final state.
            await response.write(_summary_event(job))
            await response.write_eof()
        except Exception as e:  # the socket closed under us; the job goes on
            self.logger.info(f"Job progress client disconnected ({e})")
        return response
