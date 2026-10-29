"""Repair the links the old scan gave foreign dataset files (#247), on review.

Before #247 the output scan linked each file of a foreign run (a workflow
with no PromptManager node) through the legacy parser, so every file of a
shared-prefix dataset run got the first branch's text. Recorded history
knows which save node wrote each file; the shared ladder
(``utils.file_prompt.decide_file_prompt``, scan policy) uses it
(``recorded-run``) to name the right branch. This tool judges every
``generated_images`` row of a file whose recording runs all have no
PromptManager context:

``relink``  the ladder names a positive by the recorded saver, and its text
            is not the linked prompt's
``keep``    the recorded saver or the inspector names the linked prompt
``skip``    anything else (file gone, no single recorded saver, ambiguous,
            the legacy floor or another step decided)

Dry run (the default) judges a copy of the database (SQLite backup from a
read-only connection) and writes a JSON plan and a Markdown list beside it
(``<plan stem>.list.md``). It also rehearses the apply on a second copy, to
list the prompts that would lose every link with their remaining
references. Deleting those is OFF unless the dry run is given
``--delete-orphans``; the choice is recorded in the plan and rehearsed on a
third copy. The library is never written.

``--apply <plan.json>`` (ComfyUI stopped), only on the library the plan was
made from (its real path, recorded in the plan):

1. refuses unless ``--comfyui-dir`` holds ``main.py``; refuses while a
   ``main.py`` runs from it, anything listens on ``--port``, or any other
   process holds the library, its ``-wal`` or ``-shm`` open
   (``tools/comfyui_stopped.py``; without /proc only with
   ``--i-stopped-comfyui``). ``--rehearsal`` skips the process and port
   check, never the open-file check, and never for that folder's library;
2. saves ``<db>.pre-247-repair_<stamp>`` (or into ``--backup-dir``), never
   over an existing file, and refuses unless the backup passes
   ``integrity_check`` with the library's counts. Opening the library must
   not change its schema (a newer checkout's migration): run the tool from a
   checkout at the live code version. The running check runs once more just
   before writing;
3. in ONE ``BEGIN IMMEDIATE`` transaction: re-judges every planned row and
   refuses on any drift (row moved, prompt text changed, file changed, target
   stored since), creates the target prompts that do not exist (category
   ``scanned``, tag ``auto-scanned``, note ``Auto-scanned from <first file>``),
   points each row at its target and drops its pair links (they named the old
   positive). A file that already has a row for the target keeps that row and
   loses the wrong one (UNIQUE(prompt_id, file_path)). Orphans are deleted only
   when the plan chose it and each is still unreferenced.

Any failed check rolls everything back and exits 2. Before/after table
counts, ``integrity_check`` and ``foreign_key_check`` are printed.

    python3 tools/repair_foreign_links.py <db> --output-dir <ComfyUI output> \\
        --plan repair_plan.json
    python3 tools/repair_foreign_links.py <db> --apply repair_plan.json \\
        --comfyui-dir <ComfyUI folder>

Run from a checkout at the live code version (after the merge: main) with
its venv.
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database.maintenance import ORPHANED_PROMPTS_SQL, _delete_ids  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from database.pair_ingestion import (  # noqa: E402
    SCAN_MARKS,
    file_link_rows,
    mark_created,
)
from database.run_attribution import recorded_output  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from tools.comfyui_stopped import (  # noqa: E402
    NO_PROC,
    comfyui_running,
    open_by_others,
)
from tools.review_image_links import backup_database  # noqa: E402
from utils.execution_outputs import snapshot_output_roots  # noqa: E402
from utils.file_prompt import POSITIVE, Policy, decide_file_prompt  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

PLAN_VERSION = 1
COMFY_PORT = 8188
LIVE_LIBRARY = ("user", "__promptmanager", "prompts.db")
EXCERPT = 80
COUNTED = (
    "prompts",
    "generated_images",
    "prompt_tags",
    "image_prompt_pairs",
    "workflow_documents",
    "prompt_pairs",
    "prompt_library_state",
    "execution_run_contexts",
    "tags",
)
# The ladder steps whose answer is certain enough to keep a link.
KEEP_SOURCES = ("recorded-run", "inspector")
# What must be unchanged since the dry run for a row to be written.
DRIFT_KEYS = (
    "action",
    "file",
    "current_prompt_id",
    "current_text_sha256",
    "file_size",
    "file_mtime_ns",
    "target_text",
    "target_hash",
    "target_prompt_id",
)
# Locations reported only by runs without any PromptManager context.
_FOREIGN_LOCATIONS = (
    "SELECT o.subfolder, o.filename FROM execution_run_outputs o "
    "WHERE o.folder_type = 'output' AND o.root_key = ? AND o.path_style = ? "
    "GROUP BY o.subfolder, o.filename "
    "HAVING SUM(EXISTS (SELECT 1 FROM execution_run_contexts c "
    "WHERE c.run_id = o.run_id)) = 0 "
    "ORDER BY o.subfolder, o.filename"
)


class Refused(Exception):
    """A check failed: nothing is written."""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def metadata_reader():
    """The scan's metadata extraction (PNG text chunks, ffprobe for videos)."""
    api = SimpleNamespace(logger=logging.getLogger("prompt_manager.repair_247"))
    return lambda path: PromptManagerAPI._extract_comfyui_metadata(api, path) or {}


