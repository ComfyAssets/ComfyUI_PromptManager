"""Narrow comparison tokens for a Library parent's own metadata projection."""

import hashlib
import json

_DOMAIN = "prompt-manager-library-parent:v1\n"


def library_edit_token(epoch: str, positive: dict) -> str:
    """Hash the parent's owner fields under the current storage epoch.

    Timestamps, counts, pairs and previews are excluded, and an absent state
    row projects the same null title as a default one.
    """
    preimage = {
        "epoch": epoch,
        "id": positive["id"],
        "text": positive["text"],
        "title": positive["title"],
        "category": positive["category"],
        "notes": positive["notes"],
        "rating": positive["rating"],
        "tags": sorted(set(positive["tags"])),
    }
    payload = json.dumps(
        preimage,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256((_DOMAIN + payload).encode("utf-8")).hexdigest()
