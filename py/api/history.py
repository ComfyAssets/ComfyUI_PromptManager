"""Authenticated chronological windows and bounded frozen run details."""

import asyncio
import base64
import binascii
from functools import wraps
import json
import os
import re
import sqlite3
from typing import Callable
from urllib.parse import urlencode

from aiohttp import web

from .library import _decode, _encode, _scope
from .history_current import (
    CurrentLocationUnavailable,
    CurrentOutputScope,
    VIDEO_EXTENSIONS,
    _OpenedCurrentMedia,
    _current_media_response,
    _prepare_current_media,
    accepts_jxl,
    _reauthorize_current_target,
    candidate_locator,
    resolve_current_candidate,
    snapshot_current_output_scope,
)
from .inspection_service import CurrentFileChanged, inspect_current_file
from .images import image_category_key

try:
    from ...database.execution_history import HistoryEpochChanged
    from ...database.history_gallery import read_history_gallery
    from ...database.run_attribution import recorded_output_lookup
    from ...database.history_reads import (
        HistoryCaptureChanged,
        OutputPosition,
        RunKey,
        WindowPosition,
    )
    from ...utils.history_read_fence import HistoryReadFence
    from ...utils.inspection_response import (
        OWN_CATEGORY_UNREAD,
        apply_own_category,
        encode_history_inspection_response,
    )
except ImportError:
    from database.execution_history import HistoryEpochChanged
    from database.history_gallery import read_history_gallery
    from database.run_attribution import recorded_output_lookup
    from database.history_reads import (
        HistoryCaptureChanged,
        OutputPosition,
        RunKey,
        WindowPosition,
    )
    from utils.history_read_fence import HistoryReadFence
    from utils.inspection_response import (
        OWN_CATEGORY_UNREAD,
        apply_own_category,
        encode_history_inspection_response,
    )


_RUN_MAX_BYTES = 8 * 1024 * 1024
_OUTPUTS_MAX_BYTES = 1024 * 1024
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,18}")
_RUN_DECIMALS = (
    "queue_item_id",
    "observed_dequeue_at",
    "observed_callback_at",
    "executor_started_at",
    "terminal_event_at",
)
_MESSAGES = {
    "invalid-request": "Invalid history request",
    "invalid-cursor": "Invalid history cursor; refresh recorded history",
    "not-found": "Recorded history target not found",
    "history-changed": "Recorded history changed; reload recorded history",
    "storage-unavailable": "Recorded history is temporarily unavailable",
    "response-limit": "Recorded history response exceeds the response limit",
    "capture-changed": "Reported output capture changed; reload reported outputs",
    "candidate-stale": "Current output locations changed; reload reported outputs",
    "not-authorized": "Current output media is unavailable or not authorized",
}


class HistoryReadError(Exception):
    """A bounded public failure without private storage or capture details."""

    def __init__(self, status: int, code: str, recovery: dict | None = None) -> None:
        self.status = status
        self.code = code
        self.recovery = recovery
        super().__init__(_MESSAGES[code])


def _error_response(error: HistoryReadError) -> web.Response:
    return web.json_response(
        {
            "success": False,
            "history_version": 1,
            "code": error.code,
            "error": str(error),
            "recovery": error.recovery,
        },
        status=error.status,
        headers={"Cache-Control": "no-store"},
    )


def _history_errors(handler: Callable) -> Callable:
    @wraps(handler)
    async def wrapped(self, request: web.Request) -> web.Response:
        try:
            return await handler(self, request)
        except HistoryReadError as error:
            return _error_response(error)
        except HistoryEpochChanged:
            return _error_response(
                HistoryReadError(409, "history-changed", {"action": "refresh-history"})
            )
        except ValueError:
            return _error_response(HistoryReadError(400, "invalid-request"))
        except (sqlite3.Error, OSError) as error:
            self.logger.warning("History read unavailable (%s)", type(error).__name__)
            return _error_response(
                HistoryReadError(503, "storage-unavailable", {"action": "retry-read"})
            )

    return wrapped


def _decimal(value: object, *, minimum: int = 0) -> int:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise ValueError("Invalid decimal history integer")
    parsed = int(value)
    if not minimum <= parsed <= 2**63 - 1:
        raise ValueError("History integer outside supported range")
    return parsed


def _uuid(value: object) -> str:
    if type(value) is not str or _UUID.fullmatch(value) is None:
        raise ValueError("Invalid history UUID")
    return value


