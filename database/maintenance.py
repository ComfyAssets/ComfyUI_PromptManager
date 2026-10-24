"""Reviewed maintenance plans: read-only previews and their exact apply (#249 S6).

Four operations, each the native owner of a legacy ``/maintenance`` one and
built on the same queries, so a preview counts what the legacy cleanup would
change:

- ``duplicate_prompts``: prompts whose text is equal ignoring case and outer
  spaces (``DUPLICATE_PROMPTS_SQL``, legacy ``cleanup_duplicates``); each
  group keeps its oldest prompt, which takes over the others' images and
  metadata. A group with a member in a saved pair is kept whole.
- ``optimize``: VACUUM (legacy ``vacuum``).
- ``unused_components``: prompts nothing references (``ORPHANED_PROMPTS_SQL``,
  legacy ``prune_orphaned_prompts``, with the #239 protections).
- ``missing_links``: image rows whose file is gone (legacy
  ``cleanup_orphaned_images``), counted only under a source that can be read
  now, as the health check's ``missing_files`` judges them
  (``health.link_states``). It deletes database rows only, never a file.

``preview_maintenance`` reads one snapshot on its own ``mode=ro`` connection
(``health.readonly_uri``) and closes it in ``finally``; files are looked at
after the snapshot ended. It returns a ``MaintenanceDraft``: the public plan
fields, the targets an apply needs and the fingerprint of exactly what was
reviewed (ids, text hashes, metadata, tags, Library state and pair refs of
every prompt involved, or each link's row and source). The fingerprint never
leaves the server.

``apply_maintenance`` recomputes that fingerprint on the write connection
inside ``BEGIN IMMEDIATE`` and raises ``PlanChanged`` before writing anything
when it differs; otherwise it applies the reviewed targets in batches of
``ID_BATCH`` in that one transaction. VACUUM cannot run in a transaction, so
``optimize`` binds to its database only (the caller checks that).
"""

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .execution_contexts import LIVE_HINT_CTES
from .health import BATCH_ROWS, link_states, readable_roots, readonly_uri
from .pairs import PairDependencyError
from .workflow_documents import documents_of_rows, release_documents

MAINTENANCE_OPERATIONS = (
    "duplicate_prompts",
    "optimize",
    "unused_components",
    "missing_links",
)
MAX_DETAILS = 50  # detail rows a plan lists
MAX_RESULT_ROWS = 200  # changed records an apply describes (the job lists 200)
EXCERPT_CHARS = 80
# Ids bound per statement: the pair-reference read binds each id twice, which
# stays under SQLite's historical 999-variable limit.
ID_BATCH = 400

# Why a prompt no image links to is kept (#239): predicates on ``p``, in the
# order the plan names them. ORPHANED_PROMPTS_SQL is built from them, so the
# reasons a plan lists are exactly the rules the prune applies.
PROTECTIONS = (
    (
        "EXISTS (SELECT 1 FROM prompt_pairs pp "
        "WHERE pp.positive_prompt_id = p.id OR pp.negative_prompt_id = p.id)",
        "in saved pairs",
        "in a saved pair",
    ),
    (
        "EXISTS (SELECT 1 FROM prompt_tags pt JOIN tags t ON pt.tag_id = t.id "
        "WHERE pt.prompt_id = p.id AND t.name = '__protected__')",
        "tagged __protected__",
        "tagged __protected__",
    ),
    (
        "EXISTS (SELECT 1 FROM prompt_library_state ls WHERE ls.prompt_id = p.id)",
        "with Library state (a title or an explicit parent)",
        "with Library state (a title or an explicit parent)",
    ),
    (
        "p.id IN (SELECT prompt_id FROM live_hint_prompts)",
        "used by recorded runs",
        "used by a recorded run",
    ),
)

# Exempt references: images, pairs, the __protected__ tag, Library parent
# state, and live recorded-run hints (computed once, see LIVE_HINT_CTES).
ORPHANED_PROMPTS_SQL = (
    "WITH "
    + LIVE_HINT_CTES
    + " SELECT p.id FROM prompts p"
    + " LEFT JOIN generated_images gi ON p.id = gi.prompt_id"
    + " WHERE gi.prompt_id IS NULL"
    + "".join(f" AND NOT ({predicate})" for predicate, _many, _one in PROTECTIONS)
)
# The prompts no image links to, with which protections keep each one.
_UNLINKED_PROTECTIONS_SQL = (
    "WITH "
    + LIVE_HINT_CTES
    + " SELECT "
    + ", ".join(
        f"COALESCE(SUM({predicate}), 0)" for predicate, _many, _one in PROTECTIONS
    )
    + ", COALESCE(SUM("
    + " OR ".join(f"({predicate})" for predicate, _many, _one in PROTECTIONS)
    + "), 0) FROM prompts p WHERE NOT EXISTS "
    "(SELECT 1 FROM generated_images gi WHERE gi.prompt_id = p.id)"
)