def foreign_rows(db, output_dir):
    """The link rows of every file only foreign runs reported, by id."""
    style = "windows" if os.name == "nt" else "posix"
    root_key = snapshot_output_roots(style, output_dir, output_dir).output_key
    conn = db.model.get_connection()
    rows = {}
    for subfolder, filename in conn.execute(_FOREIGN_LOCATIONS, (root_key, style)):
        path = os.path.join(output_dir, subfolder, filename)
        rows.update((row["id"], row) for row in file_link_rows(conn, path))
    return [rows[key] for key in sorted(rows)]


class Judge:
    """One file's decision per path, for all of its rows."""

    def __init__(self, db, output_dir, read=None):
        self.db = db
        self.output_dir = output_dir
        self.read = read or metadata_reader()
        self.decisions = {}

    def decide(self, path, size):
        if path not in self.decisions:
            self.decisions[path] = decide_file_prompt(
                self.read(path),
                file_name=os.path.basename(path),
                file_size=size,
                in_output=True,
                lookup=lambda: recorded_output(self.db, path, self.output_dir),
                policy=Policy.SCAN,
            )
        return self.decisions[path]

    def row(self, row):
        """The plan entry of one ``generated_images`` row."""
        conn = self.db.model.get_connection()
        path = row["image_path"]
        (text,) = conn.execute(
            "SELECT text FROM prompts WHERE id = ?", (row["prompt_id"],)
        ).fetchone()
        prompt_hash = generate_prompt_hash(text)
        item = {
            "row_id": row["id"],
            "file": path,
            "current_prompt_id": row["prompt_id"],
            "current_text_sha256": sha256(text),
        }
        try:
            stat = os.stat(path)
        except OSError:
            return {**item, "action": "skip", "reason": "file gone"}
        item.update(file_size=stat.st_size, file_mtime_ns=stat.st_mtime_ns)
        decision = self.decide(path, stat.st_size)
        action = action_of(decision, prompt_hash)
        if action == "skip":
            return {
                **item,
                "action": "skip",
                "reason": "not decided by the recorded saver",
                "decision": f"{decision.kind}/{decision.source}",
            }
        target_hash = generate_prompt_hash(decision.text)
        stored = conn.execute(
            "SELECT id FROM prompts WHERE hash = ?", (target_hash,)
        ).fetchone()
        item.update(
            target_text=decision.text,
            target_hash=target_hash,
            target_prompt_id=stored[0] if stored else "new",
            action=action,
            source=decision.source,
        )
        return item


