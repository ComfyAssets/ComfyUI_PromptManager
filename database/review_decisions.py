"""Needs review decisions (#249 E3): explicit, auditable and compatible-only.

A decision is one row of ``media_review_decisions`` for one item revision.
``selected`` links the chosen candidate's pair to the file through the public
ingester's compatible-only rules (``_ingest_resolved_pair_in``) in the same
``_pair_transaction`` as the decision row, the item's new state and revision
and the review index revision; any conflict or later failure rolls all of it
back. ``unresolved`` records the decision and links nothing. No decision
counts a run, touches usage or calls an execution writer.

Callers verify the file first (the capture must be the item's bytes and the
current file must still hash to them); nothing here reads the filesystem.
"""

import os
import sqlite3

from .annotation_schema import BYTES_TABLE
from .annotations import _new_id
from .library_commands import LibraryCommandError
from .models import utc_now_iso
from .review import (
    _BUMP_REVIEW_INDEX,
    _DETAIL,
    _DETAIL_COLUMNS,
    _PENDING_COUNT,
    _queue_row,
    dependency_fingerprint,
    dependent_images,
    same_name_rows,
)
from .review_schema import DECISIONS_TABLE, ITEMS_TABLE, canonical_evidence

DECISION_KIND = "user-selection"
_INSERT_DECISION = (
    f"INSERT INTO {DECISIONS_TABLE}(id,item_id,decision_revision,"  # nosec B608
    "created_at,outcome,candidate_hash,evidence,pair_id,image_id) "
    "VALUES(?,?,?,?,?,?,?,?,?)"
)
_ADVANCE_ITEM = (
    f"UPDATE {ITEMS_TABLE} SET state=?,revision=revision+1,"  # nosec B608
    "updated_at=? WHERE id=? AND revision=?"
)
_ITEM_BYTES = (
    f"SELECT b.sha256 FROM {ITEMS_TABLE} i "  # nosec B608
    f"JOIN {BYTES_TABLE} b ON b.id=i.byte_id WHERE i.id=?"
)


class ReviewCommandError(LibraryCommandError):
    """A decision lost a race or would rewrite an association.

    ``code`` is ``review-conflict`` (the item was decided or changed since it
    was read), ``association-conflict`` (the choice is not compatible with
    the file's saved links, or those links changed), ``file-changed`` (the
    item is not about these bytes) or ``review-missing``. ``item`` is the
    current item read inside the transaction, ``total`` the pending count and
    ``fingerprint`` the current dependency fingerprint, when known;
    ``changed`` marks an association conflict caused by links that changed
    since the reviewer read them (rather than an incompatible choice).
    """

    def __init__(
        self, code, epoch, *, item=None, total=None, fingerprint=None, changed=False
    ):
        super().__init__(code, epoch)
        self.item = item
        self.total = total
        self.fingerprint = fingerprint
        self.changed = changed
        # The public view of ``item``, set by the API when it formats one.
        self.view = None


class _Rollback(Exception):
    """Raised inside the transaction so the context manager rolls back."""

    def __init__(self, error: ReviewCommandError):
        super().__init__(error.code)
        self.error = error


