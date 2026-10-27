"""Bulk image tagging as an Activity job: Add tags and Auto-tag (#249 D1).

``POST /prompt_manager/jobs/image-tags`` starts one ``image-tags`` job over
1..500 listed images, named by the listing identity ``{root_index,
root_scope, path}``. The job is record-changing (it writes image tag sets and
new vocabulary names), so it never runs beside the metadata scan.

- **Add tags** (``mode: "add"``) hashes each current file and unions a fixed
  tag set into the record of exactly those bytes.
- **Auto-tag** (``mode: "auto"``) copies each still image into a private
  capture, asks one legacy engine for its top N tags on the capture, re-hashes
  the current file and unions the suggestions only when the bytes are still
  the captured ones. It holds the shared model gate for the whole job, loads
  the model once and never downloads one.

Every step reuses the Tag dialog's pieces: the capture store, the model gate,
the suggestion worker and the storage call ``add_image_tags``. Files are
handled one at a time and the job stops at a file boundary when cancelled,
keeping what it already wrote. Rows and log lines carry root-relative paths
and public root labels only.

Auto-tag marks each image it ran the model on as auto-tagged (#379), with the
union of its tags. The ``autotag`` job ("Tag output images", started by
``POST /jobs/autotag``) runs the same per-file loop over every still image the
output folders list (:meth:`ImageTagJobsMixin._run_autotag_batch`): image
tags only, never a prompt tag or prompt text.
"""

import asyncio
import dataclasses
import functools
import os
import time
from dataclasses import dataclass, field
from typing import NamedTuple

from aiohttp import web

from .. import model_gate
from .admin import _plural, _time_left
from .annotation_suggest import (
    OptionError,
    unavailable_message,
    validate_options,
)
from .annotations import (
    _MESSAGES,
    MAX_TARGET_PATH,
    _coded,
    _error,
    _pinned_suggestions,
    _settled,
    _submit,
    _target,
)
from .autotag_routes import BATCH_CANCELLED, MODEL_BUSY_ERROR, model_busy_response
from .images import _current_fingerprint, _listing_posix, _public_path
from .job_routes import busy_response
from .jobs import (
    AUTOTAG_BATCH_JOB_KIND,
    AUTOTAG_GROUP_NAMES,
    IMAGE_TAG_GROUP_NAMES,
    IMAGE_TAG_JOB_KIND,
    JobBusy,
)
from .maintenance import disk_plan, short_space_message
from .prompts import bad_request

try:
    from ...database.annotations import ImageTagLimitError, normalize_image_tags
    from ...database.library_commands import LibraryCommandError
    from ...utils.media_capture import (
        CaptureCapacity,
        CaptureChanged,
        CaptureDenied,
        CaptureError,
        CaptureTooLarge,
    )
    from ...utils.video_metadata import is_video_path
except ImportError:
    from database.annotations import ImageTagLimitError, normalize_image_tags
    from database.library_commands import LibraryCommandError
    from utils.media_capture import (
        CaptureCapacity,
        CaptureChanged,
        CaptureDenied,
        CaptureError,
        CaptureTooLarge,
    )
    from utils.video_metadata import is_video_path

MAX_JOB_TARGETS = 500
# 500 maximal targets plus the options: a path of MAX_TARGET_PATH code points
# takes at most 6 bytes each on the wire (a \uXXXX escape; raw UTF-8 is at most
# 4), and the root index and scope fit in 256. The annotation routes' 64 KiB
# bound is for one target, too small for a full selection.
MAX_JOB_BODY_BYTES = MAX_JOB_TARGETS * (MAX_TARGET_PATH * 6 + 256) + 64 * 1024
# A busy capture worker (a Tag dialog in another tab) is waited for this long,
# try by try, before the file is left for a rerun.
CAPACITY_BACKOFF = (0.1, 0.2, 0.4, 0.8)

