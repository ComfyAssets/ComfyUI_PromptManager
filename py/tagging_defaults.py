"""The tagging configuration's constants and validators (#328).

Server-free on purpose: ``py.api`` must import without ComfyUI's ``server``
module, which ``py/config.py`` needs; config re-exports all of this.
"""

import csv
import math
import os
import re
import threading
import unicodedata
from typing import Any, Dict, Iterable, List, Set, Tuple

# The tagging engines (``py/autotag.py`` MODELS) and the options they take.
WD14_MODEL_TYPES = frozenset({"wd14-swinv2", "wd14-vit"})
LLM_MODEL_TYPES = frozenset({"gguf", "hf"})
MODEL_TYPES = WD14_MODEL_TYPES | LLM_MODEL_TYPES
MAX_INSTRUCTION_CHARS = 4000
MAX_INSTRUCTION_BYTES = 16 * 1024
# Tag output images' review estimate (#379): seconds per image for the WD14
# engines, by where ONNX Runtime runs them. The graphics card figure was
# measured in PR 1's live check (RTX 4090, CUDA: Auto-tag selected on 100
# images in 16.7 s with the model load, 0.167 s/image). The processor figure
# is an upper bound from the maintainer's cancelled CPU run (19 min / 697
# images, backup, model load and skips included), kept as the fallback. The
# language-model engines have no figure.
WD14_SECONDS_PER_IMAGE = 1.6
WD14_GPU_SECONDS_PER_IMAGE = 0.17


def wd14_seconds_per_image(device: str) -> float:
    """The estimate's rate for ``"gpu"`` or ``"cpu"`` (anything else)."""
    return WD14_GPU_SECONDS_PER_IMAGE if device == "gpu" else WD14_SECONDS_PER_IMAGE


# What a batch does with an image that already has tags (#328, #379).
EXISTING_POLICIES = ("skip", "merge", "replace_after_review")

# The language-model tag template every page starts from until one is saved.
DEFAULT_TAG_TEMPLATE = (
    "Generate only comma-separated Danbooru tags (lowercase_underscores, no spaces).\n"
    "Include: subject_count (1girl/1boy/etc), gender, ethnicity (asian, caucasian, "
    "etc), age_approx (teen, young_woman), body_attributes, face_features, "
    "hairstyle, appearance, clothing, accessories, pose, expression, action, "
    "camera_angle, camera_shot, scene_composition (wide_shot, long_shot, "
    "dutch_angle), environment, background_elements, lighting, mood.\n"
    "Prioritize visual accuracy.\n"
    "No sentences, no commentary, no captions. Only tags. Keep length short but "
    "descriptive."
)


def validate_threshold(name: str, value: Any) -> float:
    """A WD14 confidence threshold in [0, 1], or ValueError naming ``name``."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


# Every page shows the thresholds in steps of 0.05 (the concept's System rows,
# the Tag image dialogs and the legacy sliders), so a saved one must be on that
# grid (#369). Per-request thresholds keep their own checks.
THRESHOLD_STEP = 0.05
_STEPS_PER_UNIT = round(1 / THRESHOLD_STEP)
# A computed float can miss the grid by a hair: (0.1 + 0.2) * 20 is 6.000000000000001.
_STEP_TOLERANCE = 1e-12  # in steps; refuses 0.35000000001


def validate_saved_threshold(name: str, value: Any) -> float:
    """A saved WD14 threshold: in [0, 1] and a multiple of ``THRESHOLD_STEP``,
    returned exactly on the grid; else ValueError naming ``name``."""
    value = validate_threshold(name, value)
    steps = value * _STEPS_PER_UNIT
    if abs(steps - round(steps)) > _STEP_TOLERANCE:
        raise ValueError(f"{name} must be a multiple of {THRESHOLD_STEP}")
    return round(steps) / _STEPS_PER_UNIT


def validate_template(value: Any, name: str = "instructions") -> str:
    """Non-empty language-model instructions within the character and byte
    caps, or ValueError naming ``name``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value) > MAX_INSTRUCTION_CHARS:
        raise ValueError(f"{name} must be at most {MAX_INSTRUCTION_CHARS} characters")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be valid Unicode text") from None
    if len(encoded) > MAX_INSTRUCTION_BYTES:
        raise ValueError(f"{name} {'are' if name == 'instructions' else 'is'} too long")
    return value


