"""The browser's local readers answer exactly as the server's on the same file.

``web/js/png-metadata.js`` ``readPngMetadataFile`` ports the bounded PNG reader in
``utils/prompt_inspector.py``. This test hands the inspection parity corpus (the
committed ``f*.png`` and the test-time shapes), extra compressed, oversized,
Latin-1 and batch-duplicate PNGs, and the committed JPEG/WebP header fixtures to a
Node oracle, and asserts per file that records, record formats, dimensions and
readability equal ``read_inspection_file``. The oracle reads each file through
``fs.openAsBlob`` slices, as the page reads a chosen ``File``.

``web/js/workspace/local-video.mjs`` reads the container tags the server asks
ffprobe for and ports ``utils/video_metadata.select_video_carrier`` with the
record budgets of ``read_inspection_file``. The video tests hand the oracle the
committed container fixtures (``tests/fixtures/local-inspect/*.{mp4,mov,m4v,mkv,
webm}`` with their recorded ffprobe tag maps in ``video-tags-v1.json``), recorded
and synthetic tag maps, and multi-megabyte per-key containers built at test time,
and assert that the local carrier equals the server's, which selects from the
same tag map through a mocked probe, or from the same file through real ffprobe.

``web/js/workspace/local-projection.mjs`` ports ``utils/inspection_response.py``:
the strict Decimal/duplicate-key parse, the prompt, workflow and carrier
projection, the depth and node ceilings and ``_metadata``. The projection tests
hand the oracle every file above plus the committed adversarial record sets in
``tests/fixtures/local-inspect/projection-cases.json`` and node-ceiling sets built
at test time, and assert that ``projectMetadata`` equals ``_metadata`` byte for
byte, as the server's ``_bytes`` writes it with sorted keys.
"""

import io
import json
import os
import shutil
import struct
import subprocess  # nosec B404
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from utils.inspection_response import GRAPH_NODE_CEILING, _bytes, _metadata
from utils.prompt_inspector import (
    ROLE_METADATA_BYTES,
    WORKFLOW_METADATA_BYTES,
    InspectionRead,
    MetadataReadError,
    read_inspection_file,
)

from utils.prompt_inspector import inspect_prompt_evidence, unreadable_evidence
from utils.prompt_pair_resolver import ADDED_CONDITIONING_REASON, INSPECTION_PROFILE

try:
    from tests import inspection_parity as parity
    from tests import local_resolver_cases as resolver_cases
    from tests import local_resolver_selection_cases as selection_cases
    from tests.open_handles import assert_closed
    from tests.test_utils_video_metadata import HAVE_FFMPEG, write_video
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    import local_resolver_cases as resolver_cases
    import local_resolver_selection_cases as selection_cases
    from open_handles import assert_closed
    from test_utils_video_metadata import HAVE_FFMPEG, write_video

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "js" / "support" / "local-inspect-oracle.mjs"
HEADER_FIXTURES = HERE / "fixtures" / "local-inspect"
VIDEO_FIXTURES = HEADER_FIXTURES
RECORDED_TAGS = json.loads(
    (VIDEO_FIXTURES / "video-tags-v1.json").read_text(encoding="utf-8")
)["fixtures"]
PROJECTION_CASES = json.loads(
    (HEADER_FIXTURES / "projection-cases.json").read_text(encoding="utf-8")
)["cases"]
NODE_TIMEOUT_SECONDS = 180
PNG_CLASS = {"media": "image", "kind": "png"}
_FOOTER = "Steps: 20, Sampler: Euler, Batch index: {}, Seed: 7"
MiB = 1024 * 1024
GRAPH = '{"3":{"class_type":"KSampler","inputs":{"seed":7}}}'
WORKFLOW = '{"nodes":[],"version":0.4}'
ENVELOPE = json.dumps({"prompt": '{"9":{}}', "workflow": {"v": 1}})
VIDEO_CONTAINERS = {
    ".mp4": "iso-bmff",
    ".mov": "iso-bmff",
    ".m4v": "iso-bmff",
    ".mkv": "matroska",
    ".webm": "matroska",
}
PER_KEY_NAMES = {"prompt", "workflow", "parameters", "comfyassets_prompt_pairs"}

