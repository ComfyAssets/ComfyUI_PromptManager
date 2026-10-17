"""Read prompt evidence without inference, indexing, or decoding image pixels."""

import io
import json
import struct
import subprocess  # nosec B404
import threading
import zlib
from pathlib import Path
from typing import Any

from PIL import Image

from .pair_execution import EXECUTION_KEY
from .prompt_pair_resolver import resolve_prompt_pair
from .video_metadata import ffprobe_path, is_video_path, metadata_from_tags

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_PNG_CHUNKS = 10_000
_METADATA_KEYS = frozenset({"prompt", "parameters", EXECUTION_KEY})
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MetadataReadError(ValueError):
    """Prompt evidence is unreadable, incomplete, or exceeds inspection limits."""


def _inflate(data: bytes) -> bytes:
    inflater = zlib.decompressobj()
    result = inflater.decompress(data, MAX_METADATA_BYTES + 1)
    if len(result) > MAX_METADATA_BYTES or not inflater.eof or inflater.unused_data:
        raise MetadataReadError("Compressed metadata is invalid or exceeds limits")
    return result


def _png_text(kind: bytes, data: bytes) -> tuple[str, str]:
    keyword, separator, body = data.partition(b"\0")
    if not separator or not 1 <= len(keyword) <= 79:
        raise MetadataReadError("Malformed PNG text metadata")
    key = keyword.decode("latin-1")
    encoding = "latin-1"
    if kind == b"zTXt":
        if not body or body[0] != 0:
            raise MetadataReadError("Unsupported PNG text compression")
        body = _inflate(body[1:])
    elif kind == b"iTXt":
        if len(body) < 2 or body[0] not in (0, 1):
            raise MetadataReadError("Malformed international PNG metadata")
        flag, method = body[:2]
        fields = body[2:].split(b"\0", 2)
        if len(fields) != 3 or (flag and method != 0):
            raise MetadataReadError("Malformed international PNG metadata")
        fields[0].decode("ascii")
        fields[1].decode("utf-8")
        body = _inflate(fields[2]) if flag else fields[2]
        encoding = "utf-8"
    if b"\0" in body:
        raise MetadataReadError("Malformed PNG text metadata")
    return key, body.decode(encoding)


def _png_metadata(stream: Any, size: int) -> dict:
    """CRC-check text and structural chunks, seeking past compressed pixels.

    PNG text chunks may follow IDAT. Pillow's ``.text`` loads pixels to find
    them; this bounded reader instead follows the W3C PNG chunk layout.
    IDAT checksums/pixel validity are deliberately outside metadata inspection.
    """
    found, compressed_size, expanded_size = {}, 0, 0
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
        if kind in (b"tEXt", b"zTXt", b"iTXt", b"IHDR", b"IEND"):
            compressed_size += length
            if compressed_size > MAX_METADATA_BYTES:
                raise MetadataReadError("Metadata exceeds inspection limits")
            data, checksum = stream.read(length), stream.read(4)
            if zlib.crc32(kind + data) != int.from_bytes(checksum, "big"):
                raise MetadataReadError("PNG metadata checksum mismatch")
            if kind == b"IHDR":
                if saw_header or not all(struct.unpack(">II", data[:8])):
                    raise MetadataReadError("Malformed PNG header")
                saw_header = True
            elif kind == b"IEND":
                if length or not saw_pixels:
                    raise MetadataReadError("Incomplete PNG image")
                return found
            else:
                key, value = _png_text(kind, data)
                expanded_size += len(value.encode("utf-8"))
                if expanded_size > MAX_METADATA_BYTES:
                    raise MetadataReadError("Metadata exceeds inspection limits")
                if key in _METADATA_KEYS:
                    if key in found:
                        raise MetadataReadError("Duplicate prompt metadata")
                    found[key] = value
        else:
            saw_pixels |= kind == b"IDAT"
            stream.seek(length + 4, 1)
    raise MetadataReadError("PNG chunk count exceeds inspection limits")


def _video_metadata(path: Path) -> dict:
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
    for key in ("comment", "description"):
        raw = tags.get(key)
        if not isinstance(raw, str):
            continue
        try:
            envelope = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(envelope, dict) or not _METADATA_KEYS.intersection(envelope):
            continue
        metadata = metadata_from_tags({key: raw})
        for field in _METADATA_KEYS.intersection(envelope):
            value = envelope[field]
            metadata[field] = (
                value if value is None or isinstance(value, str) else json.dumps(value)
            )
        return metadata
    return {}


def read_inspection_metadata(path: Path) -> dict:
    """Return bounded, header-only metadata or raise a response-safe error."""
    try:
        if is_video_path(path):
            metadata = _video_metadata(path)
        else:
            with path.open("rb") as stream:
                signature = stream.read(8)
                if signature == _SIGNATURE:
                    return _png_metadata(stream, path.stat().st_size)
                stream.seek(0)
                header = stream.read(MAX_HEADER_BYTES)
            with Image.open(io.BytesIO(header)) as image:
                metadata = dict(image.info)
        result, size = {}, 0
        for key in _METADATA_KEYS:
            if key not in metadata:
                continue
            value = metadata[key]
            if not isinstance(value, str):
                raise MetadataReadError("Malformed prompt metadata")
            size += len(value.encode("utf-8"))
            if size > MAX_METADATA_BYTES:
                raise MetadataReadError("Metadata exceeds inspection limits")
            result[key] = value
        return result
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


def _side(status: str, text: str | None = None, source: str | None = None) -> dict:
    return {"status": status, "text": text, "source": source}


def inspect_prompt_roles(metadata: dict, saved: dict | None = None) -> dict:
    """Keep uncertainty visible; a saved association never replaces file evidence."""
    if not metadata:
        context = saved.get("pair_context") if saved else None
        if isinstance(context, dict) and all(
            isinstance(context.get(f"{role}_text"), str)
            for role in ("positive", "negative")
        ):
            return {
                role: _side("resolved", context[f"{role}_text"], "saved-association")
                for role in ("positive", "negative")
            }
        positive = saved.get("text") if saved else None
        return {
            "positive": (
                _side("resolved", positive, "saved-association")
                if isinstance(positive, str)
                else _side("absent")
            ),
            "negative": _side("absent"),
        }
    canonical = dict(metadata)
    if "prompt" not in canonical and "parameters" not in canonical:
        return {role: _side("unresolved") for role in ("positive", "negative")}
    if isinstance(canonical.get("prompt"), str):
        try:
            canonical["prompt"] = json.loads(canonical["prompt"])
        except (ValueError, RecursionError):
            return {role: _side("unresolved") for role in ("positive", "negative")}
    resolution = resolve_prompt_pair(canonical)
    state = resolution["state"]
    result = {}
    for role in ("positive", "negative"):
        side = resolution[role]
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
    return result
