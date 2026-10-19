"""Wire projections preserve exact bounded evidence independently of role parsing."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.inspection_response import encode_inspection_response
from utils.prompt_inspector import (
    InspectionRead,
    MAX_METADATA_BYTES,
    MetadataReadError,
    inspect_prompt_roles,
    read_inspection_file,
    read_inspection_metadata,
)
from utils.pair_execution import EXECUTION_KEY

CAP = 8 * 1024 * 1024
IMAGE = {"root_index": 0, "path": "image.png", "filename": "image.png"}
PROMPTS = {
    "positive": {"status": "resolved", "text": "portrait", "source": "graph-inferred"},
    "negative": {"status": "resolved", "text": "", "source": "graph-inferred"},
}


def inspection(metadata, *, formats=None, carrier=None):
    return InspectionRead(
        metadata=metadata,
        record_formats=(
            formats
            if formats is not None
            else {key: "original-text" for key in metadata}
        ),
        video_carrier_text=carrier,
        format="PNG",
        width=4,
        height=4,
    )


def encode(read, *, prompts=None, file=None, library=None):
    return encode_inspection_response(
        image=IMAGE,
        root_scope="a" * 64,
        file=file
        or {
            "observation": "current-file",
            "source_label": "output",
            "media_type": "image",
            "extension": ".png",
            "size": 100,
            "modified_time": 100,
        },
        prompts=PROMPTS if prompts is None else prompts,
        resolution={"state": "resolved", "sampler_id": "3", "reason": ""},
        inspection=read,
        library=library
        or {
            "status": "none",
            "scope": "saved-library",
            "positive_id": None,
            "pair_id": None,
            "title": None,
            "category": None,
            "tags": [],
            "metadata_scope": None,
        },
    )


def graph_text(count=3):
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "portrait"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["1", 0],
                "negative": ["2", 0],
                "seed": 9007199254740993,
                "cfg": "EXACT_CFG",
            },
        },
    }
    for index in range(4, count + 1):
        graph[str(index)] = {
            "class_type": "Primitive",
            "inputs": {"value": 1, "labels": [True, False, "ordinary"]},
        }
    return json.dumps(graph).replace('"EXACT_CFG"', "6.5000000000000000001")


class TestInspectionResponse(unittest.TestCase):
    def test_normalized_numbers_preserve_exact_literals(self):
        text = graph_text().replace(
            '"cfg": 6.5000000000000000001',
            '"cfg": 6.5000000000000000001, "huge": 1e1000000000',
        )
        result = json.loads(encode(inspection({"prompt": text})))
        inputs = result["metadata"]["prompt_graph"]["value"]["3"]["inputs"]
        self.assertEqual(inputs["seed"], "9007199254740993")
        self.assertEqual(inputs["cfg"], "6.5000000000000000001")
        self.assertEqual(inputs["huge"].lower(), "1e+1000000000")
        self.assertEqual(inputs["positive"], ["1", 0])
        self.assertEqual(result["metadata"]["raw"]["prompt"], text)
        self.assertEqual(result["prompts"], PROMPTS)

    def test_normalization_preserves_150_node_graph_with_more_than_500_json_values(
        self,
    ):
        text = graph_text(150)
        self.assertGreater(len(text.split(":")), 500)
        for read in (
            inspection({"prompt": text}),
            inspection(
                {"prompt": json.dumps(json.loads(text))},
                formats={"prompt": "reserialized-json"},
                carrier='{"prompt":' + text + ',"unrelated":{"value":1}}',
            ),
        ):
            result = json.loads(encode(read))
            graph = result["metadata"]["prompt_graph"]
            self.assertEqual(graph["status"], "parsed")
            self.assertEqual(len(graph["value"]), 150)
            self.assertEqual(
                graph["value"]["3"]["inputs"]["cfg"], "6.5000000000000000001"
            )
            self.assertEqual(graph["value"]["3"]["inputs"]["seed"], "9007199254740993")
            self.assertEqual(graph["value"]["3"]["inputs"]["positive"], ["1", 0])
            self.assertIs(graph["value"]["4"]["inputs"]["labels"][0], True)
            self.assertEqual(result["metadata"]["raw"], read.metadata)
            self.assertEqual(result["prompts"], PROMPTS)

    def test_graph_node_limit_counts_nodes_not_json_fields(self):
        for count, status in ((500, "parsed"), (501, "limited")):
            prompt = graph_text(count)
            workflow = json.dumps(
                {
                    "nodes": [
                        {"id": i, "type": "Primitive", "widgets_values": [1, True]}
                        for i in range(count)
                    ]
                }
            )
            for read in (
                inspection({"prompt": prompt, "workflow": workflow}),
                inspection(
                    {"prompt": json.dumps(json.loads(prompt)), "workflow": workflow},
                    formats={
                        "prompt": "reserialized-json",
                        "workflow": "reserialized-json",
                    },
                    carrier='{"other":[],"prompt":'
                    + prompt
                    + ',"workflow":'
                    + workflow
                    + "}",
                ),
            ):
                result = json.loads(encode(read))
                for key in ("prompt_graph", "workflow_graph"):
                    self.assertEqual(result["metadata"][key]["status"], status)
                    if status == "limited":
                        self.assertIsNone(result["metadata"][key]["value"])
                self.assertEqual(result["metadata"]["raw"], read.metadata)
                self.assertEqual(result["prompts"], PROMPTS)

    def test_strict_projection_depth_is_separate_from_semantic_link_depth(self):
        for depth in (64, 65):
            # API map root + node + inputs + nested array containers.
            text = (
                '{"1":{"class_type":"Primitive","inputs":{"value":'
                + "[" * (depth - 3)
                + "0"
                + "]" * (depth - 3)
                + "}}}"
            )
            result = json.loads(encode(inspection({"prompt": text})))
            self.assertEqual(
                result["metadata"]["prompt_graph"]["status"],
                "parsed" if depth == 64 else "limited",
            )
            carrier = '{"prompt":' + text + "}"
            result = json.loads(
                encode(
                    inspection(
                        {"prompt": text},
                        formats={"prompt": "reserialized-json"},
                        carrier=carrier,
                    )
                )
            )
            self.assertEqual(result["metadata"]["prompt_graph"]["status"], "limited")
        text = '{"1":{"inputs":{"value":' + "[" * 60 + "0" + "]" * 60 + "}}}"
        result = json.loads(
            encode(
                inspection(
                    {"prompt": text},
                    formats={"prompt": "reserialized-json"},
                    carrier='{"prompt":' + text + "}",
                )
            )
        )
        self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")
        chain = {
            str(i): {
                "class_type": "Reroute",
                "inputs": {"value": [str((i + 1) % 150), 0]},
            }
            for i in range(150)
        }
        result = json.loads(encode(inspection({"prompt": json.dumps(chain)})))
        self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")
        self.assertEqual(len(result["metadata"]["prompt_graph"]["value"]), 150)

    def test_wide_bounded_values_do_not_consume_semantic_visit_budget(self):
        text = json.dumps(
            {
                "1": {
                    "inputs": {
                        "values": list(range(20000)),
                        "fields": {str(i): True for i in range(1000)},
                    }
                }
            }
        )
        self.assertLess(len(text.encode()), MAX_METADATA_BYTES)
        body = encode(inspection({"prompt": text}))
        graph = json.loads(body)["metadata"]["prompt_graph"]
        self.assertEqual(graph["status"], "parsed")
        self.assertEqual(len(graph["value"]["1"]["inputs"]["values"]), 20000)
        self.assertEqual(len(graph["value"]["1"]["inputs"]["fields"]), 1000)
        self.assertLess(len(body), CAP)

    def test_strict_malformed_records_preserve_original_raw_and_roles(self):
        for text in (
            '{"1":{},"1":{}}',
            '{"1":{"value":NaN}}',
            '{"1":{"value":Infinity}}',
            "{broken",
            "[]",
            "42",
            "[" * 2000,
        ):
            result = json.loads(encode(inspection({"prompt": text, "workflow": text})))
            self.assertIn(
                result["metadata"]["prompt_graph"]["status"], ("malformed", "limited")
            )
            self.assertIsNone(result["metadata"]["prompt_graph"]["value"])
            self.assertEqual(
                result["metadata"]["raw"], {"prompt": text, "workflow": text}
            )
            self.assertEqual(result["prompts"], PROMPTS)

    def test_video_missing_exact_source_is_limited_not_rounded(self):
        rounded = json.dumps(json.loads(graph_text()))
        result = json.loads(
            encode(
                inspection({"prompt": rounded}, formats={"prompt": "reserialized-json"})
            )
        )
        self.assertEqual(
            result["metadata"]["prompt_graph"], {"status": "limited", "value": None}
        )
        self.assertEqual(result["metadata"]["raw"]["prompt"], rounded)
        self.assertEqual(result["prompts"], PROMPTS)

    def test_response_escaping_omits_graphs_then_entire_raw_atomically(self):
        prompts = {
            role: {
                "status": "resolved",
                "text": "\x01" * 100000,
                "source": "graph-inferred",
            }
            for role in ("positive", "negative")
        }
        source = json.loads(graph_text())
        source["1"]["inputs"]["text"] = prompts["positive"]["text"]
        source["2"]["inputs"]["text"] = prompts["negative"]["text"]
        raw = json.dumps(source)
        for count, raw_retained in ((450000, True), (550000, False)):
            metadata = {"prompt": raw, "parameters": "\x01" * count}
            self.assertLess(
                sum(len(value.encode()) for value in metadata.values()),
                MAX_METADATA_BYTES,
            )
            body = encode(inspection(metadata), prompts=prompts)
            result = json.loads(body)
            self.assertLessEqual(len(body), CAP)
            self.assertEqual(result["image"], IMAGE)
            self.assertEqual(result["prompts"], prompts)
            self.assertEqual(
                result["metadata"]["prompt_graph"], {"status": "limited", "value": None}
            )
            self.assertEqual(
                result["metadata"]["workflow_graph"],
                {"status": "absent", "value": None},
            )
            self.assertEqual(
                result["metadata"]["record_formats"],
                {key: "original-text" for key in metadata},
            )
            if raw_retained:
                self.assertEqual(result["metadata"]["status"], "available")
                self.assertIsNone(result["metadata"]["omitted_reason"])
                self.assertEqual(result["metadata"]["raw"], metadata)
                self.assertEqual(
                    result["metadata"]["parameters"], metadata["parameters"]
                )
            else:
                self.assertEqual(result["metadata"]["raw"], {})
                self.assertIsNone(result["metadata"]["parameters"])
                self.assertEqual(result["metadata"]["status"], "limited")
                self.assertEqual(
                    result["metadata"]["omitted_reason"], "response-size-limit"
                )

    def test_response_byte_boundary_is_inclusive(self):
        prompts = {
            role: {
                "status": "resolved",
                "text": "\x01" * 100000,
                "source": "graph-inferred",
            }
            for role in ("positive", "negative")
        }
        text = graph_text().replace(
            '"cfg": 6.5000000000000000001', '"cfg":6.5000000000000000001,"label":"猫😀"'
        )
        read = inspection({"prompt": text, "parameters": "\x01" * 500000})
        base_file = {
            "observation": "current-file",
            "source_label": "output",
            "media_type": "image",
            "extension": ".png",
        }
        initial_size = len(encode(read, prompts=prompts, file=base_file))
        self.assertLess(initial_size, CAP)
        for target in (CAP - 1, CAP, CAP + 1):
            extra = target - initial_size
            file = {**base_file, "source_label": "output" + ("x" if extra % 2 else "")}
            parameters = read.metadata["parameters"] + "x" * (extra // 2)
            current = inspection({"prompt": text, "parameters": parameters})
            self.assertLess(
                sum(len(value.encode()) for value in current.metadata.values()),
                MAX_METADATA_BYTES,
            )
            body = encode(current, prompts=prompts, file=file)
            result = json.loads(body)
            if target <= CAP:
                self.assertEqual(len(body), target)
                self.assertEqual(result["metadata"]["prompt_graph"]["status"], "parsed")
            else:
                self.assertLessEqual(len(body), CAP)
                self.assertEqual(
                    result["metadata"]["prompt_graph"]["status"], "limited"
                )
            self.assertEqual(result["prompts"], prompts)
            self.assertEqual(result["image"], IMAGE)
            self.assertEqual(result["metadata"]["raw"], current.metadata)

    def test_optional_display_fields_are_validated_and_library_is_bounded(self):
        file = {
            "size": -1,
            "width": True,
            "height": float("inf"),
            "modified_time": float("nan"),
            "format": 12,
            "observation": "current-file",
        }
        library = {
            "status": "linked",
            "positive_id": 2**53,
            "pair_id": True,
            "tags": ["valid", 3],
            "category": {},
            "title": [],
        }
        result = json.loads(encode(None, file=file, library=library))
        for key in ("size", "width", "height", "modified_time", "format"):
            self.assertNotIn(key, result["file"])
        for key in ("positive_id", "pair_id", "category", "title"):
            self.assertNotIn(key, result["library"])
        self.assertEqual(result["library"]["tags"], ["valid"])
        result = json.loads(
            encode(inspection({}), library={"status": "linked", "tags": ["猫" * 12000]})
        )
        self.assertEqual(result["library"]["status"], "limited")
        self.assertEqual(result["library"]["tags"], [])
        self.assertLess(
            len(json.dumps(result["library"], ensure_ascii=False).encode()), 32 * 1024
        )

    def test_absent_unreadable_invalid_workflow_and_formats_remain_distinct(self):
        for read, status in (
            (None, "unreadable"),
            (inspection({}), "absent"),
            (inspection({}, formats={"workflow": "invalid"}), "available"),
        ):
            result = json.loads(encode(read))
            self.assertEqual(result["metadata"]["status"], status)
            self.assertEqual(result["metadata"]["raw"], {})
            self.assertIsNone(result["metadata"]["parameters"])
        self.assertEqual(result["metadata"]["workflow_graph"]["status"], "malformed")

    def test_video_null_role_marker_raises_before_successful_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"fixture")
            for field in ("prompt", "parameters", EXECUTION_KEY):
                for reader in (read_inspection_file, read_inspection_metadata):
                    process = MagicMock()
                    process.stdout = io.BytesIO(
                        json.dumps(
                            {"format": {"tags": {"comment": json.dumps({field: None})}}}
                        ).encode()
                    )
                    process.poll.return_value = 0
                    process.returncode = 0
                    with (
                        patch(
                            "utils.prompt_inspector.ffprobe_path",
                            return_value="/tool/ffprobe",
                        ),
                        patch(
                            "utils.prompt_inspector.subprocess.Popen",
                            return_value=process,
                        ),
                    ):
                        with self.assertRaises(MetadataReadError):
                            reader(path)
                    self.assertTrue(process.stdout.closed)

    def test_original_role_projection_ignores_workflow_without_losing_saved_warning(
        self,
    ):
        self.assertEqual(
            inspect_prompt_roles({"workflow": "{}"}, {"prompt_id": 1})["positive"][
                "source"
            ],
            "saved-association",
        )

    def test_unrepresentable_decimal_exponent_preserves_roles_and_raw(self):
        text = graph_text().replace(
            "6.5000000000000000001", "1e1000000000000000000000000"
        )
        for read in (
            inspection({"prompt": text}),
            inspection(
                {"prompt": json.dumps(json.loads(text))},
                formats={"prompt": "reserialized-json"},
                carrier='{"prompt":' + text + "}",
            ),
        ):
            result = json.loads(encode(read))
            self.assertEqual(
                result["metadata"]["prompt_graph"],
                {"status": "malformed", "value": None},
            )
            self.assertEqual(result["metadata"]["raw"], read.metadata)
            self.assertEqual(result["prompts"], PROMPTS)

    def test_history_identity_shares_rooted_evidence_and_library_projection(self):
        from utils.inspection_response import encode_history_inspection_response

        read = inspection({"prompt": graph_text()})
        identity = {
            "occurrence": {
                "run_id": "run",
                "output_node_id": "node",
                "image_ordinal": 2,
            },
            "reference": {"filename": "image.png"},
            "history_epoch": "epoch",
            "current_scope": "scope",
            "location_key": "location",
            "file_identity": "unverified",
        }
        body = json.loads(
            encode_history_inspection_response(
                current_output=identity,
                file={"size": 7, "observation": "current-file"},
                prompts=PROMPTS,
                resolution={"state": "resolved"},
                inspection=read,
                library={"status": "linked", "tags": ["猫" * 12000]},
            )
        )
        rooted = json.loads(
            encode(
                read,
                file={"size": 7, "observation": "current-file"},
                library={"status": "linked", "tags": ["猫" * 12000]},
            )
        )
        self.assertEqual(body["current_output"], identity)
        self.assertEqual(body.get("history_version"), 1)
        self.assertEqual(body.get("history_epoch"), "epoch")
        for key in ("file", "prompts", "metadata", "library"):
            self.assertEqual(body[key], rooted[key])
        self.assertEqual(body["library"]["status"], "limited")
        self.assertNotIn("image", body)
        self.assertNotIn("root_scope", body)
