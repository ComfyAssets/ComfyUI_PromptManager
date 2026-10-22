"""Needs review routes (#249 E3): the pending queue, item detail, decisions.

``GET /prompt_manager/review/items`` lists pending items oldest first behind
an HMAC keyset cursor bound to the storage epoch and the review index
revision (any decision, new item or restore makes an old cursor stale);
``total`` is the exact pending count. ``GET .../items/{id}`` adds the
candidates and the dependency fingerprint. ``POST .../items/{id}/decision``
records one explicit decision: Apply links the chosen sampler's pair only
when the capture is the item's bytes, the current file still hashes to them,
the evidence they yield is unchanged and the ingester's compatible-only rules
allow it; Leave unresolved links nothing.

Targets are formatted from the recorded spelling against the current roots
(no file access beyond the roots' real paths); a file under no current root
is ``unavailable``. Responses carry no server path, no locator and no
exception text.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re

from aiohttp import web

from .annotations import _HEX64, _TOKEN, _UUID, _capture_error, _error
from .prompts import bad_request

try:
    from ...database.library import display_title
    from ...database.library_commands import LibraryCommandError
    from ...database.review_decisions import ReviewCommandError
    from ...database.review_schema import canonical_evidence, evidence_digest
    from ...utils.media_capture import CaptureError
    from ...utils.review_capture import captured_review, review_evidence
except ImportError:
    from database.library import display_title
    from database.library_commands import LibraryCommandError
    from database.review_decisions import ReviewCommandError
    from database.review_schema import canonical_evidence, evidence_digest
    from utils.media_capture import CaptureError
    from utils.review_capture import captured_review, review_evidence

ITEMS_URL = "/prompt_manager/review/items"
DEFAULT_LIMIT = 40
MAX_LIMIT = 40
MAX_CURSOR = 4096
MAX_BODY_BYTES = 64 * 1024
# The serialized detail stays under this; past it the detail is ``limited``.
MAX_DETAIL_BYTES = 8 * 1024 * 1024
UNRESOLVED = "unresolved"
_DECISION_FIELDS = frozenset(
    {
        "expected_epoch",
        "expected_revision",
        "capture_token",
        "sha256",
        "dependency_fingerprint",
        "choice",
    }
)
_CURSOR_SECRET = os.urandom(32)
_LIMIT = re.compile(r"[0-9]{1,3}")
_NO_STORE = {"Cache-Control": "no-store"}

_STATUS = {
    "review-missing": 404,
    "review-conflict": 409,
    "association-conflict": 409,
    "file-changed": 409,
    "storage-changed": 409,
    "source-changed": 409,
    "capture-expired": 409,
}
_MESSAGES = {
    "review-missing": "This review item no longer exists. Reload the queue.",
    "review-conflict": (
        "This item was decided or changed since it was opened. Reload the queue."
    ),
    "association-conflict": (
        "This image is already linked to another prompt or negative, so this "
        "sampler path cannot be attached safely. The existing links were "
        "preserved."
    ),
    "dependencies-changed": (
        "The image's saved links changed since this item was opened. Reopen it "
        "to see the current association."
    ),
    "file-changed": (
        "The file changed since it was reviewed. A metadata scan adds its new "
        "bytes as a new item."
    ),
    "storage-changed": "Prompt storage was restored; reload the review queue.",
    "source-changed": (
        "The source changed. Refresh the image collection before reviewing this "
        "file."
    ),
    "capture-expired": "The preview expired. Open the item again.",
    "other-file": "The preview belongs to another file. Open the item again.",
    "evidence-changed": (
        "This file's metadata no longer gives the evidence that was reviewed. "
        "Reload the queue."
    ),
}


_DEFAULT_MESSAGE = "The decision could not be saved. Reload the queue."


def _coded(code: str, message_key: str | None = None, **extra) -> web.Response:
    message = _MESSAGES.get(message_key or code, _DEFAULT_MESSAGE)
    return _error(_STATUS.get(code, 409), code, message, **extra)


def _failed() -> web.Response:
    return _error(500, "review-failed", "The review queue could not be read.")


def format_review_target(spelled_path, locator, real_roots, *, pathmod=os.path):
    """``(root_index, relative)`` of the first root that lists the file, or None.

    ``spelled_path`` is the file's recorded spelling, ``locator`` its folded
    identity and ``real_roots`` ``(index, realpath)`` pairs. The relative
    path keeps the recorded spelling (a Windows locator is case-folded, a
    listing target is not) and must fold back to the locator.
    """
    sep = pathmod.sep
    for index, real_root in real_roots:
        head = spelled_path[: len(real_root)]
        if pathmod.normcase(head) != pathmod.normcase(real_root):
            continue
        rest = spelled_path[len(real_root) :]
        if not real_root.endswith(sep):
            if not rest.startswith(sep):
                continue
            rest = rest[1:]
        parts = rest.split(sep)
        if any(
            part in ("", ".", "..") or "/" in part or "\\" in part or ":" in part
            for part in parts
        ):
            continue
        if pathmod.normcase(pathmod.join(real_root, *parts)) != locator:
            continue
        return index, "/".join(parts)
    return None


def _encode_cursor(epoch: str, revision: int, key: tuple) -> str:
    payload = json.dumps(
        {"v": 1, "e": epoch, "r": revision, "k": list(key)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    signature = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _decode_cursor(token: str) -> tuple:
    """``(epoch, revision, (created_at, id))`` of a cursor; ValueError if forged."""
    try:
        if len(token) > MAX_CURSOR:
            raise ValueError()
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        signature, payload = raw[:32], raw[32:]
        expected = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(payload)
        key = data["k"]
        if (
            data["v"] != 1
            or not isinstance(data["e"], str)
            or type(data["r"]) is not int
            or not isinstance(key, list)
            or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise ValueError()
        return data["e"], data["r"], tuple(key)
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error):
        raise ValueError("Invalid review cursor; reload the queue") from None


def _limit(value):
    if value is None:
        return DEFAULT_LIMIT
    if _LIMIT.fullmatch(value) is None or not 1 <= int(value) <= MAX_LIMIT:
        return None
    return int(value)


def _side_text(side) -> str | None:
    if isinstance(side, dict) and isinstance(side.get("text"), str):
        return side["text"]
    return None


def _workable(candidate: dict) -> bool:
    return (
        candidate.get("state") == "resolved"
        and _side_text(candidate.get("positive")) is not None
        and _side_text(candidate.get("negative")) is not None
    )


def _side_label(titles: dict, side, empty: str) -> str:
    """The Library title of the side's saved prompt, else its first line."""
    text = _side_text(side)
    if text is None:
        return empty
    stored = side.get("storage_text")
    stored = stored if isinstance(stored, str) else text
    return display_title(titles.get(stored, ()), text) or empty


