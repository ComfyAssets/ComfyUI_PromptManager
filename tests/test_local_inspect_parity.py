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
"""

import io
import json
import os
import shutil
import struct
import subprocess  # nosec B404
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from utils.prompt_inspector import (
    ROLE_METADATA_BYTES,
    WORKFLOW_METADATA_BYTES,
    MetadataReadError,
    read_inspection_file,
)

try:
    from tests import inspection_parity as parity
    from tests.open_handles import assert_closed
    from tests.test_utils_video_metadata import HAVE_FFMPEG, write_video
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    from open_handles import assert_closed
    from test_utils_video_metadata import HAVE_FFMPEG, write_video

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "js" / "support" / "local-inspect-oracle.mjs"
HEADER_FIXTURES = HERE / "fixtures" / "local-inspect"
VIDEO_FIXTURES = HEADER_FIXTURES
RECORDED_TAGS = json.loads(
    (VIDEO_FIXTURES / "video-tags-v1.json").read_text(encoding="utf-8")
)["fixtures"]
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
    """The tags the local reader keeps: per-key names folded, envelope names exact."""
    return {
        key: value
        for key, value in tags.items()
        if key.lower() in PER_KEY_NAMES or key in ("comment", "description")
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
    names = list(tags)
    keys = _full_box(
        b"keys",
        struct.pack(">I", len(names)),
        *(
            struct.pack(">I", 8 + len(name.encode("utf-8")))
            + b"mdta"
            + name.encode("utf-8")
            for name in names
        ),
    )
    items = b"".join(
        _box(
            struct.pack(">I", index + 1),
            _box(b"data", struct.pack(">II", 1, 0), tags[name].encode("utf-8")),
        )
        for index, name in enumerate(names)
    )
    hdlr = _full_box(b"hdlr", b"\0\0\0\0", b"mdta", bytes(12), b"\0")
    return _box(b"udta", _full_box(b"meta", hdlr, keys, _box(b"ilst", items)))


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
        self.assertNotIn("workflow", limited["read"]["tags"])
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


if __name__ == "__main__":
    unittest.main()
