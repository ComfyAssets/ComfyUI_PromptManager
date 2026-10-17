"""Physical-file prompt inspection is bounded, conservative and read-only."""

import json
from unittest.mock import patch

from PIL import Image, PngImagePlugin

try:
    from test_api_images import ImageAPITestCase, make_png
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png

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
        broken[index + 8] ^= 1
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
            "py.api.images.read_inspection_metadata",
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
        lookup.assert_called_once_with(str(other / "image.png"), False)
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
