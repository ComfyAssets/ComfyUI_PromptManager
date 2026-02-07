"""
Tests for ComfyUI metadata extraction.

Tests all methods of ComfyUIMetadataExtractor using real minimal
PNG files with injected text chunks to simulate ComfyUI output.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from utils.metadata_extractor import ComfyUIMetadataExtractor


def _create_png(text_chunks=None, size=(64, 64)):
    """Create a minimal PNG file with optional text chunks.

    Args:
        text_chunks: dict of key->value to embed as PNG text metadata.
        size: tuple (width, height).

    Returns:
        Path to the temporary PNG file.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.close()

    img = Image.new("RGB", size, color=(128, 128, 128))
    pnginfo = PngInfo()
    if text_chunks:
        for key, value in text_chunks.items():
            pnginfo.add_text(key, value)
    img.save(tmp.name, pnginfo=pnginfo)
    return tmp.name


class MetadataTestCase(unittest.TestCase):
    """Base class that cleans up temp files."""

    def setUp(self):
        self.extractor = ComfyUIMetadataExtractor()
        self.temp_files = []

    def tearDown(self):
        for f in self.temp_files:
            if os.path.exists(f):
                os.unlink(f)

    def _png(self, text_chunks=None, size=(64, 64)):
        path = _create_png(text_chunks, size)
        self.temp_files.append(path)
        return path


class TestExtractMetadata(MetadataTestCase):
    """Test extract_metadata method."""

    def test_returns_none_for_plain_png(self):
        path = self._png()
        result = self.extractor.extract_metadata(path)
        self.assertIsNone(result)

    def test_extracts_workflow_data(self):
        workflow = {"nodes": [{"type": "CLIPTextEncode", "inputs": {"text": "hello"}}]}
        path = self._png({"workflow": json.dumps(workflow)})
        result = self.extractor.extract_metadata(path)
        self.assertIsNotNone(result)
        self.assertIn("workflow", result)
        self.assertEqual(result["workflow"]["nodes"][0]["type"], "CLIPTextEncode")

    def test_extracts_prompt_data(self):
        prompt = {"1": {"class_type": "KSampler", "inputs": {}}}
        path = self._png({"prompt": json.dumps(prompt)})
        result = self.extractor.extract_metadata(path)
        self.assertIsNotNone(result)
        self.assertIn("prompt", result)

    def test_extracts_text_encoder_nodes(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"text": "positive prompt"}},
            {"type": "KSampler", "inputs": {}},
        ]}
        path = self._png({"workflow": json.dumps(workflow)})
        result = self.extractor.extract_metadata(path)
        self.assertIn("text_encoder_nodes", result)
        self.assertEqual(len(result["text_encoder_nodes"]), 1)

    def test_extracts_simple_metadata_fields(self):
        path = self._png({"steps": "20", "seed": "12345", "model": "sd_v1.5"})
        result = self.extractor.extract_metadata(path)
        self.assertIsNotNone(result)
        # "20" and "12345" are valid JSON, so they get parsed to int
        self.assertEqual(result["steps"], 20)
        self.assertEqual(result["seed"], 12345)
        # "sd_v1.5" fails JSON parse, stored as string
        self.assertEqual(result["model"], "sd_v1.5")

    def test_json_metadata_fields_parsed(self):
        path = self._png({"cfg_scale": json.dumps(7.5)})
        result = self.extractor.extract_metadata(path)
        self.assertEqual(result["cfg_scale"], 7.5)

    def test_file_info_always_present(self):
        workflow = {"nodes": [{"type": "CLIPTextEncode", "inputs": {}}]}
        path = self._png({"workflow": json.dumps(workflow)}, size=(128, 256))
        result = self.extractor.extract_metadata(path)
        fi = result["file_info"]
        self.assertEqual(fi["dimensions"], [128, 256])
        self.assertEqual(fi["format"], "PNG")
        self.assertEqual(fi["mode"], "RGB")
        self.assertIn("size", fi)
        self.assertIn("created_time", fi)

    def test_invalid_workflow_json_handled(self):
        path = self._png({"workflow": "not valid json {{"})
        result = self.extractor.extract_metadata(path)
        # Should not contain workflow since JSON parse failed
        self.assertIsNone(result)

    def test_invalid_prompt_json_handled(self):
        workflow = {"nodes": [{"type": "CLIPTextEncode", "inputs": {}}]}
        path = self._png({
            "workflow": json.dumps(workflow),
            "prompt": "broken json {{",
        })
        result = self.extractor.extract_metadata(path)
        # Workflow should still be extracted even if prompt fails
        self.assertIn("workflow", result)
        self.assertNotIn("prompt", result)

    def test_nonexistent_file_returns_none(self):
        result = self.extractor.extract_metadata("/nonexistent/path/image.png")
        self.assertIsNone(result)


class TestGetFileInfo(MetadataTestCase):
    """Test get_file_info method."""

    def test_returns_correct_dimensions(self):
        path = self._png(size=(320, 240))
        with Image.open(path) as img:
            info = self.extractor.get_file_info(path, img)
        self.assertEqual(info["dimensions"], [320, 240])

    def test_returns_file_size(self):
        path = self._png()
        with Image.open(path) as img:
            info = self.extractor.get_file_info(path, img)
        self.assertGreater(info["size"], 0)
        self.assertEqual(info["size"], os.path.getsize(path))

    def test_returns_format_and_mode(self):
        path = self._png()
        with Image.open(path) as img:
            info = self.extractor.get_file_info(path, img)
        self.assertEqual(info["format"], "PNG")
        self.assertEqual(info["mode"], "RGB")


