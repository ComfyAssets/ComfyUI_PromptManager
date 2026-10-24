"""Session job routes (Activity): list, inspect, follow and cancel jobs.

The registry itself is ``py/api/jobs.py``. The scan job's runner is in
``py/api/admin.py``, the image tagging job's in ``py/api/image_tag_jobs.py``
and the thumbnail job's in ``py/api/thumbnail_jobs.py``.
Every response leaves out absolute server paths: job rows already carry
root-relative paths and public root labels.
"""

import contextlib
import json

from aiohttp import web

from .jobs import DEFAULT_SCAN_SCOPE, SCAN_SCOPES, JobBusy


def _not_found():
    return web.json_response({"success": False, "error": "Job not found"}, status=404)


MAX_SCAN_ROOTS = 64
_SCOPE_ERROR = 'The scan scope must be "new" or "all"'
_ROOTS_ERROR = (
    f"roots must be a non-empty list of up to {MAX_SCAN_ROOTS} source indexes, "
    "with paths naming the public path of each"
)
SOURCES_CHANGED_ERROR = "The sources changed since they were loaded. Reload them."


def _bad_request(message, code=None):
    body = {"success": False, "error": message}
    if code is not None:
        body["code"] = code
    return web.json_response(body, status=400)


def _scan_roots(body):
    """``(roots, error)``: ``{index: public path}`` of the selected sources, or None.

    Absent ``roots`` means every source. When present it is a non-empty list
    of at most MAX_SCAN_ROOTS non-negative ints (never bools or floats), and
    ``paths`` the same-length list of the public path the caller read for
    each (GET /settings), so a stale index never scans another folder.
    """
    if "roots" not in body:
        return None, None
    raw = body["roots"]
    paths = body.get("paths")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SCAN_ROOTS:
        return None, _ROOTS_ERROR
    if not isinstance(paths, list) or len(paths) != len(raw):
        return None, _ROOTS_ERROR
    selected = {}
    for value, path in zip(raw, paths):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None, _ROOTS_ERROR
        if not isinstance(path, str) or selected.get(value, path) != path:
            return None, _ROOTS_ERROR
        selected[value] = path
    return dict(sorted(selected.items())), None


async def _scan_request(request):
    """``({files, roots}, None)`` for a scan start, or ``(None, message)``.

    ``files`` is the scope ("new" or "all"); ``roots`` the selected source
    indexes or None for every source. An empty body (also a chunked one with
    no bytes), or one without ``scope``, asks for the default scope.
    """
    default = {"files": DEFAULT_SCAN_SCOPE, "roots": None}
    if not request.can_read_body:
        return default, None
    text = await request.text()
    if not text.strip():  # e.g. a chunked POST that sent no bytes
        return default, None
    try:
        body = json.loads(text)
    except ValueError:
        return None, _SCOPE_ERROR
    if not isinstance(body, dict):
        return None, _SCOPE_ERROR
    files = body.get("scope", DEFAULT_SCAN_SCOPE)
    if files not in SCAN_SCOPES:
        return None, _SCOPE_ERROR
    roots, error = _scan_roots(body)
    if error:
        return None, error
    return {"files": files, "roots": roots}, None


def busy_response(busy: JobBusy):
    """``409 busy``, naming the running job that holds the slot."""
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


def _summary_event(job):
    return f"data: {json.dumps(job.summary())}\n\n".encode("utf-8")


class JobRoutesMixin:
    """Mixin providing the /prompt_manager/jobs endpoints."""

    def _roots_still_listed(self, selected, output_dirs):
        """Whether each ``{index: public path}`` still names that source."""
        return all(
            index < len(output_dirs) and self._public_path(output_dirs[index]) == path
            for index, path in selected.items()
        )

    def _register_job_routes(self, routes):
        @routes.get("/prompt_manager/jobs")
        async def list_jobs_route(request):
            return await self.list_jobs(request)

        @routes.post("/prompt_manager/jobs/scan")
        async def start_scan_job_route(request):
            return await self.start_scan_job(request)

        @routes.post("/prompt_manager/jobs/image-tags")
        async def start_image_tag_job_route(request):
            return await self.start_image_tag_job(request)

        @routes.post("/prompt_manager/jobs/thumbnails")
        async def start_thumbnail_job_route(request):
            return await self.start_thumbnail_job(request)

        @routes.get("/prompt_manager/jobs/{job_id}")
        async def job_detail_route(request):
            return await self.job_detail(request)

        @routes.get("/prompt_manager/jobs/{job_id}/report")
        async def job_report_route(request):
            return await self.job_report(request)

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

    async def job_report(self, request):
        """The job's whole record as a JSON attachment (no absolute paths)."""
        job = self._jobs.get(request.match_info["job_id"])
        if job is None:
            return _not_found()
        body = json.dumps(job.report(), indent=2)
        disposition = f'attachment; filename="{job.report_filename()}"'
        return web.Response(
            text=body,
            content_type="application/json",
            headers={
                "Content-Disposition": disposition,
                "Cache-Control": "no-store",
            },
        )

    async def cancel_job(self, request):
        """Stop a job at its next file boundary; a finished job is a no-op."""
        job_id = request.match_info["job_id"]
        if self._jobs.get(job_id) is None:
            return _not_found()
        requested = self._jobs.cancel(job_id)
        return web.json_response({"success": True, "cancellation_requested": requested})

    async def start_scan_job(self, request):
        """Start the metadata scan as a job (202), or 409 naming the busy job.

        The body's ``scope`` is "new" (the default: new and unresolved files)
        or "all". ``roots``, when given, selects sources by their index in
        the effective sources (``_get_all_output_dirs``), and ``paths`` names
        the public path read for each. An index past them, or one whose
        source now has another public path (the list changed since it was
        read), is a 409 ``sources-changed`` and starts nothing. The job walks
        the sources resolved here, so a later source change cannot move it.
        """
        scan, error = await _scan_request(request)
        if error:
            return _bad_request(error)
        output_dirs = None
        roots = None
        if scan["roots"] is not None:
            output_dirs = await self._run_in_executor(self._get_all_output_dirs)
            listed = await self._run_in_executor(
                self._roots_still_listed, scan["roots"], output_dirs
            )
            if not listed:
                return web.json_response(
                    {
                        "success": False,
                        "code": "sources-changed",
                        "error": SOURCES_CHANGED_ERROR,
                    },
                    status=409,
                )
            roots = list(scan["roots"])
        try:
            job = self._start_scan_job(
                scan["files"], roots=roots, output_dirs=output_dirs
            )
        except JobBusy as busy:
            return busy_response(busy)
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
