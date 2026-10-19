"""Bounded tag and category reads, and guarded tag commands.

A count is the number of eligible Library parents that carry the value
directly or through one active saved pair, which is exactly what
``list_library(tags=[name])`` / ``list_library(category=value)`` return.

Tag commands run in one ``_pair_transaction``: the storage epoch is checked
first, then the exact ``{id, name}`` identity, and the receipt is read on the
same connection before commit. Owner rows and their timestamps never change.

Category consolidation is previewed in one ``library_snapshot`` and applied in
one ``_pair_transaction`` that recomputes the owner projection and requires the
preview's ``impact_token``; only rows carrying the exact source change.
"""

import sqlite3

from .library import _ELIGIBLE, _limit
from .library_commands import _TOKEN, LibraryCommandError, _validate_epoch
from .library_tokens import canonical_token
from .models import utc_now_iso

try:
    from ..utils.validators import validate_category, validate_tags
except ImportError:
    from utils.validators import validate_category, validate_tags

_CATEGORY_DOMAIN = "prompt-manager-vocabulary-category:v1\n"


def _tag_rows(page: str) -> str:
    """Count rows for the registry rows selected by ``page`` (id, name)."""
    # Eligible parent components carrying a tag in the page, plus the
    # positive parent of every active pair carrying it (always eligible).
    return (
        "WITH page AS (" + page + "),"
        "owners(tag_id,pid) AS ("
        "SELECT pt.tag_id,p.id FROM prompts p "
        "JOIN prompt_tags pt ON pt.prompt_id=p.id "
        "WHERE pt.tag_id IN (SELECT id FROM page) AND " + _ELIGIBLE + " "
        "UNION SELECT ppt.tag_id,pp.positive_prompt_id FROM prompt_pairs pp "
        "JOIN prompt_pair_tags ppt ON ppt.pair_id=pp.id "
        "WHERE pp.archived=0 AND ppt.tag_id IN (SELECT id FROM page)) "
        "SELECT page.id,page.name,COUNT(DISTINCT owners.pid) AS prompt_count "
        "FROM page LEFT JOIN owners ON owners.tag_id=page.id "
        "GROUP BY page.id ORDER BY page.id"
    )


_TAG_PAGE = _tag_rows("SELECT id,name FROM tags WHERE id>? ORDER BY id LIMIT ?")
_TAG_ONE = _tag_rows("SELECT id,name FROM tags WHERE id=?")
# Every owner kind a tag can sit on, for the delete confirmation.
_TAG_IMPACT = (
    "SELECT "
    "(SELECT COUNT(*) FROM prompts p JOIN prompt_tags pt ON pt.prompt_id=p.id "
    "WHERE pt.tag_id=:tag AND " + _ELIGIBLE + ") AS positive_prompts,"
    "(SELECT COUNT(*) FROM prompts p JOIN prompt_tags pt ON pt.prompt_id=p.id "
    "WHERE pt.tag_id=:tag AND NOT " + _ELIGIBLE + ") AS other_prompts,"
    "(SELECT COUNT(*) FROM prompt_pairs pp JOIN prompt_pair_tags ppt "
    "ON ppt.pair_id=pp.id WHERE ppt.tag_id=:tag AND pp.archived=0) "
    "AS negative_variants,"
    "(SELECT COUNT(*) FROM prompt_pairs pp JOIN prompt_pair_tags ppt "
    "ON ppt.pair_id=pp.id WHERE ppt.tag_id=:tag AND pp.archived!=0) "
    "AS archived_negative_variants"
)
_MAX_ID = 2**63 - 1


def validate_vocabulary_tag_name(value: object) -> str:
    """Return the trimmed name, applying tag rules plus the native comma ban."""
    if not isinstance(value, str):
        raise ValueError("Tag name must be a string")
    validate_tags([value])
    name = value.strip()
    if "," in name:
        raise ValueError("Tag names cannot contain commas")
    return name


def _validate_tag_id(tag_id: object) -> None:
    if type(tag_id) is not int or not 1 <= tag_id <= _MAX_ID:
        raise ValueError("Invalid tag ID")


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


