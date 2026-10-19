"""Physical-file prompt inspection is bounded, conservative and read-only."""

import json
from unittest.mock import patch

from PIL import Image, PngImagePlugin

try:
    from test_api_images import ImageAPITestCase, make_png
    import inspection_parity as parity
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests import inspection_parity as parity

from utils.pair_execution import EXECUTION_KEY, record_pair_execution
from database.pair_ingestion import ingest_resolved_pair
from utils.prompt_pair_resolver import resolve_prompt_pair


def graph(positive="portrait", negative="blurry"):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


def png(path, **metadata):
    info = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        info.add_text(key, value if isinstance(value, str) else json.dumps(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (4, 4)) as image:
        image.save(path, pnginfo=info)
    return path


class TestPromptInspector(ImageAPITestCase):
    async def inspect(self, path="image.png", root="0"):
        params = {} if root is None else {"root": root}
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{path}", params=params
        )
        return response, await response.json()

    def side(self, text, source="graph-inferred"):
        return {"status": "resolved", "text": text, "source": source}

    async def test_unindexed_graph_pair_is_read_without_indexing_or_decoding(self):
        path = png(self.output_dir / "image.png", prompt=graph(negative=""))
        before = path.read_bytes()
        with self.api.db.model.get_connection() as connection:
            rows = connection.execute("SELECT total_changes()").fetchone()[0]
        with patch.object(Image.Image, "load", side_effect=AssertionError("decode")):
            response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            result["image"],
            {"root_index": 0, "path": "image.png", "filename": "image.png"},
        )
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(result["prompts"]["negative"], self.side(""))
        self.assertEqual(path.read_bytes(), before)
        with self.api.db.model.get_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT total_changes()").fetchone()[0], rows
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 0
            )
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_root_identity_collisions_are_not_filename_fallbacks(self):
        other = self.comfy_dir / "input"
        other.mkdir()
        first = png(self.output_dir / "image.png", prompt=graph("output"))
        png(other / "image.png", prompt=graph("input"))
        self._link_image(first, "Saved output")
        self.api._get_all_output_dirs = lambda: [self.output_dir, other]
        _, result = await self.inspect(root="1")
        self.assertEqual(result["image"]["root_index"], 1)
        self.assertEqual(result["prompts"]["positive"]["text"], "input")
        make_png(other / "plain.png")
        _, result = await self.inspect("plain.png", root="1")
        self.assertEqual(result["prompts"]["positive"]["status"], "absent")

    async def test_requires_valid_root_safe_relative_path_existing_media(self):
        make_png(self.output_dir / "image.png")
        for root in (None, "-1", "1", "x", "0.0"):
            response, result = await self.inspect(root=root)
            self.assertEqual(response.status, 400, root)
            self.assertNotIn(str(self.comfy_dir), json.dumps(result))
        for path in (
            "%2Fetc%2Fpasswd.png",
            "..%2Fsecret.png",
            "%5Csecret.png",
            "bad.txt",
            "null%00.png",
            "C:%5Csecret.png",
        ):
            response, result = await self.inspect(path)
            self.assertEqual(response.status, 403, path)
        response, result = await self.inspect("missing.png")
        self.assertEqual(response.status, 404)
        outside = png(self.comfy_dir / "outside.png", prompt=graph("private"))
        self._make_symlink(outside, self.output_dir / "escape.png")
        response, result = await self.inspect("escape.png")
        self.assertEqual(response.status, 403)
        self.assertNotIn("private", json.dumps(result))

    async def test_missing_negative_is_distinct_from_explicit_empty(self):
        source = graph()
        del source["3"]["inputs"]["negative"]
        png(self.output_dir / "image.png", prompt=source)
        _, result = await self.inspect()
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(result["prompts"]["negative"]["status"], "absent")
        self.assertIsNone(result["prompts"]["negative"]["text"])

    async def test_graph_ambiguity_and_unknown_semantics_do_not_use_saved_fallback(
        self,
    ):
        path = self.output_dir / "image.png"
        self._link_image(png(path), "Legacy saved")
        for expected in ("ambiguous", "unresolved"):
            source = graph()
            if expected == "ambiguous":
                source["4"] = source["3"]
            else:
                source["1"]["class_type"] = "UnknownEncoder"
            png(
                path,
                prompt=source,
                parameters=(
                    "Fallback\nNegative prompt: noise\n" "Steps: 20, Sampler: Euler"
                ),
            )
            _, result = await self.inspect()
            for side in result["prompts"].values():
                self.assertEqual(side["status"], expected)
                self.assertIsNone(side["text"])

    async def test_parameters_and_partial_resolution_are_conservative(self):
        path = self.output_dir / "image.png"
        png(path, parameters="A mountain\nNegative prompt: \nSteps: 20, Sampler: Euler")
        _, result = await self.inspect()
        self.assertEqual(
            result["prompts"]["positive"],
            self.side("A mountain", "embedded-parameters"),
        )
        self.assertEqual(
            result["prompts"]["negative"], self.side("", "embedded-parameters")
        )
        source = graph()
        source["2"]["class_type"] = "UnknownEncoder"
        png(path, prompt=source)
        _, result = await self.inspect()
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(result["prompts"]["negative"]["status"], "unresolved")

    async def test_execution_evidence_wins_over_graph_and_saved_recipe(self):
        source = {
            "1": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": "portrait", "negative_text": "blurry"},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["1", 1]},
            },
        }
        evidence = {}
        record_pair_execution(
            evidence,
            "1",
            text="portrait",
            negative_text="blurry",
            positive_text="portrait LoRA trigger",
            negative_effective_text="blurry",
        )
        path = png(self.output_dir / "image.png", prompt=source, **evidence)
        self._link_image(path, "Saved recipe")
        _, result = await self.inspect()
        self.assertEqual(
            result["prompts"]["positive"],
            self.side("portrait LoRA trigger", "execution"),
        )
        self.assertEqual(
            result["prompts"]["negative"], self.side("blurry", "execution")
        )
        png(path, prompt=source, **{EXECUTION_KEY: "broken"})
        _, result = await self.inspect()
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")

    async def test_saved_associations_remain_unverified_without_file_evidence(self):
        path = make_png(self.output_dir / "image.png")
        self._link_image(path, "Saved positive")
        _, result = await self.inspect()
        self.assertEqual(
            result["prompts"]["positive"],
            {
                "status": "unresolved",
                "text": None,
                "source": "saved-association",
                "message": (
                    "A saved library association exists, but it cannot be "
                    "verified against this file."
                ),
            },
        )
        self.assertEqual(result["prompts"]["negative"]["status"], "absent")
        other = make_png(self.output_dir / "paired.png")
        metadata = {"prompt": graph("Effective positive", "Recorded negative")}
        resolution = resolve_prompt_pair(metadata)
        ingest_resolved_pair(self.api.db, str(other), metadata, resolution)
        _, result = await self.inspect("paired.png")
        self.assertEqual(
            result["prompts"]["positive"],
            {
                "status": "unresolved",
                "text": None,
                "source": "saved-association",
                "message": (
                    "A saved library association exists, but it cannot be "
                    "verified against this file."
                ),
            },
        )
        self.assertEqual(
            result["prompts"]["negative"],
            {
                "status": "unresolved",
                "text": None,
                "source": "saved-association",
                "message": (
                    "A saved library association exists, but it cannot be "
                    "verified against this file."
                ),
            },
        )

    async def test_corrupt_malformed_and_no_metadata_do_not_invent_text(self):
        path = self.output_dir / "image.png"
        for content, expected in (
            (b"broken", "unresolved"),
            (None, "absent"),
            ("bad-json", "unresolved"),
        ):
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                png(path, **({"prompt": content} if content else {}))
            _, result = await self.inspect()
            self.assertEqual(result["prompts"]["positive"]["status"], expected)
            self.assertIsNone(result["prompts"]["positive"]["text"])

    async def test_compressed_international_and_post_pixel_metadata(self):
        import struct
        import zlib

        path = self.output_dir / "image.png"
        for kind in (
            "compressed",
            "international",
            "compressed-international",
            "post-idat",
        ):
            info = PngImagePlugin.PngInfo()
            value = json.dumps(graph("猫"), ensure_ascii=False)
            if kind == "compressed":
                info.add_text("prompt", json.dumps(graph()), zip=True)
            elif kind in ("international", "compressed-international"):
                info.add_itxt(
                    "prompt",
                    value,
                    lang="en",
                    tkey="Prompt",
                    zip=kind.startswith("compressed"),
                )
            else:
                payload = b"prompt\0" + json.dumps(graph()).encode()
                info.add_text("unrelated", "header text")
            with Image.new("RGB", (4, 4)) as image:
                image.save(path, pnginfo=info)
            if kind == "post-idat":
                content = path.read_bytes()
                chunk = (
                    struct.pack(">I", len(payload))
                    + b"tEXt"
                    + payload
                    + struct.pack(">I", zlib.crc32(b"tEXt" + payload))
                )
                path.write_bytes(content[:-12] + chunk + content[-12:])
            with patch.object(
                Image.Image, "load", side_effect=AssertionError("pixels")
            ):
                _, result = await self.inspect()
            self.assertEqual(
                result["prompts"]["positive"]["text"],
                "猫" if "international" in kind else "portrait",
            )

    async def test_corrupt_truncated_duplicate_and_expanding_metadata_fail_closed(self):
        import struct
        import zlib
        from utils.prompt_inspector import MAX_METADATA_BYTES

        path = self.output_dir / "image.png"
        base = png(path, prompt=graph()).read_bytes()
        cases = [base[:-1], base[:30]]
        index = base.index(b"tEXt")
        broken = bytearray(base)
        # Corrupt the retained record's body: its CRC is checked. (A corrupt
        # keyword names an unretained chunk, which is skipped unchecked.)
        broken[index + 4 + len(b"prompt\0") + 1] ^= 1
        cases.append(bytes(broken))
        for kind, payload in (
            (b"tEXt", b"prompt\0{}"),
            (b"zTXt", b"prompt\0\0" + zlib.compress(b"x" * (MAX_METADATA_BYTES + 1))),
            (b"tEXt", b"prompt\0" + b"x" * (MAX_METADATA_BYTES + 1)),
            (b"tEXt", b"bad-key-without-separator"),
            (b"zTXt", b"prompt\0\1bad-compression"),
            (b"iTXt", b"prompt\0\2\0\0\0text"),
            (b"iTXt", b"prompt\0\1\1\0\0text"),
            (b"iTXt", b"prompt\0\0\0\0\0\xff"),
        ):
            chunk = (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload))
            )
            cases.append(base[:-12] + chunk + base[-12:])
        for index, content in enumerate(cases):
            path.write_bytes(content)
            _, result = await self.inspect()
            self.assertEqual(
                result["prompts"]["positive"]["status"], "unresolved", index
            )
            self.assertIsNone(result["prompts"]["positive"]["text"])

    async def test_jpeg_header_only_no_metadata_and_read_failure_are_safe(self):
        path = self.output_dir / "image.jpg"
        with Image.new("RGB", (4, 4)) as image:
            image.save(path)
        with patch.object(Image.Image, "load", side_effect=AssertionError("pixels")):
            response, result = await self.inspect("image.jpg")
        self.assertEqual(response.status, 200)
        self.assertEqual(result["prompts"]["positive"]["status"], "absent")
        with patch(
            "py.api.inspection_service.read_inspection_file",
            side_effect=RuntimeError(str(self.comfy_dir)),
        ):
            response, result = await self.inspect("image.jpg")
        self.assertEqual(response.status, 500)
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_invalid_json_depth_and_saved_filename_collision_do_not_escape(self):
        path = png(self.output_dir / "image.png", prompt="[" * 2000)
        _, result = await self.inspect()
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
        self._link_image(path, "Saved first")
        other = self.comfy_dir / "input"
        other.mkdir()
        make_png(other / "image.png")
        self.api._get_all_output_dirs = lambda: [self.output_dir, other]
        with patch.object(
            self.api.db,
            "get_image_prompt_info",
            wraps=self.api.db.get_image_prompt_info,
        ) as lookup:
            _, result = await self.inspect(root="1")
        lookup.assert_called_once_with(
            str(other / "image.png"), False, report_ambiguity=True
        )
        self.assertEqual(result["prompts"]["positive"]["status"], "absent")

    async def test_video_inspection_uses_bounded_container_evidence(self):
        import io
        from unittest.mock import MagicMock
        from utils.prompt_inspector import MAX_METADATA_BYTES

        (self.output_dir / "clip.mp4").write_bytes(b"video fixture")
        metadata = {"prompt": graph("Video portrait", "")}
        payload = json.dumps(
            {"format": {"tags": {"comment": json.dumps(metadata)}}}
        ).encode()
        process = MagicMock()
        process.stdout = io.BytesIO(payload)
        process.poll.return_value = 0
        process.returncode = 0
        with (
            patch("utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"),
            patch(
                "utils.prompt_inspector.subprocess.Popen", return_value=process
            ) as spawn,
        ):
            response, result = await self.inspect("clip.mp4")
        self.assertEqual(response.status, 200)
        self.assertEqual(result["prompts"]["positive"], self.side("Video portrait"))
        self.assertEqual(result["prompts"]["negative"], self.side(""))
        self.assertTrue(process.stdout.closed)
        process.wait.assert_called()
        self.assertNotIn("shell", spawn.call_args.kwargs)
        self.assertIn(str(self.output_dir / "clip.mp4"), spawn.call_args.args[0])
        process.stdout = io.BytesIO(b"x" * (MAX_METADATA_BYTES + 1))
        with (
            patch("utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"),
            patch("utils.prompt_inspector.subprocess.Popen", return_value=process),
        ):
            _, result = await self.inspect("clip.mp4")
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
        self.assertTrue(process.stdout.closed)

    async def test_video_missing_probe_and_timeout_preserve_uncertainty(self):
        import io
        from unittest.mock import MagicMock

        (self.output_dir / "clip.webm").write_bytes(b"fixture")
        with patch("utils.prompt_inspector.ffprobe_path", return_value=None):
            _, result = await self.inspect("clip.webm")
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
        process = MagicMock()
        process.stdout = io.BytesIO(b"")
        process.poll.return_value = None
        process.returncode = -9
        with (
            patch("utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"),
            patch("utils.prompt_inspector.subprocess.Popen", return_value=process),
        ):
            _, result = await self.inspect("clip.webm")
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
        process.kill.assert_called_once()
        process.wait.assert_called()
        self.assertTrue(process.stdout.closed)

    async def test_video_description_execution_evidence_and_invalid_envelope(self):
        import io
        from unittest.mock import MagicMock

        path = self.output_dir / "clip.mp4"
        path.write_bytes(b"fixture")
        source = {
            "1": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": "portrait", "negative_text": "bad"},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["1", 1]},
            },
        }
        evidence = {}
        record_pair_execution(
            evidence,
            "1",
            text="portrait",
            negative_text="bad",
            positive_text="portrait trigger",
            negative_effective_text="bad",
        )
        for value in (
            {
                "format": {
                    "tags": {
                        "comment": "not-json",
                        "description": json.dumps({"prompt": source, **evidence}),
                    }
                }
            },
            [],
            {"format": {"tags": []}},
            {"format": {"tags": {}}},
            {
                "format": {
                    "tags": {
                        "comment": json.dumps(
                            {"prompt": source, EXECUTION_KEY: "bad-json"}
                        )
                    }
                }
            },
        ):
            process = MagicMock()
            process.stdout = io.BytesIO(json.dumps(value).encode())
            process.poll.return_value = 0
            process.returncode = 0
            with (
                patch(
                    "utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"
                ),
                patch("utils.prompt_inspector.subprocess.Popen", return_value=process),
            ):
                _, result = await self.inspect("clip.mp4")
            positive = result["prompts"]["positive"]
            if (
                isinstance(value, list)
                or not isinstance(value["format"]["tags"], dict)
                or "bad-json" in json.dumps(value)
            ):
                self.assertEqual(positive["status"], "unresolved")
            elif not value["format"]["tags"]:
                self.assertEqual(positive["status"], "absent")
            else:
                self.assertEqual(positive, self.side("portrait trigger", "execution"))

    async def test_large_sparse_pixels_are_skipped_and_chunk_count_is_bounded(self):
        import struct
        import zlib
        from utils.prompt_inspector import read_inspection_metadata, MetadataReadError

        path = self.output_dir / "large.png"
        header = struct.pack(">IIBBBBB", 1024, 1024, 8, 2, 0, 0, 0)

        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data))
            )

        with path.open("wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header))
            stream.write(struct.pack(">I4s", 64 * 1024 * 1024, b"IDAT"))
            stream.seek(64 * 1024 * 1024, 1)
            stream.write(b"\0\0\0\0")
            stream.write(chunk(b"tEXt", b"prompt\0" + json.dumps(graph()).encode()))
            stream.write(chunk(b"IEND", b""))
        original_open = type(path).open
        reads = []

        def tracked_open(item, *args, **kwargs):
            stream = original_open(item, *args, **kwargs)
            original_read = stream.read

            def read(size=-1):
                value = original_read(size)
                reads.append(len(value))
                return value

            stream.read = read
            return stream

        with patch.object(type(path), "open", tracked_open):
            metadata = read_inspection_metadata(path)
        self.assertEqual(json.loads(metadata["prompt"]), graph())
        self.assertLess(sum(reads), 4096)
        with patch("utils.prompt_inspector.MAX_PNG_CHUNKS", 1):
            with self.assertRaises(MetadataReadError):
                read_inspection_metadata(path)

    async def test_large_jpeg_uses_bounded_header_without_rejecting_valid_file(self):
        from utils.prompt_inspector import MAX_HEADER_BYTES

        path = self.output_dir / "large.jpg"
        with Image.new("RGB", (4, 4)) as image:
            image.save(path)
        with path.open("ab") as stream:
            stream.write(b"x" * (MAX_HEADER_BYTES + 1))
        self._link_image(path, "Saved large JPEG")
        with patch.object(Image.Image, "load", side_effect=AssertionError("pixels")):
            _, result = await self.inspect("large.jpg")
        self.assertEqual(
            result["prompts"]["positive"],
            {
                "status": "unresolved",
                "text": None,
                "source": "saved-association",
                "message": (
                    "A saved library association exists, but it cannot be "
                    "verified against this file."
                ),
            },
        )

    async def test_invalid_or_execution_only_video_evidence_never_uses_saved_fallback(
        self,
    ):
        import io
        from unittest.mock import MagicMock

        path = self.output_dir / "clip.mp4"
        path.write_bytes(b"fixture")
        self._link_image(path, "Saved recipe must not replace unknown evidence")
        for envelope in (
            {"prompt": None},
            {"parameters": None},
            {EXECUTION_KEY: {"version": 1, "nodes": {}}},
        ):
            process = MagicMock()
            process.stdout = io.BytesIO(
                json.dumps(
                    {
                        "format": {
                            "tags": {
                                "comment": json.dumps(envelope),
                                "description": json.dumps(
                                    {"prompt": graph("Other comment")}
                                ),
                            }
                        }
                    }
                ).encode()
            )
            process.poll.return_value = 0
            process.returncode = 0
            with (
                patch(
                    "utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"
                ),
                patch("utils.prompt_inspector.subprocess.Popen", return_value=process),
            ):
                _, result = await self.inspect("clip.mp4")
            self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
            self.assertIsNone(result["prompts"]["positive"]["text"])

    async def test_same_path_same_size_replacement_does_not_resolve_stale_saved_prompts(
        self,
    ):
        import io

        metadata = {"prompt": graph("Original portrait", "Original negative")}
        path = png(self.output_dir / "image.png", **metadata)
        ingest_resolved_pair(
            self.api.db, str(path), metadata, resolve_prompt_pair(metadata)
        )
        _, original = await self.inspect()
        self.assertEqual(
            original["prompts"]["positive"], self.side("Original portrait")
        )
        size = path.stat().st_size
        replacement = io.BytesIO()
        with Image.new("RGB", (4, 4), (0, 0, 255)) as image:
            image.save(replacement, "PNG")
        content = replacement.getvalue()
        self.assertLessEqual(len(content), size)
        path.write_bytes(content.ljust(size, b"\0"))
        self.assertEqual(path.stat().st_size, size)
        _, replaced = await self.inspect()
        for record in replaced["prompts"].values():
            self.assertEqual(record["status"], "unresolved")
            self.assertIsNone(record["text"])
            self.assertEqual(record["source"], "saved-association")
            self.assertIn("cannot be verified", record["message"])
        self.assertNotIn("Original portrait", json.dumps(replaced))

    async def test_full_inspection_envelope_preserves_existing_roles_and_identity(self):
        from pathlib import Path

        source = graph(negative="")
        source["3"]["inputs"].update(seed=9007199254740993, cfg=6.5, steps=28)
        raw = json.dumps(source)
        workflow = '{"nodes":[],"seed":9007199254740993}'
        path = png(self.output_dir / "image.png", prompt=raw, workflow=workflow)
        self._link_image(path, "Saved text must not replace file roles")
        original_open = Path.open
        reads = []

        def opened(candidate, *args, **kwargs):
            if candidate == path:
                reads.append(candidate)
            return original_open(candidate, *args, **kwargs)

        with (
            patch.object(Path, "open", opened),
            patch.object(Image.Image, "load", side_effect=AssertionError("pixels")),
            patch.object(
                self.api.db,
                "get_image_prompt_info",
                wraps=self.api.db.get_image_prompt_info,
            ) as lookup,
        ):
            response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(result.get("inspection_version"), 1)
        self.assertEqual(
            result["image"],
            {
                "root_index": 0,
                "path": "image.png",
                "filename": "image.png",
            },
        )
        self.assertEqual(
            result["prompts"],
            {
                "positive": self.side("portrait"),
                "negative": self.side(""),
            },
        )
        self.assertRegex(result["root_scope"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            result["metadata"]["raw"], {"prompt": raw, "workflow": workflow}
        )
        self.assertEqual(
            result["metadata"]["record_formats"],
            {
                "prompt": "original-text",
                "workflow": "original-text",
            },
        )
        self.assertEqual(
            result["metadata"]["prompt_graph"]["value"]["3"]["inputs"]["seed"],
            "9007199254740993",
        )
        self.assertEqual(result["file"]["format"], "PNG")
        self.assertEqual((result["file"]["width"], result["file"]["height"]), (4, 4))
        self.assertEqual(result["file"]["observation"], "current-file")
        self.assertEqual(result["resolution"]["sampler_id"], "3")
        self.assertEqual(result["library"]["status"], "linked")
        self.assertEqual(reads, [path])
        lookup.assert_called_once_with(str(path), False, report_ambiguity=True)
        self.assertLessEqual(len(await response.read()), 8 * 1024 * 1024)
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_workflow_has_its_own_budget_and_never_hides_prompts(self):
        from utils.prompt_inspector import ROLE_METADATA_BYTES, WORKFLOW_METADATA_BYTES

        path = self.output_dir / "image.png"
        # Over the role budget, within the workflow budget: fully readable.
        workflow = "x" * ROLE_METADATA_BYTES
        png(path, prompt=json.dumps(graph()), workflow=workflow)
        response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["metadata"]["status"], "available")
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(result["metadata"]["raw"]["workflow"], workflow)
        self.assertEqual(result["metadata"]["workflow_graph"]["status"], "malformed")
        # Over the workflow budget: only the workflow is limited.
        png(
            path,
            prompt=json.dumps(graph()),
            workflow="x" * (WORKFLOW_METADATA_BYTES + 1),
        )
        response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["metadata"]["status"], "available")
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(result["prompts"]["negative"], self.side("blurry"))
        self.assertEqual(
            result["metadata"]["record_formats"],
            {"prompt": "original-text", "workflow": "limited"},
        )
        self.assertEqual(set(result["metadata"]["raw"]), {"prompt"})
        self.assertEqual(
            result["metadata"]["workflow_graph"], {"status": "limited", "value": None}
        )
        self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")

    def parity_fixture(self, name):
        path = self.output_dir / f"{name}.png"
        parity.write_png(path, parity.build(name))
        return path

    async def test_inspect_route_resolves_506_node_graph(self):
        # f03: 506 executable nodes. The default (ingestion) profile's 500-node
        # gate reports "Resolution limit reached"; the inspection profile bounds
        # work instead of graph size, so the route resolves the pair.
        self.parity_fixture("f03")
        response, result = await self.inspect("f03.png")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            result["prompts"],
            {"positive": self.side(parity.POS), "negative": self.side(parity.NEG)},
        )
        self.assertEqual(result["resolution"]["state"], "resolved")
        self.assertIsNone(result["resolution"]["reason_code"])
        self.assertIsNone(result["resolution"]["node"])
        self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")

    async def test_inspect_route_reports_no_sampler_instead_of_absent(self):
        # f17: a BasicGuider-only prompt graph is a record, never "not recorded".
        self.parity_fixture("f17")
        response, result = await self.inspect("f17.png")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            {role: side["status"] for role, side in result["prompts"].items()},
            {"positive": "unresolved", "negative": "unresolved"},
        )
        self.assertEqual(result["resolution"]["state"], "unresolved")
        self.assertEqual(result["resolution"]["reason_code"], "no-sampler")
        self.assertEqual(
            result["resolution"]["reason"],
            "No supported sampler pair found (saw SamplerCustomAdvanced)",
        )

    async def test_inspect_route_exposes_reason_code_and_blocking_node(self):
        self.parity_fixture("f06a")
        _, result = await self.inspect("f06a.png")
        resolution = result["resolution"]
        self.assertEqual(resolution["state"], "unresolved")
        self.assertEqual(resolution["reason_code"], "unsupported-node")
        self.assertEqual(set(resolution["node"]), {"id", "class_type"})
        self.assertIsInstance(resolution["node"]["class_type"], str)
        png(self.output_dir / "image.png", prompt="{broken")
        _, result = await self.inspect()
        self.assertEqual(result["resolution"]["reason_code"], "malformed")
        make_png(self.output_dir / "image.png")
        _, result = await self.inspect()
        self.assertEqual(result["resolution"]["state"], "absent")
        self.assertIsNone(result["resolution"]["reason_code"])
        self.assertIsNone(result["resolution"]["node"])

    async def test_workflow_only_keeps_existing_role_uncertainty(self):
        path = png(self.output_dir / "image.png", workflow={"nodes": []})
        self._link_image(path, "Saved uncertainty")
        _, result = await self.inspect()
        self.assertEqual(result["prompts"]["positive"]["source"], "saved-association")
        self.assertEqual(
            result.get("metadata", {}).get("workflow_graph", {}).get("status"), "parsed"
        )
        self.assertIsNone(result["prompts"]["positive"]["text"])
        self.assertEqual(result["prompts"]["negative"]["status"], "absent")

    async def test_library_failure_does_not_erase_file_evidence(self):
        png(self.output_dir / "image.png", prompt=graph("File portrait"))
        with patch.object(
            self.api.db,
            "get_image_prompt_info",
            side_effect=OSError("private DB detail"),
        ):
            response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["prompts"]["positive"], self.side("File portrait"))
        self.assertEqual(result.get("library", {}).get("status"), "unavailable")
        self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")
        self.assertNotIn("private DB detail", json.dumps(result))

    async def test_file_change_is_retryable_without_saved_text_fallback(self):
        from pathlib import Path

        path = png(self.output_dir / "image.png", prompt=graph())
        self._link_image(path, "Saved recipe")
        original_open = Path.open

        def opened(candidate, *args, **kwargs):
            stream = original_open(candidate, *args, **kwargs)
            if candidate == path and args and args[0] == "rb":
                with original_open(path, "ab") as output:
                    output.write(b"changed")
            return stream

        with patch.object(Path, "open", opened):
            response, result = await self.inspect()
        self.assertEqual(response.status, 409)
        self.assertFalse(result["success"])
        self.assertNotIn("code", result)
        self.assertNotIn("Saved recipe", json.dumps(result))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    async def test_surrogate_and_ordinary_unicode_round_trip_without_replacement(self):
        source = graph("猫 😀 \ud800", "\udfff")
        raw = json.dumps(source)
        png(
            self.output_dir / "image.png",
            prompt=raw,
            workflow='{"nodes":[],"label":"\\ud800 猫 😀"}',
        )
        response, result = await self.inspect()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["prompts"]["positive"], self.side("猫 😀 \ud800"))
        self.assertEqual(result["prompts"]["negative"], self.side("\udfff"))
        self.assertEqual(result["metadata"]["raw"]["prompt"], raw)
        body = await response.read()
        self.assertIn("猫 😀".encode(), body)
        self.assertIn(b"\\ud800", body)
        self.assertNotIn("\ufffd".encode(), body)
        self.assertLessEqual(len(body), 8 * 1024 * 1024)

    async def video_inspect(self, comment=None, description=None):
        import io
        import threading
        from unittest.mock import MagicMock
        from utils.prompt_inspector import MAX_METADATA_BYTES

        path = self.output_dir / "clip.mp4"
        path.write_bytes(b"fixture")
        tags = {
            key: value
            for key, value in (("comment", comment), ("description", description))
            if value is not None
        }
        payload = json.dumps({"format": {"tags": tags}}).encode()

        class ProbeOutput(io.BytesIO):
            def read(self, size=-1):
                self.requested_size = size
                return super().read(size)

        process = MagicMock()
        process.stdout = ProbeOutput(payload)
        process.poll.return_value = 0
        process.returncode = 0
        timers = []
        timer_type = threading.Timer

        def timer(*args):
            instance = timer_type(*args)
            timers.append(instance)
            return instance

        with (
            patch("utils.prompt_inspector.ffprobe_path", return_value="/tool/ffprobe"),
            patch(
                "utils.prompt_inspector.subprocess.Popen", return_value=process
            ) as spawn,
            patch("utils.prompt_inspector.threading.Timer", side_effect=timer),
        ):
            response, result = await self.inspect("clip.mp4")
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(process.stdout.closed)
        self.assertEqual(process.stdout.requested_size, MAX_METADATA_BYTES + 1)
        self.assertEqual(len(timers), 1)
        self.assertTrue(timers[0].finished.is_set())
        return response, result

    async def test_video_role_description_wins_over_workflow_only_comment(self):
        evidence = {}
        source = graph("chosen", "")
        # CLIP execution is unnecessary here: use the established pair-node carrier.
        source["1"] = {
            "class_type": "PromptManagerPair",
            "inputs": {"text": "chosen", "negative_text": ""},
        }
        source["3"]["inputs"]["negative"] = ["1", 1]
        record_pair_execution(
            evidence,
            "1",
            text="chosen",
            negative_text="",
            positive_text="chosen trigger",
            negative_effective_text="",
        )
        _, result = await self.video_inspect(
            json.dumps({"workflow": {"nodes": [{"id": "ignored"}]}}),
            json.dumps(
                {
                    "prompt": source,
                    "workflow": {"nodes": [{"id": "chosen"}]},
                    **evidence,
                }
            ),
        )
        self.assertEqual(
            result["prompts"]["positive"], self.side("chosen trigger", "execution")
        )
        self.assertEqual(result["prompts"]["negative"], self.side("", "execution"))
        self.assertEqual(
            result.get("metadata", {}).get("workflow_graph", {}).get("value"),
            {"nodes": [{"id": "chosen"}]},
        )
        self.assertNotIn("ignored", json.dumps(result))

    async def test_video_present_malformed_role_markers_block_other_carriers(self):
        for marker in (
            {"prompt": None},
            {"prompt": "not-json"},
            {"parameters": None},
            {EXECUTION_KEY: "malformed"},
        ):
            for comment, description in (
                (
                    json.dumps({"workflow": {"nodes": [{"id": "ignored"}]}}),
                    json.dumps(marker),
                ),
                (json.dumps(marker), json.dumps({"prompt": graph("must not win")})),
            ):
                _, result = await self.video_inspect(comment, description)
                self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
                self.assertIsNone(result["prompts"]["positive"]["text"])
                self.assertEqual(
                    result.get("metadata", {}).get("workflow_graph", {}).get("status"),
                    "absent",
                )
                self.assertNotIn("must not win", json.dumps(result))

    async def test_video_workflow_carrier_fallback_never_merges_tags(self):
        path = self.output_dir / "clip.mp4"
        path.write_bytes(b"fixture")
        self._link_image(path, "Saved workflow association")
        _, result = await self.video_inspect(
            '{"workflow":{"nodes":[{"id":"first"}]}}',
            '{"workflow":{"nodes":[{"id":"second"}]},"unrelated":"unselected"}',
        )
        self.assertEqual(result["prompts"]["positive"]["source"], "saved-association")
        self.assertEqual(
            result.get("metadata", {}).get("workflow_graph", {}).get("value"),
            {"nodes": [{"id": "first"}]},
        )
        self.assertEqual(
            result["metadata"]["raw"], {"workflow": '{"nodes": [{"id": "first"}]}'}
        )
        self.assertNotIn("second", json.dumps(result))
        self.assertNotIn("unselected", json.dumps(result))

    async def test_video_object_graph_decimal_and_unsafe_int_projection_is_exact(self):
        raw = json.dumps(graph("video", ""))
        raw = raw.replace(
            '"positive": ["1", 0]',
            '"seed":9007199254740993,"cfg":6.5000000000000000001,"positive": ["1", 0]',
        )
        for prompt in (raw, json.dumps(raw)):
            carrier = (
                '{"prompt":'
                + prompt
                + ',"workflow":{"nodes":[],"cfg":6.5000000000000000001,'
                '"seed":9007199254740993}}'
            )
            _, result = await self.video_inspect(carrier)
            self.assertEqual(result["prompts"]["positive"], self.side("video"))
            inputs = (
                result.get("metadata", {})
                .get("prompt_graph", {})
                .get("value", {})
                .get("3", {})
                .get("inputs", {})
            )
            self.assertEqual(inputs.get("cfg"), "6.5000000000000000001")
            self.assertEqual(inputs["seed"], "9007199254740993")
            self.assertEqual(inputs["positive"], ["1", 0])
            self.assertEqual(
                result["metadata"]["workflow_graph"]["value"]["cfg"],
                "6.5000000000000000001",
            )
            self.assertNotIn("video_carrier_text", result)
            self.assertNotIn("carrier_text", json.dumps(result))

    async def test_video_original_string_and_reserialized_object_formats_are_distinct(
        self,
    ):
        raw = json.dumps(graph())
        for record, expected in (
            (json.dumps(raw), "original-text"),
            (raw, "reserialized-json"),
        ):
            _, result = await self.video_inspect(
                '{"prompt":' + record + ',"workflow":' + record + "}"
            )
            self.assertEqual(
                result.get("metadata", {}).get("record_formats"),
                {"prompt": expected, "workflow": expected},
            )
            self.assertEqual(result["metadata"]["raw"]["prompt"], raw)

    async def test_video_null_role_marker_raises_before_successful_read(self):
        for key in ("prompt", "parameters", EXECUTION_KEY):
            _, result = await self.video_inspect(json.dumps({key: None}))
            self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
            self.assertEqual(result.get("metadata", {}).get("status"), "unreadable")

    async def test_video_null_workflow_keeps_valid_legacy_roles(self):
        _, result = await self.video_inspect(
            json.dumps({"prompt": graph(), "workflow": None})
        )
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(
            result.get("metadata", {}).get("workflow_graph"),
            {"status": "malformed", "value": None},
        )
        self.assertEqual(result["metadata"]["record_formats"]["workflow"], "invalid")
        self.assertNotIn("workflow", result["metadata"]["raw"])

    async def test_video_strict_projection_failure_preserves_legacy_roles(self):
        source = json.dumps(graph())
        for suffix, expected in (
            (',"duplicate":1,"duplicate":2', "malformed"),
            (',"depth":' + "[" * 64 + "0" + "]" * 64, "limited"),
        ):
            _, result = await self.video_inspect(
                '{"prompt":' + source + ',"workflow":{"nodes":[]}' + suffix + "}"
            )
            self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
            self.assertEqual(
                result.get("metadata", {}).get("prompt_graph", {}).get("status"),
                expected,
            )
            self.assertIsNone(result["metadata"]["prompt_graph"]["value"])
            self.assertEqual(result["metadata"]["workflow_graph"]["status"], expected)

    async def test_video_carrier_nonfinite_outside_graph_inputs_is_tolerated(self):
        # ComfyUI quirks: NaN in an unrelated carrier field, in ``is_changed`` and
        # in ``_meta`` are validated and dropped; they never reach the projection.
        source = graph()
        source["3"]["is_changed"] = ["NAN_MARKER"]
        source["3"]["_meta"] = {"title": "K", "weight": "NAN_MARKER"}
        text = json.dumps(source).replace('"NAN_MARKER"', "NaN")
        _, result = await self.video_inspect(
            '{"prompt":' + text + ',"workflow":{"nodes":[]},"nonfinite":NaN}'
        )
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        prompt_graph = result["metadata"]["prompt_graph"]
        self.assertEqual(prompt_graph["status"], "parsed")
        self.assertEqual(set(prompt_graph["value"]["3"]), {"class_type", "inputs"})
        self.assertEqual(
            result["metadata"]["workflow_graph"],
            {"status": "parsed", "value": {"nodes": []}},
        )

    async def test_video_numeric_role_text_never_becomes_sendable(self):
        source = graph(positive=123)
        _, result = await self.video_inspect(json.dumps({"prompt": source}))
        self.assertEqual(result["prompts"]["positive"]["status"], "unresolved")
        self.assertIsNone(result["prompts"]["positive"]["text"])
        self.assertEqual(
            result.get("metadata", {}).get("prompt_graph", {}).get("status"), "parsed"
        )

    async def scoped_descriptor(self, root_index=0):
        response = await self.client.get("/prompt_manager/images/output?mode=workspace")
        entries = (await response.json())["images"]
        return next(entry for entry in entries if entry["root_index"] == root_index)

    async def assert_scope_skips_access(self, scope, root="0", status=409):
        from pathlib import Path

        original_stat = Path.stat

        def stat(candidate, *args, **kwargs):
            if candidate.name == "image.png":
                raise AssertionError("candidate stat before scope guard")
            return original_stat(candidate, *args, **kwargs)

        params = {"root_scope": scope}
        if root is not None:
            params["root"] = root
        with (
            patch.object(Path, "stat", stat),
            patch(
                "utils.prompt_inspector.subprocess.Popen",
                side_effect=AssertionError("probe"),
            ),
            patch.object(
                self.api.db,
                "get_image_prompt_info",
                side_effect=AssertionError("saved lookup"),
            ),
            patch.object(
                self.api, "_file_response", side_effect=AssertionError("file response")
            ),
            patch(
                "py.api.inspection_service.read_inspection_file",
                side_effect=AssertionError("metadata read"),
            ),
        ):
            response = await self.client.get(
                "/prompt_manager/images/inspect/image.png", params=params
            )
        self.assertEqual(response.status, status)
        result = await response.json()
        self.assertFalse(result["success"])
        if status == 409:
            self.assertEqual(result["code"], "source-changed")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertNotIn(str(self.comfy_dir), json.dumps(result))

    async def test_root_scope_equal_label_reorder_or_replacement_never_reads_other_file(
        self,
    ):
        from py.config import GalleryConfig

        roots = [self.comfy_dir / name / "output" for name in ("a", "b", "c")]
        for index, root in enumerate(roots):
            png(root / "image.png", prompt=graph(str(index)))
        with patch.object(
            GalleryConfig,
            "path_anchors",
            classmethod(lambda cls: [str(root.parent) for root in roots]),
        ):
            self.api._get_all_output_dirs = lambda: roots[:2]
            descriptor = await self.scoped_descriptor()
            other = await self.scoped_descriptor(1)
            self.assertEqual(descriptor["root_dir"], other["root_dir"])
            for changed in ([roots[1], roots[0]], [roots[2], roots[1]]):
                self.api._get_all_output_dirs = lambda: changed
                await self.assert_scope_skips_access(
                    descriptor.get("root_scope", "0" * 64)
                )

    async def test_root_scope_symlink_retarget_requires_refresh(self):
        first, second = self.comfy_dir / "a", self.comfy_dir / "b"
        png(first / "image.png", prompt=graph("first"))
        png(second / "image.png", prompt=graph("second"))
        link = self.comfy_dir / "linked"
        self._make_symlink(first, link)
        self.api._get_all_output_dirs = lambda: [link]
        descriptor = await self.scoped_descriptor()
        link.unlink()
        self._make_symlink(second, link)
        await self.assert_scope_skips_access(descriptor.get("root_scope", "0" * 64))

    async def test_root_scope_rotated_key_and_removed_index_skip_reader_and_db(self):
        png(self.output_dir / "image.png", prompt=graph())
        descriptor = await self.scoped_descriptor()
        import py.api.images as images_module

        with patch.object(images_module, "_WORKSPACE_CURSOR_SECRET", b"new-key"):
            await self.assert_scope_skips_access(descriptor.get("root_scope", "0" * 64))
        self.api._get_all_output_dirs = lambda: []
        await self.assert_scope_skips_access(descriptor.get("root_scope", "0" * 64))

    async def test_root_scope_malformed_request_skips_reader(self):
        png(self.output_dir / "image.png", prompt=graph())
        for token in ("", "z" * 64, "a" * 63, "a" * 65, "a" * 4097):
            await self.assert_scope_skips_access(token, status=400)
        for root in (None, "-1", "x", "0.0", "+0"):
            await self.assert_scope_skips_access("a" * 64, root=root, status=400)

    async def test_root_scope_echo_preserves_legacy_image_and_roles(self):
        png(self.output_dir / "image.png", prompt=graph())
        descriptor = await self.scoped_descriptor()
        token = descriptor.get("root_scope", "0" * 64)
        _, original = await self.inspect()
        response = await self.client.get(
            "/prompt_manager/images/inspect/image.png",
            params={"root": "0", "root_scope": token},
        )
        result = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(result.get("root_scope"), token)
        self.assertEqual(
            result["image"],
            {"root_index": 0, "path": "image.png", "filename": "image.png"},
        )
        self.assertEqual(result["prompts"], original["prompts"])
        outside = png(self.comfy_dir / "outside.png", prompt=graph("private"))
        self._make_symlink(outside, self.output_dir / "escape.png")
        for path in ("..%2Foutside.png", "escape.png"):
            denied = await self.client.get(
                f"/prompt_manager/images/inspect/{path}",
                params={"root": "0", "root_scope": token},
            )
            self.assertEqual(denied.status, 403)

    async def test_root_scope_source_change_returns_refresh_instead_of_other_root(self):
        png(self.output_dir / "image.png", prompt=graph("old source"))
        other = self.comfy_dir / "input"
        png(other / "image.png", prompt=graph("different source"))
        self.api._get_all_output_dirs = lambda: [self.output_dir, other]
        descriptor = await self.scoped_descriptor()
        self.api._get_all_output_dirs = lambda: [other, self.output_dir]
        response = await self.client.get(
            "/prompt_manager/images/inspect/image.png",
            params={"root": 0, "root_scope": descriptor.get("root_scope", "0" * 64)},
        )
        self.assertEqual(response.status, 409)
        result = await response.json()
        self.assertEqual(result["code"], "source-changed")
        self.assertNotIn("different source", json.dumps(result))

    async def test_video_unrepresentable_decimal_preserves_file_roles_and_raw(self):
        carrier = json.dumps({"prompt": graph()}).replace(
            '"positive": ["1", 0]',
            '"cfg":1e1000000000000000000000000,"positive": ["1", 0]',
        )
        response, result = await self.video_inspect(carrier)
        self.assertEqual(response.status, 200)
        self.assertEqual(result["prompts"]["positive"], self.side("portrait"))
        self.assertEqual(
            result["metadata"]["prompt_graph"], {"status": "malformed", "value": None}
        )
        self.assertEqual(
            result["metadata"]["record_formats"]["prompt"], "reserialized-json"
        )
        self.assertIn("Infinity", result["metadata"]["raw"]["prompt"])

    async def test_root_scope_guard_and_evidence_use_one_current_roots_snapshot(self):
        png(self.output_dir / "image.png", prompt=graph())
        descriptor = await self.scoped_descriptor()
        with patch.object(
            self.api, "_get_all_output_dirs", side_effect=[[self.output_dir]]
        ) as roots:
            response = await self.client.get(
                "/prompt_manager/images/inspect/image.png",
                params={"root": 0, "root_scope": descriptor["root_scope"]},
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            (await response.json())["root_scope"], descriptor["root_scope"]
        )
        roots.assert_called_once_with()

    async def test_exact_saved_association_ambiguity_preserves_physical_file_evidence(
        self,
    ):
        for readable in (False, True):
            with self.subTest(readable=readable):
                filename = "readable.png" if readable else "empty.png"
                metadata = (
                    {"prompt": json.dumps(graph("Physical portrait", ""))}
                    if readable
                    else {}
                )
                path = png(self.output_dir / filename, **metadata)
                before = path.read_bytes()
                results, lookups = [], []
                for count, expected in ((0, "none"), (1, "linked"), (2, "ambiguous")):
                    if count:
                        positive = self._save_prompt(
                            f"Saved owner {readable} {count}",
                            category=f"Category {count}",
                            tags=[f"owner-{count}"],
                        )
                        self.api.db.link_image_to_prompt(positive, str(path))
                    with patch.object(
                        self.api.db,
                        "get_image_prompt_info",
                        wraps=self.api.db.get_image_prompt_info,
                    ) as lookup:
                        response, result = await self.inspect(filename)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(result["library"]["status"], expected)
                    self.assertEqual(lookup.call_count, 1)
                    lookups.append(lookup)
                    results.append(result)
                    self.assertEqual(result["metadata"]["raw"], metadata)
                    self.assertEqual(path.read_bytes(), before)
                    if count == 1:
                        self.assertEqual(result["library"]["positive_id"], positive)
                        self.assertEqual(
                            result["library"]["metadata_scope"], "positive"
                        )
                        self.assertEqual(result["library"]["category"], "Category 1")
                        self.assertEqual(result["library"]["tags"], ["owner-1"])
                        if not readable:
                            self.assertEqual(
                                result["prompts"]["positive"]["source"],
                                "saved-association",
                            )
                    if readable or count != 1:
                        self.assertEqual(result["prompts"], results[0]["prompts"])
                self.assertEqual(
                    results[2]["library"],
                    {
                        "status": "ambiguous",
                        "scope": "saved-library",
                        "positive_id": None,
                        "pair_id": None,
                        "title": None,
                        "category": None,
                        "tags": [],
                        "metadata_scope": None,
                    },
                )
                for field in (
                    "image",
                    "file",
                    "metadata",
                    "prompts",
                    "resolution",
                    "root_scope",
                ):
                    self.assertEqual(results[2][field], results[0][field])
                self.assertNotIn("Saved owner", json.dumps(results[2]))
                self.assertNotIn("association_status", json.dumps(results[2]))
                for lookup in lookups:
                    lookup.assert_called_once_with(
                        str(path), False, report_ambiguity=True
                    )
