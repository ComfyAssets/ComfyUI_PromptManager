"""Pair category and tags repair (#550): the ``pair_metadata`` maintenance plan.

Before #546 a Pair node run saved its category and tags on the saved pair
only; the positive prompt it created stayed Uncategorized and untagged. This
plan fills the **empty** category and the **empty** tag set of those prompts
from their saved pairs (``docs/egm/post-3.4.1/550-design.md``):

- Considered: prompts the Pair node created as a positive (their earliest
  pair, in either role, has them as the positive and its ``created_at`` is
  within ``PAIR_CREATED_TOLERANCE`` of theirs) whose category is blank
  and/or that have no tags.
- Sources: the prompt's non-archived pairs where it is the positive. Pairs
  with a blank category or no tags are ignored for that field. A field fills
  when exactly one value remains (category: trimmed, case-sensitive; tags:
  the tag-id set).
- Kept (protected), in this order: archived prompt, edited prompt
  (``updated_at`` not within the tolerance of ``created_at``), pairs that
  disagree, a tag set over ``MAX_LIBRARY_TAGS``.

The apply runs in the caller's ``BEGIN IMMEDIATE`` transaction, recomputes
the fingerprint, then writes guarded statements (the field must still be
empty) that never touch a pair, a negative or ``updated_at``. A guard that
hits nothing raises ``PlanChanged`` so the whole transaction rolls back.
"""

import datetime

from . import maintenance as _maintenance
from .library_commands import MAX_LIBRARY_TAGS
from .maintenance import (
    MAX_DETAILS,
    MaintenanceDraft,
    PlanChanged,
    _excerpt,
    _Fingerprint,
    _marks,
    _plural,
)

OPERATION = "pair_metadata"
# Seconds: the Pair save stamps its new prompt and its pair with separate
# utc_now_iso() calls in one transaction (microseconds apart).
PAIR_CREATED_TOLERANCE = 2.0
# Fill prompts one plan applies; the rest are applied by the next review.
# Bounds the complete record a job keeps in memory (about 100 bytes a row).
MAX_PLAN_FILLS = 20000
DETAIL_VALUES = 3
DETAIL_TAGS = 5
MEDIA_LABEL = "No image file or saved pair is changed."
EMPTY_LABEL = "No Pair prompts are missing a category or tags"
RECORD_NOTE = (
    "The job's report lists every filled field. Download it to keep it: "
    "Activity forgets jobs after a restart."
)
# Protected classes in precedence order: (key, detail, reason one, many).
EDITED_DETAIL = (
    "Kept: edited after it was saved; its empty category or tags may be intentional"
)
PROTECTED = (
    (
        "archived",
        "Kept: archived in the Library",
        "archived in the Library",
        "archived in the Library",
    ),
    (
        "edited",
        EDITED_DETAIL,
        "edited after it was saved",
        "edited after they were saved",
    ),
    ("conflict", None, "where pairs disagree", "where pairs disagree"),
    (
        "too-many-tags",
        f"Kept: its pair has more than {MAX_LIBRARY_TAGS} tags",
        f"whose pair has more than {MAX_LIBRARY_TAGS} tags",
        f"whose pair has more than {MAX_LIBRARY_TAGS} tags",
    ),
)
_PROTECTED_ORDER = [key for key, *_rest in PROTECTED]
# Detail rows: what the user can act on (a conflict) first.
DETAIL_ORDER = ("conflict", "edited", "archived", "too-many-tags")

_CANDIDATES_SQL = """
    SELECT p.id, p.hash, p.text, p.created_at, p.updated_at,
           COALESCE(TRIM(p.category), '') = '' AS blank_category,
           NOT EXISTS (SELECT 1 FROM prompt_tags pt WHERE pt.prompt_id = p.id)
               AS untagged,
           EXISTS (SELECT 1 FROM prompt_archive pa WHERE pa.prompt_id = p.id)
               AS archived,
           (SELECT CASE WHEN pp.positive_prompt_id = p.id
                        THEN pp.created_at END
              FROM prompt_pairs pp
             WHERE pp.positive_prompt_id = p.id OR pp.negative_prompt_id = p.id
             ORDER BY pp.id LIMIT 1) AS first_pair_at
      FROM prompts p
     WHERE EXISTS (SELECT 1 FROM prompt_pairs pp WHERE pp.positive_prompt_id = p.id)
       AND (COALESCE(TRIM(p.category), '') = ''
            OR NOT EXISTS (SELECT 1 FROM prompt_tags pt WHERE pt.prompt_id = p.id))
     ORDER BY p.id
"""
_SOURCES_SQL = (
    "SELECT id, positive_prompt_id, TRIM(COALESCE(category, '')) FROM prompt_pairs "
    "WHERE archived = 0 AND positive_prompt_id IN ({marks}) ORDER BY id"
)
_PAIR_TAGS_SQL = (
    "SELECT ppt.pair_id, t.id, t.name FROM prompt_pair_tags ppt "
    "JOIN tags t ON t.id = ppt.tag_id WHERE ppt.pair_id IN ({marks}) "
    "ORDER BY ppt.pair_id, t.id"
)