# Skipped tags (#431): words whose suggested tags are never offered.
MAX_SKIPPED_TAGS = 200
MAX_SKIPPED_TAG_CHARS = 50
_WORD_SEPARATORS = re.compile(r"[ _-]+")
_FORBIDDEN_IN_ENTRY = re.compile(r"[,\x00-\x1f\x7f-\x9f\u2028\u2029]")


def tag_words(text: str) -> Tuple[str, ...]:
    """The words of a tag or a skipped entry: NFC, case-folded, split on runs
    of space, ``_`` and ``-``. Every other character belongs to a word."""
    folded = unicodedata.normalize("NFC", text).casefold()
    return tuple(word for word in _WORD_SEPARATORS.split(folded) if word)


def validate_skipped_tags(value: Any) -> List[str]:
    """The Skipped tags list, each entry trimmed, or ValueError with the
    sentence the System page shows (entries are numbered from 1)."""
    if not isinstance(value, list):
        raise ValueError("Skipped tags must be a list of words.")
    if len(value) > MAX_SKIPPED_TAGS:
        raise ValueError(f"Skipped tags: at most {MAX_SKIPPED_TAGS} entries.")
    entries: List[str] = []
    seen: Dict[Tuple[str, ...], int] = {}
    for number, raw in enumerate(value, 1):
        if not isinstance(raw, str):
            raise ValueError(f"Skipped tags: entry {number} must be text.")
        entry = raw.strip()
        if not entry:
            raise ValueError(f"Skipped tags: entry {number} is empty.")
        if len(entry) > MAX_SKIPPED_TAG_CHARS:
            raise ValueError(
                f"Skipped tags: entry {number} must be at most "
                f"{MAX_SKIPPED_TAG_CHARS} characters."
            )
        if _FORBIDDEN_IN_ENTRY.search(entry):
            raise ValueError(
                f"Skipped tags: entry {number} must not contain a comma "
                "or a line break."
            )
        words = tag_words(entry)
        if not words:
            raise ValueError(f"Skipped tags: entry {number} is empty.")
        if words in seen:
            raise ValueError(
                f"Skipped tags: entry {number} repeats entry {seen[words]}."
            )
        seen[words] = number
        entries.append(entry)
    return entries


class SkipMatcher:
    """Whether a suggested tag contains a skipped entry's words, consecutive
    and whole (#431, 17B). Compiled once per request or job; the cost of a
    check depends on the tag's words, not on the number of entries."""

    def __init__(self, entries: Iterable[str] = ()):
        self._entries = frozenset(filter(None, (tag_words(e) for e in entries)))
        self._longest = max((len(words) for words in self._entries), default=0)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def matches(self, name: str) -> bool:
        if not self._entries:
            return False
        words = tag_words(name)
        for start in range(len(words)):
            stop = min(len(words), start + self._longest)
            for end in range(start + 1, stop + 1):
                if words[start:end] in self._entries:
                    return True
        return False

    def counting(self) -> "CountingSkip":
        """A per-request view that also remembers which names it hid."""
        return CountingSkip(self)


class CountingSkip:
    """A :class:`SkipMatcher` that records the distinct names it hid, so the
    Tag image dialog can say how many skipped tags were not suggested."""

    def __init__(self, matcher: SkipMatcher):
        self._matcher = matcher
        self.hidden: Set[str] = set()

    def matches(self, name: str) -> bool:
        if self._matcher.matches(name):
            self.hidden.add(name.casefold())
            return True
        return False


# A WD14 label list's tags the engine can suggest: general (0) and character (4).
WD14_SUGGESTED_CATEGORIES = frozenset({"0", "4"})
_label_cache: Dict[str, Tuple[Tuple[int, int], Tuple[str, ...]]] = {}
_label_cache_lock = threading.Lock()