def action_of(decision, linked_hash):
    """``relink``, ``keep`` or ``skip`` for a row linked to ``linked_hash``.

    Only the recorded saver (``recorded-run``) names a new target. A certain
    step (the recorded saver or the inspector) naming the linked prompt keeps
    it; the legacy floor and every other answer are skipped.
    """
    if decision.kind != POSITIVE or decision.source not in KEEP_SOURCES:
        return "skip"
    if generate_prompt_hash(decision.text) == linked_hash:
        return "keep"
    return "relink" if decision.source == "recorded-run" else "skip"


def library_identity(path):
    """How a plan names its library: the case-normalized real path."""
    return os.path.normcase(os.path.realpath(path))


def build_plan(db, output_dir, source_db, read=None):
    """The plan for ``db`` (a copy of ``source_db``)."""
    judge = Judge(db, output_dir, read)
    conn = db.model.get_connection()
    (max_row_id,) = conn.execute("SELECT MAX(id) FROM generated_images").fetchone()
    return {
        "version": PLAN_VERSION,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_db": library_identity(source_db),
        "max_row_id": max_row_id,
        "output_dir": output_dir,
        "rows": [judge.row(row) for row in foreign_rows(db, output_dir)],
        "orphans": [],
        "delete_orphans": False,
    }


def blockers(conn, prompt_id, unused):
    """Why ``prompt_id`` must not be deleted (empty: nothing references it)."""
    found = []
    values = {"id": prompt_id, "tag": SCAN_MARKS.tags[0]}
    checks = {
        "linked images": "SELECT COUNT(*) FROM generated_images WHERE prompt_id=:id",
        "saved pairs": "SELECT COUNT(*) FROM prompt_pairs "
        "WHERE positive_prompt_id=:id OR negative_prompt_id=:id",
        "recorded runs": "SELECT COUNT(*) FROM execution_run_contexts "
        "WHERE positive_prompt_id=:id OR negative_prompt_id=:id",
        "library state": "SELECT COUNT(*) FROM prompt_library_state "
        "WHERE prompt_id=:id",
        "user tags": "SELECT COUNT(*) FROM prompt_tags pt JOIN tags t "
        "ON t.id=pt.tag_id WHERE pt.prompt_id=:id AND t.name != :tag",
    }
    for name, sql in checks.items():
        count = conn.execute(sql, values).fetchone()[0]
        if count:
            found.append(f"{name}: {count}")
    run_count, last_used, rating, category, notes = conn.execute(
        "SELECT run_count, last_used_at, rating, category, notes FROM prompts "
        "WHERE id=?",
        (prompt_id,),
    ).fetchone()
    if run_count or last_used or rating:
        found.append("usage or rating")
    if category not in (None, SCAN_MARKS.category):
        found.append(f"category {category}")
    if notes and not notes.startswith("Auto-scanned from "):
        found.append("notes")
    if prompt_id not in unused:
        found.append("not unused by the app's rule")
    return found


def _rejudge(conn, judge, plan):
    for item in plan["rows"]:
        if item["action"] == "skip":
            continue
        row = conn.execute(
            "SELECT * FROM generated_images WHERE id = ?", (item["row_id"],)
        ).fetchone()
        if row is None:
            raise Refused(f"row {item['row_id']}: gone")
        if generate_prompt_hash(item.get("target_text") or "") != item.get(
            "target_hash"
        ):
            raise Refused(f"row {item['row_id']}: target_text does not match its hash")
        fresh = judge.row(row)
        for key in DRIFT_KEYS:
            if fresh.get(key) != item.get(key):
                raise Refused(
                    f"row {item['row_id']}: {key} changed "
                    f"({item.get(key)!r} -> {fresh.get(key)!r})"
                )


def _targets(conn, db, relinks):
    """``{target hash: prompt id}``, creating each "new" target once."""
    targets = {}
    for item in relinks:
        digest = item["target_hash"]
        if digest in targets:
            continue
        if item["target_prompt_id"] != "new":
            targets[digest] = item["target_prompt_id"]
            continue
        prompt_id, created = db._pair_component_outcome(conn, item["target_text"])
        if not created:
            raise Refused(f"row {item['row_id']}: target prompt already stored")
        mark_created(conn, db, prompt_id, SCAN_MARKS, os.path.basename(item["file"]))
        targets[digest] = prompt_id
    return targets