def parse_stamp(value):
    """A stored timestamp as an aware UTC datetime, or None when unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _close(first, second):
    """Whether two stored stamps parse and lie within the tolerance."""
    a, b = parse_stamp(first), parse_stamp(second)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= PAIR_CREATED_TOLERANCE


def _batches(ids):
    size = _maintenance.ID_BATCH
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


def _sources(conn, ids):
    """``{prompt_id: [(pair_id, category, ((tag_id, name), ...)), ...]}``."""
    pairs = {}
    for batch in _batches(ids):
        for pair_id, prompt_id, category in conn.execute(
            _SOURCES_SQL.format(marks=_marks(batch)), batch
        ):
            pairs.setdefault(prompt_id, []).append([pair_id, category, []])
    by_pair = {pair[0]: pair for group in pairs.values() for pair in group}
    pair_ids = sorted(by_pair)
    for batch in _batches(pair_ids):
        for pair_id, tag_id, name in conn.execute(
            _PAIR_TAGS_SQL.format(marks=_marks(batch)), batch
        ):
            by_pair[pair_id][2].append((tag_id, name))
    return {
        prompt_id: [(pid, category, tuple(tags)) for pid, category, tags in group]
        for prompt_id, group in pairs.items()
    }


def _field(values, guard):
    """``(class, value)`` of one field from its distinct values, or None."""
    if not values:
        return None
    if guard:
        return (guard, None)
    if len(values) > 1:
        return ("conflict", sorted(values))
    return ("fill", next(iter(values)))


def _classify(row, sources):
    """The result of one considered prompt, or None when nothing applies."""
    guard = None
    if row["archived"]:
        guard = "archived"
    elif not _close(row["updated_at"], row["created_at"]):
        guard = "edited"
    category = tags = None
    tag_pair = None
    if row["blank_category"]:
        category = _field({c for _p, c, _t in sources if c}, guard)
    if row["untagged"]:
        sets = {}
        for pair_id, _category, pair_tags in sources:
            if pair_tags:
                sets.setdefault(frozenset(pair_tags), pair_id)
        tags = _field(set(sets), guard)
        if tags and tags[0] == "fill":
            tag_pair = sets[tags[1]]
            if len(tags[1]) > MAX_LIBRARY_TAGS:
                tags = ("too-many-tags", None)
            else:
                tags = ("fill", tuple(sorted(tags[1])))
        elif tags and tags[0] == "conflict":
            tags = ("conflict", [sorted(s) for s in sorted(sets, key=sorted)])
    if category is None and tags is None:
        return None
    return {
        "id": row["id"],
        "hash": row["hash"],
        "text": row["text"],
        "category": category,
        "tags": tags,
        "tag_pair": tag_pair if tags and tags[0] == "fill" else None,
        "pairs": [pair_id for pair_id, _c, _t in sources],
    }


def _state(conn):
    """``(results, fingerprint)``: every considered prompt's result, by id."""
    considered = []
    for row in conn.execute(_CANDIDATES_SQL):
        row = dict(
            zip(
                (
                    "id",
                    "hash",
                    "text",
                    "created_at",
                    "updated_at",
                    "blank_category",
                    "untagged",
                    "archived",
                    "first_pair_at",
                ),
                tuple(row),
            )
        )
        if _close(row["created_at"], row["first_pair_at"]):
            considered.append(row)
    sources = _sources(conn, [row["id"] for row in considered])
    fingerprint = _Fingerprint(OPERATION)
    results = []
    for row in considered:
        result = _classify(row, sources.get(row["id"], []))
        if result is None:
            continue
        results.append(result)
        fingerprint.add(
            [
                result["id"],
                result["hash"],
                result["category"],
                result["tags"],
                result["tag_pair"],
                result["pairs"],
            ]
        )
    return results, fingerprint.hexdigest()


def _fills(result):
    return (
        bool(result["category"] and result["category"][0] == "fill"),
        bool(result["tags"] and result["tags"][0] == "fill"),
    )


def _targets(results):
    targets = []
    for result in results:
        category, tags = _fills(result)
        if category or tags:
            targets.append([result["id"], category, tags])
    return targets[:MAX_PLAN_FILLS]


def _protected_classes(result):
    """The protected classes of a result's fields, in precedence order."""
    found = {
        field[0]
        for field in (result["category"], result["tags"])
        if field and field[0] != "fill"
    }
    return [key for key in _PROTECTED_ORDER if key in found]


def _more(values, limit):
    shown = ", ".join(values[:limit])
    return shown + (", …" if len(values) > limit else "")


def _fill_detail(result):
    parts = []
    category, tags = _fills(result)
    if category:
        parts.append(f"category “{result['category'][1]}”")
    if tags:
        names = sorted(name for _id, name in result["tags"][1])
        noun = "tag" if len(names) == 1 else "tags"
        parts.append(f"{len(names)} {noun}: {_more(names, DETAIL_TAGS)}")
    pairs = [str(pair_id) for pair_id in result["pairs"]]
    label = "pair" if len(pairs) == 1 else "pairs"
    return f"Fills {' and '.join(parts)} ({label} {_more(pairs, DETAIL_VALUES)})"


