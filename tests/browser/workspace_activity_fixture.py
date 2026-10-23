"""Fake Activity jobs for the browser tests (#249 Activity-1).

A fake job is a real registry job whose runner waits for commands instead of
reading files, so a test sets its progress and counters exactly:

- ``POST /__test__/jobs/start`` starts one (``kind``, ``title``, ``total``,
  ``output_folders``; for ``kind: "image-tags"`` the image tagging job's
  groups and its ``{mode, images}`` scope instead) and returns its summary,
  or 409 while one runs;
- ``POST /__test__/jobs/{id}/step`` records ``records`` (``{group, path,
  root, root_index, detail}`` file outcomes, counted, listed and logged as the scan
  records them) and ``bulk`` (``{group, count, root}``: ``count`` numbered
  rows), sets ``processed`` (and ``total``) and ``counters``, then publishes
  the progress to every subscriber;
- ``POST /__test__/jobs/{id}/finish`` ends it as ``finished``, ``cancelled``
  or ``failed``; a cancel through the real route ends it ``cancelled``;
- ``POST /__test__/jobs/hold`` opens a legacy-page hold (``kind``, ``title``):
  a running, non-cancellable ``records`` job, as a legacy restore or
  maintenance request takes, until ``finish`` ends it;
- ``POST /__test__/jobs/restart`` acts out a ComfyUI restart: the open job
  streams drop without a final event and the registry starts empty.
"""

import asyncio
import re

from aiohttp import web

EVENTS_PATH = re.compile(r"^/prompt_manager/jobs/[0-9a-f]{32}/events$")
FINAL_STATES = {"finished", "cancelled", "failed"}
POLL_SECONDS = 0.02


PREFIXES = {"added": "+", "known": "=", "review": "?", "errors": "!"}
PREFIXES["no_metadata"] = "-"
# The image tagging job's groups (#249 PR D), logged with the runner's prefixes.
PREFIXES.update({"tagged": "+", "unchanged": "=", "skipped": "-"})
ROOT_INDEXES = {"output": 0, "input": 1}


def outcome_records(data):
    """The step's file outcomes: its ``records``, then its ``bulk`` rows."""
    records = data.get("records", [])
    bulk = data.get("bulk")
    if not isinstance(records, list):
        raise web.HTTPBadRequest()
    rows = []
    for record in records:
        if not isinstance(record, dict) or record.get("group") not in PREFIXES:
            raise web.HTTPBadRequest()
        root = str(record.get("root", "output"))
        rows.append(
            {
                "group": record["group"],
                "path": str(record.get("path", "")),
                "root": root,
                # The fixture's listing order: output is root 0, input root 1.
                "root_index": record.get("root_index", ROOT_INDEXES.get(root)),
                "detail": str(record.get("detail", "")),
            }
        )
    if bulk is not None:
        if not isinstance(bulk, dict) or bulk.get("group") not in PREFIXES:
            raise web.HTTPBadRequest()
        total = bulk.get("count")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise web.HTTPBadRequest()
        rows.extend(
            {
                "group": bulk["group"],
                "path": f"bulk/file_{index:05d}.png",
                "root": str(bulk.get("root", "output")),
                "root_index": 0,
                "detail": f"Bulk row {index}",
            }
            for index in range(total)
        )
    return rows


def register_activity_commands(app, routes, api):
    from py.api.jobs import (
        IMAGE_TAG_GROUP_NAMES,
        IMAGE_TAG_JOB_KIND,
        SCAN_GROUP_NAMES,
        JobBusy,
        JobRegistry,
    )

    streams = set()
    endings = {}
    holders = set()

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
        kind = str(data.get("kind", "scan"))
        if kind == IMAGE_TAG_JOB_KIND:
            title = "Add tags to images"
            scope = {
                "mode": str(data.get("mode", "add")),
                "images": count(data.get("images"), 0),
            }
            groups = IMAGE_TAG_GROUP_NAMES
        else:
            title = "Scan for metadata"
            scope = {
                "files": str(data.get("files", "new")),
                "output_folders": count(data.get("output_folders"), 2),
                # A real scan's count of its files in the Needs review queue.
                **(
                    {"review_items": count(data["review_items"])}
                    if "review_items" in data
                    else {}
                ),
            }
            groups = SCAN_GROUP_NAMES
        try:
            job = api._jobs.start(
                kind,
                str(data.get("title", title)),
                record_changing=True,
                scope=scope,
                runner=fake_runner(ending),
                groups=groups,
            )
        except JobBusy:
            raise web.HTTPConflict()
        job.update(total=count(data.get("total")))
        endings[job.id] = ending
        return web.json_response({"success": True, "job": job.summary()})

    @routes.post("/__test__/jobs/hold")
    async def hold_job(request):
        data = await body(request)
        loop = asyncio.get_running_loop()
        ending = loop.create_future()
        entered = loop.create_future()

        async def hold():
            async with api._jobs.hold(
                str(data.get("kind", "restore")),
                str(data.get("title", "Database restore (admin page)")),
                admission="records",
            ) as job:
                entered.set_result(job)
                state = await ending
                if state != "finished":
                    job.finish(state)

        holder = asyncio.ensure_future(hold())
        holders.add(holder)
        holder.add_done_callback(holders.discard)
        await asyncio.wait({entered, holder}, return_when=asyncio.FIRST_COMPLETED)
        if not entered.done():
            if isinstance(holder.exception(), JobBusy):
                raise web.HTTPConflict()
            raise holder.exception()
        job = entered.result()
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
        for record in outcome_records(data):
            group = record["group"]
            path = record["path"]
            detail = record["detail"]
            job.record(group, record, f"{PREFIXES[group]} {path} · {detail}")
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
        tasks += [holder for holder in holders if not holder.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return web.json_response({"success": True})
