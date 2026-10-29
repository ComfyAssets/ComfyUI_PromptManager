"""Read-only positive-parent views over existing prompt and pair tables."""

import datetime
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .library_folders import folder_condition, list_library_folders
from .library_tokens import library_edit_token
from .prompt_archive import ARCHIVED, SHELVED

_ELIGIBLE = """(EXISTS(SELECT 1 FROM prompt_pairs ep WHERE ep.positive_prompt_id=p.id)
 OR EXISTS(SELECT 1 FROM generated_images eg WHERE eg.prompt_id=p.id)
 OR NOT EXISTS(SELECT 1 FROM prompt_pairs en WHERE en.negative_prompt_id=p.id)
 OR EXISTS(SELECT 1 FROM prompt_library_state es WHERE es.prompt_id=p.id))"""
_FROM = "prompts p LEFT JOIN prompt_library_state ls ON ls.prompt_id=p.id"
# The characters JavaScript's String.prototype.trim() removes, so a title the
# display treats as blank also falls back to the prompt text for name sorting.
_TRIM_CODES = (9, 10, 11, 12, 13, 32, 160, 5760, *range(8192, 8203))
_TRIM_CODES += (8232, 8233, 8239, 8287, 12288, 65279)
_BLANK = f"char({','.join(map(str, _TRIM_CODES))})"
_JS_TRIM = "".join(map(chr, _TRIM_CODES))
# The Library's display title: a stored title, else the prompt's first line.
DISPLAY_TITLE_LIMIT = 80


def _utf16_units(text: str) -> int:
    """JavaScript ``length``: characters beyond the BMP count as two units."""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def _utf16_prefix(text: str, units: int) -> str:
    """The longest prefix within ``units`` UTF-16 units, never splitting a pair."""
    used = 0
    for index, ch in enumerate(text):
        used += 2 if ord(ch) > 0xFFFF else 1
        if used > units:
            return text[:index]
    return text


def titled(stored_titles: tuple, text: object) -> tuple[str | None, str | None]:
    """Return ``(title, source)``: a stored title, else the text's first line.

    ``source`` is ``"stored"`` for the first non-blank stored title (blank as
    in JavaScript's trim, like the Library view), ``"text"`` for the first line,
    and None when both are empty. The first line is trimmed and, past
    DISPLAY_TITLE_LIMIT UTF-16 units, cut with an ellipsis like the Library's
    ``libraryTitle()`` (never inside a surrogate pair).
    """
    for title in stored_titles:
        if isinstance(title, str) and title.strip(_JS_TRIM):
            return title, "stored"
    if not isinstance(text, str):
        return None, None
    first = text.split("\n")[0].strip(_JS_TRIM)
    if _utf16_units(first) > DISPLAY_TITLE_LIMIT:
        first = _utf16_prefix(first, DISPLAY_TITLE_LIMIT) + "\u2026"
    return (first, "text") if first else (None, None)


def display_title(stored_titles: tuple, text: object) -> str | None:
    """The Library display title of ``titled``, without its source."""
    return titled(stored_titles, text)[0]


