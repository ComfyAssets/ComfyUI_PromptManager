"""Hang guards for waits on disk-bound work (tests only).

A test that waits for a job or handoff whose work is file and SQLite I/O
(a safety backup with fsync, a restore, hashing and unlinking files) waits
on that state; the ceiling below only ends a real hang. Hosted Windows
runners stall disk I/O for tens of seconds at a time: a restore took over
10 s (merge queue, #382), and on PR #405 every test setup in a 50 s window
took up to 9 s against 0.1-0.6 s around it while the test bodies stayed
fast, so a one-file duplicate cleanup missed a 10 s bound (#409).

The ceiling is per wait and stays under the suite's 120 s per-test hang
watchdog. A test with several such waits in a row can reach the watchdog
first; either way a real hang fails, the watchdog with every thread's stack.
"""

DISK_BOUND_SECONDS = 90
