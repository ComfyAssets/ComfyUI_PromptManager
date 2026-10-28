"""Exact inspection-only graph projection and bounded JSON wire encoding."""

import json
import math
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal, Optional

from .inspection_outcomes import MESSAGE_LENGTH, project_resolution
from .validators import validate_tags

if TYPE_CHECKING:
    from .prompt_inspector import InspectionRead

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_LIBRARY_BYTES = 32 * 1024
# The image tag set cap (database/annotations.py MAX_IMAGE_TAGS; pinned equal
# by tests/test_config.py).
MAX_IMAGE_TAGS = 100
_IMAGE_TAG_STATES = frozenset({"current", "none", "unavailable"})
_MAX_SAFE_INTEGER = 2**53 - 1
_RECORD_KEYS = frozenset(
    {"prompt", "workflow", "parameters", "comfyassets_prompt_pairs"}
)
_FORMATS = frozenset({"original-text", "reserialized-json", "invalid", "limited"})
_PROMPT_NODE_KEYS = frozenset({"class_type", "inputs"})
MAX_PROJECTION_DEPTH = 64
# Backstop, not an expected outcome: executable API nodes for a prompt; UI nodes
# including subgraph definitions for a workflow.
GRAPH_NODE_CEILING = 20_000
ProjectionKind = Literal["prompt", "workflow", "carrier"]


class _ProjectionLimit(ValueError):
    """A present graph cannot be projected within inspection limits."""


