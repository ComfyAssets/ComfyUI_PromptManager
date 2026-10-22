"""The browser's local PNG reader answers exactly as the server's on the same file.

``web/js/png-metadata.js`` ``readPngMetadataFile`` ports the bounded PNG reader in
``utils/prompt_inspector.py``. This test hands the inspection parity corpus (the
committed ``f*.png`` and the test-time shapes), extra compressed, oversized,
Latin-1 and batch-duplicate PNGs, and the committed JPEG/WebP header fixtures to a
Node oracle, and asserts per file that records, record formats, dimensions and
readability equal ``read_inspection_file``. The oracle reads each file through
``fs.openAsBlob`` slices, as the page reads a chosen ``File``.
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
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    from open_handles import assert_closed

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "js" / "support" / "local-inspect-oracle.mjs"
HEADER_FIXTURES = HERE / "fixtures" / "local-inspect"
NODE_TIMEOUT_SECONDS = 180
PNG_CLASS = {"media": "image", "kind": "png"}
_FOOTER = "Steps: 20, Sampler: Euler, Batch index: {}, Seed: 7"


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
        node = shutil.which("node")
        if node is None:  # pragma: no cover - CI runners and dev hosts ship Node
            if os.environ.get("CI"):
                self.fail("Node.js is required in CI for the local reader parity test")
            self.skipTest("Node.js is not installed")
        payload = json.dumps(
            [{"name": name, "path": str(path)} for name, path in files.items()]
        )
        # Fixed local Node harness, no shell.
        completed = subprocess.run(  # nosec B603
            [node, str(ORACLE)],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        return json.loads(completed.stdout)

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


if __name__ == "__main__":
    unittest.main()
