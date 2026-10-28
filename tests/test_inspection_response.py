"""Wire projections preserve exact bounded evidence independently of role parsing."""

import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

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

try:
    from tests import inspection_parity as parity
    from tests.open_handles import assert_closed
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    from open_handles import assert_closed

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
            "category_scope": None,
            "tags_scope": None,
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

    def test_graph_ceiling_is_limited(self):
        # f27: the 20,000-node backstop, built at test time. Prompt nodes count
        # top-level API nodes; workflow nodes include subgraph definitions.
        for count, status in ((20_000, "parsed"), (20_001, "limited")):
            prompt = graph_text(count)
            half = count // 2
            workflow = json.dumps(
                {
                    "nodes": [
                        {"id": i, "type": "Primitive", "widgets_values": [1, True]}
                        for i in range(count - half)
                    ],
                    "definitions": {
                        "subgraphs": [
                            {"id": "sg", "nodes": [{"id": i} for i in range(half)]}
                        ]
                    },
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
                    with self.subTest(count=count, key=key):
                        self.assertEqual(result["metadata"][key]["status"], status)
                        if status == "limited":
                            self.assertIsNone(result["metadata"][key]["value"])
                self.assertEqual(result["metadata"]["raw"], read.metadata)
                self.assertEqual(result["prompts"], PROMPTS)

    def test_prompt_graph_with_600_executable_nodes_is_parsed(self):
        graph = parity.build("f03")["prompt"]
        for index in range(100):
            graph[f"{700 + index}"] = {
                "class_type": "PrimitiveInt",
                "inputs": {"value": index},
            }
        self.assertGreater(len(graph), 600)
        text = json.dumps(graph)
        result = json.loads(encode(inspection({"prompt": text})))
        projected = result["metadata"]["prompt_graph"]
        self.assertEqual(projected["status"], "parsed")
        self.assertEqual(len(projected["value"]), len(graph))
        self.assertEqual(projected["value"]["3"]["inputs"]["seed"], 42)
        self.assertEqual(projected["value"]["3"]["inputs"]["cfg"], "6.5")
        self.assertEqual(result["metadata"]["raw"]["prompt"], text)

    def test_workflow_with_513_ui_nodes_is_parsed_and_downloadable(self):
        path = parity.fixture_path("f02")
        chunks = dict(parity.read_text_chunks(path))
        self.assertGreater(len(json.loads(chunks["workflow"])["nodes"]), 513)
        result = json.loads(encode(read_inspection_file(path)))
        metadata = result["metadata"]
        self.assertEqual(metadata["workflow_graph"]["status"], "parsed")
        self.assertEqual(
            metadata["workflow_graph"]["value"]["nodes"],
            json.loads(chunks["workflow"])["nodes"],
        )
        self.assertEqual(metadata["prompt_graph"]["status"], "parsed")
        # The download is the original record text, byte for byte.
        self.assertEqual(metadata["raw"]["workflow"], chunks["workflow"])
        self.assertEqual(metadata["record_formats"]["workflow"], "original-text")

    def test_nan_is_changed_and_meta_keep_prompt_graph_parsed(self):
        path = parity.fixture_path("f14")
        chunks = dict(parity.read_text_chunks(path))
        self.assertIn("NaN", chunks["prompt"])
        source = parity.build("f14")["prompt"]
        result = json.loads(encode(read_inspection_file(path)))
        metadata = result["metadata"]
        graph = metadata["prompt_graph"]
        self.assertEqual(graph["status"], "parsed")
        self.assertEqual(set(graph["value"]), set(source))
        for node_id, node in graph["value"].items():
            self.assertEqual(set(node), {"class_type", "inputs"})
            self.assertEqual(node["class_type"], source[node_id]["class_type"])
        inputs = graph["value"]["3"]["inputs"]
        expected = dict(source["3"]["inputs"], cfg="6.5", denoise="1.0")
        self.assertEqual(inputs, expected)
        self.assertEqual(graph["value"]["4"]["inputs"], source["4"]["inputs"])
        self.assertNotIn("_meta", json.dumps(graph))
        self.assertNotIn("is_changed", json.dumps(graph))
        # Raw records are never modified: the NaN stays in the download.
        self.assertEqual(metadata["raw"]["prompt"], chunks["prompt"])
        self.assertEqual(metadata["workflow_graph"]["status"], "parsed")

    def test_nonfinite_inside_inputs_projects_null(self):
        prompt = (
            '{"3":{"class_type":"KSampler","inputs":{"cfg":NaN,"seed":1,'
            '"denoise":-Infinity,"list":[Infinity,0.5]},"is_changed":[NaN],'
            '"_meta":{"title":"K","weight":Infinity}},"9":{"value":NaN}}'
        )
        workflow = (
            '{"nodes":[{"id":1,"widgets_values":[NaN,1.5,"x"],'
            '"extra":{"v":Infinity}}],"extra":{"ds":{"scale":-Infinity}}}'
        )
        for read in (
            inspection({"prompt": prompt, "workflow": workflow}),
            inspection(
                {"prompt": prompt, "workflow": workflow},
                formats={
                    "prompt": "reserialized-json",
                    "workflow": "reserialized-json",
                },
                carrier='{"prompt":'
                + prompt
                + ',"workflow":'
                + json.dumps(workflow)
                + ',"other":{"x":NaN}}',
            ),
        ):
            result = json.loads(encode(read))
            graph = result["metadata"]["prompt_graph"]
            self.assertEqual(graph["status"], "parsed")
            self.assertEqual(
                graph["value"],
                {
                    "3": {
                        "class_type": "KSampler",
                        "inputs": {
                            "cfg": None,
                            "seed": 1,
                            "denoise": None,
                            "list": [None, "0.5"],
                        },
                    },
                    "9": {},
                },
            )
            flow = result["metadata"]["workflow_graph"]
            self.assertEqual(flow["status"], "parsed")
            self.assertEqual(
                flow["value"],
                {
                    "nodes": [
                        {
                            "id": 1,
                            "widgets_values": [None, "1.5", "x"],
                            "extra": {"v": None},
                        }
                    ],
                    "extra": {"ds": {"scale": None}},
                },
            )
            self.assertEqual(result["metadata"]["raw"], read.metadata)
            self.assertEqual(result["prompts"], PROMPTS)

    def test_prompt_projection_keeps_odd_node_shapes_and_rejects_non_objects(self):
        prompt = '{"1":[NaN,2],"2":"text","3":{"inputs":{"a":1}},"4":{"_meta":{}}}'
        expected = {"1": [None, 2], "2": "text", "3": {"inputs": {"a": 1}}, "4": {}}
        carrier_cases = (
            ('{"prompt":' + prompt + "}", "parsed"),
            ('{"prompt":[1],"workflow":{"nodes":[]}}', "malformed"),
            ('{"prompt":' + json.dumps(prompt) + "}", "parsed"),
            ("[]", "malformed"),
        )
        result = json.loads(encode(inspection({"prompt": prompt})))
        self.assertEqual(
            result["metadata"]["prompt_graph"], {"status": "parsed", "value": expected}
        )
        for carrier, status in carrier_cases:
            with self.subTest(carrier=carrier):
                result = json.loads(
                    encode(
                        inspection(
                            {"prompt": prompt},
                            formats={"prompt": "reserialized-json"},
                            carrier=carrier,
                        )
                    )
                )
                graph = result["metadata"]["prompt_graph"]
                self.assertEqual(graph["status"], status)
                self.assertEqual(
                    graph["value"], expected if status == "parsed" else None
                )

    def test_exact_numbers_survive_tolerance(self):
        prompt = (
            '{"3":{"class_type":"KSampler","inputs":{"seed":1152921504606846976,'
            '"steps":1e3,"cfg":0.1,"denoise":1e0,"exact":6.5000000000000000001,'
            '"negative_seed":-1152921504606846976,"safe":9007199254740991},'
            '"is_changed":[NaN]}}'
        )
        workflow = (
            '{"nodes":[{"id":1,"widgets_values":[1152921504606846976,1e3,0.1,NaN]}]}'
        )
        result = json.loads(
            encode(inspection({"prompt": prompt, "workflow": workflow}))
        )
        for key in ("prompt_graph", "workflow_graph"):
            self.assertEqual(result["metadata"][key]["status"], "parsed")
        inputs = result["metadata"]["prompt_graph"]["value"]["3"]["inputs"]
        self.assertEqual(
            inputs,
            {
                "seed": str(2**60),
                "steps": "1E+3",
                "cfg": "0.1",
                "denoise": "1",
                "exact": "6.5000000000000000001",
                "negative_seed": str(-(2**60)),
                "safe": 9007199254740991,
            },
        )
        self.assertEqual(inputs["seed"], "1152921504606846976")
        widgets = result["metadata"]["workflow_graph"]["value"]["nodes"][0]
        self.assertEqual(
            widgets["widgets_values"], ["1152921504606846976", "1E+3", "0.1", None]
        )

    def test_duplicate_json_keys_still_malformed(self):
        for prompt, workflow in (
            ('{"1":{},"1":{}}', '{"nodes":[],"nodes":[]}'),
            (
                '{"1":{"class_type":"A","inputs":{},"_meta":{"t":1,"t":2}}}',
                '{"nodes":[{"id":1,"extra":{"a":NaN,"a":NaN}}]}',
            ),
            (
                '{"1":{"class_type":"A","inputs":{"x":NaN,"x":1}}}',
                '{"nodes":[{"id":1,"id":2}]}',
            ),
        ):
            with self.subTest(prompt=prompt):
                result = json.loads(
                    encode(inspection({"prompt": prompt, "workflow": workflow}))
                )
                for key in ("prompt_graph", "workflow_graph"):
                    self.assertEqual(
                        result["metadata"][key], {"status": "malformed", "value": None}
                    )
                self.assertEqual(
                    result["metadata"]["raw"], {"prompt": prompt, "workflow": workflow}
                )
        valid = '{"1":{"class_type":"A","inputs":{}}}'
        for carrier in (
            '{"prompt":' + valid + ',"prompt":' + valid + "}",
            '{"prompt":' + valid + ',"other":{"a":1,"a":2}}',
        ):
            with self.subTest(carrier=carrier):
                result = json.loads(
                    encode(
                        inspection(
                            {"prompt": valid},
                            formats={"prompt": "reserialized-json"},
                            carrier=carrier,
                        )
                    )
                )
                self.assertEqual(
                    result["metadata"]["prompt_graph"],
                    {"status": "malformed", "value": None},
                )

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

    def test_response_budget_drops_workflow_graph_before_prompt_graph(self):
        import utils.inspection_response as response

        with tempfile.TemporaryDirectory() as directory:
            path = parity.fixture_path("f05", directory)
            parity.write_png(path, parity.build("f05"))
            read = read_inspection_file(path)

        def metadata_at(cap, read):
            with patch.object(response, "MAX_RESPONSE_BYTES", cap):
                body = encode(read)
            self.assertLessEqual(len(body), cap)
            return json.loads(body)["metadata"]

        # Step 1: the workflow graph alone is dropped; prompt evidence stays.
        step1 = metadata_at(4_500_000, read)
        self.assertEqual(step1["workflow_graph"], {"status": "limited", "value": None})
        self.assertEqual(step1["prompt_graph"]["status"], "parsed")
        self.assertEqual(step1["status"], "available")
        self.assertIn("workflow", step1["raw"])

        # Step 2: a large prompt graph goes too, while raw records stay.
        large = inspection({"prompt": graph_text(20000)})
        step2 = metadata_at(3_000_000, large)
        self.assertEqual(step2["prompt_graph"], {"status": "limited", "value": None})
        self.assertEqual(step2["workflow_graph"]["status"], "absent")
        self.assertEqual(step2["status"], "available")
        self.assertIn("prompt", step2["raw"])

        # Step 3: raw is omitted atomically.
        step3 = metadata_at(3_000_000, read)
        self.assertEqual(step3["prompt_graph"]["status"], "limited")
        self.assertEqual(step3["workflow_graph"]["status"], "limited")
        self.assertEqual(step3["raw"], {})
        self.assertEqual(step3["omitted_reason"], "response-size-limit")

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
        # An unknown or non-string scope is dropped, never passed through.
        for bad in ("recipe", 3, ["pair"]):
            result = json.loads(
                encode(
                    None,
                    library={
                        "status": "linked",
                        "category_scope": bad,
                        "tags_scope": bad,
                        "tags": [],
                    },
                )
            )
            self.assertNotIn("category_scope", result["library"])
            self.assertNotIn("tags_scope", result["library"])
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


MIB = 1024 * 1024


def _text_chunk(kind, keyword, body):
    return parity._raw_chunk(kind, keyword.encode("latin-1") + b"\0" + body)


def _png_with_chunks(path, chunks):
    """A tiny real PNG with raw metadata chunks spliced before IEND, then closed."""
    buffer = io.BytesIO()
    with Image.new("RGB", (4, 4), (40, 80, 120)) as image:
        image.save(buffer, format="PNG")
    data = buffer.getvalue()
    iend = data.rindex(b"IEND") - 4
    with open(path, "wb") as stream:
        stream.write(data[:iend])
        for chunk in chunks:
            stream.write(chunk)
        stream.write(data[iend:])
    return path


class TestImageTagsFacet(unittest.TestCase):
    """B3: the rooted envelope's ``image_tags`` facet is validated."""

    def encoded(self, facet):
        import inspect as signature_of

        parameters = signature_of.signature(encode_inspection_response).parameters
        if "image_tags" not in parameters:
            self.fail("the image_tags facet is not implemented")
        body = encode_inspection_response(
            image=IMAGE,
            root_scope="a" * 64,
            file={
                "observation": "current-file",
                "source_label": "output",
                "media_type": "image",
                "extension": ".png",
                "size": 100,
                "modified_time": 100,
            },
            prompts=PROMPTS,
            resolution={"state": "resolved", "sampler_id": "3", "reason": ""},
            inspection=inspection({"prompt": graph_text()}),
            library={"status": "none"},
            image_tags=facet,
        )
        return json.loads(body)

    def test_valid_facets_pass_through(self):
        for facet in (
            {"state": "current", "tags": ["dunes", "warm_light"]},
            {"state": "current", "tags": []},
            {"state": "none", "tags": []},
            {"state": "unavailable", "tags": []},
            {"state": "current", "tags": [f"tag {i}" for i in range(100)]},
        ):
            with self.subTest(facet=facet):
                self.assertEqual(self.encoded(facet)["image_tags"], facet)

    def test_envelope_rejects_malformed_image_tags(self):
        unavailable = {"state": "unavailable", "tags": []}
        for facet in (
            "current",
            ["dunes"],
            {"state": "current"},
            {"state": "stale", "tags": []},
            {"state": "current", "tags": "dunes"},
            {"state": "current", "tags": ["dunes", 3]},
            {"state": "current", "tags": [""]},
            {"state": "current", "tags": [" dunes"]},
            {"state": "current", "tags": ["a,b"]},
            {"state": "current", "tags": ["x" * 51]},
            {"state": "current", "tags": [f"tag {i}" for i in range(101)]},
            {"state": "none", "tags": ["dunes"]},
            {"state": "unavailable", "tags": ["dunes"]},
            {"state": "current", "tags": [], "locator": "/abs/path.png"},
        ):
            with self.subTest(facet=facet):
                self.assertEqual(self.encoded(facet)["image_tags"], unavailable)

    def test_facet_is_absent_unless_given(self):
        self.assertNotIn("image_tags", json.loads(encode(inspection({}))))
        self.assertNotIn("image_tags", self.encoded(None))


class TestVideoProbeBoundsMatchPngBudgets(unittest.TestCase):
    """ffprobe escapes tag text; the stdout bounds must never gate a record
    that the identical PNG reads within its decoded budget (I3)."""

    @staticmethod
    def _dense(size):
        # Quote-dense JSON text: every other byte escapes, so ffprobe's JSON
        # output is close to twice the decoded size.
        unit = '{"k":"v",' + "".join('"' for _ in range(6)) + "}"
        return (unit * (size // len(unit) + 1))[:size]

    def _video_read(self, path, tags, bound):
        payload = json.dumps({"format": {"tags": tags}}).encode()
        self.assertGreater(len(payload), bound)
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
            read = read_inspection_file(path)
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(process.stdout.closed)
        return read

    def test_near_budget_records_read_the_same_as_png(self):
        from utils.prompt_inspector import (
            ROLE_METADATA_BYTES,
            VIDEO_PROBE_BYTES,
            VIDEO_REPROBE_BYTES,
            WORKFLOW_METADATA_BYTES,
        )

        prompt = self._dense(ROLE_METADATA_BYTES - 1024)
        workflow = self._dense(WORKFLOW_METADATA_BYTES - 1024)
        with tempfile.TemporaryDirectory() as directory:
            png = _png_with_chunks(
                Path(directory) / "same.png",
                [
                    _text_chunk(b"tEXt", "prompt", prompt.encode("latin-1")),
                    _text_chunk(b"tEXt", "workflow", workflow.encode("latin-1")),
                ],
            )
            expected = read_inspection_file(png)
            self.assertEqual(
                expected.record_formats,
                {"prompt": "original-text", "workflow": "original-text"},
            )
            video = Path(directory) / "same.mp4"
            video.write_bytes(b"fixture")
            # Escaped output is over the old 6 MiB + 64 KiB stdout bound.
            read = self._video_read(
                video,
                {"prompt": prompt, "workflow": workflow},
                ROLE_METADATA_BYTES + WORKFLOW_METADATA_BYTES + 64 * 1024,
            )
            self.assertEqual(read.metadata, expected.metadata)
            self.assertEqual(read.record_formats, expected.record_formats)
            self.assertEqual(read.limited_records, frozenset())
            self.assertGreaterEqual(
                VIDEO_PROBE_BYTES,
                2 * (ROLE_METADATA_BYTES + WORKFLOW_METADATA_BYTES) + 64 * 1024,
            )
            self.assertGreaterEqual(
                VIDEO_REPROBE_BYTES, 2 * ROLE_METADATA_BYTES + 64 * 1024
            )
            # A role record near its budget alone escapes past 2 MiB and must
            # still read as the PNG does.
            png = _png_with_chunks(
                Path(directory) / "role.png",
                [_text_chunk(b"tEXt", "prompt", prompt.encode("latin-1"))],
            )
            expected = read_inspection_file(png)
            read = self._video_read(video, {"prompt": prompt}, ROLE_METADATA_BYTES)
            self.assertEqual(read.metadata, expected.metadata)
            self.assertEqual(read.record_formats, {"prompt": "original-text"})


class TestPerRecordBudgets(unittest.TestCase):
    """Role records keep the 2 MiB budget; a workflow has its own 4 MiB budget."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def prompt_chunk(self):
        prompt = json.dumps(parity.build("f04")["prompt"]).encode("latin-1")
        return _text_chunk(b"tEXt", "prompt", prompt)

    def assert_prompts_resolved(self, read):
        from utils.prompt_inspector import inspect_prompt_evidence
        from utils.prompt_pair_resolver import INSPECTION_PROFILE

        evidence = inspect_prompt_evidence(read.metadata, profile=INSPECTION_PROFILE)
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(evidence["prompts"]["positive"]["text"], parity.POS)
        self.assertEqual(evidence["prompts"]["negative"]["text"], parity.NEG)

    def test_budget_constants(self):
        from utils import prompt_inspector

        self.assertEqual(prompt_inspector.ROLE_METADATA_BYTES, 2 * MIB)
        self.assertEqual(prompt_inspector.WORKFLOW_METADATA_BYTES, 4 * MIB)
        self.assertEqual(prompt_inspector.MAX_KEYWORD_BYTES, 80)
        self.assertEqual(MAX_METADATA_BYTES, prompt_inspector.ROLE_METADATA_BYTES)
        default = inspection({"prompt": "{}"})
        self.assertEqual(default.limited_records, frozenset())

    def test_f04_large_workflow_within_budget_no_longer_hides_prompts(self):
        # f04's 2.5 MB workflow is over the old shared 2 MiB budget but within
        # the 4 MiB workflow budget: everything is readable now.
        path = self.directory / "f04.png"
        parity.write_png(path, parity.build("f04"))
        read = read_inspection_file(path)
        self.assert_prompts_resolved(read)
        metadata = json.loads(encode(read))["metadata"]
        self.assertEqual(metadata["status"], "available")
        self.assertEqual(metadata["record_formats"]["workflow"], "original-text")
        self.assertEqual(metadata["workflow_graph"]["status"], "parsed")
        self.assertEqual(metadata["prompt_graph"]["status"], "parsed")

    def test_oversize_workflow_chunk_keeps_prompt_evidence(self):
        from utils.prompt_inspector import WORKFLOW_METADATA_BYTES

        built = parity.build("f04")  # f04's shape, its note grown past budget
        built["workflow"]["nodes"][-1]["widgets_values"] = [
            "A" * (WORKFLOW_METADATA_BYTES + 1)
        ]
        path = self.directory / "f04-oversize.png"
        parity.write_png(path, built)
        read = read_inspection_file(path)
        self.assertNotIn("workflow", read.metadata)
        self.assertEqual(read.limited_records, frozenset({"workflow"}))
        self.assert_prompts_resolved(read)
        metadata = json.loads(encode(read))["metadata"]
        self.assertEqual(metadata["status"], "available")
        self.assertEqual(
            metadata["workflow_graph"], {"status": "limited", "value": None}
        )
        self.assertEqual(metadata["prompt_graph"]["status"], "parsed")
        self.assertEqual(metadata["record_formats"]["workflow"], "limited")
        self.assertEqual(metadata["record_formats"]["prompt"], "original-text")
        self.assertNotIn("workflow", metadata["raw"])
        self.assertIn("prompt", metadata["raw"])

    def test_oversize_workflow_is_skipped_by_seek_not_read(self):
        from utils.prompt_inspector import WORKFLOW_METADATA_BYTES

        workflow = b"A" * (WORKFLOW_METADATA_BYTES + 1)
        path = _png_with_chunks(
            self.directory / "seek.png",
            [_text_chunk(b"tEXt", "workflow", workflow), self.prompt_chunk()],
        )
        reads = self.tracked_reads(path)
        self.assertLess(sum(reads), 64 * 1024)

    def tracked_reads(self, path):
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
            read = read_inspection_file(path)
        self.assert_prompts_resolved(read)
        return reads

    def test_oversize_compressed_workflow_stops_inflating(self):
        from utils import prompt_inspector

        workflow = json.dumps({"nodes": [], "note": "A" * (6 * MIB)}).encode()
        compressed = zlib.compress(workflow, 9)
        self.assertLess(len(compressed), prompt_inspector.WORKFLOW_METADATA_BYTES)
        produced = []
        real = zlib.decompressobj

        class Tracked:
            def __init__(self):
                self.inner = real()

            def decompress(self, data, max_length=0):
                self.assert_bounded(max_length)
                value = self.inner.decompress(data, max_length)
                produced.append(len(value))
                return value

            @staticmethod
            def assert_bounded(max_length):
                if not 0 < max_length <= prompt_inspector.WORKFLOW_METADATA_BYTES + 1:
                    raise AssertionError(f"unbounded inflate: {max_length}")

            def __getattr__(self, name):
                return getattr(self.inner, name)

        for kind, body in (
            (b"zTXt", b"\0" + compressed),
            (b"iTXt", b"\1\0\0\0" + compressed),
        ):
            with self.subTest(kind=kind):
                produced.clear()
                path = _png_with_chunks(
                    self.directory / "compressed.png",
                    [self.prompt_chunk(), _text_chunk(kind, "workflow", body)],
                )
                with patch.object(prompt_inspector.zlib, "decompressobj", Tracked):
                    read = read_inspection_file(path)
                self.assertTrue(produced)
                self.assertLessEqual(
                    max(produced), prompt_inspector.WORKFLOW_METADATA_BYTES + 1
                )
                self.assertEqual(read.limited_records, frozenset({"workflow"}))
                self.assertEqual(read.record_formats["workflow"], "limited")
                self.assertNotIn("workflow", read.metadata)
                self.assert_prompts_resolved(read)

    def test_compressed_workflow_over_budget_is_skipped(self):
        from utils.prompt_inspector import WORKFLOW_METADATA_BYTES

        body = b"\0" + bytes(range(256)) * (WORKFLOW_METADATA_BYTES // 256 + 1)
        path = _png_with_chunks(
            self.directory / "compressed-length.png",
            [_text_chunk(b"zTXt", "workflow", body), self.prompt_chunk()],
        )
        read = read_inspection_file(path)
        self.assertEqual(read.limited_records, frozenset({"workflow"}))
        self.assert_prompts_resolved(read)

    def test_role_records_over_budget_still_unreadable(self):
        from utils.prompt_inspector import ROLE_METADATA_BYTES

        prompt = self.prompt_chunk()
        cases = {
            "prompt-text": [
                _text_chunk(b"tEXt", "prompt", b"x" * (ROLE_METADATA_BYTES + 1))
            ],
            "prompt-inflate": [
                _text_chunk(
                    b"zTXt",
                    "prompt",
                    b"\0" + zlib.compress(b"x" * (ROLE_METADATA_BYTES + 1)),
                )
            ],
            "roles-together": [
                prompt,
                _text_chunk(b"tEXt", "parameters", b"x" * ROLE_METADATA_BYTES),
            ],
            "execution-record": [
                prompt,
                _text_chunk(b"tEXt", EXECUTION_KEY, b"x" * (ROLE_METADATA_BYTES + 1)),
            ],
        }
        for name, chunks in cases.items():
            with self.subTest(case=name):
                path = _png_with_chunks(self.directory / f"{name}.png", chunks)
                with self.assertRaisesRegex(
                    MetadataReadError, "exceeds inspection limits"
                ):
                    read_inspection_file(path)

    def test_role_budget_is_not_shared_with_workflow(self):
        from utils.prompt_inspector import ROLE_METADATA_BYTES

        path = _png_with_chunks(
            self.directory / "shared.png",
            [
                self.prompt_chunk(),
                _text_chunk(b"tEXt", "parameters", b"p" * (ROLE_METADATA_BYTES - 4096)),
                _text_chunk(b"tEXt", "workflow", b"w" * (3 * MIB)),
            ],
        )
        read = read_inspection_file(path)
        self.assertEqual(read.limited_records, frozenset())
        self.assertEqual(len(read.metadata["workflow"]), 3 * MIB)
        self.assert_prompts_resolved(read)

    def test_identical_duplicate_chunks_are_accepted(self):
        path = parity.fixture_path("f15")
        chunks = dict(parity.read_text_chunks(path))
        read = read_inspection_file(path)
        self.assertEqual(read.metadata["prompt"], chunks["prompt"])
        self.assertEqual(read.metadata["workflow"], chunks["workflow"])
        self.assert_prompts_resolved(read)
        metadata = json.loads(encode(read))["metadata"]
        self.assertEqual(metadata["prompt_graph"]["status"], "parsed")
        self.assertEqual(metadata["workflow_graph"]["status"], "parsed")

    def test_conflicting_duplicate_chunks_fail(self):
        with self.assertRaisesRegex(MetadataReadError, "Duplicate prompt metadata"):
            read_inspection_file(parity.fixture_path("f16"))
        prompt = json.dumps(parity.build("f04")["prompt"]).encode("latin-1")
        cases = {
            # Same decoded text, different bytes on disk: not byte-identical.
            "kind": [
                self.prompt_chunk(),
                _text_chunk(b"zTXt", "prompt", b"\0" + zlib.compress(prompt)),
            ],
            "length": [
                self.prompt_chunk(),
                _text_chunk(b"tEXt", "prompt", prompt + b" "),
            ],
            "same-length": [
                self.prompt_chunk(),
                _text_chunk(b"tEXt", "prompt", prompt.replace(b"red fox", b"red cat")),
            ],
            "workflow": [
                self.prompt_chunk(),
                _text_chunk(b"tEXt", "workflow", b'{"nodes": []}'),
                _text_chunk(b"tEXt", "workflow", b'{"nodes": [1]}'),
            ],
        }
        for name, chunks in cases.items():
            with self.subTest(case=name):
                path = _png_with_chunks(self.directory / f"{name}.png", chunks)
                with self.assertRaisesRegex(
                    MetadataReadError, "Duplicate prompt metadata"
                ):
                    read_inspection_file(path)

    def test_duplicate_of_limited_workflow_stays_limited(self):
        from utils.prompt_inspector import WORKFLOW_METADATA_BYTES

        oversize = _text_chunk(
            b"tEXt", "workflow", b"A" * (WORKFLOW_METADATA_BYTES + 1)
        )
        path = _png_with_chunks(
            self.directory / "limited-twice.png",
            [oversize, self.prompt_chunk(), oversize],
        )
        read = read_inspection_file(path)
        self.assertEqual(read.limited_records, frozenset({"workflow"}))
        self.assertNotIn("workflow", read.metadata)
        self.assert_prompts_resolved(read)

    def test_identical_duplicates_do_not_consume_role_budget(self):
        from utils.prompt_inspector import ROLE_METADATA_BYTES

        parameters = _text_chunk(
            b"tEXt", "parameters", b"p" * (ROLE_METADATA_BYTES * 3 // 4)
        )
        path = _png_with_chunks(
            self.directory / "repeat.png",
            [self.prompt_chunk(), parameters, parameters],
        )
        read = read_inspection_file(path)
        self.assertEqual(len(read.metadata["parameters"]), ROLE_METADATA_BYTES * 3 // 4)

    def test_workflow_duplicates_are_limited_without_touching_role_records(self):
        from utils import prompt_inspector

        self.assertEqual(prompt_inspector.DUPLICATE_WORKFLOW_BYTES, 8 * MIB)
        self.assertEqual(prompt_inspector.DUPLICATE_ROLE_BYTES, 4 * MIB)
        workflow = _text_chunk(b"tEXt", "workflow", b"{}" + b" " * 1000)
        size = len(workflow) - 12
        path = _png_with_chunks(
            self.directory / "wf.png", [self.prompt_chunk()] + [workflow] * 6
        )
        added = []
        original = prompt_inspector._PngRecords.add

        def counting(records, kind, data):
            added.append(len(data))
            return original(records, kind, data)

        with (
            patch.object(
                prompt_inspector, "DUPLICATE_WORKFLOW_BYTES", size * 2 + size // 2
            ),
            patch.object(prompt_inspector._PngRecords, "add", counting),
        ):
            read = read_inspection_file(path)
        # Original plus two budgeted duplicates were read; the third is limited.
        self.assertEqual(added.count(size), 3)
        self.assertEqual(read.record_formats["workflow"], "limited")
        self.assertNotIn("workflow", read.metadata)
        self.assertEqual(read.record_formats["prompt"], "original-text")
        self.assert_prompts_resolved(read)

    def test_role_duplicates_past_their_budget_fail_closed(self):
        from utils import prompt_inspector

        chunk = self.prompt_chunk()
        size = len(chunk) - 12
        path = _png_with_chunks(self.directory / "dupes.png", [chunk] * 6)
        with patch.object(
            prompt_inspector, "DUPLICATE_ROLE_BYTES", size * 2 + size // 2
        ):
            with self.assertRaisesRegex(
                MetadataReadError, "Metadata exceeds inspection limits"
            ):
                read_inspection_file(path)

    def test_over_budget_role_record_never_falls_back_to_other_evidence(self):
        from utils import prompt_inspector

        chunk = self.prompt_chunk()
        size = len(chunk) - 12
        extras = [
            _text_chunk(b"tEXt", "parameters", b"fallback parameters"),
            _text_chunk(b"tEXt", "workflow", b"{}"),
        ]
        path = _png_with_chunks(self.directory / "fallback.png", [chunk] * 6 + extras)
        with patch.object(prompt_inspector, "DUPLICATE_ROLE_BYTES", size * 2):
            with self.assertRaisesRegex(
                MetadataReadError, "Metadata exceeds inspection limits"
            ):
                read_inspection_file(path)

    def test_role_key_can_never_end_up_limited(self):
        from utils import prompt_inspector

        path = _png_with_chunks(self.directory / "plain.png", [self.prompt_chunk()])
        original = prompt_inspector._PngRecords.skip

        def limit_role(records, key, length):
            records.limited.add("prompt")
            return original(records, key, length)

        with patch.object(prompt_inspector._PngRecords, "skip", limit_role):
            with self.assertRaisesRegex(
                MetadataReadError, "Metadata exceeds inspection limits"
            ):
                read_inspection_file(path)

    def test_duplicates_within_the_total_budget_are_unchanged(self):
        path = _png_with_chunks(self.directory / "few.png", [self.prompt_chunk()] * 3)
        read = read_inspection_file(path)
        self.assertEqual(read.record_formats["prompt"], "original-text")
        self.assert_prompts_resolved(read)

    def test_unretained_text_chunks_are_skipped_without_retention(self):
        software = _text_chunk(b"tEXt", "Software", b"S" * (5 * MIB))
        compressed = _text_chunk(b"zTXt", "Comment", b"\0" + b"not zlib at all")
        path = _png_with_chunks(
            self.directory / "software.png",
            [software, compressed, self.prompt_chunk()],
        )
        reads = self.tracked_reads(path)
        self.assertLess(sum(reads), 64 * 1024)
        read = read_inspection_file(path)
        self.assertEqual(set(read.metadata), {"prompt"})

    def test_unretained_chunk_with_malformed_keyword_still_fails(self):
        for body in (b"no-separator", b"k" * 100 + b"\0text", b"\0text"):
            with self.subTest(body=body[:12]):
                path = _png_with_chunks(
                    self.directory / "keyword.png",
                    [self.prompt_chunk(), parity._raw_chunk(b"tEXt", body)],
                )
                with self.assertRaisesRegex(MetadataReadError, "Malformed"):
                    read_inspection_file(path)

    def test_chunk_count_limit_unchanged(self):
        from utils import prompt_inspector

        self.assertEqual(prompt_inspector.MAX_PNG_CHUNKS, 10_000)
        filler = _text_chunk(b"tEXt", "Comment", b"x")
        for total, readable in ((10_000, True), (10_001, False)):
            with self.subTest(total=total):
                path = _png_with_chunks(self.directory / "count.png", [])
                structural = self.chunk_count(path)
                path = _png_with_chunks(
                    self.directory / "count.png",
                    [self.prompt_chunk()] + [filler] * (total - structural - 1),
                )
                self.assertEqual(self.chunk_count(path), total)
                if readable:
                    self.assert_prompts_resolved(read_inspection_file(path))
                else:
                    with self.assertRaisesRegex(MetadataReadError, "chunk count"):
                        read_inspection_file(path)

    @staticmethod
    def chunk_count(path):
        data, offset, count = path.read_bytes(), 8, 0
        while offset < len(data):
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            offset += 12 + length
            count += 1
        return count

    def test_non_png_workflow_over_budget_is_limited_not_unreadable(self):
        from utils.prompt_inspector import WORKFLOW_METADATA_BYTES

        prompt = json.dumps(parity.build("f04")["prompt"])
        metadata = {"prompt": prompt, "workflow": "w" * (WORKFLOW_METADATA_BYTES + 1)}
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.info, fake.format, fake.size = metadata, "WEBP", (4, 4)
        path = self.directory / "image.webp"
        path.write_bytes(b"RIFF0000WEBP")
        with patch("utils.prompt_inspector.Image.open", return_value=fake):
            read = read_inspection_file(path)
        self.assertEqual(read.limited_records, frozenset({"workflow"}))
        self.assertEqual(
            read.record_formats, {"prompt": "original-text", "workflow": "limited"}
        )
        self.assert_prompts_resolved(read)

    def test_limited_record_format_omits_raw_and_limits_graph(self):
        read = InspectionRead(
            metadata={"prompt": graph_text(), "workflow": "{}"},
            record_formats={"prompt": "original-text", "workflow": "original-text"},
            video_carrier_text=None,
            format="PNG",
            width=4,
            height=4,
            limited_records=frozenset({"workflow"}),
        )
        metadata = json.loads(encode(read))["metadata"]
        self.assertEqual(metadata["record_formats"]["workflow"], "limited")
        self.assertNotIn("workflow", metadata["raw"])
        self.assertEqual(
            metadata["workflow_graph"], {"status": "limited", "value": None}
        )
        self.assertEqual(metadata["prompt_graph"]["status"], "parsed")
        only = InspectionRead({}, {"workflow": "limited"}, None, "PNG", 4, 4)
        metadata = json.loads(encode(only))["metadata"]
        self.assertEqual(metadata["status"], "available")
        self.assertEqual(metadata["raw"], {})
        self.assertEqual(metadata["record_formats"], {"workflow": "limited"})
        self.assertEqual(metadata["workflow_graph"]["status"], "limited")
        self.assertEqual(metadata["prompt_graph"]["status"], "absent")


class TestInheritedCategory(unittest.TestCase):
    """#332 MC4: one inherited-category rule for the chip, filter and History."""

    def test_recipe_set_blank_and_absent(self):
        from utils.inspection_response import inherited_category, library_metadata

        cases = [
            (("Night", "Recipes", True), ("Recipes", "pair")),
            (("Night", "   ", True), ("Night", "positive")),
            (("Night", " \t", True), ("Night", "positive")),
            (("Night", b" ", True), ("Night", "positive")),
            (("Night", None, True), ("Night", "positive")),
            (("Night", "Recipes", False), ("Night", "positive")),
            (("  ", None, False), ("  ", "positive")),
            ((None, None, False), (None, "positive")),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(inherited_category(*args), expected)
                positive, recipe, has_recipe = args
                metadata = library_metadata(
                    positive, ["t"], (recipe, []) if has_recipe else None
                )
                self.assertEqual(
                    (metadata["category"], metadata["category_scope"]), expected
                )
                self.assertEqual(metadata["tags_scope"], "positive")