class ReviewRoutesMixin:
    """The Needs review queue over E1's schema and E2's recorded items."""

    def _register_review_routes(self, routes: web.RouteTableDef) -> None:
        routes.get(ITEMS_URL)(self.list_review_items)
        routes.get(ITEMS_URL + "/{item_id}")(self.get_review_item)
        routes.post(ITEMS_URL + "/{item_id}/decision")(self.decide_review_item)

    # -- shared ----------------------------------------------------------------

    def _review_roots(self):
        """``(roots, real_roots)``: the current gallery roots and their realpaths."""
        roots = list(self._get_all_output_dirs())
        return roots, [(i, os.path.realpath(str(root))) for i, root in enumerate(roots)]

    def _review_view(self, row: dict, roots, real_roots) -> dict:
        """The public ReviewItem of one stored row (no path, no locator)."""
        found = format_review_target(row["spelled_path"], row["locator"], real_roots)
        target = None
        if found is not None:
            index, relative = found
            target = {
                "root_index": index,
                "root_scope": self._gallery_root_scope(roots[index], index),
                "path": relative,
            }
        return {
            "id": row["id"],
            "state": row["state"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "filename": os.path.basename(row["spelled_path"]),
            "target": target,
            "unavailable": target is None,
            "candidate_count": row["candidate_count"],
            "limited": row["limited"],
        }

    @staticmethod
    def _review_image_path(row: dict, roots, target) -> str:
        """The path the scan and the monitor link this file under."""
        if target is None:
            return row["spelled_path"]
        root = roots[target["root_index"]]
        return os.path.join(str(root), *target["path"].split("/"))

    # -- queue -----------------------------------------------------------------

    async def list_review_items(self, request: web.Request) -> web.Response:
        """GET ``?limit=1..40&cursor=``: one page of pending items."""
        limit = _limit(request.query.get("limit"))
        if limit is None:
            return bad_request(f"limit must be an integer from 1 to {MAX_LIMIT}")
        bound = None
        cursor = request.query.get("cursor")
        if cursor:
            try:
                bound = _decode_cursor(cursor)
            except ValueError as exc:
                return bad_request(str(exc))
        try:
            page = await self._run_in_executor(
                self.db.review_queue_page,
                limit=limit,
                after=bound[2] if bound else None,
            )
            roots, real_roots = self._review_roots()
        except Exception:
            self.logger.exception("Review queue read failed")
            return _failed()
        if bound is not None and bound[0] != page["epoch"]:
            return _coded("storage-changed", library_epoch=page["epoch"])
        if bound is not None and bound[1] != page["revision"]:
            return _coded("review-conflict", total=page["total"])
        items = page["items"]
        next_cursor = None
        if page["more"] and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                page["epoch"], page["revision"], (last["created_at"], last["id"])
            )
        return web.json_response(
            {
                "success": True,
                "library_epoch": page["epoch"],
                "total": page["total"],
                "items": [self._review_view(row, roots, real_roots) for row in items],
                "next_cursor": next_cursor,
            },
            headers=_NO_STORE,
        )

    # -- detail ----------------------------------------------------------------

    async def get_review_item(self, request: web.Request) -> web.Response:
        """GET one item with its candidates and dependency fingerprint."""
        try:
            item = await self._run_in_executor(
                self.db.review_item, request.match_info["item_id"]
            )
            if item is None:
                return _coded("review-missing")
            roots, real_roots = self._review_roots()
            view = self._review_view(item, roots, real_roots)
            image_path = self._review_image_path(item, roots, view["target"])
            fingerprint = await self._run_in_executor(
                self.db.review_dependency_fingerprint, image_path
            )
            candidates = await self._run_in_executor(self._review_candidates, item)
        except Exception:
            self.logger.exception("Review item read failed")
            return _failed()
        detail = {
            "success": True,
            "library_epoch": item["epoch"],
            **view,
            "candidates": candidates,
            "dependency_fingerprint": fingerprint,
            "sha256": item["sha256"],
            "total": item["total"],
        }
        if len(json.dumps(detail).encode("utf-8")) > MAX_DETAIL_BYTES:
            detail = {**detail, "candidates": [], "limited": True}
        return web.json_response(detail, headers=_NO_STORE)

    def _review_candidates(self, item: dict) -> list:
        """The detail's candidates: role texts, a label and selectability."""
        if item["limited"]:
            return []
        candidates = [
            c for c in item["evidence"].get("candidates", []) if isinstance(c, dict)
        ]
        texts = set()
        for candidate in candidates:
            for role in ("positive", "negative"):
                side = candidate.get(role)
                if isinstance(side, dict):
                    for key in ("text", "storage_text"):
                        if isinstance(side.get(key), str):
                            texts.add(side[key])
        titles = self.db.stored_prompt_titles(texts)
        rows = []
        for candidate in candidates:
            positive = _side_text(candidate.get("positive"))
            negative = _side_text(candidate.get("negative"))
            label = (
                _side_label(titles, candidate.get("positive"), "No positive")
                + " / "
                + _side_label(titles, candidate.get("negative"), "No negative")
            )
            rows.append(
                {
                    "hash": candidate.get("hash"),
                    "sampler_id": candidate.get("sampler_id"),
                    "state": "resolved" if _workable(candidate) else "unresolved",
                    "reason": candidate.get("reason") or "",
                    "label": label,
                    "positive": {"text": positive} if positive is not None else None,
                    "negative": {"text": negative} if negative is not None else None,
                }
            )
        return rows

    # -- decision --------------------------------------------------------------

    async def decide_review_item(self, request: web.Request) -> web.Response:
        """POST one explicit decision on one pending item."""
        data, error = await self._read_json_body(request, max_bytes=MAX_BODY_BYTES)
        if error is not None:
            return error
        guards, error = _decision_guards(data)
        if error is not None:
            return error
        try:
            item = await self._run_in_executor(
                self.db.review_item, request.match_info["item_id"]
            )
        except Exception:
            self.logger.exception("Review item read failed")
            return _failed()
        if item is None:
            return _coded("review-missing")
        choice = guards["choice"]
        candidate = None
        if choice != UNRESOLVED:
            candidate = next(
                (
                    c
                    for c in item["evidence"].get("candidates", [])
                    if isinstance(c, dict) and c.get("hash") == choice
                ),
                None,
            )
            if candidate is None or item["limited"]:
                return bad_request("choice is not one of this item's candidates")
            if not _workable(candidate):
                return bad_request("This sampler path cannot be applied")
            if guards["capture_token"] is None:
                return bad_request("Apply needs a capture of the file")
        roots = list(self._get_all_output_dirs())
        verified = None
        if guards["capture_token"] is not None:
            verified, error = await self._verified_review_capture(
                item, guards, roots, need_metadata=candidate is not None
            )
            if error is not None:
                return error
        elif guards["sha256"] != item["sha256"]:
            return bad_request("sha256 does not match the item")
        return await self._record_decision(item, guards, candidate, verified, roots)

    async def _verified_review_capture(self, item, guards, roots, *, need_metadata):
        """``((capture, image_path, metadata), None)`` or ``(None, error)``.

        The capture must be the item's file and bytes, under the expected
        epoch and a current root; the current file must re-hash to the same
        bytes; for Apply, the capture's metadata must still yield exactly the
        reviewed evidence.
        """
        store = self._captures
        capture = store.get(guards["capture_token"]) if store is not None else None
        if capture is None:
            return None, _coded("capture-expired")
        if capture.locator != item["locator"]:
            return None, _coded("capture-expired", "other-file")
        if guards["sha256"] != capture.sha256:
            return None, bad_request("sha256 does not match the preview")
        if capture.sha256 != item["sha256"]:
            return None, _coded("file-changed")
        if guards["expected_epoch"] != capture.epoch:
            current = await self._run_in_executor(self.db.get_execution_epoch)
            return None, _coded("storage-changed", library_epoch=current)
        root, error = self._scoped_root(capture.root_index, capture.scope)
        if error is not None:
            return None, error
        found = format_review_target(
            item["spelled_path"],
            item["locator"],
            [(capture.root_index, os.path.realpath(str(root)))],
        )
        if found is None:
            return None, _coded("source-changed")
        relative = found[1]
        try:
            facts, captured = await self._run_in_executor(
                _verify_and_read, store, capture, root, relative, need_metadata
            )
        except CaptureError as exc:
            return None, _capture_error(exc)
        if facts.locator != item["locator"]:
            return None, _coded("capture-expired", "other-file")
        if facts.sha256 != item["sha256"]:
            return None, _coded("file-changed")
        metadata = None
        if need_metadata:
            if captured is None or not _same_evidence(captured[1], item):
                return None, _coded("review-conflict", "evidence-changed")
            metadata = captured[0]
        image_path = os.path.join(str(root), *relative.split("/"))
        return (capture, image_path, metadata), None

    async def _record_decision(self, item, guards, candidate, verified, roots):
        capture, image_path, metadata = verified or (None, None, None)
        try:
            result = await self._run_in_executor(
                self.db.decide_review_item,
                item_id=item["id"],
                expected_epoch=guards["expected_epoch"],
                expected_revision=guards["expected_revision"],
                sha256=item["sha256"],
                outcome="selected" if candidate is not None else UNRESOLVED,
                candidate=candidate,
                image_path=image_path,
                metadata=metadata,
                expected_fingerprint=guards["dependency_fingerprint"],
            )
        except ReviewCommandError as exc:
            return self._decision_refused(exc, roots)
        except LibraryCommandError as exc:
            return _coded(exc.code, library_epoch=exc.epoch)
        except Exception:
            self.logger.exception("Review decision failed")
            return _error(500, "decision-failed", "The decision could not be saved.")
        if capture is not None:
            # The decision used the capture; the page returns to Images.
            self._captures.release(capture.token)
        real_roots = [(i, os.path.realpath(str(r))) for i, r in enumerate(roots)]
        body = {
            "success": True,
            "library_epoch": guards["expected_epoch"],
            "item": self._review_view(result["item"], roots, real_roots),
            "total": result["total"],
        }
        if result["pair"] is not None:
            body["pair"] = result["pair"]
        return web.json_response(body, headers=_NO_STORE)

    def _decision_refused(self, exc: ReviewCommandError, roots) -> web.Response:
        extra = {"library_epoch": exc.epoch}
        if exc.total is not None:
            extra["total"] = exc.total
        if exc.item is not None:
            real_roots = [(i, os.path.realpath(str(r))) for i, r in enumerate(roots)]
            extra["item"] = self._review_view(exc.item, roots, real_roots)
        message = None
        if exc.fingerprint is not None:
            extra["dependency_fingerprint"] = exc.fingerprint
        if exc.code == "association-conflict" and exc.changed:
            message = "dependencies-changed"
        return _coded(exc.code, message, **extra)


