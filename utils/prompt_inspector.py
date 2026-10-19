"""Read prompt evidence without inference, indexing, or decoding image pixels."""

import hashlib
import io
import json
import struct
import subprocess  # nosec B404
import threading
import zlib
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

from PIL import Image

from .pair_execution import EXECUTION_KEY
from .prompt_pair_resolver import (
    INSPECTION_PROFILE,
    ResolverProfile,
    resolve_prompt_pair,
)
from .video_metadata import ffprobe_path, is_video_path

# Role records (prompt, parameters, the pair execution record) share one
# budget, encoded and decoded: roles cannot be trusted from a partial record,
# so exceeding it fails the whole read. A workflow has its own budget; past it
# only the workflow is ``limited``. Other text chunks are never retained.
ROLE_METADATA_BYTES = 2 * 1024 * 1024
WORKFLOW_METADATA_BYTES = 4 * 1024 * 1024
# Total bytes read for byte-identical duplicate chunks of one file, per class
# so workflow repeats can never starve role records: two budgets each, making
# repeated chunks cost bounded I/O instead of file size.
DUPLICATE_WORKFLOW_BYTES = 2 * WORKFLOW_METADATA_BYTES
DUPLICATE_ROLE_BYTES = 2 * ROLE_METADATA_BYTES
MAX_METADATA_BYTES = ROLE_METADATA_BYTES  # video stdout bound, unchanged here
MAX_KEYWORD_BYTES = 80  # a 1-79 byte PNG keyword plus its NUL separator
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_PNG_CHUNKS = 10_000
_ROLE_METADATA_KEYS = frozenset({"prompt", "parameters", EXECUTION_KEY})
_METADATA_KEYS = _ROLE_METADATA_KEYS | {"workflow"}
_TEXT_CHUNKS = (b"tEXt", b"zTXt", b"iTXt")
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MetadataReadError(ValueError):
    """Prompt evidence is unreadable, incomplete, or exceeds inspection limits."""


@dataclass(frozen=True)
class InspectionRead:
    """One bounded read, with its transient exact video source kept private."""

    metadata: dict[str, str]
    record_formats: dict[str, str]
    video_carrier_text: str | None
    format: str | None
    width: int | None
    height: int | None
    # Records present but over their budget: no text, format ``limited``.
    limited_records: frozenset[str] = dataclass_field(default_factory=frozenset)


@dataclass(frozen=True)
class _VideoInspection:
    metadata: dict[str, str | None]
    record_formats: dict[str, str]
    carrier_text: str | None


def _inflate(data: bytes, limit: int) -> bytes | None:
    """Inflate at most ``limit + 1`` bytes; ``None`` when the output is over limit."""
    inflater = zlib.decompressobj()
    result = inflater.decompress(data, limit + 1)
    if len(result) > limit:
        return None
    if not inflater.eof or inflater.unused_data:
        raise MetadataReadError("Compressed metadata is invalid or exceeds limits")
    return result


def _keyword(prefix: bytes) -> str:
    keyword, separator, _ = prefix.partition(b"\0")
    if not separator or not 1 <= len(keyword) <= 79:
        raise MetadataReadError("Malformed PNG text metadata")
    return keyword.decode("latin-1")


def _png_text(kind: bytes, data: bytes, limit: int) -> tuple[str, str | None]:
    """Decode one retained text chunk; ``None`` text when it inflates past limit."""
    _, _, body = data.partition(b"\0")
    key = _keyword(data[:MAX_KEYWORD_BYTES])
    encoding = "latin-1"
    if kind == b"zTXt":
        if not body or body[0] != 0:
            raise MetadataReadError("Unsupported PNG text compression")
        body = _inflate(body[1:], limit)
    elif kind == b"iTXt":
        if len(body) < 2 or body[0] not in (0, 1):
            raise MetadataReadError("Malformed international PNG metadata")
        flag, method = body[:2]
        fields = body[2:].split(b"\0", 2)
        if len(fields) != 3 or (flag and method != 0):
            raise MetadataReadError("Malformed international PNG metadata")
        fields[0].decode("ascii")
        fields[1].decode("utf-8")
        body = _inflate(fields[2], limit) if flag else fields[2]
        encoding = "utf-8"
    if body is None:
        return key, None
    if b"\0" in body:
        raise MetadataReadError("Malformed PNG text metadata")
    return key, body.decode(encoding)


