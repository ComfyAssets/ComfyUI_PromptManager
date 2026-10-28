"""
Database operations for KikoTextEncode prompt storage and retrieval.
"""

import sqlite3
import json
import math
import datetime
import os
from typing import Optional, List, Dict, Any, Set, Tuple

from .models import PromptModel, normalize_image_path, utc_now_iso
from .pairs import PairDependencyError, PairOperations  # noqa: F401 (re-exported)
from .library import LibraryOperations, titled
from .image_favorites import ImageFavoriteOperations
from .annotations import AnnotationOperations
from .review import ReviewOperations
from .review_decisions import ReviewDecisionOperations
from .saved_views import SavedViewOperations
from .library_commands import LibraryCommandOperations
from .vocabulary import VocabularyOperations
from .execution_history import ExecutionHistoryOperations
from .execution_contexts import ExecutionContextOperations
from .execution_outputs import ExecutionOutputOperations
from .history_reads import HistoryReadOperations
from .health import HealthOperations, iter_consistency_issues
from .duplicate_copies import DuplicateCopyOperations
from .workflow_storage import WorkflowStorageOperations
from .category_import import CategoryImportOperations, EmptyReplacement  # noqa: F401
from .image_categories import ImageCategoryOperations
from .workflow_documents import (
    DocumentReader,
    documents_of_rows,
    release_documents,
    resolve_image_row,
    stored_values,
)
from .maintenance import (  # noqa: F401  (ORPHANED_PROMPTS_SQL is re-exported)
    ORPHANED_PROMPTS_SQL,
    _LINK_ROWS_SQL,
    MaintenanceOperations,
    _delete_ids,
    _orphaned_ids,
)

# Import logging system
try:
    from ..utils.logging_config import get_logger
    from ..utils.hashing import generate_prompt_hash
    from ..utils.json_strict import dumps_strict
    from ..utils import library_location
except ImportError:
    import sys

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger
    from utils.hashing import generate_prompt_hash
    from utils.json_strict import dumps_strict
    from utils import library_location


# Subquery to fetch tags from junction table, embedded in SELECT statements
TAG_SUBQUERY = (
    "(SELECT GROUP_CONCAT(t.name, '|||') "
    "FROM prompt_tags pt JOIN tags t ON pt.tag_id = t.id "
    "WHERE pt.prompt_id = prompts.id) AS _tag_list"
)

# Server-side sort orders, keyed by the value the UI sends. Only these strings ever
# reach ORDER BY; unknown keys fall back to DEFAULT_SORT.
# last_used_at is NULL until the prompt has run, so "IS NULL" first keeps
# never-run prompts after every run one (SQLite sorts NULL first in DESC).
SORT_ORDERS = {
    "last_used_desc": "last_used_at IS NULL, last_used_at DESC, id DESC",
    "created_desc": "created_at DESC, id DESC",
    "created_asc": "created_at ASC, id ASC",
    "run_count_desc": (
        "run_count DESC, last_used_at IS NULL, last_used_at DESC, id DESC"
    ),
    "rating_desc": "rating IS NULL, rating DESC, id DESC",
    "rating_asc": "rating IS NULL, rating ASC, id DESC",
    "text_asc": "text COLLATE NOCASE ASC, id ASC",
    "text_desc": "text COLLATE NOCASE DESC, id DESC",
}
DEFAULT_SORT = "created_desc"


def order_by_clause(sort: Optional[str]) -> str:
    """Return a whitelisted ORDER BY expression for a UI sort key."""
    return SORT_ORDERS.get(sort or DEFAULT_SORT, SORT_ORDERS[DEFAULT_SORT])


LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Escape LIKE wildcards in user text; use with ``LIKE ? ESCAPE '\\'``."""
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def _configured_default_path():
    """config.json's ``database.default_path``, or None.

    The package-qualified config wins over a standalone ``py.config`` (a
    custom-node package uses its own configuration).
    """
    try:
        try:
            from ..py.config import PromptManagerConfig
        except ImportError:
            from py.config import PromptManagerConfig
    except Exception:
        return None
    return getattr(PromptManagerConfig, "DEFAULT_DB_PATH", None)


def resolve_library_location():
    """Where the library lives now (``utils.library_location``, #249 S6b).

    The image sources that rule out a ``PROMPT_MANAGER_DATA_DIR`` are read by
    the resolver itself, exactly as ``config_location`` reads them.
    """
    return library_location.resolve_library(_configured_default_path())


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve the database path through the one library resolver.

    With no explicit path, ``utils.library_location.resolve_library`` decides
    (``PROMPT_MANAGER_DATA_DIR``, an explicit ``database.default_path``, a
    node-folder library not moved yet, else ComfyUI's ``user/__promptmanager``).
    An explicit relative path is resolved against the extension root, as
    before, so the location does not depend on the working directory.

    Args:
        db_path: Explicit database path, or None to use the resolver.

    Returns:
        Absolute path to the SQLite database file; its folder exists (a new
        data folder is created owner-only).
    """
    if db_path is None:
        db_path = resolve_library_location().db_path
    elif not os.path.isabs(db_path):
        extension_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.normpath(os.path.join(extension_root, db_path))

    parent = os.path.dirname(db_path)
    if parent:
        library_location.ensure_private_dir(parent)

    return db_path


# Bounds for the file_info a link request may attach to an image. Anything
# outside them (or of the wrong type) is stored as NULL instead of being
# trusted into an INTEGER or TEXT column and echoed back to the gallery.
MAX_IMAGE_FILE_SIZE = 10**12
MAX_IMAGE_DIMENSION = 65535
IMAGE_FORMATS = frozenset(
    {"PNG", "JPEG", "JPG", "WEBP", "GIF", "BMP", "TIFF", "MP4", "WEBM", "MOV"}
)


def _bounded_int(value: Any, upper: int) -> Optional[int]:
    """``value`` as an int within ``[0, upper]``; None for anything else."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= upper else None


def _known_image_format(value: Any) -> Optional[str]:
    """Upper-cased *value* when it names a supported media format, else None."""
    if not isinstance(value, str):
        return None
    name = value.strip().upper()
    return name if name in IMAGE_FORMATS else None


def _coerce_file_info(metadata: Any) -> tuple:
    """``(size, width, height, format)`` from request metadata, each validated."""
    file_info = metadata.get("file_info") if isinstance(metadata, dict) else None
    if not isinstance(file_info, dict):
        return None, None, None, None
    dimensions = file_info.get("dimensions")
    if not isinstance(dimensions, (list, tuple)) or len(dimensions) < 2:
        dimensions = (None, None)
    return (
        _bounded_int(file_info.get("size"), MAX_IMAGE_FILE_SIZE),
        _bounded_int(dimensions[0], MAX_IMAGE_DIMENSION),
        _bounded_int(dimensions[1], MAX_IMAGE_DIMENSION),
        _known_image_format(file_info.get("format")),
    )


# The scan's known set (#249 Activity-2): every image row, with its stored file
# size and whether its pair link is resolved: exactly one distinct pair, the rule
# _LINK_SUMMARY_SQL and get_image_prompt_info use. A file is known and resolved
# when it has exactly one image row, that row is resolved, and the row's stored
# size equals the file's size now; several rows (one file linked to more than
# one prompt) are ambiguous, and an unknown (NULL) stored size is not known. The
# rows are streamed in fetches of KNOWN_PATHS_FETCH_SIZE and only the scanned
# paths are kept, so memory follows the scan (at most MAX_SCAN_FILES paths), not
# the table.
_KNOWN_PATHS_SQL = """SELECT gi.file_path, gi.image_path, gi.file_size,
       (SELECT COUNT(DISTINCT ip.pair_id) FROM image_prompt_pairs ip
        WHERE ip.image_id = gi.id) = 1
FROM generated_images gi"""
KNOWN_PATHS_FETCH_SIZE = 1000

# One SELECT reads the link summaries of at most this many paths: three bound
# parameters per path stays under SQLite's historical 999-variable limit.
LINK_SUMMARY_BATCH = 200
# A pair link counts only when an image has exactly one distinct pair, as in
# get_image_prompt_info; its positive (text, Library title, category, rating)
# then describes the card. The pair's own title names its negative variant, so
# it is not read (D10).
_LINK_SUMMARY_SQL = """SELECT gi.image_path, gi.file_path, gi.prompt_id,
       p.text, p.category, p.rating, ls.title,
       pp.positive_prompt_id, pt.text, pt.category, pt.rating, pls.title
FROM generated_images gi
JOIN prompts p ON gi.prompt_id = p.id
LEFT JOIN prompt_library_state ls ON ls.prompt_id = p.id
LEFT JOIN prompt_pairs pp ON pp.id = (
    SELECT CASE WHEN COUNT(DISTINCT ip.pair_id) = 1 THEN MIN(ip.pair_id) END
    FROM image_prompt_pairs ip WHERE ip.image_id = gi.id)
LEFT JOIN prompts pt ON pt.id = pp.positive_prompt_id
LEFT JOIN prompt_library_state pls ON pls.prompt_id = pp.positive_prompt_id
WHERE gi.image_path IN ({marks}) OR gi.file_path IN ({marks}){fallback}"""
# One image row describes a card only when no other row claims the same file
# (as in get_image_link_summaries, several rows are ambiguous). The pair scope
# and the linked positive follow _LINK_SUMMARY_SQL; category, rating, tags and
# the searchable text and titles come from that positive (spec D12/D13). Every
# writer since the 3.2.4 migration stores file_path, so it is the path key.
# The prompt-level conditions are uncorrelated IN sets SQLite builds once, and
# the CASE evaluates the per-row ambiguity probe only for admitted rows.
_LINK_FILTER_SQL = """SELECT gi.file_path AS file_path, p.rating AS rating,
       {hit} AS matched, p.id AS positive
FROM generated_images gi
LEFT JOIN prompt_pairs pp ON pp.id = (
    SELECT CASE WHEN COUNT(DISTINCT ip.pair_id) = 1 THEN MIN(ip.pair_id) END
    FROM image_prompt_pairs ip WHERE ip.image_id = gi.id)
JOIN prompts p ON p.id = COALESCE(pp.positive_prompt_id, gi.prompt_id)
WHERE gi.file_path IS NOT NULL AND CASE WHEN {admit} THEN NOT EXISTS (
    SELECT 1 FROM generated_images other
    WHERE other.file_path = gi.file_path AND other.id <> gi.id) ELSE 0 END"""
# _LINK_FILTER_SQL's rows as one JSON array, built by SQLite in one step (#354).
# Python's sqlite3 releases and retakes the GIL around every step, so a read
# that steps once per linked row waits on a busy ComfyUI thread thousands of
# times. A BLOB file_path or rating (no writer stores one) makes the value NULL
# (or json_array refuses it), and the read streams instead.
_LINK_MATCHES_JSON_SQL = """SELECT CASE
    WHEN max(typeof(file_path) = 'blob' OR typeof(rating) = 'blob') THEN NULL
    ELSE json_group_array(json_array(file_path, rating, matched, positive)) END
FROM ({select})"""
# Set when this SQLite has no JSON functions: every later read streams.
_one_step_unsupported = False
# One warning per process when a read falls back to streaming.
_one_step_warned = False
# The linked positive of each given path (one unambiguous link, pair scope as
# in _LINK_FILTER_SQL) with its category; {hit} as in link_filter_query. Used
# for the few files whose current image tags meet a Tags filter (#249 B3).
_LINK_FACTS_SQL = """SELECT gi.file_path, p.rating, {hit}, p.id, p.category
FROM generated_images gi
LEFT JOIN prompt_pairs pp ON pp.id = (
    SELECT CASE WHEN COUNT(DISTINCT ip.pair_id) = 1 THEN MIN(ip.pair_id) END
    FROM image_prompt_pairs ip WHERE ip.image_id = gi.id)
JOIN prompts p ON p.id = COALESCE(pp.positive_prompt_id, gi.prompt_id)
WHERE gi.file_path IN ({marks}) AND NOT EXISTS (
    SELECT 1 FROM generated_images other
    WHERE other.file_path = gi.file_path AND other.id <> gi.id)"""
_POSITIVE_TAG_NAMES_SQL = (
    "SELECT pt.prompt_id, t.name FROM prompt_tags pt JOIN tags t "
    "ON t.id = pt.tag_id WHERE pt.prompt_id IN ({marks})"
)
_FACTS_CHUNK = 500
# The positive's text, its Library title or one of its tag names contains
# :pattern (LIKE, so ASCII case is ignored as in Library search). A pair's own
# title names its negative variant and is never searched (#249 D12).
_LINK_FILTER_HIT = """p.id IN (
    SELECT hp.id FROM prompts hp
    LEFT JOIN prompt_library_state hls ON hls.prompt_id = hp.id
    WHERE hp.text LIKE :pattern ESCAPE '\\' OR hls.title LIKE :pattern ESCAPE '\\'
    OR hp.id IN (SELECT ht.prompt_id FROM prompt_tags ht JOIN tags hn
                 ON hn.id = ht.tag_id WHERE hn.name LIKE :pattern ESCAPE '\\'))"""
_LINK_FILTER_TAG = (
    "p.id IN (SELECT ft.prompt_id FROM prompt_tags ft JOIN tags fn "
    "ON fn.id = ft.tag_id WHERE fn.name = :tag{index})"
)


def link_filter_query(
    q: str, category: Optional[str], tags: List[str], *, every_link: bool = False
) -> Tuple[str, Dict[str, Any]]:
    """The workspace filter SELECT and its named parameters (all bound).

    Only code constants and enumerate indices are formatted into the SQL; see
    PromptDatabase.get_image_link_matches for the semantics.
    """
    params: Dict[str, Any] = {}
    hit = "1"
    if q:
        hit = _LINK_FILTER_HIT
        params["pattern"] = f"%{escape_like(q)}%"
    admitted = []
    if category is not None:
        admitted.append("p.category = :category")
        params["category"] = category
    for index, tag in enumerate(tags):
        admitted.append(_LINK_FILTER_TAG.format(index=index))
        params[f"tag{index}"] = tag
    if q and not admitted and not every_link:
        admitted.append(hit)
    sql = _LINK_FILTER_SQL.format(hit=hit, admit=" AND ".join(admitted) or "1")
    return sql, params


def link_matches_json_query(sql: str) -> str:
    """*sql* from link_filter_query, wrapped to return its rows as one JSON value."""
    return _LINK_MATCHES_JSON_SQL.format(select=sql)


def _streamed_link_matches(
    conn: sqlite3.Connection,
    sql: str,
    params: Dict[str, Any],
    wanted: Optional[Set[str]],
) -> Dict[str, Tuple[bool, Optional[int], int]]:
    """The link read stepped once per row (the fallback of the one-step read)."""
    matches: Dict[str, Tuple[bool, Optional[int], int]] = {}
    for path, rating, matched, positive in conn.execute(sql, params):
        if wanted is None or path in wanted:
            matches[path] = (bool(matched), rating, positive)
    return matches


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _like_suffix(value: object, suffix: str) -> bool:
    """Python form of SQLite's ``value LIKE '%' || suffix``: ASCII-only case."""
    return isinstance(value, str) and value.translate(_ASCII_FOLD).endswith(
        suffix.translate(_ASCII_FOLD)
    )


def _empty_link_summary(status: str) -> Dict[str, Any]:
    return {
        "status": status,
        "positive_id": None,
        "title": None,
        "title_source": None,
        "category": None,
        "rating": None,
    }


def _link_summary(row: tuple) -> Dict[str, Any]:
    if row[7] is not None:
        positive, text, category, rating = row[7], row[8], row[9], row[10]
        # The linked positive's Library title (D10/D20).
        titles = (row[11],)
    else:
        positive, text, category, rating = row[2], row[3], row[4], row[5]
        titles = (row[6],)
    title, source = titled(titles, text)
    return {
        "status": "linked",
        "positive_id": positive,
        "title": title,
        "title_source": source,
        "category": category,
        "rating": rating,
    }


class PromptDatabase(
    PairOperations,
    LibraryOperations,
    LibraryCommandOperations,
    VocabularyOperations,
    ExecutionHistoryOperations,
    ExecutionContextOperations,
    ExecutionOutputOperations,
    HistoryReadOperations,
    ImageFavoriteOperations,
    AnnotationOperations,
    ReviewOperations,
    ReviewDecisionOperations,
    SavedViewOperations,
    HealthOperations,
    MaintenanceOperations,
    DuplicateCopyOperations,
    WorkflowStorageOperations,
    CategoryImportOperations,
    ImageCategoryOperations,
):
    """Database operations class for managing prompts."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the database operations.

        Args:
            db_path: Path to the SQLite database file. If None, resolves from
                     config.json or defaults to prompts.db in the extension root.
        """
        self.logger = get_logger("prompt_manager.database")
        db_path = _resolve_db_path(db_path)
        self.logger.debug(f"Initializing database operations with path: {db_path}")
        self.model = PromptModel(db_path)
        self.logger.debug("Database operations initialized successfully")

    def survive_library_switch(self, write):
        """Run ``write()``; once more if it failed while the library moved.

        A background write (run counting, the image monitor) that was waiting
        on the old file when Move library switched fails when its connection
        is closed. When the model's generation changed during the attempt,
        the write runs again on a fresh connection, which is the new library;
        any other failure is raised as it was. ``write`` must open its own
        transaction (every PromptDatabase method does).
        """
        generation = self.model.generation
        try:
            return write()
        except Exception:
            if self.model.generation == generation:
                raise
            self.logger.info("Running a write again on the moved library")
            return write()

    def close(self) -> None:
        """Close the calling thread's database connection (see PromptModel.close)."""
        self.model.close()

    @staticmethod
    def close_all_instances() -> None:
        """Close the connections of every PromptDatabase/PromptModel in the process."""
        PromptModel.close_all_instances()

    def close_all(self) -> None:
        """Close every thread's connection (see PromptModel.close_all)."""
        self.model.close_all()

    def save_prompt(
        self,
        text: str,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
        prompt_hash: Optional[str] = None,
    ) -> int:
        """
        Save a new prompt to the database.

        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags
            rating: Rating 1-5
            notes: Optional notes
            prompt_hash: SHA256 hash of the prompt

        Returns:
            int: The ID of the saved prompt

        Raises:
            ValueError: If required parameters are invalid
            sqlite3.Error: If database operation fails
        """
        if not text or not text.strip():
            raise ValueError("Prompt text cannot be empty")

        if rating is not None and (rating < 1 or rating > 5):
            raise ValueError("Rating must be between 1 and 5")

        self.logger.debug(
            f"Saving prompt: text_length={len(text)}, category={category}, "
            f"tags={tags}, rating={rating}"
        )

        try:
            with self.model.get_connection() as conn:
                prompt_id = self._insert_prompt(
                    conn, text, category, tags, rating, notes, prompt_hash
                )
                conn.commit()
                self.logger.debug(f"Successfully saved prompt with ID: {prompt_id}")
                return prompt_id
        except sqlite3.IntegrityError:
            # Another caller inserted the same hash between the caller's
            # duplicate check and this INSERT (the with-block rolled back).
            existing = self.get_prompt_by_hash(prompt_hash) if prompt_hash else None
            if existing is None:
                raise
            self.logger.debug(
                f"Prompt with hash {prompt_hash} was saved concurrently; "
                f"returning existing ID {existing['id']}"
            )
            return existing["id"]

    def _insert_prompt(
        self,
        conn: sqlite3.Connection,
        text: str,
        category: Optional[str],
        tags: Optional[List[str]],
        rating: Optional[int],
        notes: Optional[str],
        prompt_hash: Optional[str],
    ) -> int:
        """Insert one prompt row and its tags on ``conn`` (no commit)."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO prompts (
                text, category, tags, rating, notes, hash, created_at,
                updated_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (text.strip(), category, rating, notes, prompt_hash, now, now),
        )
        prompt_id = cursor.lastrowid
        if tags:
            self._sync_prompt_tags(conn, prompt_id, tags)
        return prompt_id

    def record_prompt_use(self, prompt_id: int, times: int = 1) -> bool:
        """
        Count runs of a prompt and mark it as most recently used.

        Args:
            prompt_id: The prompt ID
            times: Number of runs to add (queued runs counted on first save)

        Returns:
            True if the prompt exists and was updated
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE prompts SET run_count = run_count + ?, last_used_at = ?"
                " WHERE id = ?",
                (max(1, int(times)), utc_now_iso(), prompt_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_prompt_by_id(self, prompt_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a prompt by its ID.

        Args:
            prompt_id: The prompt ID

        Returns:
            Dict containing prompt data or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE id = ?",
                (prompt_id,),
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def get_prompt_by_hash(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get a prompt by its hash.

        Args:
            prompt_hash: SHA256 hash of the prompt

        Returns:
            Dict containing prompt data or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE hash = ?",
                (prompt_hash,),
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def search_prompts(
        self,
        text: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating_min: Optional[int] = None,
        rating_max: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        folder: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        tag_partial: bool = False,
        sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search prompts with various filters.

        Args:
            text: Text to search for in prompt content
            category: Filter by category
            tags: Filter by tags (must contain all specified tags)
            rating_min: Minimum rating filter
            rating_max: Maximum rating filter
            date_from: Start date filter (ISO format)
            date_to: End date filter (ISO format)
            folder: Filter by subfolder name in generated image paths
            limit: Maximum number of results
            offset: Number of results to skip
            tag_partial: Use LIKE matching for tags instead of exact match
            sort: Sort key from SORT_ORDERS (default: newest first)

        Returns:
            List of dictionaries containing prompt data
        """
        query_parts = [f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE 1=1"]
        params = []

        if text:
            query_parts.append("AND text LIKE ? ESCAPE '\\'")
            params.append(f"%{escape_like(text)}%")

        if category:
            query_parts.append("AND category = ?")
            params.append(category)

        if tags:
            for tag in tags:
                if tag_partial:
                    query_parts.append(
                        "AND prompts.id IN ("
                        "  SELECT pt.prompt_id FROM prompt_tags pt"
                        "  JOIN tags t ON pt.tag_id = t.id"
                        "  WHERE t.name LIKE ? ESCAPE '\\')"
                    )
                    params.append(f"%{escape_like(tag)}%")
                else:
                    query_parts.append(
                        "AND prompts.id IN ("
                        "  SELECT pt.prompt_id FROM prompt_tags pt"
                        "  JOIN tags t ON pt.tag_id = t.id WHERE t.name = ?)"
                    )
                    params.append(tag)

        if folder:
            # Escape LIKE wildcards in the folder name
            safe_folder = (
                folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query_parts.append(
                "AND prompts.id IN ("
                "  SELECT DISTINCT prompt_id FROM generated_images"
                "  WHERE image_path LIKE ? ESCAPE '\\'"
                "  OR image_path LIKE ? ESCAPE '\\'"
                "  OR image_path LIKE ? ESCAPE '\\'"
                ")"
            )
            # Pattern 1: folder between forward slashes (Unix + normalized Windows)
            params.append(f"%/{safe_folder}/%")
            # Pattern 2: folder between backslashes (Windows native)
            params.append(f"%\\\\{safe_folder}\\\\%")
            # Pattern 3: folder at start of a relative path
            params.append(f"{safe_folder}/%")

        if rating_min is not None:
            query_parts.append("AND rating >= ?")
            params.append(rating_min)

        if rating_max is not None:
            query_parts.append("AND rating <= ?")
            params.append(rating_max)

        if date_from:
            query_parts.append("AND created_at >= ?")
            params.append(date_from)

        if date_to:
            query_parts.append("AND created_at <= ?")
            params.append(date_to)

        query_parts.append(f"ORDER BY {order_by_clause(sort)} LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        query = " ".join(query_parts)

        with self.model.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            prompts = [self._row_to_dict(row) for row in rows]

            if prompts:
                prompt_ids = [p["id"] for p in prompts]
                self._attach_preview_images(conn, prompts, prompt_ids)

            return prompts

    def get_recent_prompts(
        self, limit: int = 10, offset: int = 0, sort: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get the most recent prompts with pagination support.

        Args:
            limit: Maximum number of prompts to return
            offset: Number of prompts to skip (for pagination)
            sort: Sort key from SORT_ORDERS (default: newest first)

        Returns:
            Dictionary containing prompt data and pagination info
        """
        with self.model.get_connection() as conn:
            # Get total count
            cursor = conn.execute("SELECT COUNT(*) FROM prompts")
            row = cursor.fetchone()
            total_count = (row[0] if row else 0) or 0

            # Get paginated results
            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts"
                f" ORDER BY {order_by_clause(sort)} LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = cursor.fetchall()
            prompts = [self._row_to_dict(row) for row in rows]

            if prompts:
                prompt_ids = [p["id"] for p in prompts]
                self._attach_preview_images(conn, prompts, prompt_ids)

            return {
                "prompts": prompts,
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total_count,
                "page": (offset // limit) + 1,
                "total_pages": (total_count + limit - 1) // limit,  # Ceiling division
            }

    def get_prompts_by_category(
        self, category: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all prompts in a specific category.

        Args:
            category: The category name
            limit: Maximum number of prompts to return

        Returns:
            List of dictionaries containing prompt data
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE category = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    def get_top_rated_prompts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the highest rated prompts.

        Args:
            limit: Maximum number of prompts to return

        Returns:
            List of dictionaries containing prompt data
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT prompts.*, {TAG_SUBQUERY} FROM prompts
                WHERE rating IS NOT NULL
                ORDER BY rating DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    def update_prompt_metadata(
        self,
        prompt_id: int,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Update metadata for an existing prompt.

        Args:
            prompt_id: The prompt ID
            category: New category
            tags: New tags list
            rating: New rating
            notes: New notes

        Returns:
            bool: True if update was successful, False otherwise
        """
        updates = []
        params = []

        if category is not None:
            updates.append("category = ?")
            params.append(category)

        if rating is not None:
            if rating < 1 or rating > 5:
                raise ValueError("Rating must be between 1 and 5")
            updates.append("rating = ?")
            params.append(rating)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if not updates and tags is None:
            return False

        with self.model.get_connection() as conn:
            if updates:
                updates.append("updated_at = ?")
                params.append(datetime.datetime.now(datetime.timezone.utc).isoformat())
                params.append(prompt_id)
                query = f"UPDATE prompts SET {', '.join(updates)} WHERE id = ?"
                conn.execute(query, params)
            if tags is not None:
                self._sync_prompt_tags(conn, prompt_id, tags)
                conn.execute(
                    "UPDATE prompts SET updated_at = ? WHERE id = ?",
                    (
                        datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        prompt_id,
                    ),
                )
            conn.commit()
            return True

    def delete_prompt(self, prompt_id: int) -> bool:
        """
        Delete a prompt by its ID.

        Args:
            prompt_id: The prompt ID to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        with self._pair_transaction() as conn:
            self._ensure_prompts_unpaired(conn, [prompt_id])
            documents = documents_of_rows(conn, "prompt_id", [prompt_id])
            # First delete related images to avoid foreign key constraint
            conn.execute(
                "DELETE FROM generated_images WHERE prompt_id = ?", (prompt_id,)
            )
            release_documents(conn, documents)
            # Then delete the prompt
            cursor = conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_prompts_by_category(self, category: str) -> int:
        """Delete all prompts with the given category.

        Returns:
            Number of prompts deleted.
        """
        with self._pair_transaction() as conn:
            # Get IDs first for cascade cleanup
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM prompts WHERE category = ?", (category,)
                ).fetchall()
            ]
            if not ids:
                return 0
            self._ensure_prompts_unpaired(conn, ids)
            placeholders = ",".join("?" * len(ids))
            documents = documents_of_rows(conn, "prompt_id", ids)
            conn.execute(
                f"DELETE FROM generated_images WHERE prompt_id IN ({placeholders})",
                ids,
            )
            release_documents(conn, documents)
            conn.execute(
                f"DELETE FROM prompt_tags WHERE prompt_id IN ({placeholders})",
                ids,
            )
            cursor = conn.execute(
                f"DELETE FROM prompts WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
            return cursor.rowcount

    def get_all_categories(self) -> List[str]:
        """
        Get all unique categories from the database.

        Returns:
            List of category names
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT TRIM(category) AS category FROM prompts WHERE "
                "category IS NOT NULL AND TRIM(category) != '' UNION "
                "SELECT TRIM(category) AS category FROM prompt_pairs WHERE "
                "category IS NOT NULL AND TRIM(category) != '' ORDER BY category"
            )
            return [row["category"] for row in cursor.fetchall()]

    def get_prompt_subfolders(
        self,
        root_dirs: Optional[List[str]] = None,
        include_ancestors: bool = False,
    ) -> List[str]:
        """
        Get distinct subfolder paths from generated_images.

        Extracts the directory portion of image_path, made relative to
        root_dirs if provided. Returns sorted unique folder names.

        Args:
            root_dirs: Gallery root directories for computing relative paths
            include_ancestors: If True, also include all ancestor path segments
                so that hierarchical tree navigation works. For example, a path
                ``2026/08-Aug/2026-08-06`` also adds ``2026`` and ``2026/08-Aug``.
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT image_path FROM generated_images "
                "WHERE image_path IS NOT NULL AND image_path != ''"
            )

            folders = set()
            for row in cursor.fetchall():
                image_path = row["image_path"]
                parent = os.path.dirname(image_path)
                if not parent:
                    continue

                made_relative = False
                if root_dirs:
                    for root in root_dirs:
                        try:
                            rel = os.path.relpath(parent, root)
                            if not rel.startswith(".."):
                                if rel != ".":
                                    # Clients split and filter on "/" whatever the OS
                                    folders.add(rel.replace(os.sep, "/"))
                                made_relative = True
                                break
                        except ValueError:
                            continue

                if not made_relative:
                    # Preserve full relative-style path instead of collapsing
                    # to just the basename, which loses hierarchy and creates
                    # ambiguity (e.g. foo/bar and baz/bar both become "bar").
                    folders.add(parent.replace(os.sep, "/"))

        if include_ancestors:
            ancestors = set()
            for folder in folders:
                parts = folder.replace("\\", "/").split("/")
                for i in range(1, len(parts)):
                    ancestors.add("/".join(parts[:i]))
            folders.update(ancestors)

        return sorted(folders)

    def get_all_tags(self) -> List[str]:
        """
        Get every tag in the shared vocabulary, including unused registry rows.

        Returns:
            Sorted list of tag names
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT t.name AS tag FROM tags t ORDER BY t.name")
            return [row["tag"] for row in cursor.fetchall()]

    def get_tags_with_counts(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        sort: str = "alpha_asc",
    ) -> Dict[str, Any]:
        """
        Get all unique tags with their usage counts via junction table.

        Args:
            limit: Maximum number of tags to return
            offset: Number of tags to skip
            search: Optional case-insensitive substring filter
            sort: Sort order - alpha_asc, alpha_desc, count_desc, count_asc

        Returns:
            Dict with tags list, total count, and pagination info
        """
        search_clause = ""
        params: list = []
        if search:
            search_clause = "HAVING t.name LIKE ? ESCAPE '\\'"
            params.append(f"%{escape_like(search)}%")

        sort_map = {
            "alpha_desc": "tag COLLATE NOCASE DESC",
            "count_desc": "count DESC, tag COLLATE NOCASE ASC",
            "count_asc": "count ASC, tag COLLATE NOCASE ASC",
        }
        order = sort_map.get(sort, "tag COLLATE NOCASE ASC")

        with self.model.get_connection() as conn:
            count_sql = (
                "SELECT COUNT(*) as total FROM ("
                "  SELECT t.name AS tag, COUNT(*) AS count"
                "  FROM prompt_tags pt JOIN tags t ON pt.tag_id = t.id"
                f"  GROUP BY t.name {search_clause}"
                ")"
            )
            cursor = conn.execute(count_sql, params)
            row = cursor.fetchone()
            total = (row[0] if row else 0) or 0

            data_sql = (
                "SELECT t.name AS tag, COUNT(*) AS count"
                " FROM prompt_tags pt JOIN tags t ON pt.tag_id = t.id"
                f" GROUP BY t.name {search_clause}"
                f" ORDER BY {order}"
                " LIMIT ? OFFSET ?"
            )
            cursor = conn.execute(data_sql, params + [limit, offset])
            tags = [
                {"name": row["tag"], "count": row["count"]} for row in cursor.fetchall()
            ]

        return {
            "tags": tags,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    def suggest_tags(
        self, prefix: str, with_tags: List[str], limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Tag autocomplete narrowed by co-occurrence.

        Returns tags whose name starts with ``prefix`` (case-insensitive),
        counted over the prompts that carry every tag in ``with_tags``; the
        context tags themselves are left out. Counts include separate standalone
        and pair references, with owner types kept distinct. Ordered by count/name.
        Without context, unused vocabulary rows are included last with count 0;
        with context they never appear.
        """
        seen = set()
        context = []
        for raw in with_tags or []:
            name = str(raw).strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                context.append(name)

        where = ["t.name LIKE ? ESCAPE '\\'"]
        params: list = [f"{escape_like(prefix or '')}%"]
        if context:
            placeholders = ",".join("?" * len(context))
            where.append(
                "(pt.kind,pt.owner_id) IN ("
                " SELECT pt2.kind,pt2.owner_id FROM tag_refs pt2"
                " JOIN tags t2 ON t2.id = pt2.tag_id"
                f" WHERE t2.name COLLATE NOCASE IN ({placeholders})"
                " GROUP BY pt2.kind,pt2.owner_id"
                " HAVING COUNT(DISTINCT lower(t2.name)) = ?)"
            )
            params.extend(context)
            params.append(len(context))
            where.append(f"t.name COLLATE NOCASE NOT IN ({placeholders})")
            params.extend(context)
        params.append(max(1, int(limit)))

        sql = (
            "WITH tag_refs(kind,owner_id,tag_id) AS ("
            "SELECT 'prompt',prompt_id,tag_id FROM prompt_tags UNION ALL "
            "SELECT 'pair',pair_id,tag_id FROM prompt_pair_tags) "
            "SELECT t.name AS name, COUNT(pt.tag_id) AS count"
            " FROM tags t LEFT JOIN tag_refs pt ON t.id = pt.tag_id"
            f" WHERE {' AND '.join(where)}"
            " GROUP BY t.id"
            " ORDER BY count DESC, t.name COLLATE NOCASE ASC"
            " LIMIT ?"
        )
        with self.model.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{"name": row["name"], "count": row["count"]} for row in rows]

    def get_prompts_by_tags(
        self, tags: List[str], mode: str = "and", limit: int = 20, offset: int = 0
    ) -> Dict[str, Any]:
        """
        Get prompts that match the given tags with AND/OR filtering.

        Uses junction table for precise tag matching and a batched image
        fetch to avoid N+1 queries.

        Args:
            tags: List of tag names to filter by
            mode: 'and' (must have all tags) or 'or' (must have any tag)
            limit: Maximum number of prompts to return
            offset: Number of prompts to skip

        Returns:
            Dict with prompts list (including preview images), total count, pagination
        """
        if not tags:
            return {
                "prompts": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False,
            }

        with self.model.get_connection() as conn:
            placeholders = ",".join(["?"] * len(tags))
            if mode == "and":
                where_clause = (
                    "prompts.id IN ("
                    "  SELECT pt.prompt_id FROM prompt_tags pt"
                    "  JOIN tags t ON pt.tag_id = t.id"
                    f"  WHERE t.name IN ({placeholders})"
                    "  GROUP BY pt.prompt_id"
                    f"  HAVING COUNT(DISTINCT t.name) = ?"
                    ")"
                )
                tag_params = list(tags) + [len(tags)]
            else:
                where_clause = (
                    "prompts.id IN ("
                    "  SELECT pt.prompt_id FROM prompt_tags pt"
                    "  JOIN tags t ON pt.tag_id = t.id"
                    f"  WHERE t.name IN ({placeholders})"
                    ")"
                )
                tag_params = list(tags)

            cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompts WHERE {where_clause}",
                tag_params,
            )
            row = cursor.fetchone()
            total = (row[0] if row else 0) or 0

            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE {where_clause} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                tag_params + [limit, offset],
            )
            rows = cursor.fetchall()
            prompts = [self._row_to_dict(row) for row in rows]

            if prompts:
                prompt_ids = [p["id"] for p in prompts]
                self._attach_preview_images(conn, prompts, prompt_ids)

            return {
                "prompts": prompts,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            }

    @staticmethod
    def _bump_image_tag_bytes(conn: sqlite3.Connection, tag_id: int) -> int:
        """Move the tag revision of every image set that carries *tag_id*.

        Vocabulary rename, merge and delete change what an image's set reads
        as, so a Tag dialog opened before the change must see ``tag-conflict``
        on Save instead of recreating the old name. Bumps the image index
        revision once when any set was affected; returns that count.
        """
        cursor = conn.execute(
            "UPDATE media_annotation_bytes SET tag_revision = tag_revision + 1 "
            "WHERE id IN (SELECT byte_id FROM media_image_tags WHERE tag_id = ?)",
            (tag_id,),
        )
        if cursor.rowcount > 0:
            conn.execute(
                "UPDATE media_annotation_metadata SET "
                "image_index_revision = image_index_revision + 1 WHERE singleton_id = 1"
            )
        return cursor.rowcount

    @classmethod
    def _move_tag_links(
        cls, conn: sqlite3.Connection, source_id: int, target_id: int
    ) -> None:
        """Repoint component, pair and image links from source to target.

        Owners that already carry the target keep that one link; the source's
        leftover duplicates are removed (an image set keeps the target's
        position). The source registry row is left for the caller to delete.
        Image sets that carried the source get a new tag revision first.
        """
        cls._bump_image_tag_bytes(conn, source_id)
        for junction in ("prompt_tags", "prompt_pair_tags", "media_image_tags"):
            conn.execute(
                f"UPDATE OR IGNORE {junction} SET tag_id = ? WHERE tag_id = ?",
                (target_id, source_id),
            )
            conn.execute(f"DELETE FROM {junction} WHERE tag_id = ?", (source_id,))

    def rename_tag_all_prompts(self, old_name: str, new_name: str) -> Dict[str, Any]:
        """
        Rename a tag across all prompts that use it.

        With junction tables this is a single UPDATE or a merge operation.

        Args:
            old_name: Current tag name
            new_name: New tag name

        Returns:
            Dict with success status and affected_count
        """
        if not old_name or not old_name.strip():
            raise ValueError("Old tag name cannot be empty")
        if not new_name or not new_name.strip():
            raise ValueError("New tag name cannot be empty")

        old_name = old_name.strip()
        new_name = new_name.strip()
        if old_name == new_name:
            return {"success": True, "affected_count": 0, "skipped_count": 0}

        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (old_name,))
            old_tag = cursor.fetchone()
            if not old_tag:
                return {"success": True, "affected_count": 0, "skipped_count": 0}

            old_tag_id = old_tag["id"]

            # Count affected prompts before the operation
            cursor = conn.execute(
                "SELECT COUNT(*) as c FROM prompt_tags WHERE tag_id = ?", (old_tag_id,)
            )
            affected = cursor.fetchone()["c"]

            cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (new_name,))
            existing_new = cursor.fetchone()

            if existing_new:
                # Target tag exists — merge: move links, handle conflicts, delete old
                self._move_tag_links(conn, old_tag_id, existing_new["id"])
                conn.execute("DELETE FROM tags WHERE id = ?", (old_tag_id,))
            else:
                # Simple rename
                self._bump_image_tag_bytes(conn, old_tag_id)
                conn.execute(
                    "UPDATE tags SET name = ? WHERE id = ?", (new_name, old_tag_id)
                )

            conn.commit()

        self.logger.info(
            f"Renamed tag '{old_name}' -> '{new_name}' in {affected} prompts"
        )
        return {"success": True, "affected_count": affected, "skipped_count": 0}

    def delete_tag_all_prompts(self, tag_name: str) -> Dict[str, Any]:
        """
        Remove a tag from all prompts that use it.

        Deleting from tags cascades to standalone and pair tag relationships.

        Args:
            tag_name: Tag name to remove

        Returns:
            Dict with success status and affected_count
        """
        if not tag_name or not tag_name.strip():
            raise ValueError("Tag name cannot be empty")

        tag_name = tag_name.strip()

        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
            tag_row = cursor.fetchone()
            if not tag_row:
                return {"success": True, "affected_count": 0, "skipped_count": 0}

            tag_id = tag_row["id"]
            cursor = conn.execute(
                "SELECT COUNT(*) as c FROM prompt_tags WHERE tag_id = ?", (tag_id,)
            )
            affected = cursor.fetchone()["c"]

            # CASCADE delete handles prompt_tags entries
            self._bump_image_tag_bytes(conn, tag_id)
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()

        self.logger.info(f"Deleted tag '{tag_name}' from {affected} prompts")
        return {"success": True, "affected_count": affected, "skipped_count": 0}

    def merge_tags(self, source_tags: List[str], target_tag: str) -> Dict[str, Any]:
        """
        Merge one or more source tags into a target tag across all prompts.

        With junction tables, this is UPDATE + DELETE per source tag.

        Args:
            source_tags: List of tag names to merge from
            target_tag: Tag name to merge into

        Returns:
            Dict with success status, affected_count, and tags_merged
        """
        if not source_tags:
            raise ValueError("Source tags list cannot be empty")
        if not target_tag or not target_tag.strip():
            raise ValueError("Target tag cannot be empty")

        target_tag = target_tag.strip()
        source_tags = [t.strip() for t in source_tags if t.strip()]
        affected = 0
        tags_merged = 0

        with self.model.get_connection() as conn:
            # Ensure target tag exists
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (target_tag,))
            cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (target_tag,))
            target_id = cursor.fetchone()["id"]

            for src_tag in source_tags:
                cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (src_tag,))
                src_row = cursor.fetchone()
                if not src_row:
                    continue
                src_id = src_row["id"]
                if src_id == target_id:
                    continue

                cursor = conn.execute(
                    "SELECT COUNT(*) as c FROM prompt_tags WHERE tag_id = ?", (src_id,)
                )
                src_count = cursor.fetchone()["c"]

                pair_count = conn.execute(
                    "SELECT COUNT(*) FROM prompt_pair_tags WHERE tag_id = ?", (src_id,)
                ).fetchone()[0]
                image_count = conn.execute(
                    "SELECT COUNT(*) FROM media_image_tags WHERE tag_id = ?", (src_id,)
                ).fetchone()[0]
                if src_count or pair_count or image_count:
                    self._move_tag_links(conn, src_id, target_id)
                    tags_merged += 1
                    affected += src_count

                # Delete the source tag
                conn.execute("DELETE FROM tags WHERE id = ?", (src_id,))

            conn.commit()

        self.logger.info(
            f"Merged {tags_merged} tags into '{target_tag}', affected {affected} "
            "prompts"
        )
        return {
            "success": True,
            "affected_count": affected,
            "tags_merged": tags_merged,
            "skipped_count": 0,
        }

    def get_untagged_prompts_count(self) -> int:
        """
        Get the count of prompts that have no tags.

        Returns:
            Number of untagged prompts
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as total FROM prompts "
                "WHERE NOT EXISTS (SELECT 1 FROM prompt_tags WHERE prompt_id = "
                "prompts.id)"
            )
            return cursor.fetchone()["total"]

    def get_untagged_prompts(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """
        Get prompts that have no tags, with pagination.

        Uses batched image fetch to avoid N+1 queries.

        Args:
            limit: Maximum number of prompts to return
            offset: Number of prompts to skip

        Returns:
            Dict with prompts list, total count, and pagination
        """
        with self.model.get_connection() as conn:
            where = (
                "NOT EXISTS (SELECT 1 FROM prompt_tags WHERE prompt_id = prompts.id)"
            )

            cursor = conn.execute(
                f"SELECT COUNT(*) as total FROM prompts WHERE {where}"
            )
            total = cursor.fetchone()["total"]

            cursor = conn.execute(
                f"SELECT prompts.*, {TAG_SUBQUERY} FROM prompts WHERE {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = cursor.fetchall()
            prompts = [self._row_to_dict(row) for row in rows]

            if prompts:
                prompt_ids = [p["id"] for p in prompts]
                self._attach_preview_images(conn, prompts, prompt_ids)

            return {
                "prompts": prompts,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            }

    def _attach_preview_images(
        self,
        conn: sqlite3.Connection,
        prompts: List[Dict[str, Any]],
        prompt_ids: List[int],
    ) -> None:
        """Batch-fetch up to 3 preview images + counts for a list of prompts."""
        id_placeholders = ",".join(["?"] * len(prompt_ids))

        img_cursor = conn.execute(
            f"""SELECT * FROM (
                  SELECT id, prompt_id, image_path, filename,
                         generation_time, width, height, format,
                         ROW_NUMBER() OVER (
                           PARTITION BY prompt_id ORDER BY generation_time DESC
                         ) AS rn
                  FROM generated_images
                  WHERE prompt_id IN ({id_placeholders})
                ) WHERE rn <= 3""",
            prompt_ids,
        )
        images_by_prompt: Dict[int, list] = {}
        for img_row in img_cursor.fetchall():
            pid = img_row["prompt_id"]
            images_by_prompt.setdefault(pid, []).append(dict(img_row))

        cnt_cursor = conn.execute(
            f"SELECT prompt_id, COUNT(*) as cnt FROM generated_images "
            f"WHERE prompt_id IN ({id_placeholders}) GROUP BY prompt_id",
            prompt_ids,
        )
        counts_by_prompt = {
            row["prompt_id"]: row["cnt"] for row in cnt_cursor.fetchall()
        }

        for prompt in prompts:
            pid = prompt["id"]
            prompt["images"] = images_by_prompt.get(pid, [])
            prompt["image_count"] = counts_by_prompt.get(pid, 0)

    def _ensure_tags(
        self, conn: sqlite3.Connection, tag_names: List[str]
    ) -> Dict[str, int]:
        """Ensure tag names exist in tags table, return name->id mapping."""
        if not tag_names:
            return {}
        for name in tag_names:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        placeholders = ",".join(["?"] * len(tag_names))
        cursor = conn.execute(
            f"SELECT id, name FROM tags WHERE name IN ({placeholders})",
            list(tag_names),
        )
        return {row["name"]: row["id"] for row in cursor.fetchall()}

    def _sync_prompt_tags(
        self, conn: sqlite3.Connection, prompt_id: int, tags: List[str]
    ) -> None:
        """Replace all junction table entries for a prompt."""
        conn.execute("DELETE FROM prompt_tags WHERE prompt_id = ?", (prompt_id,))
        if tags:
            tag_map = self._ensure_tags(conn, tags)
            for tag_name in tags:
                tag_id = tag_map.get(tag_name)
                if tag_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag_id) "
                        "VALUES (?, ?)",
                        (prompt_id, tag_id),
                    )

    def _get_prompt_tags(self, conn: sqlite3.Connection, prompt_id: int) -> List[str]:
        """Get tag names for a prompt from junction table."""
        cursor = conn.execute(
            "SELECT t.name FROM prompt_tags pt "
            "JOIN tags t ON pt.tag_id = t.id "
            "WHERE pt.prompt_id = ?",
            (prompt_id,),
        )
        return [row["name"] for row in cursor.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        Convert a database row to a dictionary with parsed JSON fields.

        Args:
            row: SQLite row object

        Returns:
            Dictionary representation of the row
        """
        data = dict(row)

        # Parse tags from junction table subquery result (preferred)
        if "_tag_list" in data and data["_tag_list"]:
            data["tags"] = data["_tag_list"].split("|||")
            del data["_tag_list"]
        elif "_tag_list" in data:
            # TAG_SUBQUERY present but NULL (no tags)
            data["tags"] = []
            del data["_tag_list"]
        elif data.get("tags"):
            # Fallback: parse legacy JSON column
            try:
                parsed = json.loads(data["tags"])
                if isinstance(parsed, list):
                    data["tags"] = parsed
                elif isinstance(parsed, str):
                    data["tags"] = [t.strip() for t in parsed.split(",") if t.strip()]
                else:
                    data["tags"] = []
            except (json.JSONDecodeError, TypeError):
                data["tags"] = []
        else:
            data["tags"] = []

        return data

    def export_prompts(self, file_path: str, format: str = "json") -> bool:
        """
        Export all prompts to a file.

        Args:
            file_path: Path to save the export file
            format: Export format ("json" or "csv")

        Returns:
            bool: True if export was successful, False otherwise
        """
        try:
            prompts = self.search_prompts(limit=10000)  # Get all prompts

            if format.lower() == "json":
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(prompts, f, indent=2, ensure_ascii=False)
            elif format.lower() == "csv":
                import csv

                if prompts:
                    with open(file_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=prompts[0].keys())
                        writer.writeheader()
                        for prompt in prompts:
                            # Convert lists to strings for CSV
                            row = prompt.copy()
                            if isinstance(row.get("tags"), list):
                                row["tags"] = ", ".join(row["tags"])
                            writer.writerow(row)
            else:
                raise ValueError(f"Unsupported export format: {format}")

            return True
        except Exception as e:
            self.logger.error(f"Error exporting prompts: {e}")
            return False

    def cleanup_duplicates(self) -> int:
        """
        Remove duplicate prompts based on text content, preserving all image links.
        Merges metadata and transfers images to the retained prompt.

        Returns:
            int: Number of duplicates removed
        """
        self.logger.info("Starting duplicate cleanup process with image preservation")
        try:
            with self._pair_transaction() as conn:
                groups, _paired = self._duplicate_groups(conn)
                self.logger.debug(f"Found {len(groups)} groups of duplicate prompts")
                total_removed = 0
                total_images_transferred = 0
                for sorted_ids in groups:
                    removed, moved = self._merge_duplicate_group(conn, sorted_ids)
                    total_removed += removed
                    total_images_transferred += moved

                conn.commit()

                if total_removed > 0:
                    self.logger.info(
                        f"Removed {total_removed} duplicate prompts, transferred "
                        f"{total_images_transferred} images"
                    )
                else:
                    self.logger.info("No duplicate prompts found")

                return total_removed

        except Exception as e:
            self.logger.error(f"Error cleaning up duplicates: {e}", exc_info=True)
            return 0

    # Gallery-related methods
    def link_image_to_prompt(
        self, prompt_id: str, image_path: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Link a generated image to a prompt.

        Args:
            prompt_id: ID of the prompt that generated this image
            image_path: Full path to the image file
            metadata: Optional metadata about the image

        Returns:
            int: The ID of the created image record, or 0 when nothing was
            linked (invalid or unknown prompt, already linked, or an error,
            which is logged)
        """
        try:
            return self.link_image_to_prompt_or_raise(prompt_id, image_path, metadata)
        except Exception as e:
            self.logger.error(f"Error linking image to prompt {prompt_id}: {e}")
            return 0

    def link_image_to_prompt_or_raise(
        self, prompt_id: str, image_path: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """``link_image_to_prompt`` that raises database errors.

        The image monitor links through this so a write that failed because
        Move library switched reaches ``survive_library_switch`` and runs
        again on the new library (#249 S6b). Each call is one transaction:
        a failed attempt rolls back, and a committed one does not raise.
        Returns 0 for an invalid or unknown prompt or an image already linked.
        """
        # Validate that prompt_id is a valid integer and exists in prompts table
        # Convert prompt_id to integer if it's a string number
        if isinstance(prompt_id, str) and prompt_id.isdigit():
            prompt_id_int = int(prompt_id)
        elif isinstance(prompt_id, int):
            prompt_id_int = prompt_id
        else:
            # Handle temporary IDs like "temp_123456" - skip linking
            if isinstance(prompt_id, str) and prompt_id.startswith("temp_"):
                self.logger.debug(
                    f"Skipping image linking for temporary prompt ID: {prompt_id}"
                )
                return 0
            else:
                self.logger.warning(
                    f"Invalid prompt_id format: {prompt_id}, skipping image linking"
                )
                return 0

        # Verify the prompt exists in the database
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM prompts WHERE id = ?", (prompt_id_int,)
            )
            if not cursor.fetchone():
                self.logger.warning(
                    f"Prompt ID {prompt_id_int} not found in database, skipping "
                    "image linking"
                )
                return 0

            # Proceed with linking
            image_id = self._insert_image_link(
                conn, prompt_id_int, image_path, metadata
            )
            conn.commit()

            if not image_id:
                self.logger.debug(
                    f"Image {normalize_image_path(image_path)} already linked to "
                    f"prompt {prompt_id_int}"
                )
                return 0

            return image_id

    def _insert_image_link(
        self,
        conn: sqlite3.Connection,
        prompt_id_int: int,
        image_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert one image link on ``conn`` (no commit).

        Returns the new image row's id, or 0 when the file is already linked
        to this prompt (``INSERT OR IGNORE``).
        """
        if not isinstance(metadata, dict):
            metadata = {}
        filename = os.path.basename(image_path)
        file_path = normalize_image_path(image_path)
        file_size, width, height, image_format = _coerce_file_info(metadata)
        # Each workflow and prompt graph is stored once (new form).
        documents = stored_values(
            conn,
            {
                "workflow_data": dumps_strict(metadata.get("workflow", {})),
                "prompt_metadata": dumps_strict(metadata.get("prompt", {})),
            },
        )

        # INSERT OR IGNORE skips a file already linked to this prompt
        # (same prompt_id + normalised full path)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO generated_images
            (prompt_id, image_path, filename, file_path, file_size, width,
             height, format, workflow_data, prompt_metadata, parameters,
             workflow_document_id, prompt_document_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_id_int,
                image_path,
                filename,
                file_path,
                file_size,
                width,
                height,
                image_format,
                documents["workflow_data"],
                documents["prompt_metadata"],
                dumps_strict(metadata.get("parameters", {})),
                documents["workflow_document_id"],
                documents["prompt_document_id"],
            ),
        )
        if cursor.rowcount == 0:
            # Already linked: a document made only for this row goes.
            release_documents(
                conn,
                (
                    documents["workflow_document_id"],
                    documents["prompt_document_id"],
                ),
            )
            return 0
        return cursor.lastrowid

    def get_prompt_images(self, prompt_id: str) -> List[Dict[str, Any]]:
        """
        Get all images associated with a prompt.

        Args:
            prompt_id: The prompt ID

        Returns:
            List of image records
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM generated_images
                WHERE prompt_id = ?
                ORDER BY generation_time DESC
                """,
                (prompt_id,),
            )
            reader = DocumentReader(conn)
            return [self._image_row_to_dict(row, reader) for row in cursor]

    def get_recent_images(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get recently generated images across all prompts, newest first.

        Args:
            limit: Maximum number of images to return
            offset: Number of newest images to skip (paging)

        Returns:
            List of image records with prompt text
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT gi.*, p.text as prompt_text
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                ORDER BY gi.generation_time DESC
                LIMIT ? OFFSET ?
                """,
                (int(limit), max(0, int(offset))),
            )
            reader = DocumentReader(conn)
            return [self._image_row_to_dict(row, reader) for row in cursor]

    def get_all_images(
        self,
        limit: int = 0,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get generated images with their linked prompts.

        Args:
            limit: Maximum number of images to return (0 = all).
            offset: Number of images to skip.

        Returns:
            List of image records with prompt text and tags
        """
        sql = (
            "SELECT gi.*, p.text as prompt_text, "
            "(SELECT GROUP_CONCAT(t.name, '|||') FROM prompt_tags pt "
            "JOIN tags t ON pt.tag_id = t.id WHERE pt.prompt_id = p.id) AS "
            "_prompt_tags_list "
            "FROM generated_images gi "
            "INNER JOIN prompts p ON gi.prompt_id = p.id "
            "WHERE gi.image_path IS NOT NULL AND gi.image_path != '' "
            "ORDER BY gi.generation_time DESC"
        )
        params: list = []
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params = [limit, offset]

        with self.model.get_connection() as conn:
            cursor = conn.execute(sql, params)
            reader = DocumentReader(conn)
            result = []
            for row in cursor:
                data = self._image_row_to_dict(row, reader)
                if row["_prompt_tags_list"]:
                    data["prompt_tags"] = row["_prompt_tags_list"].split("|||")
                else:
                    data["prompt_tags"] = []
                result.append(data)
            return result

    def search_images_by_prompt(
        self, search_term: str, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search images by prompt text, newest first.

        Args:
            search_term: Text to search for in prompt content
            limit: Maximum number of images to return (None for no limit)
            offset: Number of newest matches to skip (paging)

        Returns:
            List of image records with prompt text
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT gi.*, p.text as prompt_text
                FROM generated_images gi
                JOIN prompts p ON gi.prompt_id = p.id
                WHERE p.text LIKE ? ESCAPE '\\'
                ORDER BY gi.generation_time DESC
                LIMIT ? OFFSET ?
                """,
                (
                    f"%{escape_like(search_term)}%",
                    -1 if limit is None else int(limit),
                    max(0, int(offset)),
                ),
            )
            reader = DocumentReader(conn)
            return [self._image_row_to_dict(row, reader) for row in cursor]

    def get_image_by_id(self, image_id: int) -> Optional[Dict[str, Any]]:
        """
        Get an image record by its ID.

        Args:
            image_id: The image ID

        Returns:
            Image record or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM generated_images WHERE id = ?", (image_id,)
            )
            row = cursor.fetchone()
            return self._image_row_to_dict(row, DocumentReader(conn)) if row else None

    def delete_image(self, image_id: int) -> bool:
        """
        Delete an image record by its ID.

        Args:
            image_id: The image ID to delete

        Returns:
            bool: True if deletion was successful
        """
        with self.model.get_connection() as conn:
            documents = documents_of_rows(conn, "id", [image_id])
            cursor = conn.execute(
                "DELETE FROM generated_images WHERE id = ?", (image_id,)
            )
            release_documents(conn, documents)
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_missing_images(self, roots=None) -> int:
        """
        Remove image records where the actual file no longer exists.

        Args:
            roots: The image sources links are judged under (the health
                check's). When given, only a link under a source that can be
                read now is removed: a link under an unavailable source (an
                unmounted drive) or outside every source is kept. None keeps
                the old rule: every link whose file does not exist.

        Returns:
            int: Number of orphaned records removed
        """
        if roots is not None:
            with self.model.get_connection() as conn:
                rows = [tuple(row) for row in conn.execute(_LINK_ROWS_SQL)]
            # Files are looked at before the write transaction starts.
            ids = self._missing_link_ids(rows, [str(root) for root in roots])
            if not ids:
                return 0
            with self._pair_transaction() as conn:
                return _delete_ids(conn, "generated_images", ids)

        removed_count = 0

        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT id, image_path FROM generated_images")
            images = cursor.fetchall()

            removed = [
                image["id"]
                for image in images
                if not os.path.exists(image["image_path"])
            ]
            documents = documents_of_rows(conn, "id", removed)
            for image_id in removed:
                conn.execute("DELETE FROM generated_images WHERE id = ?", (image_id,))
                removed_count += 1
            release_documents(conn, documents)

            conn.commit()

        return removed_count

    def _clean_nan_values(self, obj: Any) -> Any:
        """
        Recursively clean NaN values from nested data structures.

        Args:
            obj: The object to clean (dict, list, or scalar)

        Returns:
            Cleaned object with NaN and Infinity values replaced by None
        """
        if isinstance(obj, dict):
            return {key: self._clean_nan_values(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_nan_values(item) for item in obj]
        elif isinstance(obj, float) and not math.isfinite(obj):
            return None
        else:
            return obj

    def _merge_duplicate_metadata(
        self,
        conn: sqlite3.Connection,
        primary_id: int,
        duplicate_ids: List[int],
        strict: bool = False,
    ) -> Dict[str, Any]:
        """
        Merge metadata from duplicate prompts, prioritizing non-empty values.

        Args:
            conn: Database connection
            primary_id: ID of the prompt to keep
            duplicate_ids: List of duplicate prompt IDs

        Returns:
            Dict containing merged metadata
        """
        try:
            cursor = conn.execute(
                "SELECT category, rating, notes FROM prompts WHERE id = ?",
                (primary_id,),
            )
            primary_data = cursor.fetchone()
            if not primary_data:
                return {}

            merged = {
                "category": primary_data["category"],
                "tags": self._get_prompt_tags(conn, primary_id),
                "rating": primary_data["rating"],
                "notes": primary_data["notes"] or "",
            }

            for dup_id in duplicate_ids:
                cursor = conn.execute(
                    "SELECT category, rating, notes FROM prompts WHERE id = ?",
                    (dup_id,),
                )
                dup_data = cursor.fetchone()
                if not dup_data:
                    continue

                if not merged["category"] and dup_data["category"]:
                    merged["category"] = dup_data["category"]

                dup_tags = self._get_prompt_tags(conn, dup_id)
                for tag in dup_tags:
                    if tag not in merged["tags"]:
                        merged["tags"].append(tag)

                if dup_data["rating"] and (
                    not merged["rating"] or dup_data["rating"] > merged["rating"]
                ):
                    merged["rating"] = dup_data["rating"]

                if dup_data["notes"] and dup_data["notes"].strip():
                    if merged["notes"]:
                        merged["notes"] += f" | {dup_data['notes']}"
                    else:
                        merged["notes"] = dup_data["notes"]

            return merged

        except Exception as e:
            if strict:  # a reviewed apply rolls back instead
                raise
            self.logger.error(f"Error merging metadata: {e}", exc_info=True)
            return {}

    def _transfer_images_to_primary(
        self,
        conn: sqlite3.Connection,
        primary_id: int,
        duplicate_ids: List[int],
        strict: bool = False,
    ) -> int:
        """
        Transfer all images from duplicate prompts to the primary prompt.

        Args:
            conn: Database connection
            primary_id: ID of the prompt to keep
            duplicate_ids: List of duplicate prompt IDs

        Returns:
            Number of images transferred
        """
        transferred_count = 0
        try:
            for dup_id in duplicate_ids:
                # Update all images to point to primary prompt
                cursor = conn.execute(
                    "UPDATE generated_images SET prompt_id = ? WHERE prompt_id = ?",
                    (primary_id, dup_id),
                )
                transferred_count += cursor.rowcount

                if cursor.rowcount > 0:
                    self.logger.debug(
                        f"Transferred {cursor.rowcount} images from prompt {dup_id} "
                        f"to {primary_id}"
                    )

            return transferred_count

        except Exception as e:
            if strict:  # a reviewed apply rolls back instead
                raise
            self.logger.error(f"Error transferring images: {e}", exc_info=True)
            return 0

    def _update_primary_with_merged_metadata(
        self,
        conn: sqlite3.Connection,
        primary_id: int,
        merged_metadata: Dict[str, Any],
        strict: bool = False,
    ) -> None:
        """
        Update the primary prompt with merged metadata.

        Args:
            conn: Database connection
            primary_id: ID of the prompt to update
            merged_metadata: Merged metadata dictionary
        """
        try:
            conn.execute(
                """
                UPDATE prompts
                SET category = ?, rating = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged_metadata.get("category"),
                    merged_metadata.get("rating"),
                    merged_metadata.get("notes"),
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    primary_id,
                ),
            )
            self._sync_prompt_tags(conn, primary_id, merged_metadata.get("tags", []))
            self.logger.debug(
                f"Updated primary prompt {primary_id} with merged metadata"
            )

        except Exception as e:
            if strict:  # a reviewed apply rolls back instead
                raise
            self.logger.error(
                f"Error updating primary prompt metadata: {e}", exc_info=True
            )

    # ------------------------------------------------------------------
    # Methods extracted from api.py raw SQL (Fix 3.1)
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive database statistics."""
        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM prompts")
            row = cursor.fetchone()
            total_prompts = (row[0] if row else 0) or 0

            cursor = conn.execute(
                "SELECT COUNT(DISTINCT TRIM(category)) FROM prompts "
                "WHERE category IS NOT NULL AND TRIM(category) != ''"
            )
            row = cursor.fetchone()
            total_categories = (row[0] if row else 0) or 0

            cursor = conn.execute(
                "SELECT AVG(rating) FROM prompts WHERE rating IS NOT NULL"
            )
            row = cursor.fetchone()
            avg_rating = row[0] if row else None

            cursor = conn.execute(
                "SELECT COUNT(*) FROM tags t "
                "WHERE EXISTS (SELECT 1 FROM prompt_tags pt WHERE pt.tag_id = t.id)"
            )
            row = cursor.fetchone()
            total_tags = (row[0] if row else 0) or 0

            cursor = conn.execute("SELECT COUNT(*) FROM generated_images")
            row = cursor.fetchone()
            total_images = (row[0] if row else 0) or 0

            cursor = conn.execute(
                "SELECT COUNT(DISTINCT prompt_id) FROM generated_images"
            )
            row = cursor.fetchone()
            images_with_prompts = (row[0] if row else 0) or 0

            avg = round(avg_rating, 2) if avg_rating else None
            return {
                "total_prompts": total_prompts,
                "total_categories": total_categories,
                "average_rating": avg,
                "avg_rating": avg,
                "total_tags": total_tags,
                "total_images": total_images,
                "images_with_prompts": images_with_prompts,
            }

    def update_prompt_text(self, prompt_id: int, new_text: str) -> bool:
        """Update prompt text and its deduplication hash atomically.

        Returns:
            True if a row was updated.

        Raises:
            sqlite3.IntegrityError: Another prompt already has the new hash.
        """
        with self._pair_transaction() as conn:
            self._ensure_prompts_unpaired(conn, [prompt_id])
            cursor = conn.execute(
                "UPDATE prompts SET text = ?, hash = ?, updated_at = ? WHERE id = ?",
                (
                    new_text,
                    generate_prompt_hash(new_text),
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    prompt_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_prompt_rating(self, prompt_id: int, rating: int) -> bool:
        """Update only the rating of a prompt.

        Returns:
            True if a row was updated.
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE prompts SET rating = ?, updated_at = ? WHERE id = ?",
                (
                    rating,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    prompt_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_prompt_tags(self, prompt_id: int, tags: List[str]) -> None:
        """Overwrite the tags list for a prompt via junction table."""
        with self.model.get_connection() as conn:
            self._sync_prompt_tags(conn, prompt_id, tags)
            conn.execute(
                "UPDATE prompts SET updated_at = ? WHERE id = ?",
                (datetime.datetime.now(datetime.timezone.utc).isoformat(), prompt_id),
            )
            conn.commit()

    def bulk_delete_prompts(self, prompt_ids: List[int]) -> int:
        """Delete multiple prompts and their associated images.

        Returns:
            Number of prompts actually deleted.
        """
        count = 0
        with self._pair_transaction() as conn:
            self._ensure_prompts_unpaired(conn, prompt_ids)
            documents = documents_of_rows(conn, "prompt_id", prompt_ids)
            for pid in prompt_ids:
                conn.execute("DELETE FROM generated_images WHERE prompt_id = ?", (pid,))
                cursor = conn.execute("DELETE FROM prompts WHERE id = ?", (pid,))
                if cursor.rowcount > 0:
                    count += 1
            release_documents(conn, documents)
            conn.commit()
        return count

    def bulk_add_tags(self, prompt_ids: List[int], new_tags: List[str]) -> int:
        """Add tags to multiple prompts via junction table (skipping duplicates).

        Returns:
            Number of prompts that were actually modified.
        """
        count = 0
        with self.model.get_connection() as conn:
            tag_map = self._ensure_tags(conn, new_tags)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for pid in prompt_ids:
                cursor = conn.execute("SELECT id FROM prompts WHERE id = ?", (pid,))
                if not cursor.fetchone():
                    continue
                added = False
                for tag_name in new_tags:
                    tag_id = tag_map.get(tag_name)
                    if tag_id:
                        cursor = conn.execute(
                            "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag_id) "
                            "VALUES (?, ?)",
                            (pid, tag_id),
                        )
                        if cursor.rowcount > 0:
                            added = True
                if added:
                    conn.execute(
                        "UPDATE prompts SET updated_at = ? WHERE id = ?", (now, pid)
                    )
                    count += 1
            conn.commit()
        return count

    def bulk_set_category(self, prompt_ids: List[int], category: str) -> int:
        """Set category on multiple prompts.

        Returns:
            Number of prompts actually updated.
        """
        count = 0
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.model.get_connection() as conn:
            for pid in prompt_ids:
                cursor = conn.execute(
                    "UPDATE prompts SET category = ?, updated_at = ? WHERE id = ?",
                    (category, now, pid),
                )
                if cursor.rowcount > 0:
                    count += 1
            conn.commit()
        return count

    def get_image_prompt_info(
        self,
        image_path: str,
        allow_filename_fallback: bool = True,
        *,
        report_ambiguity: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Look up prompt + image metadata for a given image path.

        Returns a dict with prompt_id, text, category, tags, rating, notes,
        workflow_data, prompt_metadata, generation_time, parameters — or None.
        Opting into report_ambiguity returns only an association_status marker
        for multiple matches; default callers still receive None.
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """SELECT gi.prompt_id, p.text, p.category, p.rating, p.notes,
                          gi.workflow_data, gi.prompt_metadata, gi.generation_time,
                          gi.parameters, gi.id, gi.image_path, gi.file_path,
                          gi.workflow_document_id, gi.prompt_document_id
                   FROM generated_images gi
                   JOIN prompts p ON gi.prompt_id = p.id
                   WHERE gi.image_path = ? OR gi.file_path = ?
                      OR (? AND gi.image_path LIKE ? ESCAPE '\\')""",
                (
                    image_path,
                    normalize_image_path(image_path),
                    allow_filename_fallback,
                    f"%{escape_like(os.path.basename(image_path))}",
                ),
            )
            rows = cursor.fetchall()
            exact = [
                row
                for row in rows
                if row[10] == image_path or row[11] == normalize_image_path(image_path)
            ]
            matches = exact or rows
            if len(matches) != 1:
                if report_ambiguity and matches:
                    return {"association_status": "ambiguous"}
                return None
            row = matches[0]
            associations = self.get_image_prompt_pairs(row[9])
            pair_ids = {item["pair_id"] for item in associations}
            pair = (
                self.get_prompt_pair(next(iter(pair_ids)))
                if len(pair_ids) == 1
                else None
            )
            candidates = []
            for item in associations:
                evidence = item["provenance"]
                positive_side = evidence.get("positive")
                negative_side = evidence.get("negative")
                if not isinstance(positive_side, dict) or not isinstance(
                    negative_side, dict
                ):
                    continue
                positive, negative = positive_side.get("text"), negative_side.get(
                    "text"
                )
                if not isinstance(positive, str) or not isinstance(negative, str):
                    continue
                sources = []
                for side in (positive_side, negative_side):
                    provenance = side.get("provenance")
                    sources.append(
                        provenance.get("text_source")
                        if isinstance(provenance, dict)
                        else None
                    )
                score = sources.count("execution")
                source = (
                    "execution"
                    if score == 2
                    else (
                        "mixed"
                        if score
                        else (
                            "embedded-parameters"
                            if sources == ["embedded-parameters"] * 2
                            else "graph-inferred"
                        )
                    )
                )
                candidates.append((score, positive, negative, source))
            context = None
            if candidates and pair:
                best_score = max(item[0] for item in candidates)
                contexts = {item[1:] for item in candidates if item[0] == best_score}
                if len(contexts) == 1:
                    positive, negative, source = next(iter(contexts))
                    context = {
                        "positive_text": positive,
                        "negative_text": negative,
                        "text_source": source,
                    }
            tags = self._get_prompt_tags(conn, row[0])
            texts = resolve_image_row(DocumentReader(conn), row)
            try:
                workflow = (
                    json.loads(texts["workflow_data"])
                    if texts["workflow_data"]
                    else None
                )
            except (json.JSONDecodeError, TypeError):
                workflow = None
            try:
                prompt_meta = (
                    json.loads(texts["prompt_metadata"])
                    if texts["prompt_metadata"]
                    else None
                )
            except (json.JSONDecodeError, TypeError):
                prompt_meta = None
            return {
                "pair": pair,
                "pair_context": context,
                "prompt_id": row[0],
                "text": row[1],
                "category": row[2],
                "tags": tags,
                "rating": row[3],
                "notes": row[4],
                "workflow_data": workflow,
                "prompt_metadata": prompt_meta,
                "generation_time": row[7],
                "parameters": row[8] if len(row) > 8 else None,
            }

    def get_known_resolved_paths(self, files: Dict[str, Optional[int]]) -> Set[str]:
        """The paths in ``files`` the Library already links with a resolved link.

        ``files`` maps each scanned path to its size on disk (None when it could
        not be read). A path qualifies when exactly one ``generated_images`` row
        claims the file, that row has exactly one pair link
        (``image_prompt_pairs``, written only by resolved ingestion), and its
        stored ``file_size`` is not NULL and equals the given size, so a file
        replaced at the same path is read again. Files linked without a pair
        (the monitor's fallback and FIFO links, the legacy positive-only
        import), linked to several prompts or pairs, or not linked at all do
        not qualify. Both sides go through ``normalize_image_path`` (absolute,
        normalized, ``os.path.normcase``), so a Windows row stored in another
        case or spelling still matches. One query, streamed; returns the
        matching keys of ``files``.
        """
        wanted: Dict[str, List[Tuple[str, Optional[int]]]] = {}
        for path, size in files.items():
            key = normalize_image_path(str(path))
            wanted.setdefault(key, []).append((str(path), size))
        if not wanted:
            return set()
        rows_seen: Dict[str, int] = {}
        stored_sizes: Dict[str, int] = {}
        with self.model.get_connection() as conn:
            cursor = conn.execute(_KNOWN_PATHS_SQL)
            while True:
                rows = cursor.fetchmany(KNOWN_PATHS_FETCH_SIZE)
                if not rows:
                    break
                for file_path, image_path, file_size, is_resolved in rows:
                    # image_path is NOT NULL; file_path is NULL only on old rows.
                    key = normalize_image_path(file_path or image_path)
                    if key not in wanted:
                        continue
                    rows_seen[key] = rows_seen.get(key, 0) + 1
                    if is_resolved and file_size is not None:
                        stored_sizes[key] = file_size
        return {
            path
            for key, stored_size in stored_sizes.items()
            if rows_seen[key] == 1
            for path, size in wanted[key]
            if size is not None and size == stored_size
        }

    def get_image_link_summaries(
        self, paths: List[str], allow_filename_fallback: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        """Summarize each path's linked saved positive for an image card.

        Matching equals get_image_prompt_info: exact image_path or normalized
        file_path rows win; otherwise, with the fallback, rows whose image_path
        ends with the basename. One match is linked, several are ambiguous.
        One SELECT reads up to LINK_SUMMARY_BATCH paths.
        """
        unique = list(dict.fromkeys(paths))
        summaries: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(unique), LINK_SUMMARY_BATCH):
            batch = unique[start : start + LINK_SUMMARY_BATCH]
            summaries.update(self._link_summary_batch(batch, allow_filename_fallback))
        return summaries

    def _link_summary_batch(
        self, paths: List[str], allow_filename_fallback: bool
    ) -> Dict[str, Dict[str, Any]]:
        normalized = {path: normalize_image_path(path) for path in paths}
        basenames = {path: os.path.basename(path) for path in paths}
        marks = ",".join("?" for _ in paths)
        params: List[Any] = [*paths, *normalized.values()]
        fallback = ""
        if allow_filename_fallback:
            fallback = "".join(" OR gi.image_path LIKE ? ESCAPE '\\'" for _ in paths)
            params.extend(f"%{escape_like(basenames[path])}" for path in paths)
        sql = _LINK_SUMMARY_SQL.format(marks=marks, fallback=fallback)
        with self.model.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        summaries = {}
        for path in paths:
            exact = [
                row for row in rows if row[0] == path or row[1] == normalized[path]
            ]
            matches = exact or (
                [row for row in rows if _like_suffix(row[0], basenames[path])]
                if allow_filename_fallback
                else []
            )
            if len(matches) == 1:
                summaries[path] = _link_summary(matches[0])
            else:
                summaries[path] = _empty_link_summary(
                    "ambiguous" if matches else "none"
                )
        return summaries

    def get_image_link_matches(
        self,
        q: str,
        category: Optional[str],
        tags: List[str],
        *,
        every_link: bool = False,
        wanted: Optional[Set[str]] = None,
    ) -> Dict[str, Tuple[bool, Optional[int], int]]:
        """Map normalized paths of unambiguously linked images to
        (hit, rating, positive id).

        Rows are limited to images whose linked positive has *category* and
        every tag in *tags* (exact names). ``hit`` says whether *q* occurs in
        the positive's text, Library title or a tag name; a pair's own title
        names its negative variant and is not searched.
        Without category or tags only hits are returned, unless *every_link*
        asks for every linked image (the rating sort and the positive count).
        The positive id is the card's linked positive.

        One SELECT, returned as one JSON value (#354): SQLite steps once, so
        the read takes the GIL back a constant two or three times instead of
        once per linked row while ComfyUI generates. Peak memory therefore
        grows with the database's matching linked rows (about 530 bytes each,
        freed on return); the result is bounded by *wanted* (the listing's
        normalized paths) when given. Where the JSON step fails (no JSON
        functions, a BLOB value, a JSON text over SQLite's length limit) the
        rows are streamed as before.
        """
        sql, params = link_filter_query(q, category, tags, every_link=every_link)
        with self.model.get_connection() as conn:
            rows = None
            if not _one_step_unsupported:
                rows = self._one_step_link_rows(conn, sql, params)
            if rows is None:
                return _streamed_link_matches(conn, sql, params, wanted)
        return {
            path: (bool(matched), rating, positive)
            for path, rating, matched, positive in rows
            if wanted is None or path in wanted
        }

    def _one_step_link_rows(
        self, conn: sqlite3.Connection, sql: str, params: Dict[str, Any]
    ) -> Optional[List[List[Any]]]:
        """*sql*'s rows from one JSON value, or None when this read must stream.

        An interrupted read (Move library retiring the connection) is raised
        as before, never retried row by row.
        """
        global _one_step_unsupported, _one_step_warned
        error: Optional[str] = None
        try:
            cursor = conn.execute(link_matches_json_query(sql), params)
            try:
                ((document,),) = cursor.fetchall()
            finally:
                cursor.close()  # resets the statement: its snapshot ends now
        except (sqlite3.OperationalError, sqlite3.DataError) as failure:
            # DataError: the JSON text is longer than SQLITE_LIMIT_LENGTH.
            error = str(failure)
            if error == "interrupted":
                raise
            if error.startswith("no such function"):
                _one_step_unsupported = True
        else:
            if document is not None:
                return json.loads(document)
            error = "a BLOB value"
        if not _one_step_warned:
            _one_step_warned = True
            self.logger.warning(
                "Reading image links row by row (slower while ComfyUI "
                "generates); the one-step read failed: %s",
                error,
            )
        return None

    def get_image_link_facts(
        self, paths: List[str], q: str = ""
    ) -> Dict[str, Tuple[bool, Optional[int], int, Optional[str], Tuple[str, ...]]]:
        """Map each normalized path with one unambiguous link to its positive's
        (hit, rating, positive id, category, tag names).

        The link is resolved as in get_image_link_matches, and ``hit`` says
        whether *q* occurs in the positive's text, Library title or a tag
        name (always true without *q*). Paths go in chunks of 500 through the
        ``file_path`` index; the tag names are read for those positives only.
        """
        hit, params = "1", {}
        if q:
            hit = _LINK_FILTER_HIT
            params["pattern"] = f"%{escape_like(q)}%"
        rows: Dict[str, Tuple[bool, Optional[int], int, Optional[str]]] = {}
        names: Dict[int, List[str]] = {}
        unique = list(dict.fromkeys(paths))
        with self.model.get_connection() as conn:
            for start in range(0, len(unique), _FACTS_CHUNK):
                chunk = unique[start : start + _FACTS_CHUNK]
                bound = {f"path{i}": path for i, path in enumerate(chunk)}
                marks = ",".join(f":{key}" for key in bound)
                sql = _LINK_FACTS_SQL.format(hit=hit, marks=marks)
                for path, rating, matched, positive, category in conn.execute(
                    sql, {**params, **bound}
                ):
                    rows[path] = (bool(matched), rating, positive, category)
            positives = sorted({row[2] for row in rows.values()})
            for start in range(0, len(positives), _FACTS_CHUNK):
                chunk = positives[start : start + _FACTS_CHUNK]
                marks = ",".join("?" * len(chunk))
                for positive, name in conn.execute(
                    _POSITIVE_TAG_NAMES_SQL.format(marks=marks), chunk
                ):
                    names.setdefault(positive, []).append(name)
        return {
            path: (*row, tuple(names.get(row[2], ()))) for path, row in rows.items()
        }

    def get_image_paths_matching(
        self, q: str, category: Optional[str], tags: List[str]
    ) -> Set[str]:
        """Normalized paths whose linked positive matches *q*, *category* and *tags*.

        See get_image_link_matches; an empty *q* matches every linked image
        the category and tags admit.
        """
        return {
            path
            for path, (hit, _rating, _positive) in self.get_image_link_matches(
                q, category, tags
            ).items()
            if hit or not q
        }

    def get_prompt_id_for_image(self, image_path: str) -> Optional[int]:
        """Return the prompt_id linked to an image, or None."""
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT prompt_id FROM generated_images"
                " WHERE image_path = ? OR file_path = ?",
                (image_path, normalize_image_path(image_path)),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def check_hash_duplicates(self) -> List[Dict[str, Any]]:
        """Find prompts sharing the same hash.

        Returns list of dicts with 'hash' and 'count'.
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT hash, COUNT(*) as count FROM prompts "
                "WHERE hash IS NOT NULL GROUP BY hash HAVING COUNT(*) > 1"
            )
            return [dict(row) for row in cursor.fetchall()]

    def prune_orphaned_prompts(self) -> int:
        """Delete prompts nothing references.

        A prompt is kept when it has a linked image, belongs to a pair, carries
        the ``__protected__`` tag, has Library parent state (a titled or
        explicit parent), or is the currently valid navigation hint of a
        recorded run: same storage epoch and exact raw snapshot text matching
        only that prompt, the conditions under which history reads resolve it.

        Returns number of prompts removed.
        """
        with self._pair_transaction() as conn:
            orphaned = _orphaned_ids(conn)
            if not orphaned:
                return 0
            removed = _delete_ids(conn, "prompts", orphaned)
            conn.commit()
            return removed

    def check_consistency(self) -> List[str]:
        """Run consistency checks and return a list of issue descriptions.

        The same queries back the System health check's Consistency row
        (database/health.py).
        """
        with self.model.get_connection() as conn:
            return list(iter_consistency_issues(conn))

    def _image_row_to_dict(
        self, row: sqlite3.Row, reader: DocumentReader
    ) -> Dict[str, Any]:
        """
        Convert an image database row to a dictionary with parsed JSON fields.

        Args:
            row: SQLite row object
            reader: The workflow document reader of the row's connection

        Returns:
            Dictionary representation of the row
        """
        data = resolve_image_row(reader, row)

        # Parse JSON fields
        for field in ["workflow_data", "prompt_metadata", "parameters"]:
            if data.get(field):
                try:
                    parsed_data = json.loads(data[field])
                    data[field] = self._clean_nan_values(parsed_data)
                except (json.JSONDecodeError, TypeError):
                    data[field] = {}
            else:
                data[field] = {}

        return data
