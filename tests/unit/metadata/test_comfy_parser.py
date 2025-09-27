import json
from dataclasses import asdict

import pytest

from src.metadata.comfy_parser import ComfyMetadataParser, LoraInfo


@pytest.fixture()
def parser() -> ComfyMetadataParser:
    return ComfyMetadataParser()


def test_extracts_core_fields_from_prompt_graph(parser: ComfyMetadataParser) -> None:
    prompt_graph = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "dreamshaper.safetensors"},
            "_meta": {"title": "Checkpoint Loader"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "masterpiece, cinematic lighting"},
            "_meta": {"title": "Positive Prompt"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "bad anatomy, blurry"},
            "_meta": {"title": "Negative Prompt"},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "cfg": 7.5,
                "steps": 30,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "seed": 987654321,
            },
            "_meta": {"title": "KSampler"},
        },
        "5": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "epic-style.safetensors",
                "strength_model": 0.6,
            },
            "_meta": {"title": "Load LoRA"},
        },
    }

    result = parser.parse_metadata({"prompt": prompt_graph})

    assert result.positive_prompt == "masterpiece, cinematic lighting"
    assert result.negative_prompt == "bad anatomy, blurry"
    assert result.model == "dreamshaper.safetensors"
    assert result.cfg_scale == pytest.approx(7.5)
    assert result.steps == 30
    assert result.sampler == "dpmpp_2m"
    assert result.scheduler == "karras"
    assert result.seed == 987654321
    assert result.loras == [LoraInfo(name="epic-style.safetensors", strength=0.6)]


def test_resolves_referenced_values_and_clip_skip(parser: ComfyMetadataParser) -> None:
    prompt_graph = {
        "10": {
            "class_type": "PrimitiveNode",
            "inputs": {"value": 12},
        },
        "11": {
            "class_type": "PrimitiveNode",
            "inputs": {"value": 3},
        },
        "12": {
            "class_type": "CLIPSetLastLayer",
            "inputs": {"stop_at_clip_layer": 2},
        },
        "13": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "masterpiece, award winning"},
        },
        "14": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "bad anatomy, ugly"},
        },
        "15": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["13", 0],
                "negative": ["14", 0],
                "cfg": ["10", 0],
                "steps": ["10", 0],
                "seed": ["11", 0],
                "sampler_name": "euler",
            },
        },
    }

    result = parser.parse_metadata({"prompt": prompt_graph})

    assert result.cfg_scale == 12
    assert result.steps == 12
    assert result.seed == 3
    assert result.clip_skip == 2


def test_falls_back_to_workflow_when_prompt_missing(parser: ComfyMetadataParser) -> None:
    workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "PrimitiveNode",
                "title": "positive",
                "widgets_values": ["sunset over the ocean, golden hour"],
            },
            {
                "id": 2,
                "type": "PrimitiveNode",
                "title": "negative",
                "widgets_values": ["low quality, blurry"],
            },
        ]
    }

    result = parser.parse_metadata({"workflow": workflow})

    assert result.positive_prompt == "sunset over the ocean, golden hour"
    assert result.negative_prompt == "low quality, blurry"


def test_deduplicates_loras_and_handles_strength_defaults(parser: ComfyMetadataParser) -> None:
    prompt_graph = {
        "20": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style-a.safetensors", "strength_model": 0.5},
        },
        "21": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style-a.safetensors"},
        },
        "22": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style-b.safetensors", "strength_model": ["23", 0]},
        },
        "23": {
            "class_type": "PrimitiveNode",
            "inputs": {"value": 0.8},
        },
    }

    result = parser.parse_metadata({"prompt": prompt_graph})

    assert result.loras == [
        LoraInfo(name="style-a.safetensors", strength=0.5),
        LoraInfo(name="style-b.safetensors", strength=pytest.approx(0.8)),
    ]


def test_includes_raw_chunks_and_errors(parser: ComfyMetadataParser) -> None:
    metadata = {
        "text_chunks": {"workflow": "not json"},
    }

    result = parser.parse_metadata(metadata)

    assert result.workflow is None
    assert result.errors
    assert "workflow" in result.raw_chunks