# Tag maps as ffprobe would report them, covering every branch of the carrier rule:
# (name, tags, records the reader left out). The server answer is computed, not
# written down, so a change in either side fails here.
TAG_MAP_VECTORS = [
    (
        "per_key_wins_over_comment",
        {"prompt": GRAPH, "workflow": WORKFLOW, "comment": ENVELOPE},
        [],
    ),
    (
        "matroska_names_fold",
        {"PROMPT": GRAPH, "Workflow": WORKFLOW, "ENCODER": "Lavf"},
        [],
    ),
    ("identical_fold_duplicates", {"PROMPT": GRAPH, "prompt": GRAPH}, []),
    ("differing_fold_duplicates_fail", {"PROMPT": GRAPH, "prompt": '{"9":{}}'}, []),
    (
        "differing_workflow_duplicates_fail",
        {"Workflow": WORKFLOW, "WORKFLOW": "{}", "prompt": GRAPH},
        [],
    ),
    (
        "envelope_names_never_fold",
        {"comment": ENVELOPE, "COMMENT": "a free-text note"},
        [],
    ),
    (
        "non_json_comment_falls_to_description",
        {"comment": "not json", "description": ENVELOPE},
        [],
    ),
    (
        "array_comment_is_not_an_envelope",
        {"comment": '["prompt"]', "description": '{"workflow": {}}'},
        [],
    ),
    (
        "role_envelope_beats_workflow_only_per_key",
        {"workflow": WORKFLOW, "comment": ENVELOPE},
        [],
    ),
    (
        "role_description_beats_workflow_comment",
        {"comment": '{"workflow": {"a": 1}}', "description": '{"parameters": "cat"}'},
        [],
    ),
    (
        "workflow_only_carriers_never_merge",
        {"comment": '{"workflow": {"a": 1}}', "description": '{"workflow": {"b": 2}}'},
        [],
    ),
    ("description_only", {"description": ENVELOPE}, []),
    ("no_known_key", {"encoder": "Lavf", "major_brand": "isom"}, []),
    ("envelope_without_known_keys", {"comment": '{"title": "x"}'}, []),
    ("null_role_marker_refused", {"comment": '{"prompt": null, "workflow": {}}'}, []),
    ("numeric_role_refused", {"description": '{"parameters": 5}'}, []),
    (
        "invalid_envelope_workflow_keeps_roles",
        {"comment": '{"prompt": "{}", "workflow": null}'},
        [],
    ),
    (
        "object_prompt_reserialized",
        {
            "comment": '{"prompt": {"1": {"class_type": "A", "f": 8.0, "n": 1e16, '
            '"u": "é😀"}}}'
        },
        [],
    ),
    ("workflow_only_invalid_envelope", {"comment": '{"workflow": 5}'}, []),
    ("non_text_per_key_role_refused", {"prompt": 5, "workflow": WORKFLOW}, []),
    ("non_text_per_key_workflow_invalid", {"prompt": GRAPH, "workflow": 5}, []),
    (
        "python_number_spellings",
        {
            "description": '{"workflow": {"a": [1.0, 0.1, 1E2, 1e-7, -0.0, -0, '
            "123456789012345678, 1e400, NaN, 0.00001, 1e15], "
            '"b": "\\u0001\\u007f/"}}'
        },
        [],
    ),
    (
        "integer_like_keys_keep_order",
        {"description": '{"workflow": {"10": 1, "3": 2, "b": 3, "a": 4, "3": 5}}'},
        [],
    ),
    (
        "workflow_over_budget_limited",
        {"prompt": GRAPH, "workflow": "{" + "0" * WORKFLOW_METADATA_BYTES},
        [],
    ),
    ("workflow_at_budget_kept", {"workflow": "0" * WORKFLOW_METADATA_BYTES}, []),
    (
        "roles_over_budget_fail",
        {"prompt": "a" * ROLE_METADATA_BYTES, "parameters": "b"},
        [],
    ),
    ("roles_at_budget_kept", {"prompt": "é" * (ROLE_METADATA_BYTES // 2)}, []),
    (
        "lone_surrogates_count_three_bytes",
        {"prompt": "\ud800" * (ROLE_METADATA_BYTES // 3) + "aaa"},
        [],
    ),
    (
        "envelope_workflow_over_budget_limited",
        {
            "comment": json.dumps(
                {"prompt": "x", "workflow": {"k": "0" * WORKFLOW_METADATA_BYTES}}
            )
        },
        [],
    ),
    ("reader_limited_workflow_with_per_key", {"prompt": GRAPH}, ["workflow"]),
    ("reader_limited_workflow_without_carrier", {"encoder": "x"}, ["workflow"]),
    (
        "reader_limited_workflow_cleared_by_envelope",
        {"comment": ENVELOPE},
        ["workflow"],
    ),
    # CPython's json.loads refuses an integer of more than 4300 digits, so the
    # server skips that envelope for the next one.
    (
        "envelope_integer_over_digit_limit_skipped",
        {
            "comment": '{"workflow": {"a": 1' + "0" * 4400 + "}}",
            "description": '{"workflow": {"b": 2}}',
        },
        [],
    ),
]


def _raw_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _text_chunk(key, text):
    return _raw_chunk(b"tEXt", key.encode("latin-1") + b"\0" + text.encode("latin-1"))


def _write_png(path, info, extra_chunks=()):
    """A 16x16 PNG with Pillow-written text records and spliced raw chunks."""
    buffer = io.BytesIO()
    with Image.new("RGB", (16, 16), (10, 20, 30)) as image:
        image.save(buffer, format="PNG", pnginfo=info, compress_level=4)
    data = buffer.getvalue()
    iend = data.rindex(b"IEND") - 4
    with open(path, "wb") as stream:
        stream.write(data[:iend] + b"".join(extra_chunks) + data[iend:])


def _info(**texts):
    info = PngInfo()
    for key, value in texts.items():
        if isinstance(value, tuple):
            text, options = value
            if options.get("itxt"):
                info.add_itxt(
                    key,
                    text,
                    lang=options.get("lang", ""),
                    tkey=options.get("tkey", ""),
                    zip=options.get("zip", False),
                )
            else:
                info.add_text(key, text, zip=options.get("zip", False))
        else:
            info.add_text(key, value)
    return info


PROMPT = json.dumps(parity.build("f02")["prompt"])
WORKFLOW = json.dumps(parity.build("f02")["workflow"])

# Shapes the committed corpus lacks: compressed chunks, the budgets, Latin-1 text,
# the batch-index rule and the pair execution record.
EXTRA_SHAPES = {
    "ztxt_prompt_itxt_workflow": lambda path: _write_png(
        path,
        _info(
            prompt=(PROMPT, {"zip": True}),
            workflow=(WORKFLOW, {"itxt": True, "zip": True}),
        ),
    ),
    "itxt_bom_lang": lambda path: _write_png(
        path,
        _info(
            prompt=("﻿" + PROMPT, {"itxt": True, "lang": "en", "tkey": "prompt"}),
            workflow=(WORKFLOW, {"itxt": True}),
        ),
    ),
    "latin1_prompt": lambda path: _write_png(
        path, _info(prompt='{"1":{"class_type":"Note","inputs":{"text":"café ÿ"}}}')
    ),
    "all_four_records": lambda path: _write_png(
        path,
        _info(
            prompt=PROMPT,
            workflow=WORKFLOW,
            parameters="cat\nNegative prompt: dog\n" + _FOOTER.format(0),
            comfyassets_prompt_pairs='{"pairs":[]}',
        ),
    ),
    "workflow_over_budget_limited": lambda path: _write_png(
        path,
        _info(
            prompt=PROMPT,
            workflow="[" + "0," * ((WORKFLOW_METADATA_BYTES + 1) // 2) + "]",
        ),
    ),
    "workflow_at_budget_kept": lambda path: _write_png(
        path, _info(workflow=("{" * WORKFLOW_METADATA_BYTES, {"zip": True}))
    ),
    "roles_over_budget_unreadable": lambda path: _write_png(
        path, _info(prompt="A" * (ROLE_METADATA_BYTES + 1))
    ),
    "roles_over_budget_decoded_unreadable": lambda path: _write_png(
        path, _info(prompt=("A" * (ROLE_METADATA_BYTES + 1), {"zip": True}))
    ),
    "roles_at_budget_kept": lambda path: _write_png(
        path,
        _info(
            prompt=("A" * (ROLE_METADATA_BYTES // 2), {"zip": True}),
            parameters=("B" * (ROLE_METADATA_BYTES // 2), {"zip": True}),
        ),
    ),
    "batch_parameters_last_kept": lambda path: _write_png(
        path,
        _info(prompt=PROMPT),
        [
            _text_chunk("parameters", "cat\n" + _FOOTER.format(index))
            for index in (0, 1, 2, 10)
        ],
    ),
    "batch_parameters_conflict_unreadable": lambda path: _write_png(
        path,
        PngInfo(),
        [
            _text_chunk("parameters", "cat\n" + _FOOTER.format(0)),
            _text_chunk("parameters", "dog\n" + _FOOTER.format(1)),
        ],
    ),
    "duplicate_workflow_identical": lambda path: _write_png(
        path,
        _info(prompt=PROMPT),
        [_text_chunk("workflow", WORKFLOW), _text_chunk("workflow", WORKFLOW)],
    ),
    "unknown_keys_only": lambda path: _write_png(
        path, _info(Software="ComfyUI", Comment=("café", {"zip": True}))
    ),
}


def _classes(results):
    return {name: entry["classified"] for name, entry in results.items()}


def _run_oracle(testcase, entries):
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI runners and dev hosts ship Node
        if os.environ.get("CI"):
            testcase.fail("Node.js is required in CI for the local reader parity test")
        testcase.skipTest("Node.js is not installed")
    # Fixed local Node harness, no shell.
    completed = subprocess.run(  # nosec B603
        [node, str(ORACLE)],
        input=json.dumps(entries).encode("utf-8"),
        capture_output=True,
        timeout=NODE_TIMEOUT_SECONDS,
        check=False,
    )
    testcase.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
    return json.loads(completed.stdout)


def _known_tags(tags):
    """The tags ffprobe reports to the server: the six probed names, ASCII folded."""
    return {
        key: value
        for key, value in tags.items()
        if key.lower() in PER_KEY_NAMES or key.lower() in ("comment", "description")
    }


def _fake_probe(tags, drop_workflow=False):
    """``_probe_video_tags`` answering from a tag map as ffprobe would.

    ffprobe's ``format_tags=`` filter is case-insensitive and the stdout bound is
    the only reason a probe answers ``None``; ``drop_workflow`` makes the first
    probe (the one asking for ``workflow``) overflow, as a huge per-key workflow
    tag does, so the server takes its role-only re-probe.
    """

    def probe(binary, path, keys, limit):
        if drop_workflow and "workflow" in keys:
            return None
        selected = {key: value for key, value in tags.items() if key.lower() in keys}
        raw = json.dumps({"format": {"tags": selected}}, ensure_ascii=False).encode(
            "utf-8", "surrogatepass"
        )
        return None if len(raw) > limit else raw

    return probe


def _server_selection(tags, limited=()):
    """The server's carrier for a tag map, in the oracle's shape; None when it fails."""
    with (
        patch("utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"),
        patch(
            "utils.prompt_inspector._probe_video_tags",
            side_effect=_fake_probe(tags, drop_workflow="workflow" in limited),
        ),
    ):
        try:
            read = read_inspection_file(Path("vector.mp4"))
        except MetadataReadError:
            return None
    return _read_shape(read)


def _read_shape(read):
    return {
        "records": read.metadata,
        "record_formats": read.record_formats,
        "carrierText": read.video_carrier_text,
        "limited": sorted(read.limited_records),
    }


def _local_shape(selection):
    if selection["status"] != "complete":
        return None
    return {
        "records": selection["records"],
        "record_formats": selection["record_formats"],
        "carrierText": selection["carrierText"],
        "limited": sorted(selection["limited"]),
    }


def _box(kind, *parts):
    payload = b"".join(parts)
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _full_box(kind, *parts):
    return _box(kind, b"\0\0\0\0", *parts)


def _mdta_udta(tags):
    """``moov/udta/meta`` with one mdta key per tag, laid out as ffmpeg writes it."""
    names = [name.encode("utf-8") for name in tags]
    values = [value.encode("utf-8") for value in tags.values()]
    return _mdta_items(names, list(enumerate(values, 1)))


def _mdta_items(names, items, keys_boxes=(), legacy=()):
    """``moov/udta/meta`` from raw bytes: mdta key ``names``, ``(index, value)``
    items, extra ``keys`` boxes after the first (each a list of names) and
    ``(fourcc, value)`` legacy atoms in the same ``ilst``. An item may carry a
    third member, its ``data`` type (default 1, UTF-8)."""

    def keys(entries):
        return _full_box(
            b"keys",
            struct.pack(">I", len(entries)),
            *(struct.pack(">I", 8 + len(name)) + b"mdta" + name for name in entries),
        )

    def data(value, kind=1):
        return _box(b"data", struct.pack(">II", kind, 0), value)

    ilst = _box(
        b"ilst",
        *(_box(struct.pack(">I", item[0]), data(*item[1:])) for item in items),
        *(_box(fourcc.encode("latin-1"), data(value)) for fourcc, value in legacy),
    )
    hdlr = _full_box(b"hdlr", b"\0\0\0\0", b"mdta", bytes(12), b"\0")
    boxes = [keys(names), *(keys(entries) for entries in keys_boxes)]
    return _box(b"udta", _full_box(b"meta", hdlr, *boxes, ilst))


def _with_udta(data, udta):
    """``data`` (an MP4 with moov after mdat) with its moov's udta replaced."""
    out = bytearray()
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        if kind != b"moov":
            out += data[offset : offset + size]
            offset += size
            continue
        body = data[offset + 8 : offset + size]
        children = bytearray()
        inner = 0
        while inner < len(body):
            child_size, child_kind = struct.unpack(">I4s", body[inner : inner + 8])
            if child_kind != b"udta":
                children += body[inner : inner + child_size]
            inner += child_size
        children += udta
        out += struct.pack(">I", 8 + len(children)) + b"moov" + bytes(children)
        offset += size
    return bytes(out)


def _text_of(size, prefix):
    """A valid JSON object of exactly ``size`` UTF-8 bytes with quotes throughout."""
    unit = '"k":"v",'
    body = (unit * (size // len(unit) + 1))[: size - len(prefix) - 2]
    body = body[: body.rfind(",")] if body.endswith(",") else body
    text = prefix + body + "}"
    return text + " " * (size - len(text))


class LocalPngReaderParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def _files(self):
        files = {}
        for name in parity.COMMITTED:
            files[name] = parity.fixture_path(name)
        for name in parity.TEST_TIME:
            path = parity.fixture_path(name, self.directory)
            parity.write_png(path, parity.build(name))
            files[name] = path
        for name, write in EXTRA_SHAPES.items():
            path = self.directory / f"{name}.png"
            write(path)
            files[name] = path
        for header in ("tiny.jpg", "tiny.webp"):
            files[header] = HEADER_FIXTURES / header
        return files

    def _oracle(self, files):
        return _run_oracle(
            self, [{"name": name, "path": str(path)} for name, path in files.items()]
        )

    def test_local_png_reader_matches_read_inspection_file(self):
        files = self._files()
        observed = self._oracle(files)
        self.assertEqual(set(observed), set(files))
        outcomes = set()
        for name, path in files.items():
            if path.suffix != ".png":
                continue
            with self.subTest(fixture=name):
                self.assertEqual(observed[name]["classified"], PNG_CLASS)
                result = observed[name]["result"]
                try:
                    read = read_inspection_file(path)
                except MetadataReadError:
                    outcomes.add("unreadable")
                    self.assertEqual(result["status"], "unreadable", result)
                    continue
                self.assertEqual(result["status"], "complete", result)
                self.assertEqual(result["records"], read.metadata)
                self.assertEqual(
                    sorted(result["limited"]), sorted(read.limited_records)
                )
                formats = {key: "original-text" for key in result["records"]}
                formats.update({key: "limited" for key in result["limited"]})
                self.assertEqual(formats, read.record_formats)
                self.assertEqual(
                    (result["width"], result["height"]), (read.width, read.height)
                )
                self.assertEqual(read.format, "PNG")
                outcomes.add("limited" if read.limited_records else "complete")
        # The corpus must exercise every outcome, or agreement proves little.
        self.assertEqual(outcomes, {"complete", "limited", "unreadable"})

    def test_local_image_header_matches_pillow(self):
        files = {name: HEADER_FIXTURES / name for name in ("tiny.jpg", "tiny.webp")}
        observed = self._oracle(files)
        self.assertEqual(
            _classes(observed),
            {
                "tiny.jpg": {"media": "image", "kind": "jpeg"},
                "tiny.webp": {"media": "image", "kind": "webp"},
            },
        )
        for name, path in files.items():
            with self.subTest(fixture=name):
                read = read_inspection_file(path)
                self.assertEqual(read.metadata, {})
                self.assertEqual(
                    observed[name]["result"],
                    {"format": read.format, "width": read.width, "height": read.height},
                )
                self.assertLess(path.stat().st_size, 4096)


class LocalVideoCarrierParityTests(unittest.TestCase):
    """The local video readers and carrier rule answer as the server does."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def test_local_carrier_matches_server_selection(self):
        vectors = [(name, tags, []) for name, tags in RECORDED_TAGS.items()]
        vectors += TAG_MAP_VECTORS
        observed = _run_oracle(
            self,
            [
                {"name": name, "tags": tags, "limited": limited}
                for name, tags, limited in vectors
            ],
        )
        outcomes = set()
        for name, tags, limited in vectors:
            with self.subTest(vector=name):
                selection = observed[name]["result"]["selection"]
                server = _server_selection(tags, limited)
                self.assertEqual(
                    _local_shape(selection), server, selection.get("reason")
                )
                if server is None:
                    outcomes.add(selection["status"] + ":" + selection["reason"])
                elif not server["records"] and not server["record_formats"]:
                    outcomes.add("no-carrier")
                else:
                    outcomes.update(server["record_formats"].values())
        # Every outcome of the rule must occur, or agreement proves little.
        self.assertEqual(
            outcomes,
            {
                "original-text",
                "reserialized-json",
                "invalid",
                "limited",
                "no-carrier",
                "unreadable:duplicate-metadata",
                "unreadable:invalid-metadata",
                "limited:metadata-limit",
            },
        )

    def test_local_video_readers_match_recorded_tags_and_server(self):
        files = {name: VIDEO_FIXTURES / name for name in RECORDED_TAGS}
        observed = _run_oracle(
            self, [{"name": name, "path": str(path)} for name, path in files.items()]
        )
        for name, path in files.items():
            with self.subTest(fixture=name):
                self.assertLess(path.stat().st_size, 16 * 1024)
                container = VIDEO_CONTAINERS[path.suffix]
                self.assertEqual(
                    observed[name]["classified"], {"media": "video", "kind": container}
                )
                read = observed[name]["result"]["read"]
                self.assertEqual(read["status"], "complete", read)
                self.assertEqual(read["container"], container)
                self.assertEqual(read["tags"], _known_tags(RECORDED_TAGS[name]))
                self.assertEqual(read["limited"], [])
                local = _local_shape(observed[name]["result"]["selection"])
                self.assertEqual(local, _server_selection(RECORDED_TAGS[name]))
                if HAVE_FFMPEG:
                    self.assertEqual(local, _read_shape(read_inspection_file(path)))
        # The corpus covers per-key carriers in every container, an envelope and a
        # Matroska COMMENT that the exact-case envelope rule leaves unread.
        self.assertTrue(
            _server_selection(RECORDED_TAGS["legacy-comment.mp4"])["carrierText"]
        )
        self.assertEqual(
            _server_selection(RECORDED_TAGS["legacy-comment.webm"])["records"], {}
        )

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_large_per_key_videos_read_the_same_as_the_server(self):
        base = self.directory / "base.mp4"
        write_video(base)
        source = base.read_bytes()
        shapes = {
            "available": {
                "prompt": _text_of(
                    MiB, '{"1":{"class_type":"Note","inputs":{"text":"a"}},'
                ),
                "workflow": _text_of(5 * MiB // 2, '{"nodes":[],"version":0.4,'),
            },
            "workflow_limited": {
                "prompt": GRAPH,
                "workflow": "[" + "0," * (WORKFLOW_METADATA_BYTES // 2) + "]",
            },
            "roles_over_budget": {"prompt": "A" * (ROLE_METADATA_BYTES + 1)},
        }
        files = {}
        for name, tags in shapes.items():
            path = self.directory / f"{name}.mp4"
            path.write_bytes(_with_udta(source, _mdta_udta(tags)))
            files[name] = path
        observed = _run_oracle(
            self, [{"name": name, "path": str(path)} for name, path in files.items()]
        )
        available = observed["available"]["result"]
        self.assertEqual(available["read"]["status"], "complete")
        self.assertEqual(available["read"]["limited"], [])
        self.assertEqual(
            available["selection"]["records"], shapes["available"], "1 MiB + 2.5 MiB"
        )
        self.assertEqual(
            _local_shape(available["selection"]),
            _read_shape(read_inspection_file(files["available"])),
        )
        limited = observed["workflow_limited"]["result"]
        self.assertEqual(limited["read"]["limited"], ["workflow"])
        # The unread workflow keeps its place in the carrier choice as a marker.
        self.assertEqual(limited["read"]["tags"]["workflow"], {"oversized": "workflow"})
        self.assertEqual(
            _local_shape(limited["selection"]),
            _read_shape(read_inspection_file(files["workflow_limited"])),
        )
        self.assertEqual(
            limited["selection"]["record_formats"],
            {"prompt": "original-text", "workflow": "limited"},
        )
        over = observed["roles_over_budget"]["result"]
        self.assertEqual(
            over["read"], {"status": "limited", "reason": "metadata-limit"}
        )
        self.assertIsNone(over["selection"])
        with self.assertRaises(MetadataReadError):
            read_inspection_file(files["roles_over_budget"])

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_byte_level_container_shapes_read_the_same_as_the_server(self):
        """Shapes where ffprobe is lenient: invalid UTF-8 is replaced (one U+FFFD
        per failing decode step), names that do not decode are no carrier, stray
        ``ilst`` indexes are skipped, the first ``keys`` box wins, and an
        oversized per-key workflow still takes part in the carrier choice."""
        base = self.directory / "base.mp4"
        write_video(base)
        source = base.read_bytes()
        graph, workflow = GRAPH.encode(), WORKFLOW.encode()
        envelope = json.dumps({"workflow": {"nodes": [1]}}).encode()
        oversized = b'{"nodes":[' + b"0," * (5 * MiB // 2) + b"0]}"
        shapes = {
            "latin1_byte_in_workflow": _mdta_items(
                [b"prompt", b"workflow"], [(1, graph), (2, b'{"a":"caf\xe9"}')]
            ),
            "undecodable_key_name": _mdta_items(
                [b"prom\xffpt", b"prompt"], [(1, b"junk"), (2, graph)]
            ),
            "stray_ilst_indexes": _mdta_items(
                [b"prompt", b"workflow"],
                [(1, graph), (5, b"junk"), (0, b"junk"), (2, workflow)],
            ),
            "second_keys_box": _mdta_items(
                [b"prompt"], [(1, graph)], keys_boxes=[[b"workflow"]]
            ),
            "oversized_workflow_beside_envelope": _mdta_items(
                [b"workflow"], [(1, oversized)], legacy=[("\u00a9cmt", envelope)]
            ),
            # ffmpeg holds one tag per ASCII-folded name (MP4 joins with ";"
            # under the last spelling), its format_tags filter folds ASCII only,
            # and data types 0 and 3 are Mac Roman text.
            "case_variant_keys": _mdta_items(
                [b"prompt", b"Prompt", b"workflow"],
                [(1, graph), (2, b"x"), (3, workflow)],
            ),
            "legacy_comment_then_upper_key": _mdta_items(
                [b"COMMENT"],
                [(1, b"y")],
                legacy=[("\u00a9cmt", envelope)],
            ),
            "kelvin_sign_workflow": _mdta_items(
                [b"prompt", "wor\u212aflow".encode()], [(1, graph), (2, workflow)]
            ),
            "mac_roman_types": _mdta_items(
                [b"prompt", b"workflow"],
                [
                    (1, b'{"1":{"class_type":"caf\x8e","inputs":{}}}', 0),
                    (2, b'{"n":"\x80\x81' + bytes(range(0x80, 0x100)) + b'|"}', 3),
                ],
            ),
            # A repeat joined onto it makes the first probe overflow: the server's
            # role-only re-probe reports no per-key workflow.
            "first_probe_overflows": _mdta_items(
                [b"workflow", b"prompt"],
                [(1, oversized), (1, b"A" * (8 * MiB)), (2, graph)],
            ),
        }
        files = {}
        for name, udta in shapes.items():
            files[name] = self.directory / f"{name}.mp4"
            files[name].write_bytes(_with_udta(source, udta))
        # One byte of core.mkv's PROMPT TagString patched to 0xFF.
        mkv = bytearray((VIDEO_FIXTURES / "core.mkv").read_bytes())
        at = mkv.index(b'"KSampler"', mkv.index(b"PROMPT")) + 1
        mkv[at] = 0xFF
        files["matroska_invalid_byte"] = self.directory / "invalid-byte.mkv"
        files["matroska_invalid_byte"].write_bytes(bytes(mkv))
        # Its WORKFLOW TagName renamed to "Prompt": ffmpeg keeps the last of the
        # two spellings, so the server reads one prompt, not a duplicate.
        case_mkv = (VIDEO_FIXTURES / "core.mkv").read_bytes()
        files["matroska_case_variants"] = self.directory / "case-variants.mkv"
        files["matroska_case_variants"].write_bytes(
            case_mkv.replace(b"WORKFLOW", b"Prompt\0\0")
        )
        observed = _run_oracle(
            self, [{"name": name, "path": str(path)} for name, path in files.items()]
        )
        for name, path in files.items():
            with self.subTest(shape=name):
                server = read_inspection_file(path)
                self.assertEqual(
                    _local_shape(observed[name]["result"]["selection"]),
                    _read_shape(server),
                )
                metadata = _metadata(server)
                self.assertEqual(
                    _canonical_bytes(observed[name]["metadata"]),
                    _canonical_bytes(metadata),
                )
                self.assertEqual(
                    metadata["status"],
                    (
                        "absent"
                        if name == "legacy_comment_then_upper_key"
                        else "available"
                    ),
                )
        # The re-probe has its own bound: an 8 MiB envelope beside the oversized
        # workflow fails the server's read and stops the local one.
        wide = self.directory / "reprobe_overflows.mp4"
        wide.write_bytes(
            _with_udta(
                source,
                _mdta_items(
                    [b"workflow"],
                    [(1, oversized)],
                    legacy=[("\u00a9cmt", b'{"n":"' + b"x" * (8 * MiB) + b'"}')],
                ),
            )
        )
        local = _run_oracle(self, [{"name": "wide", "path": str(wide)}])["wide"]
        self.assertEqual(
            local["result"]["read"], {"status": "limited", "reason": "metadata-limit"}
        )
        with self.assertRaises(MetadataReadError):
            read_inspection_file(wide)
        # The server outcomes these shapes pin.
        variants = read_inspection_file(files["case_variant_keys"])
        self.assertEqual(variants.metadata["prompt"], GRAPH + ";x")
        upper = read_inspection_file(files["legacy_comment_then_upper_key"])
        self.assertEqual((upper.metadata, upper.video_carrier_text), ({}, None))
        self.assertNotIn(
            "workflow", read_inspection_file(files["kelvin_sign_workflow"]).metadata
        )
        mac = read_inspection_file(files["mac_roman_types"])
        self.assertIn("caf\u00e9", mac.metadata["prompt"])
        self.assertIn("\u00c4\u00c5", mac.metadata["workflow"])
        self.assertIn(
            '"last_node_id"',
            read_inspection_file(files["matroska_case_variants"]).metadata["prompt"],
        )
        latin1 = read_inspection_file(files["latin1_byte_in_workflow"])
        self.assertEqual(latin1.metadata["workflow"], '{"a":"caf\ufffd"}')
        self.assertEqual(
            read_inspection_file(files["stray_ilst_indexes"]).metadata,
            {"prompt": GRAPH, "workflow": WORKFLOW},
        )
        self.assertEqual(
            read_inspection_file(files["second_keys_box"]).metadata, {"prompt": GRAPH}
        )
        patched = read_inspection_file(files["matroska_invalid_byte"])
        self.assertIn('"\ufffdSampler"', patched.metadata["prompt"])
        over = read_inspection_file(files["oversized_workflow_beside_envelope"])
        self.assertEqual(
            (over.record_formats, over.video_carrier_text),
            ({"workflow": "limited"}, None),
        )
        dropped = read_inspection_file(files["first_probe_overflows"])
        self.assertEqual(
            (dropped.metadata, dropped.record_formats),
            ({"prompt": GRAPH}, {"prompt": "original-text", "workflow": "limited"}),
        )


def _canonical(value):
    """``value`` with every dict's keys sorted, so two orders compare as one."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _canonical_bytes(value):
    return _bytes(_canonical(value))


def _server_metadata(path):
    try:
        return _metadata(read_inspection_file(path))
    except MetadataReadError:
        return _metadata(None)


def _synthetic_read(case):
    if case["records"] is None:
        return None
    formats = case.get("record_formats")
    if formats is None:
        formats = {key: "original-text" for key in case["records"]}
    return InspectionRead(
        case["records"],
        formats,
        case.get("carrier_text"),
        None,
        None,
        None,
        frozenset(case.get("limited", ())),
    )


def _case_supported(case):
    """Whether this interpreter has the feature a case depends on."""
    if case.get("needs") == "int-max-str-digits":
        limit = getattr(sys, "get_int_max_str_digits", None)
        return limit is not None and limit() == 4300
    return True


def _node_ceiling_cases():
    """Record sets at and past the 20,000-node ceiling, too large to commit."""
    api = lambda count: "{%s}" % ",".join(  # noqa: E731
        '"%d":{"class_type":"A","inputs":{}}' % index for index in range(count)
    )
    ui = lambda count: "[%s]" % ",".join("{}" for _ in range(count))  # noqa: E731
    half = GRAPH_NODE_CEILING // 2
    return [
        {"name": "prompt_at_ceiling", "records": {"prompt": api(GRAPH_NODE_CEILING)}},
        {
            "name": "prompt_over_ceiling",
            "records": {"prompt": api(GRAPH_NODE_CEILING + 1)},
        },
        {
            "name": "workflow_at_ceiling",
            "records": {"workflow": '{"nodes":%s}' % ui(GRAPH_NODE_CEILING)},
        },
        {
            "name": "workflow_over_ceiling",
            "records": {"workflow": '{"nodes":%s}' % ui(GRAPH_NODE_CEILING + 1)},
        },
        {
            "name": "workflow_subgraphs_over_ceiling",
            "records": {
                "workflow": '{"nodes":%s,"definitions":{"subgraphs":[{"nodes":%s},'
                '5,{"nodes":3}]}}' % (ui(half + 1), ui(half))
            },
        },
        {
            "name": "workflow_subgraphs_at_ceiling",
            "records": {
                "workflow": '{"nodes":%s,"definitions":{"subgraphs":[{"nodes":%s}]}}'
                % (ui(half), ui(half))
            },
        },
        {
            "name": "carrier_prompt_over_ceiling",
            "records": {"prompt": "x"},
            "record_formats": {"prompt": "reserialized-json"},
            "carrier_text": '{"prompt":%s}' % api(GRAPH_NODE_CEILING + 1),
        },
    ]


class LocalProjectionParityTests(unittest.TestCase):
    """The local projection answers byte for byte as ``_metadata`` does."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def _files(self):
        files = {}
        for name in parity.COMMITTED:
            files[name] = parity.fixture_path(name)
        for name in parity.TEST_TIME:
            path = parity.fixture_path(name, self.directory)
            parity.write_png(path, parity.build(name))
            files[name] = path
        for name, write in EXTRA_SHAPES.items():
            path = self.directory / f"{name}.png"
            write(path)
            files[name] = path
        for header in ("tiny.jpg", "tiny.webp"):
            files[header] = HEADER_FIXTURES / header
        for name in RECORDED_TAGS:
            files[name] = VIDEO_FIXTURES / name
        return files

    def test_local_metadata_matches_route_for_parity_corpus(self):
        files = self._files()
        cases = [
            case
            for case in PROJECTION_CASES + _node_ceiling_cases()
            if _case_supported(case)
        ]
        self.assertGreater(len(cases), len(PROJECTION_CASES) - 3)
        entries = [{"name": name, "path": str(path)} for name, path in files.items()]
        entries += [
            {
                "name": "case:" + case["name"],
                "records": case["records"],
                "record_formats": case.get("record_formats"),
                "carrier_text": case.get("carrier_text"),
                "limited": case.get("limited", []),
            }
            for case in cases
        ]
        observed = _run_oracle(self, entries)
        outcomes = set()
        for name, path in files.items():
            with self.subTest(fixture=name):
                self.assertIsNotNone(observed[name]["classified"], name)
                if path.suffix in VIDEO_CONTAINERS and not HAVE_FFMPEG:
                    # Without ffprobe the server cannot read the file; the tag-map
                    # vectors below cover the carrier projection instead.
                    continue
                server = _server_metadata(path)
                local = observed[name]["metadata"]
                self.assertEqual(_canonical_bytes(local), _canonical_bytes(server))
                outcomes.add(server["status"])
                for field in ("prompt_graph", "workflow_graph"):
                    outcomes.add(field + ":" + server[field]["status"])
        for case in cases:
            with self.subTest(case=case["name"]):
                server = _metadata(_synthetic_read(case))
                local = observed["case:" + case["name"]]["metadata"]
                self.assertEqual(_canonical_bytes(local), _canonical_bytes(server))
                expect = case.get("expect") or {}
                for field, status in expect.items():
                    self.assertEqual(server[field]["status"], status, field)
                outcomes.add(server["status"])
                for field in ("prompt_graph", "workflow_graph"):
                    outcomes.add(field + ":" + server[field]["status"])
        # Every projection outcome must occur, or agreement proves little.
        self.assertEqual(
            outcomes,
            {
                "available",
                "absent",
                "unreadable",
                "prompt_graph:parsed",
                "prompt_graph:limited",
                "prompt_graph:malformed",
                "prompt_graph:absent",
                "workflow_graph:parsed",
                "workflow_graph:limited",
                "workflow_graph:malformed",
                "workflow_graph:absent",
            },
        )

    def test_recorded_video_tag_maps_project_as_the_server(self):
        """The carrier projection on the recorded maps, with or without ffprobe."""
        vectors = [(name, tags, []) for name, tags in RECORDED_TAGS.items()]
        vectors += [
            (name, tags, limited)
            for name, tags, limited in TAG_MAP_VECTORS
            if name
            in {
                "per_key_wins_over_comment",
                "object_prompt_reserialized",
                "python_number_spellings",
                "integer_like_keys_keep_order",
                "invalid_envelope_workflow_keeps_roles",
                "workflow_only_invalid_envelope",
                "reader_limited_workflow_with_per_key",
                "envelope_workflow_over_budget_limited",
            }
        ]
        observed = _run_oracle(
            self,
            [
                {"name": name, "tags": tags, "limited": limited}
                for name, tags, limited in vectors
            ],
        )
        seen = set()
        for name, tags, limited in vectors:
            with self.subTest(vector=name):
                with (
                    patch(
                        "utils.prompt_inspector.ffprobe_path",
                        return_value="/tool/ffprobe",
                    ),
                    patch(
                        "utils.prompt_inspector._probe_video_tags",
                        side_effect=_fake_probe(
                            tags, drop_workflow="workflow" in limited
                        ),
                    ),
                ):
                    try:
                        server = _metadata(read_inspection_file(Path("vector.mp4")))
                    except MetadataReadError:
                        server = _metadata(None)
                local = observed[name]["metadata"]
                self.assertEqual(_canonical_bytes(local), _canonical_bytes(server))
                seen.update(server["record_formats"].values())
                seen.add(server["workflow_graph"]["status"])
        self.assertLessEqual(
            {"reserialized-json", "original-text", "invalid", "limited"}, seen
        )
        self.assertLessEqual({"parsed", "malformed", "limited"}, seen)


def _server_evidence(records, file_name=None):
    return inspect_prompt_evidence(
        records, None, profile=INSPECTION_PROFILE, file_name=file_name
    )


FILE_NAME = resolver_cases.FILE_NAME


def _records(case):
    return {key: value for key, value in case.items() if key != FILE_NAME}


class LocalResolverParityTests(unittest.TestCase):
    """``resolveLocalEvidence`` answers as ``inspect_prompt_evidence`` (L3 Tasks 8-9).

    Python is the oracle: every expected value is the server's own answer on the
    same record texts. Task 8 ported one sampler's tracing; Task 9 ports
    candidate selection (several samplers, the identical-branch rule of #270,
    preview-only stages, detailer stages) and A1111 ``parameters``, so no
    record set is left to the server any more.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def _compare(self, cases):
        """Assert each record set's local evidence equal; return the server answers."""
        observed = _run_oracle(
            self,
            [
                {
                    "name": name,
                    "mode": "resolve",
                    "records": _records(records),
                    "fileName": records.get(FILE_NAME),
                }
                for name, records in cases.items()
            ],
        )
        answers = {}
        for name, records in cases.items():
            with self.subTest(case=name):
                local = observed[name]
                server = _server_evidence(_records(records), records.get(FILE_NAME))
                answers[name] = server
                self.assertEqual(
                    _canonical_bytes(local["evidence"]), _canonical_bytes(server)
                )
        return answers

    def test_local_roles_match_execution_and_single_chain_vectors(self):
        files = {}
        for name in parity.COMMITTED:
            files[name] = parity.fixture_path(name)
        for name in parity.TEST_TIME:
            path = parity.fixture_path(name, self.directory)
            parity.write_png(path, parity.build(name))
            files[name] = path
        observed = _run_oracle(
            self,
            [
                {"name": name, "mode": "resolve", "path": str(path)}
                for name, path in files.items()
            ],
        )
        vectors = {
            vector["fixture"]: vector
            for vector in json.loads(parity.VECTORS_PATH.read_text(encoding="utf-8"))
            if vector["profile"] == "inspection"
        }
        compared = 0
        for name, path in files.items():
            with self.subTest(fixture=name):
                local = observed[name]
                try:
                    records = read_inspection_file(path).metadata
                except MetadataReadError:
                    self.assertTrue(local["unreadable"])
                    continue
                # The single-chain subset (Task 8); the rest is Task 9's test below.
                if not resolver_cases.single_chain(records):
                    continue
                server = _server_evidence(records, Path(path).name)
                self.assertEqual(
                    _canonical_bytes(local["evidence"]), _canonical_bytes(server)
                )
                # The committed vector agrees with the server read here.
                expect = vectors[name]["expect"]
                self.assertEqual(server["resolution"]["state"], expect["state"])
                self.assertEqual(
                    server["resolution"]["reason_code"], expect["reason_code"]
                )
                compared += 1
        self.assertGreaterEqual(compared, 20)

    def test_local_roles_match_inspection_vectors(self):
        """Every inspection vector: local prompts, resolution and facts equal Images'.

        The facts are ``extractGenerationFacts`` (with the legacy fill) on the
        local payload, against the facts the committed vectors pin for the
        server's response on the same file.
        """
        vectors = [
            vector
            for vector in json.loads(parity.VECTORS_PATH.read_text(encoding="utf-8"))
            if vector["profile"] == "inspection"
        ]
        files = {}
        for vector in vectors:
            name = vector["fixture"]
            if name in parity.TEST_TIME:
                path = parity.fixture_path(name, self.directory)
                parity.write_png(path, parity.build(name))
            else:
                path = parity.fixture_path(name)
            files[name] = path
        observed = _run_oracle(
            self,
            [
                {"name": name, "mode": "resolve", "path": str(path)}
                for name, path in files.items()
            ],
        )
        for vector in vectors:
            name = vector["fixture"]
            with self.subTest(fixture=name):
                local, expect = observed[name], vector["expect"]
                try:
                    server = _server_evidence(
                        read_inspection_file(files[name]).metadata,
                        Path(files[name]).name,
                    )
                except MetadataReadError:
                    server = unreadable_evidence()
                    self.assertTrue(local["unreadable"])
                self.assertEqual(
                    _canonical_bytes(local["evidence"]), _canonical_bytes(server)
                )
                resolution, prompts = (
                    local["evidence"]["resolution"],
                    local["evidence"]["prompts"],
                )
                self.assertEqual(
                    (
                        resolution["state"],
                        resolution["reason"],
                        resolution["reason_code"],
                    ),
                    (expect["state"], expect["reason"], expect["reason_code"]),
                )
                self.assertEqual(
                    [
                        parity._role_text(
                            prompts[role]["status"], prompts[role]["text"]
                        )
                        for role in ("positive", "negative")
                    ],
                    [expect["positive"], expect["negative"]],
                )
                self.assertEqual(local["facts"], expect["facts"])
        self.assertEqual(len(vectors), 28)

    def test_local_roles_match_selection_record_sets(self):
        answers = self._compare(selection_cases.selection_cases())
        reasons = {answer["resolution"]["reason"] for answer in answers.values()}
        self.assertLessEqual(
            {
                "Every sampler branch uses the same prompt pair",
                "Multiple sampler branches",
                "Sampler stages have different prompts",
                "Cyclic sampler stage ancestry",
                "Expected an image link",
                "Unsupported sampler stage ancestry",
                "Resolution depth limit reached",
                "A1111 settings footer is not unique",
                "Multiple negative prompt delimiters",
                "Negative prompt delimiter is absent",
                "Parameters exceed the length limit",
                "No A1111 parameters",
            },
            reasons,
        )
        # A stage reaching only previews is dropped: one sampler is named.
        self.assertEqual(
            answers["select-preview-dropped"]["resolution"]["sampler_id"], "3"
        )
        # Pass-through lineage: the chain shapes reach every new outcome.
        self.assertEqual(
            {
                name: answers[f"select-{name}"]["resolution"]["reason"]
                for name in ("hires-fix", "flux-three-stage", "hires-fix-differs")
            },
            {
                "hires-fix": "",
                "flux-three-stage": ADDED_CONDITIONING_REASON,
                "hires-fix-differs": "Sampler stages have different prompts",
            },
        )

    def test_local_roles_match_fuzzed_pass_through_chains(self):
        cases = {}
        for seed in range(4):
            cases.update(selection_cases.passthrough_fuzz_cases(seed, 120))
        answers = self._compare(cases)
        reasons = [answer["resolution"]["reason"] for answer in answers.values()]
        for reason in (
            "",
            "Sampler stages have different prompts",
            "A later stage adds conditioning to the base prompt",
            "Combined conditioning has different texts",
            "Multiple sampler branches",
        ):
            self.assertGreater(reasons.count(reason), 5, reason)

    def test_local_roles_match_fuzzed_selection_sets(self):
        cases = {}
        for seed in range(4):
            cases.update(selection_cases.selection_fuzz_cases(seed, 150))
        answers = self._compare(cases)
        reasons = [answer["resolution"]["reason"] for answer in answers.values()]
        self.assertGreater(reasons.count("Multiple sampler branches"), 20)
        self.assertGreater(reasons.count("Sampler stages have different prompts"), 0)
        self.assertGreater(
            reasons.count("Every sampler branch uses the same prompt pair"), 0
        )
        self.assertGreater(reasons.count("A1111 settings footer is not unique"), 0)

    def test_local_roles_match_golden_record_sets(self):
        answers = self._compare(resolver_cases.golden_cases())
        codes = {answer["resolution"]["reason_code"] for answer in answers.values()}
        self.assertLessEqual(
            {
                "malformed",
                "unsupported-node",
                "runtime-value",
                "composite",
                "work-limit",
                "depth-limit",
                "text-limit",
                "graph-ceiling",
                "no-sampler",
                None,
            },
            codes,
        )
        sources = {
            side["source"]
            for answer in answers.values()
            for side in answer["prompts"].values()
        }
        self.assertLessEqual(
            {"execution", "graph-inferred", "zeroed-conditioning"}, sources
        )

    def test_committed_resolver_vectors_are_current(self):
        """The Node unit test's committed answers are still the server's."""
        committed = json.loads(resolver_cases.VECTORS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            committed,
            json.loads(json.dumps(resolver_cases.committed_vectors())),
            "regenerate with: python3 -m tests.local_resolver_cases --write",
        )

    def test_local_roles_match_at_every_work_bound(self):
        answers = self._compare(resolver_cases.boundary_cases())
        for name in ("visits", "depth", "scanned"):
            self.assertEqual(answers[f"{name}-edge"]["resolution"]["state"], "resolved")
            self.assertIn(
                answers[f"{name}-over"]["resolution"]["reason_code"],
                ("work-limit", "depth-limit"),
            )
        self.assertEqual(
            answers["named-astral"]["resolution"]["node"]["class_type"],
            "\U0001f600" * 64,
        )

    def test_local_json_depth_cap_at_its_boundary(self):
        """Both kinds of deep record at the browser's JSON depth cap (1,000).

        Locally a record nested past the cap is invalid JSON: a prompt is
        malformed and a workflow is ignored, as the server answers when
        ``json.loads`` raises RecursionError. The server's own limit depends on
        the Python (TESTING.md, "Deep JSON"): about 990 on 3.10 and 3.11, so it
        agrees past the cap there; about 10,000 on 3.12 and 3.13 and over
        50,000 on 3.14, so it agrees up to the cap there and reads deeper.
        """
        cases = resolver_cases.depth_cases()
        observed = _run_oracle(
            self,
            [
                {"name": name, "mode": "resolve", "records": records}
                for name, _kind, _depth, records in cases
            ],
        )
        newer = sys.version_info >= (3, 12)
        compared = 0
        for name, kind, depth, records in cases:
            with self.subTest(case=name):
                local = observed[name]["evidence"]["resolution"]
                within = depth <= resolver_cases.JSON_MAX_DEPTH
                if kind == "prompt":
                    expected = (
                        ("resolved", None) if within else ("unresolved", "malformed")
                    )
                else:
                    # A workflow that is read finds the overwritten pair (R1).
                    expected = (
                        ("unresolved", "malformed") if within else ("resolved", None)
                    )
                self.assertEqual((local["state"], local["reason_code"]), expected)
                server = _server_evidence(records)
                if server["resolution"]["state"] == local["state"]:
                    self.assertEqual(
                        _canonical_bytes(observed[name]["evidence"]),
                        _canonical_bytes(server),
                    )
                    compared += 1
                else:
                    # The documented divergence only: an older server refuses
                    # a little below the cap, a newer one reads past it.
                    self.assertEqual(within, not newer)
        self.assertGreaterEqual(compared, 2)

    def test_local_roles_match_fuzzed_record_sets(self):
        cases = {}
        for seed in range(4):
            cases.update(resolver_cases.fuzz_cases(seed, 150))
        answers = self._compare(cases)
        states = [answer["resolution"]["state"] for answer in answers.values()]
        # Coherent graphs reach real results, not only early refusals.
        self.assertGreater(states.count("resolved"), len(states) // 5)
        self.assertGreater(states.count("ambiguous"), 0)
        self.assertGreater(states.count("unresolved"), len(states) // 10)
        self.assertGreaterEqual(
            len({answer["resolution"]["reason_code"] for answer in answers.values()}),
            5,
        )


if __name__ == "__main__":
    unittest.main()