def _relink(conn, item, target):
    """Point the row at ``target``; True when the wrong row was deleted instead."""
    row_id = item["row_id"]
    correct = [
        row
        for row in file_link_rows(conn, item["file"])
        if row["prompt_id"] == target and row["id"] != row_id
    ]
    if correct:  # UNIQUE(prompt_id, file_path): the correct row stays alone
        if _delete_ids(conn, "generated_images", [row_id]) != 1:
            raise Refused(f"row {row_id}: delete touched no row")
        return True
    # A pair link records the old positive: it goes with the old link
    conn.execute("DELETE FROM image_prompt_pairs WHERE image_id = ?", (row_id,))
    cursor = conn.execute(
        "UPDATE generated_images SET prompt_id = ? WHERE id = ? AND prompt_id = ?",
        (target, row_id, item["current_prompt_id"]),
    )
    if cursor.rowcount != 1:
        raise Refused(f"row {row_id}: relink touched {cursor.rowcount} rows")
    return False


def _audit(conn, relinks):
    """The prompts the relinks leave without any link, with their blockers."""
    sources = {}
    for item in relinks:
        sources.setdefault(item["current_prompt_id"], []).append(item)
    unused = {row[0] for row in conn.execute(ORPHANED_PROMPTS_SQL)}
    audit = []
    for prompt_id in sorted(sources):
        if conn.execute(
            "SELECT 1 FROM generated_images WHERE prompt_id = ?", (prompt_id,)
        ).fetchone():
            continue
        (text,) = conn.execute(
            "SELECT text FROM prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        found = blockers(conn, prompt_id, unused)
        audit.append(
            {
                "id": prompt_id,
                "text_excerpt": " ".join(text.split())[:EXCERPT],
                "text_sha256": sha256(text),
                "images_before": len(sources[prompt_id]),
                "blockers": found,
                "safe": not found,
            }
        )
    return audit


def _delete_orphans(conn, plan):
    chosen = [orphan for orphan in plan["orphans"] if orphan["safe"]]
    unused = {row[0] for row in conn.execute(ORPHANED_PROMPTS_SQL)}
    for orphan in chosen:
        row = conn.execute(
            "SELECT text FROM prompts WHERE id = ?", (orphan["id"],)
        ).fetchone()
        if row is None or sha256(row[0]) != orphan["text_sha256"]:
            raise Refused(f"prompt {orphan['id']}: gone or text changed")
        found = blockers(conn, orphan["id"], unused)
        if found:
            raise Refused(f"prompt {orphan['id']}: {', '.join(found)}")
    ids = [orphan["id"] for orphan in chosen]
    if _delete_ids(conn, "prompts", ids) != len(ids):
        raise Refused("an orphan prompt was not deleted")
    return len(ids)


def apply_plan(db, plan, read=None, before_write=None):
    """Write ``plan`` in one transaction; returns ``(result, orphan audit)``.

    ``before_write()`` runs just before ``BEGIN IMMEDIATE`` (the running
    check). Raises ``Refused`` (and rolls back) on any drift or failed check.
    """
    judge = Judge(db, plan["output_dir"], read)
    relinks = [item for item in plan["rows"] if item["action"] == "relink"]
    if before_write is not None:
        before_write()
    with db._pair_transaction() as conn:
        _rejudge(conn, judge, plan)
        targets = _targets(conn, db, relinks)
        dropped = sum(
            _relink(conn, item, targets[item["target_hash"]]) for item in relinks
        )
        audit = _audit(conn, relinks)
        deleted = _delete_orphans(conn, plan) if plan.get("delete_orphans") else 0
    created = sum(1 for item in relinks if item["target_prompt_id"] == "new")
    result = {
        "relinked": len(relinks) - dropped,
        "duplicates_dropped": dropped,
        "prompts_created": len(
            {i["target_hash"] for i in relinks if i["target_prompt_id"] == "new"}
        ),
        "rows_to_new_prompts": created,
        "kept": sum(1 for item in plan["rows"] if item["action"] == "keep"),
        "orphans_deleted": deleted,
    }
    return result, audit


def counts(path, readonly=True):
    """Table counts plus the integrity and foreign-key checks.

    Read-only for a library; the tool's own backup is opened read-write so
    a WAL file leaves no side files behind once it closes.
    """
    uri = Path(os.path.abspath(path)).as_uri() + ("?mode=ro" if readonly else "")
    conn = sqlite3.connect(uri, uri=True)
    try:
        found = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in COUNTED  # nosec B608 - fixed table names
        }
        found["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        found["foreign_key_check"] = len(
            conn.execute("PRAGMA foreign_key_check").fetchall()
        )
        return found
    finally:
        conn.close()


