"""Thumbnail encoders (#317): WebP, JPEG and an optional JPEG XL encoder.

``py/api/thumbnail_encode.py`` writes every preview: the first frame fitted
to the edge, encoded in the configured format into a temp sibling that is
closed, verified to decode and renamed over the preview. JPEG XL needs no new
dependency: Pillow (natively or through ``pillow_jxl``), ffmpeg's ``libjxl``
or ``cjxl``, in that order. Encoder processes are tracked, bounded and
killed on shutdown.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from unittest.mock import MagicMock

sys.modules.setdefault("server", MagicMock())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from py.api import thumbnail_encode as te  # noqa: E402
from py.api.thumbnail_settings import ThumbnailSettings  # noqa: E402

JXL_CODESTREAM = b"\xff\x0a"
JXL_CONTAINER = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _ffmpeg_has_libjxl():
    try:
        return te._ffmpeg_has_libjxl()
    except Exception:
        return False


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        te.reset_encoders()
        self.addCleanup(te.reset_encoders)

    def _image(self, size=(800, 400), color=(200, 30, 30), mode="RGB"):
        return Image.new(mode, size, color)

    def _leftovers(self):
        return [p.name for p in self.tmp.rglob("*") if p.name.startswith(".")]


class TestDistance(unittest.TestCase):
    def test_quality_maps_to_libjxl_distance(self):
        self.assertAlmostEqual(te.jxl_distance(80), 1.9)
        self.assertAlmostEqual(te.jxl_distance(85), 1.45)
        self.assertAlmostEqual(te.jxl_distance(90), 1.0)
        self.assertAlmostEqual(te.jxl_distance(30), 6.4)
        self.assertAlmostEqual(te.jxl_distance(1), 23.8676666, places=4)
        # A preview is never encoded lossless.
        self.assertAlmostEqual(te.jxl_distance(100), 0.1)


class TestDetection(TempCase):
    def test_pillow_native_first(self):
        with patch.dict(Image.SAVE, {"JXL": object()}):
            self.assertEqual(te.detect_jxl_encoder(refresh=True), "pillow")

    def test_pillow_plugin_is_imported_when_installed(self):
        def fake_import(name):
            if name != "pillow_jxl":
                raise ImportError(name)
            Image.SAVE["JXL"] = object()
            return object()

        self.addCleanup(Image.SAVE.pop, "JXL", None)
        with patch.object(te.importlib, "import_module", side_effect=fake_import):
            self.assertEqual(te.detect_jxl_encoder(refresh=True), "pillow")

    def _no_pillow(self):
        saved = Image.SAVE.pop("JXL", None)
        if saved is not None:
            self.addCleanup(Image.SAVE.__setitem__, "JXL", saved)
        return patch.object(te.importlib, "import_module", side_effect=ImportError)

    def test_ffmpeg_with_libjxl_then_cjxl_then_none(self):
        with self._no_pillow():
            with patch.object(te, "_ffmpeg_has_libjxl", return_value=True):
                self.assertEqual(te.detect_jxl_encoder(refresh=True), "ffmpeg")
            with (
                patch.object(te, "_ffmpeg_has_libjxl", return_value=False),
                patch.object(te.shutil, "which", return_value="/usr/bin/cjxl"),
            ):
                self.assertEqual(te.detect_jxl_encoder(refresh=True), "cjxl")
            with (
                patch.object(te, "_ffmpeg_has_libjxl", return_value=False),
                patch.object(te.shutil, "which", return_value=None),
            ):
                self.assertIsNone(te.detect_jxl_encoder(refresh=True))
                status = te.encoder_status()
        self.assertEqual(
            status["jxl"],
            {"available": False, "encoder": None, "reason": te.JXL_UNAVAILABLE},
        )
        self.assertIn("pillow-jxl-plugin", te.JXL_UNAVAILABLE)
        self.assertTrue(status["webp"]["available"])
        self.assertTrue(status["jpeg"]["available"])

    def test_detection_is_cached_until_refreshed(self):
        with (
            self._no_pillow(),
            patch.object(te, "_ffmpeg_has_libjxl", return_value=True) as probe,
        ):
            te.detect_jxl_encoder(refresh=True)
            te.detect_jxl_encoder()
            te.encoder_status()
        self.assertEqual(probe.call_count, 1)

    def test_ffmpeg_probe_reads_the_encoder_list(self):
        listing = " V....D libjxl               libjxl JPEG XL (codec jpegxl)\n"
        done = subprocess.CompletedProcess([], 0, listing, "")
        with (
            patch.object(te, "run_encoder", return_value=done),
            patch.object(te.shutil, "which", return_value="/usr/bin/ffmpeg"),
        ):
            self.assertTrue(te._ffmpeg_has_libjxl())
        other = subprocess.CompletedProcess([], 0, " V..... libwebp\n", "")
        with (
            patch.object(te, "run_encoder", return_value=other),
            patch.object(te.shutil, "which", return_value="/usr/bin/ffmpeg"),
        ):
            self.assertFalse(te._ffmpeg_has_libjxl())
        with patch.object(te.shutil, "which", return_value=None):
            self.assertFalse(te._ffmpeg_has_libjxl())


class TestFrame(TempCase):
    def test_first_frame_of_an_animation_is_a_still(self):
        path = self.tmp / "anim.gif"
        frames = [Image.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 0, 255))]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=50)
        with Image.open(path) as img:
            img.seek(1)
            frame = te.thumbnail_frame(img, 32)
        self.assertEqual(frame.mode, "RGB")
        self.assertEqual(frame.size, (32, 32))
        red, green, blue = frame.getpixel((16, 16))
        self.assertGreater(red, 200)
        self.assertLess(blue, 50)

    def test_modes_without_rgb_are_converted_and_fitted(self):
        for mode, color in (("RGBA", (1, 2, 3, 0)), ("I;16", 300), ("L", 9)):
            frame = te.thumbnail_frame(self._image((1000, 250), color, mode), 512)
            self.assertEqual((frame.mode, frame.size), ("RGB", (512, 128)))


class TestWrite(TempCase):
    def _settings(self, fmt="webp", quality=80, edge=512):
        return ThumbnailSettings(fmt, quality, edge)

    def test_webp_and_jpeg_are_written_in_their_format(self):
        for fmt, name, pil in (("webp", "a.webp", "WEBP"), ("jpeg", "a.jpg", "JPEG")):
            dst = self.tmp / name
            written = te.write_thumbnail(self._image(), dst, self._settings(fmt))
            self.assertEqual(written, dst)
            with Image.open(dst) as img:
                self.assertEqual(img.format, pil)
        self.assertEqual(self._leftovers(), [])

    def test_quality_changes_the_bytes(self):
        noisy = Image.effect_noise((256, 256), 64).convert("RGB")
        low = te.write_thumbnail(
            noisy, self.tmp / "low.webp", self._settings(quality=10)
        )
        high = te.write_thumbnail(
            noisy, self.tmp / "high.webp", self._settings(quality=95)
        )
        self.assertLess(low.stat().st_size, high.stat().st_size)

    def test_guard_false_keeps_the_old_preview(self):
        dst = self.tmp / "a.webp"
        dst.write_bytes(b"old")
        with self.assertRaises(te.PreviewWriteHeld):
            te.write_thumbnail(
                self._image(), dst, self._settings(), guard=lambda: False
            )
        self.assertEqual(dst.read_bytes(), b"old")
        self.assertEqual(self._leftovers(), [])

    def test_a_file_that_does_not_decode_is_never_renamed_in(self):
        dst = self.tmp / "a.webp"
        dst.write_bytes(b"old")

        def broken(img, tmp, settings, track):
            Path(tmp).write_bytes(b"not an image")

        with patch.dict(te._WRITERS, {"webp": broken}):
            with self.assertRaises(te.ThumbnailVerifyError):
                te.write_thumbnail(self._image(), dst, self._settings())
        self.assertEqual(dst.read_bytes(), b"old")
        self.assertEqual(self._leftovers(), [])

    def test_jxl_without_an_encoder_falls_back_to_webp_and_logs_once(self):
        with patch.object(te, "detect_jxl_encoder", return_value=None):
            with self.assertLogs("prompt_manager.thumbnails", logging.WARNING) as logs:
                first = te.write_thumbnail(
                    self._image(), self.tmp / "a.jxl", self._settings("jxl")
                )
                second = te.write_thumbnail(
                    self._image(), self.tmp / "b.jxl", self._settings("jxl")
                )
        self.assertEqual((first.name, second.name), ("a.webp", "b.webp"))
        self.assertEqual(len([m for m in logs.output if "JPEG XL" in m]), 1)
        with Image.open(first) as img:
            self.assertEqual(img.format, "WEBP")

    def _write_jxl(self, encoder):
        dst = self.tmp / f"{encoder}.jxl"
        with patch.object(te, "detect_jxl_encoder", return_value=encoder):
            written = te.write_thumbnail(self._image(), dst, self._settings("jxl"))
        self.assertEqual(written, dst)
        head = dst.read_bytes()[:12]
        self.assertTrue(
            head.startswith(JXL_CODESTREAM) or head.startswith(JXL_CONTAINER), head
        )
        self.assertEqual(self._leftovers(), [])

    @unittest.skipUnless(_ffmpeg_has_libjxl(), "ffmpeg without libjxl")
    def test_jxl_through_ffmpeg(self):
        self._write_jxl("ffmpeg")

    @unittest.skipUnless(shutil.which("cjxl"), "no cjxl")
    def test_jxl_through_cjxl(self):
        self._write_jxl("cjxl")

    def test_jxl_through_pillow_passes_the_quality(self):
        seen = {}

        def save_jxl(im, fp, filename):
            seen.update(im.encoderinfo)
            fp.write(JXL_CODESTREAM + b"\x00" * 16)

        Image.register_save("PMTESTJXL", save_jxl)
        self.addCleanup(Image.SAVE.pop, "PMTESTJXL", None)
        with (
            patch.object(te, "PILLOW_JXL_FORMAT", "PMTESTJXL"),
            patch.object(te, "detect_jxl_encoder", return_value="pillow"),
        ):
            te.write_thumbnail(self._image(), self.tmp / "p.jxl", self._settings("jxl"))
        self.assertEqual(seen["quality"], 80)
        self.assertEqual(seen["num_threads"], 1)

    def test_a_failing_jxl_encoder_raises_and_keeps_nothing(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"boom")
        with (
            patch.object(te, "detect_jxl_encoder", return_value="cjxl"),
            patch.object(te, "run_encoder", return_value=failed),
        ):
            with self.assertRaises(te.EncoderFailed):
                te.write_thumbnail(
                    self._image(), self.tmp / "c.jxl", self._settings("jxl")
                )
        self.assertFalse((self.tmp / "c.jxl").exists())
        self.assertEqual(self._leftovers(), [])

    def test_verify_accepts_a_jxl_signature_when_pillow_cannot_decode(self):
        path = self.tmp / "s.jxl"
        path.write_bytes(JXL_CONTAINER + b"\x00" * 8)
        with patch.object(te, "_pillow_decodes_jxl", return_value=False):
            te.verify_thumbnail(path, "jxl")
            path.write_bytes(b"\x89PNG")
            with self.assertRaises(te.ThumbnailVerifyError):
                te.verify_thumbnail(path, "jxl")


class TestFfmpegNames(TempCase):
    """Review I1: ffmpeg's image2 muxer reads ``%d`` in an output name as a
    sequence pattern; ``-update 1`` keeps the temp name literal."""

    def test_the_ffmpeg_command_writes_one_literal_file(self):
        seen = []

        def fake(cmd, **kwargs):
            seen.append(cmd)
            Path(cmd[-1]).write_bytes(JXL_CODESTREAM + b"\x00" * 8)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with (
            patch.object(te, "detect_jxl_encoder", return_value="ffmpeg"),
            patch.object(te, "run_encoder", side_effect=fake),
            patch.object(te, "_pillow_decodes_jxl", return_value=False),
        ):
            te.write_thumbnail(
                Image.new("RGB", (8, 8)),
                self.tmp / "a_thumb.jxl",
                ThumbnailSettings("jxl", 80, 64),
            )
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("-update") + 1], "1")
        self.assertLess(cmd.index("-update"), len(cmd) - 1)

    @unittest.skipUnless(_ffmpeg_has_libjxl(), "ffmpeg without libjxl")
    def test_a_percent_in_the_name_is_written_under_that_name(self):
        dst = self.tmp / "50%done_thumb.jxl"
        with patch.object(te, "detect_jxl_encoder", return_value="ffmpeg"):
            written = te.write_thumbnail(
                Image.new("RGB", (32, 32), (10, 200, 10)),
                dst,
                ThumbnailSettings("jxl", 80, 64),
            )
        self.assertEqual(written, dst)
        self.assertTrue(dst.is_file())
        self.assertEqual(self._leftovers(), [], "no stray sequence-named temp")


class TestStrayTemps(TempCase):
    """Review I1: a temp left by a crash (or the old ``%`` bug) is swept."""

    def _touch(self, name, age=3600):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        when = path.stat().st_mtime - age
        os.utime(path, (when, when))
        return path

    def test_only_old_temp_siblings_are_removed(self):
        strays = [
            self._touch(".a_thumb.part-12-34.webp"),
            self._touch(".501one.part-1-2.jxl"),
            self._touch("sub/.b_thumb.src-5-6.png"),
        ]
        kept = [
            self._touch("a_thumb.webp"),
            self._touch(".hidden"),
            self._touch("notes.part-1-2.webp"),
            self._touch(".c_thumb.part-x-2.webp"),
            self._touch(".young_thumb.part-7-8.webp", age=0),
        ]
        own = self._touch(f".mine_thumb.part-{os.getpid()}-9.webp")
        count, size = te.sweep_stray_temps(self.tmp, min_age=60)
        self.assertEqual((count, size), (3, 30))
        for path in strays:
            self.assertFalse(path.exists(), path.name)
        for path in kept + [own]:
            self.assertTrue(path.exists(), path.name)

    def test_without_an_age_every_temp_goes_but_links_stay(self):
        stray = self._touch(".a_thumb.part-1-2.webp", age=0)
        own = self._touch(f".mine_thumb.part-{os.getpid()}-9.webp", age=0)
        target = self._touch("target.bin")
        link = self.tmp / ".l_thumb.part-1-2.webp"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            link = None
        self.assertEqual(te.sweep_stray_temps(self.tmp, min_age=0)[0], 2)
        self.assertFalse(stray.exists() or own.exists())
        if link is not None:
            self.assertTrue(link.is_symlink())

    def test_a_missing_folder_sweeps_nothing(self):
        self.assertEqual(te.sweep_stray_temps(self.tmp / "none"), (0, 0))


class TestEncoderVanishes(TempCase):
    """Review I3: an encoder that disappears after detection is detected
    again once; with none left, that file is written as WebP and the
    availability Appearance reads is refreshed."""

    def test_a_missing_binary_falls_back_to_webp_and_refreshes(self):
        found = iter(["cjxl", None])
        with (
            patch.object(te, "_detect", side_effect=lambda: next(found)),
            patch.object(te, "run_encoder", side_effect=FileNotFoundError("cjxl")),
            self.assertLogs("prompt_manager.thumbnails", logging.WARNING),
        ):
            self.assertTrue(te.encoder_status()["jxl"]["available"])
            written = te.write_thumbnail(
                Image.new("RGB", (16, 16)),
                self.tmp / "v_thumb.jxl",
                ThumbnailSettings("jxl", 80, 64),
            )
            status = te.encoder_status()["jxl"]
        self.assertEqual(written.name, "v_thumb.webp")
        with Image.open(written) as img:
            self.assertEqual(img.format, "WEBP")
        self.assertEqual((status["available"], status["encoder"]), (False, None))
        self.assertEqual(self._leftovers(), [])

    def test_another_encoder_found_again_is_used(self):
        found = iter(["cjxl", "ffmpeg"])
        used = []

        def fake(cmd, **kwargs):
            used.append(cmd[0])
            if cmd[0] == "cjxl":
                raise FileNotFoundError("cjxl")
            Path(cmd[-1]).write_bytes(JXL_CODESTREAM + b"\x00" * 8)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with (
            patch.object(te, "_detect", side_effect=lambda: next(found)),
            patch.object(te, "run_encoder", side_effect=fake),
            patch.object(te, "_pillow_decodes_jxl", return_value=False),
        ):
            written = te.write_thumbnail(
                Image.new("RGB", (16, 16)),
                self.tmp / "w_thumb.jxl",
                ThumbnailSettings("jxl", 80, 64),
            )
        self.assertEqual(written.name, "w_thumb.jxl")
        self.assertEqual(used, ["cjxl", "ffmpeg"])
        self.assertEqual(self._leftovers(), [])

    def test_the_same_encoder_failing_again_raises(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"bad input")
        with (
            patch.object(te, "_detect", return_value="cjxl") as detect,
            patch.object(te, "run_encoder", return_value=failed),
        ):
            with self.assertRaises(te.EncoderFailed):
                te.write_thumbnail(
                    Image.new("RGB", (16, 16)),
                    self.tmp / "x_thumb.jxl",
                    ThumbnailSettings("jxl", 80, 64),
                )
        self.assertEqual(detect.call_count, 2, "detected again once")


class TestEncoderProcesses(TempCase):
    SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]

    def test_a_process_past_its_timeout_is_killed(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            te.run_encoder(self.SLEEPER, timeout=0.5)

    def test_shutdown_kills_running_encoders_and_refuses_new_ones(self):
        started = threading.Event()
        tracked = []

        def track(process):
            tracked.append(process)
            started.set()

        outcome = {}

        def run():
            try:
                outcome["result"] = te.run_encoder(
                    self.SLEEPER, timeout=60, track=track
                )
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                outcome["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(20))
        te.shutdown_encoders()
        worker.join(20)
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(tracked[0].poll())
        self.assertIsInstance(outcome.get("error"), te.EncoderClosed)
        with self.assertRaises(te.EncoderClosed):
            te.run_encoder(self.SLEEPER, timeout=1)

    def test_input_is_piped_and_output_returned(self):
        echo = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])",
        ]
        done = te.run_encoder(echo, input=b"abc", timeout=30)
        self.assertEqual((done.returncode, done.stdout), (0, b"cba"))


if __name__ == "__main__":
    unittest.main()
