"""Request validation and the worker of the Tag dialog's Generate (#249 B2).

``POST /prompt_manager/image-annotations/suggest`` asks one of legacy's four
engines for the top N tags of a captured image. Options are validated
strictly, per engine: WD14 classifiers take two thresholds, the language
models (GGUF and HuggingFace JoyCaption) take the instructions; every engine
takes the suggestion count and the GPU switch. Nothing here touches the
filesystem or the database.
"""

import math

WD14_MODELS = frozenset({"wd14-swinv2", "wd14-vit"})
LLM_MODELS = frozenset({"gguf", "hf"})
MODEL_TYPES = WD14_MODELS | LLM_MODELS

MAX_INSTRUCTION_CHARS = 4000
MAX_INSTRUCTION_BYTES = 16 * 1024

SUGGEST_FIELDS = frozenset(
    {"target", "capture_token", "expected_epoch", "model_type", "options"}
)
_COMMON = frozenset({"suggestion_limit", "use_gpu"})
_WD14_REQUIRED = frozenset({"general_threshold", "character_threshold"})
_LLM_REQUIRED = frozenset({"instructions"})

UNAVAILABLE_HINT = "Download it in System › Auto-tagging."


class OptionError(ValueError):
    """A suggest request whose model type or options are not acceptable."""


def _threshold(name: str, value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise OptionError(f"{name} must be a finite number")
    if not 0 <= value <= 1:
        raise OptionError(f"{name} must be between 0 and 1")
    return float(value)


def _instructions(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OptionError("instructions must be non-empty text")
    if len(value) > MAX_INSTRUCTION_CHARS:
        raise OptionError(
            f"instructions must be at most {MAX_INSTRUCTION_CHARS} characters"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise OptionError("instructions must be valid Unicode text") from None
    if len(encoded) > MAX_INSTRUCTION_BYTES:
        raise OptionError("instructions are too long")
    return value


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