class TestFindTextEncoderNodes(MetadataTestCase):
    """Test find_text_encoder_nodes method."""

    def test_finds_clip_text_encode(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"text": "prompt"}},
        ]}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 1)

    def test_finds_sdxl_encoder(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncodeSDXL", "inputs": {}},
        ]}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 1)

    def test_finds_prompt_manager_node(self):
        workflow = {"nodes": [
            {"type": "PromptManager", "inputs": {"text": "test"}},
        ]}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 1)

    def test_ignores_non_encoder_nodes(self):
        workflow = {"nodes": [
            {"type": "KSampler", "inputs": {}},
            {"type": "CheckpointLoader", "inputs": {}},
        ]}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 0)

    def test_handles_dict_format_nodes(self):
        workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "test"}},
            "2": {"class_type": "KSampler", "inputs": {}},
        }
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_id"], "1")

    def test_handles_nested_workflow(self):
        workflow = {"workflow": {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {}},
        ]}}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(len(nodes), 1)

    def test_empty_workflow(self):
        nodes = self.extractor.find_text_encoder_nodes({})
        self.assertEqual(nodes, [])

    def test_non_dict_returns_empty(self):
        nodes = self.extractor.find_text_encoder_nodes("not a dict")
        self.assertEqual(nodes, [])

    def test_none_nodes_data(self):
        workflow = {"nodes": None}
        nodes = self.extractor.find_text_encoder_nodes(workflow)
        self.assertEqual(nodes, [])


class TestIsTextEncoderNode(MetadataTestCase):
    """Test is_text_encoder_node method."""

    def test_clip_text_encode(self):
        self.assertTrue(self.extractor.is_text_encoder_node({"type": "CLIPTextEncode"}))

    def test_class_type_field(self):
        self.assertTrue(self.extractor.is_text_encoder_node({"class_type": "CLIPTextEncode"}))

    def test_case_insensitive(self):
        self.assertTrue(self.extractor.is_text_encoder_node({"type": "cliptextencode"}))

    def test_title_with_text_keyword(self):
        self.assertTrue(self.extractor.is_text_encoder_node(
            {"type": "Unknown", "title": "My Text Encoder"}
        ))

    def test_title_with_prompt_keyword(self):
        self.assertTrue(self.extractor.is_text_encoder_node(
            {"type": "Unknown", "title": "Prompt Input"}
        ))

    def test_non_encoder_node(self):
        self.assertFalse(self.extractor.is_text_encoder_node({"type": "KSampler"}))

    def test_non_dict_returns_false(self):
        self.assertFalse(self.extractor.is_text_encoder_node("string"))
        self.assertFalse(self.extractor.is_text_encoder_node(None))
        self.assertFalse(self.extractor.is_text_encoder_node(42))

    def test_empty_dict_returns_false(self):
        self.assertFalse(self.extractor.is_text_encoder_node({}))


class TestExtractPromptTextFromWorkflow(MetadataTestCase):
    """Test extract_prompt_text_from_workflow method."""

    def test_extracts_text_input(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"text": "beautiful sunset"}},
        ]}
        text = self.extractor.extract_prompt_text_from_workflow(workflow)
        self.assertEqual(text, "beautiful sunset")

    def test_extracts_prompt_input(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"prompt": "a cat"}},
        ]}
        text = self.extractor.extract_prompt_text_from_workflow(workflow)
        self.assertEqual(text, "a cat")

    def test_returns_none_when_no_text(self):
        workflow = {"nodes": [
            {"type": "KSampler", "inputs": {"steps": 20}},
        ]}
        text = self.extractor.extract_prompt_text_from_workflow(workflow)
        self.assertIsNone(text)

    def test_returns_none_for_empty_workflow(self):
        text = self.extractor.extract_prompt_text_from_workflow({})
        self.assertIsNone(text)

    def test_handles_list_input_value(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"text": ["linked_value", 0]}},
        ]}
        text = self.extractor.extract_prompt_text_from_workflow(workflow)
        self.assertEqual(text, "linked_value")

    def test_skips_empty_text_fields(self):
        workflow = {"nodes": [
            {"type": "CLIPTextEncode", "inputs": {"text": ""}},
            {"type": "PromptManager", "inputs": {"text": "actual prompt"}},
        ]}
        text = self.extractor.extract_prompt_text_from_workflow(workflow)
        self.assertEqual(text, "actual prompt")


class TestGetGenerationParameters(MetadataTestCase):
    """Test get_generation_parameters method."""

    def test_extracts_top_level_params(self):
        metadata = {"steps": 20, "cfg_scale": 7.5, "sampler": "euler", "seed": 12345}
        params = self.extractor.get_generation_parameters(metadata)
        self.assertEqual(params["steps"], 20)
        self.assertEqual(params["cfg_scale"], 7.5)
        self.assertEqual(params["sampler"], "euler")
        self.assertEqual(params["seed"], 12345)

    def test_empty_metadata(self):
        params = self.extractor.get_generation_parameters({})
        self.assertEqual(params, {})

    def test_only_relevant_fields_extracted(self):
        metadata = {"steps": 20, "irrelevant_field": "ignored", "workflow": {}}
        params = self.extractor.get_generation_parameters(metadata)
        self.assertIn("steps", params)
        self.assertNotIn("irrelevant_field", params)

    def test_with_workflow_present(self):
        metadata = {"steps": 20, "workflow": {"nodes": []}}
        params = self.extractor.get_generation_parameters(metadata)
        self.assertIn("steps", params)

    def test_dimension_fields(self):
        metadata = {"width": 512, "height": 768, "batch_size": 4}
        params = self.extractor.get_generation_parameters(metadata)
        self.assertEqual(params["width"], 512)
        self.assertEqual(params["height"], 768)
        self.assertEqual(params["batch_size"], 4)


if __name__ == "__main__":
    unittest.main()
