"""Tag cleanup: a registry of rules over the tag registry (#618).

Each ``TagRule`` finds tag ids on a read-only connection, rechecks one
reviewed target on the write connection and applies it there. ``RULES`` is
the registry; its order is the review's order and decides ownership: a tag
belongs to the first rule that finds it and is listed only there. Two rules
ship:

- ``comma``: tags whose name holds an ASCII or a fullwidth comma (a whole
  list saved as one tag, usually from a LoRA's trained words). A tag whose
  name starts with ``lora:`` is never one: it is the LoRA import's identity
  tag, which the next import makes again (maintainer ruling, 2026-11-22).
  Applying removes the tag from its prompts, pairs and images and deletes it.
- ``unused``: tags no prompt, saved pair or image carries. Deleted.

``find_tag_cleanup`` reads one ``mode=ro`` snapshot and returns every rule's
findings (at most ``MAX_FINDINGS`` held per rule, totals exact).
``plan_tag_cleanup`` re-reads the selected findings on another snapshot; one
whose fingerprint ``(tag_id, name, links)`` changed since the find is
protected, not planned. ``links`` hashes the sorted prompt ids, pair ids and
``(byte_id, position)`` rows carrying the tag, so a tag moved from one prompt
to another is seen. ``apply_tag_cleanup`` runs inside the caller's ``BEGIN
IMMEDIATE`` and rechecks each target: one that changed is kept with its
reason and the rest still apply (one edited tag must not block hundreds).

Saved views are never rewritten; a finding says how many name it. The
auto-tag marks (``media_image_autotags``) are never read or written.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from .maintenance import (
    MAX_DETAILS,
    MaintenanceDraft,
    _Fingerprint,
    _plural,
    _read_snapshot,
)
from .vocabulary import VocabularyOperations, _delete_tag

OPERATION = "tag_cleanup"
MAX_FINDINGS = 2000  # findings a find holds and lists per rule
COMMAS = (",", "，")
LORA_PREFIX = "lora:"
CHANGED_SINCE_SEARCH = "changed since the search"
NO_FILES = "No files are removed"

# Findings are listed by name (case-insensitive), then id.
_COMMA_SQL = (
    "SELECT id FROM tags WHERE (instr(name, ?) > 0 OR instr(name, ?) > 0) "
    "AND substr(name, 1, ?) != ? ORDER BY name COLLATE NOCASE, id"
)
_UNUSED_SQL = (
    "SELECT id FROM tags t WHERE "
    "NOT EXISTS (SELECT 1 FROM prompt_tags x WHERE x.tag_id = t.id) AND "
    "NOT EXISTS (SELECT 1 FROM prompt_pair_tags x WHERE x.tag_id = t.id) "
    "ORDER BY name COLLATE NOCASE, id"
)
_UNUSED_WITH_IMAGES_SQL = (
    "SELECT id FROM tags t WHERE "
    "NOT EXISTS (SELECT 1 FROM prompt_tags x WHERE x.tag_id = t.id) AND "
    "NOT EXISTS (SELECT 1 FROM prompt_pair_tags x WHERE x.tag_id = t.id) AND "
    "NOT EXISTS (SELECT 1 FROM media_image_tags x WHERE x.tag_id = t.id) "
    "ORDER BY name COLLATE NOCASE, id"
)
_ANNOTATION_TABLES = ("media_image_tags", "media_annotation_bytes")


@dataclass(frozen=True)
class TagRule:
    """One cleanup rule: its copy and its three functions."""

    id: str
    title: str
    explanation: str
    action_label: str
    find: Callable[[sqlite3.Connection], list]
    recheck: Callable[[sqlite3.Connection, dict], Optional[str]]
    apply: Callable[[sqlite3.Connection, dict], None]


def _has_image_links(conn):
    """Whether the image annotation tables exist (else images link nothing)."""
    found = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
        _ANNOTATION_TABLES,
    ).fetchone()[0]
    return found == len(_ANNOTATION_TABLES)


def _links(conn, tag_id):
    """sha256 of the link rows carrying ``tag_id``: prompts, pairs, images."""
    prompts = [
        r[0]
        for r in conn.execute(
            "SELECT prompt_id FROM prompt_tags WHERE tag_id=? ORDER BY 1", (tag_id,)
        )
    ]
    pairs = [
        r[0]
        for r in conn.execute(
            "SELECT pair_id FROM prompt_pair_tags WHERE tag_id=? ORDER BY 1",
            (tag_id,),
        )
    ]
    images = []
    if _has_image_links(conn):
        images = [
            [r[0], r[1]]
            for r in conn.execute(
                "SELECT byte_id, position FROM media_image_tags WHERE tag_id=? "
                "ORDER BY 1, 2",
                (tag_id,),
            )
        ]
    return _hash_links(prompts, pairs, images)


def _hash_links(prompts, pairs, images):
    encoded = json.dumps([prompts, pairs, images], separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


NO_LINKS = _hash_links([], [], [])


def _find_comma(conn):
    params = (*COMMAS, len(LORA_PREFIX), LORA_PREFIX)
    return [row[0] for row in conn.execute(_COMMA_SQL, params)]


def _find_unused(conn):
    sql = _UNUSED_WITH_IMAGES_SQL if _has_image_links(conn) else _UNUSED_SQL
    return [row[0] for row in conn.execute(sql)]


def _identity_reason(conn, target):
    """``already deleted`` or ``renamed since the review``, else None."""
    row = conn.execute("SELECT name FROM tags WHERE id=?", (target["tag_id"],))
    row = row.fetchone()
    if row is None:
        return "already deleted"
    if row[0] != target["name"]:
        return "renamed since the review"
    return None


def _recheck_comma(conn, target):
    reason = _identity_reason(conn, target)
    if reason is None and _links(conn, target["tag_id"]) != target["links"]:
        reason = "its links changed since the review"
    return reason


def _recheck_unused(conn, target):
    reason = _identity_reason(conn, target)
    if reason is None and _links(conn, target["tag_id"]) != target["links"]:
        reason = "it is used now"
    return reason


def _delete_target(conn, target):
    """Both shipped rules' apply: delete the tag; its links cascade."""
    _delete_tag(conn, target["tag_id"])


