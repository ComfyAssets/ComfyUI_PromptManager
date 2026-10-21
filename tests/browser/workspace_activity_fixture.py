"""Fake Activity jobs for the browser tests (#249 Activity-1).

A fake job is a real registry job whose runner waits for commands instead of
reading files, so a test sets its progress and counters exactly:

- ``POST /__test__/jobs/start`` starts one (``kind``, ``title``, ``total``,
  ``output_folders``) and returns its summary, or 409 while one runs;
- ``POST /__test__/jobs/{id}/step`` sets ``processed`` (and ``total``) and
  merges ``counters``, then publishes the progress to every subscriber;
- ``POST /__test__/jobs/{id}/finish`` ends it as ``finished``, ``cancelled``
  or ``failed``; a cancel through the real route ends it ``cancelled``;
- ``POST /__test__/jobs/restart`` acts out a ComfyUI restart: the open job
  streams drop without a final event and the registry starts empty.
"""

import asyncio
import re

from aiohttp import web

EVENTS_PATH = re.compile(r"^/prompt_manager/jobs/[0-9a-f]{32}/events$")
FINAL_STATES = {"finished", "cancelled", "failed"}
POLL_SECONDS = 0.02


def register_activity_commands(app, routes, api):
    from py.api.jobs import SCAN_GROUP_NAMES, JobBusy, JobRegistry

    streams = set()
    endings = {}

    @web.middleware
    async def track_streams(request, handler):
        if not EVENTS_PATH.match(request.path):
            return await handler(request)
        transport = request.transport
        streams.add(transport)
        try:
            return await handler(request)
        finally:
            streams.discard(transport)

    app.middlewares.append(track_streams)

    def fake_runner(ending):
        async def run(job):
            while not ending.done() and not job.cancelled:
                await asyncio.sleep(POLL_SECONDS)
            state = ending.result() if ending.done() else "cancelled"
            job.publish({"type": "complete", "progress": job.progress})
            job.finish(state)

        return run

    def job_or_404(request):
        job = api._jobs.get(request.match_info["job_id"])
        if job is None or job.id not in endings:
            raise web.HTTPNotFound()
        return job

    async def body(request):
        data = await request.json() if request.can_read_body else {}
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        return data

    def count(value, default=0):
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise web.HTTPBadRequest()
        return value

    @routes.post("/__test__/jobs/start")
    async def start_job(request):
        data = await body(request)
        ending = asyncio.get_running_loop().create_future()
        try:
            job = api._jobs.start(
                str(data.get("kind", "scan")),
                str(data.get("title", "Scan for metadata")),
                record_changing=True,
                scope={
                    "mode": "all",
                    "output_folders": count(data.get("output_folders"), 2),
                },
                runner=fake_runner(ending),
                groups=SCAN_GROUP_NAMES,
            )
        except JobBusy:
            raise web.HTTPConflict()
        job.update(total=count(data.get("total")))
        endings[job.id] = ending
        return web.json_response({"success": True, "job": job.summary()})

    @routes.post("/__test__/jobs/{job_id}/step")
    async def step_job(request):
        job = job_or_404(request)
        data = await body(request)
        total = count(data.get("total"), job.total)
        processed = count(data.get("processed"), job.processed)
        counters = data.get("counters", {})
        if not isinstance(counters, dict) or set(counters) - set(job.counters):
            raise web.HTTPBadRequest()
        for key, value in counters.items():
            job.counters[key] = count(value)
        progress = processed * 100 // total if total else 0
        job.update(
            progress=progress,
            processed=processed,
            total=total,
            status_line=f"Processing file {processed:,} of {total:,}",
        )
        job.publish({"type": "progress", "progress": progress})
        return web.json_response({"success": True, "job": job.summary()})

    @routes.post("/__test__/jobs/{job_id}/finish")
    async def finish_job(request):
        job = job_or_404(request)
        state = (await body(request)).get("state", "finished")
        if state not in FINAL_STATES:
            raise web.HTTPBadRequest()
        ending = endings[job.id]
        if not ending.done():
            ending.set_result(state)
        while job.running:
            await asyncio.sleep(POLL_SECONDS)
        return web.json_response({"success": True, "job": job.summary()})

    @routes.post("/__test__/jobs/restart")
    async def restart(request):
        old = api._jobs
        api._jobs = JobRegistry(api.logger)
        endings.clear()
        for transport in list(streams):
            transport.close()
        tasks = [job.task for job in old.list() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return web.json_response({"success": True})