class _PngRecords:
    """Retained records with per-class budgets and byte-identical duplicates."""

    def __init__(self) -> None:
        self.found: dict[str, str] = {}
        self.limited: set[str] = set()
        self.digests: dict[str, tuple[int, bytes]] = {}
        self.role_encoded = self.role_decoded = 0
        self.duplicate_workflow = self.duplicate_role = 0

    def skip(self, key: str, length: int) -> bool:
        """Decide from the keyword and length alone whether to seek past a chunk."""
        if key not in _METADATA_KEYS or key in self.limited:
            return True  # never retained, or a duplicate of a limited workflow
        if key in self.digests:
            if self.digests[key][0] != length:
                raise MetadataReadError("Duplicate prompt metadata")
            if key == "workflow":
                if self.duplicate_workflow + length > DUPLICATE_WORKFLOW_BYTES:
                    self.limited.add(key)
                    self.found.pop(key, None)
                    return True
                self.duplicate_workflow += length
            else:
                if self.duplicate_role + length > DUPLICATE_ROLE_BYTES:
                    raise MetadataReadError("Metadata exceeds inspection limits")
                self.duplicate_role += length
            return False  # same length: read and compare bytes
        if key == "workflow":
            if length > WORKFLOW_METADATA_BYTES:
                self.limited.add(key)
                return True
            return False
        self.role_encoded += length
        if self.role_encoded > ROLE_METADATA_BYTES:
            raise MetadataReadError("Metadata exceeds inspection limits")
        return False

    def add(self, kind: bytes, data: bytes) -> None:
        key = _keyword(data[:MAX_KEYWORD_BYTES])
        digest = hashlib.sha256(kind + data).digest()
        if key in self.digests:
            if self.digests[key][1] != digest:
                raise MetadataReadError("Duplicate prompt metadata")
            return
        self.digests[key] = (len(data), digest)
        workflow = key == "workflow"
        limit = (
            WORKFLOW_METADATA_BYTES
            if workflow
            else ROLE_METADATA_BYTES - self.role_decoded
        )
        _, value = _png_text(kind, data, limit)
        size = None if value is None else len(value.encode("utf-8"))
        if workflow:
            if size is None or size > WORKFLOW_METADATA_BYTES:
                self.limited.add(key)
                return
        else:
            self.role_decoded += limit + 1 if size is None else size
            if self.role_decoded > ROLE_METADATA_BYTES:
                raise MetadataReadError("Metadata exceeds inspection limits")
        self.found[key] = value


def _png_metadata(stream: Any, size: int) -> tuple[dict, frozenset, int, int]:
    """CRC-check retained text and structural chunks, seeking past everything else.

    PNG text chunks may follow IDAT. Pillow's ``.text`` loads pixels to find
    them; this bounded reader instead follows the W3C PNG chunk layout. A text
    chunk's keyword is read first (at most ``MAX_KEYWORD_BYTES``); chunks that
    are not retained, or are over their budget, are skipped by length, so their
    bytes are never held in memory or checksummed. IDAT checksums/pixel
    validity are deliberately outside metadata inspection.
    """
    records = _PngRecords()
    saw_header = saw_pixels = False
    for index in range(MAX_PNG_CHUNKS):
        header = stream.read(8)
        if len(header) != 8:
            raise MetadataReadError("Incomplete PNG metadata")
        length, kind = struct.unpack(">I4s", header)
        if length > 0x7FFFFFFF or stream.tell() + length + 4 > size:
            raise MetadataReadError("Incomplete PNG chunk")
        if index == 0 and (kind != b"IHDR" or length != 13):
            raise MetadataReadError("Missing PNG header")
        if kind in _TEXT_CHUNKS:
            prefix = stream.read(min(length, MAX_KEYWORD_BYTES))
            if records.skip(_keyword(prefix), length):
                stream.seek(length - len(prefix) + 4, 1)
                continue
            data = prefix + stream.read(length - len(prefix))
        elif kind in (b"IHDR", b"IEND"):
            data = stream.read(length)
        else:
            saw_pixels |= kind == b"IDAT"
            stream.seek(length + 4, 1)
            continue
        checksum = stream.read(4)
        if zlib.crc32(kind + data) != int.from_bytes(checksum, "big"):
            raise MetadataReadError("PNG metadata checksum mismatch")
        if kind == b"IHDR":
            if saw_header or not all(struct.unpack(">II", data[:8])):
                raise MetadataReadError("Malformed PNG header")
            saw_header = True
            width, height = struct.unpack(">II", data[:8])
        elif kind == b"IEND":
            if length or not saw_pixels:
                raise MetadataReadError("Incomplete PNG image")
            return records.found, frozenset(records.limited), width, height
        else:
            records.add(kind, data)
    raise MetadataReadError("PNG chunk count exceeds inspection limits")