def _pairs(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class _Nonfinite:
    """A parsed ``NaN``/``Infinity`` literal: never a fact, projected as null."""


_NONFINITE = _Nonfinite()


def _nonfinite(value: str) -> _Nonfinite:
    return _NONFINITE


def _normalize(value: object, depth: int = 1, *, retain: bool = True) -> object:
    """Walk bounded source once; unretained values are only validated as JSON."""
    if isinstance(value, (dict, list)):
        if depth > MAX_PROJECTION_DEPTH:
            raise _ProjectionLimit()
        if isinstance(value, dict):
            result = {} if retain else None
            for key, child in value.items():
                normalized = _normalize(child, depth + 1, retain=retain)
                if retain:
                    result[key] = normalized
        else:
            result = [] if retain else None
            for child in value:
                normalized = _normalize(child, depth + 1, retain=retain)
                if retain:
                    result.append(normalized)
        return result
    if not retain or value is _NONFINITE:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite JSON number")
        # str keeps large exponents compact and never rounds through a float.
        return str(value)
    if type(value) is int and abs(value) > _MAX_SAFE_INTEGER:
        return str(value)
    return value


def _project_prompt(tree: object, depth: int = 1) -> dict:
    """Each API node keeps exactly ``class_type`` and ``inputs``.

    Other node keys (``_meta``, ``is_changed``, future keys) are validated and
    dropped, so a nonfinite value there never reaches the projection.
    """
    if not isinstance(tree, dict):
        raise ValueError("Prompt graph must be an object of nodes")
    if depth + 1 > MAX_PROJECTION_DEPTH:  # the root and its node objects
        raise _ProjectionLimit()
    result = {}
    for node_id, node in tree.items():
        if not isinstance(node, dict):
            result[node_id] = _normalize(node, depth + 1)
            continue
        projected = {}
        for key, child in node.items():
            keep = key in _PROMPT_NODE_KEYS
            normalized = _normalize(child, depth + 2, retain=keep)
            if keep:
                projected[key] = normalized
        result[node_id] = projected
    return result


def _project(tree: object, kind: str, depth: int = 1) -> object:
    if kind == "prompt":
        return _project_prompt(tree, depth)
    if kind == "workflow":
        return _normalize(tree, depth)
    if not isinstance(tree, dict):
        raise ValueError("Malformed carrier")
    result = {}
    for key, member in tree.items():
        if key not in ("prompt", "workflow"):
            _normalize(member, depth + 1, retain=False)
        elif isinstance(member, str):
            result[key] = member  # projected by its own kind when read
        elif key == "prompt" and not isinstance(member, dict):
            result[key] = _normalize(member, depth + 1)
        else:
            result[key] = _project(member, key, depth + 1)
    return result


def _strict_projection(text: str, *, kind: ProjectionKind) -> object:
    """Parse exactly (Decimal, no duplicate keys) and project by record kind."""
    try:
        tree = json.loads(
            text,
            parse_float=Decimal,
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
        return _project(tree, kind)
    except RecursionError:
        raise _ProjectionLimit() from None
    except InvalidOperation:
        raise ValueError("JSON decimal exceeds supported range") from None


def _node_count(value: dict, *, workflow: bool) -> int:
    if not workflow:
        return len(value)
    nodes = value.get("nodes")
    count = len(nodes) if isinstance(nodes, list) else 0
    definitions = value.get("definitions")
    subgraphs = definitions.get("subgraphs") if isinstance(definitions, dict) else None
    for subgraph in subgraphs if isinstance(subgraphs, list) else ():
        inner = subgraph.get("nodes") if isinstance(subgraph, dict) else None
        count += len(inner) if isinstance(inner, list) else 0
    return count


def _graph(value: object, *, workflow: bool = False) -> dict:
    if not isinstance(value, dict):
        return {"status": "malformed", "value": None}
    if _node_count(value, workflow=workflow) > GRAPH_NODE_CEILING:
        return {"status": "limited", "value": None}
    return {"status": "parsed", "value": value}


def project_graph(text: str, kind: Literal["prompt", "workflow"]) -> dict:
    """One record's graph as the inspector projects it: ``{status, value}``.

    ``status`` is ``parsed`` (``value`` is the projected graph the browser
    parser receives), ``limited`` (past the depth or node bound) or
    ``malformed``. Shared with the workspace card so both read one bound.
    """
    try:
        return _graph(_strict_projection(text, kind=kind), workflow=kind == "workflow")
    except _ProjectionLimit:
        return {"status": "limited", "value": None}
    except (ValueError, TypeError, KeyError, OverflowError):
        return {"status": "malformed", "value": None}


def _metadata(inspection: "InspectionRead | None") -> dict:
    formats = (
        {}
        if inspection is None
        else {
            key: value
            for key, value in inspection.record_formats.items()
            if key in _RECORD_KEYS and value in _FORMATS
        }
    )
    if inspection is not None:
        # A record over its read budget has no text: like ``invalid``, raw omits
        # it, and its graph is ``limited`` rather than parsed from nothing.
        for key in inspection.limited_records & _RECORD_KEYS:
            formats[key] = "limited"
    raw = (
        {}
        if inspection is None
        else {
            key: value
            for key, value in inspection.metadata.items()
            if key in _RECORD_KEYS
            and isinstance(value, str)
            and formats.get(key) != "limited"
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
            carrier = _strict_projection(inspection.video_carrier_text, kind="carrier")
        except _ProjectionLimit:
            failed = "limited"
        except (ValueError, TypeError, OverflowError):
            failed = "malformed"
    for key, field in (("prompt", "prompt_graph"), ("workflow", "workflow_graph")):
        graph = {"status": "absent", "value": None}
        if key in raw or key in formats:
            if failed or formats.get(key) == "limited":
                graph["status"] = failed or "limited"
            elif formats.get(key) == "invalid":
                graph["status"] = "malformed"
            elif carrier is None and formats.get(key) == "reserialized-json":
                graph["status"] = "limited"
            elif carrier is None:
                graph = project_graph(raw.get(key), key)
            else:
                try:
                    value = carrier.get(key)
                    if isinstance(value, str):
                        value = _strict_projection(value, kind=key)
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


def _blank(value: str | bytes | None) -> bool:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value is None or not value.strip()


def inherited_category(
    positive_category: str | bytes | None,
    recipe_category: str | bytes | None,
    has_recipe: bool,
) -> tuple:
    """``(value, scope)`` of the category an image's link supplies (#332 MC4).

    The one rule for the inspector chip, the Images category filter and
    History (D39): a linked recipe's own category when it is not blank,
    ``(recipe category, "pair")``; otherwise the positive's value unchanged,
    blank or not, ``(positive category, "positive")``.
    """
    if has_recipe and not _blank(recipe_category):
        return recipe_category, "pair"
    return positive_category, "positive"


def library_metadata(
    positive_category: str | bytes | None,
    positive_tags: list,
    recipe: tuple | None = None,
) -> dict:
    """The panel's saved category and tags, each with the scope it came from.

    D39 (maintainer ruling, 2026-10-21): a recipe-linked image shows the recipe's
    own category and tags when set; otherwise each field separately falls back to
    the linked positive prompt's, the values Set category writes and the Images
    filters match. Tag sets are never merged. ``recipe`` is (category, tags).
    """
    category, category_scope = inherited_category(
        positive_category,
        recipe[0] if recipe is not None else None,
        recipe is not None,
    )
    tags, tags_scope = positive_tags, "positive"
    if recipe is not None and recipe[1]:
        tags, tags_scope = recipe[1], "pair"
    return {
        "category": category,
        "category_scope": category_scope,
        "tags": tags,
        "tags_scope": tags_scope,
    }


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
    for key in ("category_scope", "tags_scope"):
        scope = library.get(key)
        if scope in ("pair", "positive", None):
            result[key] = scope
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
            "category_scope": None,
            "tags_scope": None,
        }
    return result


def _image_tag_name(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip() or "," in value:
        return False
    try:
        return validate_tags([value])
    except ValueError:
        return False


def project_image_tags(facet: object) -> dict:
    """The ``image_tags`` facet as the client accepts it, else unavailable.

    ``current`` carries the byte record's names (at most 100 valid vocabulary
    names); ``none`` and ``unavailable`` carry no names. Anything else, extra
    keys included, is reported as ``unavailable`` rather than passed on.
    """
    unavailable = {"state": "unavailable", "tags": []}
    if not isinstance(facet, dict) or set(facet) != {"state", "tags"}:
        return unavailable
    state, tags = facet["state"], facet["tags"]
    if state not in _IMAGE_TAG_STATES or not isinstance(tags, list):
        return unavailable
    if state != "current" and tags:
        return unavailable
    if len(tags) > MAX_IMAGE_TAGS or not all(_image_tag_name(tag) for tag in tags):
        return unavailable
    return {"state": state, "tags": list(tags)}


def _bounded_message(value: Any) -> Optional[str]:
    """A role line as the client accepts it: 1 to 512 UTF-16 code units."""
    if not isinstance(value, str) or not value:
        return None
    value = value[:MESSAGE_LENGTH]
    while len(value.encode("utf-16-le", "surrogatepass")) > 2 * MESSAGE_LENGTH:
        value = value[:-1]
    return value


def _bounded_messages(prompts: dict) -> dict:
    """Keep a role ``message`` only as bounded text on a non-resolved role."""
    bounded = {}
    for role, side in prompts.items():
        if isinstance(side, dict) and "message" in side:
            side = {key: item for key, item in side.items() if key != "message"}
            message = _bounded_message(prompts[role]["message"])
            if message is not None and side.get("status") != "resolved":
                side["message"] = message
        bounded[role] = side
    return bounded


def _encode_inspection(
    *,
    identity: dict,
    file: dict,
    prompts: dict,
    resolution: dict,
    inspection: "InspectionRead | None",
    library: dict,
    image_tags: dict | None = None,
) -> bytes:
    """Serialize exactly once per budget step, preserving identity and roles."""
    metadata = _metadata(inspection)
    response = {
        "success": True,
        "inspection_version": 1,
        **identity,
        "file": _file_projection(file, inspection),
        "prompts": _bounded_messages(prompts),
        "resolution": project_resolution(resolution),
        "metadata": metadata,
        "library": _library_projection(library),
    }
    if image_tags is not None:
        response["image_tags"] = project_image_tags(image_tags)
    body = _bytes(response)
    # Omission order keeps prompt evidence longest: the workflow graph first,
    # then the prompt graph, then all raw records atomically.
    for field in ("workflow_graph", "prompt_graph"):
        if len(body) <= MAX_RESPONSE_BYTES:
            break
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


def encode_inspection_response(
    *,
    image: dict,
    root_scope: str,
    file: dict,
    prompts: dict,
    resolution: dict,
    inspection: "InspectionRead | None",
    library: dict,
    image_tags: dict | None = None,
) -> bytes:
    """Preserve the existing rooted wire and its bounded metadata projection.

    *image_tags*, when given, adds the validated ``image_tags`` facet (B3).
    """
    return _encode_inspection(
        identity={"root_scope": root_scope, "image": image},
        file=file,
        prompts=prompts,
        resolution=resolution,
        inspection=inspection,
        library=library,
        image_tags=image_tags,
    )


def encode_history_inspection_response(
    *,
    current_output: dict,
    file: dict,
    prompts: dict,
    resolution: dict,
    inspection: "InspectionRead | None",
    library: dict,
) -> bytes:
    """Use occurrence identity with the same current-file evidence projection."""
    return _encode_inspection(
        identity={
            "history_version": 1,
            "history_epoch": current_output["history_epoch"],
            "current_output": current_output,
        },
        file=file,
        prompts=prompts,
        resolution=resolution,
        inspection=inspection,
        library=library,
    )
