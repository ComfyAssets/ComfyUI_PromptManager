"""Ordered reusable prompt pairs and their generation provenance."""

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from .models import pair_provenance_error, utc_now_iso

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash


class PairDependencyError(ValueError):
    """An operation would change or remove content used by a saved pair."""


def _validate_metadata(
    title: Optional[str],
    category: Optional[str],
    tags: Optional[List[str]],
    rating: Optional[int],
    notes: Optional[str],
) -> Optional[List[str]]:
    for name, value in (("title", title), ("category", category), ("notes", notes)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
        raise ValueError("Rating must be between 1 and 5")
    if tags is None:
        return None
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


class PairOperations:
    """Connection-level pair operations mixed into PromptDatabase."""

    @contextmanager
    def _pair_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise RuntimeError("Pair operations require their own transaction")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def _ensure_prompts_unpaired(
        self, conn: sqlite3.Connection, prompt_ids: List[int]
    ) -> None:
        if not prompt_ids:
            return
        placeholders = ",".join("?" for _ in prompt_ids)
        row = conn.execute(
            f"SELECT id FROM prompt_pairs WHERE positive_prompt_id IN ({placeholders}) "
            f"OR negative_prompt_id IN ({placeholders}) LIMIT 1",
            [*prompt_ids, *prompt_ids],
        ).fetchone()
        if row:
            raise PairDependencyError(
                f"Prompt belongs to saved pair {row['id']}; delete the pair first "
                "or save changed text as a new prompt"
            )

    def _pair_component(self, conn: sqlite3.Connection, text: str) -> int:
        return self._pair_component_outcome(conn, text)[0]

    def _pair_component_outcome(
        self, conn: sqlite3.Connection, text: str
    ) -> tuple[int, bool]:
        """Resolve a component by its dedup hash; report whether it was inserted.

        The first stored spelling wins: the hash ignores outer whitespace and
        case, internal whitespace stays significant. ``created`` comes from the
        INSERT result under the caller's held write transaction.
        """
        prompt_hash = generate_prompt_hash(text)
        now = utc_now_iso()
        created = (
            conn.execute(
                "INSERT INTO prompts(text, hash, created_at, updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(hash) DO NOTHING",
                (text.strip(), prompt_hash, now, now),
            ).rowcount
            == 1
        )
        row = conn.execute(
            "SELECT id FROM prompts WHERE hash = ?", (prompt_hash,)
        ).fetchone()
        return row["id"], created

    def _sync_pair_tags(
        self, conn: sqlite3.Connection, pair_id: int, tags: List[str]
    ) -> None:
        conn.execute("DELETE FROM prompt_pair_tags WHERE pair_id = ?", (pair_id,))
        for tag_id in self._ensure_tags(conn, tags).values():
            conn.execute(
                "INSERT INTO prompt_pair_tags(pair_id,tag_id) VALUES(?,?)",
                (pair_id, tag_id),
            )

    def save_prompt_pair(
        self,
        positive_text: str,
        negative_text: str = "",
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Save an ordered pair atomically; repeat saves preserve its metadata."""
        with self._pair_transaction() as conn:
            return self._save_pair(
                conn,
                positive_text,
                negative_text,
                title=title,
                category=category,
                tags=tags,
                rating=rating,
                notes=notes,
            )

    def _save_pair(
        self,
        conn: sqlite3.Connection,
        positive_text: str,
        negative_text: str = "",
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Save an ordered pair atomically; repeat saves preserve its metadata."""
        if not isinstance(positive_text, str) or not positive_text.strip():
            raise ValueError("Positive prompt text cannot be empty")
        if not isinstance(negative_text, str):
            raise ValueError("Negative prompt text must be a string; use '' for empty")
        # Validate metadata before any component insertion.
        _validate_metadata(title, category, tags, rating, notes)
        positive = self._pair_component(conn, positive_text)
        negative = (
            self._pair_component(conn, negative_text) if negative_text.strip() else None
        )
        return self._save_pair_components(
            conn,
            positive,
            negative,
            title=title,
            category=category,
            tags=tags,
            rating=rating,
            notes=notes,
        )[0]

    def _save_pair_components(
        self,
        conn: sqlite3.Connection,
        positive_id: int,
        negative_id: Optional[int],
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> tuple[int, bool]:
        """Create or reuse the ordered pair of existing component IDs.

        Returns ``(pair_id, created)``. A reused pair keeps all of its metadata
        and archive state; metadata applies only to a newly inserted pair.
        """
        tags = _validate_metadata(title, category, tags, rating, notes)
        existing = conn.execute(
            "SELECT id FROM prompt_pairs WHERE positive_prompt_id = ? "
            "AND negative_prompt_id IS ?",
            (positive_id, negative_id),
        ).fetchone()
        if existing:
            return existing["id"], False
        now = utc_now_iso()
        pair_id = conn.execute(
            "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id,title,"
            "category,rating,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (positive_id, negative_id, title, category, rating, notes, now, now),
        ).lastrowid
        if tags:
            self._sync_pair_tags(conn, pair_id, tags)
        return pair_id, True

    def _read_pair_rows(
        self, conn: sqlite3.Connection, where: str, params: List[Any], suffix: str = ""
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            "SELECT pp.*, p.text AS positive_text, "
            "COALESCE(n.text,'') AS negative_text,"
            "(SELECT COUNT(DISTINCT image_id) FROM image_prompt_pairs ip "
            "WHERE ip.pair_id=pp.id) AS image_count FROM prompt_pairs pp "
            "JOIN prompts p ON p.id=pp.positive_prompt_id "
            "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id WHERE " + where + suffix,
            params,
        ).fetchall()
        pairs = [dict(row) for row in rows]
        if not pairs:
            return pairs
        by_id = {pair["id"]: pair for pair in pairs}
        for pair in pairs:
            pair["archived"] = bool(pair["archived"])
            pair["tags"] = []
        placeholders = ",".join("?" for _ in by_id)
        for row in conn.execute(
            "SELECT pt.pair_id,t.name FROM prompt_pair_tags pt JOIN tags t ON "
            f"t.id=pt.tag_id WHERE pt.pair_id IN ({placeholders}) ORDER BY t.name",
            list(by_id),
        ):
            by_id[row["pair_id"]]["tags"].append(row["name"])
        return pairs

    def get_prompt_pair(self, pair_id: int) -> Optional[Dict[str, Any]]:
        """Return components, pair metadata and image count, including archives."""
        rows = self._read_pair_rows(self.model.get_connection(), "pp.id = ?", [pair_id])
        return rows[0] if rows else None

    def list_prompt_pairs(
        self,
        *,
        text: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ) -> Dict[str, Any]:
        """List pairs with all-tag filtering and stable paginated ordering."""
        orders = {
            "newest": "pp.id DESC",
            "last_used_desc": "julianday(pp.last_used_at) DESC, pp.id DESC",
        }
        if sort not in orders:
            raise ValueError("Unknown pair sort order")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        tags = _validate_metadata(None, category, tags, None, None)
        if text is not None and not isinstance(text, str):
            raise ValueError("text must be a string")
        clauses, params = ["1=1"], []
        if not include_archived:
            clauses.append("pp.archived=0")
        if category is not None:
            clauses.append("pp.category = ?")
            params.append(category)
        if text:
            clauses.append("(p.text LIKE ? OR n.text LIKE ? OR pp.title LIKE ?)")
            params.extend([f"%{text}%"] * 3)
        for tag in tags or []:
            clauses.append(
                "EXISTS(SELECT 1 FROM prompt_pair_tags pt JOIN tags t "
                "ON t.id=pt.tag_id WHERE pt.pair_id=pp.id AND t.name=?)"
            )
            params.append(tag)
        where = " AND ".join(clauses)
        conn = self.model.get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM prompt_pairs pp "
            "JOIN prompts p ON p.id=pp.positive_prompt_id "
            "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id WHERE " + where,
            params,
        ).fetchone()[0]
        pairs = self._read_pair_rows(
            conn,
            where,
            [*params, limit, offset],
            f" ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
        )
        return {
            "pairs": pairs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(pairs) < total,
        }

    def update_prompt_pair_metadata(
        self,
        pair_id: int,
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Update pair metadata explicitly; None preserves and empty values clear."""
        tags = _validate_metadata(title, category, tags, rating, notes)
        changes = {
            key: value
            for key, value in (
                ("title", title),
                ("category", category),
                ("rating", rating),
                ("notes", notes),
            )
            if value is not None
        }
        if not changes and tags is None:
            return False
        with self._pair_transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM prompt_pairs WHERE id=?", (pair_id,)
            ).fetchone():
                return False
            changes["updated_at"] = utc_now_iso()
            conn.execute(
                "UPDATE prompt_pairs SET "
                + ",".join(f"{key}=?" for key in changes)
                + " WHERE id=?",
                [*changes.values(), pair_id],
            )
            if tags is not None:
                self._sync_pair_tags(conn, pair_id, tags)
            return True

    def archive_prompt_pair(self, pair_id: int, archived: bool = True) -> bool:
        """Hide a reusable pair without changing components or generation history."""
        if type(archived) is not bool:
            raise ValueError("archived must be a boolean")
        with self._pair_transaction() as conn:
            return (
                conn.execute(
                    "UPDATE prompt_pairs SET archived=?,updated_at=? WHERE id=?",
                    (int(archived), utc_now_iso(), pair_id),
                ).rowcount
                > 0
            )

    def delete_prompt_pair(self, pair_id: int) -> bool:
        """Delete unused pair metadata; generated pairs must be archived instead."""
        with self._pair_transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM image_prompt_pairs WHERE pair_id=? LIMIT 1", (pair_id,)
            ).fetchone():
                raise PairDependencyError(
                    "Pair has generated images; archive it instead"
                )
            return (
                conn.execute("DELETE FROM prompt_pairs WHERE id=?", (pair_id,)).rowcount
                > 0
            )

    def record_prompt_pair_use(self, pair_id: int, times: int = 1) -> bool:
        """Count pair uses independently of standalone prompt usage."""
        if type(times) is not int or times < 1:
            raise ValueError("times must be a positive integer")
        with self._pair_transaction() as conn:
            return (
                conn.execute(
                    "UPDATE prompt_pairs SET run_count=run_count+?,last_used_at=? "
                    "WHERE id=?",
                    (times, utc_now_iso(), pair_id),
                ).rowcount
                > 0
            )

    def link_image_to_pair(
        self, pair_id: int, image_id: int, provenance: Dict[str, Any]
    ) -> int:
        """Associate an image with immutable run provenance, idempotently."""
        with self._pair_transaction() as conn:
            return self._link_image_to_pair(conn, pair_id, image_id, provenance)

    def _link_image_to_pair(
        self,
        conn: sqlite3.Connection,
        pair_id: int,
        image_id: int,
        provenance: Dict[str, Any],
    ) -> int:
        """Associate an image with immutable run provenance, idempotently."""
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be an object")
        try:
            encoded = json.dumps(
                provenance, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("provenance must contain JSON values") from error
        error = pair_provenance_error(encoded)
        if error:
            raise ValueError(error)
        pair = conn.execute(
            "SELECT positive_prompt_id FROM prompt_pairs WHERE id=?", (pair_id,)
        ).fetchone()
        image = conn.execute(
            "SELECT prompt_id FROM generated_images WHERE id=?", (image_id,)
        ).fetchone()
        if not pair or not image:
            raise ValueError("Pair and image must exist")
        if pair["positive_prompt_id"] != image["prompt_id"]:
            raise ValueError("Image must belong to the pair's positive prompt")
        conn.execute(
            "INSERT INTO image_prompt_pairs(pair_id,image_id,provenance,created_at) "
            "VALUES(?,?,?,?) ON CONFLICT(pair_id,image_id,provenance) DO NOTHING",
            (pair_id, image_id, encoded, utc_now_iso()),
        )
        return conn.execute(
            "SELECT id FROM image_prompt_pairs "
            "WHERE pair_id=? AND image_id=? AND provenance=?",
            (pair_id, image_id, encoded),
        ).fetchone()["id"]

    def get_image_prompt_pairs(self, image_id: int) -> List[Dict[str, Any]]:
        """Return every recorded pair association with decoded provenance."""
        rows = (
            self.model.get_connection()
            .execute(
                "SELECT * FROM image_prompt_pairs WHERE image_id=? ORDER BY id",
                (image_id,),
            )
            .fetchall()
        )
        return [
            {**dict(row), "provenance": json.loads(row["provenance"])} for row in rows
        ]
