"""Advanced ComfyUI PNG metadata parsing utilities.

This module focuses on extracting generation details from ComfyUI
"prompt" graphs and workflow exports embedded in PNG metadata. It
combines heuristics from prior experimental parsers in ``metadata/``
with a robust, type-hinted implementation suitable for production use.

First-class support for PromptManager nodes:
- PromptManager: Standard node with single text field
- PromptManagerV2: Combined positive/negative with text and negative_text fields
- PromptManagerPositive: Dedicated positive prompt node
- PromptManagerNegative: Dedicated negative prompt node

All PromptManager nodes are recognized as text encoders and properly
extract prompt data based on their specific field structures.
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union


TextChunkMap = Dict[str, str]
PromptGraph = Dict[str, Dict[str, Any]]
WorkflowGraph = Dict[str, Any]


@dataclass(eq=True, frozen=True)
class LoraInfo:
    """Information about a single LoRA usage within a workflow."""

    name: str
    strength: Optional[float] = None


@dataclass
class ParsedComfyMetadata:
    """Structured results produced by :class:`ComfyMetadataParser`."""

    positive_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    loras: List[LoraInfo] = field(default_factory=list)
    cfg_scale: Optional[float] = None
    steps: Optional[int] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None
    seed: Optional[int] = None
    denoise: Optional[float] = None
    clip_skip: Optional[int] = None
    workflow: Optional[WorkflowGraph] = None
    prompt: Optional[PromptGraph] = None
    raw_chunks: TextChunkMap = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""

        result: Dict[str, Any] = {
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "model": self.model,
            "loras": [lora.__dict__ for lora in self.loras],
            "cfg_scale": self.cfg_scale,
            "steps": self.steps,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "seed": self.seed,
            "denoise": self.denoise,
            "clip_skip": self.clip_skip,
            "workflow": self.workflow,
            "prompt": self.prompt,
            "raw_chunks": self.raw_chunks,
            "errors": self.errors,
            "extras": self.extras,
        }
        return result


class ComfyMetadataParser:
    """Parse ComfyUI metadata embedded inside PNG files.

    The parser works in three phases:

    1. Read tEXt/iTXt/zTXt chunks from a PNG file (optional when metadata is
       already supplied as dictionaries).
    2. Parse the ``prompt`` graph and workflow JSON, handling both string and
       dictionary inputs.
    3. Walk through the prompt graph, resolving node references to collect the
       desired generation parameters (prompts, model, sampler, etc.).
    """

    #: Node class types that are likely to contain model checkpoints
    MODEL_NODE_TYPES: Tuple[str, ...] = (
        "CheckpointLoader",
        "CheckpointLoaderSimple",
        "Checkpoint Loader",
        "UNETLoader",
        "UNETLoaderGGUF",
        "ModelMerge",
        "UNETLoaderGGUFAdvanced",
        "ModelMergeSimple",
    )

    #: Node class types that denote LoRA usage
    LORA_NODE_KEYWORDS: Tuple[str, ...] = (
        "LoraLoader",
        "LoRALoader",
        "LoRA",
    )

    #: Node class types considered text encoders
    TEXT_NODE_KEYWORDS: Tuple[str, ...] = (
        "CLIPTextEncode",
        "CLIPTextEncodeSDXL",
        "CLIPTextEncodeFlux",
        "PromptManager",
        "PromptManagerPositive",
        "PromptManagerNegative",
        "PromptManagerV2",
        "PromptManagerText",
        "Text",
        "String",
    )

    #: Node class types that perform sampling
    SAMPLER_NODE_KEYWORDS: Tuple[str, ...] = (
        "KSampler",
        "KSamplerAdvanced",
        "SamplerCustom",
        "SamplerCustomAdvanced",
        "ttN KSampler",
    )

    #: Node class types which can control CLIP skip behaviour
    CLIP_SKIP_NODE_KEYWORDS: Tuple[str, ...] = (
        "CLIPSetLastLayer",
        "CLIPLoader",
        "DualCLIPLoader",
        "CLIPLoaderSDXL",
    )

    #: Keywords found in negative prompts
    NEGATIVE_PROMPT_CUES: Tuple[str, ...] = (
        "bad anatomy",
        "bad hands",
        "worst quality",
        "low quality",
        "blurry",
        "deformed",
        "ugly",
        "jpeg artifacts",
        "mutation",
    )

    #: Keywords suggesting positive prompts
    POSITIVE_PROMPT_CUES: Tuple[str, ...] = (
        "masterpiece",
        "best quality",
        "ultra detailed",
        "cinematic",
        "beautiful",
        "highres",
        "award winning",
    )

    #: PNG signature used to validate input files
    PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

    def parse_file(self, file_path: Union[str, Path]) -> ParsedComfyMetadata:
        """Parse a PNG file on disk.

        Args:
            file_path: Path to the PNG image.

        Returns:
            ParsedComfyMetadata with extracted fields.
        """

        path = Path(file_path)
        text_chunks = self._read_text_chunks(path)

        metadata: Dict[str, Any] = {
            "text_chunks": text_chunks,
        }

        # Preserve raw JSON strings so downstream callers get consistent data
        if "workflow" in text_chunks:
            metadata["workflow"] = text_chunks["workflow"]
        if "prompt" in text_chunks:
            metadata["prompt"] = text_chunks["prompt"]

        return self.parse_metadata(metadata)

    # ------------------------------------------------------------------
    # Public entry point when metadata is already extracted
    # ------------------------------------------------------------------
    def parse_metadata(self, metadata: Dict[str, Any]) -> ParsedComfyMetadata:
        """Parse already extracted metadata contents.

        ``metadata`` may include any of the following keys:

        ``prompt``
            The ComfyUI prompt graph, either as a dictionary or JSON string.
        ``workflow``
            The ComfyUI workflow export ("UI graph"), either dict or JSON.
        ``text_chunks``
            Raw PNG text chunk values, used to populate ``raw_chunks`` in the
            result for diagnostic purposes.
        """

        result = ParsedComfyMetadata()
        raw_chunks = metadata.get("text_chunks") or {}
        prompt_payload = metadata.get("prompt")
        workflow_payload = metadata.get("workflow")

        if isinstance(raw_chunks, dict):
            normalised_chunks = {str(k): str(v) for k, v in raw_chunks.items()}
            result.raw_chunks = normalised_chunks
            prompt_payload = prompt_payload or normalised_chunks.get("prompt")
            workflow_payload = workflow_payload or normalised_chunks.get("workflow")

        prompt_data = self._ensure_json_dict(prompt_payload)
        if isinstance(prompt_data, dict):
            result.prompt = prompt_data
        elif prompt_payload:
            result.errors.append("Failed to parse prompt JSON")

        workflow_data = self._ensure_json_dict(workflow_payload)
        if isinstance(workflow_data, dict):
            result.workflow = workflow_data
        elif workflow_payload:
            result.errors.append("Failed to parse workflow JSON")

        if result.prompt:
            self._extract_from_prompt_graph(result, result.prompt)

        # Only use workflow extraction as a fallback when we don't have prompts from the prompt graph
        # This prevents the workflow from overwriting correctly parsed prompts
        if (not result.positive_prompt or not result.negative_prompt) and result.workflow:
            # Store current values to avoid overwriting good data with bad
            original_positive = result.positive_prompt
            original_negative = result.negative_prompt

            self._extract_from_workflow(result, result.workflow)

            # Restore original values if they were better (longer/more meaningful)
            if original_positive and len(original_positive) > len(result.positive_prompt or ""):
                result.positive_prompt = original_positive
            if original_negative and len(original_negative) > len(result.negative_prompt or ""):
                result.negative_prompt = original_negative

        return result

    # ------------------------------------------------------------------
    # PNG chunk helpers
    # ------------------------------------------------------------------
    def _read_text_chunks(self, path: Path) -> TextChunkMap:
        """Read PNG text chunks (tEXt, zTXt, iTXt) from *path*."""

        text_chunks: TextChunkMap = {}
        if not path.exists():
            return text_chunks

        with path.open("rb") as stream:
            signature = stream.read(8)
            if signature != self.PNG_SIGNATURE:
                return text_chunks

            while True:
                header = stream.read(8)
                if len(header) != 8:
                    break

                length = struct.unpack(">I", header[:4])[0]
                chunk_type = header[4:8]
                data = stream.read(length)
                stream.read(4)  # skip CRC

                if chunk_type in {b"tEXt", b"zTXt", b"iTXt"}:
                    parsed = self._decode_chunk(chunk_type, data)
                    if parsed:
                        text_chunks[parsed[0]] = parsed[1]
                elif chunk_type == b"IEND":
                    break

        return text_chunks

    def _decode_chunk(self, chunk_type: bytes, data: bytes) -> Optional[Tuple[str, str]]:
        """Decode PNG text chunk payload."""

        try:
            null_pos = data.index(b"\x00")
        except ValueError:
            return None

        key = data[:null_pos].decode("latin-1", errors="ignore")
        payload = data[null_pos + 1 :]

        if chunk_type == b"tEXt":
            value = payload.decode("latin-1", errors="ignore")
            return key, value

        if chunk_type == b"zTXt":
            if not payload:
                return key, ""
            compression_method = payload[0]
            if compression_method != 0:
                return None
            try:
                decompressed = zlib.decompress(payload[1:])
                return key, decompressed.decode("utf-8", errors="ignore")
            except zlib.error:
                return None

        if chunk_type == b"iTXt":
            if len(payload) < 2:
                return None
            compression_flag = payload[0]
            payload = payload[2:]  # skip compression method

            # Skip language tag and translated keyword
            for _ in range(2):
                try:
                    terminator = payload.index(b"\x00")
                except ValueError:
                    return None
                payload = payload[terminator + 1 :]

            if compression_flag == 1:
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    return None

            value = payload.decode("utf-8", errors="ignore")
            return key, value

        return None

    # ------------------------------------------------------------------
    # Prompt graph extraction
    # ------------------------------------------------------------------
    def _extract_from_prompt_graph(self, result: ParsedComfyMetadata, prompt: PromptGraph) -> None:
        node_map = {str(node_id): node for node_id, node in prompt.items() if isinstance(node, dict)}
        if not node_map:
            return

        sampler_links = self._collect_sampler_links(node_map)
        reference_labels = self._index_prompt_links(sampler_links)

        for node_id, node in node_map.items():
            class_type = str(node.get("class_type", ""))
            inputs = node.get("inputs", {}) or {}

            if self._is_model_node(class_type):
                model_name = self._resolve_string(inputs.get("ckpt_name") or inputs.get("model_name") or inputs.get("checkpoint"), node_map)
                if not model_name:
                    model_name = self._resolve_string(inputs.get("unet_name"), node_map)
                if model_name and not result.model:
                    result.model = model_name

            if self._contains_lora_keyword(class_type):
                self._ingest_lora(inputs, node_map, result)

            if self._contains_text_keyword(class_type):
                # First-class support for ALL PromptManager nodes
                if "PromptManager" in class_type:
                    # Handle different PromptManager variants
                    if "PromptManagerV2" in class_type:
                        # PromptManagerV2: has both text and negative_text fields
                        positive_text = self._resolve_text(inputs.get("text"), node_map, inputs)
                        if positive_text:
                            result.positive_prompt = positive_text

                        negative_text = self._resolve_text(inputs.get("negative_text"), node_map, inputs)
                        if negative_text:
                            result.negative_prompt = negative_text

                    elif "PromptManagerPositive" in class_type:
                        # PromptManagerPositive: only handles positive prompts
                        text_value = self._resolve_text(inputs.get("text"), node_map, inputs)
                        if text_value and not result.positive_prompt:
                            result.positive_prompt = text_value

                    elif "PromptManagerNegative" in class_type:
                        # PromptManagerNegative: only handles negative prompts
                        text_value = self._resolve_text(inputs.get("text"), node_map, inputs)
                        if text_value and not result.negative_prompt:
                            result.negative_prompt = text_value

                    else:
                        # Standard PromptManager: single text field, determine type by connections or title
                        text_value = self._resolve_text(inputs.get("text"), node_map, inputs)
                        if text_value:
                            # Check if this node is connected as positive or negative
                            label = reference_labels.get(node_id)
                            if not label:
                                # Check node title or metadata for hints
                                meta = node.get("_meta", {}) or {}
                                title = str(meta.get("title", "")).lower()
                                if "negative" in title:
                                    label = "negative"
                                elif "positive" in title:
                                    label = "positive"
                                else:
                                    # Default to positive if we don't have one yet
                                    label = "positive" if not result.positive_prompt else "negative"

                            if label == "positive" and not result.positive_prompt:
                                result.positive_prompt = text_value
                            elif label == "negative" and not result.negative_prompt:
                                result.negative_prompt = text_value
                else:
                    # Standard text node handling
                    text_value = self._resolve_text(inputs.get("text"), node_map, inputs)
                    if not text_value:
                        text_value = self._resolve_text_from_any(inputs, node_map)
                    if text_value:
                        label = reference_labels.get(node_id)
                        if not label:
                            label = self._classify_prompt(node, text_value, result)
                        self._assign_prompt_text(label, text_value, result)

            if self._contains_sampler_keyword(class_type):
                self._ingest_sampler_settings(inputs, node_map, result)

            if self._contains_clip_skip_keyword(class_type):
                clip_value = inputs.get("stop_at_clip_layer") or inputs.get("clip_skip")
                resolved = self._resolve_numeric(clip_value, node_map)
                if resolved is not None:
                    try:
                        result.clip_skip = int(resolved)
                    except (TypeError, ValueError):
                        pass

        # Ensure LoRA list is deterministic for testing/stability
        if result.loras:
            result.loras = self._deduplicate_loras(result.loras)

    def _collect_sampler_links(self, node_map: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        sampler_nodes: Dict[str, Dict[str, Any]] = {}
        for node_id, node in node_map.items():
            class_type = str(node.get("class_type", ""))
            if self._contains_sampler_keyword(class_type):
                sampler_nodes[node_id] = node
        return sampler_nodes

    def _index_prompt_links(self, sampler_nodes: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        labels: Dict[str, str] = {}
        for sampler in sampler_nodes.values():
            inputs = sampler.get("inputs", {}) or {}
            for field, label in (("positive", "positive"), ("negative", "negative")):
                link = inputs.get(field)
                if isinstance(link, list) and link:
                    link_node_id = str(link[0])
                    labels.setdefault(link_node_id, label)
        return labels

    def _is_model_node(self, class_type: str) -> bool:
        base = class_type.split("::")[0]
        return any(keyword in base for keyword in self.MODEL_NODE_TYPES)

    def _contains_lora_keyword(self, class_type: str) -> bool:
        base = class_type.split("::")[0]
        return any(keyword in base for keyword in self.LORA_NODE_KEYWORDS)

    def _contains_text_keyword(self, class_type: str) -> bool:
        base = class_type.split("::")[0]
        return any(keyword in base for keyword in self.TEXT_NODE_KEYWORDS)

    def _contains_sampler_keyword(self, class_type: str) -> bool:
        base = class_type.split("::")[0]
        return any(keyword in base for keyword in self.SAMPLER_NODE_KEYWORDS)

    def _contains_clip_skip_keyword(self, class_type: str) -> bool:
        base = class_type.split("::")[0]
        return any(keyword in base for keyword in self.CLIP_SKIP_NODE_KEYWORDS)

    def _ingest_lora(self, inputs: Dict[str, Any], node_map: Dict[str, Dict[str, Any]], result: ParsedComfyMetadata) -> None:
        lora_name_raw = inputs.get("lora_name") or inputs.get("lora") or inputs.get("model") or inputs.get("file")
        lora_name = self._resolve_string(lora_name_raw, node_map)
        if not lora_name:
            return

        strength_raw = inputs.get("strength_model") or inputs.get("strength") or inputs.get("weight")
        strength_value = self._resolve_numeric(strength_raw, node_map)
        lora_info = LoraInfo(name=lora_name, strength=strength_value if strength_value is not None else None)
        result.loras.append(lora_info)

    def _ingest_sampler_settings(self, inputs: Dict[str, Any], node_map: Dict[str, Dict[str, Any]], result: ParsedComfyMetadata) -> None:
        cfg_value = self._resolve_numeric(inputs.get("cfg") or inputs.get("cfg_scale"), node_map)
        if cfg_value is not None:
            result.cfg_scale = float(cfg_value)

        steps_value = self._resolve_numeric(inputs.get("steps"), node_map)
        if steps_value is not None:
            try:
                result.steps = int(steps_value)
            except (TypeError, ValueError):
                pass

        sampler_value = self._resolve_string(inputs.get("sampler_name") or inputs.get("sampler"), node_map)
        if sampler_value:
            result.sampler = sampler_value

        scheduler_value = self._resolve_string(inputs.get("scheduler"), node_map)
        if scheduler_value:
            result.scheduler = scheduler_value

        seed_value = self._resolve_numeric(inputs.get("seed") or inputs.get("noise_seed"), node_map)
        if seed_value is not None:
            try:
                result.seed = int(seed_value)
            except (TypeError, ValueError):
                result.seed = seed_value  # type: ignore[assignment]

        denoise_value = self._resolve_numeric(inputs.get("denoise") or inputs.get("denoising_strength"), node_map)
        if denoise_value is not None:
            result.denoise = float(denoise_value)

    def _resolve_text(self, value: Any, node_map: Dict[str, Dict[str, Any]], inputs: Dict[str, Any]) -> Optional[str]:
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            ref_node_id = str(value[0])
            if ref_node_id in node_map:
                ref_node = node_map[ref_node_id]
                ref_inputs = ref_node.get("inputs", {}) or {}
                for candidate_key in (
                    "text",
                    "string",
                    "prompt",
                    "value",
                    "content",
                    "result",
                    "multiline",
                    "wildcard",
                ):
                    if candidate_key in ref_inputs:
                        resolved = self._resolve_text(ref_inputs[candidate_key], node_map, ref_inputs)
                        if resolved:
                            return resolved
        return None

    def _resolve_text_from_any(self, inputs: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> Optional[str]:
        for key, value in inputs.items():
            if isinstance(value, str) and len(value) > 3:
                return value
            resolved = self._resolve_text(value, node_map, inputs)
            if resolved:
                return resolved
        return None

    def _classify_prompt(self, node: Dict[str, Any], text: str, result: ParsedComfyMetadata) -> str:
        meta = node.get("_meta", {}) or {}
        title = str(meta.get("title", "")).lower()
        class_type = str(node.get("class_type", "")).lower()
        text_lower = text.lower()

        if "negative" in title or "negative" in class_type:
            return "negative"

        if any(cue in text_lower for cue in self.NEGATIVE_PROMPT_CUES):
            return "negative"

        if not result.positive_prompt and ("positive" in title or "positive" in class_type):
            return "positive"

        if any(cue in text_lower for cue in self.POSITIVE_PROMPT_CUES):
            return "positive"

        return "positive" if not result.positive_prompt else "negative"

    def _assign_prompt_text(self, label: Optional[str], text: str, result: ParsedComfyMetadata) -> None:
        if not text:
            return

        label = label or ("positive" if not result.positive_prompt else "negative")
        if label == "positive" and not result.positive_prompt:
            result.positive_prompt = text
        elif label == "negative" and not result.negative_prompt and text != result.positive_prompt:
            result.negative_prompt = text

    def _deduplicate_loras(self, loras: Iterable[LoraInfo]) -> List[LoraInfo]:
        seen: Set[str] = set()
        deduped: List[LoraInfo] = []
        for lora in loras:
            if lora.name not in seen:
                deduped.append(lora)
                seen.add(lora.name)
        return deduped

    # ------------------------------------------------------------------
    # Workflow fallback
    # ------------------------------------------------------------------
    def _extract_from_workflow(self, result: ParsedComfyMetadata, workflow: WorkflowGraph) -> None:
        nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
        if not isinstance(nodes, list):
            return

        for node in nodes:
            if not isinstance(node, dict):
                continue

            node_type = str(node.get("type", ""))
            title = str(node.get("title", "")).lower()

            # Only consider nodes that are likely to contain prompts
            # Skip utility nodes like StringListToCombo, etc.
            if not any(keyword in node_type for keyword in self.TEXT_NODE_KEYWORDS):
                # Also check if title suggests it's a prompt node
                if not any(word in title for word in ["prompt", "text", "clip", "positive", "negative"]):
                    continue

            widgets = node.get("widgets_values", []) or []
            for widget in widgets:
                if not isinstance(widget, str):
                    continue
                text = widget

                # Skip very short strings that are likely not prompts
                if len(text) < 3:
                    continue

                text_lower = text.lower()
                is_negative = "negative" in title or any(cue in text_lower for cue in self.NEGATIVE_PROMPT_CUES)
                if not result.positive_prompt and ("positive" in title or not is_negative):
                    result.positive_prompt = text
                elif not result.negative_prompt and ("negative" in title or is_negative):
                    if text != result.positive_prompt:
                        result.negative_prompt = text

                if result.positive_prompt and result.negative_prompt:
                    return

    # ------------------------------------------------------------------
    # Value resolution helpers
    # ------------------------------------------------------------------
    def _resolve_string(self, value: Any, node_map: Dict[str, Dict[str, Any]]) -> Optional[str]:
        resolved = self._resolve_value(value, node_map)
        if isinstance(resolved, str):
            return resolved
        if isinstance(value, str):
            return value
        return None

    def _resolve_numeric(self, value: Any, node_map: Dict[str, Dict[str, Any]]) -> Optional[float]:
        resolved = self._resolve_value(value, node_map)
        if isinstance(resolved, (int, float)):
            return float(resolved)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            if isinstance(resolved, str) and resolved.strip():
                return float(resolved)
        except ValueError:
            return None
        return None

    def _resolve_value(self, value: Any, node_map: Dict[str, Dict[str, Any]], *, visited: Optional[Set[str]] = None) -> Any:
        if visited is None:
            visited = set()

        if isinstance(value, list) and value:
            ref_node_id = str(value[0])
            if ref_node_id in visited:
                return None
            visited.add(ref_node_id)
            ref_node = node_map.get(ref_node_id)
            if not ref_node:
                return None
            ref_inputs = ref_node.get("inputs", {}) or {}
            for key in (
                "value",
                "val",
                "float",
                "int",
                "number",
                "seed",
                "noise_seed",
                "cfg",
                "cfg_scale",
                "steps",
                "sampler_name",
                "scheduler",
                "denoise",
                "strength_model",
                "strength",
                "weight",
                "text",
                "string",
                "prompt",
                "result",
                "output",
            ):
                if key in ref_inputs:
                    candidate = ref_inputs[key]
                    resolved = self._resolve_value(candidate, node_map, visited=visited)
                    if resolved is not None:
                        return resolved
            return None

        return value

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------
    def _ensure_json_dict(self, payload: Any) -> Optional[Dict[str, Any]]:
        if payload is None:
            return None
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str) and payload.strip():
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
        return None
