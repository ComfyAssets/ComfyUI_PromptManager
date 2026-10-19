"""Bounded tag and category reads whose counts match Library Browse totals.

A count is the number of eligible Library parents that carry the value
directly or through one active saved pair, which is exactly what
``list_library(tags=[name])`` / ``list_library(category=value)`` return.
"""

import sqlite3

from .library import _ELIGIBLE, _limit

# Eligible parent components carrying a tag in the requested page, plus the
# positive parent of every active pair carrying it (always eligible).
_TAG_PAGE = (
    "WITH page AS (SELECT id,name FROM tags WHERE id>? ORDER BY id LIMIT ?),"
    "owners(tag_id,pid) AS ("
    "SELECT pt.tag_id,p.id FROM prompts p JOIN prompt_tags pt ON pt.prompt_id=p.id "
    "WHERE pt.tag_id IN (SELECT id FROM page) AND " + _ELIGIBLE + " "
    "UNION SELECT ppt.tag_id,pp.positive_prompt_id FROM prompt_pairs pp "
    "JOIN prompt_pair_tags ppt ON ppt.pair_id=pp.id "
    "WHERE pp.archived=0 AND ppt.tag_id IN (SELECT id FROM page)) "
    "SELECT page.id,page.name,COUNT(DISTINCT owners.pid) AS prompt_count "
    "FROM page LEFT JOIN owners ON owners.tag_id=page.id "
    "GROUP BY page.id ORDER BY page.id"
)
# The /library/options universe: nonblank values on eligible parents or on
# active pairs, grouped exactly (whitespace and case variants stay distinct).
_CATEGORY_GROUPS = (
    "WITH owners(value,pid) AS ("
    "SELECT p.category,p.id FROM prompts p WHERE p.category IS NOT NULL "
    "AND TRIM(p.category)!='' AND " + _ELIGIBLE + " "
    "UNION SELECT pp.category,pp.positive_prompt_id FROM prompt_pairs pp "
    "WHERE pp.archived=0 AND pp.category IS NOT NULL AND TRIM(pp.category)!=''),"
    "grouped AS (SELECT value,MIN(pid) AS first_pid,"
    "COUNT(DISTINCT pid) AS prompt_count FROM owners GROUP BY value) "
)


def _tag_after(after: object) -> int:
    if after is None:
        return 0
    if type(after) is not int or after <= 0:
        raise ValueError("Invalid vocabulary cursor")
    return after


def _category_after(after: object) -> tuple[int, str] | None:
    if after is None:
        return None
    if (
        not isinstance(after, list)
        or len(after) != 2
        or type(after[0]) is not int
        or not isinstance(after[1], str)
    ):
        raise ValueError("Invalid vocabulary cursor")
    return after[0], after[1]


class VocabularyOperations:
    """Vocabulary reads; ``_`` helpers take a ``library_snapshot`` connection."""

    def _vocabulary_tags(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        *,
        limit: int = 100,
        after: int | None = None,
    ) -> dict:
        _limit(limit)
        start = _tag_after(after)
        total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        rows = [dict(row) for row in conn.execute(_TAG_PAGE, (start, limit + 1))]
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "library_epoch": epoch,
            "tags": rows,
            "total": total,
            "has_more": has_more,
            "next_key": rows[-1]["id"] if has_more else None,
        }

    def _vocabulary_categories(
        self,
        conn: sqlite3.Connection,
        epoch: str,
        *,
        limit: int = 100,
        after: list | None = None,
    ) -> dict:
        _limit(limit)
        key = _category_after(after)
        total = conn.execute(
            _CATEGORY_GROUPS + "SELECT COUNT(*) FROM grouped"
        ).fetchone()[0]
        where, params = "", []
        if key is not None:
            where = " WHERE first_pid>? OR (first_pid=? AND value>?)"
            params = [key[0], key[0], key[1]]
        rows = conn.execute(
            _CATEGORY_GROUPS
            + "SELECT value,first_pid,prompt_count FROM grouped"
            + where
            + " ORDER BY first_pid,value LIMIT ?",
            [*params, limit + 1],
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "library_epoch": epoch,
            "categories": [
                {"value": row["value"], "prompt_count": row["prompt_count"]}
                for row in rows
            ],
            "total": total,
            "has_more": has_more,
            "next_key": (
                [rows[-1]["first_pid"], rows[-1]["value"]] if has_more else None
            ),
        }

    def list_vocabulary_tags(self, *, limit: int = 100, after: int | None = None):
        with self.library_snapshot() as (conn, epoch, _ceiling):
            return self._vocabulary_tags(conn, epoch, limit=limit, after=after)

    def list_vocabulary_categories(
        self, *, limit: int = 100, after: list | None = None
    ) -> dict:
        with self.library_snapshot() as (conn, epoch, _ceiling):
            return self._vocabulary_categories(conn, epoch, limit=limit, after=after)
