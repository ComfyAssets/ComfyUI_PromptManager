"""Read-only inspection corpus tool for the #240 invariants (H1 to H8).

Runs the workspace current-file inspection path (``inspect_current_file`` then
``encode_inspection_response``) of a code tree on every servable media file under
``--root``, and hands each exact response to the workspace's JS facts. Example::

    python3 tools/inspection_corpus.py --root OUTPUT_DIR --out SCRATCH/after.jsonl \\
        --workers 8 --baseline SCRATCH/before.jsonl --summary SCRATCH/after.txt

``--code`` selects the tree whose Python and ``web/js`` are imported (default: the
tree holding this tool), so a copy of the tool can measure an older commit. Run
it against a base checkout first and pass that output as ``--baseline``.

Read-only by construction: media is opened only for reading, symlinks are not
followed, no database or config is imported, bytecode is not written, and
``--out``/``--summary`` are refused inside the scanned root or a code tree. The
JSONL rows and the summary carry counts, statuses and SHA-256 digests of resolved
role texts, never file names or prompt text, unless ``--with-paths`` is given.
"""

import argparse
import hashlib
import importlib
import importlib.util
import json
import math
import multiprocessing
import os
import shutil
import stat
import struct
import subprocess  # nosec B404 - fixed local Node harness, no shell
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

TOOL_TREE = Path(__file__).resolve().parents[1]
FACTS_HARNESS = Path(__file__).resolve().with_name("inspection_corpus_facts.mjs")
SERVICE = Path("py", "api", "inspection_service.py")
LIBRARY = {"status": "none"}
RECORD_KEYS = ("prompt", "parameters", "workflow", "comfyassets_prompt_pairs")
ROLE_RECORDS = frozenset({"prompt", "parameters"})
FACTS_KNOWN_TARGET = 4
TOP_CLASSES = 20
BASELINE_P99_MS = 520
P99_FACTOR = 2
MAX_MS = 5000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_TEXT_CHUNKS = (b"tEXt", b"zTXt", b"iTXt")
MAX_PNG_CHUNKS = 10_000
HASH_BLOCK = 1 << 20
MAX_JSON_DEPTH = 64
EXIT_OK, EXIT_INVARIANT_FAILED, EXIT_REFUSED = 0, 1, 2

_STATE = SimpleNamespace(code=None, node=None)


class Refused(Exception):
    """An argument would make the run write somewhere it must not."""


def _inside(path: Path, area: Path) -> bool:
    child = os.path.normcase(os.path.realpath(path))
    parent = os.path.normcase(os.path.realpath(area))
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives on Windows
        return False


def _check_outputs(args) -> None:
    areas = [("--root", args.root), ("the code tree", args.code)]
    if (TOOL_TREE / SERVICE).is_file():  # a copied tool may live in scratch
        areas.append(("this tool's code tree", TOOL_TREE))
    outputs = [("--out", args.out)] + (
        [("--summary", args.summary)] if args.summary else []
    )
    for label, path in outputs:
        if Path(path).is_dir():
            raise Refused(f"{label} is a directory")
        for name, area in areas:
            if _inside(Path(path), area):
                raise Refused(f"refusing to write {label} inside {name}")
    targets = [os.path.realpath(path) for _, path in outputs]
    if args.baseline:
        targets.append(os.path.realpath(args.baseline))
    if len(set(map(os.path.normcase, targets))) != len(targets):
        raise Refused("--out, --summary and --baseline must be different files")


def _load_code(code_dir: Path) -> SimpleNamespace:
    """Import only the inspection modules of ``code_dir``: no routes, no database."""
    location = code_dir / SERVICE
    if not location.is_file():
        raise Refused("--code is not a PromptManager code tree")
    loaded = sys.modules.get("utils")
    if loaded is None:
        sys.path.insert(0, str(code_dir))
    elif not any(_inside(Path(p), code_dir) for p in loaded.__path__):
        raise Refused("another code tree's utils package is already imported")
    spec = importlib.util.spec_from_file_location("_corpus_inspection", location)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    response = importlib.import_module("utils.inspection_response")
    inspector = importlib.import_module("utils.prompt_inspector")
    return SimpleNamespace(
        inspect=service.inspect_current_file,
        extensions=frozenset(service.SERVABLE_EXTENSIONS),
        encode=response.encode_inspection_response,
        cap=response.MAX_RESPONSE_BYTES,
        read=inspector.read_inspection_file,
        read_error=inspector.MetadataReadError,
    )