ADD_TITLE = "Add tags to images"
AUTO_TITLE = "Auto-tag images"
GATE_OWNER_KIND = "image-tag-job"

_FIELDS = {
    "add": frozenset({"mode", "targets", "tags"}),
    "auto": frozenset({"mode", "targets", "model_type", "options"}),
}
_VERBS = {"add": "Add tags", "auto": "Auto-tag"}
_LOG_PREFIXES = {"tagged": "+", "unchanged": "=", "skipped": "-", "errors": "!"}

NO_NEW_TAGS = "No new tags"
VIDEO_SKIPPED = "Video: manual tags only"
TOO_LARGE = "Too large to verify"
FILE_CHANGED = "File changed"
FILE_NOT_FOUND = "File not found"
NOT_A_STILL = "Not a supported still image"
INFERENCE_FAILED = "Auto-tagging failed. Check server logs for details."
SAVE_FAILED = "The tags could not be saved. Check server logs for details."
READ_FAILED = "The file could not be read."
SOURCE_CHANGED = "The source changed. Refresh the image collection."
STORAGE_RESTORED = "Prompt storage was restored; start the job again."
BUSY_RETRY = "The file could not be read now. Run the job again for it."
# Tag output images (#379): the per-image batch.
ALREADY_AUTOTAGGED = "Already auto-tagged"
LISTING_FAILED = "The output folders could not be listed. Try again."
LISTING_STATUS = "Listing output images…"
_INTERNAL_ERROR = "An internal error occurred. Check server logs for details."
# The pre-pass stats and looks up this many targets at a time, checking for a
# cancel between chunks.
PREPASS_CHUNK = 500

_monotonic = time.monotonic  # the job's clock (a test can fake it)


class _JobStopped(Exception):
    """No later file can be written (prompt storage was restored)."""


@dataclass
class _Run:
    """One running job: its request, the gate owner and the resolved roots.

    ``roots`` maps each target's ``(root_index, root_scope)`` to ``(root,
    label)``, read once per job off the event loop; ``root`` is None when the
    scope no longer names a current root.
    """

    job: object
    spec: object
    epoch: str
    owner: object
    service: object = None
    roots: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Spec:
    """A validated start request."""

    mode: str
    targets: tuple
    tags: tuple = ()
    model_type: str | None = None
    options: dict | None = None

    @property
    def title(self) -> str:
        return ADD_TITLE if self.mode == "add" else AUTO_TITLE

    def scope(self) -> dict:
        scope = {"mode": self.mode, "images": len(self.targets)}
        if self.mode == "auto":
            scope["model_type"] = self.model_type
        return scope