def utc_iso(value: object) -> str | None:
    """A stored timestamp as UTC ISO text, or None when absent or unreadable.

    Usage writes are already UTC ISO; a naive value (SQLite's CURRENT_TIMESTAMP
    form) is read as UTC, and an offset is converted to UTC. One bad row must
    not fail a whole Library read, so a value past the datetime range after
    conversion (OverflowError) is unreadable too. Python 3.11+ fromisoformat
    accepts more ISO forms than 3.10 (e.g. a 2-digit fraction); every form the
    app writes (utc_now_iso, the 3.2.4 backfill, CURRENT_TIMESTAMP, Z) parses
    the same on both, so only hand-edited values can differ by version.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return stamp.astimezone(datetime.timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


_NAME = (
    f"LOWER(CASE WHEN TRIM(COALESCE(ls.title,''),{_BLANK})!='' "
    "THEN ls.title ELSE p.text END)"
)
_ORDERS = {
    "newest": ("COALESCE(julianday(p.created_at),0)", "DESC"),
    "oldest": ("COALESCE(julianday(p.created_at),0)", "ASC"),
    "name": (_NAME, "ASC"),
    "rating": ("COALESCE(p.rating,0)", "DESC"),
}
# Pair archive scopes for variant and image reads, as conditions on alias pp.
ARCHIVE_SCOPES = {
    "active": " AND pp.archived=0",
    "archived": " AND pp.archived=1",
    "all": "",
}


# Shelves (#443): the parents on the shelf, or the archived ones.
PARENT_SCOPES = {"active": SHELVED, "archived": ARCHIVED}
# A positive pair owner that is on the shelf, as a condition on alias pp.
SHELVED_PAIR = SHELVED.replace("p.id", "pp.positive_prompt_id")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("limit must be between 1 and 200")


def _archive(value: str) -> str:
    """Return the pair-archive condition for one scope (alias ``pp``)."""
    if not isinstance(value, str) or value not in ARCHIVE_SCOPES:
        raise ValueError("archive must be one of active, archived or all")
    return ARCHIVE_SCOPES[value]


# How the Tags filter matches (#444): every tag (AND), any of them (OR), or
# parents without any tag.
TAG_MODES = ("all", "any", "untagged")
# A parent that no tag's Browse returns (#444 "Untagged prompts"): no tag of its
# own and no active pair carrying one. The vocabulary's untagged count uses it too.
UNTAGGED = (
    "NOT EXISTS(SELECT 1 FROM prompt_tags ut WHERE ut.prompt_id=p.id) "
    "AND NOT EXISTS(SELECT 1 FROM prompt_pairs up JOIN prompt_pair_tags upt "
    "ON upt.pair_id=up.id WHERE up.positive_prompt_id=p.id AND up.archived=0)"
)


def _tag_mode(tag_mode: object, tags: list[str]) -> str:
    if not isinstance(tag_mode, str) or tag_mode not in TAG_MODES:
        raise ValueError("tag_mode must be one of " + ", ".join(TAG_MODES))
    if tag_mode == "untagged" and tags:
        raise ValueError("Untagged prompts cannot also filter by tags")
    return tag_mode


def _tag_conditions(tags: list[str], any_tag: bool) -> tuple[list, list, list]:
    """Parent-arm and pair-arm tag conditions and the tags' parameters."""
    parent_exists = (
        "EXISTS(SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
        "WHERE pt.prompt_id=p.id AND t.name{test})"
    )
    pair_exists = (
        "EXISTS(SELECT 1 FROM prompt_pair_tags pt JOIN tags t ON t.id=pt.tag_id "
        "WHERE pt.pair_id=pp.id AND t.name{test})"
    )
    if any_tag and tags:
        # One condition per arm: the card, or one active pair, carries any tag.
        test = " IN (" + ",".join("?" for _ in tags) + ")"
        return [parent_exists.format(test=test)], [pair_exists.format(test=test)], tags
    return (
        [parent_exists.format(test="=?") for _ in tags],
        [pair_exists.format(test="=?") for _ in tags],
        tags,
    )


