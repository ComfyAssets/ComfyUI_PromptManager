"""Authenticated chronological windows and bounded frozen run details."""

import base64
import binascii
from functools import wraps
import json
import re
import sqlite3
from typing import Callable

from aiohttp import web

from .library import _decode, _encode, _scope

try:
    from ...database.execution_history import HistoryEpochChanged
    from ...database.history_reads import RunKey, WindowPosition
    from ...utils.history_read_fence import HistoryReadFence
except ImportError:
    from database.execution_history import HistoryEpochChanged
    from database.history_reads import RunKey, WindowPosition
    from utils.history_read_fence import HistoryReadFence


_RUN_MAX_BYTES = 8 * 1024 * 1024
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
    token: str | None, positive_id: int
) -> tuple[str | None, WindowPosition | None]:
    if token is None:
        return None, None
    try:
        value = _cursor_payload(token, "history-window")
        if type(value) is not dict or set(value) != {
            "version",
            "epoch",
            "positive_id",
            "raw_text_id",
            "direction",
            "upper",
            "before",
        }:
            raise ValueError
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or _decimal(value["positive_id"], minimum=1) != positive_id
            or value["direction"] != "older"
            or type(value["raw_text_id"]) is not str
            or _DIGEST.fullmatch(value["raw_text_id"]) is None
        ):
            raise ValueError
        epoch = _uuid(value["epoch"])
        upper = _decode_run_key(value["upper"])
        before = _decode_run_key(value["before"])
        if before > upper:
            raise ValueError
        return epoch, WindowPosition(value["raw_text_id"], upper, before)
    except ValueError:
        raise HistoryReadError(400, "invalid-cursor") from None


def _encode_window_cursor(
    position: WindowPosition | None, epoch: str, positive_id: int
) -> str | None:
    if position is None:
        return None
    return _encode(
        {
            "version": 1,
            "epoch": epoch,
            "positive_id": str(positive_id),
            "raw_text_id": position.raw_text_id,
            "direction": "older",
            "upper": _wire_run_key(position.upper),
            "before": _wire_run_key(position.before),
        },
        _scope({"kind": "history-window"}),
    )


def _wire_run(run: dict) -> None:
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
        routes.get("/prompt_manager/history/runs/{run_id}")(self.get_history_run)

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
        positive_id = _decimal(request.match_info["positive_id"], minimum=1)
        limit = _page_limit(request)
        expected_epoch, position = _decode_window_cursor(
            request.query.get("cursor"), positive_id
        )
        fence = self._history_fence()
        db = self.db

        def project() -> bytes:
            generation = fence.begin()
            try:
                with db.recorded_history_snapshot(expected_epoch) as epoch:
                    try:
                        result = db.list_history_window(
                            positive_id, limit=limit, after=position
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
