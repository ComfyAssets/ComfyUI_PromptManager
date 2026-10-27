"""Request validation and the worker of the Tag dialog's Generate (#249 B2).

``POST /prompt_manager/image-annotations/suggest`` asks one of legacy's four
engines for the top N tags of a captured image. Options are validated
strictly, per engine: WD14 classifiers take two thresholds, the language
models (GGUF and HuggingFace JoyCaption) take the instructions; every engine
takes the suggestion count and the GPU switch. Nothing here touches the
filesystem or the database.
"""

import math

from ..tagging_defaults import (  # noqa: F401 - re-exported for the route
    LLM_MODEL_TYPES as LLM_MODELS,
    MAX_INSTRUCTION_BYTES,
    MAX_INSTRUCTION_CHARS,
    MODEL_TYPES,
    WD14_MODEL_TYPES as WD14_MODELS,
    validate_template,
    validate_threshold,
)

SUGGEST_FIELDS = frozenset(
    {"target", "capture_token", "expected_epoch", "model_type", "options"}
)
_COMMON = frozenset({"suggestion_limit", "use_gpu"})
_WD14_REQUIRED = frozenset({"general_threshold", "character_threshold"})
_LLM_REQUIRED = frozenset({"instructions"})

UNAVAILABLE_HINT = "Download it from the legacy Auto-tag page."


class OptionError(ValueError):
    """A suggest request whose model type or options are not acceptable."""


def _threshold(name: str, value) -> float:
    try:
        return validate_threshold(name, value)
    except ValueError as error:
        raise OptionError(str(error)) from None


def _instructions(value) -> str:
    try:
        return validate_template(value)
    except ValueError as error:
        raise OptionError(str(error)) from None


def validate_options(model_type, options, *, default_limit: int, max_limit: int):
    """Normalised ``(model_type, options)`` or :class:`OptionError`.

    The answer echoes every option the request will run with, the defaults
    (suggestion count from the settings, GPU on) filled in.
    """
    if not isinstance(model_type, str) or model_type not in MODEL_TYPES:
        raise OptionError("model_type must be one of " + ", ".join(sorted(MODEL_TYPES)))
    if not isinstance(options, dict):
        raise OptionError("options must be an object")
    required = _WD14_REQUIRED if model_type in WD14_MODELS else _LLM_REQUIRED
    keys = set(options)
    if not required <= keys or not keys <= required | _COMMON:
        raise OptionError(
            f"options for {model_type} take exactly "
            + ", ".join(sorted(required))
            + " and optionally "
            + ", ".join(sorted(_COMMON))
        )
    limit = options.get("suggestion_limit", default_limit)
    if type(limit) is not int or not 1 <= limit <= max_limit:
        raise OptionError(f"suggestion_limit must be an integer from 1 to {max_limit}")
    use_gpu = options.get("use_gpu", True)
    if not isinstance(use_gpu, bool):
        raise OptionError("use_gpu must be true or false")
    normalised = {"use_gpu": use_gpu, "suggestion_limit": limit}
    if model_type in WD14_MODELS:
        for name in sorted(_WD14_REQUIRED):
            normalised[name] = _threshold(name, options[name])
    else:
        normalised["instructions"] = _instructions(options["instructions"])
    return model_type, normalised


def unavailable_message(model_name: str) -> str:
    return f"{model_name} is not downloaded. {UNAVAILABLE_HINT}"


def run_suggestions(service, capture_path: str, model_type: str, options: dict):
    """Blocking: load (or swap to) *model_type*, then suggest for the capture.

    The caller holds the model gate for the whole call; nothing is ever
    downloaded (``load_model`` refuses a model that is not on disk).
    """
    if not service.is_model_loaded() or service.get_loaded_model_type() != model_type:
        service.load_model(model_type, options["use_gpu"])
    return service.generate_tag_suggestions(
        capture_path,
        limit=options["suggestion_limit"],
        prompt=options.get("instructions"),
        general_threshold=options.get("general_threshold"),
        character_threshold=options.get("character_threshold"),
    )


def suggestion_rows(pairs) -> list:
    """``[{name, confidence}]``; a non-finite score is reported as unknown."""
    rows = []
    for name, score in pairs:
        confidence = None
        if score is not None and math.isfinite(score):
            confidence = float(score)
        rows.append({"name": str(name), "confidence": confidence})
    return rows