def _apply_target(conn, target):
    # Looked up at call time, so a test can stand in for the delete.
    _delete_target(conn, target)


RULES = (
    TagRule(
        id="comma",
        title="Tags containing a comma",
        explanation=(
            "A whole list saved as one tag, usually from a LoRA's trained words."
        ),
        action_label=(
            "Removes the tag from its prompts, pairs and images and deletes it."
        ),
        find=_find_comma,
        recheck=_recheck_comma,
        apply=_apply_target,
    ),
    TagRule(
        id="unused",
        title="Tags with no links",
        explanation="No prompt, saved pair or image carries the tag.",
        action_label="Deletes the tag.",
        find=_find_unused,
        recheck=_recheck_unused,
        apply=_apply_target,
    ),
)
RULE_IDS = tuple(rule.id for rule in RULES)
_BY_ID = {rule.id: rule for rule in RULES}


def rule(rule_id):
    """The registered rule ``rule_id`` (KeyError when there is none)."""
    return _BY_ID[rule_id]


def _saved_view_names(conn):
    """``{tag name: views naming it}`` from every saved view's state."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='saved_views'"
    ).fetchone()
    if not exists:
        return {}
    counts = {}
    for (state,) in conn.execute("SELECT state FROM saved_views"):
        try:
            tags = json.loads(state).get("tags") or []
        except (TypeError, ValueError, AttributeError):
            continue
        for name in {tag for tag in tags if isinstance(tag, str)}:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _finding(conn, rule_id, tag_id, views):
    row = conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
    if row is None:
        return None
    counts = (
        VocabularyOperations._tag_owner_counts(conn, tag_id)
        if _has_image_links(conn)
        else _counts_without_images(conn, tag_id)
    )
    return {
        "rule": rule_id,
        "tag_id": tag_id,
        "name": row[0],
        "prompts": counts["components_changed"],
        "pairs": counts["pairs_changed"],
        "images": counts["images_changed"],
        "saved_views": views.get(row[0], 0),
        "links": _links(conn, tag_id),
    }


def _counts_without_images(conn, tag_id):
    prompts = conn.execute(
        "SELECT COUNT(DISTINCT prompt_id) FROM prompt_tags WHERE tag_id=?", (tag_id,)
    ).fetchone()[0]
    pairs = conn.execute(
        "SELECT COUNT(DISTINCT pair_id) FROM prompt_pair_tags WHERE tag_id=?",
        (tag_id,),
    ).fetchone()[0]
    return {"components_changed": prompts, "pairs_changed": pairs, "images_changed": 0}


def _owners_text(finding):
    parts = [
        _plural(finding[key], noun)
        for key, noun in (("prompts", "prompt"), ("pairs", "pair"), ("images", "image"))
        if finding[key]
    ]
    return " · ".join(parts)


def finding_detail(finding):
    """The server's line for a finding: ``3 prompts · 1 pair``, ``no links``,
    with `` · in 1 saved view`` when a saved view names it."""
    detail = _owners_text(finding) or "no links"
    views = finding.get("saved_views", 0)
    if views:
        detail += f" · in {_plural(views, 'saved view')}"
    return detail


def find_tag_cleanup(db_path):
    """Every rule's findings on one ``mode=ro`` snapshot (blocking, read-only).

    ``{"rules": {rule_id: [finding, ...]}, "totals": {rule_id: int}}``:
    findings by name (case-insensitive), then id; at most ``MAX_FINDINGS``
    held per rule, totals exact. A tag is listed under the first rule that
    finds it only.
    """

    def read(conn):
        owned = set()
        views = _saved_view_names(conn)
        rules, totals = {}, {}
        for item in RULES:
            ids = [tag_id for tag_id in item.find(conn) if tag_id not in owned]
            owned.update(ids)
            totals[item.id] = len(ids)
            findings = []
            for tag_id in ids[:MAX_FINDINGS]:
                finding = _finding(conn, item.id, tag_id, views)
                if finding is not None:
                    findings.append(finding)
            rules[item.id] = findings
        return {"rules": rules, "totals": totals}

    return _read_snapshot(db_path, read)


def _target(finding):
    return {
        key: finding[key]
        for key in (
            "rule",
            "tag_id",
            "name",
            "prompts",
            "pairs",
            "images",
            "saved_views",
            "links",
        )
    }


def _scope_label(targets):
    if not targets:
        return "No tags to delete"
    prompts = sum(t["prompts"] for t in targets)
    pairs = sum(t["pairs"] for t in targets)
    images = sum(t["images"] for t in targets)
    return (
        f"Delete {_plural(len(targets), 'tag')} (removed from "
        f"{_plural(prompts, 'prompt')}, {_plural(pairs, 'pair')} and "
        f"{_plural(images, 'image')})"
    )


def plan_tag_cleanup(db_path, held, tag_ids_by_rule):
    """The reviewed plan for the selected findings (blocking, read-only).

    ``held`` is a ``find_tag_cleanup`` result and ``tag_ids_by_rule``
    ``{rule_id: [tag_id, ...]}`` of its findings (the caller validated
    both). Each selected finding is read again on one snapshot; one whose
    ``(tag_id, name, links)`` changed since the find is protected
    (``changed since the search``), not planned. Targets follow the
    registry's order, then the find's.
    """
    reviewed = {
        (finding["rule"], finding["tag_id"]): finding
        for findings in held["rules"].values()
        for finding in findings
    }

    def read(conn):
        views = _saved_view_names(conn)
        targets, protected = [], []
        for item in RULES:
            for tag_id in tag_ids_by_rule.get(item.id, ()):
                before = reviewed[(item.id, tag_id)]
                now = _finding(conn, item.id, tag_id, views)
                same = now is not None and (now["name"], now["links"]) == (
                    before["name"],
                    before["links"],
                )
                if same:
                    targets.append(_target(now))
                else:
                    protected.append(
                        {**_target(before), "reason": CHANGED_SINCE_SEARCH}
                    )
        return targets, protected

    targets, protected = _read_snapshot(db_path, read)
    fingerprint = _Fingerprint(OPERATION)
    for target in targets:
        fingerprint.add([target["rule"], target["tag_id"], target["name"]])
        fingerprint.add(target["links"])
    reasons = (
        [f"{_plural(len(protected), 'tag')} {CHANGED_SINCE_SEARCH}"]
        if protected
        else []
    )
    draft = MaintenanceDraft(
        operation=OPERATION,
        fingerprint=fingerprint.hexdigest(),
        targets=targets,
        scope={"count": len(targets), "label": _scope_label(targets)},
        protected={"count": len(protected), "reasons": reasons},
        media={"deletes_files": False, "label": NO_FILES},
        details=[
            {
                "label": target["name"],
                "detail": f"{rule(target['rule']).title} · {finding_detail(target)}",
            }
            for target in targets[:MAX_DETAILS]
        ],
    )
    draft.held = protected
    return draft


def held_rows(draft):
    """The findings a plan protected, each with its reason."""
    return list(getattr(draft, "held", ()))


def _empties_legacy_tags(conn):
    """Whether deleting one more tag would empty ``tags`` while a prompt still
    holds legacy JSON tags: the start-up migration would then bring every
    legacy tag and link back (``models._migrate_json_tags_to_junction``)."""
    if conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] > 1:
        return False
    legacy = conn.execute(
        "SELECT 1 FROM prompts WHERE tags IS NOT NULL AND tags != '' "
        "AND tags != '[]' LIMIT 1"
    ).fetchone()
    return legacy is not None


LAST_TAG = "kept so the old tag migration does not run again"


def apply_tag_cleanup(conn, targets):
    """Apply the reviewed targets in the caller's transaction (blocking).

    Each target's rule rechecks it on ``conn``; one that changed is kept with
    its reason and the rest still apply. ``{"changed": n, "rows": [every
    deleted target], "kept": [{...target, "reason"}]}``. Any exception
    propagates, so the caller's transaction rolls back as a whole.
    """
    changed, rows, kept = 0, [], []
    for target in targets:
        item = rule(target["rule"])
        reason = item.recheck(conn, target)
        if reason is None and _empties_legacy_tags(conn):
            reason = LAST_TAG
        if reason is not None:
            kept.append({**target, "reason": reason})
            continue
        item.apply(conn, target)
        changed += 1
        rows.append(dict(target))
    return {"changed": changed, "rows": rows, "kept": kept}
