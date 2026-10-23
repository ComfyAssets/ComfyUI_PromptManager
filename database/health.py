"""The read-only health check behind System › Overview and Maintenance.

``run_health_checks`` opens its own ``mode=ro`` connection, reads one
snapshot and closes the connection in ``finally``; it never writes. Each
check reports ``passed``, ``issues`` (with an exact ``count``),
``unavailable`` (it could not be checked here, so ``count`` is None) or
``failed`` (it raised). At most ``MAX_DETAIL`` detail strings are returned per
check, and none of them carries a server path.
"""

import datetime
import logging
import os
import sqlite3
from pathlib import Path

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


def _integrity(conn, _roots):
    rows = [
        row[0] for row in conn.execute(f"PRAGMA integrity_check({INTEGRITY_LIMIT})")
    ]
    if rows == ["ok"]:
        return _result(0, [])
    return _result(len(rows), rows)


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


def _root_prefixes(roots):
    return [normalize_image_path(root).rstrip("\\/") + os.sep for root in roots]


def _under(path, prefixes):
    return any(path.startswith(prefix) for prefix in prefixes)


def _missing_files(conn, roots):
    """Links whose file is gone, counted only under readable sources.

    A source that is not readable (an unmounted drive) makes the whole check
    unavailable rather than turning its links into a count; links outside
    every source are named in the detail, not counted.
    """
    if not roots:
        return _result(None, ["No image source is available."], status="unavailable")
    unavailable = [root for root in roots if not _readable(root)]
    if unavailable:
        noun = "source is" if len(unavailable) == 1 else "sources are"
        return _result(
            None,
            [
                f"{len(unavailable)} image {noun} unavailable, "
                "so missing files were not counted."
            ],
            status="unavailable",
        )
    prefixes = _root_prefixes(roots)
    count, outside, detail = 0, 0, []
    cursor = conn.execute(
        "SELECT id,image_path,filename,file_path FROM generated_images ORDER BY id"
    )
    while rows := cursor.fetchmany(BATCH_ROWS):
        for image_id, image_path, filename, file_path in rows:
            spelled = file_path or normalize_image_path(image_path)
            if not _under(spelled, prefixes) and not _under(
                normalize_image_path(os.path.realpath(image_path)), prefixes
            ):
                outside += 1
            elif not os.path.exists(image_path):
                count += 1
                if len(detail) < MAX_DETAIL:
                    name = filename or os.path.basename(image_path)
                    detail.append(f"Image {image_id}: {name} is missing")
    result = _result(count, detail)
    if outside and len(result["detail"]) < MAX_DETAIL:
        links = "link" if outside == 1 else "links"
        verb = "was" if outside == 1 else "were"
        result["detail"].append(
            f"{outside} {links} outside the image sources {verb} not checked."
        )
    return result


def _ambiguous(review_count):
    if review_count is None:
        return _result(None, [REVIEW_UNAVAILABLE], status="unavailable")
    return _result(review_count, [])


def overall_status(checks) -> str:
    """``passed`` only when every check passed; any check not run is incomplete."""
    statuses = {check["status"] for check in checks}
    if statuses & {"unavailable", "failed"}:
        return "incomplete"
    return "issues" if "issues" in statuses else "passed"


def _failed():
    return _result(None, [CHECK_FAILED], status="failed")


def _run_database_checks(db_path, roots):
    """Each database check's result (None when it raised), from one snapshot."""
    results = dict.fromkeys(_DATABASE_CHECKS)
    try:
        conn = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        logger.exception("Health check could not open the database")
        return results
    try:
        conn.execute("BEGIN")  # one read snapshot for every check
        for check_id in _DATABASE_CHECKS:
            runner = globals()["_" + check_id]  # looked up per run (tests patch it)
            try:
                results[check_id] = runner(conn, roots)
            except (sqlite3.Error, OSError):
                logger.exception(f"Health check {check_id} failed")
    except sqlite3.Error:
        logger.exception("Health check could not read the database")
    finally:
        conn.close()
    return results


class HealthOperations:
    """Mixin for PromptDatabase: the read-only System health check."""

    def run_health_checks(self, roots, *, review_count=None) -> dict:
        """Run every check and return a ``HealthReport``.

        Args:
            roots: The image source directories to judge missing files under:
                the effective sources plus any configured source that is not
                available (which makes ``missing_files`` unavailable).
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