def _filter(
    text: str,
    category: str | None,
    tags: list[str],
    tag_mode: str = "all",
    folder_prefixes: list[str] | None = None,
) -> tuple[str, list]:
    if not isinstance(text, str) or len(text) > 500:
        raise ValueError("Search text must be a string of at most 500 characters")
    if category is not None and (not isinstance(category, str) or len(category) > 200):
        raise ValueError("Invalid library category")
    if (
        not isinstance(tags, list)
        or len(tags) > 30
        or any(not isinstance(tag, str) or not tag or len(tag) > 200 for tag in tags)
    ):
        raise ValueError("Invalid library tags")
    mode = _tag_mode(tag_mode, tags)
    untagged = mode == "untagged"
    parent, pair, parent_params, pair_params = [], ["pp.archived=0"], [], []
    if text:
        parent.append("(p.text LIKE ? ESCAPE '\\' OR ls.title LIKE ? ESCAPE '\\')")
        parent_params.extend([f"%{_escape(text)}%"] * 2)
        pair.append(
            "(p.text LIKE ? ESCAPE '\\' OR ls.title LIKE ? ESCAPE '\\' "
            "OR n.text LIKE ? ESCAPE '\\' OR pp.title LIKE ? ESCAPE '\\')"
        )
        pair_params.extend([f"%{_escape(text)}%"] * 4)
    if category is not None:
        parent.append("p.category=?")
        pair.append("pp.category=?")
        parent_params.append(category)
        pair_params.append(category)
    parent_tags, pair_tags, tag_params = _tag_conditions(tags, mode == "any")
    parent.extend(parent_tags)
    pair.extend(pair_tags)
    parent_params.extend(tag_params)
    pair_params.extend(tag_params)
    condition = (
        "("
        + (" AND ".join(parent) or "1=1")
        + (
            ") OR EXISTS(SELECT 1 FROM prompt_pairs pp "
            "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
            "WHERE pp.positive_prompt_id=p.id AND "
        )
        + " AND ".join(pair)
        + ")"
    )
    if untagged:
        # A whole-card condition: inside the parent-or-pair OR an untagged pair
        # would let a tagged parent through.
        condition = f"({condition}) AND {UNTAGGED}"
    params = [*parent_params, *pair_params]
    if folder_prefixes:
        # A whole-card condition around both arms: images link to the parent.
        folder, folder_params = folder_condition(folder_prefixes)
        condition = f"({condition}) AND {folder}"
        params.extend(folder_params)
    return f"({_ELIGIBLE}) AND ({condition})", params