def _print_counts(before, after, out):
    out.write(f"{'':24}{'before':>12}{'after':>12}\n")
    for key in before:
        out.write(f"{key:24}{before[key]!s:>12}{after[key]!s:>12}\n")


def _summary(plan):
    actions = [item["action"] for item in plan["rows"]]
    return "  ".join(
        f"{name}: {actions.count(name)}" for name in ("relink", "keep", "skip")
    )


def _cell(text, limit=EXCERPT):
    """One table cell: whitespace folded, cut to ``limit`` (None: whole)."""
    folded = " ".join(str(text).split())
    return (folded if limit is None else folded[:limit]).replace("|", "\\|")


def _relative(path, root):
    try:
        return os.path.relpath(path, root)
    except ValueError:  # another drive on Windows
        return path


def markdown(plan, prompt_texts):
    """The maintainer's list of the plan."""
    root = plan["output_dir"]
    rel = lambda path: _relative(path, root)  # noqa: E731
    rows = plan["rows"]
    lines = [
        "# #247 repair plan (dry run)",
        "",
        f"Created {plan['created']}; output folder `{root}`. {_summary(plan)}.",
        "",
        f"## Relink ({sum(i['action'] == 'relink' for i in rows)})",
        "",
        "| row | file | linked prompt | linked text | target | target hash "
        "| target text |",
        "|---:|---|---:|---|---|---|---|",
    ]
    for item in (i for i in rows if i["action"] == "relink"):
        lines.append(
            f"| {item['row_id']} | {_cell(rel(item['file']), None)} | "
            f"{item['current_prompt_id']} | "
            f"{_cell(prompt_texts.get(item['current_prompt_id'], ''))} | "
            f"{item['target_prompt_id']} | `{item['target_hash'][:12]}` | "
            f"{_cell(item['target_text'])} |"
        )
    lines += ["", f"## Keep ({sum(i['action'] == 'keep' for i in rows)})", ""]
    lines += ["| row | file | prompt |", "|---:|---|---:|"]
    for item in (i for i in rows if i["action"] == "keep"):
        lines.append(
            f"| {item['row_id']} | {_cell(rel(item['file']), None)} | "
            f"{item['current_prompt_id']} |"
        )
    lines += ["", f"## Skip ({sum(i['action'] == 'skip' for i in rows)})", ""]
    lines += ["| row | file | prompt | reason |", "|---:|---|---:|---|"]
    for item in (i for i in rows if i["action"] == "skip"):
        reason = item["reason"] + (
            f" ({item['decision']})" if item.get("decision") else ""
        )
        lines.append(
            f"| {item['row_id']} | {_cell(rel(item['file']), None)} | "
            f"{item['current_prompt_id']} | {reason} |"
        )
    choice = "ON (--delete-orphans)" if plan["delete_orphans"] else "OFF"
    lines += [
        "",
        f"## Prompts that would lose every link ({len(plan['orphans'])})",
        "",
        f"Deleting the safe ones: {choice}.",
        "",
        "| id | text | images before | blockers | safe to delete |",
        "|---:|---|---:|---|---|",
    ]
    for orphan in plan["orphans"]:
        lines.append(
            f"| {orphan['id']} | {_cell(orphan['text_excerpt'])} | "
            f"{orphan['images_before']} | {', '.join(orphan['blockers']) or 'none'} | "
            f"{'yes' if orphan['safe'] else 'no'} |"
        )
    simulated = plan.get("simulated")
    if simulated:
        lines += ["", "## Rehearsed on the dry run's copy", ""]
        lines += ["| table | before | after |", "|---|---:|---:|"]
        for key, value in simulated["before"].items():
            lines.append(f"| {key} | {value} | {simulated['after'][key]} |")
    return "\n".join(lines) + "\n"


