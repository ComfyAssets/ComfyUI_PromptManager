"""Review, and on request repair, image links that nothing certain supports (#342).

Before #342 the image monitor linked an image it could not identify to the
newest prompt in the database or to a stale tracker entry. This tool judges the
selected images again by today's rules, using only what is certain:

- the image's own metadata, as the monitor reads it;
- the output scan's resolver, and its legacy browser-parser floor (#170);
- recorded execution history (``database.run_attribution``).

Each image gets one verdict:

``keep``      a certain source names the prompt it is linked to
``relink``    every certain source names one other stored prompt
``unlink``    nothing certain names a prompt (the link was a guess)
``conflict``  certain sources disagree: decide by hand
``missing``   the file is gone: leave it to Maintenance's missing links

Dry run by default: the database is copied (SQLite backup from a read-only
connection) into a temporary folder and judged there, so the library is never
written. ``--apply`` writes the ``relink`` and ``unlink`` verdicts of the
selected images, after saving a backup beside the database
(``<db>.pre-relink_<stamp>``). Only ids named on the command line are ever
changed. A batch item linked through the monitor's queue (PromptSearchList)
can't be proven after the fact and reads ``unlink``: review before applying.

    python3 tools/review_image_links.py <db> --output-dir <ComfyUI output> \\
        --ids 58005-58010
    python3 tools/review_image_links.py <db> --output-dir <dir> --prompt 5278
    python3 tools/review_image_links.py <db> --output-dir <dir> \\
        --ids 58005-58010 --apply

Stop ComfyUI before ``--apply``. Run from this repository with its venv; the
package's logger may write to its usual log file.
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database.maintenance import _delete_ids  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from database.pair_ingestion import blank_positive  # noqa: E402
from database.run_attribution import stored_run_prompt  # noqa: E402
from utils.generation_params import (  # noqa: E402
    PLACEHOLDERS,
    GenerationParamsError,
    extract_generation_params,
)
from utils.hashing import generate_prompt_hash  # noqa: E402
from utils.image_monitor import ImageGenerationHandler  # noqa: E402
from utils.prompt_pair_resolver import resolve_prompt_pair  # noqa: E402

VERDICTS = ("keep", "relink", "unlink", "conflict", "missing")
# Ids per query (well under SQLite's bound-parameter limit)
ID_BATCH = 500


def parse_ids(text):
    """``"1,4-6"`` -> ``[1, 4, 5, 6]``; raises ValueError on anything else."""
    ids = set()
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        if first < 1 or last < first or last - first > 100_000:
            raise ValueError(f"Bad id range: {part}")
        ids.update(range(first, last + 1))
    return sorted(ids)


def _stored_id(db, text):
    if not isinstance(text, str) or not text.strip():
        return None
    row = db.get_prompt_by_hash(generate_prompt_hash(text.strip()))
    return row["id"] if row else None


def _parser_text(metadata):
    """The legacy browser parser's positive prompt (the scan's floor), or None."""
    records = {key: metadata.get(key) for key in ("prompt", "workflow")}
    try:
        positive = extract_generation_params(records)["positivePrompt"]
    except (GenerationParamsError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(positive, str) or positive == PLACEHOLDERS["positivePrompt"]:
        return None
    return positive.strip() or None


def certain_prompts(db, handler, path, metadata, output_dir):
    """``{source: prompt id}`` of every certain source that names a stored prompt."""
    found = {}
    if metadata:
        state, prompt = handler._find_prompt_from_metadata(metadata)
        if state == "linked" and prompt:
            found["metadata"] = prompt["id"]
        resolution = resolve_prompt_pair(metadata)
        if resolution["state"] == "resolved" and not blank_positive(resolution):
            positive = resolution["positive"]
            scanned = _stored_id(db, positive.get("storage_text", positive["text"]))
        else:
            scanned = _stored_id(db, _parser_text(metadata))
        if scanned is not None:
            found["scan"] = scanned
    recorded = stored_run_prompt(db, path, output_dir)
    if recorded is not None:
        found["history"] = recorded
    return found


def judge(db, handler, row, output_dir):
    """One image's verdict dict."""
    path = row["image_path"]
    verdict = {
        "image_id": row["id"],
        "file": path,
        "linked_prompt": row["prompt_id"],
        "sources": {},
        "target": None,
    }
    if not os.path.isfile(path):
        return {**verdict, "verdict": "missing"}
    try:
        metadata = handler.metadata_extractor.extract_metadata(path)
    except Exception:
        metadata = None
    sources = certain_prompts(db, handler, path, metadata, output_dir)
    named = set(sources.values())
    verdict["sources"] = sources
    if not named:
        return {**verdict, "verdict": "unlink"}
    if len(named) > 1:
        return {**verdict, "verdict": "conflict"}
    (target,) = named
    if target == row["prompt_id"]:
        return {**verdict, "verdict": "keep", "target": target}
    return {**verdict, "verdict": "relink", "target": target}