# Duplicate prompt groups, as the legacy cleanup finds them (case-insensitive
# text; GROUP_CONCAT order is sorted by created_at afterwards).
DUPLICATE_PROMPTS_SQL = """
    SELECT LOWER(TRIM(text)) as normalized_text, COUNT(*) as count,
           GROUP_CONCAT(id) as ids,
           GROUP_CONCAT(created_at) as created_dates
    FROM prompts
    GROUP BY LOWER(TRIM(text))
    HAVING COUNT(*) > 1
"""

_LINK_ROWS_SQL = (
    "SELECT id, image_path, filename, file_path FROM generated_images ORDER BY id"
)

# What a prompt's fingerprint covers, per batch of ids.
_PROMPT_STATE_SQL = (
    "SELECT id, hash, text, category, rating, notes, created_at, updated_at "
    "FROM prompts WHERE id IN ({marks}) ORDER BY id",
    "SELECT prompt_id, tag_id FROM prompt_tags WHERE prompt_id IN ({marks}) "
    "ORDER BY prompt_id, tag_id",
    "SELECT * FROM prompt_library_state WHERE prompt_id IN ({marks}) "
    "ORDER BY prompt_id",
    "SELECT id, positive_prompt_id, negative_prompt_id FROM prompt_pairs "
    "WHERE positive_prompt_id IN ({marks}) OR negative_prompt_id IN ({marks}) "
    "ORDER BY id",
)

NO_FILES_DELETED = "No image file is deleted."
LINKS_ONLY = "Database links only; no image file is deleted."
UNUSED_DETAIL = "No image, saved pair, Library state or recorded run uses it"


# Why a duplicate group is kept whole (``_duplicate_conflict``).
CONFLICT_REASONS = {
    "collision": ("group links one image twice", "groups link one image twice"),
    "title": (
        "group has its own Library title",
        "groups have their own Library titles",
    ),
}
CONFLICT_DETAILS = {
    "collision": "Kept: two copies link the same image",
    "title": "Kept: a copy has its own Library title",
}
OPTIMIZE_LOCK_NOTE = (
    "Runs alone: the database is locked while it runs, and on a large library "
    "this takes a while. Prompt runs counted and images captured meanwhile "
    "wait up to 30 seconds each; anything that waits longer is not recorded."
)


class PlanChanged(Exception):
    """What a plan was reviewed on changed before it was applied."""


class MergeConflict(Exception):
    """A strict duplicate merge would drop an image link or a Library title."""


@dataclass
class MaintenanceDraft:
    """A preview: the public plan fields plus what only the server keeps."""

    operation: str
    fingerprint: str
    targets: list
    scope: dict
    protected: dict
    media: dict
    details: list = field(default_factory=list)

    def public(self):
        """The plan fields a response may carry (no fingerprint, no targets)."""
        return {
            "operation": self.operation,
            "scope": dict(self.scope),
            "protected": {
                "count": self.protected["count"],
                "reasons": list(self.protected["reasons"]),
            },
            "media": dict(self.media),
            "details": [dict(row) for row in self.details],
        }


def _plural(count, one, many=None):
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _excerpt(text):
    text = " ".join(str(text or "").split())
    if len(text) <= EXCERPT_CHARS:
        return text
    return text[: EXCERPT_CHARS - 1].rstrip() + "…"


def _megabytes(size):
    return f"{size / 2**20:,.1f} MB"


class _Fingerprint:
    """A streaming sha256 over JSON-encoded parts."""

    def __init__(self, operation):
        self._hash = hashlib.sha256()
        self.add(operation)

    def add(self, part):
        encoded = json.dumps(part, separators=(",", ":"), default=str)
        self._hash.update(encoded.encode("utf-8") + b"\n")

    def hexdigest(self):
        return "sha256:" + self._hash.hexdigest()


def _batches(ids):
    ids = list(ids)
    for start in range(0, len(ids), ID_BATCH):
        yield ids[start : start + ID_BATCH]


def _marks(batch):
    return ",".join("?" for _ in batch)


