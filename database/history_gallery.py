"""Bounded exact-path saved display within the caller's history snapshot."""

import os
import sqlite3

try:
    from ..utils.inspection_response import (
        MAX_LIBRARY_BYTES,
        _library_projection,
        library_metadata,
    )
except ImportError:
    from utils.inspection_response import (
        MAX_LIBRARY_BYTES,
        _library_projection,
        library_metadata,
    )


def _display_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", "replace")


def _tags(
    conn: sqlite3.Connection, owner: int, *, pair: bool
) -> tuple[list[str], bool]:
    table, column = (
        ("prompt_pair_tags", "pair_id") if pair else ("prompt_tags", "prompt_id")
    )
    tags = []
    used = 0
    cursor = conn.execute(
        f"SELECT substr(CAST(t.name AS BLOB),1,?) FROM {table} pt "
        f"JOIN tags t ON t.id=pt.tag_id WHERE pt.{column}=? LIMIT 129",
        (MAX_LIBRARY_BYTES + 1, owner),
    )
    for row in cursor:
        used += len(row[0])
        if used > MAX_LIBRARY_BYTES or len(tags) == 128:
            return [], True
        tags.append(_display_bytes(row[0]))
    return tags, False


def _saved_projection(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict, dict]:
    saved = {"prompt_id": row["prompt_id"]}
    pairs = list(
        conn.execute(
            "SELECT pp.id,pp.positive_prompt_id,"
            "substr(CAST(pp.title AS BLOB),1,?) AS title,"
            "substr(CAST(pp.category AS BLOB),1,?) AS category "
            "FROM image_prompt_pairs ip JOIN prompt_pairs pp ON pp.id=ip.pair_id "
            "WHERE ip.image_id=? LIMIT 2",
            (MAX_LIBRARY_BYTES + 1, MAX_LIBRARY_BYTES + 1, row["id"]),
        )
    )
    if len(pairs) > 1:
        return saved, _library_projection({"status": "limited"})
    pair = pairs[0] if pairs else None
    title = pair["title"] if pair is not None else None
    recipe_tags, limited = (
        _tags(conn, pair["id"], pair=True) if pair is not None else ([], False)
    )
    positive_tags = []
    if not recipe_tags and not limited:
        # D39: only read the positive's tags when they are what the panel shows.
        positive_tags, limited = _tags(conn, row["prompt_id"], pair=False)
    metadata = library_metadata(
        row["category"],
        positive_tags,
        (pair["category"], recipe_tags) if pair is not None else None,
    )
    limited = limited or any(
        value is not None and len(value) > MAX_LIBRARY_BYTES
        for value in (title, metadata["category"])
    )
    if pair is not None:
        saved["pair"] = {
            "id": pair["id"],
            "positive_prompt_id": pair["positive_prompt_id"],
        }
    library = _library_projection(
        {
            "status": "linked",
            "positive_id": row["prompt_id"],
            "pair_id": pair["id"] if pair is not None else None,
            "title": _display_bytes(title),
            **metadata,
            "category": _display_bytes(metadata["category"]),
        }
    )
    if limited:
        library = _library_projection({"status": "limited", "tags": []})
    return saved, library


def read_history_gallery(
    conn: sqlite3.Connection, paths: tuple[str, ...]
) -> dict[str, dict]:
    """Read at most two rows/path and two pair associations/unique row; no writes."""
    if not conn.in_transaction:
        raise sqlite3.ProgrammingError("Saved history display requires caller snapshot")
    if (
        type(paths) is not tuple
        or len(paths) > 100
        or any(
            type(path) is not str
            or not os.path.isabs(path)
            or path != os.path.normcase(os.path.normpath(path))
            or "\0" in path
            for path in paths
        )
    ):
        raise ValueError("Invalid history gallery paths")
    result = {}
    for path in dict.fromkeys(paths):
        rows = list(
            conn.execute(
                "SELECT gi.id,gi.prompt_id,"
                "substr(CAST(p.category AS BLOB),1,?) AS category "
                "FROM generated_images gi JOIN prompts p ON p.id=gi.prompt_id "
                "WHERE gi.file_path=? OR gi.image_path=? LIMIT 2",
                (MAX_LIBRARY_BYTES + 1, path, path),
            )
        )
        match = "none" if not rows else "unique" if len(rows) == 1 else "ambiguous"
        saved, library = None, _library_projection(
            {"status": "none" if not rows else "ambiguous"}
        )
        if match == "unique":
            saved, library = _saved_projection(conn, rows[0])
        result[path] = {
            "gallery_match": match,
            "gallery_image_id": rows[0]["id"] if match == "unique" else None,
            "saved": saved,
            "library": library,
        }
    return result