def selected_rows(db, ids, prompt_id):
    """The named images and every image of ``prompt_id``, by id."""
    conn = db.model.get_connection()
    columns = "SELECT id, prompt_id, image_path FROM generated_images "
    rows = {}
    for start in range(0, len(ids), ID_BATCH):
        batch = ids[start : start + ID_BATCH]
        marks = ",".join("?" for _ in batch)
        query = columns + f"WHERE id IN ({marks})"  # only ? marks
        rows.update((row["id"], row) for row in conn.execute(query, batch))
    if prompt_id is not None:
        found = conn.execute(columns + "WHERE prompt_id = ?", (prompt_id,))
        rows.update((row["id"], row) for row in found)
    return [rows[key] for key in sorted(rows)]


def review(db, ids, prompt_id, output_dir):
    handler = ImageGenerationHandler(db, None)
    return [
        judge(db, handler, row, output_dir) for row in selected_rows(db, ids, prompt_id)
    ]


def apply(db, verdicts, ids):
    """Write the relink/unlink verdicts of explicitly named ids; returns counts."""
    allowed = set(ids)
    relink = [
        v for v in verdicts if v["verdict"] == "relink" and v["image_id"] in allowed
    ]
    unlink = [
        v for v in verdicts if v["verdict"] == "unlink" and v["image_id"] in allowed
    ]
    with db._pair_transaction() as conn:
        for item in relink:
            # A pair link records the old positive: it goes with the old link
            conn.execute(
                "DELETE FROM image_prompt_pairs WHERE image_id=?", (item["image_id"],)
            )
            conn.execute(
                "UPDATE generated_images SET prompt_id=? WHERE id=? AND prompt_id=?",
                (item["target"], item["image_id"], item["linked_prompt"]),
            )
        removed = _delete_ids(conn, "generated_images", [v["image_id"] for v in unlink])
    return {"relinked": len(relink), "unlinked": removed}


def backup_database(source, target):
    """SQLite online backup of ``source`` (opened read-only) into ``target``."""
    uri = Path(os.path.abspath(source)).as_uri() + "?mode=ro"
    with (
        closing(sqlite3.connect(uri, uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)


def _print(verdicts, out):
    for item in verdicts:
        sources = ", ".join(f"{k}={v}" for k, v in sorted(item["sources"].items()))
        target = f" -> {item['target']}" if item["verdict"] == "relink" else ""
        linked = item["linked_prompt"]
        out.write(
            f"{item['image_id']:>8}  {item['verdict']:<8} prompt {linked}{target}"
            f"  [{sources or 'nothing certain'}]  {item['file']}\n"
        )
    counts = {name: sum(v["verdict"] == name for v in verdicts) for name in VERDICTS}
    out.write("  ".join(f"{name}: {count}" for name, count in counts.items()) + "\n")


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("database", help="the library database (prompts.db)")
    parser.add_argument(
        "--output-dir", required=True, help="ComfyUI's output directory (history)"
    )
    parser.add_argument("--ids", default="", help="image ids, e.g. 58005-58010,58020")
    parser.add_argument("--prompt", type=int, help="every image linked to this prompt")
    parser.add_argument(
        "--apply", action="store_true", help="write relink/unlink for --ids images"
    )
    parser.add_argument("--json", action="store_true", help="print JSON verdicts")
    args = parser.parse_args(argv)
    try:
        ids = parse_ids(args.ids)
    except ValueError as error:
        parser.error(str(error))
    if not ids and args.prompt is None:
        parser.error("select images with --ids and/or --prompt")
    if args.apply and not ids:
        parser.error("--apply changes only images named with --ids")
    if not os.path.isfile(args.database):
        parser.error(f"no database at {args.database}")

    if not args.apply:
        with tempfile.TemporaryDirectory() as scratch:
            copy = os.path.join(scratch, "review.db")
            backup_database(args.database, copy)
            db = PromptDatabase(copy)
            try:
                verdicts = review(db, ids, args.prompt, args.output_dir)
            finally:
                db.close_all()
        _report(verdicts, args.json, out)
        out.write("Dry run: nothing was changed.\n")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{args.database}.pre-relink_{stamp}"
    backup_database(args.database, backup)
    out.write(f"Backup: {backup}\n")
    db = PromptDatabase(args.database)
    try:
        verdicts = review(db, ids, args.prompt, args.output_dir)
        _report(verdicts, args.json, out)
        result = apply(db, verdicts, ids)
    finally:
        db.close_all()
    out.write(
        f"Applied: {result['relinked']} relinked, {result['unlinked']} unlinked\n"
    )
    return 0


def _report(verdicts, as_json, out):
    if as_json:
        out.write(json.dumps(verdicts, indent=2, sort_keys=True) + "\n")
    else:
        _print(verdicts, out)


if __name__ == "__main__":
    sys.exit(main())