def _decision_guards(data: dict):
    """The validated decision fields, or ``(None, error)``."""
    if set(data) != _DECISION_FIELDS:
        return None, bad_request(
            "Request must contain exactly " + ", ".join(sorted(_DECISION_FIELDS))
        )
    epoch, revision = data["expected_epoch"], data["expected_revision"]
    if not isinstance(epoch, str) or _UUID.fullmatch(epoch) is None:
        return None, bad_request("expected_epoch must be a canonical UUID")
    if type(revision) is not int or revision < 0:
        return None, bad_request("expected_revision must be a non-negative integer")
    token = data["capture_token"]
    if token is not None and (
        not isinstance(token, str) or _TOKEN.fullmatch(token) is None
    ):
        return None, bad_request("Invalid capture token")
    for name in ("sha256", "dependency_fingerprint"):
        value = data[name]
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            return None, bad_request(
                f"{name} must be 64 lowercase hexadecimal characters"
            )
    choice = data["choice"]
    if not isinstance(choice, str) or (
        choice != UNRESOLVED and _HEX64.fullmatch(choice) is None
    ):
        return None, bad_request("choice must be a candidate hash or unresolved")
    return {**data}, None


def _verify_and_read(store, capture, root, relative: str, need_metadata: bool):
    """Worker thread: re-hash the current file, then read the capture."""
    facts = store.verify(root, relative)
    captured = captured_review(store, capture) if need_metadata else None
    return facts, captured


def _same_evidence(resolution: dict, item: dict) -> bool:
    """True when the captured bytes still yield the item's exact evidence."""
    if not isinstance(resolution, dict) or resolution.get("state") != "ambiguous":
        return False
    evidence, _count, _limited = review_evidence(resolution)
    try:
        return evidence_digest(canonical_evidence(evidence)) == item["evidence_hash"]
    except ValueError:
        return False
