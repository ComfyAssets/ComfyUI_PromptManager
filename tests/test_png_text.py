"""PNG text without a pixel decode (#523), against the legacy reader floor.

Pillow's ``PngImageFile.text`` loads (decodes) every pixel to reach text
chunks written after IDAT. ``utils.png_text.png_text`` walks the remaining
chunks instead. The legacy reader (``Image.open(path).text``, every failure
read as ``{}``) is the floor: a file it read must read the same, and the
curated corpus below must read identically. The documented difference is
pixel data, which the walk never decodes: undecodable or cut-short pixel data
made the legacy reader fail, the walk still reads the text before it.
"""

import io
import random
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageFile, PngImagePlugin

from utils.metadata_extractor import ComfyUIMetadataExtractor
from utils.png_text import png_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SIGNATURE = b"\x89PNG\r\n\x1a\n"
FUZZ_SEED = 523
FUZZ_CASES = 400


def legacy_text(path):
    """The legacy scan reader: Pillow's ``.text`` (a full decode)."""
    try:
        with Image.open(path) as image:
            return dict(getattr(image, "text", None) or {})
    except Exception:
        return {}


def walked_text(path):
    """The new reader under the same failure rule."""
    try:
        with Image.open(path) as image:
            return png_text(image)
    except Exception:
        return {}


def chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def chunks_of(raw):
    """``[(kind, data)]`` of a well-formed PNG."""
    found, offset = [], 8
    while offset < len(raw):
        length, kind = struct.unpack(">I4s", raw[offset : offset + 8])
        found.append((kind, raw[offset + 8 : offset + 8 + length]))
        offset += 12 + length
    return found


def assemble(parts):
    return SIGNATURE + b"".join(
        part if isinstance(part, bytes) else chunk(*part) for part in parts
    )


