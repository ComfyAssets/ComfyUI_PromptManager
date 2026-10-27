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
# What a batch does with a prompt that already has tags (#328).
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