def _parse_targets(raw):
    """Unique validated targets in request order, or ``(None, error)``."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_JOB_TARGETS:
        return None, bad_request(f"targets must list 1 to {MAX_JOB_TARGETS} images")
    seen, targets = set(), []
    for item in raw:
        target, error = _target(item)
        if error is not None:
            return None, error
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return tuple(targets), None


def _parse_add(targets, data):
    try:
        tags = normalize_image_tags(data["tags"])
    except ImageTagLimitError as exc:
        return None, _error(400, "tag-limit", str(exc))
    except ValueError as exc:
        return None, bad_request(str(exc))
    if not tags:
        return None, bad_request("tags must name at least one tag")
    return _Spec("add", targets, tags=tuple(tags)), None


def _parse_auto(targets, data):
    from ..config import MAX_SUGGESTION_LIMIT, AutotagConfig

    try:
        model_type, options = validate_options(
            data["model_type"],
            data["options"],
            default_limit=AutotagConfig.suggestion_limit(),
            max_limit=MAX_SUGGESTION_LIMIT,
        )
    except OptionError as exc:
        return None, bad_request(str(exc))
    return _Spec("auto", targets, model_type=model_type, options=options), None


def parse_image_tag_request(data: dict):
    """A :class:`_Spec` for a start body, or ``(None, error response)``.

    ``{mode:"add", targets, tags}`` or ``{mode:"auto", targets, model_type,
    options}`` exactly; options follow the Tag dialog's Generate rules.
    """
    fields = _FIELDS.get(data.get("mode"))
    if fields is None:
        return None, bad_request('mode must be "add" or "auto"')
    if set(data) != fields:
        return None, bad_request(
            "Request must contain exactly " + ", ".join(sorted(fields))
        )
    targets, error = _parse_targets(data["targets"])
    if error is not None:
        return None, error
    if data["mode"] == "add":
        return _parse_add(targets, data)
    return _parse_auto(targets, data)


def _capture_outcome(exc: CaptureError, root, relative: str):
    """The (group, detail) of a file the capture store refused to read."""
    if isinstance(exc, CaptureTooLarge):
        return "skipped", TOO_LARGE
    if isinstance(exc, CaptureDenied):
        return "skipped", FILE_NOT_FOUND
    if isinstance(exc, CaptureChanged):
        path = os.path.join(root, *relative.split("/"))
        return "skipped", FILE_CHANGED if os.path.lexists(path) else FILE_NOT_FOUND
    if isinstance(exc, CaptureCapacity):
        return "skipped", BUSY_RETRY
    return "errors", READ_FAILED


async def _capacity_retried(job, attempt):
    """``await attempt()``, tried again while the capture store is at capacity.

    Raises the last ``CaptureCapacity`` once ``CAPACITY_BACKOFF`` is spent or the
    job was cancelled meanwhile.
    """
    for delay in CAPACITY_BACKOFF:
        try:
            return await attempt()
        except CaptureCapacity:
            if job.cancelled:
                raise
        await asyncio.sleep(delay)
    return await attempt()


class Refusal(NamedTuple):
    """Why a Tag output images start was refused.

    ``response`` is the native route's JSON answer; ``message`` the sentence
    a legacy stream frame carries.
    """

    status: int
    code: str
    message: str
    response: web.Response


@dataclass(frozen=True)
class AutotagRun:
    """What one Tag output images job runs with (validated at its start)."""

    model_type: str
    existing: str
    options: dict
    keep_in_memory: bool
    backup_on: bool
    owner: object


@dataclass(frozen=True)
class _BatchTarget:
    """One still image of the batch: the job target and its private keys."""

    target: tuple  # (root_index, root_scope, relative): the image-tags target
    path: str  # the listed file, for the pre-pass stat (never sent out)
    locator: str  # the image tag key: normcase(join(realpath(root), relative))
    label: str  # the root's public label


def _still_targets(api, index, root, files, seen: set) -> list:
    """Worker thread: one root's listed still images as _BatchTargets.

    Newest first (the listing's order). A locator an earlier root already
    produced (overlapping roots) is dropped; ``seen`` collects them.
    """
    scope = api._gallery_root_scope(root, index)
    label = _public_path(root)
    real = os.path.realpath(root)
    prefix = os.path.join(str(root), "")
    found = []
    for path, _mtime in files:
        relative = _listing_posix(root, prefix, path)
        if is_video_path(relative):
            continue
        locator = os.path.normcase(os.path.join(real, *relative.split("/")))
        if locator in seen:
            continue
        seen.add(locator)
        found.append(_BatchTarget((index, scope, relative), str(path), locator, label))
    return found


def _prepass_chunk(db, chunk, skip_marked: bool, max_file: int):
    """Worker thread: ``(remaining, skips)`` for a chunk of _BatchTargets.

    Stats each file (no open, no hash): gone is "File not found", over the
    capture limit "Too large to verify"; under "Skip already tagged" a file
    whose current bytes are marked is "Already auto-tagged". ``skips`` are
    ``(target, detail)`` pairs in target order.
    """
    stats, skips, alive = {}, [], []
    for item in chunk:
        fingerprint = _current_fingerprint(item.path)
        if fingerprint is None:
            skips.append((item, FILE_NOT_FOUND))
        elif fingerprint[0] > max_file:
            skips.append((item, TOO_LARGE))
        else:
            stats[item.locator] = fingerprint
            alive.append(item)
    marked = db.autotag_state(stats) if skip_marked and stats else {}
    remaining = []
    for item in alive:
        if marked.get(item.locator):
            skips.append((item, ALREADY_AUTOTAGGED))
        else:
            remaining.append(item)
    return remaining, skips


def _load_model(service, model_type: str, use_gpu: bool) -> None:
    """Worker thread: load (or swap to) *model_type*; never downloads."""
    if service.is_model_loaded() and service.get_loaded_model_type() == model_type:
        return
    if service.load_model(model_type, use_gpu) is False:
        raise RuntimeError(f"{model_type} could not be loaded")


def _refusal(status: int, code: str, message: str) -> Refusal:
    response = web.json_response(
        {"success": False, "code": code, "error": message}, status=status
    )
    return Refusal(status, code, message, response)


def _busy_refusal(busy: JobBusy) -> Refusal:
    title = getattr(busy.running, "title", "") or "Another job"
    return Refusal(409, "busy", f"{title} already running", busy_response(busy))


def _batch_cancelled(job) -> str:
    """Say the batch was cancelled; its end state (the runner ends the job)."""
    job.update(status_line=BATCH_CANCELLED)
    job.log_line(BATCH_CANCELLED)
    return "cancelled"


class ImageTagJobsMixin:
    """The ``image-tags`` job kind: start route and runner."""

    async def start_image_tag_job(self, request: web.Request) -> web.Response:
        """Start bulk tagging (202), or 409 ``busy`` / ``model-busy``.

        Every target's root scope is checked before the job starts
        (``source-changed``). Auto-tag takes the model gate here, so a start
        while anything else holds it is refused at once.
        """
        data, error = await self._read_json_body(request, max_bytes=MAX_JOB_BODY_BYTES)
        if error is not None:
            return error
        spec, error = parse_image_tag_request(data)
        if error is not None:
            return error
        roots = await self._run_in_executor(self._job_roots, spec.targets)
        if any(root is None for root, _label in roots.values()):
            return _coded("source-changed")
        epoch = await self._run_in_executor(self.db.get_execution_epoch)
        # A running job in the way answers first: it says which job to wait for.
        blocker = self._jobs.blocker(IMAGE_TAG_JOB_KIND, record_changing=True)
        if blocker is not None:
            return busy_response(JobBusy(blocker))
        owner = None
        if spec.mode == "auto":
            owner = model_gate.new_owner(GATE_OWNER_KIND)
            if not model_gate.try_acquire(owner):
                return model_busy_response()
        try:
            job = self._jobs.start(
                IMAGE_TAG_JOB_KIND,
                spec.title,
                record_changing=True,
                scope=spec.scope(),
                runner=functools.partial(self._run_image_tag_job, spec, epoch, owner),
                groups=IMAGE_TAG_GROUP_NAMES,
            )
        except JobBusy as busy:
            if owner is not None:
                model_gate.release(owner)
            return busy_response(busy)
        if owner is not None:
            # Also when the task is cancelled before its runner ever starts.
            job.task.add_done_callback(lambda _task: model_gate.release(owner))
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    # -- Tag output images (#379) ---------------------------------------------

    async def _autotag_targets(self) -> list:
        """Every still image the output folders list, as _BatchTargets.

        Root by root in configured order, each newest first (the shared
        listing, ``_get_gallery_files``, used as it is even when stale). Videos
        are not targets; a file under two overlapping roots is one target.
        """
        seen: set = set()
        found = []
        for index, root in enumerate(list(self._get_all_output_dirs())):
            files = await self._get_gallery_files(root)
            found.extend(
                await self._run_in_executor(
                    _still_targets, self, index, root, files, seen
                )
            )
        return found

    async def _start_autotag(
        self, *, model_type, existing, options, keep_in_memory, title, owner_kind
    ):
        """Register a Tag output images job: ``(job, None)`` or ``(None, Refusal)``.

        ``options`` are already validated (``validate_options``). The checks
        run in this order: 409 ``model-unavailable``; 507
        ``insufficient-space`` (only when the safety backup is on); 409
        ``busy`` (another records job); 409 ``model-busy``. No await follows
        the busy check. The gate is taken before the job registers and
        released when the task ends, also before its runner ever starts.
        """
        from ..autotag import MODELS, get_autotag_service

        backup_on = self._safety_backup_policy() == "on"
        status = await self._run_in_executor(get_autotag_service().get_models_status)
        _disk, short, _size = await self._run_in_executor(
            functools.partial(
                disk_plan, self.db.model.db_path, backup=backup_on, vacuum=False
            )
        )
        label = MODELS[model_type]["label"]
        if not (status.get(model_type) or {}).get("downloaded"):
            message = (
                f"{label} is not downloaded. Download it in System › Auto-tagging."
            )
            return None, _refusal(409, "model-unavailable", message)
        if backup_on and short is not None:
            return None, _refusal(507, "insufficient-space", short_space_message(short))
        busy = self._jobs.blocker(AUTOTAG_BATCH_JOB_KIND, admission="records")
        if busy is not None:
            return None, _busy_refusal(JobBusy(busy))
        owner = model_gate.new_owner(owner_kind)
        if not model_gate.try_acquire(owner):
            response = model_busy_response()
            return None, Refusal(409, "model-busy", MODEL_BUSY_ERROR, response)
        run = AutotagRun(
            model_type, existing, options, keep_in_memory, backup_on, owner
        )
        try:
            job = self._jobs.start(
                AUTOTAG_BATCH_JOB_KIND,
                title,
                admission="records",
                cancellable=True,
                scope={"model_type": model_type, "existing": existing, "label": label},
                groups=AUTOTAG_GROUP_NAMES,
                runner=functools.partial(self._run_autotag_batch, run),
            )
        except BaseException as refused:
            model_gate.release(owner)
            if isinstance(refused, JobBusy):
                return None, _busy_refusal(refused)
            raise
        job.task.add_done_callback(lambda _task: model_gate.release(owner))
        return job, None

    async def _run_autotag_batch(self, run: AutotagRun, job) -> None:
        """The ``autotag`` job: backup, model, listing, pre-pass, per-image loop.

        ``job.cancelled`` is checked between phases (and per pre-pass chunk
        and per image). Each phase publishes ``{"type": "phase", "phase"}``.
        With ``keep_in_memory`` false the model is unloaded under the gate.
        The job ends only after that cleanup and the gate release, so a job
        that reads as ended never still holds the gate.
        """
        state = "failed"
        try:
            state = await self._autotag_batch_phases(run, job)
        except Exception:
            self.logger.exception("Tag output images failed")
            job.update(status_line=_INTERNAL_ERROR)
            job.log_line(f"Tagging failed · {_INTERNAL_ERROR}")
        finally:
            try:
                if not run.keep_in_memory:
                    await self._unload_batch_model(run.owner)
                if run.backup_on:
                    await self._prune_backup_pool(job, self.db)
            finally:
                model_gate.release(run.owner)
                job.publish({"type": state, "progress": job.progress})
                job.finish(state)

    async def _unload_batch_model(self, owner) -> None:
        from ..autotag import get_autotag_service

        service = get_autotag_service()
        try:
            if service.is_model_loaded():
                await model_gate.run(owner, service.unload_model)
        except Exception:
            self.logger.exception("The tagging model could not be unloaded")

    async def _autotag_batch_phases(self, run: AutotagRun, job) -> str:
        """Run the phases; the job's end state (the caller ends the job)."""
        job.publish({"type": "phase", "phase": "backup"})
        ok, _backup = await self._safety_backup_step(job, self.db, run.backup_on)
        if not ok:
            return "failed"
        if job.cancelled:
            return _batch_cancelled(job)
        job.publish({"type": "phase", "phase": "model"})
        job.update(status_line="Loading the tagging model")
        spec = _Spec("auto", (), model_type=run.model_type, options=run.options)
        service = await self._prepare_tagging_model(job, spec, run.owner)
        if service is None:
            return "failed"
        if job.cancelled:
            return _batch_cancelled(job)
        job.publish({"type": "phase", "phase": "listing"})
        job.update(status_line=LISTING_STATUS)
        epoch = await self._run_in_executor(self.db.get_execution_epoch)
        try:
            targets = await self._autotag_targets()
        except Exception:
            self.logger.exception("Tag output images could not list the outputs")
            job.update(status_line=LISTING_FAILED)
            job.log_line(f"! {LISTING_FAILED}")
            return "failed"
        total = len(targets)
        job.scope["images"] = total
        job.update(total=total)
        policy = (
            "skip images already auto-tagged" if run.existing == "skip" else "merge"
        )
        job.log_line(
            f"Tagging started · {job.scope.get('label') or run.model_type} · "
            f"{_plural(total, 'image')} · {policy}"
        )
        if job.cancelled:
            return _batch_cancelled(job)
        remaining = await self._autotag_prepass(job, targets, run.existing == "skip")
        if remaining is None:
            return _batch_cancelled(job)
        done = total - len(remaining)
        spec = dataclasses.replace(
            spec, targets=tuple(item.target for item in remaining)
        )
        loop_run = _Run(job, spec, epoch, run.owner, service)
        if not await self._tag_each_file(loop_run, done_before=done):
            job.update(status_line=STORAGE_RESTORED)
            job.log_line(f"Tagging stopped · {STORAGE_RESTORED}")
            return "failed"
        if job.cancelled:
            return _batch_cancelled(job)
        finished = f"Tagged {_plural(job.counters.get('tagged', 0), 'image')}"
        job.update(status_line=finished, progress=100)
        job.log_line(finished)
        return "finished"

    async def _autotag_prepass(self, job, targets, skip_marked: bool):
        """The targets left for the model, or None when cancelled meanwhile.

        Opens and hashes nothing: one stat per file and one indexed read per
        chunk. Skips are counted with ``record_counted`` (exact counters, rows
        up to the cap, no log line each).
        """
        max_file = self._capture_store().max_file
        remaining, skipped = [], 0
        total = len(targets)
        for start in range(0, total, PREPASS_CHUNK):
            if job.cancelled:
                return None
            chunk = targets[start : start + PREPASS_CHUNK]
            left, skips = await self._run_in_executor(
                _prepass_chunk, self.db, chunk, skip_marked, max_file
            )
            remaining.extend(left)
            if skips:
                job.record_counted(
                    "skipped",
                    len(skips),
                    [
                        {
                            "path": item.target[2],
                            "root": item.label,
                            "root_index": item.target[0],
                            "detail": detail,
                        }
                        for item, detail in skips
                    ],
                )
                skipped += len(skips)
                job.update(processed=skipped, progress=skipped * 100 // total)
                job.publish({"type": "progress", "progress": job.progress})
        if skipped:
            job.log_line(f"Skipped {_plural(skipped, 'image')} before tagging")
        return remaining

    # -- runner ----------------------------------------------------------------

    def _job_roots(self, targets) -> dict:
        """Worker thread: ``{(index, scope): (root or None, label)}`` (see _Run)."""
        roots = list(self._get_all_output_dirs())
        resolved = {}
        for index, scope in dict.fromkeys((t[0], t[1]) for t in targets):
            label = _public_path(roots[index]) if index < len(roots) else ""
            error = self._gallery_scope_error(
                {"root_scope": scope, "root": str(index)}, roots
            )
            resolved[(index, scope)] = (roots[index] if error is None else None, label)
        return resolved

    async def _run_image_tag_job(self, spec: _Spec, epoch: str, owner, job) -> None:
        verb = _VERBS[spec.mode]
        total = len(spec.targets)
        job.update(total=total, status_line=f"Processing file 0 of {total:,}")
        started = f"{verb} started · {_plural(total, 'image')}"
        if spec.mode == "auto":
            started += f" · {spec.model_type}"
        job.log_line(started)
        run = _Run(job, spec, epoch, owner)
        try:
            if spec.mode == "auto":
                run.service = await self._prepare_tagging_model(job, spec, owner)
                if run.service is None:
                    job.finish("failed")
                    return
            if not await self._tag_each_file(run):
                job.log_line(f"{verb} stopped · {STORAGE_RESTORED}")
                job.finish("failed")
                return
            state = "cancelled" if job.cancelled else "finished"
            job.log_line(f"{verb} {state} · {_plural(job.processed, 'file')} processed")
            job.publish({"type": state, "progress": job.progress})
            job.finish(state)
        finally:
            if owner is not None:
                model_gate.release(owner)

    async def _tag_each_file(self, run: _Run, *, done_before: int = 0) -> bool:
        """Tag the targets in order; False when the job had to stop.

        ``done_before`` files were already counted (the batch's pre-pass): file
        n of this loop is "file done_before + n" of the job, and the time left
        uses this loop's rate only.
        """
        job, count = run.job, len(run.spec.targets)
        total = done_before + count
        run.roots = await self._run_in_executor(self._job_roots, run.spec.targets)
        started = _monotonic()
        for number, target in enumerate(run.spec.targets, 1):
            if job.cancelled:
                break
            # The scan's estimate (D56), with each finished file as one batch.
            left = _time_left(number - 1, count, number - 1, _monotonic() - started)
            shown = done_before + number
            job.update(status_line=f"Processing file {shown:,} of {total:,}{left}")
            job.publish({"type": "progress", "progress": job.progress})
            stopped = False
            try:
                group, detail = await self._tag_one_file(run, target)
            except _JobStopped:
                stopped, group, detail = True, "errors", STORAGE_RESTORED
            except Exception:
                self.logger.exception("Image tagging failed for one file")
                group, detail = "errors", SAVE_FAILED
            self._record_tag_outcome(run, target, group, detail)
            job.update(processed=shown, progress=shown * 100 // total)
            if stopped:
                return False
        return True

    async def _prepare_tagging_model(self, job, spec: _Spec, owner):
        """The loaded service, or None (logged) when the job cannot run."""
        from ..autotag import MODELS, get_autotag_service

        service = get_autotag_service()
        status = await self._run_in_executor(service.get_models_status)
        if not status.get(spec.model_type, {}).get("downloaded"):
            name = MODELS.get(spec.model_type, {}).get("name", spec.model_type)
            message = unavailable_message(name)
            job.update(status_line=message)
            job.log_line(f"! {message}")
            return None
        try:
            await model_gate.run(
                owner,
                _load_model,
                service,
                spec.model_type,
                spec.options["use_gpu"],
            )
        except Exception:
            self.logger.exception("The auto-tag job could not load its model")
            job.update(status_line=INFERENCE_FAILED)
            job.log_line(f"! {INFERENCE_FAILED}")
            return None
        return service

    async def _verified(self, run: _Run, store, root, relative):
        """The current file's facts, waiting out a busy capture store."""
        return await _capacity_retried(
            run.job, lambda: self._run_in_executor(store.verify, root, relative)
        )

    async def _tag_one_file(self, run: _Run, target):
        """The (group, detail) of one target; raises _JobStopped on a restore."""
        index, scope, relative = target
        root, _label = run.roots[(index, scope)]
        if root is None:
            return "errors", SOURCE_CHANGED
        store = self._capture_store()
        if run.spec.mode == "add":
            try:
                facts = await self._verified(run, store, root, relative)
            except CaptureError as exc:
                return _capture_outcome(exc, root, relative)
            return await self._union_image_tags(facts, list(run.spec.tags), run.epoch)
        if is_video_path(relative):
            return "skipped", VIDEO_SKIPPED
        try:
            capture = await _capacity_retried(
                run.job,
                lambda: _settled(
                    _submit(
                        store.capture,
                        root,
                        relative,
                        epoch=run.epoch,
                        scope=scope,
                        root_index=index,
                    ),
                    lambda made: made is not None and store.release(made.token),
                ),
            )
        except CaptureError as exc:
            return _capture_outcome(exc, root, relative)
        try:
            return await self._auto_tag_capture(run, store, capture, root, relative)
        finally:
            store.release(capture.token)

    async def _auto_tag_capture(self, run: _Run, store, capture, root, relative):
        """Suggest on the capture, then union only onto the same current bytes.

        The bytes are marked auto-tagged with the union, also when the model
        returned no names; captured bytes no model can read are marked too
        (and stay skipped), so a "Skip already tagged" run passes them by.
        """
        if not capture.inferable:
            group, detail = await self._union_image_tags(
                capture, [], run.epoch, mark_autotagged=True
            )
            return (group, detail) if group == "errors" else ("skipped", NOT_A_STILL)
        if store.acquire(capture.token) is None:
            raise _JobStopped  # every capture was invalidated (restore)
        spec = run.spec
        try:
            pairs = await model_gate.run(
                run.owner,
                _pinned_suggestions,
                store,
                capture.token,
                run.service,
                capture,
                spec.model_type,
                spec.options,
                on_skipped=functools.partial(store.release_use, capture.token),
            )
        except Exception:
            self.logger.exception("Auto-tagging failed for one file")
            return "errors", INFERENCE_FAILED
        try:
            facts = await self._verified(run, store, root, relative)
        except CaptureError as exc:
            return _capture_outcome(exc, root, relative)
        if facts.locator != capture.locator or facts.sha256 != capture.sha256:
            return "skipped", FILE_CHANGED
        names = [str(name) for name, _score in pairs]
        return await self._union_image_tags(
            facts, names, run.epoch, mark_autotagged=True
        )

    async def _union_image_tags(
        self, facts, tags: list, epoch: str, *, mark_autotagged: bool = False
    ):
        """``add_image_tags`` on the verified bytes, as a (group, detail).

        ``mark_autotagged`` (a model's result) marks the bytes auto-tagged in
        the same write; then ``tags`` may be empty.
        """
        try:
            receipt = await self._run_in_executor(
                functools.partial(
                    self.db.add_image_tags,
                    locator=facts.locator,
                    sha256=facts.sha256,
                    size=facts.size,
                    mtime_ns=facts.mtime_ns,
                    expected_epoch=epoch,
                    tags=tags,
                    mark_autotagged=mark_autotagged,
                )
            )
        except LibraryCommandError as exc:
            if exc.code == "storage-changed":
                raise _JobStopped from None
            if exc.code == "tag-limit":
                return "errors", _MESSAGES["tag-limit"]
            self.logger.warning(f"Image tags not saved ({exc.code})")
            return "errors", SAVE_FAILED
        except ValueError:
            self.logger.exception("Image tags were refused")
            return "errors", SAVE_FAILED
        added = receipt.get("added", 0)
        if added:
            return "tagged", f"Added {_plural(added, 'tag')}"
        return "unchanged", NO_NEW_TAGS

    def _record_tag_outcome(self, run: _Run, target, group: str, detail: str) -> None:
        index, scope, relative = target
        _root, label = run.roots[(index, scope)]
        where = f"{label}/{relative}" if label else relative
        run.job.record(
            group,
            {"path": relative, "root": label, "root_index": index, "detail": detail},
            f"{_LOG_PREFIXES[group]} {where} · {detail}",
        )