def base_png(mode="RGB", size=(64, 64), text=None, noise=False):
    """A PNG from Pillow (text before IDAT), as ``[(kind, data)]``."""
    image = Image.new(mode, size)
    if noise:
        rng = random.Random(7)
        image = Image.frombytes(
            "RGB", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3))
        )
    info = PngImagePlugin.PngInfo()
    for key, value in (text or {}).items():
        info.add_text(key, value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return chunks_of(buffer.getvalue())


def split_idat(parts, pieces=3):
    """Spread the IDAT payload over ``pieces`` consecutive IDAT chunks."""
    data = b"".join(d for k, d in parts if k == b"IDAT")
    step = max(1, len(data) // pieces)
    idats = [(b"IDAT", data[i : i + step]) for i in range(0, len(data), step)]
    first = next(i for i, (k, _) in enumerate(parts) if k == b"IDAT")
    rest = [p for p in parts if p[0] != b"IDAT"]
    return rest[:first] + idats + rest[first:]


def before_iend(parts, *extra):
    return parts[:-1] + list(extra) + parts[-1:]


def text_chunk(key, value):
    return (b"tEXt", key.encode("latin-1") + b"\0" + value.encode("latin-1"))


def ztxt_chunk(key, value):
    return (b"zTXt", key.encode() + b"\0\0" + zlib.compress(value.encode("latin-1")))


def itxt_chunk(key, value, compressed=False):
    body = value.encode("utf-8")
    if compressed:
        body = zlib.compress(body)
    flag = b"\1\0" if compressed else b"\0\0"
    return (b"iTXt", key.encode() + b"\0" + flag + b"en\0" + b"Title\0" + body)


def bad_crc(kind, data):
    good = chunk(kind, data)
    return good[:-4] + bytes(b ^ 0xFF for b in good[-4:])


PROMPT = '{"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}'
WORKFLOW = '{"nodes": [], "links": []}'


def curated():
    """Name to PNG bytes; each must read the same with both readers."""
    plain = base_png(text={"prompt": PROMPT})
    trailing = before_iend(
        base_png(), text_chunk("prompt", PROMPT), text_chunk("workflow", WORKFLOW)
    )
    cases = {
        "no-text": assemble(base_png()),
        "text-before-pixels": assemble(plain),
        "text-after-pixels": assemble(trailing),
        "ztxt-after-pixels": assemble(
            before_iend(base_png(), ztxt_chunk("prompt", PROMPT))
        ),
        "itxt-plain-and-compressed": assemble(
            before_iend(
                base_png(),
                itxt_chunk("prompt", "café " + PROMPT),
                itxt_chunk("workflow", WORKFLOW, compressed=True),
            )
        ),
        "itxt-bad-utf8": assemble(
            before_iend(
                base_png(),
                (b"iTXt", b"prompt\0\0\0\0\0\xff\xfe"),
                text_chunk("workflow", WORKFLOW),
            )
        ),
        "later-copy-wins": assemble(
            before_iend(plain, text_chunk("prompt", '{"later": 1}'))
        ),
        "empty-key-and-no-separator": assemble(
            before_iend(base_png(), (b"tEXt", b"\0value"), (b"tEXt", b"lonely"))
        ),
        "split-pixels-text-between": assemble(
            split_idat(before_iend(base_png(noise=True), text_chunk("prompt", PROMPT)))
        ),
        "palette": assemble(
            before_iend(base_png(mode="P"), text_chunk("prompt", PROMPT))
        ),
        "sixteen-bit": assemble(
            before_iend(base_png(mode="I;16"), text_chunk("prompt", PROMPT))
        ),
        "bad-crc-before-pixels": assemble(
            [base_png()[0], bad_crc(*text_chunk("prompt", PROMPT))] + base_png()[1:]
        ),
        "bad-crc-after-pixels": assemble(
            base_png()[:-1] + [bad_crc(*text_chunk("prompt", PROMPT))] + base_png()[-1:]
        ),
        "unknown-chunk-after-pixels": assemble(
            before_iend(base_png(), (b"prVt", b"x" * 40), text_chunk("prompt", PROMPT))
        ),
        "garbage-chunk-id-after-pixels": assemble(
            base_png()[:-1]
            + [b"\0\0\0\4\x00\x01\x02\x03abcd\0\0\0\0"]
            + [chunk(*text_chunk("prompt", PROMPT))]
            + base_png()[-1:]
        ),
        "oversized-ztxt-after-pixels": assemble(
            before_iend(
                base_png(),
                (
                    b"zTXt",
                    b"prompt\0\0"
                    + zlib.compress(b"a" * (PngImagePlugin.MAX_TEXT_CHUNK + 10)),
                ),
            )
        ),
        "pixels-length-past-end": assemble(
            [p for p in base_png() if p[0] != b"IDAT" and p[0] != b"IEND"]
        )
        + b"\xff\xff\x00\x00IDAT"
        + b"\0" * 64,
    }
    full = assemble(trailing)
    idat_at = full.index(b"IDAT")
    cases["truncated-in-pixels"] = full[: idat_at + 20]
    cases["no-end-after-pixels"] = full[: full.rindex(b"IEND") - 4]
    cases["truncated-in-trailing-text"] = full[: full.rindex(b"workflow") + 4]
    cases["unknown-chunk-past-end"] = (
        assemble(before_iend(base_png(), ztxt_chunk("prompt", PROMPT))[:-1])
        + chunk(b"prVt", b"x" * 40)[:-20]
    )
    cases["not-a-png"] = b"nope, not an image at all"
    cases["signature-only"] = SIGNATURE
    return cases


def corrupt_pixels():
    """Complete IDAT chunks with valid CRCs over data zlib cannot inflate."""
    parts = before_iend(base_png(), text_chunk("prompt", PROMPT))
    return assemble(
        [(k, b"\x78\x9c" + b"\xff" * 30) if k == b"IDAT" else (k, d) for k, d in parts]
    )


def apng():
    frames = [Image.new("RGB", (16, 16), colour) for colour in ("red", "blue")]
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", PROMPT)
    buffer = io.BytesIO()
    frames[0].save(
        buffer, format="PNG", save_all=True, append_images=frames[1:], pnginfo=info
    )
    return assemble(
        before_iend(chunks_of(buffer.getvalue()), text_chunk("workflow", WORKFLOW))
    )


def fixture_images():
    return sorted(p for p in FIXTURES.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


class _TempFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, raw):
        path = Path(self._tmp.name) / name
        path.write_bytes(raw)
        return path


class PngTextParityTests(_TempFiles):
    def test_every_fixture_image_reads_like_the_legacy_reader(self):
        images = fixture_images()
        self.assertGreaterEqual(len(images), 25)
        for path in images:
            with self.subTest(path=path.relative_to(FIXTURES)):
                self.assertEqual(walked_text(path), legacy_text(path))

    def test_curated_corpus_reads_like_the_legacy_reader(self):
        for name, raw in curated().items():
            with self.subTest(name):
                path = self.write(name + ".png", raw)
                self.assertEqual(walked_text(path), legacy_text(path))

    def test_curated_corpus_exercises_trailing_text(self):
        """The corpus is not vacuous: trailing text exists and is read."""
        cases = curated()
        path = self.write("t.png", cases["text-after-pixels"])
        self.assertEqual(walked_text(path), {"prompt": PROMPT, "workflow": WORKFLOW})
        with Image.open(path) as image:
            self.assertNotIn("prompt", image.info)
        self.assertEqual(
            walked_text(self.write("x.png", cases["truncated-in-pixels"])), {}
        )

    def test_itxt_values_keep_their_language_and_type(self):
        path = self.write("i.png", curated()["itxt-plain-and-compressed"])
        new, old = walked_text(path), legacy_text(path)
        self.assertIsInstance(new["prompt"], PngImagePlugin.iTXt)
        self.assertEqual(new["prompt"].lang, old["prompt"].lang)
        self.assertEqual(new["prompt"].tkey, old["prompt"].tkey)

    def test_animated_png_falls_back_to_the_legacy_read(self):
        path = self.write("a.png", apng())
        self.assertEqual(walked_text(path), legacy_text(path))
        self.assertEqual(walked_text(path)["workflow"], WORKFLOW)

    def test_truncated_pixels_still_fail_when_truncated_images_load(self):
        """With LOAD_TRUNCATED_IMAGES set (a node pack may set it) both readers
        accept a short file."""
        path = self.write("t.png", curated()["truncated-in-pixels"])
        with patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", True):
            self.assertEqual(walked_text(path), legacy_text(path))

    def test_a_text_decode_error_ends_the_walk_like_legacy(self):
        """Pillow's ``load_end`` stops at a chunk whose handler raises
        ``UnicodeDecodeError`` and keeps the text read so far."""
        path = self.write(
            "u.png",
            assemble(
                before_iend(
                    base_png(),
                    ztxt_chunk("prompt", PROMPT),
                    text_chunk("workflow", WORKFLOW),
                    ztxt_chunk("parameters", "steps 20"),
                )
            ),
        )

        def undecodable(stream, position, length):
            raise UnicodeDecodeError("latin-1", b"", 0, 1, "test")

        with patch.object(PngImagePlugin.PngStream, "chunk_tEXt", undecodable):
            old = legacy_text(path)
            self.assertEqual(walked_text(path), old)
        self.assertEqual(old, {"prompt": PROMPT})

    def test_complete_but_undecodable_pixels_read_more_than_legacy(self):
        path = self.write("c.png", corrupt_pixels())
        self.assertEqual(legacy_text(path), {})
        self.assertEqual(walked_text(path), {"prompt": PROMPT})

    def test_cut_short_pixels_read_the_text_before_them(self):
        full = assemble(base_png(text={"prompt": PROMPT}))
        path = self.write("s.png", full[: full.index(b"IDAT") + 12])
        self.assertEqual(legacy_text(path), {})
        self.assertEqual(walked_text(path), {"prompt": PROMPT})

    def test_mutated_files_never_read_less_than_legacy(self):
        """Seeded fuzz: truncations and byte flips of a file with text before,
        between and after its pixel chunks. Wherever the legacy reader found
        text, the walk finds exactly the same."""
        base = assemble(
            split_idat(
                before_iend(
                    base_png(text={"workflow": WORKFLOW}),
                    ztxt_chunk("prompt", PROMPT),
                    itxt_chunk("parameters", "steps 20"),
                )
            )
        )
        rng = random.Random(FUZZ_SEED)
        compared = 0
        for case in range(FUZZ_CASES):
            raw = bytearray(base)
            if case % 2:
                del raw[rng.randrange(8, len(raw)) :]
            else:
                for _ in range(rng.randrange(1, 4)):
                    raw[rng.randrange(8, len(raw))] = rng.randrange(256)
            path = self.write(f"fuzz{case}.png", bytes(raw))
            old = legacy_text(path)
            if old:
                compared += 1
                with self.subTest(case=case):
                    self.assertEqual(walked_text(path), old)
        self.assertGreater(compared, FUZZ_CASES // 5)

    def test_non_png_images_have_no_text(self):
        for name in ("tiny.jpg", "tiny.webp"):
            with Image.open(FIXTURES / "local-inspect" / name) as image:
                self.assertEqual(png_text(image), {})


class NoPixelDecodeTests(_TempFiles):
    """Metadata reads never decode pixels (the #523 memory finding)."""

    def png_fixtures(self):
        return sorted((FIXTURES / "inspection_parity").glob("*.png"))

    def test_png_text_does_not_load_pixels_and_leaves_the_image_usable(self):
        path = self.write("t.png", curated()["text-after-pixels"])
        with Image.open(path) as image:
            with patch.object(ImageFile.ImageFile, "load", autospec=True) as load:
                self.assertEqual(png_text(image)["workflow"], WORKFLOW)
            load.assert_not_called()
            image.load()
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.text["workflow"], WORKFLOW)

    def test_scan_reader_does_not_decode_pixels(self):
        from py.api import PromptManagerAPI

        api = PromptManagerAPI.__new__(PromptManagerAPI)
        fixtures = self.png_fixtures() + [
            self.write("t.png", curated()["text-after-pixels"])
        ]
        with patch.object(ImageFile.ImageFile, "load", autospec=True) as load:
            found = [api._extract_comfyui_metadata(str(p)) for p in fixtures]
        load.assert_not_called()
        self.assertEqual(found, [legacy_text(p) for p in fixtures])
        self.assertTrue(all(found))

    def test_monitor_extractor_does_not_decode_pixels(self):
        extractor = ComfyUIMetadataExtractor()
        fixtures = self.png_fixtures()
        with patch.object(ImageFile.ImageFile, "load", autospec=True) as load:
            found = [extractor.extract_metadata(str(p)) for p in fixtures]
        load.assert_not_called()
        self.assertTrue(all(metadata and len(metadata) > 1 for metadata in found))


class MonitorExtractorParityTests(_TempFiles):
    """``extract_metadata`` answers exactly as with the legacy ``.text``."""

    def legacy_extract(self, path):
        def old_text(image):
            return dict(getattr(image, "text", None) or {})

        with patch("utils.metadata_extractor.png_text", old_text):
            return ComfyUIMetadataExtractor().extract_metadata(str(path))

    def test_every_fixture_and_curated_file_extracts_like_legacy(self):
        paths = fixture_images() + [
            self.write(name + ".png", raw) for name, raw in curated().items()
        ]
        extractor = ComfyUIMetadataExtractor()
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(
                    extractor.extract_metadata(str(path)), self.legacy_extract(path)
                )


if __name__ == "__main__":
    unittest.main()
