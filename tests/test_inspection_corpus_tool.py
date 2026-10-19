"""The read-only corpus tool (``tools/inspection_corpus.py``) on synthetic fixtures.

The committed parity fixtures are copied into a disposable root. The tool must
reproduce the counts their ``vectors.json`` expectations imply, leave the root
byte-for-byte and mtime-for-mtime unchanged, refuse outputs inside the root or the
code tree, keep file names and prompt text out of its output, and compute H1
against a baseline file.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import zlib
from collections import Counter
from pathlib import Path
from unittest import mock

from tools import inspection_corpus as corpus

try:
    from tests import inspection_parity as parity
    from tests.open_handles import assert_closed
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    from open_handles import assert_closed

FIXTURES = Path(parity.__file__).resolve().parent / "fixtures" / "inspection_parity"


def _snapshot(root: Path) -> dict:
    state = {}
    for directory, subdirectories, names in os.walk(root):
        for name in subdirectories + names:
            path = Path(directory, name)
            info = path.lstat()
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else None
            )
            state[path.relative_to(root).as_posix()] = (
                info.st_mtime_ns,
                info.st_size,
                digest,
            )
    return state


def _expected():
    names = {path.stem for path in FIXTURES.glob("*.png")}
    return [
        vector
        for vector in parity.load_vectors()
        if vector["profile"] == "inspection"
        and vector["anchor"] is None
        and vector["fixture"] in names
    ]


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _png(*texts: bytes) -> bytes:
    header = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    body = b"".join(_chunk(b"tEXt", text) for text in texts)
    return corpus.PNG_SIGNATURE + header + body + _chunk(b"IEND", b"")


class CorpusToolTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.base = Path(temporary.name)
        self.root = self.base / "root"
        shutil.copytree(FIXTURES, self.root / "nested")
        (self.root / "nested" / "vectors.json").unlink()
        self.node = shutil.which("node")
        if self.node is None and os.environ.get("CI"):
            self.fail("Node.js is required in CI for corpus facts")

    def _run(self, *extra: str) -> tuple:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = corpus.main(["--root", str(self.root), "--workers", "1", *extra])
        return code, output.getvalue()

    def _rows(self, path: Path) -> list:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()]

    def test_corpus_tool_aggregates_synthetic_directory_read_only(self):
        before = _snapshot(self.root)
        out, summary = self.base / "out.jsonl", self.base / "summary.txt"

        code, printed = self._run("--out", str(out), "--summary", str(summary))

        expected_code = corpus.EXIT_OK if self.node else corpus.EXIT_INVARIANT_FAILED
        self.assertEqual(code, expected_code, printed)
        self.assertEqual(_snapshot(self.root), before)
        expected = _expected()
        rows = self._rows(out)
        self.assertEqual(len(rows), len(expected))
        report = corpus.build_report(rows, None, corpus._STATE.code.cap)
        states = Counter(vector["expect"]["state"] for vector in expected)
        self.assertEqual(report["states"], states)
        codes = Counter(
            str(vector["expect"]["reason_code"] or "null") for vector in expected
        )
        self.assertEqual(report["reason_codes"], codes)
        self.assertEqual(report["selection_kinds"], Counter({"null": len(expected)}))
        self.assertEqual(report["harness_errors"], Counter())
        # An unreadable file is an inspector outcome, not a harness error.
        self.assertEqual(report["metadata_status"]["unreadable"], 1)
        if self.node is not None:
            self.assertEqual(report["invariants"]["harness"]["status"], "PASS")
        for name in ("H2", "H3", "H4", "H5", "H6", "H8"):
            self.assertEqual(report["invariants"][name]["status"], "PASS", name)
        self.assertEqual(report["invariants"]["H1"]["status"], "N/A")
        if self.node is not None:
            known = sum(
                1
                for vector in expected
                if sum(
                    fact["status"] == "known"
                    for fact in (vector["expect"]["facts"] or {}).values()
                )
                >= corpus.FACTS_KNOWN_TARGET
            )
            self.assertEqual(report["facts_known_at_least_4"], known)
            self.assertEqual(report["facts_errors"], Counter())
        self.assertEqual(summary.read_text("utf-8"), printed)
        self.assertIn("\nstates: ", printed)
        text = out.read_text("utf-8") + printed
        for fixture in FIXTURES.glob("*.png"):
            self.assertNotIn(fixture.name, text)

    def test_outputs_inside_root_or_code_tree_are_refused(self):
        before = _snapshot(self.root)
        inside = self.root / "nested" / "out.jsonl"
        code_tree = corpus.TOOL_TREE / "corpus-out.jsonl"
        cases = [
            ["--out", str(inside)],
            ["--out", str(self.base / "ok.jsonl"), "--summary", str(code_tree)],
            [
                "--out",
                str(self.base / "same.txt"),
                "--summary",
                str(self.base / "same.txt"),
            ],
            ["--out", str(self.base)],
        ]
        link = self.base / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
            cases.append(["--out", str(link / "via-link.jsonl")])
        except OSError:  # pragma: no cover - Windows without symlink privilege
            pass
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, printed = self._run(*arguments)
                self.assertEqual(code, corpus.EXIT_REFUSED)
                self.assertIn("inspection_corpus:", printed)
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(code_tree.exists())
        self.assertFalse((self.base / "ok.jsonl").exists())

    def test_h1_is_computed_against_a_baseline_file(self):
        first = self.base / "first.jsonl"
        self._run("--out", str(first), "--with-paths")
        rows = self._rows(first)
        self.assertTrue(all(row["path"].startswith("nested/") for row in rows))
        resolved = [row for row in rows if row["state"] == "resolved"]
        unresolved = [row for row in rows if row["state"] == "unresolved"]
        changed = {resolved[0]["id"], unresolved[0]["id"]}
        baseline = []
        for row in rows:
            if row["id"] == resolved[0]["id"]:
                row = {
                    **row,
                    "prompts": {**row["prompts"], "positive": {"sha256": "0"}},
                }
            if row["id"] == unresolved[0]["id"]:
                row = {**row, "state": "resolved"}
            baseline.append(row)
        baseline_path = self.base / "baseline.jsonl"
        baseline_path.write_text("".join(json.dumps(r) + "\n" for r in baseline))

        code, printed = self._run(
            "--out", str(self.base / "second.jsonl"), "--baseline", str(baseline_path)
        )

        self.assertEqual(code, corpus.EXIT_INVARIANT_FAILED, printed)
        h1 = corpus.invariants(
            self._rows(self.base / "second.jsonl"), baseline, corpus._STATE.code.cap
        )["H1"]
        self.assertEqual(h1["status"], "FAIL")
        self.assertEqual(h1["count"], len(changed))
        self.assertEqual(
            h1["breakdown"],
            {
                "resolved:texts-differ": 1,
                f"unresolved:{unresolved[0]['reason_code']}": 1,
            },
        )
        self.assertIn("  H1 FAIL 2", printed)
        clean = corpus.invariants(rows, rows, corpus._STATE.code.cap)["H1"]
        self.assertEqual((clean["status"], clean["count"]), ("PASS", 0))
        missing = corpus.invariants([], resolved, corpus._STATE.code.cap)["H1"]
        self.assertEqual(missing["breakdown"], {"missing": len(resolved)})

    def test_parallel_workers_match_the_single_process_rows(self):
        single, parallel = self.base / "single.jsonl", self.base / "parallel.jsonl"
        self._run("--out", str(single))
        self._run("--out", str(parallel), "--workers", "2")

        def stable(path):
            rows = self._rows(path)
            return sorted(
                ({k: v for k, v in r.items() if k != "ms"} for r in rows),
                key=lambda r: r["id"],
            )

        self.assertEqual(stable(parallel), stable(single))
        ids = [row["id"] for row in self._rows(parallel)]
        self.assertEqual(ids, sorted(ids))

    def test_symlinks_and_other_files_are_not_read(self):
        outside = self.base / "outside.png"
        shutil.copy(FIXTURES / "f02.png", outside)
        (self.root / "notes.txt").write_text("not media")
        try:
            (self.root / "linked.png").symlink_to(outside)
        except OSError:  # pragma: no cover - Windows without symlink privilege
            self.skipTest("symlinks unavailable")
        extensions = frozenset({".png"})
        found = [rel for _, rel in corpus.media_files(self.root, extensions)]
        self.assertEqual(len(found), len(_expected()))
        self.assertNotIn("linked.png", found)

    def test_missing_root_baseline_or_code_tree_is_refused(self):
        out = str(self.base / "x.jsonl")
        for arguments in (
            ["--root", str(self.base / "absent"), "--out", out],
            ["--root", str(self.root), "--out", out, "--baseline", out + ".none"],
            ["--root", str(self.root), "--out", out, "--code", str(self.base)],
        ):
            with self.subTest(arguments=arguments):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(corpus.main(arguments), corpus.EXIT_REFUSED)

    def test_without_node_facts_fail_h3_and_the_run(self):
        out = self.base / "out.jsonl"
        code, printed = self._run("--out", str(out), "--node", "")
        self.assertEqual(code, corpus.EXIT_INVARIANT_FAILED)
        rows = self._rows(out)
        self.assertTrue(all(row["facts_error"] == "node-unavailable" for row in rows))
        self.assertIn(f"facts errors: node-unavailable {len(rows)}", printed)
        self.assertIn(f"  H3 FAIL {len(rows)}", printed)
        self.assertIn(f"  harness FAIL {len(rows)}", printed)

    def test_a_node_that_exits_fails_the_run(self):
        false = shutil.which("false")
        if false is None:  # pragma: no cover - Windows has no false binary
            self.skipTest("no false binary")
        code, printed = self._run("--out", str(self.base / "o.jsonl"), "--node", false)
        self.assertEqual(code, corpus.EXIT_INVARIANT_FAILED)
        self.assertIn("node-exited", printed)
        self.assertIsNone(corpus._STATE.node)

    def test_a_missing_node_binary_is_refused(self):
        missing = str(self.base / "no-such-node")
        code, printed = self._run(
            "--out", str(self.base / "o.jsonl"), "--node", missing
        )
        self.assertEqual(code, corpus.EXIT_REFUSED)
        self.assertIn("--node", printed)

    def test_harness_exceptions_fail_the_run(self):
        out = self.base / "out.jsonl"
        with mock.patch.object(corpus, "_observe", side_effect=RuntimeError("boom")):
            code, printed = self._run("--out", str(out))
        self.assertEqual(code, corpus.EXIT_INVARIANT_FAILED)
        rows = self._rows(out)
        self.assertEqual({row["error"] for row in rows}, {"RuntimeError"})
        self.assertIn(f"  harness FAIL {len(rows)}", printed)
        self.assertNotIn("boom", out.read_text("utf-8") + printed)


class CorpusHelpersTest(unittest.TestCase):
    def test_malformed_cause_names_only_quirks_outside_inputs(self):
        node = '{"1": {"class_type": "K", "inputs": {"seed": %s}%s}}'
        cases = {
            node % ("1", ', "is_changed": [NaN]'): "non-input",
            node % ("NaN", ""): "input-nonfinite",
            '{"1": {"inputs": {}}}': "shape",
            "[1]": "shape",
            '{"1": {}, "1": {}}': "unparseable",
            "{": "unparseable",
        }
        for text, cause in cases.items():
            with self.subTest(text=text):
                self.assertEqual(corpus.malformed_cause(text), cause)

    def test_png_duplicate_scan_compares_record_bytes(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(assert_closed, self, directory.name)
        path = Path(directory.name, "f.png")
        cases = (
            ((b"prompt\0{}", b"prompt\0{}"), True),
            ((b"prompt\0{}", b"prompt\0[]"), False),
            ((b"prompt\0{}", b"other\0x", b"other\0y"), None),
        )
        for texts, expected in cases:
            with self.subTest(texts=texts):
                path.write_bytes(_png(*texts))
                self.assertIs(corpus.png_duplicates_identical(path), expected)
        path.write_bytes(b"not a png")
        self.assertIsNone(corpus.png_duplicates_identical(path))

    def test_percentile_is_nearest_rank(self):
        self.assertIsNone(corpus.percentile([], 0.5))
        self.assertEqual(corpus.percentile([3, 1, 2], 0.5), 2)
        self.assertEqual(corpus.percentile(list(range(1, 101)), 0.99), 99)

    def test_h8_fails_on_slow_files_or_responses_over_the_cap(self):
        fast = [{"id": "a", "ext": ".png", "ms": 1.0}]
        self.assertEqual(corpus.invariants(fast, None, 8)["H8"]["status"], "PASS")
        for rows in (
            [{"id": "a", "ext": ".png", "ms": corpus.MAX_MS}],
            [{"id": "a", "ext": ".png", "ms": corpus.H8_FLOOR_MS + 1}],
            [{"id": "a", "ext": ".png", "ms": 1.0, "over_cap": True}],
        ):
            with self.subTest(rows=rows):
                result = corpus.invariants(rows, fast, 8)["H8"]
                self.assertEqual(result["status"], "FAIL")
        # A tiny baseline p99 cannot make H8 flap below the floor.
        noisy = [{"id": "a", "ext": ".png", "ms": 3.0}]
        self.assertEqual(corpus.invariants(noisy, fast, 8)["H8"]["status"], "PASS")

    def test_h5_excludes_negative_only_absence(self):
        def row(positive, state):
            return {
                "ext": ".png",
                "records": ["prompt"],
                "state": state,
                "prompts": {"positive": {"status": positive}},
            }

        rows = [row("resolved", "absent"), row("absent", "absent"), row("absent", "x")]
        self.assertEqual(corpus.invariants(rows, None, 8)["H5"]["count"], 2)
        no_record = {**row("absent", "absent"), "records": ["workflow"]}
        self.assertEqual(corpus.invariants([no_record], None, 8)["H5"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
