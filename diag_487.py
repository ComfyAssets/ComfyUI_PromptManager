"""Temporary #487 diagnostics (removed before merge)."""

import logging
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

logging.disable(logging.CRITICAL)
sys.path.insert(0, os.getcwd())
from database import models  # noqa: E402
from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402


def bench(label, fn, n):
    times = []
    for i in range(n):
        t = time.perf_counter()
        fn(i)
        times.append((time.perf_counter() - t) * 1000)
    print(
        f"  {label:44s} median {statistics.median(times):8.2f} ms"
        f"  mean {statistics.mean(times):8.2f} ms"
    )


def run(base):
    d = tempfile.mkdtemp(dir=base)
    print(f"== base {base}")

    def small(i):
        p = os.path.join(d, f"f{i}.txt")
        with open(p, "wb") as h:
            h.write(b"x" * 4096)
        os.unlink(p)

    def fsync(i):
        p = os.path.join(d, f"s{i}.txt")
        with open(p, "wb") as h:
            h.write(b"x" * 4096)
            h.flush()
            os.fsync(h.fileno())
        os.unlink(p)

    def raw(sync):
        def f(i):
            p = os.path.join(d, f"r{sync}{i}.db")
            c = sqlite3.connect(p)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(f"PRAGMA synchronous={sync}")
            c.execute("CREATE TABLE t(x)")
            c.execute("INSERT INTO t VALUES(1)")
            c.commit()
            c.close()
            for s in ("", "-wal", "-shm"):
                if os.path.exists(p + s):
                    os.unlink(p + s)

        return f

    def pdb(i):
        p = os.path.join(d, f"p{i}.db")
        db = PromptDatabase(p)
        db.save_prompt(
            text=f"t{i}",
            category=None,
            tags=[],
            rating=None,
            notes=None,
            prompt_hash=str(i),
        )
        PromptModel.close_all_instances()
        for s in ("", "-wal", "-shm"):
            if os.path.exists(p + s):
                os.unlink(p + s)

    def mkrm(i):
        t = tempfile.mkdtemp(dir=d)
        open(os.path.join(t, "a"), "w").close()
        shutil.rmtree(t)

    bench("create+write+close+unlink 4k file", small, 100)
    bench("same + fsync", fsync, 50)
    bench("mkdtemp + file + rmtree", mkrm, 100)
    bench("raw sqlite WAL db NORMAL", raw("NORMAL"), 30)
    bench("raw sqlite WAL db OFF", raw("OFF"), 30)
    bench("PromptDatabase create+save+close+unlink", pdb, 20)
    orig = models.configure_connection

    def off(conn):
        orig(conn)
        conn.execute("PRAGMA synchronous = OFF")

    models.configure_connection = off
    bench("  same with synchronous=OFF", lambda i: pdb(1000 + i), 20)
    models.configure_connection = orig
    shutil.rmtree(d, ignore_errors=True)


bases = [tempfile.gettempdir()]
if os.environ.get("RUNNER_TEMP"):
    bases.append(os.environ["RUNNER_TEMP"])
for b in bases:
    run(b)
