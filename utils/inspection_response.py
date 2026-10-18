"""Exact inspection-only graph projection and bounded JSON wire encoding."""

import json
import math
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .prompt_inspector import InspectionRead

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_LIBRARY_BYTES = 32 * 1024
_MAX_SAFE_INTEGER = 2**53 - 1
_RECORD_KEYS = frozenset(
    {"prompt", "workflow", "parameters", "comfyassets_prompt_pairs"}
)
_FORMATS = frozenset({"original-text", "reserialized-json", "invalid"})


class _ProjectionLimit(ValueError):
    """A present graph cannot be projected within inspection limits."""


def _pairs(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _normalize(
    value: object, depth: int = 1, *, retain: bool = True, carrier: bool = False
) -> object:
    """Walk bounded source once; unrelated carrier fields are only validated."""
    if isinstance(value, (dict, list)):
        if depth > 64:
            raise _ProjectionLimit()
        if isinstance(value, dict):
            result = {} if retain else None
            for key, child in value.items():
                keep = retain and (not carrier or key in ("prompt", "workflow"))
                normalized = _normalize(child, depth + 1, retain=keep)
                if keep:
                    result[key] = normalized
        else:
            result = [] if retain else None
            for child in value:
                normalized = _normalize(child, depth + 1, retain=retain)
                if retain:
                    result.append(normalized)
        return result
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite JSON number")
        # str keeps large exponents compact and never rounds through a float.
        return str(value) if retain else None
    if type(value) is int and abs(value) > _MAX_SAFE_INTEGER:
        return str(value) if retain else None
    return value if retain else None


def _strict_projection(text: str, *, carrier: bool = False) -> object:
    try:
        tree = json.loads(
            text,
            parse_float=Decimal,
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
        if carrier and not isinstance(tree, dict):
            raise ValueError("Malformed carrier")
        return _normalize(tree, carrier=carrier)
    except RecursionError:
        raise _ProjectionLimit() from None
    except InvalidOperation:
        raise ValueError("JSON decimal exceeds supported range") from None


def _graph(value: object, *, workflow: bool = False) -> dict:
    if not isinstance(value, dict):
        return {"status": "malformed", "value": None}
    nodes = value.get("nodes") if workflow else value
    if isinstance(nodes, (dict, list)) and len(nodes) > 500:
        return {"status": "limited", "value": None}
    return {"status": "parsed", "value": value}


def _metadata(inspection: "InspectionRead | None") -> dict:
    raw = (
        {}
        if inspection is None
        else {
            key: value
            for key, value in inspection.metadata.items()
            if key in _RECORD_KEYS and isinstance(value, str)
        }
    )
    formats = (
        {}
        if inspection is None
        else {
            key: value
            for key, value in inspection.record_formats.items()
            if key in _RECORD_KEYS and value in _FORMATS
        }
    )
    result = {
        "status": (
            "unreadable"
            if inspection is None
            else ("available" if raw or formats else "absent")
        ),
        "omitted_reason": None,
        "raw": raw,
        "record_formats": formats,
        "parameters": raw.get("parameters"),
    }
    carrier, failed = None, None
    if inspection is not None and inspection.video_carrier_text is not None:
        try:
            carrier = _strict_projection(inspection.video_carrier_text, carrier=True)
        except _ProjectionLimit:
            failed = "limited"
        except (ValueError, TypeError, OverflowError):
            failed = "malformed"
    for key, field in (("prompt", "prompt_graph"), ("workflow", "workflow_graph")):
        graph = {"status": "absent", "value": None}
        if key in raw or key in formats:
            if failed:
                graph["status"] = failed
            elif formats.get(key) == "invalid":
                graph["status"] = "malformed"
            else:
                try:
                    if carrier is not None:
                        value = carrier.get(key)
                        if isinstance(value, str):
                            value = _strict_projection(value)
                    elif formats.get(key) == "reserialized-json":
                        raise _ProjectionLimit()
                    else:
                        value = _strict_projection(raw[key])
                    graph = _graph(value, workflow=key == "workflow")
                except _ProjectionLimit:
                    graph["status"] = "limited"
                except (ValueError, TypeError, KeyError, OverflowError):
                    graph["status"] = "malformed"
        result[field] = graph
    return result


def _bytes(value: dict) -> bytes:
    # JSON preserves isolated surrogate evidence through escaped code units.
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8", "backslashreplace")


def _finite(value: object, *, positive: bool = False) -> bool:
    try:
        return (
            type(value) in (int, float)
            and math.isfinite(value)
            and (value > 0 if positive else value >= 0)
        )
    except OverflowError:
        return False


def _file_projection(file: dict, inspection: "InspectionRead | None") -> dict:
    source = dict(file)
    if inspection is not None:
        source.update(
            format=inspection.format, width=inspection.width, height=inspection.height
        )
    result = {"observation": "current-file"}
    for key in ("source_label", "extension", "format"):
        value = source.get(key)
        if value is None and key == "format":
            result[key] = None
        elif isinstance(value, str):
            result[key] = value
    if source.get("media_type") in ("image", "video"):
        result["media_type"] = source["media_type"]
    for key in ("size", "modified_time", "width", "height"):
        if key not in source:
            continue
        value = source[key]
        if value is None or _finite(value, positive=key in ("width", "height")):
            if key not in ("width", "height") or value is None or type(value) is int:
                result[key] = value
    return result


def _library_projection(library: dict) -> dict:
    status = library.get("status")
    result = {
        "status": (
            status
            if status in ("linked", "none", "ambiguous", "unavailable", "limited")
            else "unavailable"
        ),
        "scope": "saved-library",
    }
    for key in ("positive_id", "pair_id"):
        value = library.get(key)
        if value is None or type(value) is int and 0 < value <= _MAX_SAFE_INTEGER:
            result[key] = value
    for key in ("title", "category"):
        value = library.get(key)
        if value is None or isinstance(value, str):
            result[key] = value
    scope = library.get("metadata_scope")
    if scope in ("pair", "positive", None):
        result["metadata_scope"] = scope
    tags = library.get("tags")
    result["tags"] = (
        [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []
    )
    if len(_bytes(result)) > MAX_LIBRARY_BYTES:
        return {
            "status": "limited",
            "scope": "saved-library",
            "positive_id": None,
            "pair_id": None,
            "title": None,
            "category": None,
            "tags": [],
            "metadata_scope": None,
        }
    return result


def encode_inspection_response(
    *,
    image: dict,
    root_scope: str,
    file: dict,
    prompts: dict,
    resolution: dict,
    inspection: "InspectionRead | None",
    library: dict,
) -> bytes:
    """Serialize exactly once per budget step, preserving identity and roles."""
    metadata = _metadata(inspection)
    response = {
        "success": True,
        "inspection_version": 1,
        "root_scope": root_scope,
        "image": image,
        "file": _file_projection(file, inspection),
        "prompts": prompts,
        "resolution": resolution,
        "metadata": metadata,
        "library": _library_projection(library),
    }
    body = _bytes(response)
    if len(body) > MAX_RESPONSE_BYTES:
        for field in ("prompt_graph", "workflow_graph"):
            if metadata[field]["status"] != "absent":
                metadata[field] = {"status": "limited", "value": None}
        body = _bytes(response)
    if len(body) > MAX_RESPONSE_BYTES:
        metadata.update(
            raw={},
            parameters=None,
            status="limited",
            omitted_reason="response-size-limit",
        )
        body = _bytes(response)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Inspection response exceeds limits")
    return body