def _query(request: web.Request) -> None:
    if any(len(request.query.getall(key)) != 1 for key in request.query):
        raise ValueError("Duplicate history query parameter")


def _jxl_flag(request: web.Request) -> bool:
    """The cover's ``jxl`` flag (#317): ``1`` when the workspace decoded a
    JPEG XL image (a browser may not list ``image/jxl`` in Accept), ``0`` or
    absent otherwise; anything else is a bad request."""
    value = request.query.get("jxl")
    if value is None:
        return False
    if value not in ("0", "1"):
        raise ValueError("Invalid jxl flag")
    return value == "1"


def _page_limit(request: web.Request) -> int:
    limit = _decimal(request.query.get("limit", "40"), minimum=1)
    if limit > 100:
        raise ValueError("History limit outside supported range")
    return limit


def _decode_run_key(value: object) -> RunKey:
    if type(value) is not list or len(value) != 4:
        raise ValueError("Invalid history run key")
    return (_decimal(value[0]), _uuid(value[1]), _decimal(value[2]), _uuid(value[3]))


def _wire_run_key(key: RunKey) -> list[str]:
    return [str(key[0]), key[1], str(key[2]), key[3]]


def _cursor_payload(token: str, kind: str) -> object:
    """Use Library authentication, allowing only inline, domain-bound keys."""
    try:
        if not 1 <= len(token.encode("ascii")) <= 4096:
            raise ValueError
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        wrapper = json.loads(raw[32:])
        if type(wrapper) is not dict or set(wrapper) != {"scope", "key"}:
            raise ValueError
        return _decode(token, _scope({"kind": kind}))
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        raise HistoryReadError(400, "invalid-cursor") from None


def _decode_window_cursor(
    token: str | None, positive_id: int, *, resume: bool = False
) -> tuple[str | None, WindowPosition | None]:
    if token is None:
        return None, None
    try:
        value = _cursor_payload(token, "history-resume" if resume else "history-window")
        if type(value) is not dict or set(value) != {
            "version",
            "epoch",
            "positive_id",
            "raw_text_id",
            "upper",
        } | (set() if resume else {"direction", "before"}):
            raise ValueError
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or _decimal(value["positive_id"], minimum=1) != positive_id
            or (not resume and value["direction"] != "older")
            or type(value["raw_text_id"]) is not str
            or _DIGEST.fullmatch(value["raw_text_id"]) is None
        ):
            raise ValueError
        epoch = _uuid(value["epoch"])
        upper = _decode_run_key(value["upper"])
        before = upper if resume else _decode_run_key(value["before"])
        if before > upper:
            raise ValueError
        return epoch, WindowPosition(value["raw_text_id"], upper, before)
    except ValueError:
        raise HistoryReadError(400, "invalid-cursor") from None


def _encode_window_cursor(
    position: WindowPosition | None,
    epoch: str,
    positive_id: int,
    *,
    resume: bool = False,
) -> str | None:
    if position is None:
        return None
    value = {
        "version": 1,
        "epoch": epoch,
        "positive_id": str(positive_id),
        "raw_text_id": position.raw_text_id,
        "upper": _wire_run_key(position.upper),
    }
    if not resume:
        value.update(direction="older", before=_wire_run_key(position.before))
    return _encode(
        value,
        _scope({"kind": "history-resume" if resume else "history-window"}),
    )


def _node(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or "\0" in value:
        raise ValueError("Invalid output node")
    if len(value.encode("utf-8")) > 512:
        raise ValueError("Invalid output node size")
    return value


def _decode_output_cursor(
    token: str | None, run_id: str, epoch: str
) -> OutputPosition | None:
    if token is None:
        return None
    try:
        value = _cursor_payload(token, "history-outputs")
        if type(value) is not dict or set(value) != {
            "version",
            "epoch",
            "run_id",
            "capture_fingerprint",
            "after",
        }:
            raise ValueError
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or _uuid(value["run_id"]) != run_id
            or type(value["capture_fingerprint"]) is not str
            or _DIGEST.fullmatch(value["capture_fingerprint"]) is None
            or type(value["after"]) is not list
            or len(value["after"]) != 2
            or type(value["after"][1]) is not int
            or not 0 <= value["after"][1] <= 255
        ):
            raise ValueError
        node = _node(value["after"][0])
        if _uuid(value["epoch"]) != epoch:
            raise HistoryReadError(
                409, "history-changed", {"action": "refresh-history"}
            )
        return OutputPosition(value["capture_fingerprint"], (node, value["after"][1]))
    except (ValueError, UnicodeError):
        raise HistoryReadError(400, "invalid-cursor") from None