def _add_prompt_state(fingerprint, conn, ids):
    """Fold every reviewed fact about the prompts ``ids`` into ``fingerprint``."""
    for batch in _batches(sorted(ids)):
        marks = _marks(batch)
        for sql in _PROMPT_STATE_SQL:
            params = batch * sql.count("{marks}")
            for row in conn.execute(sql.format(marks=marks), params):
                fingerprint.add(list(row))


def _texts(conn, ids, limit=MAX_DETAILS):
    """``{id: text}`` of the first ``limit`` prompts, for detail rows."""
    ids = list(ids)[:limit]
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT id, text FROM prompts WHERE id IN ({_marks(ids)})", ids
    )
    return {row[0]: row[1] for row in rows}


def _orphaned_ids(conn):
    return sorted(row[0] for row in conn.execute(ORPHANED_PROMPTS_SQL))


def _delete_ids(conn, table, ids):
    """Delete ``table`` rows by id in batches; returns the rows removed.

    The workflow documents only those rows referenced go with them (a
    prompt's images go by cascade), in the same transaction.
    """
    if table not in ("prompts", "generated_images"):
        raise ValueError(f"Unsupported table {table!r}")
    removed = 0
    for batch in _batches(ids):
        documents = documents_of_rows(
            conn, "id" if table == "generated_images" else "prompt_id", batch
        )
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE id IN ({_marks(batch)})",  # nosec B608
            batch,
        )
        removed += cursor.rowcount
        release_documents(conn, documents)
    return removed