def read_wd14_label_rows(path) -> List[Dict[str, str]]:
    """Every row of a WD14 ``selected_tags.csv``, read as UTF-8 and closed
    before returning (the handle never outlives the call, for Windows)."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def wd14_label_names(path) -> Tuple[str, ...]:
    """The names a WD14 label list can suggest (categories 0 and 4), cached
    by ``(mtime_ns, size)``. OSError (missing) and ValueError (unreadable)
    propagate; the caller skips that list."""
    key = os.fspath(path)
    stat = os.stat(key)
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _label_cache_lock:
        cached = _label_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        rows = read_wd14_label_rows(key)
    except csv.Error as exc:  # a damaged list (a NUL byte, an oversized field)
        raise ValueError(f"unreadable label list: {exc}") from None
    names = tuple(
        name
        for row in rows
        if row.get("category", "0") in WD14_SUGGESTED_CATEGORIES
        and (name := (row.get("name") or "").strip())
    )
    with _label_cache_lock:
        _label_cache[key] = (stamp, names)
    return names


# Saved instructions (#431): named language-model instructions with a default
# per engine. Built-in is never stored; readers put it first.
BUILTIN_INSTRUCTIONS_ID = "builtin"
BUILTIN_INSTRUCTIONS_NAME = "Built-in"
MAX_SAVED_INSTRUCTIONS = 20
MAX_INSTRUCTION_NAME_CHARS = 60
INSTRUCTION_ENGINES = ("gguf", "hf")
ENGINE_LABELS = {"gguf": "GGUF JoyCaption", "hf": "HuggingFace JoyCaption"}
_INSTRUCTION_ID = re.compile(r"[0-9a-f]{12}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def builtin_instructions() -> Dict[str, str]:
    """The read-only Built-in entry, always the shipped template."""
    return {
        "id": BUILTIN_INSTRUCTIONS_ID,
        "name": BUILTIN_INSTRUCTIONS_NAME,
        "text": DEFAULT_TAG_TEMPLATE,
    }


def _instruction_entry(raw: Any, number: int) -> Dict[str, str]:
    prefix = f"Saved instructions: entry {number}"
    if (
        not isinstance(raw, dict)
        or set(raw) != {"id", "name", "text"}
        or not isinstance(raw["id"], str)
        or _INSTRUCTION_ID.fullmatch(raw["id"]) is None
    ):
        raise ValueError(f"{prefix} has an invalid id.")
    name = raw["name"].strip() if isinstance(raw["name"], str) else ""
    if not name:
        raise ValueError(f"{prefix} needs a name.")
    if len(name) > MAX_INSTRUCTION_NAME_CHARS:
        raise ValueError(
            f"{prefix} name must be at most {MAX_INSTRUCTION_NAME_CHARS} characters."
        )
    if _CONTROL.search(name):
        raise ValueError(f"{prefix} name must not contain a line break.")
    if name.casefold() == BUILTIN_INSTRUCTIONS_NAME.casefold():
        raise ValueError("Saved instructions: the name “Built-in” is reserved.")
    try:
        text = validate_template(raw["text"], f"{prefix} text")
    except ValueError as error:
        raise ValueError(f"{error}.") from None
    return {"id": raw["id"], "name": name, "text": text}


def validate_instructions(value: Any) -> List[Dict[str, str]]:
    """The saved entries ``[{id, name, text}]`` in order (names trimmed), or
    ValueError with the sentence System shows (Built-in is not counted)."""
    if not isinstance(value, list):
        raise ValueError("Saved instructions must be a list.")
    if len(value) > MAX_SAVED_INSTRUCTIONS:
        raise ValueError(
            f"Saved instructions: at most {MAX_SAVED_INSTRUCTIONS} entries besides "
            "Built-in."
        )
    entries: List[Dict[str, str]] = []
    ids: Dict[str, int] = {}
    names: Dict[str, int] = {}
    for number, raw in enumerate(value, 1):
        item = _instruction_entry(raw, number)
        if item["id"] in ids:
            raise ValueError(
                f"Saved instructions: entry {number} repeats the id of entry "
                f"{ids[item['id']]}."
            )
        key = item["name"].casefold()
        if key in names:
            raise ValueError(
                f"Saved instructions: entry {number} name “{item['name']}” is "
                "already used."
            )
        ids[item["id"]] = number
        names[key] = number
        entries.append(item)
    return entries


def validate_default_instructions(value: Any, entries) -> Dict[str, str]:
    """``{gguf: id, hf: id}``, each Built-in or an id of ``entries``."""
    if not isinstance(value, dict) or set(value) != set(INSTRUCTION_ENGINES):
        raise ValueError("Default instructions must name GGUF and HuggingFace.")
    ids = {BUILTIN_INSTRUCTIONS_ID} | {item["id"] for item in entries}
    for engine in INSTRUCTION_ENGINES:
        if not isinstance(value[engine], str) or value[engine] not in ids:
            raise ValueError(
                f"Default for {ENGINE_LABELS[engine]} must be one of the saved "
                "instructions."
            )
    return {engine: value[engine] for engine in INSTRUCTION_ENGINES}
