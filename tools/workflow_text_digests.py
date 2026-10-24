"""Digests of every image record's stored workflow and prompt graph text.

Proves the workflow storage rewrite (#249 S6) kept every text byte for byte.
Run it from this repository on the database before the rewrite (or on its
``prompts.db.pre-workflow-storage_*`` backup) and again after, then compare::

    python3 tools/workflow_text_digests.py prompts.db.pre-workflow-storage_<stamp> \\
        before.sha
    python3 tools/workflow_text_digests.py prompts.db after.sha
    diff before.sha after.sha        # prints nothing when every text is identical

Each line is ``<image id> <sha256 of workflow_data> <sha256 of prompt_metadata>``
(``-`` for none), hashing the exact stored bytes: the inline column before the
rewrite, the document read through the one accessor after it.

Read-only, with no side effects: the database is opened with ``mode=ro``,
and with ``immutable=1`` when no ``-wal``/``-shm`` file exists beside it (a
stopped database or a backup), so SQLite creates none. A live database
already has them and is read through its WAL. Only
``database/workflow_documents.py`` is loaded (by path; it needs only the
standard library), so no package start-up runs and no log file or handler is
added. Run it as the user that runs ComfyUI.
"""

import argparse
import hashlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_documents():
    """``database/workflow_documents.py`` alone, without the ``database`` package."""
    path = _ROOT / "database" / "workflow_documents.py"
    spec = importlib.util.spec_from_file_location("_pm_workflow_documents", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def database_uri(db_path):
    """``mode=ro``, plus ``immutable=1`` when nothing has the database open."""
    path = os.path.abspath(db_path)
    uri = Path(path).as_uri() + "?mode=ro"
    if not any(os.path.exists(path + suffix) for suffix in ("-wal", "-shm")):
        uri += "&immutable=1"
    return uri


def _digest(value):
    if value is None:
        return "-"
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def write_digests(db_path, out):
    """Write one digest line per image record of ``db_path`` to ``out``."""
    documents = _load_documents()
    conn = sqlite3.connect(database_uri(db_path), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.text_factory = bytes  # inline text exactly as stored, even undecodable
        reader = documents.DocumentReader(conn)
        count = 0
        with open(out, "w", encoding="utf-8") as handle:
            for row in conn.execute("SELECT * FROM generated_images ORDER BY id"):
                texts = documents.resolve_image_row(reader, row)
                handle.write(
                    f"{row['id']} {_digest(texts.get('workflow_data'))} "
                    f"{_digest(texts.get('prompt_metadata'))}\n"
                )
                count += 1
        return count
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database", help="prompts.db or one of its backups")
    parser.add_argument("out", help="file to write the digests to")
    args = parser.parse_args(argv)
    count = write_digests(args.database, args.out)
    print(f"{count} image records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