def _read_snapshot(db_path, read):
    """``read(conn)`` on one ``mode=ro`` snapshot; the connection always closes."""
    conn = sqlite3.connect(readonly_uri(db_path), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")  # one read snapshot for the whole plan
        return read(conn)
    finally:
        conn.close()


def _relative(image_path, root):
    try:
        return Path(os.path.relpath(image_path, root)).as_posix()
    except ValueError:  # another drive (Windows)
        return os.path.basename(image_path)


def _source_name(root, index):
    return os.path.basename(str(root).rstrip("\\/")) or f"Source {index + 1}"


class MaintenanceOperations:
    """Mixin for PromptDatabase: maintenance previews and their apply."""

    # ── shared with the legacy cleanup ───────────────────────────────

    def _duplicate_groups(self, conn):
        """``(groups, paired)``: duplicate id groups, oldest first.

        ``paired`` are the groups with a member in a saved pair, which the
        cleanup keeps whole.
        """
        groups, paired = [], []
        for duplicate in conn.execute(DUPLICATE_PROMPTS_SQL).fetchall():
            ids = duplicate["ids"].split(",")
            created_dates = duplicate["created_dates"].split(",")
            # Sort IDs by created_at date to keep the oldest
            id_date_pairs = list(zip(ids, created_dates))
            id_date_pairs.sort(key=lambda x: x[1])
            sorted_ids = [int(pair[0]) for pair in id_date_pairs]
            try:
                self._ensure_prompts_unpaired(conn, sorted_ids)
            except PairDependencyError:
                paired.append(sorted_ids)
                continue
            groups.append(sorted_ids)
        return groups, paired

    @staticmethod
    def _duplicate_conflict(conn, sorted_ids):
        """Why merging the group would lose something, or None.

        ``collision``: two members link the same file, so moving the images
        to the oldest prompt would hit ``UNIQUE(prompt_id, file_path)``.
        ``title``: a member that would be deleted has its own Library title.
        """
        marks = _marks(sorted_ids)
        collision = conn.execute(
            "SELECT 1 FROM generated_images WHERE prompt_id IN "  # nosec B608
            f"({marks}) AND file_path IS NOT NULL GROUP BY file_path "
            "HAVING COUNT(DISTINCT prompt_id) > 1 LIMIT 1",
            sorted_ids,
        ).fetchone()
        if collision:
            return "collision"
        duplicates = sorted_ids[1:]
        titled = conn.execute(
            "SELECT 1 FROM prompt_library_state WHERE prompt_id IN "  # nosec B608
            f"({_marks(duplicates)}) AND trim(coalesce(title, '')) != '' LIMIT 1",
            duplicates,
        ).fetchone()
        return "title" if titled else None

    def _merge_duplicate_group(self, conn, sorted_ids, strict=False):
        """Keep the oldest prompt of a group; returns (removed, images moved).

        ``strict`` (the reviewed apply): a merge that would drop an image
        link or a Library title raises MergeConflict, and every helper error
        propagates, so the transaction rolls back. The legacy cleanup keeps
        its old, lenient behaviour.
        """
        primary_id, duplicate_ids = sorted_ids[0], sorted_ids[1:]
        if strict:
            conflict = self._duplicate_conflict(conn, sorted_ids)
            if conflict:
                raise MergeConflict(conflict)
        self.logger.debug(
            f"Merging duplicates: keeping {primary_id}, removing {duplicate_ids}"
        )
        primary = conn.execute(
            "SELECT * FROM prompts WHERE id = ?", (primary_id,)
        ).fetchone()
        if not primary:
            return 0, 0
        merged_metadata = self._merge_duplicate_metadata(
            conn, primary_id, duplicate_ids, strict=strict
        )
        moved = self._transfer_images_to_primary(
            conn, primary_id, duplicate_ids, strict=strict
        )
        if merged_metadata:
            self._update_primary_with_merged_metadata(
                conn, primary_id, merged_metadata, strict=strict
            )
        removed = 0
        # Images the lenient merge could not move go with their prompt.
        documents = documents_of_rows(conn, "prompt_id", duplicate_ids)
        for duplicate_id in duplicate_ids:
            conn.execute("DELETE FROM prompts WHERE id = ?", (duplicate_id,))
            removed += 1
            self.logger.debug(f"Removed duplicate prompt {duplicate_id}")
        release_documents(conn, documents)
        return removed, moved

    @staticmethod
    def _missing_link_ids(rows, roots):
        """Ids of the ``rows`` whose file is gone under a readable source."""
        readable = readable_roots(roots)
        return [
            row[0]
            for row, _owner, state in link_states(rows, roots, readable)
            if state == "missing"
        ]

    # ── previews ──────────────────────────────────────────────────────

    def preview_maintenance(self, operation, roots=()) -> MaintenanceDraft:
        """The reviewed plan of ``operation``; writes nothing (blocking).

        ``roots`` are the sources missing links are judged under (the health
        check's: the effective sources plus configured ones that are gone).
        """
        if operation not in MAINTENANCE_OPERATIONS:
            raise ValueError(f"Unknown maintenance operation {operation!r}")
        roots = [str(root) for root in roots]
        if operation == "optimize":
            return self._plan_optimize()
        if operation == "missing_links":
            rows = _read_snapshot(self.model.db_path, self._link_rows)
            return self._plan_missing_links(rows, roots)
        reader = (
            self._plan_duplicate_prompts
            if operation == "duplicate_prompts"
            else self._plan_unused_components
        )
        return _read_snapshot(self.model.db_path, reader)

    @staticmethod
    def _link_rows(conn):
        rows = []
        cursor = conn.execute(_LINK_ROWS_SQL)
        while batch := cursor.fetchmany(BATCH_ROWS):
            rows.extend(tuple(row) for row in batch)
        return rows

    def _duplicates_fingerprint(self, conn):
        """``(groups, paired, conflicted, fingerprint)`` of the duplicate merge.

        ``conflicted`` holds ``(ids, reason)`` of the groups a merge would
        lose an image link or a Library title in (``_duplicate_conflict``);
        the plan protects them.
        """
        candidates, paired = self._duplicate_groups(conn)
        groups, conflicted = [], []
        for ids in candidates:
            reason = self._duplicate_conflict(conn, ids)
            if reason:
                conflicted.append((ids, reason))
            else:
                groups.append(ids)
        fingerprint = _Fingerprint("duplicate_prompts")
        fingerprint.add({"groups": groups, "paired": paired, "conflicted": conflicted})
        _add_prompt_state(fingerprint, conn, [i for group in groups for i in group])
        return groups, paired, conflicted, fingerprint.hexdigest()

    def _plan_duplicate_prompts(self, conn):
        groups, paired, conflicted, fingerprint = self._duplicates_fingerprint(conn)
        removed = sum(len(group) - 1 for group in groups)
        held = [ids for ids, _reason in conflicted]
        texts = _texts(conn, [group[0] for group in (groups + paired + held)])
        details = (
            [
                {
                    "label": _excerpt(texts.get(group[0])),
                    "detail": f"Keeps the oldest of {len(group)} copies",
                }
                for group in groups
            ]
            + [
                {
                    "label": _excerpt(texts.get(group[0])),
                    "detail": "Kept: belongs to a saved pair",
                }
                for group in paired
            ]
            + [
                {
                    "label": _excerpt(texts.get(ids[0])),
                    "detail": CONFLICT_DETAILS[reason],
                }
                for ids, reason in conflicted
            ]
        )
        if groups:
            label = (
                f"{_plural(removed, 'duplicate prompt')} in "
                f"{_plural(len(groups), 'group')}"
            )
        else:
            label = "No duplicate prompt text"
        reasons = []
        if len(paired) == 1:
            reasons.append("1 group belongs to a saved pair")
        elif paired:
            reasons.append(f"{len(paired):,} groups belong to saved pairs")
        for reason, (one, many) in CONFLICT_REASONS.items():
            count = sum(1 for _ids, why in conflicted if why == reason)
            if count:
                reasons.append(f"{count:,} {one if count == 1 else many}")
        return MaintenanceDraft(
            operation="duplicate_prompts",
            fingerprint=fingerprint,
            targets=groups,
            scope={"count": removed, "label": label},
            protected={
                "count": sum(len(group) for group in paired + held),
                "reasons": reasons,
            },
            media={"deletes_files": False, "label": NO_FILES_DELETED},
            details=details[:MAX_DETAILS],
        )

    def _unused_fingerprint(self, conn):
        ids = _orphaned_ids(conn)
        fingerprint = _Fingerprint("unused_components")
        fingerprint.add(ids)
        _add_prompt_state(fingerprint, conn, ids)
        return ids, fingerprint.hexdigest()

    def _plan_unused_components(self, conn):
        ids, fingerprint = self._unused_fingerprint(conn)
        *by_reason, kept = conn.execute(_UNLINKED_PROTECTIONS_SQL).fetchone()
        reasons = [
            f"{count:,} {many if count != 1 else one}"
            for count, (_predicate, many, one) in zip(by_reason, PROTECTIONS)
            if count
        ]
        texts = _texts(conn, ids)
        count = len(ids)
        return MaintenanceDraft(
            operation="unused_components",
            fingerprint=fingerprint,
            targets=ids,
            scope={
                "count": count,
                "label": (
                    _plural(count, "unused prompt component")
                    if count
                    else "No unused prompt components"
                ),
            },
            protected={"count": kept, "reasons": reasons},
            media={"deletes_files": False, "label": NO_FILES_DELETED},
            details=[
                {"label": _excerpt(texts.get(prompt_id)), "detail": UNUSED_DETAIL}
                for prompt_id in ids[:MAX_DETAILS]
            ],
        )

    @staticmethod
    def _links_fingerprint(candidates):
        fingerprint = _Fingerprint("missing_links")
        for row, owner in candidates:
            fingerprint.add([row[0], row[1], row[3], owner])
        return fingerprint.hexdigest()

    def _plan_missing_links(self, rows, roots):
        readable = readable_roots(roots)
        candidates, unavailable, outside = [], 0, 0
        for row, owner, state in link_states(rows, roots, readable):
            if state == "missing":
                candidates.append((row, owner))
            elif state == "unavailable":
                unavailable += 1
            elif state == "outside":
                outside += 1
        reasons = []
        if unavailable:
            reasons.append(
                _plural(
                    unavailable,
                    "link under an unavailable source",
                    "links under unavailable sources",
                )
            )
        if outside:
            reasons.append(
                _plural(
                    outside,
                    "link outside the image sources",
                    "links outside the image sources",
                )
            )
        count = len(candidates)
        return MaintenanceDraft(
            operation="missing_links",
            fingerprint=self._links_fingerprint(candidates),
            targets=[row[0] for row, _owner in candidates],
            scope={
                "count": count,
                "label": (
                    _plural(count, "link to a missing file", "links to missing files")
                    if count
                    else "No links to missing files"
                ),
            },
            protected={"count": unavailable + outside, "reasons": reasons},
            media={"deletes_files": False, "label": LINKS_ONLY},
            details=[
                {
                    "label": _relative(row[1], roots[owner]),
                    "detail": (
                        f"{_source_name(roots[owner], owner)} · the file is missing"
                    ),
                }
                for row, owner in candidates[:MAX_DETAILS]
            ],
        )

    def _plan_optimize(self):
        def read(conn):
            return [
                conn.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("page_size", "page_count", "freelist_count")
            ]

        page_size, page_count, free_pages = _read_snapshot(self.model.db_path, read)
        db_path = self.model.db_path
        wal = db_path + "-wal"
        size = page_size * page_count
        wal_size = os.path.getsize(wal) if os.path.exists(wal) else 0
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
        # VACUUM writes the rebuilt database through the WAL beside it.
        return MaintenanceDraft(
            operation="optimize",
            fingerprint=_Fingerprint("optimize").hexdigest(),
            targets=[],
            scope={"count": 1, "label": "The whole database"},
            protected={"count": 0, "reasons": []},
            media={"deletes_files": False, "label": NO_FILES_DELETED},
            details=[
                {
                    "label": "Database file",
                    "detail": f"{_megabytes(size)}, plus {_megabytes(wal_size)} "
                    "not yet checkpointed",
                },
                {
                    "label": "Free pages",
                    "detail": f"About {_megabytes(free_pages * page_size)} "
                    "can be reclaimed",
                },
                {
                    "label": "Lock",
                    "detail": OPTIMIZE_LOCK_NOTE,
                },
                {
                    "label": "Free space needed",
                    "detail": f"About {_megabytes(size)} beside the database and "
                    f"{_megabytes(size)} in SQLite's temporary folder; "
                    f"{_megabytes(free)} available beside the database",
                },
            ],
        )

    # ── apply ─────────────────────────────────────────────────────────

    def apply_maintenance(self, operation, fingerprint, targets, roots=()) -> dict:
        """Apply a reviewed plan exactly, or raise PlanChanged (blocking).

        Inside ``BEGIN IMMEDIATE`` the fingerprint is computed again on the
        write connection; when it differs nothing is written. ``targets`` and
        ``roots`` are the plan's. Returns ``{changed, rows}``: how many
        records changed and up to ``MAX_RESULT_ROWS`` descriptions of them
        (``{prompt_id[, kept_id]}`` or ``{image_path}``).
        """
        if operation not in MAINTENANCE_OPERATIONS:
            raise ValueError(f"Unknown maintenance operation {operation!r}")
        if operation == "optimize":
            # VACUUM cannot run in a transaction; the plan binds the database.
            if fingerprint != _Fingerprint("optimize").hexdigest():
                raise PlanChanged("optimize")
            self.model.vacuum_database()
            return {"changed": 1, "rows": []}
        appliers = {
            "duplicate_prompts": self._apply_duplicate_prompts,
            "unused_components": self._apply_unused_components,
            "missing_links": self._apply_missing_links,
        }
        with self._pair_transaction() as conn:
            return appliers[operation](conn, fingerprint, targets, roots)

    def _apply_duplicate_prompts(self, conn, fingerprint, targets, _roots):
        groups, _paired, _conflicted, current = self._duplicates_fingerprint(conn)
        if current != fingerprint or groups != targets:
            raise PlanChanged("duplicate_prompts")
        changed, rows = 0, []
        for sorted_ids in groups:
            removed, _moved = self._merge_duplicate_group(conn, sorted_ids, strict=True)
            changed += removed
            rows.extend(
                {"prompt_id": duplicate_id, "kept_id": sorted_ids[0]}
                for duplicate_id in sorted_ids[1:]
            )
        return {"changed": changed, "rows": rows[:MAX_RESULT_ROWS]}

    def _apply_unused_components(self, conn, fingerprint, targets, _roots):
        ids, current = self._unused_fingerprint(conn)
        if current != fingerprint or ids != targets:
            raise PlanChanged("unused_components")
        texts = _texts(conn, ids, limit=MAX_RESULT_ROWS)
        changed = _delete_ids(conn, "prompts", ids)
        rows = [
            {"prompt_id": prompt_id, "text": _excerpt(texts.get(prompt_id))}
            for prompt_id in ids[:MAX_RESULT_ROWS]
        ]
        return {"changed": changed, "rows": rows}

    def _apply_missing_links(self, conn, fingerprint, targets, roots):
        rows = []
        for batch in _batches(sorted(targets)):
            rows.extend(
                tuple(row)
                for row in conn.execute(
                    "SELECT id, image_path, filename, file_path FROM generated_images "
                    f"WHERE id IN ({_marks(batch)}) ORDER BY id",  # nosec B608
                    batch,
                )
            )
        readable = readable_roots(roots)
        candidates = []
        for row, owner, state in link_states(rows, roots, readable):
            if state != "missing":  # back, or its source went away
                raise PlanChanged("missing_links")
            candidates.append((row, owner))
        if self._links_fingerprint(candidates) != fingerprint:
            raise PlanChanged("missing_links")
        changed = _delete_ids(conn, "generated_images", [row[0] for row in rows])
        return {
            "changed": changed,
            "rows": [{"image_path": row[1]} for row in rows[:MAX_RESULT_ROWS]],
        }
