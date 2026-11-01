"""Move thumbnails, the review (#320 Part 3 phase 1): what a move would copy,
remove and leave, checked before anything is written."""

import hashlib
import json
import os
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from . import preview_location, preview_store
from . import thumbnail_move_files as files
from .job_routes import busy_response
from .jobs import THUMBNAILS_JOB_KIND, JobBusy
from .thumbnail_location import default_path

try:
    from ...utils import library_location
except ImportError:  # tests import the package top-level
    from utils import library_location

MOVE_TITLE = "Move thumbnails"
FINISHING_TITLE = "Move thumbnails (finishing)"
PRUNE_TITLE = "Remove thumbnails of removed sources"
MOVE_GROUPS = ("moved", "skipped", "left", "errors")
PRUNE_GROUPS = ("removed", "left", "errors")
PLAN_TTL = 600.0
JOURNAL_NAME = "thumbnail-move.json"
SPACE_MARGIN = 64 * 2**20
SPACE_SHARE = 0.05
LISTED = 5
SENTENCE = (
    "Every thumbnail is copied and checked before Prompt Manager switches. "
    "Scans, deletes and tagging wait until it finishes; closing this page does "
    "not stop it."
)


def _number(value):
    return f"{value:,}"


def _plural(count, one, many=None):
    return f"{_number(count)} {one if count == 1 else (many or one + 's')}"


def _size(value):
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _refusal(code, status=None, **extra):
    known_status, text = preview_location.MESSAGES.get(code, (400, code))
    return web.json_response(
        {"success": False, "code": code, "error": text, **extra},
        status=status or known_status,
    )