def _start_node(node: str | None, code_dir: Path):
    if not node:
        return None
    return subprocess.Popen(  # nosec B603 - fixed local harness, no shell
        [node, str(FACTS_HARNESS), str(code_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _stop_node() -> None:
    process, _STATE.node = _STATE.node, None
    if process is not None:
        process.stdin.close()
        process.wait()
        process.stdout.close()


def _init_worker(code_dir: str, node: str | None) -> None:
    sys.dont_write_bytecode = True  # never write into the measured code tree
    _STATE.code = _load_code(Path(code_dir))
    _STATE.node = _start_node(node, Path(code_dir))


def _facts(body: bytes) -> dict:
    process = _STATE.node
    if process is None:
        return {"facts": None, "facts_error": "node-unavailable"}
    try:
        process.stdin.write(body + b"\n")
        process.stdin.flush()
        line = process.stdout.readline()
    except OSError:
        line = b""
    if not line:
        _STATE.node = None
        return {"facts": None, "facts_error": "node-exited"}
    facts = json.loads(line)
    if "error" in facts:
        return {"facts": None, "facts_error": facts["error"]}
    known = sum(1 for fact in facts.values() if fact["status"] == "known")
    return {"facts": facts, "facts_known": known}


def _digest(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _roles(prompts: dict) -> dict:
    return {
        role: {
            "status": side.get("status"),
            "source": side.get("source"),
            "sha256": (
                _digest(side.get("text")) if side.get("status") == "resolved" else None
            ),
        }
        for role, side in prompts.items()
    }


def _texts(side_pair: dict) -> tuple:
    return tuple(
        (side_pair.get(role) or {}).get("text") for role in ("positive", "negative")
    )


def _resolution(response: dict) -> dict:
    resolution = response["resolution"]
    candidates = resolution.get("candidates") or []
    node = resolution.get("node")
    resolved = _texts(response["prompts"])
    return {
        "state": resolution.get("state"),
        "reason": resolution.get("reason"),
        "reason_code": resolution.get("reason_code"),
        "selection": resolution.get("selection"),
        "node_class": node.get("class_type") if isinstance(node, dict) else None,
        "candidate_count": resolution.get("candidate_count", len(candidates)),
        "h2": resolution.get("state") == "resolved"
        and any(_texts(candidate) != resolved for candidate in candidates),
    }


def _nonfinite(value: object) -> bool:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            return True
        if depth < MAX_JSON_DEPTH and isinstance(item, (dict, list)):
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return False


def _unique(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def malformed_cause(text: str) -> str:
    """Why a prompt record may be malformed: ``non-input`` means only quirks outside
    node ``inputs`` (``_meta``, ``is_changed``, nonfinite numbers) are left."""
    try:
        tree = json.loads(text, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        return "unparseable"
    nodes = tree.values() if isinstance(tree, dict) else None
    if nodes is None or not all(
        isinstance(node, dict)
        and isinstance(node.get("class_type"), str)
        and isinstance(node.get("inputs", {}), dict)
        for node in nodes
    ):
        return "shape"
    if any(_nonfinite(node.get("inputs")) for node in nodes):
        return "input-nonfinite"
    return "non-input"


def _hash_chunk(handle, length: int) -> tuple:
    digest, keyword, remaining = hashlib.sha256(), b"", length
    while remaining:
        block = handle.read(min(HASH_BLOCK, remaining))
        if not block:
            break
        if remaining == length:
            keyword = block.split(b"\0", 1)[0]
        digest.update(block)
        remaining -= len(block)
    return keyword.decode("latin-1"), digest.digest()


def png_duplicates_identical(path: Path) -> bool | None:
    """True when every duplicated record key repeats byte-identical chunk data."""
    seen: dict = {}
    with open(path, "rb") as handle:
        if handle.read(8) != PNG_SIGNATURE:
            return None
        for _ in range(MAX_PNG_CHUNKS):
            head = handle.read(8)
            if len(head) < 8:
                break
            length, kind = struct.unpack(">I4s", head)
            if kind in PNG_TEXT_CHUNKS:
                keyword, digest = _hash_chunk(handle, length)
                if keyword in RECORD_KEYS:
                    seen.setdefault(keyword, []).append(digest)
                handle.seek(4, os.SEEK_CUR)
            else:
                handle.seek(length + 4, os.SEEK_CUR)
            if kind == b"IEND":
                break
    repeated = [digests for digests in seen.values() if len(digests) > 1]
    if not repeated:
        return None
    return all(len(set(digests)) == 1 for digests in repeated)


def _read_evidence(path: Path, row: dict) -> None:
    code = _STATE.code
    try:
        code.read(path)
        row["read_error"] = None
    except code.read_error as error:
        row["read_error"] = str(error)
    if row["read_error"] == "Duplicate prompt metadata" and row["ext"] == ".png":
        row["duplicates_identical"] = png_duplicates_identical(path)


def _metadata(response: dict, inspection) -> dict:
    metadata = response["metadata"]
    records = set() if inspection is None else set(inspection.metadata)
    records |= set(getattr(inspection, "limited_records", ()) or ())
    row = {
        "records": sorted(records & set(RECORD_KEYS)),
        "metadata_status": metadata["status"],
        "omitted_reason": metadata["omitted_reason"],
        "graphs": {
            "prompt": metadata["prompt_graph"]["status"],
            "workflow": metadata["workflow_graph"]["status"],
        },
        "record_formats": metadata["record_formats"],
    }
    text = None if inspection is None else inspection.metadata.get("prompt")
    if row["graphs"]["prompt"] == "malformed" and isinstance(text, str):
        row["malformed_cause"] = malformed_cause(text)
    return row


def _observe(path: Path, rel: str, row: dict) -> None:
    code = _STATE.code
    row["size"] = path.stat().st_size
    _read_evidence(path, row)
    started = time.perf_counter()
    observed = code.inspect(path, source_label="corpus", saved=None, library=LIBRARY)
    image = {"root_index": 0, "path": rel, "filename": path.name}
    try:
        body = code.encode(image=image, root_scope="0" * 64, **observed)
    except ValueError:
        row["ms"] = round((time.perf_counter() - started) * 1000, 1)
        row["over_cap"] = True
        return
    row["ms"] = round((time.perf_counter() - started) * 1000, 1)
    row["response_bytes"] = len(body)
    row["over_cap"] = len(body) > code.cap
    response = json.loads(body)
    row["prompts"] = _roles(response["prompts"])
    row.update(_resolution(response))
    row.update(_metadata(response, observed["inspection"]))
    row.update(_facts(body))


def file_id(rel: str) -> str:
    """Stable, name-free join key for ``--baseline`` comparisons."""
    return hashlib.sha256(rel.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def inspect_one(task: tuple) -> dict:
    path, rel, with_paths = Path(task[0]), task[1], task[2]
    row = {"id": file_id(rel), "ext": path.suffix.lower()}
    if with_paths:
        row["path"] = rel
    try:
        _observe(path, rel, row)
    except Exception as error:  # one file never stops the corpus; class only
        row["error"] = type(error).__name__
    return row


def media_files(root: Path, extensions: frozenset):
    """Regular servable files under ``root``; symlinks are neither followed nor read."""
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        subdirectories.sort()
        for name in sorted(names):
            path = Path(directory, name)
            if path.suffix.lower() not in extensions:
                continue
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                yield str(path), path.relative_to(root).as_posix()


def _rows(tasks: list, workers: int, code_dir: Path, node: str | None):
    if workers <= 1:
        _STATE.node = _start_node(node, code_dir)
        try:
            for task in tasks:
                yield inspect_one(task)
        finally:
            _stop_node()
        return
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, _init_worker, (str(code_dir), node)) as pool:
        yield from pool.imap_unordered(inspect_one, tasks, chunksize=4)


def percentile(values: list, fraction: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _fact_reasons(row: dict) -> set:
    return {fact.get("reason") for fact in (row.get("facts") or {}).values()}


def _h1(rows: list, baseline: list | None) -> dict:
    if baseline is None:
        return {"status": "N/A", "detail": "no --baseline"}
    current = {row["id"]: row for row in rows}
    before = [row for row in baseline if row.get("state") == "resolved"]
    regressions = Counter()
    for old in before:
        new = current.get(old["id"])
        if new is None:
            regressions["missing"] += 1
        elif new.get("state") != "resolved":
            regressions[f"{new.get('state')}:{new.get('reason_code')}"] += 1
        elif _role_digests(new) != _role_digests(old):
            regressions["resolved:texts-differ"] += 1
    resolved_now = sum(1 for row in rows if row.get("state") == "resolved")
    return {
        "status": "FAIL" if regressions else "PASS",
        "count": sum(regressions.values()),
        "detail": f"resolved at baseline {len(before)}, now {resolved_now}",
        "breakdown": dict(sorted(regressions.items())),
    }


def _role_digests(row: dict) -> tuple:
    prompts = row.get("prompts") or {}
    return tuple(
        (
            (prompts.get(role) or {}).get("status"),
            (prompts.get(role) or {}).get("sha256"),
        )
        for role in ("positive", "negative")
    )


def _false_absent(row: dict) -> bool:
    if not ROLE_RECORDS & set(row.get("records") or ()):
        return False
    positive = ((row.get("prompts") or {}).get("positive") or {}).get("status")
    # Negative-only absence beside a resolved positive is honest (ledger ruling).
    return (
        positive == "absent" or row.get("state") == "absent" and positive != "resolved"
    )


def _counted(count: int, detail: str = "") -> dict:
    return {"status": "FAIL" if count else "PASS", "count": count, "detail": detail}


def _h8(rows: list, baseline: list | None, cap: int) -> dict:
    timings = [row["ms"] for row in rows if "ms" in row]
    p99, peak = percentile(timings, 0.99), max(timings, default=None)
    base = percentile([row["ms"] for row in baseline or () if "ms" in row], 0.99)
    limit = P99_FACTOR * (base if base is not None else BASELINE_P99_MS)
    over_cap = sum(1 for row in rows if row.get("over_cap"))
    failed = (p99 or 0) > limit or (peak or 0) >= MAX_MS or over_cap
    return {
        "status": "FAIL" if failed else "PASS",
        "count": over_cap,
        "detail": f"p99 {p99} ms (limit {limit}), max {peak} ms (limit <{MAX_MS}), "
        f"responses over the {cap}-byte cap {over_cap}",
    }


def invariants(rows: list, baseline: list | None, cap: int) -> dict:
    """H1 to H8 from the design; H7 is informational until the video slice."""
    videos = [row for row in rows if row["ext"] == ".mp4"]
    with_records = sum(1 for row in videos if row.get("records"))
    return {
        "H1": _h1(rows, baseline),
        "H2": _counted(sum(1 for row in rows if row.get("h2"))),
        "H3": _counted(
            sum(
                1
                for row in rows
                if row.get("read_error") is None
                and "projection-limited" in _fact_reasons(row)
            ),
        ),
        "H4": _counted(
            sum(1 for row in rows if row.get("malformed_cause") == "non-input")
        ),
        "H5": _counted(sum(1 for row in rows if _false_absent(row))),
        "H6": _counted(
            sum(1 for row in rows if row.get("duplicates_identical") is True)
        ),
        "H7": {
            "status": "N/A",
            "detail": "informational until PR4: mp4 with records "
            f"{with_records} of {len(videos)}",
        },
        "H8": _h8(rows, baseline, cap),
    }


def build_report(rows: list, baseline: list | None, cap: int) -> dict:
    """Aggregate counts only: no names, no text."""
    timings = [row["ms"] for row in rows if "ms" in row]
    unsupported = Counter(row["node_class"] for row in rows if row.get("node_class"))
    return {
        "files": len(rows),
        "extensions": Counter(row["ext"] for row in rows),
        "harness_errors": Counter(row["error"] for row in rows if row.get("error")),
        "states": Counter(row.get("state") for row in rows if not row.get("error")),
        "states_by_extension": Counter(
            f"{row['ext']} {row.get('state')}" for row in rows if not row.get("error")
        ),
        "reason_codes": Counter(
            str(row.get("reason_code") or "null")
            for row in rows
            if not row.get("error")
        ),
        "selection_kinds": Counter(
            str(row.get("selection") or "null") for row in rows if not row.get("error")
        ),
        "metadata_status": Counter(
            row.get("metadata_status") for row in rows if "metadata_status" in row
        ),
        "read_errors": Counter(
            row["read_error"] for row in rows if row.get("read_error")
        ),
        "malformed_causes": Counter(
            row["malformed_cause"] for row in rows if "malformed_cause" in row
        ),
        "facts_known_at_least_4": sum(
            1 for row in rows if (row.get("facts_known") or 0) >= FACTS_KNOWN_TARGET
        ),
        "facts_errors": Counter(
            row["facts_error"] for row in rows if row.get("facts_error")
        ),
        "top_unsupported_classes": unsupported.most_common(TOP_CLASSES),
        "timing_ms": {
            "p50": percentile(timings, 0.5),
            "p99": percentile(timings, 0.99),
            "max": max(timings, default=None),
        },
        "invariants": invariants(rows, baseline, cap),
    }


def _counter_line(label: str, counts) -> str:
    items = counts.most_common() if isinstance(counts, Counter) else counts
    body = ", ".join(f"{key} {value}" for key, value in items) or "none"
    return f"{label}: {body}"


def format_report(report: dict) -> str:
    timing = report["timing_ms"]
    lines = [
        "Inspection corpus summary",
        f"files: {report['files']}",
        _counter_line("extensions", report["extensions"]),
        _counter_line("harness errors", report["harness_errors"]),
        _counter_line("states", report["states"]),
        _counter_line("states by extension", report["states_by_extension"]),
        _counter_line("reason codes", report["reason_codes"]),
        _counter_line("selection kinds", report["selection_kinds"]),
        _counter_line("metadata status", report["metadata_status"]),
        _counter_line("read errors", report["read_errors"]),
        _counter_line("malformed prompt graph causes", report["malformed_causes"]),
        f"facts at least {FACTS_KNOWN_TARGET} known: "
        f"{report['facts_known_at_least_4']}",
        _counter_line("facts errors", report["facts_errors"]),
        _counter_line(
            f"top {TOP_CLASSES} unsupported classes", report["top_unsupported_classes"]
        ),
        f"timing ms: p50 {timing['p50']}, p99 {timing['p99']}, max {timing['max']}",
        "invariants:",
    ]
    for name, result in report["invariants"].items():
        count = f" {result['count']}" if "count" in result else ""
        detail = f" ({result['detail']})" if result.get("detail") else ""
        lines.append(f"  {name} {result['status']}{count}{detail}")
        for key, value in (result.get("breakdown") or {}).items():
            lines.append(f"    {key} {value}")
    return "\n".join(lines) + "\n"


def _load_baseline(path: str | None) -> list | None:
    if path is None:
        return None
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", required=True, help="media folder, scanned read-only")
    parser.add_argument("--out", required=True, help="per-file JSONL to write")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--baseline", help="JSONL from an earlier run (H1, H8)")
    parser.add_argument("--summary", help="also write the summary text here")
    parser.add_argument("--code", default=str(TOOL_TREE), help="code tree to measure")
    parser.add_argument("--node", default=shutil.which("node"), help="Node.js binary")
    parser.add_argument(
        "--with-paths", action="store_true", help="add relative paths to the JSONL"
    )
    return parser


def main(argv: list | None = None) -> int:
    args = _parser().parse_args(argv)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:  # never write bytecode into the measured code tree
        return _run(args)
    finally:
        sys.dont_write_bytecode = previous


def _run(args) -> int:
    args.root = Path(os.path.realpath(args.root))
    args.code = Path(os.path.realpath(args.code))
    try:
        if not args.root.is_dir():
            raise Refused("--root is not a directory")
        _check_outputs(args)
        _STATE.code = _load_code(args.code)
        baseline = _load_baseline(args.baseline)
    except (Refused, OSError, ValueError) as error:
        print(f"inspection_corpus: {error}", file=sys.stderr)
        return EXIT_REFUSED
    tasks = [
        (path, rel, args.with_paths)
        for path, rel in media_files(args.root, _STATE.code.extensions)
    ]
    rows = []
    with open(args.out, "w", encoding="utf-8", newline="\n") as out:
        for row in _rows(tasks, args.workers, args.code, args.node):
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = build_report(rows, baseline, _STATE.code.cap)
    text = format_report(report)
    sys.stdout.write(text)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    failed = any(result["status"] == "FAIL" for result in report["invariants"].values())
    return EXIT_INVARIANT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