# Every owner of one exact category value, in id order, with the flag the
# impact counts split on.
_CATEGORY_COMPONENTS = (
    "SELECT p.id," + _ELIGIBLE + " AS eligible FROM prompts p "
    "WHERE p.category=? ORDER BY p.id"
)
_CATEGORY_PAIRS = "SELECT id,archived FROM prompt_pairs WHERE category=? ORDER BY id"
_CATEGORY_EXISTS = (
    "SELECT EXISTS(SELECT 1 FROM prompts WHERE category=:v)"
    " OR EXISTS(SELECT 1 FROM prompt_pairs WHERE category=:v)"
)

# Pairs after components, so a failing pair update rolls both back.
_CATEGORY_UPDATES = (
    "UPDATE prompts SET category=?,updated_at=? WHERE category=?",
    "UPDATE prompt_pairs SET category=?,updated_at=? WHERE category=?",
)


def category_impact_token(
    epoch: str,
    source: str,
    target: str,
    target_exists: bool,
    components: list,
    pairs: list,
) -> str:
    """Hash a category projection like ``library_edit_token`` (no secret)."""
    preimage = {
        "epoch": epoch,
        "source": source,
        "target": target,
        "target_exists": bool(target_exists),
        "components": sorted([int(i), bool(flag)] for i, flag in components),
        "pairs": sorted([int(i), bool(flag)] for i, flag in pairs),
    }
    return canonical_token(_CATEGORY_DOMAIN, preimage)