class ReviewDecisionOperations:
    """PromptDatabase mixin: record one decision on one Needs review item."""

    def decide_review_item(
        self,
        *,
        item_id: str,
        expected_epoch: str,
        expected_revision: int,
        sha256: str,
        outcome: str,
        candidate: dict | None = None,
        image_path: str | None = None,
        metadata: dict | None = None,
        expected_fingerprint: str | None = None,
        aliases: tuple = (),
        known_rows: frozenset | None = None,
    ) -> dict:
        """Record the decision; return ``{item, total, pair}``.

        ``selected`` needs the evidence ``candidate`` (with its ``hash``), the
        ``image_path`` the scan and the monitor link under, the captured
        ``metadata`` and the ``expected_fingerprint`` the reviewer saw.
        ``aliases`` are the other spellings legacy rows of the same physical
        file use (a symlinked, junctioned or mapped root) and ``known_rows``
        the ids of every same-name row the caller classified; a same-name row
        it did not see is a changed association.
        Raises ``LibraryCommandError("storage-changed")`` after a restore and
        :class:`ReviewCommandError` for every refused decision, with zero
        writes either way.
        """
        if outcome not in ("selected", "unresolved"):
            raise ValueError("outcome must be selected or unresolved")
        if outcome == "selected" and (
            not isinstance(candidate, dict) or not image_path
        ):
            raise ValueError("A selected decision needs a candidate and a file")
        try:
            with self._pair_transaction() as conn:
                return self._decide_in(
                    conn,
                    item_id=item_id,
                    expected_epoch=expected_epoch,
                    expected_revision=expected_revision,
                    sha256=sha256,
                    outcome=outcome,
                    candidate=candidate,
                    image_path=image_path,
                    metadata=metadata or {},
                    expected_fingerprint=expected_fingerprint,
                    aliases=tuple(aliases),
                    known_rows=known_rows,
                )
        except _Rollback as rollback:
            raise rollback.error from None

    def _decide_in(self, conn: sqlite3.Connection, **command) -> dict:
        epoch = self._library_epoch(conn, command["expected_epoch"])
        item_id = command["item_id"]
        current = _current_item(conn, item_id)
        if current is None:
            raise _Rollback(ReviewCommandError("review-missing", epoch))
        total = conn.execute(_PENDING_COUNT).fetchone()[0]
        if (
            current["state"] != "pending"
            or current["revision"] != command["expected_revision"]
        ):
            raise _Rollback(
                ReviewCommandError("review-conflict", epoch, item=current, total=total)
            )
        if conn.execute(_ITEM_BYTES, (item_id,)).fetchone()[0] != command["sha256"]:
            raise _Rollback(ReviewCommandError("file-changed", epoch))
        decision_id = _new_id()
        pair = None
        candidate = command["candidate"] if command["outcome"] == "selected" else None
        if candidate is not None:
            pair = self._apply_candidate(
                conn, epoch, current, decision_id, total, **command
            )
        evidence = {
            "kind": DECISION_KIND,
            "decision_id": decision_id,
            "outcome": command["outcome"],
            "reviewed_digest": command["sha256"],
            "item_evidence_hash": current["evidence_hash"],
            "resolver_version": current["resolver_version"],
            "candidate": candidate,
            "dependency_fingerprint": command["expected_fingerprint"],
            "pair": pair,
        }
        now = utc_now_iso()
        self._insert_review_decision(
            conn,
            (
                decision_id,
                item_id,
                current["revision"] + 1,
                now,
                command["outcome"],
                candidate["hash"] if candidate is not None else None,
                canonical_evidence(evidence),
                pair["id"] if pair else None,
                pair["image_id"] if pair else None,
            ),
        )
        conn.execute(
            _ADVANCE_ITEM, (command["outcome"], now, item_id, current["revision"])
        )
        conn.execute(_BUMP_REVIEW_INDEX)
        return {
            "item": _current_item(conn, item_id),
            "total": conn.execute(_PENDING_COUNT).fetchone()[0],
            "pair": pair,
            "decision_id": decision_id,
        }

    def _apply_candidate(
        self, conn, epoch, current, decision_id, total, **command
    ) -> dict:
        """Link the candidate's pair, compatible-only; raise on any conflict."""
        # Imported here: pair_ingestion imports operations, which composes this.
        from .pair_ingestion import _ingest_resolved_pair_in

        image_path = command["image_path"]
        aliases = command["aliases"]
        fingerprint = dependency_fingerprint(conn, image_path, aliases)
        known = command["known_rows"]
        unseen = known is not None and any(
            row_id not in known
            for row_id, _path in same_name_rows(conn, os.path.basename(image_path))
        )
        if unseen or fingerprint != command["expected_fingerprint"]:
            raise _Rollback(
                ReviewCommandError(
                    "association-conflict",
                    epoch,
                    item=current,
                    total=total,
                    fingerprint=fingerprint,
                    changed=True,
                )
            )
        rows = dependent_images(conn, (image_path, *aliases))
        if len(rows) > 1:
            # One file linked under several spellings: no single row to enrich.
            raise _Rollback(
                ReviewCommandError(
                    "association-conflict",
                    epoch,
                    item=current,
                    total=total,
                    fingerprint=fingerprint,
                )
            )
        if rows:
            # Enrich the one row of this file under the spelling it was
            # linked with, so the ingester's compatibility rules see it.
            image_path = rows[0][1]
        candidate = command["candidate"]
        positive, negative = candidate["positive"], candidate["negative"]
        provenance = {
            "kind": DECISION_KIND,
            "decision_id": decision_id,
            "reviewed_digest": command["sha256"],
            "candidate_hash": candidate["hash"],
            "resolver_version": current["resolver_version"],
            "positive": positive,
            "negative": negative,
        }
        result = _ingest_resolved_pair_in(
            conn,
            self,
            image_path,
            command["metadata"],
            {"state": "resolved", "positive": positive, "negative": negative},
            provenance=provenance,
        )
        if result["status"] == "conflict":
            raise _Rollback(
                ReviewCommandError(
                    "association-conflict",
                    epoch,
                    item=current,
                    total=total,
                    fingerprint=fingerprint,
                )
            )
        return {
            "id": result["pair_id"],
            "image_id": result["image_id"],
            "status": result["status"],
        }

    @staticmethod
    def _insert_review_decision(conn: sqlite3.Connection, values: tuple) -> None:
        conn.execute(_INSERT_DECISION, values)


def _current_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    row = conn.execute(_DETAIL, (item_id,)).fetchone()
    if row is None:
        return None
    item = _queue_row(row, _DETAIL_COLUMNS)
    item.pop("evidence")
    return item
