"""Bounded scalar output locators, without filesystem resolution or authority."""

import hashlib
import json
import ntpath
import posixpath
import re
from dataclasses import dataclass
from types import ModuleType

from .execution_contexts import text_size


@dataclass(frozen=True)
class OutputReference:
    output_node_id: str
    image_ordinal: int
    folder_type: str
    subfolder: str
    filename: str
    path_style: str
    root_key: str | None


@dataclass(frozen=True)
class OutputBundle:
    references: tuple[OutputReference, ...]
    state: str
    reason: str
    format_version: int = 1
    source: str = "host_final_history"


@dataclass(frozen=True)
class OutputRoots:
    path_style: str
    output_key: str | None
    temp_key: str | None


MAX_REFERENCE_BYTES = 4096
MAX_BUNDLE_BYTES = 128 * 1024
MAX_IMAGE_POSITIONS = 256
MAX_ROOT_BYTES = 8192
REFERENCE_OVERHEAD = 128
BUNDLE_OVERHEAD = 128
STATE_REASONS = {
    "complete": frozenset({"reported", "none_reported"}),
    "partial": frozenset(
        {
            "invalid_evidence",
            "unsupported",
            "limit_reached",
            "root_unavailable",
            "budget_exhausted",
        }
    ),
    "unavailable": frozenset({"unavailable", "invalid_evidence", "budget_exhausted"}),
    "conflicting": frozenset({"conflicting"}),
}


def _path_module(path_style: str) -> ModuleType:
    if type(path_style) is not str or path_style not in ("posix", "windows"):
        raise ValueError("Invalid output path style")
    return posixpath if path_style == "posix" else ntpath


def _scalar_size(value: str, limit: int) -> int:
    size = text_size(value, limit)
    if "\x00" in value:
        raise ValueError("NUL in output scalar")
    return size


def _root_key(path_style: str, folder_type: str, root: object) -> str | None:
    try:
        _scalar_size(root, MAX_ROOT_BYTES)
    except ValueError:
        return None
    paths = _path_module(path_style)
    if not paths.isabs(root):
        return None
    if path_style == "windows":
        drive, _ = ntpath.splitdrive(root)
        portable = root.replace("/", "\\")
        if not drive or portable.startswith(("\\\\?\\", "\\\\.\\")):
            return None
        portable_drive = drive.replace("/", "\\")
        if portable_drive.startswith("\\\\"):
            parts = portable_drive[2:].split("\\")
            if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
                return None
        elif re.fullmatch(r"[a-zA-Z]:", drive) is None:
            return None
    normalized = paths.normpath(root)
    encoded = json.dumps(
        [1, path_style, folder_type, normalized],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_output_roots(
    path_style: str, output_root: object, temp_root: object
) -> OutputRoots:
    """Namespace bounded, lexically absolute configured roots without reading files."""
    _path_module(path_style)
    return OutputRoots(
        path_style,
        _root_key(path_style, "output", output_root),
        _root_key(path_style, "temp", temp_root),
    )


def _reference_size(reference: OutputReference) -> int:
    if type(reference) is not OutputReference:
        raise ValueError("Invalid output reference")
    size = REFERENCE_OVERHEAD
    if (
        type(reference.output_node_id) is not str
        or not reference.output_node_id
        or len(reference.output_node_id) > 128
    ):
        raise ValueError("Invalid output node ID")
    size += _scalar_size(reference.output_node_id, 512)
    if type(reference.image_ordinal) is not int or not (
        0 <= reference.image_ordinal < MAX_IMAGE_POSITIONS
    ):
        raise ValueError("Invalid output image ordinal")
    size += _scalar_size(reference.folder_type, 6)
    if reference.folder_type not in ("output", "temp"):
        raise ValueError("Unsupported output folder type")
    size += _scalar_size(reference.path_style, 7)
    paths = _path_module(reference.path_style)
    size += _scalar_size(reference.subfolder, 2048)
    size += _scalar_size(reference.filename, 1024)
    if not reference.filename or reference.filename in (".", ".."):
        raise ValueError("Invalid output filename")
    separators = ("/", "\\") if reference.path_style == "windows" else ("/",)
    if any(separator in reference.filename for separator in separators):
        raise ValueError("Output filename must be a basename")
    if paths.isabs(reference.subfolder) or paths.splitdrive(reference.subfolder)[0]:
        raise ValueError("Output subfolder must be relative")
    if reference.path_style == "windows" and (
        ":" in reference.filename or ":" in reference.subfolder
    ):
        raise ValueError("Windows output colon or alternate stream")
    if reference.subfolder:
        parts = (
            re.split(r"[/\\]", reference.subfolder)
            if reference.path_style == "windows"
            else reference.subfolder.split("/")
        )
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("Unsafe or ambiguous output subfolder")
    if reference.root_key is not None:
        size += _scalar_size(reference.root_key, 64)
        if re.fullmatch(r"[a-f0-9]{64}", reference.root_key) is None:
            raise ValueError("Invalid output root key")
    if size > MAX_REFERENCE_BYTES:
        raise ValueError("Output reference payload limit reached")
    return size


def validate_output_reference(reference: OutputReference) -> None:
    _reference_size(reference)


def validate_output_bundle(bundle: OutputBundle) -> None:
    output_payload_size(bundle)


def output_payload_size(bundle: OutputBundle) -> int:
    """Validate the immutable capture and measure its charged scalar payload."""
    if (
        type(bundle) is not OutputBundle
        or type(bundle.format_version) is not int
        or bundle.format_version != 1
        or type(bundle.references) is not tuple
        or len(bundle.references) > MAX_IMAGE_POSITIONS
    ):
        raise ValueError("Invalid output bundle")
    for value in (bundle.state, bundle.reason, bundle.source):
        _scalar_size(value, 128)
    if bundle.source != "host_final_history" or bundle.reason not in STATE_REASONS.get(
        bundle.state, ()
    ):
        raise ValueError("Invalid output state, reason or source")
    if bundle.state == "complete":
        if bool(bundle.references) != (bundle.reason == "reported"):
            raise ValueError("Inconsistent complete output rows")
    if bundle.state == "unavailable" and bundle.references:
        raise ValueError("Unavailable output capture has rows")
    if bundle.reason == "root_unavailable" and not bundle.references:
        raise ValueError("Root unavailability requires output rows")
    size = BUNDLE_OVERHEAD
    previous = None
    for reference in bundle.references:
        size += _reference_size(reference)
        coordinate = (reference.output_node_id, reference.image_ordinal)
        if previous is not None and coordinate <= previous:
            raise ValueError("Output coordinates must be unique and sorted")
        previous = coordinate
        if bundle.state == "complete" and reference.root_key is None:
            raise ValueError("Complete output rows require root keys")
        if size > MAX_BUNDLE_BYTES:
            raise ValueError("Output bundle payload limit reached")
    return size
