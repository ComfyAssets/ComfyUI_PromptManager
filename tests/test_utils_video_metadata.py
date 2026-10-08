"""Tests for utils/video_metadata.py: prompt metadata from video containers.

ComfyUI's video savers store ``{"prompt": ..., "workflow": ...}`` as JSON in
the container's ``comment`` tag. ffprobe reads it; when ffprobe is missing
videos are skipped without noise.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import video_metadata  # noqa: E402
from utils.video_metadata import (  # noqa: E402
    VIDEO_EXTENSIONS,
    is_video_path,
    metadata_from_tags,
    read_video_metadata,
)

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
GRAPH = json.dumps({"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}}})


def write_mp4(path, comment):
    """Write a tiny black video carrying ``comment`` in its container tags."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=0.2",
            "-metadata",
            f"comment={comment}",
            "-y",
            str(path),
        ],
        check=True,
        timeout=60,
    )


class TestMetadataFromTags(unittest.TestCase):

    def test_prompt_string_and_workflow_dict_become_text_chunks(self):
        tags = {"comment": json.dumps({"prompt": GRAPH, "workflow": {"nodes": []}})}
        meta = metadata_from_tags(tags)
        self.assertEqual(meta["prompt"], GRAPH)
        self.assertEqual(json.loads(meta["workflow"]), {"nodes": []})

    def test_missing_or_non_json_comment_is_empty(self):
        self.assertEqual(metadata_from_tags({}), {})
        self.assertEqual(metadata_from_tags({"comment": "just a note"}), {})
        self.assertEqual(metadata_from_tags({"comment": "[1, 2]"}), {})
        self.assertEqual(metadata_from_tags({"comment": json.dumps({"other": 1})}), {})
        self.assertEqual(metadata_from_tags({"comment": 7}), {})

    def test_description_tag_is_a_fallback(self):
        tags = {"description": json.dumps({"prompt": GRAPH})}
        self.assertEqual(metadata_from_tags(tags), {"prompt": GRAPH})


class TestIsVideoPath(unittest.TestCase):

    def test_extensions(self):
        for ext in VIDEO_EXTENSIONS:
            self.assertTrue(is_video_path(f"clip{ext.upper()}"), ext)
        self.assertFalse(is_video_path("image.png"))
        self.assertFalse(is_video_path("noext"))


class TestReadVideoMetadataWithoutFfprobe(unittest.TestCase):

    def setUp(self):
        video_metadata.ffprobe_path.cache_clear()
        self.addCleanup(video_metadata.ffprobe_path.cache_clear)

    def test_missing_ffprobe_returns_empty_without_spawning(self):
        with mock.patch.object(video_metadata.shutil, "which", return_value=None):
            with mock.patch.object(video_metadata.subprocess, "run") as run:
                self.assertEqual(read_video_metadata("clip.mp4"), {})
        run.assert_not_called()

    def test_missing_ffprobe_is_mentioned_once(self):
        video_metadata.reset_missing_notice()
        with mock.patch.object(video_metadata.shutil, "which", return_value=None):
            with self.assertLogs("prompt_manager.video", level="INFO") as cm:
                read_video_metadata("a.mp4")
                read_video_metadata("b.mp4")
        self.assertEqual(len(cm.output), 1)
        self.assertIn("ffprobe", cm.output[0])

    def _run_returning(self, **attrs):
        return mock.patch.object(
            video_metadata.subprocess,
            "run",
            return_value=mock.Mock(**{"returncode": 0, "stdout": "{}", **attrs}),
        )

    def test_ffprobe_failure_timeout_and_garbage_are_empty(self):
        with mock.patch.object(
            video_metadata.shutil, "which", return_value="/bin/ffprobe"
        ):
            with self._run_returning(returncode=1):
                self.assertEqual(read_video_metadata("clip.mp4"), {})
            with self._run_returning(stdout="not json"):
                self.assertEqual(read_video_metadata("clip.mp4"), {})
            with mock.patch.object(
                video_metadata.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("ffprobe", 1),
            ):
                self.assertEqual(read_video_metadata("clip.mp4"), {})
            with mock.patch.object(
                video_metadata.subprocess, "run", side_effect=OSError
            ):
                self.assertEqual(read_video_metadata("clip.mp4"), {})

    def test_ffprobe_output_is_parsed(self):
        payload = {"format": {"tags": {"comment": json.dumps({"prompt": GRAPH})}}}
        with mock.patch.object(
            video_metadata.shutil, "which", return_value="/bin/ffprobe"
        ):
            with self._run_returning(stdout=json.dumps(payload)) as run:
                self.assertEqual(read_video_metadata("clip.mp4"), {"prompt": GRAPH})
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/ffprobe")
        self.assertEqual(cmd[-1], "clip.mp4")
        self.assertIn("format_tags", cmd)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
class TestReadVideoMetadataWithRealFfprobe(unittest.TestCase):

    def setUp(self):
        video_metadata.ffprobe_path.cache_clear()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_round_trip_through_a_real_mp4(self):
        path = os.path.join(self.tmpdir, "clip.mp4")
        write_mp4(path, json.dumps({"prompt": GRAPH, "workflow": {"v": 1}}))

        meta = read_video_metadata(path)

        self.assertEqual(meta["prompt"], GRAPH)
        self.assertEqual(json.loads(meta["workflow"]), {"v": 1})

    def test_video_without_a_comment_is_empty(self):
        path = os.path.join(self.tmpdir, "plain.mp4")
        write_mp4(path, "")
        self.assertEqual(read_video_metadata(path), {})


if __name__ == "__main__":
    unittest.main()
