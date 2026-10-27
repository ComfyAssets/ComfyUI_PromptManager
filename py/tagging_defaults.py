"""The tagging configuration's constants and validators (#328).

Server-free on purpose: ``py.api`` must import without ComfyUI's ``server``
module, which ``py/config.py`` needs; config re-exports all of this.
"""

import math
from typing import Any

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
