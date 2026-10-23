"""Generation parameters traced from ComfyUI metadata: the browser parser in Python.

A port of ``extractGenerationParams`` in ``web/js/comfy-metadata.js``, the parser
behind the gallery, admin and metadata pages. For the same records it returns
what that function returns; ``tests/test_generation_params_js_parity.py``
enforces it against the JavaScript itself.

It traces the API ``prompt`` graph from the sampler: the positive and negative
inputs back to their text nodes, the model link back to a loader, and the
sampler settings, following primitives and pass-through nodes. Fields the API
graph cannot give are read from the UI ``workflow`` graph.

The result depends on JavaScript value semantics, so those are reproduced
explicitly: property order, truthiness, ``String()``, ``trim()``, strict
equality, ``Map``/``Set`` keys, and the inputs on which the JavaScript throws
(raised here as ``GenerationParamsError``). The recursive tracing runs on an
explicit stack, so a crafted graph cannot exhaust the Python stack.
"""

import json
import math
import re
from types import MappingProxyType

__all__ = [
    "PLACEHOLDERS",
    "GenerationParamsError",
    "extract_generation_params",
    "extract_from_records",
    "parse_js_json",
    "parse_page_json",
]

PLACEHOLDERS = MappingProxyType(
    {
        "positivePrompt": "No prompt found",
        "negativePrompt": "No negative prompt found",
        "checkpoint": "Unknown",
        "steps": "Unknown",
        "cfgScale": "Unknown",
        "sampler": "Unknown",
        "seed": "Unknown",
    }
)

# Upper bound on nodes expanded per lookup; crafted files can contain any graph.
MAX_VISITS = 500
MODEL_NAME_KEYS = ("ckpt_name", "unet_name", "model_name", "gguf_name")
SEED_CONTROL_VALUES = frozenset(("fixed", "increment", "decrement", "randomize"))
PASS_THROUGH_TYPES = frozenset(("Reroute", "GetNode", "SetNode"))
MODE_MUTED = 2
MODE_BYPASSED = 4
# Widget order for core nodes, used when a workflow carries no widget names.
PROMPT_MANAGER_WIDGETS = (
    "text",
    "category",
    "tags",
    "search_text",
    "prepend_text",
    "append_text",
)
WIDGET_POSITIONS = {
    "KSampler": ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "KSamplerAdvanced": (
        "add_noise",
        "noise_seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "start_at_step",
        "end_at_step",
        "return_with_leftover_noise",
    ),
    "CLIPTextEncode": ("text",),
    "PromptManager": PROMPT_MANAGER_WIDGETS,
    "PromptManagerText": PROMPT_MANAGER_WIDGETS,
    "PromptManagerFocused": PROMPT_MANAGER_WIDGETS,
    "PromptManagerSplit": PROMPT_MANAGER_WIDGETS,
    "PromptManagerPair": (
        "text",
        "negative_text",
        "category",
        "tags",
        "search_text",
        "prepend_text",
        "append_text",
    ),
    "CheckpointLoader": ("config_name", "ckpt_name"),
    "UNETLoader": ("unet_name", "weight_dtype"),
}

# JavaScript regular expressions without the ``u`` flag fold case in ASCII only.
_FLAGS = re.IGNORECASE | re.ASCII
TEXT_KEYS = re.compile(r"(text|string|prompt|value)(_?[a-z0-9]+)?", _FLAGS)
_PROMPT_MANAGER = re.compile(r"promptmanager", _FLAGS)
_KSAMPLER = re.compile(r"ksampler", _FLAGS)
_CHECKPOINT_LOADER = re.compile(r"checkpointloader", _FLAGS)
_UNET = re.compile(r"unet", _FLAGS)
_SEED_NAME = re.compile(r"seed\Z")
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_MAX_ARRAY_INDEX = 2**32 - 2
_MAX_SAFE_INTEGER = 2**53
# String.prototype.trim() removes WhiteSpace and LineTerminator code points.
_JS_WHITESPACE = "\t\n\v\f\r          " "        　﻿"
# Properties every object literal inherits; reading one yields a function or object.
_OBJECT_PROTOTYPE_KEYS = frozenset(
    (
        "constructor",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "toLocaleString",
        "toString",
        "valueOf",
        "__proto__",
        "__defineGetter__",
        "__defineSetter__",
        "__lookupGetter__",
        "__lookupSetter__",
    )
)