def _video_metadata(path: Path) -> _VideoInspection:
    probe = ffprobe_path()
    if not probe:
        raise MetadataReadError("Video metadata reader is unavailable")
    command = [
        probe,
        "-v",
        "quiet",
        "-show_entries",
        "format_tags=comment,description",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.Popen(  # nosec B603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def stop() -> None:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                # Timeout and final cleanup can race with the process exiting.
                pass

    timer = threading.Timer(3, stop)
    timer.daemon = True
    timer.start()
    try:
        raw = process.stdout.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise MetadataReadError("Video metadata exceeds inspection limits")
        process.wait()
        if process.returncode:
            raise MetadataReadError("Video metadata could not be read")
    finally:
        timer.cancel()
        stop()
        process.wait()
        process.stdout.close()
    probed = json.loads(raw)
    if not isinstance(probed, dict) or not isinstance(probed.get("format"), dict):
        raise MetadataReadError("Malformed video metadata")
    tags = probed["format"].get("tags", {})
    if not isinstance(tags, dict):
        raise MetadataReadError("Malformed video metadata")
    # The scan normalizer drops null fields and unknown keys. Inspection must
    # retain explicit prompt/execution markers so malformed evidence cannot
    # silently fall back to an unrelated saved recipe or another comment.
    candidates = []
    for key in ("comment", "description"):
        raw = tags.get(key)
        if not isinstance(raw, str):
            continue
        try:
            envelope = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(envelope, dict):
            continue
        candidates.append((raw, envelope))
    selected = next(
        (item for item in candidates if _ROLE_METADATA_KEYS.intersection(item[1])),
        None,
    )
    if selected is None:
        selected = next((item for item in candidates if "workflow" in item[1]), None)
    if selected is not None:
        raw, envelope = selected
        metadata, formats = {}, {}
        for field in _METADATA_KEYS.intersection(envelope):
            value = envelope[field]
            if field == "workflow" and not isinstance(value, (str, dict)):
                formats[field] = "invalid"
                continue
            metadata[field] = (
                value if value is None or isinstance(value, str) else json.dumps(value)
            )
            formats[field] = (
                "original-text" if isinstance(value, str) else "reserialized-json"
            )
        return _VideoInspection(metadata, formats, raw)
    return _VideoInspection({}, {}, None)


def read_inspection_file(path: Path) -> InspectionRead:
    """Read bounded metadata and header facts without decoding media pixels."""
    try:
        formats, carrier, limited = {}, None, frozenset()
        format_name = width = height = None
        if is_video_path(path):
            video = _video_metadata(path)
            metadata, formats, carrier = (
                video.metadata,
                video.record_formats,
                video.carrier_text,
            )
        else:
            with path.open("rb") as stream:
                signature = stream.read(8)
                if signature == _SIGNATURE:
                    metadata, limited, width, height = _png_metadata(
                        stream, path.stat().st_size
                    )
                    format_name = "PNG"
                else:
                    stream.seek(0)
                    header = stream.read(MAX_HEADER_BYTES)
                    with Image.open(io.BytesIO(header)) as image:
                        metadata = dict(image.info)
                        format_name, (width, height) = image.format, image.size
        result, role_size, limited = {}, 0, set(limited)
        for key in _METADATA_KEYS:
            if key not in metadata:
                continue
            value = metadata[key]
            if key == "workflow" and not isinstance(value, str):
                formats[key] = "invalid"
                continue
            if not isinstance(value, str):
                raise MetadataReadError("Malformed prompt metadata")
            size = len(value.encode("utf-8", "surrogatepass"))
            if key == "workflow":
                if size > WORKFLOW_METADATA_BYTES:
                    limited.add(key)
                    continue
            else:
                role_size += size
                if role_size > ROLE_METADATA_BYTES:
                    raise MetadataReadError("Metadata exceeds inspection limits")
            result[key] = value
            formats.setdefault(key, "original-text")
        if _ROLE_METADATA_KEYS.intersection(limited):
            # A limited role record would vanish and let fallback evidence win.
            raise MetadataReadError("Metadata exceeds inspection limits")
        for key in limited:
            formats[key] = "limited"
        return InspectionRead(
            result,
            formats,
            carrier,
            format_name,
            width,
            height,
            frozenset(limited),
        )
    except MetadataReadError:
        raise
    except (
        OSError,
        ValueError,
        EOFError,
        SyntaxError,
        RecursionError,
        struct.error,
        zlib.error,
    ):
        raise MetadataReadError("Media metadata could not be read") from None


def read_inspection_metadata(path: Path) -> dict[str, str]:
    """Compatible metadata-only projection of the single inspection read."""
    return read_inspection_file(path).metadata


def _side(status: str, text: str | None = None, source: str | None = None) -> dict:
    return {"status": status, "text": text, "source": source}


def inspect_prompt_evidence(
    metadata: dict,
    saved: dict | None = None,
    *,
    profile: ResolverProfile = INSPECTION_PROFILE,
) -> dict:
    """Keep uncertainty visible; a saved association never replaces file evidence.

    Inspection routes resolve with ``INSPECTION_PROFILE``: bounded work, not
    graph size. Ingestion (scan, live monitor, admin import) calls the resolver
    directly with its default profile. ``resolution`` carries the closed
    ``reason_code`` and, for a blocking node, ``node: {id, class_type}``.
    """
    metadata = {
        key: value for key, value in metadata.items() if key in _ROLE_METADATA_KEYS
    }
    resolution = {
        "state": "unresolved",
        "sampler_id": None,
        "reason": "Prompt evidence is malformed",
        "reason_code": "malformed",
        "node": None,
    }
    if not metadata:
        result = {role: _side("absent") for role in ("positive", "negative")}
        if saved:
            context = saved.get("pair_context")
            paired = isinstance(saved.get("pair"), dict) or isinstance(context, dict)
            for role in ("positive", "negative") if paired else ("positive",):
                result[role] = {
                    **_side("unresolved", source="saved-association"),
                    "message": (
                        "A saved library association exists, but it cannot be "
                        "verified against this file."
                    ),
                }
        return {
            "prompts": result,
            "resolution": {
                "state": "absent",
                "sampler_id": None,
                "reason": "No parsed prompt metadata",
                "reason_code": None,
                "node": None,
            },
        }
    canonical = dict(metadata)
    if "prompt" not in canonical and "parameters" not in canonical:
        return {
            "prompts": {role: _side("unresolved") for role in ("positive", "negative")},
            "resolution": resolution,
        }
    if isinstance(canonical.get("prompt"), str):
        try:
            canonical["prompt"] = json.loads(canonical["prompt"])
        except (ValueError, RecursionError):
            return {
                "prompts": {
                    role: _side("unresolved") for role in ("positive", "negative")
                },
                "resolution": resolution,
            }
    resolved = resolve_prompt_pair(canonical, profile=profile)
    state = resolved["state"]
    result = {}
    for role in ("positive", "negative"):
        side = resolved[role]
        if (
            state != "ambiguous"
            and isinstance(side, dict)
            and (role == "negative" or side["text"].strip())
        ):
            result[role] = _side(
                "resolved", side["text"], side["provenance"]["text_source"]
            )
        else:
            result[role] = _side(state if state != "resolved" else "unresolved")
    return {
        "prompts": result,
        "resolution": {
            "state": state,
            "sampler_id": resolved.get("sampler_id"),
            "reason": resolved.get("reason", ""),
            "reason_code": resolved.get("reason_code"),
            "node": resolved.get("node"),
        },
    }


def inspect_prompt_roles(metadata: dict, saved: dict | None = None) -> dict:
    """Keep the established role-only helper contract."""
    return inspect_prompt_evidence(metadata, saved)["prompts"]
