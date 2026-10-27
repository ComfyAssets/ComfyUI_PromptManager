"""Run a browser-test fixture seeder off the live database, then publish once (#384).

The seeders make hundreds of small commits. On a slow CI disk the live
database's commits queue behind WAL checkpoint syncs and a reset took over
30 s. ``ScratchSeeder.run`` instead:

1. copies the live database into a scratch database (SQLite backup API) whose
   connections never sync (a crash loses only the copy);
2. runs the unchanged seeder against an API proxy whose ``db`` is the scratch
   database; everything else (files, thumbnails, caches, command state) is
   the live API's, written exactly as before;
3. replaces the rows of every table the seed changed in the live database, in
   one transaction on the live connection. Tables the seed left alone are not
   touched, so state another fixture owns stays as it was.

A commit by any other live connection while the seed ran fails the reset
instead of being overwritten (``PRAGMA data_version``).
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import weakref
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Set to 1 to run seeders on the live database as before #384 (the equivalence
# proof, tests/browser/reset_equivalence.py, compares both ways).
SEED_ON_LIVE_ENV = "PM_TEST_SEED_ON_LIVE"
SCRATCH_SCHEMA = "reset_seed"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection, schema: str = "main") -> list[str]:
    rows = conn.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master WHERE type = 'table' "
        "ORDER BY name"
    ).fetchall()
    for name, sql in rows:
        if sql and sql.lstrip().upper().startswith("CREATE VIRTUAL"):
            raise NotImplementedError(f"virtual table {name} cannot be copied")
    return [name for name, _sql in rows]


def _columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    """Writable columns (hidden and generated ones excluded), plus ``rowid``
    when the table has a rowid that no INTEGER PRIMARY KEY column carries."""
    info = conn.execute(f"PRAGMA {schema}.table_xinfo({_quote(table)})").fetchall()
    columns = [row[1] for row in info if row[6] == 0]
    keys = [row for row in info if row[5]]
    sql = conn.execute(
        f"SELECT sql FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()[0]
    without_rowid = sql is not None and "WITHOUT ROWID" in sql.upper()
    integer_key = len(keys) == 1 and str(keys[0][2]).upper() == "INTEGER"
    if not without_rowid and not integer_key and table != "sqlite_sequence":
        columns.insert(0, "rowid")
    return columns


def table_digests(conn: sqlite3.Connection, schema: str = "main") -> dict[str, str]:
    """A digest of every table's rows (rowids included), by table name."""
    result = {}
    for table in _tables(conn, schema):
        selected = ", ".join(
            _quote(c) if c != "rowid" else "rowid"
            for c in _columns(conn, table, schema)
        )
        digest = hashlib.sha256()
        rows = conn.execute(f"SELECT {selected} FROM {schema}.{_quote(table)}")
        for row in sorted(repr(tuple(row)) for row in rows):
            digest.update(row.encode("utf-8", "surrogatepass"))
            digest.update(b"\n")
        result[table] = digest.hexdigest()
    return result


class _SeedApi:
    """The live API, except that ``db`` is the scratch database."""

    def __init__(self, api: Any, db: Any) -> None:
        object.__setattr__(self, "_api", api)
        object.__setattr__(self, "db", db)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._api, name, value)


class ScratchSeeder:
    """Seed into a scratch copy and publish the changed tables in one commit."""

    def __init__(self, api: Any, directory: Path, *, on_live: bool = False) -> None:
        self._api = api
        self._directory = Path(directory)
        self._on_live = on_live
        self._scratch = None
        self._unsynced: "weakref.WeakSet[sqlite3.Connection]" = weakref.WeakSet()

    def run(self, seed: Callable[[Any], T]) -> T:
        """Run ``seed(api)`` (blocking; call it in an executor thread)."""
        if self._on_live:
            return seed(self._api)
        live = self._api.db.model.get_connection()
        if live.in_transaction:
            raise RuntimeError("the live connection has an open transaction")
        version = live.execute("PRAGMA data_version").fetchone()[0]
        scratch = self._scratch_database()
        target = self._scratch_connection(scratch)
        with target.using():
            live.backup(target)
        before = table_digests(target)
        result = seed(_SeedApi(self._api, scratch))
        target = self._scratch_connection(scratch)
        if target.in_transaction:
            raise RuntimeError("the seed left a scratch transaction open")
        after = table_digests(target)
        changed = [table for table in after if after[table] != before.get(table)]
        if set(before) != set(after):
            raise RuntimeError("the seed changed the database schema")
        if live.execute("PRAGMA data_version").fetchone()[0] != version:
            raise RuntimeError("the live database changed while the seed ran")
        self._publish(live, scratch.model.db_path, changed, version)
        return result

    def _scratch_database(self):
        if self._scratch is None:
            from database.operations import PromptDatabase

            self._directory.mkdir(parents=True, exist_ok=True)
            self._scratch = PromptDatabase(str(self._directory / "seed.db"))
        return self._scratch

    def _scratch_connection(self, scratch) -> sqlite3.Connection:
        """The calling thread's scratch connection, set never to sync."""
        conn = scratch.model.get_connection()
        if conn not in self._unsynced:
            conn.execute("PRAGMA synchronous = OFF")
            self._unsynced.add(conn)
        return conn

    @staticmethod
    def _publish(
        live: sqlite3.Connection, path: str, tables: list[str], version: int
    ) -> None:
        """Replace ``tables`` with the scratch rows in one live transaction.

        ``version`` is the live ``data_version`` read before the seed. It is
        checked again under the write lock: a writer whose transaction was
        open during the seed commits before ``BEGIN IMMEDIATE`` returns, and
        its rows must fail the reset instead of being overwritten.
        """
        if not tables:
            return
        foreign_keys = live.execute("PRAGMA foreign_keys").fetchone()[0]
        # Rows are copied as they are; no cascade may reach a table left alone.
        live.execute("PRAGMA foreign_keys = OFF")
        try:
            live.execute(f"ATTACH DATABASE ? AS {SCRATCH_SCHEMA}", (path,))
            try:
                live.execute("BEGIN IMMEDIATE")
                try:
                    if live.execute("PRAGMA data_version").fetchone()[0] != version:
                        raise RuntimeError(
                            "the live database changed while the seed ran"
                        )
                    for table in tables:
                        columns = ", ".join(
                            _quote(c) if c != "rowid" else "rowid"
                            for c in _columns(live, table)
                        )
                        name = _quote(table)
                        live.execute(f"DELETE FROM main.{name}")
                        live.execute(
                            f"INSERT INTO main.{name} ({columns}) "
                            f"SELECT {columns} FROM {SCRATCH_SCHEMA}.{name}"
                        )
                    live.commit()
                except BaseException:
                    live.rollback()
                    raise
            finally:
                live.execute(f"DETACH DATABASE {SCRATCH_SCHEMA}")
        finally:
            live.execute(f"PRAGMA foreign_keys = {int(foreign_keys)}")

    def close(self) -> None:
        """Close every scratch connection, then remove the scratch files."""
        if self._on_live:
            return
        if self._scratch is not None:
            self._scratch.close_all()
            self._scratch = None
        shutil.rmtree(self._directory, ignore_errors=True)
