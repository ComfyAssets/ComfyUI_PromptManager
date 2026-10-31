"""Needs review routes (#249 E3): the pending queue, item detail, decisions.

``GET /prompt_manager/review/items`` lists pending items oldest first behind
an HMAC keyset cursor bound to the storage epoch only (a restore makes an
old cursor stale; a decision or a new item does not, because the
``(created_at, id)`` keyset stays ordered when items leave or are appended);
``total`` is the exact pending count. ``GET .../items/{id}`` adds the
candidates and the dependency fingerprint. ``POST .../items/{id}/decision``
records one explicit decision: Apply links the chosen sampler's pair only
when the capture is the item's bytes, the current file still hashes to them,
the evidence they yield is unchanged and the ingester's compatible-only rules
allow it; Leave unresolved links nothing. ``POST .../items/{id}/link`` (#536)
links a Library prompt the reviewer chose, under the same capture and
compatibility guards; it is their choice, so what the bytes resolve to is not
asked.

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
import urllib.parse

from aiohttp import web

from .annotations import _capture_error, _error
from .images import media_thumbnail_url
from .inspection_service import VIDEO_EXTENSIONS
from .job_routes import busy_response
from .jobs import JobBusy
from .prompts import bad_request
from .review_guards import UNRESOLVED, _decision_guards, _link_guards

try:
    from ...database.library import display_title
    from ...database.library_commands import LibraryCommandError
    from ...database.review_decisions import ReviewCommandError
    from ...utils.media_capture import CaptureError
    from ...utils.review_capture import (
        captured_review,
        item_profile,
        review_evidence,
    )
except ImportError:
    from database.library import display_title
    from database.library_commands import LibraryCommandError
    from database.review_decisions import ReviewCommandError
    from utils.media_capture import CaptureError
    from utils.review_capture import captured_review, item_profile, review_evidence

ITEMS_URL = "/prompt_manager/review/items"
DEFAULT_LIMIT = 40
MAX_LIMIT = 40
MAX_CURSOR = 4096
MAX_BODY_BYTES = 64 * 1024
# The serialized detail stays under this; past it the detail is ``limited``.
MAX_DETAIL_BYTES = 8 * 1024 * 1024
_CURSOR_SECRET = os.urandom(32)
# The real path of a spelling; module-level so a test can fake a junction.
_realpath = os.path.realpath
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
    "prompt-missing": 409,
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
    "prompt-missing": (
        "That prompt or recipe is no longer in the Library. Search again."
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


class _StaleCursor(ValueError):
    """A well-formed cursor this process cannot verify (e.g. after a restart)."""


def _encode_cursor(epoch: str, key: tuple) -> str:
    payload = json.dumps(
        {"v": 2, "e": epoch, "k": list(key)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    signature = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _decode_cursor(token: str) -> tuple:
    """``(epoch, (created_at, id))`` of a cursor.

    ``ValueError`` when malformed; :class:`_StaleCursor` when well-formed but
    signed by another secret (the per-process secret changes on every start,
    so an open page's cursor is stale after a restart, not forged).
    """
    try:
        if len(token) > MAX_CURSOR:
            raise ValueError()
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        signature, payload = raw[:32], raw[32:]
        if len(signature) != 32 or not payload:
            raise ValueError()
        expected = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise _StaleCursor("This page of the queue is out of date")
        data = json.loads(payload)
        key = data["k"]
        if (
            data["v"] != 2
            or not isinstance(data["e"], str)
            or not isinstance(key, list)
            or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise ValueError()
        return data["e"], tuple(key)
    except _StaleCursor:
        raise
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


REVIEW_DECISION_LEASE = "Needs review decision"


class ReviewRoutesMixin:
    """The Needs review queue over E1's schema and E2's recorded items."""

    def _register_review_routes(self, routes: web.RouteTableDef) -> None:
        routes.get(ITEMS_URL)(self.list_review_items)
        routes.get(ITEMS_URL + "/{item_id}")(self.get_review_item)
        routes.post(ITEMS_URL + "/{item_id}/decision")(self.decide_review_item)
        routes.post(ITEMS_URL + "/{item_id}/link")(self.link_review_item)

    # -- shared ----------------------------------------------------------------

    def _review_roots(self):
        """``(roots, real_roots)``: the current gallery roots and their realpaths.

        Blocking (it lists the roots and resolves them): call it on a worker.
        """
        roots = list(self._get_all_output_dirs())
        return roots, [(i, _realpath(str(root))) for i, root in enumerate(roots)]

    def _review_view(self, row: dict, roots, real_roots) -> dict:
        """The public ReviewItem of one stored row (no path, no locator)."""
        found = format_review_target(row["spelled_path"], row["locator"], real_roots)
        target = url = thumbnail_url = None
        if found is not None:
            index, relative = found
            target = {
                "root_index": index,
                "root_scope": self._gallery_root_scope(roots[index], index),
                "path": relative,
            }
            # The Images listing's media URL for the same file (formatting only).
            url = (
                "/prompt_manager/images/serve/"
                f"{urllib.parse.quote(relative, safe='/')}?root={index}"
                f"&root_scope={target['root_scope']}"
            )
            # The Images listing's thumbnail, when one exists (one stat).
            thumbnail_url = media_thumbnail_url(
                roots[index], relative, index, root_scope=target["root_scope"]
            )
        filename = os.path.basename(row["spelled_path"])
        return {
            "id": row["id"],
            "state": row["state"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "filename": filename,
            "target": target,
            "unavailable": target is None,
            "candidate_count": row["candidate_count"],
            "limited": row["limited"],
            "url": url,
            "thumbnail_url": thumbnail_url,
            "is_video": os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS,
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
            except _StaleCursor:
                return await self._stale_cursor()
            except ValueError as exc:
                return bad_request(str(exc))
        try:
            page, views = await self._run_in_executor(
                self._review_page, limit, bound[1] if bound else None
            )
        except Exception:
            self.logger.exception("Review queue read failed")
            return _failed()
        if bound is not None and bound[0] != page["epoch"]:
            return _coded("storage-changed", library_epoch=page["epoch"])
        items = page["items"]
        next_cursor = None
        if page["more"] and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                page["epoch"], (last["created_at"], last["id"])
            )
        return web.json_response(
            {
                "success": True,
                "library_epoch": page["epoch"],
                # F3: the queue version the change stream's ``ready`` names.
                "review_revision": page["revision"],
                "total": page["total"],
                "items": views,
                "next_cursor": next_cursor,
            },
            headers=_NO_STORE,
        )

    async def _stale_cursor(self) -> web.Response:
        """409 ``review-conflict`` with the current total: reload the queue."""
        try:
            page = await self._run_in_executor(self.db.review_queue_page, limit=1)
        except Exception:
            self.logger.exception("Review queue read failed")
            return _failed()
        return _coded(
            "review-conflict", total=page["total"], library_epoch=page["epoch"]
        )

    def _review_page(self, limit: int, after):
        """Worker thread: one queue page and the public views of its items."""
        page = self.db.review_queue_page(limit=limit, after=after)
        roots, real_roots = self._review_roots()
        return page, [
            self._review_view(row, roots, real_roots) for row in page["items"]
        ]

    # -- detail ----------------------------------------------------------------

    async def get_review_item(self, request: web.Request) -> web.Response:
        """GET one item with its candidates and dependency fingerprint."""
        try:
            read = await self._run_in_executor(
                self._review_detail, request.match_info["item_id"]
            )
        except Exception:
            self.logger.exception("Review item read failed")
            return _failed()
        if read is None:
            return _coded("review-missing")
        item, view, fingerprint, candidates = read
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

    def _review_detail(self, item_id: str):
        """Worker thread: ``(item, view, fingerprint, candidates)`` or None."""
        item = self.db.review_item(item_id)
        if item is None:
            return None
        roots, real_roots = self._review_roots()
        view = self._review_view(item, roots, real_roots)
        image_path = self._review_image_path(item, roots, view["target"])
        aliases, _known = physical_spellings(self.db, image_path, item["locator"])
        fingerprint = self.db.review_dependency_fingerprint(image_path, aliases)
        return item, view, fingerprint, self._review_candidates(item)

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
        """POST one explicit decision on one pending item.

        The decision runs under a registry lease (#249 S8): it is refused
        (409 ``busy``) while a restore or another ``exclusive`` job runs, and
        a restore waits for decisions already running before it switches the
        database. Decisions never refuse one another or a scan.
        """
        try:
            async with self._jobs.lease(REVIEW_DECISION_LEASE):
                return await self._decide_review_item(request)
        except JobBusy as busy:
            return busy_response(busy)

    async def link_review_item(self, request: web.Request) -> web.Response:
        """POST the Library prompt the reviewer chose for one pending item.

        Under the same lease as a decision. The capture must be the item's
        current bytes; the choice and the file's links are checked in the
        decision's transaction.
        """
        try:
            async with self._jobs.lease(REVIEW_DECISION_LEASE):
                return await self._link_review_item(request)
        except JobBusy as busy:
            return busy_response(busy)

    async def _link_review_item(self, request: web.Request) -> web.Response:
        data, error = await self._read_json_body(request, max_bytes=MAX_BODY_BYTES)
        if error is not None:
            return error
        guards, error = _link_guards(data)
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
        verified, error = await self._verified_review_capture(
            item, guards, need_metadata=True
        )
        if error is not None:
            return error
        manual = {"positive_id": guards["positive_id"], "pair_id": guards["pair_id"]}
        return await self._record_decision(item, guards, None, verified, manual)

    async def _decide_review_item(self, request: web.Request) -> web.Response:
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
        verified = None
        if guards["capture_token"] is not None:
            verified, error = await self._verified_review_capture(
                item, guards, need_metadata=candidate is not None
            )
            if error is not None:
                return error
        elif guards["sha256"] != item["sha256"]:
            return bad_request("sha256 does not match the item")
        return await self._record_decision(item, guards, candidate, verified)

    async def _verified_review_capture(self, item, guards, *, need_metadata):
        """``((capture, facts, image_path, metadata), None)`` or ``(None, error)``.

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
        try:
            root, error = await self._run_in_executor(
                self._scoped_root, capture.root_index, capture.scope
            )
            if error is not None:
                return None, error
            read = await self._run_in_executor(
                _verify_and_read, store, capture, item, root, need_metadata
            )
        except CaptureError as exc:
            return None, _capture_error(exc)
        if read is None:
            return None, _coded("source-changed")
        relative, facts, captured = read
        if facts.locator != item["locator"]:
            return None, _coded("capture-expired", "other-file")
        if facts.sha256 != item["sha256"]:
            return None, _coded("file-changed")
        metadata = None
        if need_metadata:
            # A manual link has no ``choice``: it rests on the bytes alone.
            if captured is None or (
                "choice" in guards
                and not _candidate_still_offered(captured[1], guards["choice"])
            ):
                return None, _coded("review-conflict", "evidence-changed")
            metadata = captured[0]
        image_path = os.path.join(str(root), *relative.split("/"))
        return (capture, facts, image_path, metadata), None

    def _decide_sync(self, item, guards, candidate, verified, manual=None):
        """Worker thread: resolve the file's spellings, decide, build the view.

        Returns ``(result, view)``; a refusal raises with ``exc.view`` set.
        """
        _capture, facts, image_path, metadata = verified or (None, None, None, None)
        aliases, known = (), None
        selected = candidate is not None or manual is not None
        if selected:
            aliases, known = physical_spellings(self.db, image_path, item["locator"])
        try:
            result = self.db.decide_review_item(
                item_id=item["id"],
                expected_epoch=guards["expected_epoch"],
                expected_revision=guards["expected_revision"],
                # The bytes the re-hash proved, or the body's for Leave unresolved.
                sha256=facts.sha256 if facts is not None else guards["sha256"],
                outcome="selected" if selected else UNRESOLVED,
                candidate=candidate,
                manual=manual,
                image_path=image_path,
                metadata=metadata,
                expected_fingerprint=guards["dependency_fingerprint"],
                aliases=aliases,
                known_rows=known,
            )
        except ReviewCommandError as exc:
            if exc.item is not None:
                roots, real_roots = self._review_roots()
                exc.view = self._review_view(exc.item, roots, real_roots)
            raise
        roots, real_roots = self._review_roots()
        return result, self._review_view(result["item"], roots, real_roots)

    async def _record_decision(self, item, guards, candidate, verified, manual=None):
        capture = verified[0] if verified else None
        try:
            result, view = await self._run_in_executor(
                self._decide_sync, item, guards, candidate, verified, manual
            )
        except ReviewCommandError as exc:
            return self._decision_refused(exc)
        except LibraryCommandError as exc:
            return _coded(exc.code, library_epoch=exc.epoch)
        except Exception:
            self.logger.exception("Review decision failed")
            return _error(500, "decision-failed", "The decision could not be saved.")
        if capture is not None:
            # The decision used the capture; the page returns to Images.
            self._captures.release(capture.token)
        body = {
            "success": True,
            "library_epoch": guards["expected_epoch"],
            "item": view,
            "total": result["total"],
        }
        if result["pair"] is not None:
            body["pair"] = result["pair"]
        return web.json_response(body, headers=_NO_STORE)

    def _decision_refused(self, exc: ReviewCommandError) -> web.Response:
        extra = {"library_epoch": exc.epoch}
        if exc.total is not None:
            extra["total"] = exc.total
        if exc.view is not None:
            extra["item"] = exc.view
        message = None
        if exc.fingerprint is not None:
            extra["dependency_fingerprint"] = exc.fingerprint
        if exc.code == "association-conflict" and exc.changed:
            message = "dependencies-changed"
        return _coded(exc.code, message, **extra)


def _verify_and_read(store, capture, item, root, need_metadata: bool):
    """Worker thread: ``(relative, facts, captured)``, or None off the root.

    Formats the item's target under the capture's root (resolving the root's
    real path), re-hashes the current file, then reads the capture and
    resolves it with the profile the item's evidence was recorded with.
    """
    found = format_review_target(
        item["spelled_path"],
        item["locator"],
        [(capture.root_index, _realpath(str(root)))],
    )
    if found is None:
        return None
    relative = found[1]
    facts = store.verify(root, relative)
    captured = None
    if need_metadata:
        # The file's own name, so an inspection item resolves anchored on its
        # saver as it was recorded (#390).
        captured = captured_review(
            store,
            capture,
            item_profile(item["evidence"]),
            relative.rsplit("/", 1)[-1],
        )
    return relative, facts, captured


def _same_file(spelled: str, image_path: str, locator: str) -> bool:
    """True when ``spelled`` reaches the physical file at ``locator``."""
    try:
        if os.path.normcase(_realpath(spelled)) == locator:
            return True
        return os.path.samefile(spelled, image_path)
    except (OSError, ValueError):
        return False


def physical_spellings(db, image_path: str, locator: str):
    """``(aliases, known_rows)`` for the legacy rows of one physical file.

    The roots are real paths, while the monitor links a file under the
    directory it watches, so a symlinked or junctioned root, a ``subst`` or a
    mapped drive spells the same file otherwise. Every row whose spelling
    ends with the file's name (the link summaries' fallback lookup) is a
    candidate; ``aliases`` are the candidates that resolve to ``locator``
    (``normcase(realpath)``, as the capture forms it) or are the same file,
    and ``known_rows`` every candidate id, so a decision can refuse a
    same-name row it did not classify. Blocking: call it on a worker.
    """
    rows = db.review_same_name_rows(os.path.basename(image_path))
    aliases = tuple(
        spelled
        for _row_id, spelled in rows
        if isinstance(spelled, str)
        and spelled != image_path
        and _same_file(spelled, image_path, locator)
    )
    return tuple(dict.fromkeys(aliases)), frozenset(row_id for row_id, _ in rows)


def _candidate_still_offered(resolution: dict, choice: str) -> bool:
    """True when the captured bytes still offer the chosen candidate.

    Only what the decision depends on is re-checked: the bytes are still
    ``ambiguous`` and the chosen candidate (its hash covers its sampler,
    sides and provenance) is still among their candidates. A resolver change
    that rewords the top-level reason or reorders candidates leaves a
    pending item applicable.
    """
    if isinstance(resolution, dict) and resolution.get("anchor") == "saver":
        # Only the saver anchor resolves the bytes (#390): its pair alone.
        resolution = {"state": "ambiguous", "candidates": [resolution]}
    if not isinstance(resolution, dict) or resolution.get("state") != "ambiguous":
        return False
    evidence, _count, limited = review_evidence(resolution)
    if limited:
        return False
    return any(c.get("hash") == choice for c in evidence.get("candidates", ()))
