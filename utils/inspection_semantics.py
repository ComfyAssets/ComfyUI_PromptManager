"""Load and validate the declarative inspection node semantics table (#240).

The table lives once in ``web/js/inspection-semantics.json`` so a browser-local
resolver can load the same data. It maps node classes (or ``(class, output
slot)`` pairs) to a named strategy from the closed ``STRATEGIES`` set plus the
exact input names that strategy reads. Code implements each strategy; adding a
node that reuses one is a data-only change.

Only the inspection resolver profile consumes this table, through
``get_semantics()``, which reads and validates it on first use and caches only a
valid result. Importing this module reads nothing, and the default (ingestion)
profile never imports it, so a missing or bad table can never break ingestion.
An invalid table raises ``SemanticsError`` so a bad edit fails loudly instead of
silently changing what the inspector resolves. The file is bounded in size and
nesting before it is validated. Every class appears in exactly one category;
classes that are not listed stay ``unsupported-node``.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple, Union

SEMANTICS_PATH = (
    Path(__file__).resolve().parents[1] / "web" / "js" / "inspection-semantics.json"
)
SCHEMA_VERSION = 1
# The real table is a few KiB and four levels deep; these only reject nonsense.
MAX_TABLE_BYTES = 1024 * 1024
MAX_NESTING = 8
_NAME_LENGTH = 128
# Comfy output slots are small; this only rejects nonsense, never a real node.
_MAX_SLOT = 255

STRATEGIES: Mapping[str, FrozenSet[str]] = MappingProxyType(
    {
        "samplers": frozenset({"ksampler", "custom-advanced", "detailer-stage"}),
        "guiders": frozenset({"cfg", "basic"}),
        "conditioning": frozenset({"passthrough", "zeroed"}),
        "selectors": frozenset({"boolean-select"}),
        "composites": frozenset({"combine"}),
        "encoders": frozenset({"single", "parts-equal", "pm-compose"}),
        "strings": frozenset({"literal", "strip-newlines", "join-multi", "concat"}),
        "savers": frozenset({"comfy-counter", "vhs-counter"}),
    }
)
# Shapes of the facts each sampler class provides (consumed by comfy-metadata.js).
FACT_SHAPES = frozenset({"ksampler", "custom", "custom-advanced"})
SAVER_PATTERNS = MappingProxyType(
    {
        "comfy-counter": "{name}_{counter:05}_.{ext}",
        "vhs-counter": "{name}_{counter:05}.{ext}",
    }
)
_CONFIGURATION_KINDS = frozenset({"noise", "sampler", "sigmas", "guider"})
_SCALAR_KINDS = frozenset({"number", "string"})
_TYPE_FIELDS = frozenset({"type_fields"})
_CLASS_CATEGORIES = ("samplers", "guiders", "selectors", "composites", "savers")
_SLOT_CATEGORIES = ("conditioning", "encoders", "strings")
_SET_CATEGORIES = ("runtime", "non_file_outputs")
_FACTS_TABLES = (
    "configuration_outputs",
    "model_outputs",
    "model_passthroughs",
    "scalar_outputs",
)
_CATEGORIES = frozenset(
    ("schema",)
    + _CLASS_CATEGORIES
    + _SLOT_CATEGORIES
    + _SET_CATEGORIES
    + ("stage_inputs", "facts")
)
# Input names that make an unlisted node a possible sampler stage in an ancestry
# walk (``shaped``: pipes, video embeds and custom-sampler parts carry a prompt
# without a ``positive`` link), and those that do so beside a linked ``model``
# (``with_model``: XlabsSampler's ``conditioning``, video samplers' ``samples``).
# ``model`` alone (ModelSampling*) or with ``clip`` (LoraLoader) is a model patch.
# The resolver and comfy-metadata.js both read these lists.
_STAGE_INPUT_LISTS = ("shaped", "with_model")
_STRING_PARAMS = MappingProxyType(
    {
        "literal": frozenset(),
        "concat": frozenset(),
        "strip-newlines": frozenset({"flag"}),
        "join-multi": frozenset({"count", "delimiter", "list_flag", "input_prefix"}),
    }
)

Frozen = Union[Mapping[str, Any], Tuple[Any, ...], str, int, None]


class SemanticsError(ValueError):
    """The semantics table is unreadable or violates its schema."""


@dataclass(frozen=True)
class Semantics:
    """Validated, deeply frozen semantics. Slot categories key by ``(class, slot)``."""

    samplers: Mapping[str, Mapping[str, Any]]
    guiders: Mapping[str, Mapping[str, Any]]
    conditioning: Mapping[Tuple[str, int], Mapping[str, Any]]
    selectors: Mapping[str, Mapping[str, Any]]
    composites: Mapping[str, Mapping[str, Any]]
    encoders: Mapping[Tuple[str, int], Mapping[str, Any]]
    strings: Mapping[Tuple[str, int], Mapping[str, Any]]
    runtime: FrozenSet[str]
    savers: Mapping[str, Mapping[str, Any]]
    non_file_outputs: FrozenSet[str]
    stage_inputs: Mapping[str, Tuple[str, ...]]
    facts: Mapping[str, Any]
    all_classes: FrozenSet[str]


def _freeze(value: Any) -> Frozen:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _reject_duplicate_keys(pairs: list) -> dict:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticsError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> None:
    raise SemanticsError(f"non-finite number {name} is not allowed")


def _name(value: Any, where: str, what: str = "input") -> str:
    if not isinstance(value, str) or not value or len(value) > _NAME_LENGTH:
        raise SemanticsError(f"{where}: missing or invalid {what} name {value!r}")
    return value


def _names(value: Any, where: str, minimum: int = 1) -> None:
    if not isinstance(value, list) or len(value) < minimum:
        raise SemanticsError(f"{where}: needs at least {minimum} input name(s)")
    for item in value:
        _name(item, where)
    if len(set(value)) != len(value):
        raise SemanticsError(f"{where}: repeated input name")


def _fields(
    entry: Any, where: str, required: FrozenSet[str], optional: FrozenSet[str]
) -> None:
    if not isinstance(entry, dict):
        raise SemanticsError(f"{where}: entry must be an object")
    missing = sorted(required - entry.keys())
    if missing:
        raise SemanticsError(f"{where}: missing input field(s) {missing}")
    unknown = sorted(entry.keys() - required - optional)
    if unknown:
        raise SemanticsError(f"{where}: unknown field(s) {unknown}")


def _strategy(entry: Any, category: str, where: str) -> str:
    strategy = entry.get("strategy") if isinstance(entry, dict) else None
    if not isinstance(strategy, str) or strategy not in STRATEGIES[category]:
        raise SemanticsError(f"{where}: unknown strategy {strategy!r}")
    return strategy


def _check_sampler(entry: dict, strategy: str, where: str) -> None:
    stage = "image_stage" if strategy == "detailer-stage" else "latent"
    _fields(
        entry,
        where,
        frozenset({"strategy", "conditioning", stage, "facts"}),
        frozenset(),
    )
    _name(entry[stage], where)
    keys = {"guider"} if strategy == "custom-advanced" else {"positive", "negative"}
    conditioning = entry["conditioning"]
    _fields(conditioning, where + ".conditioning", frozenset(keys), frozenset())
    for key in keys:
        _name(conditioning[key], where + ".conditioning")
    if entry["facts"] not in FACT_SHAPES:
        raise SemanticsError(f"{where}: unknown facts shape {entry['facts']!r}")


def _check_guider(entry: dict, strategy: str, where: str) -> None:
    if strategy == "cfg":
        _fields(
            entry, where, frozenset({"strategy", "positive", "negative"}), frozenset()
        )
        _name(entry["negative"], where)
    else:
        # Negative absent by design; its reason is fixed text, not an input.
        _fields(
            entry,
            where,
            frozenset({"strategy", "positive"}),
            frozenset({"absent_reason"}),
        )
        if "absent_reason" in entry:
            _name(entry["absent_reason"], where, "absent reason")
    _name(entry["positive"], where)


def _check_selector(entry: dict, strategy: str, where: str) -> None:
    _fields(entry, where, frozenset({"strategy", "condition", "branches"}), frozenset())
    _name(entry["condition"], where)
    branches = entry["branches"]
    _fields(branches, where + ".branches", frozenset({"true", "false"}), frozenset())
    _name(branches["true"], where)
    _name(branches["false"], where)


def _check_composite(entry: dict, strategy: str, where: str) -> None:
    _fields(entry, where, frozenset({"strategy", "inputs"}), frozenset())
    _names(entry["inputs"], where, minimum=2)


def _check_saver(entry: dict, strategy: str, where: str) -> None:
    _fields(
        entry,
        where,
        frozenset({"strategy", "prefix_input", "image_input", "pattern"}),
        frozenset(),
    )
    _name(entry["prefix_input"], where)
    _name(entry["image_input"], where)
    if entry["pattern"] != SAVER_PATTERNS[strategy]:
        raise SemanticsError(f"{where}: pattern does not match {strategy}")


def _check_conditioning(entry: dict, strategy: str, where: str) -> None:
    _fields(entry, where, frozenset({"strategy", "input"}), frozenset())
    _name(entry["input"], where)


def _check_encoder(entry: dict, strategy: str, where: str) -> None:
    _fields(entry, where, frozenset({"strategy", "inputs"}), frozenset({"compose"}))
    _names(entry["inputs"], where, minimum=2 if strategy == "parts-equal" else 1)
    if "compose" in entry and not isinstance(entry["compose"], bool):
        raise SemanticsError(f"{where}: compose must be a boolean")


def _check_string(entry: dict, strategy: str, where: str) -> None:
    required = _STRING_PARAMS[strategy]
    if required:
        _fields(entry, where, frozenset({"strategy", "inputs", "params"}), frozenset())
        params = entry["params"]
        if not isinstance(params, dict) or set(params) != required:
            raise SemanticsError(f"{where}: params must be exactly {sorted(required)}")
        for value in params.values():
            _name(value, where + ".params")
    else:
        _fields(entry, where, frozenset({"strategy", "inputs"}), frozenset())
    _names(entry["inputs"], where)


_CHECKS: Mapping[str, Callable[[dict, str, str], None]] = MappingProxyType(
    {
        "samplers": _check_sampler,
        "guiders": _check_guider,
        "selectors": _check_selector,
        "composites": _check_composite,
        "savers": _check_saver,
        "conditioning": _check_conditioning,
        "encoders": _check_encoder,
        "strings": _check_string,
    }
)


class _Owners:
    """Each class belongs to one category; each (class, slot) is listed once."""

    def __init__(self) -> None:
        self._category: Dict[str, str] = {}
        self._outputs: set = set()

    def claim(self, name: str, category: str, slot: Optional[int] = None) -> None:
        owner = self._category.setdefault(name, category)
        if owner != category:
            raise SemanticsError(f"duplicate class {name!r} in {owner} and {category}")
        key = (name, slot)
        if slot is not None and key in self._outputs:
            raise SemanticsError(f"{category}: duplicate output key {key!r}")
        self._outputs.add(key)

    def classes(self) -> FrozenSet[str]:
        return frozenset(self._category)


def _class_category(doc: dict, category: str, owners: _Owners) -> Mapping:
    table = doc[category]
    if not isinstance(table, dict):
        raise SemanticsError(f"category {category} must be an object")
    result = {}
    for name, entry in table.items():
        where = f"{category}[{name!r}]"
        _name(name, where, "class")
        strategy = _strategy(entry, category, where)
        _CHECKS[category](entry, strategy, where)
        owners.claim(name, category)
        result[name] = _freeze(entry)
    return MappingProxyType(result)


def _slot(entry: dict, where: str) -> int:
    slot = entry.get("slot")
    # bool is an int subclass; a JSON true is not an output slot.
    if type(slot) is not int or not 0 <= slot <= _MAX_SLOT:
        raise SemanticsError(f"{where}: slot must be a non-negative int, not {slot!r}")
    return slot


def _slot_category(doc: dict, category: str, owners: _Owners) -> Mapping:
    rows = doc[category]
    if not isinstance(rows, list):
        raise SemanticsError(f"category {category} must be a list")
    result = {}
    for index, row in enumerate(rows):
        where = f"{category}[{index}]"
        if not isinstance(row, dict):
            raise SemanticsError(f"{where}: entry must be an object")
        name = _name(row.get("class"), where, "class")
        slot = _slot(row, where)
        entry = {
            key: value for key, value in row.items() if key not in ("class", "slot")
        }
        strategy = _strategy(entry, category, where)
        _CHECKS[category](entry, strategy, where)
        owners.claim(name, category, slot)
        result[(name, slot)] = _freeze(entry)
    return MappingProxyType(result)


def _set_category(doc: dict, category: str, owners: _Owners) -> FrozenSet[str]:
    rows = doc[category]
    if not isinstance(rows, list):
        raise SemanticsError(f"category {category} must be a list")
    seen: set = set()
    for name in rows:
        _name(name, category, "class")
        if name in seen:
            raise SemanticsError(f"{category}: duplicate class {name!r}")
        seen.add(name)
        owners.claim(name, category)
    return frozenset(seen)


def _string_map(table: Any, where: str, allowed: Optional[FrozenSet[str]]) -> None:
    if not isinstance(table, dict):
        raise SemanticsError(f"{where} must be an object")
    for name, value in table.items():
        _name(name, where, "class")
        _name(value, where)
        if allowed is not None and value not in allowed:
            raise SemanticsError(f"{where}[{name!r}]: unknown kind {value!r}")


def _stage_inputs(doc: dict) -> Mapping[str, Tuple[str, ...]]:
    table = doc["stage_inputs"]
    if not isinstance(table, dict) or set(table) != set(_STAGE_INPUT_LISTS):
        raise SemanticsError(
            f"stage_inputs must have exactly {list(_STAGE_INPUT_LISTS)}"
        )
    for key in _STAGE_INPUT_LISTS:
        _names(table[key], f"stage_inputs.{key}")
        if "model" in table[key]:
            raise SemanticsError(f"stage_inputs.{key}: model is a patch input")
    if set(table["shaped"]) & set(table["with_model"]):
        raise SemanticsError("stage_inputs: an input is listed in both lists")
    return _freeze(table)


def _facts(doc: dict) -> Mapping[str, Any]:
    facts = doc["facts"]
    if not isinstance(facts, dict) or set(facts) != set(_FACTS_TABLES):
        raise SemanticsError(f"facts must have exactly {list(_FACTS_TABLES)}")
    configuration = facts["configuration_outputs"]
    if not isinstance(configuration, dict):
        raise SemanticsError("facts.configuration_outputs must be an object")
    for name, adapter in configuration.items():
        where = f"facts.configuration_outputs[{name!r}]"
        _name(name, where, "class")
        _fields(adapter, where, frozenset({"kind", "fields"}), _TYPE_FIELDS)
        if adapter["kind"] not in _CONFIGURATION_KINDS:
            raise SemanticsError(f"{where}: unknown kind {adapter['kind']!r}")
        _names(adapter["fields"], where)
        if "type_fields" in adapter:
            # A fact whose value is the provider's node type (D8: Flux2Scheduler).
            _names(adapter["type_fields"], where)
            if set(adapter["type_fields"]) & set(adapter["fields"]):
                raise SemanticsError(f"{where}: type_fields repeat an input field")
    _string_map(facts["model_outputs"], "facts.model_outputs", None)
    _string_map(facts["scalar_outputs"], "facts.scalar_outputs", _SCALAR_KINDS)
    passthroughs = facts["model_passthroughs"]
    _names(passthroughs, "facts.model_passthroughs", minimum=0)
    return _freeze(facts)


def _check_nesting(doc: Any) -> None:
    """Reject containers nested deeper than ``MAX_NESTING`` (iterative walk)."""
    stack = [(doc, 1)]
    while stack:
        value, level = stack.pop()
        if not isinstance(value, (dict, list)):
            continue
        if level > MAX_NESTING:
            raise SemanticsError(f"semantics table nests deeper than {MAX_NESTING}")
        items = value.values() if isinstance(value, dict) else value
        stack.extend((item, level + 1) for item in items)


def _validate(doc: Any) -> Semantics:
    if not isinstance(doc, dict):
        raise SemanticsError("semantics table must be an object")
    if set(doc) != _CATEGORIES:
        missing = sorted(_CATEGORIES - doc.keys())
        unknown = sorted(doc.keys() - _CATEGORIES)
        raise SemanticsError(f"category mismatch: missing {missing}, unknown {unknown}")
    if type(doc["schema"]) is not int or doc["schema"] != SCHEMA_VERSION:
        raise SemanticsError(f"unsupported schema {doc['schema']!r}")
    owners = _Owners()
    tables: Dict[str, Any] = {}
    for category in _CLASS_CATEGORIES:
        tables[category] = _class_category(doc, category, owners)
    for category in _SLOT_CATEGORIES:
        tables[category] = _slot_category(doc, category, owners)
    for category in _SET_CATEGORIES:
        tables[category] = _set_category(doc, category, owners)
    return Semantics(
        stage_inputs=_stage_inputs(doc),
        facts=_facts(doc),
        all_classes=owners.classes(),
        **tables,
    )


def load_semantics(path: Optional[Union[str, Path]] = None) -> Semantics:
    """Read and validate a semantics table; ``SemanticsError`` on any problem."""
    source = Path(path) if path is not None else SEMANTICS_PATH
    try:
        with source.open("rb") as stream:
            data = stream.read(MAX_TABLE_BYTES + 1)
        if len(data) > MAX_TABLE_BYTES:
            raise SemanticsError(
                f"semantics table {source.name} exceeds {MAX_TABLE_BYTES} bytes"
            )
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except SemanticsError:
        raise
    except RecursionError:
        raise SemanticsError(
            f"semantics table {source.name} nests deeper than {MAX_NESTING}"
        ) from None
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SemanticsError(f"cannot read semantics table {source.name}: {error}")
    _check_nesting(doc)
    return _validate(doc)


@lru_cache(maxsize=1)
def get_semantics() -> Semantics:
    """The repository table, loaded on first use; failures are not cached."""
    return load_semantics()