class LibraryOperations:
    """Group reusable negative variants without changing legacy prompt queries.

    Each public read runs inside ``library_snapshot``; the ``_``-prefixed
    helpers take that snapshot's connection and epoch so routes can share it.
    """

    @contextmanager
    def library_snapshot(
        self, *, include_ceiling: bool = False
    ) -> Iterator[tuple[sqlite3.Connection, str, int | None]]:
        """Hold one read transaction, reading the epoch (and ceiling) first."""
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise sqlite3.ProgrammingError("Library reads require their own snapshot")
        conn.execute("BEGIN")
        try:
            row = conn.execute(
                "SELECT storage_epoch,"
                + (
                    "(SELECT COALESCE(MAX(id),0) FROM prompts)"
                    if include_ceiling
                    else "NULL"
                )
                + " FROM execution_history_metadata WHERE singleton_id=1"
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Missing execution history metadata")
            yield conn, row[0], row[1]
        finally:
            if conn.in_transaction:
                conn.rollback()

    def _library_parents(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        where: str,
        params: list,
        suffix: str = "",
        sort_expression: str | None = None,
    ) -> list[dict]:
        rows = conn.execute(
            "SELECT p.id,p.text,ls.title,p.category,p.rating,p.notes,p.created_at,"
            "p.run_count,p.last_used_at,"
            + ARCHIVED
            + " AS archived,"
            + (f"{sort_expression} AS _sort," if sort_expression else "")
            + "(SELECT COUNT(*) FROM prompt_pairs v WHERE v.positive_prompt_id=p.id "
            "AND v.archived=0) AS variant_count,"
            "(SELECT COUNT(*) FROM generated_images g "
            "WHERE g.prompt_id=p.id) AS image_count "
            "FROM " + _FROM + " WHERE " + where + suffix,
            params,
        ).fetchall()
        result = [{**dict(row), "tags": []} for row in rows]
        if result:
            by_id = {row["id"]: row for row in result}
            marks = ",".join("?" for _ in result)
            for row in conn.execute(
                "SELECT pt.prompt_id,t.name FROM prompt_tags pt "
                "JOIN tags t ON t.id=pt.tag_id "
                f"WHERE pt.prompt_id IN ({marks}) ORDER BY t.name",
                list(by_id),
            ):
                by_id[row["prompt_id"]]["tags"].append(row["name"])
        for row in result:
            # Usage is shown, never compared: the token hashes owner fields only.
            row["run_count"] = row["run_count"] if type(row["run_count"]) is int else 0
            row["last_used_at"] = utc_iso(row["last_used_at"])
            row["archived"] = bool(row["archived"])
            row["edit_token"] = library_edit_token(epoch, row)
        return result

    def _list_library(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        ceiling: int,
        *,
        text: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
        tag_mode: str = "all",
        folder_prefixes: list[str] | None = None,
        sort: str = "newest",
        limit: int = 40,
        after: dict | None = None,
        shelf: str = "active",
    ) -> dict:
        _limit(limit)
        if sort not in _ORDERS:
            raise ValueError("Unknown library sort order")
        if not isinstance(shelf, str) or shelf not in PARENT_SCOPES:
            raise ValueError("shelf must be active or archived")
        where, params = _filter(
            text, category, [] if tags is None else tags, tag_mode, folder_prefixes
        )
        if after is not None:
            if (
                not isinstance(after, dict)
                or set(after) != {"ceiling", "value", "id"}
                or type(after["ceiling"]) is not int
                or type(after["id"]) is not int
            ):
                raise ValueError("Invalid library cursor")
            ceiling = after["ceiling"]
        where += " AND p.id<=?"
        params.append(ceiling)
        # One SELECT counts the filtered parents of this scope, their active
        # variants, and the filtered archived parents (Show archived, #443).
        scope = PARENT_SCOPES[shelf]
        total, variant_total, archived_total = conn.execute(
            f"SELECT COALESCE(SUM({scope}),0),"
            f"COALESCE(SUM(CASE WHEN {scope} THEN (SELECT COUNT(*) "
            "FROM prompt_pairs v WHERE v.positive_prompt_id=p.id "
            "AND v.archived=0) ELSE 0 END),0),"
            f"COALESCE(SUM({ARCHIVED}),0) FROM " + _FROM + " WHERE " + where,
            params,
        ).fetchone()
        where += f" AND {scope}"
        expression, direction = _ORDERS[sort]
        if after is not None:
            operator = ">" if direction == "ASC" else "<"
            where += (
                f" AND ({expression}{operator}? OR "
                f"({expression}=? AND p.id{operator}?))"
            )
            params.extend([after["value"], after["value"], after["id"]])
        rows = self._library_parents(
            conn,
            epoch,
            where,
            [*params, limit + 1],
            f" ORDER BY {expression} {direction},p.id {direction} LIMIT ?",
            sort_expression=expression,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_key = None
        if has_more:
            next_key = {
                "ceiling": ceiling,
                "value": rows[-1]["_sort"],
                "id": rows[-1]["id"],
            }
        for row in rows:
            row.pop("_sort")
        return {
            "library_epoch": epoch,
            "items": rows,
            "total": total,
            "variant_total": variant_total,
            "archived_total": archived_total,
            # Echoed so a client can tell this read applied its tag match.
            "tag_mode": tag_mode,
            "has_more": has_more,
            "next_key": next_key,
        }

    def list_library(
        self,
        *,
        text: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
        tag_mode: str = "all",
        folder_prefixes: list[str] | None = None,
        sort: str = "newest",
        limit: int = 40,
        after: dict | None = None,
        shelf: str = "active",
    ) -> dict:
        with self.library_snapshot(include_ceiling=True) as (conn, epoch, ceiling):
            return self._list_library(
                conn,
                epoch,
                ceiling,
                text=text,
                category=category,
                tags=tags,
                tag_mode=tag_mode,
                folder_prefixes=folder_prefixes,
                sort=sort,
                limit=limit,
                after=after,
                shelf=shelf,
            )

    def _is_library_positive(self, conn: sqlite3.Connection, positive_id: int) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM prompts p WHERE p.id=? AND " + _ELIGIBLE, (positive_id,)
            ).fetchone()
            is not None
        )

    def _get_library_positive(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        positive_id: int,
        *,
        variant_limit: int = 40,
        variant_after: int | None = None,
        archive: str = "active",
    ) -> dict | None:
        _limit(variant_limit)
        scope = _archive(archive)
        if variant_after is not None and (
            type(variant_after) is not int or variant_after <= 0
        ):
            raise ValueError("Invalid variant cursor")
        rows = self._library_parents(
            conn, epoch, _ELIGIBLE + " AND p.id=?", [positive_id]
        )
        if not rows:
            return None
        positive = rows[0]
        positive["archived_variant_count"] = conn.execute(
            "SELECT COUNT(*) FROM prompt_pairs "
            "WHERE positive_prompt_id=? AND archived=1",
            (positive_id,),
        ).fetchone()[0]
        variant_total = {
            "active": positive["variant_count"],
            "archived": positive["archived_variant_count"],
            "all": positive["variant_count"] + positive["archived_variant_count"],
        }[archive]
        where = "pp.positive_prompt_id=?" + scope
        params = [positive_id]
        if variant_after is not None:
            where += " AND pp.id<?"
            params.append(variant_after)
        variants = [
            dict(row)
            for row in conn.execute(
                "SELECT pp.id,COALESCE(n.text,'') AS negative_text,"
                "pp.category,pp.title,pp.archived,"
                "pp.negative_prompt_id IS NULL AS explicit_empty,pp.run_count,"
                "(SELECT COUNT(DISTINCT image_id) FROM image_prompt_pairs ip "
                "WHERE ip.pair_id=pp.id) AS image_count "
                "FROM prompt_pairs pp "
                "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
                "WHERE " + where + " ORDER BY pp.id DESC LIMIT ?",
                [*params, variant_limit + 1],
            )
        ]
        variants_has_more = len(variants) > variant_limit
        variants = variants[:variant_limit]
        by_id = {row["id"]: row for row in variants}
        for row in variants:
            row["tags"] = []
            row["archived"] = bool(row["archived"])
            row["explicit_empty"] = bool(row["explicit_empty"])
            # The history lane's "N runs" (#249): queue-hook pair uses, 0 when unset.
            row["run_count"] = row["run_count"] if type(row["run_count"]) is int else 0
        if by_id:
            marks = ",".join("?" for _ in by_id)
            for row in conn.execute(
                "SELECT pt.pair_id,t.name FROM prompt_pair_tags pt "
                "JOIN tags t ON t.id=pt.tag_id "
                f"WHERE pt.pair_id IN ({marks}) ORDER BY t.name",
                list(by_id),
            ):
                by_id[row["pair_id"]]["tags"].append(row["name"])
        unassigned = conn.execute(
            "SELECT COUNT(*) FROM generated_images g WHERE g.prompt_id=? "
            "AND NOT EXISTS(SELECT 1 FROM image_prompt_pairs ip "
            "WHERE ip.image_id=g.id)",
            (positive_id,),
        ).fetchone()[0]
        return {
            "library_epoch": epoch,
            "positive": positive,
            "variants": variants,
            "unassigned_image_count": unassigned,
            "variant_total": variant_total,
            "variants_has_more": variants_has_more,
            "variants_next_key": variants[-1]["id"] if variants_has_more else None,
        }

    def get_library_positive(
        self,
        positive_id: int,
        *,
        variant_limit: int = 40,
        variant_after: int | None = None,
        archive: str = "active",
    ) -> dict | None:
        with self.library_snapshot() as (conn, epoch, _ceiling):
            return self._get_library_positive(
                conn,
                epoch,
                positive_id,
                variant_limit=variant_limit,
                variant_after=variant_after,
                archive=archive,
            )

    def _get_library_options(self, conn: sqlite3.Connection, epoch: str) -> dict:
        rows = conn.execute(
            "SELECT category FROM (SELECT p.category AS category "
            "FROM prompts p WHERE "
            + _ELIGIBLE
            + (" UNION SELECT category FROM prompt_pairs " "WHERE archived=0) ")
            + "WHERE category IS NOT NULL AND TRIM(category)!='' "
            "ORDER BY category COLLATE NOCASE,category"
        ).fetchall()
        return {
            "library_epoch": epoch,
            "categories": [row["category"] for row in rows],
        }

    def get_library_options(self, folder_roots=None) -> dict:
        """Categories; with *folder_roots* (root spellings) also ``folders``."""
        with self.library_snapshot() as (conn, epoch, _ceiling):
            result = self._get_library_options(conn, epoch)
            if folder_roots is not None:
                result["folders"] = list_library_folders(conn, folder_roots, _ELIGIBLE)
            return result

    def get_library_previews(self, positive_ids: list[int]) -> dict[int, list[dict]]:
        """Read at most two links per parent in one bounded page query."""
        if (
            not isinstance(positive_ids, list)
            or len(positive_ids) > 200
            or any(type(value) is not int or value <= 0 for value in positive_ids)
        ):
            raise ValueError("Invalid preview parent IDs")
        if not positive_ids:
            return {}
        result = {value: [] for value in positive_ids}
        marks = ",".join("?" for _ in positive_ids)
        rows = self.model.get_connection().execute(
            "WITH ranked AS (SELECT id,prompt_id,image_path,ROW_NUMBER() OVER "
            "(PARTITION BY prompt_id ORDER BY id DESC) AS position "
            f"FROM generated_images WHERE prompt_id IN ({marks})) "
            "SELECT id,prompt_id,image_path FROM ranked WHERE position<=2 "
            "ORDER BY prompt_id,id DESC",
            positive_ids,
        )
        for row in rows:
            result[row["prompt_id"]].append(dict(row))
        return result

    def _list_library_images(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        positive_id: int,
        *,
        pair_id: int | None = None,
        limit: int = 40,
        after: int | None = None,
        archive: str = "active",
    ) -> dict:
        _limit(limit)
        scope = _archive(archive)
        if after is not None and (type(after) is not int or after <= 0):
            raise ValueError("Invalid image cursor")
        if (
            pair_id is not None
            and not conn.execute(
                "SELECT 1 FROM prompt_pairs pp WHERE pp.id=? "
                "AND pp.positive_prompt_id=?" + scope,
                (pair_id, positive_id),
            ).fetchone()
        ):
            if conn.execute(
                "SELECT 1 FROM prompt_pairs WHERE id=? AND positive_prompt_id=?",
                (pair_id, positive_id),
            ).fetchone():
                raise ValueError("Variant is not in the requested archive scope")
            raise ValueError("Variant does not belong to this positive prompt")
        where, params = "g.prompt_id=?", [positive_id]
        if pair_id is not None:
            where += (
                " AND EXISTS(SELECT 1 FROM image_prompt_pairs ip "
                "WHERE ip.image_id=g.id AND ip.pair_id=?)"
            )
            params.append(pair_id)
        total = conn.execute(
            "SELECT COUNT(*) FROM generated_images g WHERE " + where, params
        ).fetchone()[0]
        if after is not None:
            where += " AND g.id<?"
            params.append(after)
        rows = conn.execute(
            "SELECT g.id,g.image_path FROM generated_images g WHERE "
            + where
            + " ORDER BY g.id DESC LIMIT ?",
            [*params, limit + 1],
        ).fetchall()
        return {
            "library_epoch": epoch,
            "images": [dict(row) for row in rows[:limit]],
            "total": total,
            "has_more": len(rows) > limit,
            "next_key": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    def list_library_images(
        self,
        positive_id: int,
        *,
        pair_id: int | None = None,
        limit: int = 40,
        after: int | None = None,
        archive: str = "active",
    ) -> dict:
        with self.library_snapshot() as (conn, epoch, _ceiling):
            return self._list_library_images(
                conn,
                epoch,
                positive_id,
                pair_id=pair_id,
                limit=limit,
                after=after,
                archive=archive,
            )

    def export_library(self) -> dict:
        """Read every portable owner and pair from one snapshot, writing nothing.

        Local IDs order the rows internally and never appear in the document.
        """
        with self.library_snapshot() as (conn, _epoch, _ceiling):
            owners = {}
            order = []
            for row in conn.execute(
                "SELECT p.id,p.text,ls.title,p.category,p.rating,p.notes,"
                + ARCHIVED
                + " AS archived FROM "
                + _FROM
                + " WHERE "
                + _ELIGIBLE
                + " ORDER BY p.id"
            ):
                owners[row["id"]] = {
                    "positive_text": row["text"],
                    "title": row["title"],
                    "category": row["category"],
                    "tags": [],
                    "rating": row["rating"],
                    "notes": row["notes"],
                    "archived": bool(row["archived"]),
                    "variants": [],
                }
                order.append(row["id"])
            for row in conn.execute(
                "SELECT pt.prompt_id,t.name FROM prompt_tags pt "
                "JOIN tags t ON t.id=pt.tag_id ORDER BY pt.prompt_id,t.name"
            ):
                if row["prompt_id"] in owners:
                    owners[row["prompt_id"]]["tags"].append(row["name"])
            variants = {}
            for row in conn.execute(
                "SELECT pp.id,pp.positive_prompt_id,"
                "COALESCE(n.text,'') AS negative_text,"
                "pp.title,pp.category,pp.rating,pp.notes,pp.archived "
                "FROM prompt_pairs pp "
                "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
                "ORDER BY pp.id"
            ):
                variant = {
                    "negative_text": row["negative_text"],
                    "title": row["title"],
                    "category": row["category"],
                    "tags": [],
                    "rating": row["rating"],
                    "notes": row["notes"],
                    "archived": bool(row["archived"]),
                }
                variants[row["id"]] = variant
                owners[row["positive_prompt_id"]]["variants"].append(variant)
            for row in conn.execute(
                "SELECT pt.pair_id,t.name FROM prompt_pair_tags pt "
                "JOIN tags t ON t.id=pt.tag_id ORDER BY pt.pair_id,t.name"
            ):
                variants[row["pair_id"]]["tags"].append(row["name"])
            return {
                "schema_version": 1,
                "kind": "prompt-manager-library",
                "positives": [owners[key] for key in order],
            }

    def library_counts(self) -> dict:
        """System › Overview counts from one snapshot, writing nothing.

        ``positive_prompts`` and ``negative_variants`` are the unfiltered
        Library's ``total`` and ``variant_total``: eligible parents on the
        shelf (not archived, #443) and their active pairs (every pair's
        positive is eligible). ``images`` counts indexed
        ``generated_images`` rows, not files on disk. An image has no rating
        of its own (it shows its linked prompt's), so ``rated_images`` counts
        the indexed images whose linked prompt is rated. ``review_pending`` is
        the Needs review queue's pending total.
        """
        with self.library_snapshot() as (conn, _epoch, _ceiling):
            row = conn.execute(
                "SELECT (SELECT COUNT(*) FROM prompts p WHERE "
                + _ELIGIBLE
                + " AND "
                + SHELVED
                + "),"
                "(SELECT COUNT(*) FROM prompt_pairs pp WHERE pp.archived=0 AND "
                + SHELVED_PAIR
                + "),"
                "(SELECT COUNT(*) FROM generated_images),"
                "(SELECT COUNT(*) FROM generated_images gi JOIN prompts r "
                "ON r.id=gi.prompt_id WHERE r.rating IS NOT NULL),"
                "(SELECT COUNT(*) FROM media_review_items WHERE state='pending')"
            ).fetchone()
        return {
            "positive_prompts": row[0],
            "negative_variants": row[1],
            "images": row[2],
            "rated_images": row[3],
            "review_pending": row[4],
        }

    def usage_by_category(self) -> list[dict]:
        """Runs and average rating of the Library's parents, per category.

        Only eligible parents count, so a negative-only prompt's runs (which
        repeat its pairs' runs) are never added twice. A blank category groups
        with no category as ``None``. Unrated parents are left out of the
        average, which is ``None`` when no parent is rated. Most runs first.
        """
        with self.library_snapshot() as (conn, _epoch, _ceiling):
            rows = conn.execute(
                "SELECT * FROM (SELECT NULLIF(TRIM(p.category),'') AS category,"
                "COALESCE(SUM(p.run_count),0) AS runs,AVG(p.rating) AS rating "
                "FROM prompts p WHERE " + _ELIGIBLE + " GROUP BY 1) "
                "ORDER BY runs DESC,category IS NULL,category"
            ).fetchall()
        return [
            {
                "category": row["category"],
                "runs": row["runs"],
                "average_rating": (
                    None if row["rating"] is None else round(row["rating"], 2)
                ),
            }
            for row in rows
        ]
