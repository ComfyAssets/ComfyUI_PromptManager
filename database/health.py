"""The read-only health check behind System › Overview and Maintenance.

``run_health_checks`` opens its own ``mode=ro`` connection, reads one
snapshot and closes the connection in ``finally``; it never writes. Each
check reports ``passed``, ``issues`` (with an exact ``count``; the
integrity check stops at ``INTEGRITY_LIMIT`` problems and then says
``capped``),
``unavailable`` (it could not be checked here, so ``count`` is None, or
``missing_files`` checked only some sources and found nothing) or ``failed``
(it raised; the other checks still run). ``missing_files`` also reports each
source and whether the check was ``partial``; it reads the image rows inside
the snapshot and checks their files after the snapshot ended, so a slow source
never holds the WAL. At most ``MAX_DETAIL`` detail strings are returned per
check, and none of them carries a server path.
"""

import datetime
import logging
import ntpath
import os
import sqlite3
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote

try:
    from ..utils.hashing import generate_prompt_hash
    from .models import normalize_image_path
except ImportError:
    from utils.hashing import generate_prompt_hash
    from database.models import normalize_image_path

logger = logging.getLogger("prompt_manager.database.health")

MAX_DETAIL = 10  # detail strings returned per check
INTEGRITY_LIMIT = 100  # PRAGMA integrity_check(N): problems SQLite reports
BATCH_ROWS = 100  # image rows read per fetch
REVIEW_UNAVAILABLE = "Needs review is not available yet."
CHECK_FAILED = "The check could not finish."
# Check ids and the labels of the concept's health report, in its order;
# pair uniqueness and consistency are the added rows (D63, now D95).
CHECKS = (
    ("integrity", "Integrity check"),
    ("foreign_keys", "Foreign keys & pair references"),
    ("canonical_hashes", "Canonical hashes"),
    ("pair_uniqueness", "Pair uniqueness"),
    ("missing_files", "Missing files"),
    ("ambiguous_metadata", "Ambiguous metadata"),
    ("consistency", "Consistency"),
)
_DATABASE_CHECKS = (
    "integrity",
    "foreign_keys",
    "canonical_hashes",
    "pair_uniqueness",
    "missing_files",
    "consistency",
)


def iter_consistency_issues(conn):
    """The legacy consistency issues: tag and image rows of a missing prompt."""
    for ref in conn.execute(
        "SELECT pt.prompt_id FROM prompt_tags pt "
        "LEFT JOIN prompts p ON pt.prompt_id = p.id WHERE p.id IS NULL"
    ):
        yield f"prompt_tags entry references non-existent prompt {ref[0]}"
    for ref in conn.execute(
        "SELECT gi.id, gi.prompt_id FROM generated_images gi "
        "LEFT JOIN prompts p ON gi.prompt_id = p.id WHERE p.id IS NULL"
    ):
        yield f"Image {ref[0]} references non-existent prompt {ref[1]}"


def _result(count, detail, *, status=None):
    return {
        "status": status or ("issues" if count else "passed"),
        "count": count,
        "detail": detail[:MAX_DETAIL],
    }


def _counted(lines):
    """(count, first MAX_DETAIL lines) of an iterable, consumed once."""
    count, detail = 0, []
    for line in lines:
        count += 1
        if len(detail) < MAX_DETAIL:
            detail.append(line)
    return _result(count, detail)


def readonly_uri(db_path, *, windows=None):
    """The ``mode=ro`` SQLite URI of *db_path*, on this platform or the given one.

    pathlib spells a UNC path ``\\\\server\\share\\x.db`` as
    ``file://server/share/x.db``, a remote authority SQLite refuses to open.
    With an empty authority (``file:////server/share/x.db``) SQLite hands
    Windows the ``//server/share`` path, which it opens as the UNC path.
    """
    if windows is None:
        windows = os.name == "nt"
        db_path = os.path.abspath(db_path)
    if not windows:
        return PurePosixPath(db_path).as_uri() + "?mode=ro"
    path = PureWindowsPath(db_path)
    if ntpath.splitdrive(str(path))[0].startswith("\\\\"):
        return "file:" + quote("//" + path.as_posix()) + "?mode=ro"
    return path.as_uri() + "?mode=ro"


def _integrity(conn, _roots):
    rows = [
        row[0] for row in conn.execute(f"PRAGMA integrity_check({INTEGRITY_LIMIT})")
    ]
    if rows == ["ok"]:
        return {**_result(0, []), "capped": False}
    # SQLite stops listing at the limit: there may be more problems than that.
    return {**_result(len(rows), rows), "capped": len(rows) >= INTEGRITY_LIMIT}