def _encode_output_cursor(
    position: OutputPosition | None, run_id: str, epoch: str
) -> str | None:
    if position is None:
        return None
    return _encode(
        {
            "version": 1,
            "epoch": epoch,
            "run_id": run_id,
            "capture_fingerprint": position.capture_fingerprint,
            "after": list(position.after),
        },
        _scope({"kind": "history-outputs"}),
    )


def _current_stale() -> HistoryReadError:
    return HistoryReadError(409, "candidate-stale", {"action": "reload-outputs"})


def _current_unavailable(reason: str) -> HistoryReadError:
    if reason == "temporary_unavailable":
        return HistoryReadError(503, "storage-unavailable", {"action": "retry-read"})
    if reason == "missing":
        return HistoryReadError(404, "not-found", {"action": "retry-read"})
    return HistoryReadError(403, "not-authorized", {"action": "retry-read"})


def _check_current(
    scope: CurrentOutputScope,
    api: object,
    fence: HistoryReadFence,
    epoch: str,
    generation: int,
) -> None:
    if snapshot_current_output_scope(api).current_scope != scope.current_scope:
        raise _current_stale()
    fence.validate(epoch, generation)


def _media_descriptor(receipt: dict, candidate: dict, epoch: str) -> dict:
    occurrence = receipt["occurrence"]
    query = urlencode(
        {
            "node": occurrence["output_node_id"],
            "ordinal": occurrence["image_ordinal"],
            "epoch": epoch,
            "current_scope": candidate["current_scope"],
        }
    )
    base = f"/prompt_manager/history/runs/{occurrence['run_id']}/output/current"
    return {
        "kind": "reported-location",
        "occurrence": occurrence,
        "history_epoch": epoch,
        "current_scope": candidate["current_scope"],
        "location_key": candidate["location_key"],
        "reference": receipt["reference"],
        "filename": receipt["reference"]["filename"],
        "is_video": receipt["reference"]["filename"]
        .lower()
        .endswith(tuple(VIDEO_EXTENSIONS)),
        "url": f"{base}/media?{query}",
        "thumbnail_url": f"{base}/thumbnail?{query}",
        "inspection_url": f"{base}/inspection?{query}",
    }


def _close_prepared_result(task: asyncio.Task) -> None:
    try:
        result = task.result()
    except (Exception, asyncio.CancelledError):
        return
    if not isinstance(result, bytes):
        result.close()


def _wire_run(run: dict) -> None:
    run["diagnostics_omitted"] = bool(run["diagnostics_omitted"])
    for key in _RUN_DECIMALS:
        if run[key] is not None:
            run[key] = str(run[key])


def _serialize_history(payload: dict, *, max_bytes: int | None = None) -> bytes:
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise sqlite3.DatabaseError("Invalid recorded history payload") from None
    if max_bytes is not None and len(encoded) > max_bytes:
        raise HistoryReadError(422, "response-limit")
    return encoded


