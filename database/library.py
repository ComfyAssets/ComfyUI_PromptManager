"""Read-only positive-parent views over existing prompt and pair tables."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .library_tokens import library_edit_token

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


def titled(stored_titles: tuple, text: object) -> tuple[str | None, str | None]:
    """Return ``(title, source)``: a stored title, else the text's first line.

    ``source`` is ``"stored"`` for the first non-blank stored title (blank as
    in JavaScript's trim, like the Library view), ``"text"`` for the first line,
    and None when both are empty. The first line is trimmed and, past
    DISPLAY_TITLE_LIMIT characters, cut with an ellipsis like the Library's
    ``title()``.
    """
    for title in stored_titles:
        if isinstance(title, str) and title.strip(_JS_TRIM):
            return title, "stored"
    if not isinstance(text, str):
        return None, None
    first = text.split("\n")[0].strip(_JS_TRIM)
    if len(first) > DISPLAY_TITLE_LIMIT:
        first = first[:DISPLAY_TITLE_LIMIT] + "\u2026"
    return (first, "text") if first else (None, None)


def display_title(stored_titles: tuple, text: object) -> str | None:
    """The Library display title of ``titled``, without its source."""
    return titled(stored_titles, text)[0]


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


def _filter(text: str, category: str | None, tags: list[str]) -> tuple[str, list]:
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
    for tag in tags:
        parent.append(
            "EXISTS(SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.prompt_id=p.id AND t.name=?)"
        )
        pair.append(
            "EXISTS(SELECT 1 FROM prompt_pair_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.pair_id=pp.id AND t.name=?)"
        )
        parent_params.append(tag)
        pair_params.append(tag)
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
    return f"({_ELIGIBLE}) AND ({condition})", [*parent_params, *pair_params]


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
        sort: str = "newest",
        limit: int = 40,
        after: dict | None = None,
    ) -> dict:
        _limit(limit)
        if sort not in _ORDERS:
            raise ValueError("Unknown library sort order")
        where, params = _filter(text, category, [] if tags is None else tags)
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
        total = conn.execute(
            "SELECT COUNT(*) FROM " + _FROM + " WHERE " + where, params
        ).fetchone()[0]
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
            "has_more": has_more,
            "next_key": next_key,
        }

    def list_library(
        self,
        *,
        text: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
        sort: str = "newest",
        limit: int = 40,
        after: dict | None = None,
    ) -> dict:
        with self.library_snapshot(include_ceiling=True) as (conn, epoch, ceiling):
            return self._list_library(
                conn,
                epoch,
                ceiling,
                text=text,
                category=category,
                tags=tags,
                sort=sort,
                limit=limit,
                after=after,
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
                "pp.negative_prompt_id IS NULL AS explicit_empty,"
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

    def get_library_options(self) -> dict:
        with self.library_snapshot() as (conn, epoch, _ceiling):
            return self._get_library_options(conn, epoch)

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
                "SELECT p.id,p.text,ls.title,p.category,p.rating,p.notes FROM "
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