def _texts(db):
    return dict(
        db.model.get_connection().execute(
            "SELECT id, text FROM prompts WHERE id IN (SELECT prompt_id "
            "FROM generated_images)"
        )
    )


def _rehearse(source, scratch, name, plan):
    """``apply_plan`` on a fresh backup of ``source``: (counts, result, audit)."""
    copy = os.path.join(scratch, name)
    backup_database(source, copy)
    db = PromptDatabase(copy)
    try:
        before = counts(copy)
        result, audit = apply_plan(db, plan)
    finally:
        db.close_all()
    return {"before": before, "after": counts(copy), "result": result}, audit


def dry_run(args, out):
    with tempfile.TemporaryDirectory() as scratch:
        copy = os.path.join(scratch, "judge.db")
        backup_database(args.database, copy)
        db = PromptDatabase(copy)
        try:
            plan = build_plan(db, os.path.abspath(args.output_dir), args.database)
            texts = _texts(db)
        finally:
            db.close_all()
        simulated, audit = _rehearse(args.database, scratch, "relinks.db", plan)
        plan.update(orphans=audit, delete_orphans=bool(args.delete_orphans))
        if plan["delete_orphans"]:
            # The plan as the apply will write it, deletes included
            simulated, _audit = _rehearse(args.database, scratch, "deletes.db", plan)
    plan["simulated"] = simulated
    plan_path = Path(args.plan)
    plan_path.write_text(
        json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    list_path = plan_path.with_name(plan_path.stem + ".list.md")
    list_path.write_text(markdown(plan, texts), encoding="utf-8")
    out.write(f"{_summary(plan)}\n")
    out.write(
        f"Prompts that would lose every link: {len(audit)} "
        f"({sum(o['safe'] for o in audit)} safe to delete; deleting "
        f"{'ON' if plan['delete_orphans'] else 'OFF'})\n"
    )
    out.write(f"Plan: {plan_path}\nList: {list_path}\n")
    out.write("Dry run: nothing was changed.\n")
    return 0


def _is_live(database, comfyui_dir):
    live = os.path.join(comfyui_dir, *LIVE_LIBRARY)
    return library_identity(database) == library_identity(live)


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def schema_of(path):
    """``sqlite_master`` and ``user_version`` from a read-only connection."""
    uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        return master, conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def running_reasons(args, probe, handles):
    """Why writing must not start now (empty: go ahead).

    Another process holding the library open always counts, also in a
    rehearsal; ComfyUI's processes and port count unless rehearsing.
    """
    held = [
        reason
        for reason in handles(args.database)
        if not (reason == NO_PROC and args.i_stopped_comfyui)
    ]
    return held + ([] if args.rehearsal else probe(args.comfyui_dir, args.port))


def _preflight(args, plan, probe, handles):
    """The refusals before any backup or write, as a message (None: go on)."""
    if plan.get("version") != PLAN_VERSION:
        return "not a plan of this tool"
    if args.rehearsal and _is_live(args.database, args.comfyui_dir):
        return f"--rehearsal is for a copy; this is the library of {args.comfyui_dir}"
    if plan.get("source_db") != library_identity(args.database):
        return f"this plan was made for {plan.get('source_db')}"
    if not os.path.isfile(os.path.join(args.comfyui_dir, "main.py")):
        return f"no main.py in {args.comfyui_dir}: not a ComfyUI folder"
    reasons = running_reasons(args, probe, handles)
    if reasons:
        return "ComfyUI may be running: " + "; ".join(reasons)
    return None


def _backup(args, before):
    """The verified backup's path; raises ``Refused``."""
    folder = args.backup_dir or os.path.dirname(os.path.abspath(args.database))
    name = f"{os.path.basename(args.database)}.pre-247-repair_{_stamp()}"
    backup = os.path.join(folder, name)
    if os.path.exists(backup):
        raise Refused(f"the backup {backup} already exists")
    backup_database(args.database, backup)
    saved = counts(backup, readonly=False)
    if saved != before or saved["integrity_check"] != "ok":
        raise Refused(f"the backup {backup} does not match the library")
    return backup


def _open_unchanged(path):
    """``PromptDatabase(path)``; raises ``Refused`` when opening it changed
    its schema (a migration of a newer checkout, committed on open)."""
    schema = schema_of(path)
    db = PromptDatabase(path)
    db.model.get_connection()
    if schema_of(path) != schema:
        db.close_all()
        raise Refused(
            "opening the library with this checkout changed its schema (that "
            "change is committed); run the tool from a checkout at the live "
            "code version"
        )
    return db


def apply(args, out, probe, handles):
    with open(args.apply, encoding="utf-8") as stream:
        plan = json.load(stream)
    refusal = _preflight(args, plan, probe, handles)
    if refusal:
        out.write(f"REFUSED: {refusal}\n")
        return 2
    if args.rehearsal:
        out.write("(rehearsal on a copy: ComfyUI's process and port not checked)\n")
    before = counts(args.database)
    try:
        backup = _backup(args, before)
    except Refused as error:
        out.write(f"REFUSED: {error}\n")
        return 2
    out.write(f"Backup: {backup}\n")

    def still_stopped():
        reasons = running_reasons(args, probe, handles)
        if reasons:
            raise Refused("ComfyUI may be running: " + "; ".join(reasons))

    try:
        db = _open_unchanged(args.database)
    except Refused as error:
        out.write(f"REFUSED: {error}. Backup: {backup}\n")
        return 2
    try:
        result, _audit = apply_plan(db, plan, before_write=still_stopped)
    except Exception as error:
        out.write(
            f"REFUSED (rolled back, nothing changed): {error}. Backup: {backup}\n"
        )
        return 2
    finally:
        db.close_all()
    after = counts(args.database)
    _print_counts(before, after, out)
    out.write(f"Applied: {json.dumps(result, sort_keys=True)}\n")
    if after["integrity_check"] != "ok" or after["foreign_key_check"]:
        out.write(f"WARNING: integrity or foreign-key check failed; restore {backup}\n")
        return 1
    return 0


def main(argv=None, out=sys.stdout, probe=comfyui_running, handles=open_by_others):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("database", help="the library database (prompts.db)")
    parser.add_argument("--output-dir", help="ComfyUI's output folder (dry run)")
    parser.add_argument(
        "--plan", default="repair_foreign_links_plan.json", help="plan to write"
    )
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="record in the plan that the safe orphan prompts are deleted",
    )
    parser.add_argument("--apply", metavar="PLAN", help="write this reviewed plan")
    parser.add_argument("--comfyui-dir", help="the ComfyUI folder (apply)")
    parser.add_argument("--backup-dir", help="where the backup goes (apply)")
    parser.add_argument("--port", type=int, default=COMFY_PORT)
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="apply to a copy while ComfyUI runs (never its own library)",
    )
    parser.add_argument(
        "--i-stopped-comfyui",
        action="store_true",
        help="without /proc: say ComfyUI is stopped (the port is still checked)",
    )
    args = parser.parse_args(argv)
    if not os.path.isfile(args.database):
        parser.error(f"no database at {args.database}")
    if args.apply:
        if not args.comfyui_dir:
            parser.error("--apply needs --comfyui-dir (the running check)")
        return apply(args, out, probe, handles)
    if not args.output_dir:
        parser.error("the dry run needs --output-dir")
    if Path(args.plan).suffix.lower() == ".md":
        parser.error("--plan is the JSON plan; the list is written beside it")
    return dry_run(args, out)


if __name__ == "__main__":
    sys.exit(main())
