"""Bounded private capture store (#249 A3): exact bytes, hard limits, cleanup."""

import hashlib
import os
import stat
import struct
import tempfile
import threading
import time
import unittest
import warnings
import zlib
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed, open_handles_under
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed, open_handles_under

try:  # guarded so a missing module fails assertions, not test loading
    from utils import media_capture as capture_module
    from utils.media_capture import (
        CAPTURE_EXTENSIONS,
        CaptureCapacity,
        CaptureChanged,
        CaptureDenied,
        CaptureStore,
        CaptureTooLarge,
    )
except ImportError:
    capture_module = None
    CAPTURE_EXTENSIONS = None
    CaptureCapacity = CaptureChanged = CaptureDenied = CaptureTooLarge = None
    CaptureStore = None

EPOCH = "epoch-1"
SCOPE = "a" * 64
WAIT = 30.0  # generous ceiling for cross-thread handoffs, never a short clock


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def png_bytes(width: int, height: int, pixels: bytes = b"\x00" * 16) -> bytes:
    """A structurally valid PNG whose header declares *width* x *height*."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


class FakeClock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CaptureStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(capture_module, "utils.media_capture is not implemented")
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        self.addCleanup(assert_closed, self, parent.name)
        # The resolved path: macOS /var -> /private/var, Windows 8.3 names.
        self.parent = Path(os.path.realpath(parent.name))
        self.root = self.parent / "output"
        self.root.mkdir()
        self.clock = FakeClock()

    def make_store(self, **options) -> "CaptureStore":
        options.setdefault("clock", self.clock)
        store = CaptureStore(self.parent / "captures", **options)
        self.addCleanup(store.close)
        return store

    def write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def symlink(self, target, link):
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable here: {error}")

    def wait_for_change_time_after(self, ns: int) -> None:
        """Block until a new file's ``st_ctime_ns`` passes *ns*.

        File timestamps tick coarsely (Linux jiffies, Windows ~1-16 ms). Each
        probe is a fresh name: Windows reports the creation time (or, on
        Python 3.12+, the change time) and tunnels the creation time of a
        reused name for 15 s.
        """
        deadline = time.monotonic() + WAIT
        for index in range(1_000_000):
            probe = self.parent / f"tick-{index}"
            probe.write_bytes(b"x")
            advanced = os.stat(probe).st_ctime_ns > ns
            probe.unlink()
            if advanced:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        self.fail("the filesystem clock did not advance")  # pragma: no cover

    def capture(self, store, relative="a.png", **options):
        options.setdefault("epoch", EPOCH)
        options.setdefault("scope", SCOPE)
        return store.capture(self.root, relative, **options)

    def assert_empty(self, store):
        self.assertFalse(any(Path(store.directory).iterdir()))
        self.assertEqual(
            store.stats(), {"captures": 0, "reserved_bytes": 0, "workers": 0}
        )

    # -- exact bytes and identity -------------------------------------------

    def test_capture_hashes_and_copies_exact_bytes_owner_only(self):
        source_bytes = png_bytes(3, 2, os.urandom(40))
        source = self.write("a.png", source_bytes)
        store = self.make_store()

        cap = self.capture(store, root_index=1)

        self.assertEqual(Path(cap.path).read_bytes(), source_bytes)
        self.assertEqual(cap.sha256, hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(cap.mtime_ns, os.stat(source).st_mtime_ns)
        self.assertEqual(cap.size, len(source_bytes))
        self.assertEqual(
            cap.locator,
            os.path.normcase(os.path.join(os.path.realpath(self.root), "a.png")),
        )
        self.assertEqual((cap.root_index, cap.scope, cap.epoch), (1, SCOPE, EPOCH))
        self.assertEqual((cap.media_type, cap.width, cap.height), ("image", 3, 2))
        self.assertTrue(cap.inferable)
        self.assertIsNone(cap.inferable_reason)
        self.assertIsInstance(cap.token, str)
        self.assertGreaterEqual(len(cap.token), 32)
        self.assertNotIn(cap.token, str(cap.path))
        self.assertNotIn(str(self.root), str(cap.path))
        capture_path = Path(cap.path)
        self.assertEqual(capture_path.parent, Path(store.directory))
        self.assertEqual(capture_path.suffix, ".png")
        self.assertTrue(
            Path(store.directory).name.startswith("prompt-manager-captures-")
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(store.directory).st_mode) & 0o077, 0)
            self.assertEqual(stat.S_IMODE(os.stat(cap.path).st_mode) & 0o077, 0)
        self.assertIs(store.get(cap.token), cap)
        self.assertEqual(
            store.stats(),
            {"captures": 1, "reserved_bytes": len(source_bytes), "workers": 0},
        )
        self.assertTrue(store.release(cap.token))
        self.assertFalse(store.release(cap.token))
        self.assertIsNone(store.get(cap.token))
        self.assert_empty(store)
        assert_closed(self, self.parent)

    def test_streams_in_bounded_chunks_never_the_whole_file(self):
        data = png_bytes(1, 1, os.urandom(5 * capture_module.CHUNK_SIZE + 7))
        self.write("big.png", data)
        store = self.make_store(max_file=len(data))
        sizes = []
        original = CaptureStore._read

        def hook(store_, stream, size):
            sizes.append(size)
            return original(store_, stream, size)

        with patch.object(CaptureStore, "_read", hook):
            cap = self.capture(store, "big.png")
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), capture_module.CHUNK_SIZE)
        self.assertEqual(cap.sha256, hashlib.sha256(data).hexdigest())

    def test_symlinked_file_or_root_escape_is_denied(self):
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "real.png").write_bytes(png_bytes(1, 1))
        self.write("a.png", png_bytes(1, 1))
        self.write("notes.txt", b"text")
        (self.root / "folder").mkdir()
        store = self.make_store()

        denied = [
            "../outside/real.png",
            "a/../a.png",
            "./a.png",
            "a.png/",
            "",
            "/a.png",
            "C:/a.png",
            "a\\b.png",
            "a\x00.png",
            "notes.txt",
            "folder",
            "x" * 1021 + ".png",
        ]
        for relative in denied:
            with self.subTest(relative=relative):
                with self.assertRaises(CaptureDenied):
                    self.capture(store, relative)
        with self.assertRaises(CaptureDenied):
            self.capture(store, "a.png", root_index=True)
        with self.assertRaises(CaptureChanged):
            self.capture(store, "missing.png")
        self.assert_empty(store)

        self.symlink(outside / "real.png", self.root / "link.png")
        self.symlink(outside, self.root / "linked-dir")
        with self.assertRaises(CaptureDenied):
            self.capture(store, "link.png")
        with self.assertRaises(CaptureDenied):
            self.capture(store, "linked-dir/real.png")
        self.assert_empty(store)
        # A symlink to a file inside the root is still an alias, never a locator.
        self.symlink(self.root / "a.png", self.root / "alias.png")
        with self.assertRaises(CaptureDenied):
            self.capture(store, "alias.png")
        self.assert_empty(store)

    def test_locator_matches_listing_identity_for_symlinked_root(self):
        real_root = self.parent / "real-output"
        real_root.mkdir()
        (real_root / "sub").mkdir()
        source_bytes = png_bytes(2, 2)
        (real_root / "sub" / "a.png").write_bytes(source_bytes)
        link_root = self.parent / "link-output"
        self.symlink(real_root, link_root)
        store = self.make_store()

        cap = store.capture(link_root, "sub/a.png", epoch=EPOCH, scope=SCOPE)

        expected = os.path.normcase(
            os.path.join(os.path.realpath(link_root), "sub", "a.png")
        )
        self.assertEqual(cap.locator, expected)
        self.assertEqual(
            cap.locator, os.path.normcase(os.path.realpath(link_root / "sub/a.png"))
        )
        self.assertTrue(
            cap.locator.startswith(os.path.normcase(os.path.realpath(real_root)))
        )
        self.assertEqual(cap.sha256, hashlib.sha256(source_bytes).hexdigest())

    # -- change detection ----------------------------------------------------

    def test_change_during_copy_is_detected_and_temp_removed(self):
        source_bytes = png_bytes(1, 1, b"\x11" * 40)
        source = self.write("a.png", source_bytes)
        store = self.make_store(max_file=4096)
        original = CaptureStore._read

        def appending(store_, stream, size):
            data = original(store_, stream, 16)
            if data and not getattr(appending, "done", False):
                appending.done = True
                with open(source, "ab") as handle:
                    handle.write(b"\x22" * 8)
            return data

        with patch.object(CaptureStore, "_read", appending):
            with self.assertRaises(CaptureChanged):
                store.capture(self.root, "a.png", epoch=EPOCH, scope=SCOPE)
        self.assert_empty(store)

        # Same size, different bytes, mtime restored: only the change time
        # (POSIX ctime) can tell. Windows exposes no change time through stat,
        # so there the torn copy completes and its digest describes the copy.
        source.write_bytes(source_bytes)
        before = os.stat(source)
        self.wait_for_change_time_after(before.st_ctime_ns)
        rewritten = bytes(b ^ 0xFF for b in source_bytes)

        def rewriting(store_, stream, size):
            data = original(store_, stream, 16)
            if data and not getattr(rewriting, "done", False):
                rewriting.done = True
                with open(source, "r+b") as handle:
                    handle.write(rewritten)
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            return data

        # Only a change time can tell: POSIX always has one; Windows exposes
        # it from stat on Python 3.12+ (where the OS supports the fast path)
        # and never from fstat, so detection there is best effort. The test
        # asserts the outcome, not the platform: a detected change leaves no
        # temp file; an undetected one (Windows only) completes with a digest
        # that describes the copied bytes.
        with patch.object(CaptureStore, "_read", rewriting):
            try:
                cap = store.capture(self.root, "a.png", epoch=EPOCH, scope=SCOPE)
            except CaptureChanged:
                detected = True
            else:
                detected = False
                self.assertEqual(os.name, "nt", "only Windows may miss the rewrite")
                copied = Path(cap.path).read_bytes()
                self.assertEqual(cap.sha256, hashlib.sha256(copied).hexdigest())
                store.release(cap.token)
        if os.name == "posix":
            self.assertTrue(detected)
        self.assert_empty(store)
        self.assertEqual(os.stat(source).st_mtime_ns, before.st_mtime_ns)

        if os.name == "posix":
            # The path now names another file (new inode) once the copy ends.
            source.write_bytes(source_bytes)

            def replacing(store_, stream, size):
                data = original(store_, stream, size)
                if not data and not getattr(replacing, "done", False):
                    replacing.done = True
                    other = self.root / "other.png"
                    other.write_bytes(source_bytes)
                    os.replace(other, source)
                return data

            with patch.object(CaptureStore, "_read", replacing):
                with self.assertRaises(CaptureChanged):
                    store.capture(self.root, "a.png", epoch=EPOCH, scope=SCOPE)
            self.assert_empty(store)
        assert_closed(self, self.parent)

    def test_size_cap_on_stat_and_stream(self):
        store = self.make_store(max_file=64)
        self.write("large.png", bytes(65))
        self.write("fits.png", bytes(64))

        def never(store_, stream, size):  # pragma: no cover - guard only
            raise AssertionError("an over-cap file must not be read")

        with patch.object(CaptureStore, "_read", never):
            with self.assertRaises(CaptureTooLarge):
                self.capture(store, "large.png")
        self.assert_empty(store)

        cap = self.capture(store, "fits.png")
        self.assertEqual(cap.size, 64)
        store.release(cap.token)

        growing = self.write("growing.png", bytes(40))
        original = CaptureStore._read

        def grow(store_, stream, size):
            data = original(store_, stream, 16)
            if data and not getattr(grow, "done", False):
                grow.done = True
                with open(growing, "ab") as handle:
                    handle.write(bytes(40))
            return data

        with patch.object(CaptureStore, "_read", grow):
            with self.assertRaises(CaptureTooLarge):
                self.capture(store, "growing.png")
        self.assert_empty(store)

    # -- admission -----------------------------------------------------------

    def test_count_byte_and_worker_admission_is_nonblocking(self):
        for name in ("a", "b", "c"):
            self.write(f"{name}.png", bytes(60))
        store = self.make_store(max_captures=2, max_bytes=1000)
        first = self.capture(store, "a.png")
        self.capture(store, "b.png")
        with self.assertRaises(CaptureCapacity):
            self.capture(store, "c.png")
        self.assertTrue(store.release(first.token))
        third = self.capture(store, "c.png")
        self.assertEqual(store.stats()["captures"], 2)
        store.invalidate_all()
        self.assertIsNone(store.get(third.token))
        self.assert_empty(store)

        bytes_store = self.make_store(max_bytes=100)
        kept = self.capture(bytes_store, "a.png")
        with self.assertRaises(CaptureCapacity):
            self.capture(bytes_store, "b.png")
        self.assertEqual(bytes_store.stats()["reserved_bytes"], 60)
        bytes_store.release(kept.token)
        self.capture(bytes_store, "b.png")

        workers = self.make_store(max_workers=1)
        entered, proceed = threading.Event(), threading.Event()
        original = CaptureStore._read
        results = {}

        def blocking(store_, stream, size):
            if not entered.is_set():
                entered.set()
                self.assertTrue(proceed.wait(WAIT))
            return original(store_, stream, size)

        def worker():
            try:
                results["capture"] = self.capture(workers, "a.png")
            except Exception as error:  # pragma: no cover - reported below
                results["error"] = error

        with patch.object(CaptureStore, "_read", blocking):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(WAIT))
                self.assertEqual(workers.stats()["workers"], 1)
                with self.assertRaises(CaptureCapacity):
                    self.capture(workers, "b.png")
            finally:
                proceed.set()
                thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", results)
        self.assertEqual(workers.stats()["workers"], 0)
        self.assertEqual(results["capture"].size, 60)
        self.capture(workers, "b.png")

    # -- lifetime ------------------------------------------------------------

    def test_idle_and_hard_expiry_and_in_use_retention(self):
        self.write("a.png", png_bytes(1, 1))
        self.write("b.png", png_bytes(1, 1))
        store = self.make_store(idle=900, hard=1800)
        cap = self.capture(store, "a.png")

        self.clock.advance(899)
        self.assertIs(store.get(cap.token), cap)  # touches idle
        self.assertAlmostEqual(store.expires_in(cap.token), 900)
        self.clock.advance(899)
        self.assertIs(store.get(cap.token), cap)
        self.assertAlmostEqual(store.expires_in(cap.token), 2)
        self.clock.advance(2)
        self.assertIsNone(store.get(cap.token))  # hard limit despite touches
        self.assertIsNone(store.expires_in(cap.token))
        self.assertFalse(Path(cap.path).exists())
        self.assert_empty(store)

        idle = self.capture(store, "a.png")
        self.clock.advance(900)
        store.sweep()
        self.assertIsNone(store.get(idle.token))
        self.assert_empty(store)

        used = self.capture(store, "b.png")
        self.assertIs(store.acquire(used.token), used)
        self.assertIs(store.acquire(used.token), used)
        self.clock.advance(901)
        store.sweep()
        self.assertIs(store.get(used.token), used)  # in use is not idle
        self.clock.advance(900)
        store.sweep()
        self.assertIsNone(store.get(used.token))  # expired for new requests
        self.assertIsNone(store.acquire(used.token))
        self.assertTrue(Path(used.path).exists())  # retained while in use
        self.assertEqual(store.stats()["captures"], 1)
        store.release_use(used.token)
        self.assertTrue(Path(used.path).exists())
        store.release_use(used.token)
        self.assertFalse(Path(used.path).exists())
        self.assert_empty(store)
        store.release_use(used.token)  # unknown token: no error, nothing freed
        self.assertIsNone(store.acquire("unknown"))

    def test_release_while_in_use_keeps_file_until_last_use_ends(self):
        self.write("a.png", png_bytes(1, 1))
        store = self.make_store()
        cap = self.capture(store, "a.png")
        self.assertIs(store.acquire(cap.token), cap)
        self.assertTrue(store.release(cap.token))
        self.assertIsNone(store.get(cap.token))
        self.assertTrue(Path(cap.path).exists())
        store.invalidate_all()
        self.assertTrue(Path(cap.path).exists())
        store.release_use(cap.token)
        self.assertFalse(Path(cap.path).exists())
        self.assert_empty(store)

    def test_invalidate_all_and_close_remove_owned_directory_only(self):
        self.write("a.png", png_bytes(1, 1))
        self.write("b.png", png_bytes(1, 1))
        sibling = self.parent / "captures" / "sibling.txt"
        sibling.parent.mkdir()
        sibling.write_bytes(b"keep")
        store = CaptureStore(self.parent / "captures", clock=self.clock)
        try:
            a = self.capture(store, "a.png")
            b = self.capture(store, "b.png")
            self.assertEqual(Path(store.directory).parent, sibling.parent)
            store.invalidate_all()
            self.assertIsNone(store.get(a.token))
            self.assertIsNone(store.get(b.token))
            self.assert_empty(store)
            self.assertTrue(Path(store.directory).is_dir())
            c = self.capture(store, "a.png")
            self.assertTrue(Path(c.path).exists())
        finally:
            store.close()
        self.assertFalse(Path(store.directory).exists())
        self.assertEqual(sibling.read_bytes(), b"keep")
        self.assertTrue((self.root / "a.png").exists())
        store.close()  # idempotent
        with self.assertRaises(RuntimeError):
            self.capture(store, "a.png")
        self.assertIsNone(store.get(c.token))
        assert_closed(self, self.parent)

    def test_windows_handles_closed_before_unlink(self):
        source_bytes = png_bytes(1, 1, b"\x33" * 40)
        source = self.write("a.png", source_bytes)
        store = self.make_store()
        original_unlink = os.unlink
        unlinked = []

        def checked_unlink(path, *args, **kwargs):
            self.assertEqual(open_handles_under(path), [])
            self.assertTrue(store._lock.acquire(blocking=False))  # no I/O under lock
            store._lock.release()
            unlinked.append(os.fspath(path))
            return original_unlink(path, *args, **kwargs)

        with patch("os.unlink", checked_unlink):
            cap = self.capture(store, "a.png")
            self.assertEqual(open_handles_under(cap.path), [])
            self.assertEqual(open_handles_under(source), [])
            store.release(cap.token)
            original = CaptureStore._read

            def appending(store_, stream, size):
                data = original(store_, stream, 16)
                if data and not getattr(appending, "done", False):
                    appending.done = True
                    with open(source, "ab") as handle:
                        handle.write(b"\x44")
                return data

            with patch.object(CaptureStore, "_read", appending):
                with self.assertRaises(CaptureChanged):
                    self.capture(store, "a.png")
            self.assertEqual(open_handles_under(source), [])
        self.assertEqual(len(unlinked), 2)
        self.assertTrue(all(p.startswith(str(store.directory)) for p in unlinked))
        self.assert_empty(store)
        assert_closed(self, self.parent)

    # -- inference readiness -------------------------------------------------

    def test_dimension_limits_mark_generate_unsupported(self):
        cases = {
            "edge.png": (png_bytes(4096, 4096), True),
            "wide.png": (png_bytes(4097, 1), False),
            "tall.png": (png_bytes(1, 4097), False),
        }
        for name, (data, _) in cases.items():
            self.write(name, data)
        self.write("garbage.jpg", b"\xff\xd8not really a jpeg")
        store = self.make_store()

        for name, (data, supported) in cases.items():
            with self.subTest(name=name):
                cap = self.capture(store, name)
                self.assertEqual(cap.inferable, supported)
                self.assertEqual((cap.width, cap.height) != (None, None), True)
                if supported:
                    self.assertIsNone(cap.inferable_reason)
                else:
                    self.assertIn("4096", cap.inferable_reason)
                store.release(cap.token)
        self.assertEqual(
            (capture_module.MAX_INFERENCE_SIDE, capture_module.MAX_INFERENCE_PIXELS),
            (4096, 16_777_216),
        )
        cap = self.capture(store, "garbage.jpg")
        self.assertEqual((cap.media_type, cap.width, cap.height), ("image", None, None))
        self.assertFalse(cap.inferable)
        self.assertIn("header", cap.inferable_reason)
        self.assertEqual(
            cap.sha256, hashlib.sha256(b"\xff\xd8not really a jpeg").hexdigest()
        )

    def test_metadata_over_inspection_budgets_does_not_block_generate(self):
        # B2 carry-over (A M1): inferable is decided from the image header
        # alone, so prompt metadata over the inspection budgets (here a 3 MiB
        # text chunk) leaves a decodable image inferable.
        base = png_bytes(64, 32)
        huge = _chunk(b"tEXt", b"prompt\x00" + b"x" * (3 * 1024 * 1024))
        self.write("big-metadata.png", base[:-12] + huge + base[-12:])
        store = self.make_store()

        cap = self.capture(store, "big-metadata.png")

        self.assertEqual((cap.width, cap.height), (64, 32))
        self.assertTrue(cap.inferable)
        self.assertIsNone(cap.inferable_reason)
        assert_closed(self, self.parent)

    def test_decompression_bomb_header_is_not_inferable(self):
        self.write("bomb.png", png_bytes(20_000, 20_000))
        store = self.make_store()

        cap = self.capture(store, "bomb.png")

        self.assertFalse(cap.inferable)
        self.assertIn("4096", cap.inferable_reason)
        assert_closed(self, self.parent)

    def test_header_dimensions_reads_only_the_header(self):
        path = self.write("dims.png", png_bytes(7, 5))
        self.assertEqual(capture_module.header_dimensions(str(path)), (7, 5))
        assert_closed(self, self.parent)
        with self.assertRaises(capture_module.HeaderUnreadable):
            capture_module.header_dimensions(
                str(self.write("bad.png", b"\x89PNG\r\n\x1a\nnope"))
            )
        assert_closed(self, self.parent)

    def test_header_dimensions_leaves_the_warning_filters_alone(self):
        # The capture workers run in the ComfyUI process: touching the
        # process-global warnings filters from them races (catch_warnings is
        # not thread-safe) and can leave an "error" filter installed for every
        # later Pillow open in the host.
        from PIL import Image

        path = str(self.write("dims.png", png_bytes(7, 5)))
        before = list(warnings.filters)
        seen_during = []
        real_open = Image.open

        def recording_open(*args, **kwargs):
            seen_during.append(list(warnings.filters))
            return real_open(*args, **kwargs)

        with patch.object(Image, "open", recording_open):
            self.assertEqual(capture_module.header_dimensions(path), (7, 5))
        self.assertEqual(seen_during, [before])
        self.assertEqual(list(warnings.filters), before)

        barrier = threading.Barrier(8)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(50):
                    capture_module.header_dimensions(path)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(list(warnings.filters), before)
        assert_closed(self, self.parent)

    def test_header_over_the_warning_size_is_refused_by_the_pixel_cap(self):
        # 100 MP: past Pillow's warning size, under its error size. The
        # header is read, then the inference pixel cap refuses it.
        from PIL import Image

        path = str(self.write("big.png", png_bytes(10_000, 10_000)))
        with self.assertWarns(Image.DecompressionBombWarning):
            self.assertEqual(capture_module.header_dimensions(path), (10_000, 10_000))
        store = self.make_store()
        with self.assertWarns(Image.DecompressionBombWarning):
            cap = self.capture(store, "big.png")
        self.assertFalse(cap.inferable)
        self.assertIn("4096", cap.inferable_reason)
        assert_closed(self, self.parent)

    def test_video_capture_is_not_inferable(self):
        data = b"\x00\x00\x00\x18ftypmp42" + os.urandom(32)
        self.write("clip.mp4", data)
        store = self.make_store()

        def never(path):  # pragma: no cover - guard only
            raise AssertionError("video captures are never probed")

        with patch.object(capture_module, "header_dimensions", never):
            cap = self.capture(store, "clip.mp4")
        self.assertEqual((cap.media_type, cap.width, cap.height), ("video", None, None))
        self.assertFalse(cap.inferable)
        self.assertEqual(
            cap.inferable_reason,
            "Auto-tagging is available for supported still images only.",
        )
        self.assertEqual(Path(cap.path).suffix, ".mp4")
        self.assertEqual(cap.sha256, hashlib.sha256(data).hexdigest())

    # -- verification without a copy -------------------------------------

    def test_verify_hashes_current_file_without_retaining_a_copy(self):
        source_bytes = png_bytes(1, 1, b"\x55" * 30)
        source = self.write("a.png", source_bytes)
        store = self.make_store(max_file=128, max_workers=1)

        facts = store.verify(self.root, "a.png")

        self.assertEqual(facts.sha256, hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(facts.size, len(source_bytes))
        self.assertEqual(facts.mtime_ns, os.stat(source).st_mtime_ns)
        self.assertEqual(
            facts.locator,
            os.path.normcase(os.path.join(os.path.realpath(self.root), "a.png")),
        )
        self.assert_empty(store)
        self.write("large.png", bytes(129))
        with self.assertRaises(CaptureTooLarge):
            store.verify(self.root, "large.png")
        with self.assertRaises(CaptureDenied):
            store.verify(self.root, "../a.png")
        self.assert_empty(store)

    # -- arguments and failure paths ---------------------------------------

    def test_constructor_and_arguments_are_validated(self):
        for options in (
            {"max_file": 0},
            {"max_captures": True},
            {"max_bytes": -1},
            {"max_workers": "2"},
            {"idle": 0},
            {"hard": -5},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    CaptureStore(self.parent / "captures", **options)
        self.write("a.png", png_bytes(1, 1))
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.capture(self.root, "a.png", epoch="", scope=SCOPE)
        with self.assertRaises(ValueError):
            store.capture(self.root, "a.png", epoch=EPOCH, scope=None)
        with self.assertRaises(CaptureDenied):
            self.capture(store, root_index=-1)
        with self.assertRaises(CaptureDenied):
            store.capture(
                self.parent / "missing-root", "a.png", epoch=EPOCH, scope=SCOPE
            )
        self.assert_empty(store)

        default = CaptureStore(clock=self.clock)
        try:
            self.assertEqual(
                Path(default.directory).parent, Path(tempfile.gettempdir())
            )
            self.assertTrue(Path(default.directory).is_dir())
        finally:
            default.close()
        self.assertFalse(Path(default.directory).exists())

    def test_directory_target_and_unreadable_file(self):
        (self.root / "dir.png").mkdir()
        store = self.make_store()
        with self.assertRaises(CaptureDenied):
            self.capture(store, "dir.png")
        self.assert_empty(store)
        if os.name != "posix" or os.geteuid() == 0:
            return  # read permission cannot be withdrawn here
        locked = self.write("locked.png", png_bytes(1, 1))
        os.chmod(locked, 0)
        try:
            with self.assertRaises(CaptureChanged):
                self.capture(store, "locked.png")
        finally:
            os.chmod(locked, 0o600)
        self.assert_empty(store)

    def test_removal_failures_are_logged_and_never_touch_other_files(self):
        self.write("a.png", png_bytes(1, 1))
        store = CaptureStore(self.parent / "captures", clock=self.clock)
        stray = Path(store.directory) / "stray.bin"
        try:
            cap = self.capture(store, "a.png")
            self.assertIs(store.acquire(cap.token), cap)
            self.assertAlmostEqual(store.expires_in(cap.token), 1800)  # in use
            store.release_use(cap.token)
            original_unlink = os.unlink

            def failing(path, *args, **kwargs):
                if os.fspath(path) == cap.path:
                    raise PermissionError("busy")
                return original_unlink(path, *args, **kwargs)

            with patch("os.unlink", failing):
                with self.assertLogs("utils.media_capture", "WARNING") as logs:
                    self.assertTrue(store.release(cap.token))
            self.assertIn("could not be removed", logs.output[0])
            self.assertIsNone(store.get(cap.token))
            self.assertTrue(Path(cap.path).exists())
            os.unlink(cap.path)
            stray.write_bytes(b"not ours")
        finally:
            with self.assertLogs("utils.media_capture", "WARNING") as logs:
                store.close()
        self.assertIn("could not be removed", logs.output[0])
        self.assertEqual(stray.read_bytes(), b"not ours")
        stray.unlink()
        os.rmdir(store.directory)
        assert_closed(self, self.parent)

    # -- platform identity checks -------------------------------------------

    def test_path_and_handle_stats_are_never_compared_across_apis(self):
        """Windows (Python 3.12+) reports the change time from stat but the
        creation time from fstat, and may encode dev/ino differently; only
        size and mtime_ns may be compared across the two APIs."""
        source_bytes = png_bytes(2, 2, b"\x66" * 40)
        self.write("a.png", source_bytes)
        store = self.make_store()
        original_fstat = os.fstat

        def shifted(fd):
            st = original_fstat(fd)
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino + 7,
                    st.st_dev + 7,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime + 1,
                ),
                {
                    "st_atime_ns": st.st_atime_ns,
                    "st_mtime_ns": st.st_mtime_ns,
                    "st_ctime_ns": st.st_ctime_ns + 1_000_000_000,
                },
            )

        with patch.object(capture_module.os, "fstat", shifted):
            cap = self.capture(store, "a.png")
            facts = store.verify(self.root, "a.png")
        self.assertEqual(cap.sha256, hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(facts.sha256, cap.sha256)
        store.release(cap.token)
        self.assert_empty(store)

    def test_change_time_moving_between_fstat_reads_is_detected(self):
        """The Windows 3.13 / POSIX signal, forced on every platform: the
        descriptor's change time differs after the copy, size and mtime equal."""
        self.write("a.png", png_bytes(1, 1, b"\x77" * 40))
        store = self.make_store()
        original_fstat = os.fstat
        calls = []

        def drifting(fd):
            st = original_fstat(fd)
            calls.append(fd)
            if len(calls) < 2:
                return st
            return os.stat_result(
                tuple(st)[:10],
                {
                    "st_atime_ns": st.st_atime_ns,
                    "st_mtime_ns": st.st_mtime_ns,
                    "st_ctime_ns": st.st_ctime_ns + 1,
                },
            )

        with patch.object(capture_module.os, "fstat", drifting):
            with self.assertRaises(CaptureChanged):
                self.capture(store, "a.png")
        self.assertEqual(len(calls), 2)
        self.assert_empty(store)

    def test_fresh_file_captures_twice_on_every_platform(self):
        data = png_bytes(1, 1, os.urandom(3 * capture_module.CHUNK_SIZE))
        self.write("fresh.png", data)
        store = self.make_store()
        first = self.capture(store, "fresh.png")
        second = self.capture(store, "fresh.png")
        self.assertEqual(first.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(second.sha256, first.sha256)
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(store.stats()["captures"], 2)

    def test_error_messages_never_name_server_paths(self):
        self.write("a.png", png_bytes(1, 1))
        self.write("dir.png/x", b"")  # dir.png is a directory
        store = self.make_store(max_file=64)
        attempts = [
            lambda: self.capture(store, "missing.png"),
            lambda: self.capture(store, "a.png"),  # over the 64-byte cap
            lambda: self.capture(store, "dir.png"),
            lambda: self.capture(store, "../a.png"),
            lambda: self.capture(store, "notes.txt"),
            lambda: store.verify(self.root, "missing.png"),
        ]
        if os.name == "posix" and os.geteuid() != 0:
            locked = self.write("locked.png", bytes(10))
            os.chmod(locked, 0)
            self.addCleanup(os.chmod, locked, 0o600)
            attempts.append(lambda: self.capture(store, "locked.png"))
        for attempt in attempts:
            with self.assertRaises(capture_module.CaptureError) as caught:
                attempt()
            message = str(caught.exception)
            self.assertTrue(message)
            self.assertNotIn(str(self.root), message)
            self.assertNotIn(str(self.parent), message)
            self.assertNotIn(os.sep, message)
        self.assert_empty(store)

    def test_close_during_a_capture_removes_the_directory_once_it_finishes(self):
        self.write("a.png", png_bytes(1, 1, bytes(64)))
        store = CaptureStore(self.parent / "captures", clock=self.clock)
        original = CaptureStore._read

        def closing(store_, stream, size):
            data = original(store_, stream, 16)
            if data and not getattr(closing, "done", False):
                closing.done = True
                store.close()
            return data

        with patch.object(CaptureStore, "_read", closing):
            with self.assertRaises(RuntimeError):
                self.capture(store, "a.png")
        self.assertFalse(Path(store.directory).exists())
        self.assertEqual(store.stats()["reserved_bytes"], 0)
        store.close()
        assert_closed(self, self.parent)

    # -- constants ---------------------------------------------------------

    def test_defaults_match_the_plan(self):
        store = self.make_store()
        self.assertEqual(
            (
                store.max_file,
                store.max_captures,
                store.max_bytes,
                store.max_workers,
                store.idle,
                store.hard,
            ),
            (256 * 2**20, 8, 512 * 2**20, 2, 900, 1800),
        )
        from py.api.inspection_service import SERVABLE_EXTENSIONS

        self.assertEqual(CAPTURE_EXTENSIONS, SERVABLE_EXTENSIONS)

    def test_exceptions_carry_codes(self):
        self.assertEqual(CaptureCapacity.code, "review-capacity")
        self.assertEqual(CaptureChanged.code, "file-changed")
        self.assertEqual(CaptureTooLarge.code, "unsupported-media")
        self.assertEqual(CaptureDenied.code, "source-changed")
        for cls in (CaptureCapacity, CaptureChanged, CaptureTooLarge, CaptureDenied):
            self.assertTrue(issubclass(cls, capture_module.CaptureError))


if __name__ == "__main__":
    unittest.main()
