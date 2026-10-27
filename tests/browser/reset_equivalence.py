"""Prove the scratch-seeded resets leave the state the old resets left (#384).

Run by hand on Linux (the browser server's platform):

    python -m tests.browser.reset_equivalence

It starts the browser server three times: twice seeding on the live database
as before #384 (``PM_TEST_SEED_ON_LIVE=1``) and once with the scratch seeder.
Each server gets the same sequence: every reset kind of ``/__test__/reset``
and ``/__test__/concept/reset``, with other fixtures' resets and API writes
between them. After every reset it reads every table's rows (by rowid), the
output and input file trees (size, SHA-256, mtime) and the response body.

Values that differ between the two old runs are wall-clock values (created_at,
an unpinned concept "now", file mtimes); they are masked. Everything else must
be equal between the old runs and the new one. Exit status 0 means equal.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
READY_TIMEOUT_S = 60
CLOCK = "<clock>"

RESETS = [
    ("/__test__/reset", {}),
    ("/__test__/reset", {"extra_images": 2}),
    ("/__test__/reset", {"extra_images": 108}),
    ("/__test__/reset", {"include_video": True}),
    ("/__test__/reset", {"prompt_inspector": True}),
    ("/__test__/reset", {"native_library": True}),
    ("/__test__/reset", {"native_library": True, "extra_prompts": 125}),
    ("/__test__/reset", {"native_library": True, "extra_variants": 41}),
    ("/__test__/reset", {"workspace_visual": True}),
    ("/__test__/reset", {"full_inspector": True}),
    (
        "/__test__/reset",
        {"full_inspector": True, "include_video": True, "extra_images": 2},
    ),
    ("/__test__/reset", {"chronological_history": True}),
    ("/__test__/reset", {"chronological_history": True, "history_runs": 85}),
    ("/__test__/concept/reset", {}),
    ("/__test__/concept/reset", {"extra_images": 100}),
    ("/__test__/concept/reset", {"seedless_images": 2, "short_seedless": True}),
    ("/__test__/concept/reset", {"now": 1_780_000_000}),
]
# Writes by other fixtures and the API between resets; the resets must keep or
# replace them exactly as before.
DIRTY = [
    ("/__test__/tagging/reset", {}),
    ("/__test__/system/reset", None),
    (
        "/__test__/saved-views/raw",
        {"page": "images", "name": "dirty", "state": "{}"},
    ),
]


class Server:
    def __init__(self, on_live: bool) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="pm-equivalence-")
        env = {**os.environ, "TMPDIR": self.tmp.name}
        env.update(TEMP=self.tmp.name, TMP=self.tmp.name)
        env.pop("PM_TEST_SEED_ON_LIVE", None)
        if on_live:
            env["PM_TEST_SEED_ON_LIVE"] = "1"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.browser.server",
                "--port",
                "0",
                "--exit-on-stdin-eof",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        line = self.process.stdout.readline().decode()
        if not line.startswith("PM_TEST_SERVER_PORT="):
            raise RuntimeError(f"no port line: {line!r}")
        self.base = f"http://127.0.0.1:{int(line.split('=', 1)[1])}"
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            try:
                self.post_json("GET", "/prompt_manager/test", None)
                break
            except OSError:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise
                time.sleep(0.1)
        (self.root,) = Path(self.tmp.name).glob("pm-browser-*")

    def post_json(self, method: str, path: str, data) -> dict:
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            self.base + path,
            data=body if method == "POST" else None,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
            text = response.read().decode()
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text[:200]}

    def stop(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=READY_TIMEOUT_S)
        self.process.stdout.close()
        self.tmp.cleanup()


def read_tables(path: Path) -> dict:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = {}
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")]
            try:
                cursor = conn.execute(f"SELECT rowid, * FROM {quoted} ORDER BY rowid")
                columns = ["rowid", *columns]
            except sqlite3.OperationalError:
                cursor = conn.execute(f"SELECT * FROM {quoted} ORDER BY 1")
            rows = [
                [value.hex() if isinstance(value, bytes) else value for value in row]
                for row in cursor
            ]
            tables[name] = {"columns": columns, "rows": rows}
        return tables
    finally:
        conn.close()


def read_files(root: Path) -> dict:
    files = {}
    for top in ("output", "input"):
        for path in sorted((root / top).rglob("*")):
            if path.is_file():
                stat = path.stat()
                files[path.relative_to(root).as_posix()] = [
                    stat.st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    stat.st_mtime_ns,
                ]
    return files


def flatten(value, prefix="") -> dict:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}/{key}"))
        return out
    if isinstance(value, list):
        out = {f"{prefix}#len": len(value)}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def snapshot(server: Server, response: dict) -> dict:
    flat = {}
    for table, data in read_tables(server.root / "browser.db").items():
        flat[f"table {table}#rows"] = len(data["rows"])
        for index, row in enumerate(data["rows"]):
            for column, value in zip(data["columns"], row):
                flat[f"table {table}[{index}].{column}"] = value
    for name, (size, digest, mtime) in read_files(server.root).items():
        flat[f"file {name}"] = [size, digest]
        flat[f"file {name}#mtime"] = mtime
    flat["files#count"] = sum(
        1 for key in flat if key.startswith("file ") and "#" not in key
    )
    for key, value in flatten(response).items():
        flat[f"response{key}"] = value
    # Each server has its own temp root and a random storage epoch; name them
    # instead of masking every column that carries them.
    epoch = flat.get("table execution_history_metadata[0].storage_epoch")
    names = [(str(server.root), "<root>")]
    if isinstance(epoch, str) and epoch:
        names.append((epoch, "<epoch>"))
    for key, value in flat.items():
        if isinstance(value, str):
            for text, name in names:
                value = value.replace(text, name)
            flat[key] = value
    return flat


def run(on_live: bool) -> list[dict]:
    server = Server(on_live)
    try:
        states = []
        for index, (path, data) in enumerate(RESETS * 2):
            dirty_path, dirty_data = DIRTY[index % len(DIRTY)]
            server.post_json("POST", dirty_path, dirty_data)
            started = time.monotonic()
            response = server.post_json("POST", path, data)
            elapsed = time.monotonic() - started
            print(f"  {'old' if on_live else 'new'} {path} {data}: {elapsed:.2f}s")
            states.append(snapshot(server, response))
        return states
    finally:
        server.stop()


def column_key(key: str) -> str:
    """``table t[3].created_at`` -> ``table t.created_at`` (mask whole columns)."""
    if key.startswith("table ") and "[" in key:
        head, _, tail = key.partition("[")
        return head + tail.partition("]")[2]
    return key


def main() -> int:
    old_a, old_b, new = run(True), run(True), run(False)
    failures = 0
    masked_all = set()
    for step, (path, data) in enumerate(RESETS * 2):
        a, b, n = old_a[step], old_b[step], new[step]
        masked = {
            column_key(key)
            for key in set(a) | set(b)
            if a.get(key, CLOCK) != b.get(key, CLOCK)
        }
        masked_all |= masked
        keys = set(a) | set(n)
        diffs = [
            key
            for key in sorted(keys)
            if column_key(key) not in masked
            and a.get(key, "<missing>") != n.get(key, "<missing>")
        ]
        tables = len(
            {k.split("[")[0].split("#")[0] for k in a if k.startswith("table ")}
        )
        status = "EQUAL" if not diffs else f"DIFFERENT ({len(diffs)} values)"
        print(
            f"{status}: {path} {json.dumps(data)}: {tables} tables, "
            f"{a['files#count']} files, {len(keys)} values, "
            f"{len(masked)} masked columns"
        )
        for key in diffs[:20]:
            old, now = a.get(key, "<missing>"), n.get(key, "<missing>")
            print(f"    {key}: old {old!r} new {now!r}")
        failures += bool(diffs)
    print("masked (differ between the two old runs):")
    for key in sorted(masked_all):
        print(f"    {key}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