def journal_path():
    """``<data folder>/data/thumbnail-move.json`` (only while a move copies)."""
    return library_location.cache_dir(JOURNAL_NAME)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _is_network(path):
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    if os.name == "nt":
        try:
            import ctypes

            drive = os.path.splitdrive(os.path.abspath(text))[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4  # DRIVE_REMOTE
        except (AttributeError, OSError):
            return False
    return False


def _volume(path):
    current = Path(os.path.abspath(path))
    while not current.exists() and current.parent != current:
        current = current.parent
    try:
        return os.stat(current).st_dev
    except OSError:
        return None


def _free(path):
    current = Path(os.path.abspath(path))
    while not current.exists() and current.parent != current:
        current = current.parent
    try:
        return shutil.disk_usage(current).free
    except OSError:
        return None


# ── the plan ─────────────────────────────────────────────────────────


@dataclass
class SourcePlan:
    """One listed source: its old store, its new store and what moves."""

    root: str
    label: str
    key: str | None
    old_store: str | None
    new_store: str
    scan: files.StoreScan | None
    available: bool = True
    too_long: list = field(default_factory=list)


@dataclass
class MovePlan:
    """A reviewed move: from, to, the sources and the removals."""

    token: str
    created: float
    revision: str
    old: preview_location.ParsedLocation
    target: dict  # {"mode", "path"}
    target_id: str | None
    target_exists: bool
    target_default: bool
    install: str
    sources: list
    unlisted: list  # (key, store path, scan)
    needs_bytes: int
    confirm_required: bool

    @property
    def to_folder(self):
        return self.target["mode"] == preview_store.FOLDER_MODE

    @property
    def from_folder(self):
        return self.old.mode == preview_store.FOLDER_MODE


def _roots_revision(roots):
    text = json.dumps(sorted(str(root) for root in roots))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ThumbnailMovePlanMixin:
    """The move's review and its routes."""

    _move_plans = None

    # ── routes ────────────────────────────────────────────────────────

    def _register_thumbnail_move_routes(self, routes):
        @routes.post("/prompt_manager/images/thumbnails/plan")
        async def thumbnail_move_plan_route(request):
            return await self.plan_thumbnail_move(request)

        @routes.post("/prompt_manager/images/thumbnails/location/recreate")
        async def thumbnail_location_recreate_route(request):
            return await self.recreate_thumbnail_folder(request)

        @routes.post("/prompt_manager/images/thumbnails/location/adopt")
        async def thumbnail_location_adopt_route(request):
            return await self.adopt_thumbnail_folder(request)

    # ── review ────────────────────────────────────────────────────────

    async def plan_thumbnail_move(self, request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or body.get("mode") != "move":
            return web.json_response(
                {
                    "success": False,
                    "code": "invalid-request",
                    "error": "mode must be move",
                },
                status=400,
            )
        target = body.get("location")
        if not isinstance(target, dict) or target.get("mode") not in (
            preview_store.SOURCE_MODE,
            preview_store.FOLDER_MODE,
        ):
            return web.json_response(
                {
                    "success": False,
                    "code": "invalid-request",
                    "error": "location must name a mode",
                },
                status=400,
            )
        blocker = self._jobs.blocker(THUMBNAILS_JOB_KIND, admission="guarded")
        if blocker is not None:
            return busy_response(JobBusy(blocker))
        try:
            outcome = await self._run_in_executor(self._build_move_plan, target)
        except Exception:
            self.logger.exception("The thumbnail move could not be planned")
            return web.json_response(
                {
                    "success": False,
                    "code": None,
                    "error": "The move could not be planned",
                },
                status=500,
            )
        if isinstance(outcome, str):
            return _refusal(outcome)
        plan, payload = outcome
        if payload.get("insufficient"):
            return _refusal(
                "insufficient-space",
                status=507,
                error_detail=payload["insufficient"],
                plan=payload,
            )
        self._store_move_plan(plan)
        return web.json_response({"success": True, **payload})

    def _store_move_plan(self, plan):
        if self._move_plans is None:
            self._move_plans = {}
        now = time.monotonic()
        self._move_plans = {
            token: kept
            for token, kept in self._move_plans.items()
            if now - kept.created < PLAN_TTL
        }
        self._move_plans[plan.token] = plan

    def _build_move_plan(self, target):
        """``(plan, payload)`` or a refusal code (blocking)."""
        old = preview_store.setting()
        location = preview_store.current()
        install = old.install or str(uuid.uuid4())
        target_path = None
        default = default_path()
        if target["mode"] == preview_store.FOLDER_MODE:
            raw = target.get("path")
            if not isinstance(raw, str):
                return "location-not-absolute"
            context = self._thumbnail_location_context()
            code = preview_location.location_problem(
                raw, purpose="move", context=context
            )
            if code is not None:
                return code
            target_path = os.path.abspath(raw)
        elif old.mode == preview_store.SOURCE_MODE and not old.problem:
            return "location-same"
        target_default = bool(default) and os.path.normcase(
            target_path or ""
        ) == os.path.normcase(os.path.abspath(default))
        target_exists = bool(target_path) and os.path.isdir(target_path)
        target_marker = (
            preview_location.read_marker(target_path) if target_exists else None
        )
        roots = [str(root) for root in self._get_all_output_dirs()]
        labels = self._source_labels(roots)
        old_keys = {}
        if old.mode == preview_store.FOLDER_MODE and location.available:
            marker = preview_location.read_marker(old.path) or {}
            old_keys = marker.get("keys") or {}
        new_keys = dict((target_marker or {}).get("keys") or {})
        taken = {key: entry.get("identity") for key, entry in new_keys.items()}
        sources = []
        for root in roots:
            identity = os.path.normcase(os.path.realpath(root))
            dev_ino = preview_location.file_identity(root)
            old_key = None
            old_store = None
            available = True
            if old.mode == preview_store.SOURCE_MODE:
                old_store = str(preview_store.source_folder(root))
            elif location.available:
                old_key = preview_location.find_key(old_keys, root, identity, dev_ino)
                if old_key:
                    old_store = os.path.join(old.path, old_key)
            else:
                available = False
            key = None
            if target_path:
                key = old_key or preview_location.find_key(
                    new_keys, root, identity, dev_ino
                )
                if key is None or taken.get(key, identity) != identity:
                    key = preview_location.source_key(root, identity, taken)
                taken[key] = identity
                new_store = os.path.join(target_path, key)
            else:
                new_store = str(preview_store.source_folder(root))
            scan = None
            if old_store:
                scan = files.scan_store(old_store, root)
            plan = SourcePlan(
                root, labels.get(root, root), key, old_store, new_store, scan, available
            )
            if scan is not None and target_path:
                plan.too_long = [
                    item[0]
                    for item in scan.copy
                    if files.too_long(Path(new_store).joinpath(*item[0]))
                ]
            sources.append(plan)
        unlisted = self._unlisted_keys(old, location, old_keys, install)
        needs = sum(source.scan.bytes for source in sources if source.scan is not None)
        plan = MovePlan(
            token=secrets.token_urlsafe(16),
            created=time.monotonic(),
            revision=_roots_revision(roots),
            old=old,
            target={"mode": target["mode"], "path": target_path},
            target_id=(target_marker or {}).get("id"),
            target_exists=target_exists,
            target_default=target_default,
            install=install,
            sources=sources,
            unlisted=unlisted,
            needs_bytes=needs,
            confirm_required=bool(target_path) and not target_default,
        )
        return plan, self._plan_payload(plan, location)

    def _unlisted_keys(self, old, location, keys, install):
        """This install's keys whose source no longer is in any root list
        (``(key, store, scan)``), in the folder in use."""
        if old.mode != preview_store.FOLDER_MODE or not location.available:
            return []
        listed = {
            os.path.normcase(os.path.realpath(root))
            for root in self._all_source_roots()
        }
        found = []
        for key, entry in sorted(keys.items()):
            if entry.get("install") not in (install, None):
                continue
            if entry.get("identity") in listed:
                continue
            store = os.path.join(old.path, key)
            found.append(
                (key, store, files.scan_store(store, entry.get("path") or store))
            )
        return found

    def _source_labels(self, roots):
        from . import sources

        labels = sources._comfyui_labels()
        return {root: sources.source_label(root, labels) for root in roots}

    def _plan_payload(self, plan, location):
        sources = plan.sources
        copies = sum(len(s.scan.copy) for s in sources if s.scan)
        orphans = sum(len(s.scan.orphans) for s in sources if s.scan)
        temps = sum(len(s.scan.temps) for s in sources if s.scan)
        links = sum(s.scan.links for s in sources if s.scan)
        left = [
            f"{s.label} · {'/'.join(item)}"
            for s in sources
            if s.scan
            for item in s.scan.left
        ]
        too_long = [
            f"{s.label} · {'/'.join(item)}" for s in sources for item in s.too_long
        ]
        unlisted_count = sum(
            len(scan.copy) + len(scan.orphans) for _k, _s, scan in plan.unlisted
        )
        free = None
        insufficient = None
        if plan.needs_bytes:
            volumes = {}
            for source in sources:
                if source.scan is None or not source.scan.bytes:
                    continue
                volume = _volume(source.new_store)
                entry = volumes.setdefault(volume, [source.new_store, 0])
                entry[1] += source.scan.bytes
            for where, needed in volumes.values():
                available = _free(where)
                if available is None:
                    continue
                free = available if free is None else min(free, available)
                margin = max(SPACE_MARGIN, int(needed * SPACE_SHARE))
                if needed + margin > available:
                    insufficient = {
                        "needed": needed + margin,
                        "free": available,
                        "text": (
                            f"There is not enough space for the thumbnails: "
                            f"{_size(available)} free, {_size(needed + margin)} "
                            "needed. Free some space or choose another folder."
                        ),
                    }
        payload = {
            "mode": "move",
            "from": {
                "mode": plan.old.mode,
                "path": plan.old.path,
                "available": location.available,
            },
            "to": {
                "mode": plan.target["mode"],
                "path": plan.target["path"],
                "exists": plan.target_exists,
                "will_create": bool(plan.target["path"]) and not plan.target_exists,
                "default": plan.target_default,
            },
            "sources": [
                {
                    "label": s.label,
                    "key": s.key,
                    "files": len(s.scan.copy) if s.scan else 0,
                    "bytes": s.scan.bytes if s.scan else 0,
                    "too_long": len(s.too_long),
                    "links": s.scan.links if s.scan else 0,
                    "orphans": len(s.scan.orphans) if s.scan else 0,
                    "left_in_place": len(s.scan.left) if s.scan else 0,
                    "available": s.available,
                }
                for s in sources
            ],
            "totals": {
                "sources": len(sources),
                "files": copies,
                "bytes": plan.needs_bytes,
                "orphans": orphans,
                "temps": temps,
                "links": links,
                "left_in_place": len(left),
                "too_long": len(too_long),
                "unlisted_sources": len(plan.unlisted),
                "unlisted_files": unlisted_count,
            },
            "too_long_listed": too_long[:LISTED],
            "left_listed": left[:LISTED],
            "unlisted": [
                {"key": key, "files": len(scan.copy) + len(scan.orphans)}
                for key, _store, scan in plan.unlisted
            ],
            "needs_bytes": plan.needs_bytes,
            "free_bytes": free,
            "confirm_required": plan.confirm_required,
            "token": plan.token,
            "sentence": SENTENCE,
        }
        if insufficient:
            payload["insufficient"] = insufficient
        return payload
