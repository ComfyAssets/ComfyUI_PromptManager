"""Read-only native library with bounded, filter-scoped continuation cursors."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from .images import _canonical, _media_access_error
from .pairs import pair_response

_SECRET = os.urandom(32)


class _NameCursorStore:
    """Full lexical keys stay off URLs, bounded by age, count and UTF-8 bytes."""

    def __init__(
        self,
        ttl: int = 3600,
        max_entries: int = 128,
        max_bytes: int = 8 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        self.ttl = ttl
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.size = 0

    def _remove(self, reference: str) -> None:
        _expires, _scope, raw = self.entries.pop(reference)
        self.size -= len(raw)

    def _prune(self) -> None:
        now = self.clock()
        for reference, (expires, _scope, _raw) in list(self.entries.items()):
            if expires <= now:
                self._remove(reference)

    def put(self, key: object, scope: str) -> str:
        raw = json.dumps(key, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > min(512 * 1024, self.max_bytes):
            raise ValueError("Prompt name exceeds the cursor limit")
        self._prune()
        while self.entries and (
            len(self.entries) >= self.max_entries
            or self.size + len(raw) > self.max_bytes
        ):
            self._remove(next(iter(self.entries)))
        reference = base64.urlsafe_b64encode(os.urandom(24)).decode()
        self.entries[reference] = (self.clock() + self.ttl, scope, raw)
        self.size += len(raw)
        return reference

    def get(self, reference: str, scope: str) -> object:
        self._prune()
        entry = self.entries.get(reference)
        if entry is None or entry[1] != scope:
            raise ValueError("Name cursor expired; refresh the library")
        self.entries.move_to_end(reference)
        return json.loads(entry[2])


_NAME_CURSORS = _NameCursorStore()


def _scope(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _encode(key: object, scope: str, *, name: bool = False) -> str | None:
    if key is None:
        return None
    value = (
        {"scope": scope, "reference": _NAME_CURSORS.put(key, scope)}
        if name
        else {"scope": scope, "key": key}
    )
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    signature = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _decode(token: str | None, scope: str) -> object:
    if token is None:
        return None
    try:
        if not 1 <= len(token) <= 4096:
            raise ValueError
        decoded = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        signature, payload = decoded[:32], decoded[32:]
        if not hmac.compare_digest(
            signature, hmac.new(_SECRET, payload, hashlib.sha256).digest()
        ):
            raise ValueError
        value = json.loads(payload)
        if (
            set(value) not in ({"scope", "key"}, {"scope", "reference"})
            or value["scope"] != scope
        ):
            raise ValueError
        if "reference" in value:
            return _NAME_CURSORS.get(value["reference"], scope)
        return value["key"]
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise ValueError("Invalid library cursor; refresh the library") from None


def _positive_id(request: web.Request) -> int:
    value = int(request.match_info["positive_id"])
    if not 1 <= value <= 2**63 - 1:
        raise ValueError("Invalid positive prompt ID")
    return value


def _page_limit(request: web.Request, key: str = "limit") -> int:
    value = int(request.query.get(key, "40"))
    if not 1 <= value <= 200:
        raise ValueError("limit must be between 1 and 200")
    return value


def _missing() -> web.Response:
    return web.json_response(
        {"success": False, "error": "Positive prompt not found"}, status=404
    )


class LibraryRoutesMixin:
    def _register_library_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/library")(self.list_library)
        routes.get("/prompt_manager/library/options")(self.get_library_options)
        routes.get("/prompt_manager/library/{positive_id}")(self.get_library_positive)
        routes.get("/prompt_manager/library/{positive_id}/images")(
            self.get_library_images
        )

    def _library_media(self, records: list[dict], roots: list[Path]) -> list[dict]:
        page = []
        for record in records:
            try:
                raw_path = Path(record["image_path"])
                if not raw_path.is_absolute():
                    continue
                path = _canonical(raw_path)
                denied = _media_access_error(path, roots)
                if denied is not None or not path.is_file():
                    continue
                for index, root in enumerate(roots):
                    canonical_root = _canonical(root)
                    if path.is_relative_to(canonical_root):
                        page.append((path, 0, canonical_root, index))
                        break
            except (OSError, ValueError, RuntimeError):
                continue
        return self._format_workspace_page(page)

    @pair_response
    async def get_library_options(self, request: web.Request) -> web.Response:
        result = await self._run_in_executor(self.db.get_library_options)
        return web.json_response({"success": True, **result})

    @pair_response
    async def list_library(self, request: web.Request) -> web.Response:
        options = {
            "text": request.query.get("text", ""),
            "category": request.query.get("category"),
            "tags": sorted(set(request.query.getall("tag", []))),
            "sort": request.query.get("sort", "newest"),
        }
        scope = _scope({"kind": "parents", **options})
        result = await self._run_in_executor(
            self.db.list_library,
            **options,
            limit=_page_limit(request),
            after=_decode(request.query.get("cursor"), scope),
        )
        roots = list(self._get_all_output_dirs())

        def previews() -> None:
            records = self.db.get_library_previews(
                [parent["id"] for parent in result["items"]]
            )
            for parent in result["items"]:
                parent["previews"] = self._library_media(records[parent["id"]], roots)

        await self._run_in_executor(previews)
        result["next_cursor"] = _encode(
            result.pop("next_key"), scope, name=options["sort"] == "name"
        )
        return web.json_response({"success": True, **result})

    @pair_response
    async def get_library_positive(self, request: web.Request) -> web.Response:
        positive = _positive_id(request)
        scope = _scope({"kind": "variants", "positive": positive})
        result = await self._run_in_executor(
            self.db.get_library_positive,
            positive,
            variant_limit=_page_limit(request, "variant_limit"),
            variant_after=_decode(request.query.get("variant_cursor"), scope),
        )
        if result is None:
            return _missing()
        roots = list(self._get_all_output_dirs())
        records = await self._run_in_executor(
            self.db.list_library_images, result["positive"]["id"], limit=2
        )
        result["positive"]["previews"] = await self._run_in_executor(
            self._library_media, records["images"], roots
        )
        result["variants_next_cursor"] = _encode(result.pop("variants_next_key"), scope)
        return web.json_response({"success": True, **result})

    @pair_response
    async def get_library_images(self, request: web.Request) -> web.Response:
        positive = _positive_id(request)
        pair = request.query.get("pair_id")
        pair = int(pair) if pair is not None else None
        if pair is not None and not 1 <= pair <= 2**63 - 1:
            raise ValueError("Invalid variant ID")
        roots = list(self._get_all_output_dirs())
        scope = _scope(
            {
                "kind": "images",
                "positive": positive,
                "pair": pair,
                "roots": [str(_canonical(root)) for root in roots],
            }
        )
        if await self._run_in_executor(self.db.get_library_positive, positive) is None:
            return _missing()
        result = await self._run_in_executor(
            self.db.list_library_images,
            positive,
            pair_id=pair,
            limit=_page_limit(request),
            after=_decode(request.query.get("cursor"), scope),
        )
        result["images"] = await self._run_in_executor(
            self._library_media, result["images"], roots
        )
        result["next_cursor"] = _encode(result.pop("next_key"), scope)
        return web.json_response({"success": True, **result})
