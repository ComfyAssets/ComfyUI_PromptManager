import types

import pytest

from src.metadata.comfy_parser import ParsedComfyMetadata, LoraInfo
from utils import metadata_extractor


@pytest.fixture(autouse=True)
def restore_comfy_parser(monkeypatch):
    original = metadata_extractor._COMFY_PARSER
    yield
    monkeypatch.setattr(metadata_extractor, "_COMFY_PARSER", original)


def test_merge_comfy_metadata_injects_structured_fields(monkeypatch):
    parsed = ParsedComfyMetadata(
        positive_prompt="{positive}",
        negative_prompt="{negative}",
        model="dreamy.safetensors",
        loras=[LoraInfo(name="detail.safetensors", strength=0.7)],
        cfg_scale=7.5,
        steps=30,
        sampler="dpmpp_2m",
        scheduler="karras",
        seed=1234,
        denoise=0.42,
        clip_skip=2,
        workflow={"nodes": []},
        prompt={"1": {"class_type": "CLIPTextEncode"}},
        raw_chunks={"prompt": "{}"},
        errors=["minor warning"],
    )

    fake_parser = types.SimpleNamespace(parse_metadata=lambda payload: parsed)
    monkeypatch.setattr(metadata_extractor, "_COMFY_PARSER", fake_parser)

    payload = {
        "prompt": "{}",
        "workflow": "{}",
        "positive_prompt": None,  # ensure new values replace blanks
    }

    metadata_extractor.MetadataExtractor._merge_comfy_metadata(payload)

    comfy = payload["comfy_parsed"]
    assert comfy["positive_prompt"] == "{positive}"
    assert comfy["negative_prompt"] == "{negative}"
    assert comfy["model"] == "dreamy.safetensors"
    assert comfy["loras"] == [{"name": "detail.safetensors", "strength": 0.7}]
    assert payload["positive_prompt"] == "{positive}"
    assert payload["negative_prompt"] == "{negative}"
    assert payload["model"] == "dreamy.safetensors"
    assert payload["cfg_scale"] == 7.5
    assert payload["steps"] == 30
    assert payload["sampler"] == "dpmpp_2m"
    assert payload["scheduler"] == "karras"
    assert payload["seed"] == 1234
    assert payload["denoising_strength"] == 0.42
    assert payload["clip_skip"] == 2
    assert payload["loras"] == [{"name": "detail.safetensors", "strength": 0.7}]
    assert payload["comfy_raw_chunks"] == {"prompt": "{}"}
    assert payload["comfy_errors"] == ["minor warning"]


def test_merge_comfy_metadata_preserves_existing_values(monkeypatch):
    parsed = ParsedComfyMetadata(
        positive_prompt="from parser",
        negative_prompt="from parser",
        model="parser-model",
        cfg_scale=6.5,
        steps=25,
    )

    fake_parser = types.SimpleNamespace(parse_metadata=lambda payload: parsed)
    monkeypatch.setattr(metadata_extractor, "_COMFY_PARSER", fake_parser)

    payload = {
        "positive_prompt": "pre-existing",
        "model": "custom",
        "cfg_scale": 4.0,
        "steps": 12,
    }

    metadata_extractor.MetadataExtractor._merge_comfy_metadata(payload)

    assert payload["positive_prompt"] == "pre-existing"
    assert payload["model"] == "custom"
    assert payload["cfg_scale"] == 4.0
    assert payload["steps"] == 12