def _validate_category_change(source: object, target: object) -> str:
    """Check the exact source and return the trimmed, different target."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a nonblank category value")
    if not isinstance(target, str):
        raise ValueError("Category must be a string")
    validate_category(target)
    target = target.strip()
    if not target:
        raise ValueError("New category name cannot be blank")
    if target == source:
        raise ValueError("New category name must differ from the current one")
    return target


def _validate_impact_token(impact_token: object) -> None:
    if not isinstance(impact_token, str) or not _TOKEN.fullmatch(impact_token):
        raise ValueError("impact_token must be 64 lowercase hexadecimal characters")


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
    """Vocabulary reads and tag commands.

    ``_`` helpers take an open ``library_snapshot`` or ``_pair_transaction``
    connection.
    """

    def _vocabulary_tag(self, conn: sqlite3.Connection, tag_id: int) -> dict | None:
        row = conn.execute(_TAG_ONE, (tag_id,)).fetchone()
        return dict(row) if row else None

    def _vocabulary_identity(
        self, conn: sqlite3.Connection, epoch: str, tag_id: int, name: str
    ) -> dict:
        """The stored tag, or vocabulary-conflict when it is gone or renamed."""
        tag = self._vocabulary_tag(conn, tag_id)
        if tag is None or tag["name"] != name:
            raise LibraryCommandError("vocabulary-conflict", epoch)
        return tag

    @staticmethod
    def _tag_owner_counts(conn: sqlite3.Connection, tag_id: int) -> dict:
        components = conn.execute(
            "SELECT COUNT(DISTINCT prompt_id) FROM prompt_tags WHERE tag_id=?",
            (tag_id,),
        ).fetchone()[0]
        pairs = conn.execute(
            "SELECT COUNT(DISTINCT pair_id) FROM prompt_pair_tags WHERE tag_id=?",
            (tag_id,),
        ).fetchone()[0]
        return {"components_changed": components, "pairs_changed": pairs}

    def create_vocabulary_tag(self, name: str, *, expected_epoch: str) -> dict:
        """Insert an unused registry row, or reuse the exact existing name."""
        _validate_epoch(expected_epoch)
        name = validate_vocabulary_tag_name(name)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            created = row is None
            if created:
                tag_id = conn.execute(
                    "INSERT INTO tags(name) VALUES(?)", (name,)
                ).lastrowid
            else:
                tag_id = row[0]
            return {
                "library_epoch": epoch,
                "created": created,
                "tag": self._vocabulary_tag(conn, tag_id),
            }

    def rename_vocabulary_tag(
        self, tag_id: int, *, expected_epoch: str, name: str, new_name: str
    ) -> dict:
        """Rename ``{tag_id, name}``, merging into an existing exact ``new_name``.

        Links move across components and all pairs, archived included; an
        owner already carrying the target keeps one link.
        """
        _validate_tag_id(tag_id)
        _validate_epoch(expected_epoch)
        if not isinstance(name, str):
            raise ValueError("name must be the tag's current name")
        new_name = validate_vocabulary_tag_name(new_name)
        if new_name == name:
            raise ValueError("New name must differ from the current name")
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            self._vocabulary_identity(conn, epoch, tag_id, name)
            counts = self._tag_owner_counts(conn, tag_id)
            target = conn.execute(
                "SELECT id FROM tags WHERE name=?", (new_name,)
            ).fetchone()
            if target is None:
                conn.execute("UPDATE tags SET name=? WHERE id=?", (new_name, tag_id))
                result_id = tag_id
            else:
                result_id = target[0]
                self._move_tag_links(conn, tag_id, result_id)
                conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            return {
                "library_epoch": epoch,
                "merged": target is not None,
                "tag": self._vocabulary_tag(conn, result_id),
                **counts,
            }

    def delete_vocabulary_tag(
        self, tag_id: int, *, expected_epoch: str, name: str
    ) -> dict:
        """Remove the registry row ``{tag_id, name}``; its links cascade."""
        _validate_tag_id(tag_id)
        _validate_epoch(expected_epoch)
        if not isinstance(name, str):
            raise ValueError("name must be the tag's current name")
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            self._vocabulary_identity(conn, epoch, tag_id, name)
            counts = self._tag_owner_counts(conn, tag_id)
            conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            return {"library_epoch": epoch, "deleted": True, **counts}

    def vocabulary_tag_impact(self, tag_id: int) -> dict | None:
        """Owner counts by kind for one tag, or None when it does not exist."""
        _validate_tag_id(tag_id)
        with self.library_snapshot() as (conn, epoch, _ceiling):
            if not conn.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
                return None
            row = conn.execute(_TAG_IMPACT, {"tag": tag_id}).fetchone()
            return {"library_epoch": epoch, "impact": dict(row)}

    @staticmethod
    def _category_projection(
        conn: sqlite3.Connection, epoch: str, source: str, target: str
    ) -> dict:
        """Owners of ``source``, impact counts and the token over them."""
        components = [
            [row[0], bool(row[1])]
            for row in conn.execute(_CATEGORY_COMPONENTS, (source,))
        ]
        pairs = [
            [row[0], bool(row[1])] for row in conn.execute(_CATEGORY_PAIRS, (source,))
        ]
        if not components and not pairs:
            raise LibraryCommandError("vocabulary-conflict", epoch)
        target_exists = bool(
            conn.execute(_CATEGORY_EXISTS, {"v": target}).fetchone()[0]
        )
        eligible = sum(flag for _id, flag in components)
        archived = sum(flag for _id, flag in pairs)
        return {
            "target_exists": target_exists,
            "impact": {
                "positive_prompts": eligible,
                "other_prompts": len(components) - eligible,
                "negative_variants": len(pairs) - archived,
                "archived_negative_variants": archived,
            },
            "impact_token": category_impact_token(
                epoch, source, target, target_exists, components, pairs
            ),
        }

    def preview_vocabulary_category(
        self, *, expected_epoch: str, source: str, target: str
    ) -> dict:
        """Read-only impact of renaming the exact ``source`` to ``target``."""
        _validate_epoch(expected_epoch)
        target = _validate_category_change(source, target)
        with self.library_snapshot() as (conn, epoch, _ceiling):
            if epoch != expected_epoch:
                raise LibraryCommandError("storage-changed", epoch)
            projection = self._category_projection(conn, epoch, source, target)
            return {
                "library_epoch": epoch,
                "source": source,
                "target": target,
                **projection,
            }

    def apply_vocabulary_category(
        self, *, expected_epoch: str, source: str, target: str, impact_token: str
    ) -> dict:
        """Set the exact ``source`` to ``target`` on every component and pair.

        The recomputed projection must still match the preview's token; a
        mismatch is ``vocabulary-conflict`` with nothing written.
        """
        _validate_epoch(expected_epoch)
        target = _validate_category_change(source, target)
        _validate_impact_token(impact_token)
        with self._pair_transaction() as conn:
            epoch = self._library_epoch(conn, expected_epoch)
            projection = self._category_projection(conn, epoch, source, target)
            if projection["impact_token"] != impact_token:
                raise LibraryCommandError("vocabulary-conflict", epoch)
            stamp = utc_now_iso()
            for statement in _CATEGORY_UPDATES:
                conn.execute(statement, (target, stamp, source))
            return {
                "library_epoch": epoch,
                "source": source,
                "target": target,
                "consolidated": projection["target_exists"],
                "impact": projection["impact"],
            }

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