class GenerationParamsError(ValueError):
    """The browser parser throws on this input, so there is no result."""


class _Undefined:
    """JavaScript ``undefined``: a missing property, distinct from JSON ``null``."""

    __slots__ = ()

    def __repr__(self):
        return "undefined"


UNDEFINED = _Undefined()


# --- JavaScript value semantics -------------------------------------------------


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nullish(value):
    return value is None or value is UNDEFINED


def _truthy(value):
    if value is None or value is UNDEFINED or value is False:
        return False
    if _is_number(value):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def _typeof(value):
    if value is UNDEFINED:
        return "undefined"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def _number_string(value):
    """``String(number)`` for a double, per the ECMAScript Number::toString rules."""
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))
    mantissa, _, exponent = text.partition("e")
    whole, _, fraction = mantissa.partition(".")
    raw = whole + fraction
    leading = len(raw) - len(raw.lstrip("0"))
    digits = raw.lstrip("0").rstrip("0")
    point = len(whole) - leading + int(exponent or 0)
    count = len(digits)
    if count <= point <= 21:
        body = digits + "0" * (point - count)
    elif 0 < point <= 21:
        body = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        body = "0." + "0" * -point + digits
    else:
        power = point - 1
        mark = ("+" if power >= 0 else "-") + str(abs(power))
        body = (digits if count == 1 else digits[0] + "." + digits[1:]) + "e" + mark
    return sign + body