def _protected_detail(result, key):
    if key != "conflict":
        return next(detail for k, detail, *_r in PROTECTED if k == key)
    if result["category"] and result["category"][0] == "conflict":
        values = result["category"][1]
        return f"Kept: pairs disagree on category: {_more(values, DETAIL_VALUES)}"
    return "Kept: pairs disagree on tags"


def _details(results, targets):
    rows = []
    for key in DETAIL_ORDER:
        for result in results:
            if key in _protected_classes(result):
                rows.append(
                    {
                        "label": _excerpt(result["text"]),
                        "detail": _protected_detail(result, key),
                    }
                )
    planned = {target[0] for target in targets}
    for result in results:
        if result["id"] in planned:
            rows.append(
                {"label": _excerpt(result["text"]), "detail": _fill_detail(result)}
            )
    if not targets:
        return rows[:MAX_DETAILS]
    return rows[: MAX_DETAILS - 1] + [{"label": "Record", "detail": RECORD_NOTE}]


def _scope_label(targets, total):
    if not targets:
        return EMPTY_LABEL
    categories = sum(1 for _id, category, _tags in targets if category)
    tag_sets = sum(1 for _id, _category, tags in targets if tags)
    label = (
        f"{_plural(len(targets), 'prompt')} to fill: "
        f"{_plural(categories, 'category', 'categories')}, "
        f"{_plural(tag_sets, 'tag set')}"
    )
    if total > len(targets):
        label += (
            f" (the first {len(targets):,} of {total:,}; review again for the rest)"
        )
    return label


def plan_pair_metadata(conn):
    """The reviewed ``pair_metadata`` plan on ``conn``'s snapshot."""
    results, fingerprint = _state(conn)
    targets = _targets(results)
    total = sum(1 for result in results if any(_fills(result)))
    counts = {key: 0 for key in _PROTECTED_ORDER}
    held = 0
    for result in results:
        classes = _protected_classes(result)
        held += bool(classes)
        for key in classes:
            counts[key] += 1
    reasons = [
        f"{counts[key]:,} {one if counts[key] == 1 else many}"
        for key, _detail, one, many in PROTECTED
        if counts[key]
    ]
    return MaintenanceDraft(
        operation=OPERATION,
        fingerprint=fingerprint,
        targets=targets,
        scope={"count": len(targets), "label": _scope_label(targets, total)},
        protected={"count": held, "reasons": reasons},
        media={"deletes_files": False, "label": MEDIA_LABEL},
        details=_details(results, targets),
    )


def _fill_categories(conn, fills):
    """Set each blank category; raises PlanChanged when one is no longer blank."""
    cursor = conn.executemany(
        "UPDATE prompts SET category = ? WHERE id = ? "
        "AND COALESCE(TRIM(category), '') = ''",
        fills,
    )
    if cursor.rowcount != len(fills):
        raise PlanChanged(OPERATION)


def _fill_tags(conn, fills, expected):
    """Copy each source pair's tags onto its untagged prompt."""
    cursor = conn.executemany(
        "INSERT INTO prompt_tags(prompt_id, tag_id) "
        "SELECT ?, ppt.tag_id FROM prompt_pair_tags ppt "
        "JOIN tags t ON t.id = ppt.tag_id WHERE ppt.pair_id = ? "
        "AND NOT EXISTS (SELECT 1 FROM prompt_tags WHERE prompt_id = ?)",
        fills,
    )
    if cursor.rowcount != expected:
        raise PlanChanged(OPERATION)


def apply_pair_metadata(conn, fingerprint, targets):
    """Apply the reviewed fills on ``conn`` (inside BEGIN IMMEDIATE).

    Returns ``{changed, categories, tag_sets, rows}``; ``rows`` lists every
    filled prompt (``{prompt_id, category?, tags?}``), never capped.
    """
    results, current = _state(conn)
    if current != fingerprint or _targets(results) != targets:
        raise PlanChanged(OPERATION)
    by_id = {result["id"]: result for result in results}
    categories, tags, rows = [], [], []
    tag_count = 0
    for prompt_id, fill_category, fill_tags in targets:
        result = by_id[prompt_id]
        row = {"prompt_id": prompt_id}
        if fill_category:
            row["category"] = result["category"][1]
            categories.append((row["category"], prompt_id))
        if fill_tags:
            row["tags"] = sorted(name for _id, name in result["tags"][1])
            tags.append((prompt_id, result["tag_pair"], prompt_id))
            tag_count += len(result["tags"][1])
        rows.append(row)
    if categories:
        _fill_categories(conn, categories)
    if tags:
        _fill_tags(conn, tags, tag_count)
    return {
        "changed": len(rows),
        "categories": len(categories),
        "tag_sets": len(tags),
        "rows": rows,
    }
