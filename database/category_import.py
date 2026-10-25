"""Atomic replacement of one imported category (#221, #249 System S10).

The LoRA reference import used to delete its whole category and then save
each record in its own transaction, so a failure part way left the previous
collection deleted and the new one partial. ``replace_category_prompts``
does both in one ``BEGIN IMMEDIATE`` transaction: the category's current
prompts (with their tags and image links) are replaced by the staged
records, or, on any error, nothing changes.

A staged record is ``{text, hash, tags, images}``. A record whose hash a
prompt outside the category already has links its images to that prompt
(the legacy import did the same); a second record with the hash of an
earlier one links its images to that one. Neither is a new prompt.
"""

from typing import Any, Dict, List, Optional, Sequence

try:
    from .maintenance import PlanChanged
    from .workflow_documents import documents_of_rows, release_documents
except ImportError:  # pragma: no cover - flat test imports
    from database.maintenance import PlanChanged
    from database.workflow_documents import documents_of_rows, release_documents


class EmptyReplacement(ValueError):
    """The replacement has no record and would remove every current import."""


# Each record's outcome and the result counter it adds to.
_COUNTERS = {"imported": "imported", "linked": "linked", "duplicate": "duplicates"}


def _placeholders(values: Sequence[Any]) -> str:
    return ",".join("?" * len(values))


class CategoryImportOperations:
    """Read the state of an imported category and replace it atomically."""

    def category_import_state(self, category: str) -> Dict[str, Any]:
        """``{ids, paired}``: the category's prompt ids (ascending) and how
        many of them a saved pair uses (those block a replacement)."""
        with self.model.get_connection() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM prompts WHERE category = ? ORDER BY id",
                    (category,),
                ).fetchall()
            ]
            paired = 0
            if ids:
                marks = _placeholders(ids)
                paired = conn.execute(
                    f"SELECT COUNT(*) FROM prompts WHERE id IN ({marks}) AND id IN "
                    "(SELECT positive_prompt_id FROM prompt_pairs UNION "
                    "SELECT negative_prompt_id FROM prompt_pairs)",
                    ids,
                ).fetchone()[0]
        return {"ids": ids, "paired": paired}

    def existing_prompt_hashes(
        self, hashes: Sequence[str], exclude_category: str
    ) -> set:
        """The given hashes a prompt outside ``exclude_category`` already has."""
        found = set()
        wanted = list(dict.fromkeys(hashes))
        with self.model.get_connection() as conn:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                rows = conn.execute(
                    f"SELECT hash FROM prompts WHERE hash IN ({_placeholders(chunk)}) "
                    "AND (category IS NULL OR category != ?)",
                    [*chunk, exclude_category],
                ).fetchall()
                found.update(row[0] for row in rows)
        return found

    def replace_category_prompts(
        self,
        category: str,
        records: List[Dict[str, Any]],
        *,
        expected_ids: Optional[List[int]] = None,
        allow_empty: bool = False,
    ) -> Dict[str, Any]:
        """Replace every prompt of ``category`` with ``records`` in one transaction.

        Raises (and changes nothing): ``PlanChanged`` when ``expected_ids`` is
        given and the category's prompt ids differ from it;
        ``PairDependencyError`` when a saved pair uses one of its prompts;
        ``EmptyReplacement`` when ``records`` is empty while the category has
        prompts, unless ``allow_empty`` (an empty replacement that was
        reviewed). Any other error rolls everything back too.

        Returns ``{removed, imported, linked, duplicates, images, rows}``:
        prompts removed and created, records linked to an existing prompt or
        to an earlier record with the same text, image links added, and per
        record ``{name, outcome}`` (``imported``/``linked``/``duplicate``).
        """
        with self._pair_transaction() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM prompts WHERE category = ? ORDER BY id",
                    (category,),
                ).fetchall()
            ]
            if expected_ids is not None and ids != list(expected_ids):
                raise PlanChanged("The imported prompts changed since the review")
            if not records and ids and not allow_empty:
                raise EmptyReplacement(
                    "Nothing to import; the previous imports were kept"
                )
            self._ensure_prompts_unpaired(conn, ids)
            removed = self._delete_category_rows(conn, ids)
            result = {
                "removed": removed,
                "imported": 0,
                "linked": 0,
                "duplicates": 0,
                "images": 0,
                "rows": [],
            }
            created = {}
            for record in records:
                self._import_record(conn, category, record, created, result)
            return result

    def _delete_category_rows(self, conn, ids: List[int]) -> int:
        """Delete prompts ``ids`` with their image links and tags (no commit)."""
        if not ids:
            return 0
        marks = _placeholders(ids)
        documents = documents_of_rows(conn, "prompt_id", ids)
        conn.execute(f"DELETE FROM generated_images WHERE prompt_id IN ({marks})", ids)
        release_documents(conn, documents)
        conn.execute(f"DELETE FROM prompt_tags WHERE prompt_id IN ({marks})", ids)
        return conn.execute(f"DELETE FROM prompts WHERE id IN ({marks})", ids).rowcount

    def _import_record(self, conn, category, record, created, result) -> None:
        """Save one staged record on ``conn``, or link it to the prompt it repeats."""
        prompt_hash = record["hash"]
        name = record.get("name") or record["text"]
        if prompt_hash in created:
            prompt_id, outcome = created[prompt_hash], "duplicate"
        else:
            row = conn.execute(
                "SELECT id FROM prompts WHERE hash = ?", (prompt_hash,)
            ).fetchone()
            if row is not None:
                prompt_id, outcome = row[0], "linked"
            else:
                prompt_id = self._insert_prompt(
                    conn,
                    record["text"],
                    category,
                    list(record.get("tags") or []),
                    None,
                    None,
                    prompt_hash,
                )
                outcome = "imported"
            created[prompt_hash] = prompt_id
        result[_COUNTERS[outcome]] += 1
        for image in record.get("images") or []:
            if self._insert_image_link(conn, prompt_id, image):
                result["images"] += 1
        result["rows"].append({"name": name, "outcome": outcome})