def _foreign_keys(conn, _roots):
    return _counted(
        f"{table} row {rowid} references a missing {parent} row"
        for table, rowid, parent, _fkid in conn.execute("PRAGMA foreign_key_check")
    )


def _prompt_hash(text):
    return generate_prompt_hash(text) if isinstance(text, str) else None


def _canonical_hashes(conn, _roots):
    conn.create_function("pm_prompt_hash", 1, _prompt_hash, deterministic=True)
    return _counted(
        f"Prompt {row[0]}: the stored hash does not match its text"
        for row in conn.execute(
            "SELECT id FROM prompts WHERE hash IS NOT pm_prompt_hash(text) ORDER BY id"
        )
    )


def _pair_uniqueness(conn, _roots):
    groups = conn.execute(
        "SELECT positive_prompt_id,negative_prompt_id,COUNT(*) FROM prompt_pairs "
        "GROUP BY positive_prompt_id,negative_prompt_id HAVING COUNT(*)>1 "
        "ORDER BY positive_prompt_id,negative_prompt_id"
    ).fetchall()
    detail = [
        f"Positive prompt {positive} with "
        + ("no negative" if negative is None else f"negative {negative}")
        + f": {rows} pair rows"
        for positive, negative, rows in groups
    ]
    # The surplus rows: what keeping one row per pair would leave out.
    return _result(sum(rows - 1 for _p, _n, rows in groups), detail)


def _consistency(conn, _roots):
    return _counted(iter_consistency_issues(conn))


def _readable(root):
    return os.path.isdir(root) and os.access(root, os.R_OK | os.X_OK)


def _root_prefix(root):
    return normalize_image_path(root).rstrip("\\/") + os.sep


def _source_name(root, index):
    """A source's folder name for the report (never its server path)."""
    return os.path.basename(str(root).rstrip("\\/")) or f"Source {index + 1}"


def _owner(path, prefixes):
    """Index of the longest source prefix *path* lies under, or None."""
    owner, longest = None, -1
    for index, prefix in enumerate(prefixes):
        if path.startswith(prefix) and len(prefix) > longest:
            owner, longest = index, len(prefix)
    return owner


def _image_owner(image_path, file_path, prefixes):
    spelled = file_path or normalize_image_path(image_path)
    owner = _owner(spelled, prefixes)
    if owner is None:
        owner = _owner(normalize_image_path(os.path.realpath(image_path)), prefixes)
    return owner


def readable_roots(roots):
    """Whether each source can be read now (an unmounted drive cannot)."""
    return [_readable(root) for root in roots]


def link_states(rows, roots, readable):
    """``(row, owner, state)`` for each image row ``(id, image_path, filename,
    file_path)``: ``owner`` is the index of the source it lies under (None
    outside every source) and ``state`` is ``missing``, ``present``,
    ``unavailable`` (its source cannot be read, so the file is not looked at)
    or ``outside``. ``readable`` is ``readable_roots(roots)``. The health
    check's ``missing_files`` and the maintenance missing-link plan both
    judge links this way, so their counts agree.
    """
    prefixes = [_root_prefix(root) for root in roots]
    for row in rows:
        owner = _image_owner(row[1], row[3], prefixes)
        if owner is None:
            yield row, None, "outside"
        elif not readable[owner]:
            yield row, owner, "unavailable"
        elif os.path.exists(row[1]):
            yield row, owner, "present"
        else:
            yield row, owner, "missing"


def _unavailable_line(unavailable, *, partial):
    noun = "source is" if unavailable == 1 else "sources are"
    effect = "its files were" if unavailable == 1 else "their files were"
    if not partial:
        effect = "missing files were"
    return f"{unavailable} image {noun} unavailable, so {effect} not counted."


def _missing_files(conn, roots):
    """Links whose file is gone, counted per source (only readable ones).

    Each source reports ``checked`` with its own count, or ``unavailable``
    (an unmounted drive) with no count: its links are never turned into a
    count, while the readable sources still are. The check's ``count`` is the
    readable sources' total (None when none is readable) and ``partial`` says
    some sources were not checked. Links outside every source are named in the
    detail, not counted.

    Inside the snapshot this only reads the image rows; it returns the step
    that checks their files, which runs after the snapshot ended.
    """
    if not roots:
        result = _result(None, ["No image source is available."], status="unavailable")
        return {**result, "partial": False, "sources": []}
    rows = []
    cursor = conn.execute(
        "SELECT id,image_path,filename,file_path FROM generated_images ORDER BY id"
    )
    while batch := cursor.fetchmany(BATCH_ROWS):
        rows.extend(batch)
    return lambda: _check_files(rows, roots)


