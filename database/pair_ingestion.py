"""Atomic metadata enrichment for a single known generation context."""

import os
from typing import Any, Dict

from .models import normalize_image_path
from .operations import _coerce_file_info
from .workflow_documents import (
    TEXT_COLUMNS,
    DocumentReader,
    release_documents,
    resolve_image_row,
    stored_values,
)

try:
    from ..utils.hashing import generate_prompt_hash
    from ..utils.json_strict import dumps_strict
    from ..utils.prompt_pair_resolver import RESOLVER_VERSION
except ImportError:
    from utils.hashing import generate_prompt_hash
    from utils.json_strict import dumps_strict
    from utils.prompt_pair_resolver import RESOLVER_VERSION


def blank_positive(resolution: Dict[str, Any]) -> bool:
    """A resolved pair whose stored positive text is empty (#342).

    A PromptManager node whose editor is empty encodes only its prepend and
    append text, which is never stored as a prompt: such a result names no
    prompt to link.
    """
    if resolution.get("state") != "resolved":
        return False
    positive = resolution.get("positive") or {}
    text = positive.get("storage_text", positive.get("text"))
    return not (isinstance(text, str) and text.strip())


def ingest_resolved_pair(
    db: Any, image_path: str, metadata: Dict[str, Any], resolution: Dict[str, Any]
) -> Dict[str, Any]:
    state = resolution.get("state", "unresolved")
    if state == "resolved" and blank_positive(resolution):
        state = "unresolved"
    if state != "resolved":
        return {"status": state, "pairs_added": 0, "links_added": 0}
    with db._pair_transaction() as conn:
        return _ingest_resolved_pair_in(conn, db, image_path, metadata, resolution)


def _conflict() -> Dict[str, Any]:
    return {"status": "conflict", "pairs_added": 0, "links_added": 0}


def _ingest_resolved_pair_in(
    conn: Any,
    db: Any,
    image_path: str,
    metadata: Dict[str, Any],
    resolution: Dict[str, Any],
    *,
    provenance: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """``ingest_resolved_pair`` inside the caller's open ``_pair_transaction``.

    The compatible-only rules are the public ingester's: an image with more
    than one exact row, another positive, or a prior pair with another
    negative is a ``conflict`` and nothing is written. The caller owns the
    transaction, so its own later failure rolls the link back too.
    ``provenance`` replaces the link's default resolver provenance (Needs
    review records the user's selection there).
    """
    positive, negative = resolution["positive"], resolution["negative"]
    positive_text = positive.get("storage_text", positive["text"])
    negative_text = negative.get("storage_text", negative["text"])
    normalized = normalize_image_path(image_path)
    if provenance is None:
        provenance = {
            "resolver_version": RESOLVER_VERSION,
            "positive": positive,
            "negative": negative,
        }
    encoded = dumps_strict(provenance, sort_keys=True, separators=(",", ":"))
    images = conn.execute(
        "SELECT gi.*,p.hash AS positive_hash FROM generated_images gi "
        "JOIN prompts p ON p.id=gi.prompt_id "
        "WHERE gi.file_path=? OR gi.image_path=?",
        (normalized, image_path),
    ).fetchall()
    if len(images) > 1 or (
        images and images[0]["positive_hash"] != generate_prompt_hash(positive_text)
    ):
        return _conflict()
    existing = images[0] if images else None
    if existing:
        prior = conn.execute(
            "SELECT n.hash AS negative_hash FROM image_prompt_pairs ip "
            "JOIN prompt_pairs pp ON pp.id=ip.pair_id "
            "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id WHERE ip.image_id=?",
            (existing["id"],),
        ).fetchall()
        expected = (
            generate_prompt_hash(negative_text) if negative_text.strip() else None
        )
        if any(row["negative_hash"] != expected for row in prior):
            return _conflict()
    prior_pair = conn.execute(
        "SELECT pp.id FROM prompt_pairs pp "
        "JOIN prompts p ON p.id=pp.positive_prompt_id "
        "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
        "WHERE p.hash=? AND n.hash IS ?",
        (
            generate_prompt_hash(positive_text),
            generate_prompt_hash(negative_text) if negative_text.strip() else None,
        ),
    ).fetchone()
    pair_id = db._save_pair(conn, positive_text, negative_text)
    pair = conn.execute("SELECT * FROM prompt_pairs WHERE id=?", (pair_id,)).fetchone()
    pairs_added = int(prior_pair is None)
    file_size, width, height, image_format = _coerce_file_info(metadata)
    # ComfyUI's NaN/Infinity are stored as null: the snapshot is strict JSON.
    snapshots = {
        column: dumps_strict(metadata[key], sort_keys=True)
        for column, key in (
            ("workflow_data", "workflow"),
            ("prompt_metadata", "prompt"),
            ("parameters", "parameters"),
        )
        if key in metadata
    }
    info = {
        "file_size": file_size,
        "width": width,
        "height": height,
        "format": image_format,
    }
    snapshots.update({key: value for key, value in info.items() if value is not None})
    changed = False
    if existing:
        image_id = existing["id"]
        # Compared as stored text, whichever form the row is in.
        current = resolve_image_row(DocumentReader(conn), existing)
        updates = {
            key: value for key, value in snapshots.items() if current[key] != value
        }
        if updates:
            replaced = [
                existing[reference]
                for column, reference in TEXT_COLUMNS
                if column in updates
            ]
            updates = _document_form(conn, updates)
            conn.execute(
                "UPDATE generated_images SET "
                + ",".join(f"{key}=?" for key in updates)
                + " WHERE id=?",
                [*updates.values(), image_id],
            )
            release_documents(conn, replaced)
            changed = True
    else:
        stored = _document_form(conn, snapshots)
        columns = ["prompt_id", "image_path", "filename", "file_path", *stored]
        values = [
            pair["positive_prompt_id"],
            image_path,
            os.path.basename(image_path),
            normalized,
            *stored.values(),
        ]
        image_id = conn.execute(
            "INSERT INTO generated_images("
            + ",".join(columns)
            + ") VALUES("
            + ",".join("?" for _ in columns)
            + ")",
            values,
        ).lastrowid
    linked = conn.execute(
        "SELECT 1 FROM image_prompt_pairs WHERE pair_id=? "
        "AND image_id=? AND provenance=?",
        (pair_id, image_id, encoded),
    ).fetchone()
    db._link_image_to_pair(conn, pair_id, image_id, provenance)
    status = (
        "added"
        if existing is None
        else "enriched" if changed or not linked else "unchanged"
    )
    return {
        "status": status,
        "pairs_added": pairs_added,
        "links_added": int(not linked),
        "pair_id": pair_id,
        "image_id": image_id,
    }


def _document_form(conn: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    """``values`` with the workflow and prompt graph texts stored once.

    Each of those columns becomes NULL beside its document id (new form);
    every other column is unchanged.
    """
    texts = {
        column: values[column]
        for column, _reference in TEXT_COLUMNS
        if column in values
    }
    rest = {key: value for key, value in values.items() if key not in texts}
    return {**rest, **stored_values(conn, texts)}
