"""Tests for utils/video_metadata.py: prompt metadata from video containers.

Current ComfyUI savers (core SaveVideo/SaveWEBM, VideoHelperSuite) write one
container tag per key (``prompt``, ``workflow``, ``parameters``,
``comfyassets_prompt_pairs``); older VideoHelperSuite wrote one ``comment``
JSON envelope, with ``description`` as a fallback. One selector picks a single
carrier for the inspector and the scan. ffprobe reads the tags; when ffprobe
is missing videos are skipped without noise.
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
    DuplicateVideoTagError,
    is_video_path,
    metadata_from_tags,
    read_video_metadata,
    select_video_carrier,
)

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
GRAPH = json.dumps({"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}}})


def write_video(path, metadata_tags=True, **tags):
    """Write a tiny black video carrying one container tag per keyword.

    MP4 keeps arbitrary keys only with ``use_metadata_tags`` (mdta atoms), the
    movflag the core SaveVideo node sets; without it ffmpeg writes the legacy
    ilst atoms (``comment`` becomes ``©cmt``) that old VideoHelperSuite files
    carry. Matroska and WebM upper-case the key names.
    """
    command = [
        "ffmpeg",
        "-v",
        "quiet",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=16x16:d=0.2",
    ]
    if metadata_tags and str(path).lower().endswith((".mp4", ".mov", ".m4v")):
        command += ["-movflags", "use_metadata_tags"]
    for key, value in tags.items():
        command += ["-metadata", f"{key}={value}"]
    subprocess.run(command + ["-y", str(path)], check=True, timeout=60)


def write_mp4(path, comment):
    """Write a tiny black MP4 with ``comment`` in its legacy ilst ``©cmt`` atom."""
    write_video(path, metadata_tags=False, comment=comment)


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


class TestSelectVideoCarrier(unittest.TestCase):
    """Spec 2026-10-19 resolution parity section 8: one carrier, never merged."""

    WORKFLOW = json.dumps({"nodes": [{"id": "per-key"}]})

    def test_per_key_tags_win_over_comment(self):
        tags = {
            "prompt": GRAPH,
            "workflow": self.WORKFLOW,
            "comment": json.dumps({"prompt": json.dumps({"9": {}})}),
        }
        metadata, formats, carrier = select_video_carrier(tags)
        self.assertEqual(metadata, {"prompt": GRAPH, "workflow": self.WORKFLOW})
        self.assertEqual(
            formats, {"prompt": "original-text", "workflow": "original-text"}
        )
        self.assertIsNone(carrier)

    def test_matroska_uppercase_keys_fold(self):
        tags = {"PROMPT": GRAPH, "Workflow": self.WORKFLOW, "ENCODER": "Lavf"}
        metadata, formats, carrier = select_video_carrier(tags)
        self.assertEqual(metadata, {"prompt": GRAPH, "workflow": self.WORKFLOW})
        self.assertEqual(
            formats, {"prompt": "original-text", "workflow": "original-text"}
        )
        self.assertIsNone(carrier)
        # Envelope keys are read exactly as legacy did: no fold, so a Matroska
        # upper-cased COMMENT is not an envelope.
        self.assertIsNone(
            select_video_carrier({"COMMENT": json.dumps({"prompt": GRAPH})})
        )

    def test_folded_duplicate_keys_fail(self):
        for tags in (
            {"PROMPT": GRAPH, "prompt": json.dumps({"9": {}})},
            {"Workflow": self.WORKFLOW, "WORKFLOW": "{}", "prompt": GRAPH},
        ):
            with self.assertRaises(DuplicateVideoTagError):
                select_video_carrier(tags)
        # Byte-identical duplicates are one record, as for PNG text chunks.
        metadata, _, _ = select_video_carrier({"PROMPT": GRAPH, "prompt": GRAPH})
        self.assertEqual(metadata, {"prompt": GRAPH})
        # Envelope keys never fold, so a foreign COMMENT note is not a duplicate.
        envelope = json.dumps({"prompt": GRAPH})
        metadata, _, carrier = select_video_carrier(
            {"comment": envelope, "COMMENT": "a free-text note"}
        )
        self.assertEqual(metadata, {"prompt": GRAPH})
        self.assertEqual(carrier, envelope)

    def test_comment_envelope_still_read(self):
        envelope = json.dumps({"prompt": GRAPH, "workflow": {"nodes": []}})
        metadata, formats, carrier = select_video_carrier(
            {"comment": envelope, "encoder": "Lavf"}
        )
        self.assertEqual(metadata["prompt"], GRAPH)
        self.assertEqual(json.loads(metadata["workflow"]), {"nodes": []})
        self.assertEqual(
            formats, {"prompt": "original-text", "workflow": "reserialized-json"}
        )
        self.assertEqual(carrier, envelope)

    def test_description_fallback(self):
        envelope = json.dumps({"prompt": GRAPH})
        tags = {"comment": "not json", "description": envelope}
        self.assertEqual(
            select_video_carrier(tags),
            ({"prompt": GRAPH}, {"prompt": "original-text"}, envelope),
        )
        self.assertIsNone(select_video_carrier({"comment": "note", "encoder": "x"}))
        self.assertIsNone(select_video_carrier({"comment": "[1]"}))
        self.assertIsNone(select_video_carrier({}))
        self.assertIsNone(select_video_carrier(None))
        self.assertIsNone(select_video_carrier({"comment": 7}))

    def test_role_carrier_beats_workflow_only_carrier(self):
        envelope = json.dumps({"prompt": GRAPH, "workflow": {"nodes": ["c"]}})
        metadata, formats, carrier = select_video_carrier(
            {"workflow": self.WORKFLOW, "comment": envelope}
        )
        self.assertEqual(metadata["prompt"], GRAPH)
        self.assertEqual(json.loads(metadata["workflow"]), {"nodes": ["c"]})
        self.assertEqual(carrier, envelope)
        for role in ("parameters", "comfyassets_prompt_pairs"):
            metadata, _, carrier = select_video_carrier(
                {"comment": json.dumps({"workflow": {}}), role: "x"}
            )
            self.assertEqual(metadata, {role: "x"})
            self.assertIsNone(carrier)
        # Without a role anywhere, the first workflow-only carrier wins.
        metadata, _, carrier = select_video_carrier(
            {"workflow": self.WORKFLOW, "comment": json.dumps({"workflow": {}})}
        )
        self.assertEqual(metadata, {"workflow": self.WORKFLOW})
        self.assertIsNone(carrier)

    def test_carriers_never_merge(self):
        metadata, _, carrier = select_video_carrier(
            {
                "prompt": GRAPH,
                "comment": json.dumps({"workflow": {"nodes": ["comment"]}}),
                "description": json.dumps({"parameters": "Steps: 1"}),
            }
        )
        self.assertEqual(metadata, {"prompt": GRAPH})
        self.assertIsNone(carrier)
        metadata, _, carrier = select_video_carrier(
            {
                "workflow": self.WORKFLOW,
                "comment": json.dumps({"prompt": GRAPH}),
                "description": json.dumps({"parameters": "Steps: 1"}),
            }
        )
        self.assertEqual(metadata, {"prompt": GRAPH})
        self.assertEqual(carrier, json.dumps({"prompt": GRAPH}))

    def test_per_key_values_are_original_text_and_envelope_objects_reserialized(
        self,
    ):
        exact = '{"3":{"inputs":{"cfg":6.5000000000000000001}}}'
        metadata, formats, _ = select_video_carrier({"prompt": exact})
        self.assertEqual(metadata, {"prompt": exact})
        self.assertEqual(formats, {"prompt": "original-text"})
        envelope = json.dumps(
            {"prompt": {"3": {}}, "workflow": {"nodes": []}, "parameters": "Steps: 1"}
        )
        metadata, formats, carrier = select_video_carrier({"comment": envelope})
        self.assertEqual(json.loads(metadata["prompt"]), {"3": {}})
        self.assertEqual(json.loads(metadata["workflow"]), {"nodes": []})
        self.assertEqual(metadata["parameters"], "Steps: 1")
        self.assertEqual(
            formats,
            {
                "prompt": "reserialized-json",
                "workflow": "reserialized-json",
                "parameters": "original-text",
            },
        )
        self.assertEqual(carrier, envelope)

    def test_null_role_markers_and_invalid_workflow_keep_their_shape(self):
        # The inspector refuses a null role marker rather than falling back.
        metadata, formats, _ = select_video_carrier(
            {"comment": json.dumps({"prompt": None, "workflow": [1]})}
        )
        self.assertEqual(metadata, {"prompt": None})
        self.assertEqual(formats, {"workflow": "invalid"})
        metadata, formats, _ = select_video_carrier({"prompt": 7, "workflow": 1})
        self.assertEqual(metadata, {"prompt": None})
        self.assertEqual(formats, {"workflow": "invalid"})


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

    def test_read_video_metadata_returns_per_key_prompt(self):
        payload = {
            "format": {
                "tags": {
                    "PROMPT": GRAPH,
                    "WORKFLOW": '{"nodes":[]}',
                    "COMFYASSETS_PROMPT_PAIRS": '{"version":1}',
                    "COMMENT": json.dumps({"prompt": "must not win"}),
                    "ENCODER": "Lavf",
                }
            }
        }
        with mock.patch.object(
            video_metadata.shutil, "which", return_value="/bin/ffprobe"
        ):
            with self._run_returning(stdout=json.dumps(payload)):
                self.assertEqual(
                    read_video_metadata("clip.mkv"),
                    {
                        "prompt": GRAPH,
                        "workflow": '{"nodes":[]}',
                        "comfyassets_prompt_pairs": '{"version":1}',
                    },
                )
            duplicates = {"format": {"tags": {"PROMPT": GRAPH, "prompt": "{}"}}}
            with self._run_returning(stdout=json.dumps(duplicates)):
                self.assertEqual(read_video_metadata("clip.mkv"), {})
            markers = {"format": {"tags": {"comment": json.dumps({"prompt": None})}}}
            with self._run_returning(stdout=json.dumps(markers)):
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

    def test_per_key_tags_round_trip_through_real_containers(self):
        for name in ("per_key.mp4", "per_key.mkv", "per_key.webm"):
            path = os.path.join(self.tmpdir, name)
            write_video(path, prompt=GRAPH, workflow='{"nodes":[]}')
            self.assertEqual(
                read_video_metadata(path),
                {"prompt": GRAPH, "workflow": '{"nodes":[]}'},
                name,
            )

    def test_legacy_ilst_comment_atom_is_read(self):
        path = os.path.join(self.tmpdir, "vhs.mp4")
        write_mp4(path, json.dumps({"prompt": GRAPH}))
        probe = subprocess.run(
            [
                shutil.which("ffprobe"),
                "-v",
                "quiet",
                "-show_entries",
                "format_tags",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        tags = json.loads(probe.stdout)["format"]["tags"]
        self.assertIn("comment", tags)
        self.assertEqual(read_video_metadata(path), {"prompt": GRAPH})

    def test_video_without_a_comment_is_empty(self):
        path = os.path.join(self.tmpdir, "plain.mp4")
        write_mp4(path, "")
        self.assertEqual(read_video_metadata(path), {})


if __name__ == "__main__":
    unittest.main()