def _success_response(encoded: bytes) -> web.Response:
    return web.Response(
        body=encoded,
        content_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


class HistoryRoutesMixin:
    def _register_history_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/library/{positive_id}/history")(
            self.get_library_history
        )
        routes.get(
            "/prompt_manager/library/{positive_id}/history/pairs/{pair_id}/newest"
        )(self.get_newest_pair_run)
        routes.get("/prompt_manager/history/runs/{run_id}")(self.get_history_run)
        routes.get("/prompt_manager/history/runs/{run_id}/outputs")(
            self.get_history_outputs
        )
        routes.get("/prompt_manager/history/runs/{run_id}/output/current/media")(
            self.get_history_current_media
        )
        routes.get("/prompt_manager/history/runs/{run_id}/output/current/thumbnail")(
            self.get_history_current_thumbnail
        )
        routes.get("/prompt_manager/history/runs/{run_id}/output/current/inspection")(
            self.get_history_current_inspection
        )

    def _history_fence(self) -> HistoryReadFence:
        # Called synchronously by the event-loop owner before scheduling work.
        if self._history_reads_closed:
            raise sqlite3.OperationalError("History reads are closed")
        if self._history_read_fence is None:
            self._history_read_fence = HistoryReadFence(self.db)
        return self._history_read_fence

    def close_history_reads(self) -> None:
        self._history_reads_closed = True
        if self._history_read_fence is not None:
            self._history_read_fence.close()

    async def cleanup_history_reads(self, app: web.Application) -> None:
        self.close_history_reads()

    @_history_errors
    async def get_library_history(self, request: web.Request) -> web.Response:
        _query(request)
        if set(request.query) - {"limit", "cursor", "resume"} or (
            "cursor" in request.query and "resume" in request.query
        ):
            raise ValueError("Invalid history window query")
        positive_id = _decimal(request.match_info["positive_id"], minimum=1)
        limit = _page_limit(request)
        resuming = "resume" in request.query
        expected_epoch, position = _decode_window_cursor(
            request.query.get("resume" if resuming else "cursor"),
            positive_id,
            resume=resuming,
        )
        fence = self._history_fence()
        db = self.db

        def project() -> bytes:
            generation = fence.begin()
            try:
                with db.recorded_history_snapshot(expected_epoch) as epoch:
                    try:
                        result = db.list_history_window(
                            positive_id, limit=limit, after=position, restart=resuming
                        )
                    except ValueError:
                        raise HistoryReadError(400, "invalid-cursor") from None
            finally:
                # Restore may retire this owner before a queued read opens it.
                db.model.close()
            if result is None:
                fence.validate(epoch, generation)
                raise HistoryReadError(404, "not-found")
            result["next_cursor"] = _encode_window_cursor(
                result.pop("next_position"), epoch, positive_id
            )
            result["resume_cursor"] = _encode_window_cursor(
                result.pop("resume_position"), epoch, positive_id, resume=True
            )
            for run in result["items"]:
                _wire_run(run)
            encoded = _serialize_history(
                {
                    "success": True,
                    "history_version": 1,
                    "history_epoch": epoch,
                    **result,
                }
            )
            fence.validate(epoch, generation)
            return encoded

        return _success_response(await self._run_in_executor(project))

    @_history_errors
    async def get_newest_pair_run(self, request: web.Request) -> web.Response:
        """The pair's newest recorded run for the history run dot (#249 D26)."""
        _query(request)
        if request.query:
            raise ValueError("Invalid newest pair run query")
        positive_id = _decimal(request.match_info["positive_id"], minimum=1)
        pair_id = _decimal(request.match_info["pair_id"], minimum=1)
        fence = self._history_fence()
        db = self.db

        def project() -> bytes:
            generation = fence.begin()
            try:
                with db.recorded_history_snapshot() as epoch:
                    result = db.get_newest_pair_run(positive_id, pair_id)
            finally:
                db.model.close()
            if result is None:
                fence.validate(epoch, generation)
                raise HistoryReadError(404, "not-found")
            encoded = _serialize_history(
                {
                    "success": True,
                    "history_version": 1,
                    "history_epoch": epoch,
                    **result,
                }
            )
            fence.validate(epoch, generation)
            return encoded

        return _success_response(await self._run_in_executor(project))

    @_history_errors
    async def get_history_run(self, request: web.Request) -> web.Response:
        _query(request)
        run_id = _uuid(request.match_info["run_id"])
        expected_epoch = request.query.get("epoch")
        if expected_epoch is not None:
            _uuid(expected_epoch)
        fence = self._history_fence()
        db = self.db

        def project() -> bytes:
            generation = fence.begin()
            try:
                with db.recorded_history_snapshot(expected_epoch) as epoch:
                    result = db.get_history_run(run_id)
            finally:
                db.model.close()
            if result is None:
                fence.validate(epoch, generation)
                raise HistoryReadError(404, "not-found")
            _wire_run(result["run"])
            encoded = _serialize_history(
                {
                    "success": True,
                    "history_version": 1,
                    "history_epoch": epoch,
                    **result,
                },
                max_bytes=_RUN_MAX_BYTES,
            )
            fence.validate(epoch, generation)
            return encoded

        return _success_response(await self._run_in_executor(project))

    def _history_gallery(
        self, conn: sqlite3.Connection, paths: tuple[str, ...]
    ) -> tuple[dict, bool]:
        try:
            return read_history_gallery(conn, paths), False
        except Exception as error:
            self.logger.warning(
                "Saved history display unavailable (%s)", type(error).__name__
            )
            return {}, True

    @_history_errors
    async def get_history_outputs(self, request: web.Request) -> web.Response:
        _query(request)
        run_id = _uuid(request.match_info["run_id"])
        expected_epoch = _uuid(request.query.get("epoch"))
        limit = _page_limit(request)
        position = _decode_output_cursor(
            request.query.get("cursor"), run_id, expected_epoch
        )
        fence = self._history_fence()
        db = self.db

        def project() -> bytes:
            generation = fence.begin()
            scope = snapshot_current_output_scope(self)
            try:
                with db.recorded_history_snapshot(expected_epoch) as epoch:
                    try:
                        result = db.list_history_outputs(
                            run_id, limit=limit, after=position
                        )
                    except HistoryCaptureChanged:
                        raise HistoryReadError(
                            409, "capture-changed", {"action": "reload-outputs"}
                        ) from None
                    if result is not None:
                        paths = tuple(
                            dict.fromkeys(
                                path
                                for item in result["items"]
                                if (path := candidate_locator(item["reference"], scope))
                                is not None
                            )
                        )
                        gallery, saved_unavailable = self._history_gallery(
                            db.model.get_connection(), paths
                        )
            finally:
                db.model.close()
            if result is None:
                _check_current(scope, self, fence, epoch, generation)
                raise HistoryReadError(404, "not-found")
            resolved = {}
            for receipt in result["items"]:
                reference = receipt["reference"]
                locator = tuple(reference.items())
                if locator not in resolved:
                    try:
                        if saved_unavailable:
                            raise sqlite3.OperationalError(
                                "Optional saved display unavailable"
                            )
                        resolved[locator] = resolve_current_candidate(
                            reference, scope, gallery
                        ).public
                    except Exception:
                        resolved[locator] = {
                            "status": "error",
                            "reason": "temporary_unavailable",
                            "evidence_class": "locator_candidate",
                        }
                candidate = dict(resolved[locator])
                if candidate["status"] == "available":
                    candidate["media"] = _media_descriptor(receipt, candidate, epoch)
                receipt["current_candidate"] = candidate
            result["next_cursor"] = _encode_output_cursor(
                result.pop("next_position"), run_id, epoch
            )
            encoded = _serialize_history(
                {
                    "success": True,
                    "history_version": 1,
                    "history_epoch": epoch,
                    **result,
                }
            )
            _check_current(scope, self, fence, epoch, generation)
            if len(encoded) > _OUTPUTS_MAX_BYTES:
                recovery = (
                    {"action": "smaller-limit", "limit": max(1, limit // 2)}
                    if limit > 1
                    else None
                )
                raise HistoryReadError(422, "response-limit", recovery)
            return encoded

        return _success_response(await self._run_in_executor(project))

    @_history_errors
    async def get_history_current_media(
        self, request: web.Request
    ) -> web.StreamResponse:
        return await self._history_current(request, "media")

    @_history_errors
    async def get_history_current_thumbnail(
        self, request: web.Request
    ) -> web.StreamResponse:
        return await self._history_current(request, "thumbnail")

    @_history_errors
    async def get_history_current_inspection(
        self, request: web.Request
    ) -> web.Response:
        return await self._history_current(request, "inspection")

    def _history_own_category(self, reference: dict, scope):
        """The recorded output's own category (#332), inside the snapshot.

        The path is the symlink-resolved output folder plus the receipt's
        subfolder and filename, keyed by ``image_category_key`` over the
        configured output sources as Images keys it. A temp output or a path
        no source holds has none. A failed read is OWN_CATEGORY_UNREAD.
        """
        if (
            reference.get("folder_type") != "output"
            or scope.canonical_output is None
            or candidate_locator(reference, scope) is None
        ):
            return None
        path = os.path.join(
            str(scope.canonical_output), reference["subfolder"], reference["filename"]
        )
        try:
            key = image_category_key(self._get_all_output_dirs(), path)
            return None if key is None else self.db.image_category(*key)
        except Exception:
            self.logger.warning(
                "History image category could not be read", exc_info=True
            )
            return OWN_CATEGORY_UNREAD

    async def _history_current(
        self, request: web.Request, kind: str
    ) -> web.StreamResponse:
        _query(request)
        run_id = _uuid(request.match_info["run_id"])
        node = _node(request.query.get("node"))
        ordinal = _decimal(request.query.get("ordinal"))
        if ordinal > 255:
            raise ValueError("Invalid output ordinal")
        expected_epoch = _uuid(request.query.get("epoch"))
        current_scope = request.query.get("current_scope")
        if current_scope is None or _DIGEST.fullmatch(current_scope) is None:
            raise ValueError("Invalid current output scope")
        fence = self._history_fence()
        db = self.db

        def project() -> bytes | _OpenedCurrentMedia:
            generation = fence.begin()
            scope = snapshot_current_output_scope(self)
            try:
                with db.recorded_history_snapshot(expected_epoch) as epoch:
                    if scope.current_scope != current_scope:
                        raise _current_stale()
                    result = db.get_history_occurrence(run_id, node, ordinal)
                    own = None
                    if result is not None:
                        receipt = result["receipt"]
                        path = candidate_locator(receipt["reference"], scope)
                        gallery, saved_unavailable = self._history_gallery(
                            db.model.get_connection(),
                            (path,) if path is not None else (),
                        )
                        if kind == "inspection":
                            own = self._history_own_category(
                                receipt["reference"], scope
                            )
            finally:
                db.model.close()
            if result is None:
                _check_current(scope, self, fence, epoch, generation)
                raise HistoryReadError(404, "not-found")
            resolved = resolve_current_candidate(receipt["reference"], scope, gallery)
            if resolved.public["status"] != "available":
                _check_current(scope, self, fence, epoch, generation)
                raise _current_unavailable(resolved.public["reason"])
            prepared = None
            try:
                if kind == "inspection":
                    saved = gallery.get(path, {})
                    _reauthorize_current_target(resolved, scope)
                    observed = inspect_current_file(
                        resolved.canonical_target,
                        source_label=resolved.public["source_label"],
                        saved=saved.get("saved"),
                        library=apply_own_category(
                            saved.get(
                                "library",
                                {
                                    "status": (
                                        "unavailable" if saved_unavailable else "none"
                                    )
                                },
                            ),
                            own,
                        ),
                        recorded=recorded_output_lookup(
                            db, close=True, reference=receipt["reference"]
                        ),
                    )
                    _reauthorize_current_target(resolved, scope)
                    identity = {
                        **receipt,
                        "history_epoch": epoch,
                        "current_scope": scope.current_scope,
                        "location_key": resolved.public["location_key"],
                        "evidence_class": "locator_candidate",
                        "observation": "current-file",
                    }
                    try:
                        prepared = encode_history_inspection_response(
                            current_output=identity, **observed
                        )
                    except ValueError:
                        _check_current(scope, self, fence, epoch, generation)
                        raise HistoryReadError(422, "response-limit") from None
                else:
                    prepared = _prepare_current_media(
                        resolved,
                        scope,
                        thumbnail=kind == "thumbnail",
                        **media_options,
                    )
                _check_current(scope, self, fence, epoch, generation)
                return prepared
            except CurrentFileChanged:
                if prepared is not None and not isinstance(prepared, bytes):
                    prepared.close()
                _check_current(scope, self, fence, epoch, generation)
                raise _current_stale() from None
            except CurrentLocationUnavailable as error:
                if prepared is not None and not isinstance(prepared, bytes):
                    prepared.close()
                _check_current(scope, self, fence, epoch, generation)
                raise (
                    _current_stale()
                    if error.stale
                    else _current_unavailable(error.reason)
                ) from None
            except BaseException as error:
                if prepared is not None and not isinstance(prepared, bytes):
                    prepared.close()
                if isinstance(error, Exception) and not isinstance(
                    error, (HistoryReadError, HistoryEpochChanged, sqlite3.Error)
                ):
                    _check_current(scope, self, fence, epoch, generation)
                raise

        async def execute():
            pending = asyncio.create_task(self._run_in_executor(project))
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                pending.add_done_callback(_close_prepared_result)
                raise
            except (
                HistoryReadError,
                HistoryEpochChanged,
                sqlite3.Error,
                OSError,
                ValueError,
            ):
                raise
            except Exception as error:
                self.logger.warning(
                    "Current history media unavailable (%s)", type(error).__name__
                )
                raise _current_unavailable("temporary_unavailable") from None

        # A missing preview (#249) is noted on the first read, made through
        # the serve route's admission and single flight, then read again. A
        # video whose preview cannot be made is the placeholder frame.
        needed = []
        media_options = {}
        if kind == "thumbnail":
            media_options = {
                "ensure_preview": lambda *preview: needed.append(preview),
                "placeholder": self._video_placeholder_bytes(),
                "accepts_jxl": _jxl_flag(request) or accepts_jxl(request),
            }
        prepared = await execute()
        if needed:
            if not isinstance(prepared, bytes):
                prepared.close()
            await self._admit_preview(*needed[0])
            media_options["ensure_preview"] = None
            prepared = await execute()
        if isinstance(prepared, bytes):
            return _success_response(prepared)
        return await _current_media_response(request, prepared)