def _js_string(value):
    """``String(value)`` for any JSON-derived value."""
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if _is_number(value):
        return _number_string(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _array_string(value)
    return "[object Object]"


def _array_string(value):
    """``String(array)``: ``join(",")`` applied to nested arrays, on an explicit stack.

    null and undefined elements join as "". A crafted file can nest arrays far
    deeper than the Python stack allows, so this never recurses.
    """
    out = []
    stack = [(value, 0)]
    while stack:
        items, index = stack.pop()
        if index == len(items):
            continue
        if index:
            out.append(",")
        stack.append((items, index + 1))
        item = items[index]
        if isinstance(item, list):
            stack.append((item, 0))
        elif not _nullish(item):
            out.append(_js_string(item))
    return "".join(out)


def _key(value):
    return value if isinstance(value, str) else _js_string(value)


def _index(key):
    """The array index a property key names, or None."""
    if (
        _is_number(key)
        and not isinstance(key, bool)
        and float(key).is_integer()
        and key >= 0
    ):
        number = int(key)
        return number if number <= _MAX_ARRAY_INDEX else None
    text = _key(key)
    if _ARRAY_INDEX.match(text) and int(text) <= _MAX_ARRAY_INDEX:
        return int(text)
    return None


def _get(obj, key):
    """Property read ``obj[key]``; reading from null or undefined throws."""
    if obj is None or obj is UNDEFINED:
        raise GenerationParamsError(f"cannot read {key!r} of {_js_string(obj)}")
    if isinstance(obj, dict):
        return obj.get(_key(key), UNDEFINED)
    if isinstance(obj, (list, str)):
        if key == "length":
            return len(obj)
        index = _index(key)
        return obj[index] if index is not None and index < len(obj) else UNDEFINED
    return UNDEFINED


def _has(obj, key):
    """``key in obj``; throws on primitives."""
    if isinstance(obj, dict):
        return _key(key) in obj
    if isinstance(obj, list):
        index = _index(key)
        return key == "length" or (index is not None and index < len(obj))
    raise GenerationParamsError(f"cannot use 'in' on {_typeof(obj)}")


def _own(obj, key):
    """``Object.prototype.hasOwnProperty.call(obj, key)``."""
    if obj is None or obj is UNDEFINED:
        raise GenerationParamsError("hasOwnProperty on null or undefined")
    if isinstance(obj, dict):
        return _key(key) in obj
    if isinstance(obj, (list, str)):
        index = _index(key)
        return key == "length" or (index is not None and index < len(obj))
    return False


def _ordered_keys(obj):
    """Own property order: array indexes ascending, then other keys as inserted."""
    indexes = sorted(
        (k for k in obj if _ARRAY_INDEX.match(k) and int(k) <= _MAX_ARRAY_INDEX),
        key=int,
    )
    marked = set(indexes)
    return indexes + [k for k in obj if k not in marked]


def _entries(obj):
    """``Object.entries(obj)``."""
    if obj is None or obj is UNDEFINED:
        raise GenerationParamsError("Object.entries of null or undefined")
    if isinstance(obj, dict):
        return [(k, obj[k]) for k in _ordered_keys(obj)]
    if isinstance(obj, (list, str)):
        return [(str(i), item) for i, item in enumerate(obj)]
    return []


def _values(obj):
    """``Object.values(obj)``."""
    return [value for _, value in _entries(obj)]


def _strict_equal(left, right):
    """``left === right``."""
    if left is UNDEFINED or right is UNDEFINED or left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return left is right


def _same_value_key(value):
    """A hashable key with ``Map``/``Set`` (SameValueZero) equality."""
    if value is UNDEFINED:
        return ("undefined", None)
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("boolean", value)
    if _is_number(value):
        return (
            ("nan", None)
            if isinstance(value, float) and math.isnan(value)
            else ("number", value)
        )
    if isinstance(value, str):
        return ("string", value)
    return ("object", id(value))


def _trim(text):
    return text.strip(_JS_WHITESPACE)


def _first_truthy(*values):
    """``a || b || ...``: the first truthy operand, else the last."""
    for value in values:
        if _truthy(value):
            return value
    return values[-1]


def _and_chain(obj, *steps):
    """``a && a[k1] && a[k1][k2] ...``: stops at the first falsy operand."""
    value = obj
    for step in steps:
        if not _truthy(value):
            return value
        value = _get(value, step)
    return value


def _coalesce(value, fallback):
    """``value ?? fallback()``; the fallback runs only when needed."""
    return fallback() if _nullish(value) else value


def _array(value):
    """A value used with an Array method; anything else throws."""
    if isinstance(value, list):
        return value
    raise GenerationParamsError(f"{_typeof(value)} has no array methods")


def _iterate(value):
    """``for (const x of value)``: arrays and strings iterate, other values throw."""
    if isinstance(value, (list, str)):
        return value
    raise GenerationParamsError(f"{_typeof(value)} is not iterable")


def _set(obj, key, value):
    """``obj[key] = value`` on an object literal (``__proto__`` is not an own key)."""
    name = _key(key)
    if name == "__proto__" and name not in obj:
        return
    obj[name] = value


def _matches(pattern, value):
    """``pattern.test(value || "")``."""
    return pattern.search(_js_string(value if _truthy(value) else "")) is not None


def _run(generator):
    """Drive generator-based recursion on an explicit stack, not the call stack."""
    stack = [generator]
    value = None
    while stack:
        try:
            request = stack[-1].send(value)
        except StopIteration as done:
            stack.pop()
            value = done.value
            continue
        stack.append(request)
        value = None
    return value


# --- Tracing ---------------------------------------------------------------------


def _is_link(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and (isinstance(value[0], str) or _is_number(value[0]))
        and _is_number(value[1])
    )


def _is_sampler(node):
    """A KSampler or a guider (CFGGuider); the model link excludes pass-throughs."""
    if not _truthy(node):
        return False
    inputs = _get(node, "inputs")
    return (
        _truthy(inputs)
        and _is_link(_get(inputs, "positive"))
        and _is_link(_get(inputs, "negative"))
        and _is_link(_get(inputs, "model"))
    )


def _is_custom_sampler(node):
    """SamplerCustomAdvanced-style nodes split settings across noise/sampler/sigmas."""
    if not _truthy(node):
        return False
    inputs = _get(node, "inputs")
    return (
        _truthy(inputs)
        and _is_link(_get(inputs, "guider"))
        and _is_link(_get(inputs, "sigmas"))
    )


def _workflow_nodes(workflow):
    if not _truthy(workflow):
        return []
    nodes = _get(workflow, "nodes")
    return nodes if isinstance(nodes, list) else []


def _widgets_of(node):
    values = _get(node, "widgets_values")
    return values if isinstance(values, list) else []


class _Context:
    """The graph being traced and recovered PromptManager widget text."""

    __slots__ = ("graph", "widget_text")

    def __init__(self, graph, widget_text):
        self.graph = graph
        self.widget_text = widget_text


def _create_context(graph, workflow):
    """Recover PromptManager text overwritten by the pre-3.2.3 SaveImage patch.

    The signature is two or more PromptManager nodes with identical API text
    whose workflow widgets differ; only those get their widget text back.
    Otherwise the API graph is what actually ran and always wins.
    """
    widgets = {}
    for node in _workflow_nodes(workflow):
        text = _get(_widgets_of(node), 0)
        node_type = _get(node, "type")
        if (
            not _strict_equal(node_type, "PromptManagerPair")
            and _matches(_PROMPT_MANAGER, node_type)
            and isinstance(text, str)
        ):
            widgets[_js_string(_get(node, "id"))] = text
    by_api_text = {}
    for node_id in widgets:
        api_text = _and_chain(graph, node_id, "inputs", "text")
        if isinstance(api_text, str):
            by_api_text.setdefault(api_text, []).append(node_id)
    widget_text = {}
    for ids in by_api_text.values():
        if len(ids) > 1 and len({widgets[node_id] for node_id in ids}) > 1:
            for node_id in ids:
                widget_text[node_id] = widgets[node_id]
    return _Context(graph, widget_text)


def _enter(context, link, seen):
    """``(node_id, node)`` for a link; None when unresolved or already expanded."""
    if not _is_link(link) or len(seen) >= MAX_VISITS:
        return None
    node_id = _js_string(link[0])
    node = _get(context.graph, node_id)
    key = f"{node_id}:{_js_string(link[1])}"
    if not _truthy(node) or not _truthy(_get(node, "inputs")) or key in seen:
        return None
    seen.add(key)
    return node_id, node


def _resolve_scalar(context, value, name, seen):
    """A scalar input, following a link; an input named like the field wins."""
    if not _is_link(value):
        return value
    entered = _enter(context, value, seen)
    if entered is None:
        return UNDEFINED
    inputs = _get(entered[1], "inputs")
    if _truthy(name) and _own(inputs, name):
        return (yield _resolve_scalar(context, _get(inputs, name), name, seen))
    for item in _values(inputs):
        resolved = yield _resolve_scalar(context, item, name, seen)
        if resolved is not UNDEFINED and _typeof(resolved) != "object":
            return resolved
    return UNDEFINED


def _resolve_linked_input(context, link, keys):
    """The first of ``keys`` on the node a link points to, resolving further links."""
    seen = set()
    entered = _enter(context, link, seen)
    if entered is None:
        return UNDEFINED
    inputs = _get(entered[1], "inputs")
    key = next((k for k in keys if _has(inputs, k)), None)
    if key is None:
        return UNDEFINED
    return (yield _resolve_scalar(context, _get(inputs, key), key, seen))


def _resolve_string(context, value, seen):
    """A string input; text-producing nodes may be chained (concat, primitives)."""
    if isinstance(value, str):
        return value
    entered = _enter(context, value, seen)
    if entered is None:
        return ""
    node_id, node = entered
    inputs = _get(node, "inputs")
    if _strict_equal(_get(node, "class_type"), "PromptManagerPair"):
        if _strict_equal(value[1], 3):
            return (yield _resolve_string(context, _get(inputs, "negative_text"), seen))
        if _strict_equal(value[1], 2):
            return (yield _compose_node_text(context, node_id, node, seen))
        return ""
    if _has(inputs, "text"):
        return (yield _compose_node_text(context, node_id, node, seen))
    delimiter = _get(inputs, "delimiter")
    if not isinstance(delimiter, str):
        delimiter = " "
    parts = []
    for key, item in _entries(inputs):
        if TEXT_KEYS.fullmatch(key):
            parts.append((yield _resolve_string(context, item, seen)))
    return delimiter.join(part for part in parts if _trim(part))


def _compose_node_text(context, node_id, node, seen):
    """An encoder's text, joined like PromptManager: prepend + text + append."""
    inputs = _get(node, "inputs")
    if node_id in context.widget_text:
        raw = context.widget_text[node_id]
    else:
        raw = yield _resolve_string(context, _get(inputs, "text"), seen)
    text = _trim(raw)
    if not text:
        return ""
    # One shared set even across siblings keeps crafted chains linear.
    prepend = _trim(
        (yield _resolve_string(context, _get(inputs, "prepend_text"), seen))
    )
    append = _trim((yield _resolve_string(context, _get(inputs, "append_text"), seen)))
    # Older saves stored the already-combined prompt in ``text``.
    parts = (
        prepend if prepend and not text.startswith(prepend) else "",
        text,
        append if append and not text.endswith(append) else "",
    )
    return " ".join(part for part in parts if part)


def _trace_conditioning(context, link, role, seen):
    """Follow a conditioning link upstream to the encoder that produced it."""
    entered = _enter(context, link, seen)
    if entered is None:
        return ""
    node_id, node = entered
    inputs = _get(node, "inputs")
    if _strict_equal(_get(node, "class_type"), "PromptManagerPair"):
        if _strict_equal(link[1], 1):
            return (yield _resolve_string(context, _get(inputs, "negative_text"), seen))
        if _strict_equal(link[1], 0):
            return (yield _compose_node_text(context, node_id, node, seen))
        return ""
    if _has(inputs, "text"):
        return (yield _compose_node_text(context, node_id, node, seen))
    # Pass-through nodes (ControlNet apply, conditioning combine/set area, ...).
    following = _first_truthy(
        _get(inputs, role),
        _get(inputs, "conditioning"),
        _get(inputs, "conditioning_to"),
        _get(inputs, "conditioning_1"),
    )
    return (yield _trace_conditioning(context, following, role, seen))


def _trace_model_name(context, link, seen):
    """Follow the model link upstream (through LoRA loaders etc.) to a loader."""
    entered = _enter(context, link, seen)
    if entered is None:
        return UNDEFINED
    inputs = _get(entered[1], "inputs")
    key = next((k for k in MODEL_NAME_KEYS if isinstance(_get(inputs, k), str)), None)
    if key is not None:
        return _get(inputs, key)
    return (yield _trace_model_name(context, _get(inputs, "model"), seen))


def _find_any_model_name(nodes):
    for node in nodes:
        inputs = _and_chain(node, "inputs")
        if not _truthy(inputs):
            inputs = {}
        key = next(
            (k for k in MODEL_NAME_KEYS if isinstance(_get(inputs, k), str)), None
        )
        if key is not None:
            return _get(inputs, key)
    return UNDEFINED


def _from_prompt_graph(graph, workflow):
    context = _create_context(graph, workflow)
    nodes = _values(graph)
    custom = next((node for node in nodes if _is_custom_sampler(node)), UNDEFINED)
    guider = UNDEFINED
    if custom is not UNDEFINED:
        entered = _enter(context, _get(_get(custom, "inputs"), "guider"), set())
        guider = entered[1] if entered is not None else UNDEFINED
    sampler = (
        guider
        if _is_sampler(guider)
        else next((n for n in nodes if _is_sampler(n)), UNDEFINED)
    )

    if sampler is UNDEFINED:
        loose = next(
            (
                n
                for n in nodes
                if _truthy(n)
                and _truthy(_get(n, "inputs"))
                and _matches(_KSAMPLER, _get(n, "class_type"))
            ),
            UNDEFINED,
        )
        inputs = _and_chain(loose, "inputs")
        if not _truthy(inputs):
            inputs = {}
        seed = _coalesce(_get(inputs, "seed"), lambda: _get(inputs, "noise_seed"))
        return {
            "checkpoint": _find_any_model_name(nodes),
            "seed": (yield _resolve_scalar(context, seed, "seed", set())),
            "steps": (
                yield _resolve_scalar(context, _get(inputs, "steps"), "steps", set())
            ),
            "cfgScale": (
                yield _resolve_scalar(context, _get(inputs, "cfg"), "cfg", set())
            ),
            "sampler": (
                yield _resolve_scalar(
                    context, _get(inputs, "sampler_name"), "sampler_name", set()
                )
            ),
        }

    inputs = _get(sampler, "inputs")

    def from_custom(field, keys):
        if custom is UNDEFINED:
            return UNDEFINED
        return _run(
            _resolve_linked_input(context, _get(_get(custom, "inputs"), field), keys)
        )

    result = {
        "positivePrompt": (
            yield _trace_conditioning(
                context, _get(inputs, "positive"), "positive", set()
            )
        ),
        "negativePrompt": (
            yield _trace_conditioning(
                context, _get(inputs, "negative"), "negative", set()
            )
        ),
    }
    model = yield _trace_model_name(context, _get(inputs, "model"), set())
    result["checkpoint"] = model if _truthy(model) else _find_any_model_name(nodes)
    seed_value = _coalesce(_get(inputs, "seed"), lambda: _get(inputs, "noise_seed"))
    seed_name = "seed" if _has(inputs, "seed") else "noise_seed"
    seed = yield _resolve_scalar(context, seed_value, seed_name, set())
    result["seed"] = _coalesce(
        seed, lambda: from_custom("noise", ("noise_seed", "seed"))
    )
    steps = yield _resolve_scalar(context, _get(inputs, "steps"), "steps", set())
    result["steps"] = _coalesce(steps, lambda: from_custom("sigmas", ("steps",)))
    result["cfgScale"] = yield _resolve_scalar(
        context, _get(inputs, "cfg"), "cfg", set()
    )
    sampler_name = yield _resolve_scalar(
        context, _get(inputs, "sampler_name"), "sampler_name", set()
    )
    result["sampler"] = _coalesce(
        sampler_name, lambda: from_custom("sampler", ("sampler_name",))
    )
    return result


# --- UI workflow graph -------------------------------------------------------------


def _widget_names(node):
    inputs = _first_truthy(_get(node, "inputs"), [])
    from_inputs = [
        _get(_get(item, "widget"), "name")
        for item in _array(inputs)
        if _truthy(item)
        and _truthy(_get(item, "widget"))
        and _truthy(_get(_get(item, "widget"), "name"))
    ]
    if from_inputs:
        return from_inputs
    node_type = _get(node, "type")
    key = _js_string(node_type)
    if key in _OBJECT_PROTOTYPE_KEYS:
        raise GenerationParamsError(f"widget positions of {key!r} are not iterable")
    if key in WIDGET_POSITIONS:
        return list(WIDGET_POSITIONS[key])
    if _matches(_CHECKPOINT_LOADER, node_type):
        return ["ckpt_name"]
    if _matches(_UNET, node_type):
        return ["unet_name"]
    return []


def _named_widgets(node):
    """Map widget values to input names, skipping control_after_generate."""
    named = _get(node, "widgets_values_named")
    if _truthy(named) and isinstance(named, dict):
        return dict(named)
    values = _widgets_of(node)
    out = {}
    position = 0
    for name in _iterate(_widget_names(node)):
        if position >= len(values):
            break
        _set(out, name, values[position])
        position += 1
        following = values[position] if position < len(values) else UNDEFINED
        if (
            _SEED_NAME.search(_js_string(name))
            and isinstance(following, str)
            and following in SEED_CONTROL_VALUES
        ):
            position += 1
    return out


def _workflow_to_graph(workflow):
    """Convert a UI workflow ({nodes, links}) into the API graph shape.

    Set/Get pairs, Reroutes and bypassed nodes resolve to their real source;
    muted nodes are dropped.
    """
    nodes = [
        n
        for n in _workflow_nodes(workflow)
        if _truthy(n) and not _nullish(_get(n, "id"))
    ]
    by_id = {}
    for node in nodes:
        by_id[_js_string(_get(node, "id"))] = node
    links = {}
    raw_links = _get(workflow, "links")
    for link in raw_links if isinstance(raw_links, list) else []:
        if isinstance(link, list):
            links[_same_value_key(_get(link, 0))] = (
                _get(link, 1),
                _get(link, 2),
                _get(link, 5),
            )
        elif _truthy(link) and _typeof(link) == "object":
            links[_same_value_key(_get(link, "id"))] = (
                _get(link, "origin_id"),
                _get(link, "origin_slot"),
                _get(link, "type"),
            )
    setters = {}
    for node in nodes:
        if _strict_equal(_get(node, "type"), "SetNode"):
            setters[_same_value_key(_get(_widgets_of(node), 0))] = _and_chain(
                node, "inputs", 0, "link"
            )

    def source(link_id):
        hops = 0
        while True:
            link = links.get(_same_value_key(link_id))
            node = by_id.get(_js_string(link[0])) if link is not None else None
            if (
                node is None
                or _strict_equal(_get(node, "mode"), MODE_MUTED)
                or hops > MAX_VISITS
            ):
                return UNDEFINED
            hops += 1
            node_type = _get(node, "type")
            if _strict_equal(node_type, "Reroute"):
                link_id = _and_chain(node, "inputs", 0, "link")
                continue
            if _strict_equal(node_type, "GetNode"):
                link_id = setters.get(
                    _same_value_key(_get(_widgets_of(node), 0)), UNDEFINED
                )
                continue
            if _strict_equal(_get(node, "mode"), MODE_BYPASSED):
                link_type = link[2]
                same = next(
                    (
                        item
                        for item in _array(_first_truthy(_get(node, "inputs"), []))
                        if _truthy(item)
                        and not _nullish(_get(item, "link"))
                        and (
                            not _truthy(link_type)
                            or _strict_equal(link_type, "*")
                            or _strict_equal(_get(item, "type"), link_type)
                        )
                    ),
                    UNDEFINED,
                )
                if same is UNDEFINED:
                    return UNDEFINED
                link_id = _get(same, "link")
                continue
            return [_js_string(_get(node, "id")), link[1]]

    graph = {}
    for node in nodes:
        mode = _get(node, "mode")
        node_type = _get(node, "type")
        if (
            _strict_equal(mode, MODE_MUTED)
            or _strict_equal(mode, MODE_BYPASSED)
            or (isinstance(node_type, str) and node_type in PASS_THROUGH_TYPES)
        ):
            continue
        inputs = _named_widgets(node)
        for item in _iterate(_first_truthy(_get(node, "inputs"), [])):
            if not _truthy(item) or _nullish(_get(item, "link")):
                continue
            found = source(_get(item, "link"))
            if _truthy(found):
                _set(inputs, _get(item, "name"), found)
        _set(graph, _get(node, "id"), {"class_type": node_type, "inputs": inputs})
    return graph


# --- Public API ----------------------------------------------------------------------


def _has_value(value):
    return not _nullish(value) and not (isinstance(value, str) and value == "")


def extract_generation_params(data):
    """Prompts, model and sampler settings from parsed ``prompt``/``workflow`` records.

    ``data`` holds the parsed records (``{"prompt": ..., "workflow": ...}``). Each
    field falls back to its placeholder when neither graph provides it.
    Raises ``GenerationParamsError`` where the browser parser throws.
    """
    data = data if _truthy(data) else {}
    prompt = _get(data, "prompt")
    workflow = _get(data, "workflow")
    primary = {}
    if _truthy(prompt) and _typeof(prompt) == "object":
        primary = _run(_from_prompt_graph(prompt, workflow))
    fallback = {}
    if _truthy(workflow):
        fallback = _run(_from_prompt_graph(_workflow_to_graph(workflow), None))
    merged = {}
    for key, placeholder in PLACEHOLDERS.items():
        value = primary.get(key, UNDEFINED)
        if not _has_value(value):
            value = fallback.get(key, UNDEFINED)
        merged[key] = value if _has_value(value) else placeholder
    return merged


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def _double_integer(text):
    """Integers parse as doubles in JavaScript; beyond 2**53 they round."""
    value = int(text)
    return value if -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER else float(text)


def parse_js_json(text):
    """``JSON.parse(text)``, or None where it throws (or the text is ``null``)."""
    if not isinstance(text, str):
        return None
    try:
        return json.loads(
            text, parse_constant=_reject_constant, parse_int=_double_integer
        )
    except (ValueError, RecursionError):
        return None


def _null_constant(_name):
    return None


def parse_page_json(text):
    """A metadata record as the legacy pages parse it, or None where they fail.

    The gallery, admin and metadata pages replace ComfyUI's ``NaN`` with
    ``null`` before ``JSON.parse``. Here every non-finite literal ComfyUI
    writes (``NaN``, ``Infinity``, ``-Infinity``) is null, and only where it is
    a number: unlike the pages' text replacement, a prompt that spells "NaN"
    keeps its text.
    """
    if not isinstance(text, str):
        return None
    try:
        return json.loads(
            text, parse_constant=_null_constant, parse_int=_double_integer
        )
    except (ValueError, RecursionError):
        return None


def extract_from_records(prompt_text, workflow_text):
    """``extract_generation_params`` for raw ``prompt``/``workflow`` record text.

    A record that is absent or fails to parse is treated as absent, as the
    browser pages do.
    """
    data = {}
    for key, text in (("prompt", prompt_text), ("workflow", workflow_text)):
        if isinstance(text, str):
            data[key] = parse_js_json(text)
    return extract_generation_params(data)