def _check_files(rows, roots):
    """The ``missing_files`` result of the image *rows*, from the file system."""
    readable = readable_roots(roots)
    sources = [
        {
            "name": _source_name(root, index),
            "status": "checked" if ok else "unavailable",
            "count": 0 if ok else None,
        }
        for index, (root, ok) in enumerate(zip(roots, readable))
    ]
    unavailable = readable.count(False)
    if not any(readable):
        result = _result(
            None, [_unavailable_line(unavailable, partial=False)], status="unavailable"
        )
        return {**result, "partial": False, "sources": sources}
    outside, missing = 0, []
    for row, owner, state in link_states(rows, roots, readable):
        if state == "outside":
            outside += 1
        elif state == "missing":
            image_id, image_path, filename, _file_path = row
            sources[owner]["count"] += 1
            if len(missing) < MAX_DETAIL:
                name = filename or os.path.basename(image_path)
                missing.append(f"Image {image_id}: {name} is missing")
    count = sum(source["count"] or 0 for source in sources)
    detail = [_unavailable_line(unavailable, partial=True)] if unavailable else []
    detail += missing
    if outside:
        links = "link" if outside == 1 else "links"
        verb = "was" if outside == 1 else "were"
        detail.append(
            f"{outside} {links} outside the image sources {verb} not checked."
        )
    status = None if count or not unavailable else "unavailable"
    result = _result(count, detail, status=status)
    return {**result, "partial": bool(unavailable), "sources": sources}


def _ambiguous(review_count):
    if review_count is None:
        return _result(None, [REVIEW_UNAVAILABLE], status="unavailable")
    return _result(review_count, [])


def overall_status(checks) -> str:
    """``passed`` only when every check passed; any check not run is incomplete."""
    statuses = {check["status"] for check in checks}
    if statuses & {"unavailable", "failed"} or any(
        check.get("partial") for check in checks
    ):
        return "incomplete"
    return "issues" if "issues" in statuses else "passed"


def _failed():
    return _result(None, [CHECK_FAILED], status="failed")


def _run_check(check_id, step, *args):
    """One check's result, or None (reported as failed) when it raised."""
    try:
        return step(*args)
    except Exception:  # any error fails this check only; the others still run
        logger.exception(f"Health check {check_id} failed")
        return None


def _run_database_checks(db_path, roots):
    """Each database check's result (None when it raised), from one snapshot.

    A check may return a step instead of its result: the step runs after the
    snapshot ended and the connection closed (``missing_files``' file checks).
    """
    results = dict.fromkeys(_DATABASE_CHECKS)
    try:
        conn = sqlite3.connect(readonly_uri(db_path), uri=True)
    except sqlite3.Error:
        logger.exception("Health check could not open the database")
        return results
    try:
        conn.execute("BEGIN")  # one read snapshot for every check
        for check_id in _DATABASE_CHECKS:
            runner = globals()["_" + check_id]  # looked up per run (tests patch it)
            results[check_id] = _run_check(check_id, runner, conn, roots)
    except sqlite3.Error:
        logger.exception("Health check could not read the database")
    finally:
        conn.close()
    for check_id, result in results.items():
        if callable(result):
            results[check_id] = _run_check(check_id, result)
    return results


class HealthOperations:
    """Mixin for PromptDatabase: the read-only System health check."""

    def run_health_checks(self, roots, *, review_count=None) -> dict:
        """Run every check and return a ``HealthReport``.

        Args:
            roots: The image source directories to judge missing files under:
                the effective sources plus any configured source that is not
                available (reported as an unavailable source of ``missing_files``).
            review_count: The Needs-review pending count, or None while no
                review queue exists (``ambiguous_metadata`` is then unavailable).
        """
        if review_count is not None and (
            type(review_count) is not int or review_count < 0
        ):
            raise ValueError("review_count must be a non-negative integer or None")
        roots = [str(root) for root in roots]
        database = _run_database_checks(self.model.db_path, roots)
        checks = []
        for check_id, label in CHECKS:
            if check_id == "ambiguous_metadata":
                result = _ambiguous(review_count)
            else:
                result = database[check_id] or _failed()
            checks.append({"id": check_id, "label": label, **result})
        return {
            "success": True,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": overall_status(checks),
            "checks": checks,
        }
