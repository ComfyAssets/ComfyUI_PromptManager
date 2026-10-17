"""Atomic metadata enrichment for a single known generation context."""

import json
import os
from typing import Any, Dict

from .models import normalize_image_path
from .operations import _coerce_file_info

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:
    from utils.hashing import generate_prompt_hash


def ingest_resolved_pair(
    db: Any, image_path: str, metadata: Dict[str, Any], resolution: Dict[str, Any]
) -> Dict[str, Any]:
    state = resolution.get("state", "unresolved")
    if state != "resolved":
        return {"status": state, "pairs_added": 0, "links_added": 0}
    positive, negative = resolution["positive"], resolution["negative"]
    positive_text = positive.get("storage_text", positive["text"])
    negative_text = negative.get("storage_text", negative["text"])
    normalized = normalize_image_path(image_path)
    provenance = {"resolver_version": 1, "positive": positive, "negative": negative}
    encoded = json.dumps(
        provenance, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    with db._pair_transaction() as conn:
        images = conn.execute(
            "SELECT gi.*,p.hash AS positive_hash FROM generated_images gi "
            "JOIN prompts p ON p.id=gi.prompt_id "
            "WHERE gi.file_path=? OR gi.image_path=?",
            (normalized, image_path),
        ).fetchall()
        if len(images) > 1 or (
            images and images[0]["positive_hash"] != generate_prompt_hash(positive_text)
        ):
            return {"status": "conflict", "pairs_added": 0, "links_added": 0}
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
                return {"status": "conflict", "pairs_added": 0, "links_added": 0}
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
        pair = conn.execute(
            "SELECT * FROM prompt_pairs WHERE id=?", (pair_id,)
        ).fetchone()
        pairs_added = int(prior_pair is None)
        file_size, width, height, image_format = _coerce_file_info(metadata)
        snapshots = {
            column: json.dumps(metadata[key], sort_keys=True, allow_nan=False)
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
        snapshots.update(
            {key: value for key, value in info.items() if value is not None}
        )
        changed = False
        if existing:
            image_id = existing["id"]
            updates = {
                key: value for key, value in snapshots.items() if existing[key] != value
            }
            if updates:
                conn.execute(
                    "UPDATE generated_images SET "
                    + ",".join(f"{key}=?" for key in updates)
                    + " WHERE id=?",
                    [*updates.values(), image_id],
                )
                changed = True
        else:
            columns = ["prompt_id", "image_path", "filename", "file_path", *snapshots]
            values = [
                pair["positive_prompt_id"],
                image_path,
                os.path.basename(image_path),
                normalized,
                *snapshots.values(),
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
