"""Collections (#331): named, hand-picked groups of images.

Routes under ``/prompt_manager/collections``. A file is named by its listing
identity ``{root_index, root_scope, path}`` (``media_target.py``) and stored
under the favorites' keys (``_favorite_root_key``, ``_favorite_relative_key``),
which never leave the server. A stale scope (the sources were reordered) is a
409 ``source-changed`` before anything is written. Responses are
``{"success": bool, ...}``; an unexpected failure is a logged 500 with a
generic message, never the exception text.
"""

import hmac
import re
from functools import wraps
from typing import Callable

from aiohttp import web

from .images import (
    _favorite_relative_key,
    _favorite_root_key,
    _forbidden,
    _json_body_or_error,
    collection_not_found,
)
from .media_target import media_target
from .prompts import bad_request

try:
    from ...database.image_collections import (
        CollectionFilesMissing,
        CollectionInvalid,
        CollectionMissing,
        CollectionNameTaken,
        normalize_collection_name,
    )
    from ...database.image_favorites import MAX_KEY_LENGTH
except ImportError:
    from database.image_collections import (
        CollectionFilesMissing,
        CollectionInvalid,
        CollectionMissing,
        CollectionNameTaken,
        normalize_collection_name,
    )
    from database.image_favorites import MAX_KEY_LENGTH

BASE = "/prompt_manager/collections"
# The most files one add or remove names (the bulk tagging cap, MC12).
MAX_ITEMS = 500
MAX_ID = 999_999_999
_PATH_ID = re.compile(r"[1-9][0-9]{0,8}")
_ROOT_INDEX = re.compile(r"[0-9]{1,9}")
_ADD_TARGETS = (frozenset({"collection_id", "items"}), frozenset({"name", "items"}))
_REMOVE_FIELDS = frozenset({"collection_id", "items"})
_RENAME_FIELDS = frozenset({"name"})
_MEDIA_MESSAGE = "Only media files can be added to a collection"
READ_FAILED = "Collections could not be read."
CHANGE_FAILED = "The collection could not be changed."
SOURCES_CHANGED = "The sources changed. Refresh the images and try again."
FILES_NOT_FOUND = (
    "None of the selected images could be found. Refresh the images and try again."
)
NAME_TAKEN = "Another collection already has this name."


class _Refused(Exception):
    """A request refused before any write; carries its response."""

    def __init__(self, response: web.Response):
        super().__init__(response.status)
        self.response = response


def _coded(status: int, code: str, message: str, **extra) -> web.Response:
    return web.json_response(
        {"success": False, "code": code, "error": message, **extra},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _collection_response(failure: str) -> Callable:
    """Map the database's refusals to their status; anything else to a 500."""

    def decorate(handler: Callable) -> Callable:
        @wraps(handler)
        async def wrapped(self, request: web.Request) -> web.Response:
            try:
                return await handler(self, request)
            except _Refused as refused:
                return refused.response
            except CollectionInvalid as exc:
                return bad_request(str(exc))
            except CollectionMissing:
                return collection_not_found()
            except CollectionFilesMissing:
                return _coded(404, "files-not-found", FILES_NOT_FOUND)
            except CollectionNameTaken as exc:
                return _coded(
                    409, "name-taken", NAME_TAKEN, collection_id=exc.collection_id
                )
            except Exception:
                self.logger.exception("Collection request failed")
                return web.json_response(
                    {"success": False, "error": failure}, status=500
                )

        return wrapped

    return decorate


def _path_id(request: web.Request) -> int:
    raw = request.match_info["collection_id"]
    if not raw.isascii() or _PATH_ID.fullmatch(raw) is None:
        raise _Refused(bad_request("Invalid collection id"))
    return int(raw)


def _body_id(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise _Refused(bad_request("Invalid collection id"))
    return value


async def _body(request: web.Request, allowed: tuple) -> dict:
    data, error = await _json_body_or_error(request)
    if error is not None:
        raise _Refused(error)
    if frozenset(data) not in allowed:
        names = " or ".join(", ".join(sorted(fields)) for fields in allowed)
        raise _Refused(bad_request(f"Request must contain exactly {names}"))
    return data


class CollectionRoutesMixin:
    """Mixin providing the collection routes (#331)."""

    def _register_collection_routes(self, routes: web.RouteTableDef) -> None:
        # The fixed paths go first: "/items" must never match "/{collection_id}".
        routes.get(BASE)(self.list_collections)
        routes.post(BASE + "/items")(self.add_collection_items)
        routes.delete(BASE + "/items")(self.remove_collection_items)
        routes.get(BASE + "/membership")(self.collection_membership)
        routes.patch(BASE + "/{collection_id}")(self.rename_collection)
        routes.delete(BASE + "/{collection_id}")(self.delete_collection)

    # -- reads -------------------------------------------------------------

    @_collection_response(READ_FAILED)
    async def list_collections(self, request: web.Request) -> web.Response:
        """Every collection with its listed ``count`` and its ``missing`` rows.

        ``count`` is the members listed under every configured source (one
        primary-key range read per source, matched against its cached
        listing); ``missing`` the stored rows beyond it. When a scan or a read
        fails both are null for every collection and the names still return.
        """
        collections = await self._run_in_executor(self.db.list_collections)
        counts = None
        if collections:
            try:
                counts = await self._collection_counts()
            except Exception:
                self.logger.warning(
                    "Collection counts could not be read", exc_info=True
                )
        rows = []
        for collection in collections:
            listed, stored = (None, None) if counts is None else counts
            count = None if listed is None else listed.get(collection["id"], 0)
            stored_rows = None if stored is None else stored.get(collection["id"], 0)
            rows.append(
                {
                    **collection,
                    "count": count,
                    # Clamped: never below zero, whatever the snapshot saw.
                    "missing": None if count is None else max(0, stored_rows - count),
                }
            )
        return web.json_response({"success": True, "collections": rows})

    async def _collection_counts(self) -> tuple[dict, dict]:
        """``({id: listed members}, {id: stored rows})`` over every source.

        The scans come from the gallery cache; the root keys (realpath), the
        listings and one read snapshot of every member and stored count run
        in the executor, so a write between two reads cannot skew them.
        """
        roots = list(self._get_all_output_dirs())
        root_keys = await self._run_in_executor(
            lambda: [_favorite_root_key(root) for root in roots]
        )
        distinct = {}  # one folder configured twice counts once
        for root, root_key in zip(roots, root_keys):
            distinct.setdefault(root_key, root)
        scans = {
            root_key: await self._get_gallery_files(root)
            for root_key, root in distinct.items()
        }
        return await self._run_in_executor(self._count_members, distinct, scans)

    def _count_members(self, roots: dict, scans: dict) -> tuple[dict, dict]:
        """Blocking: match one snapshot's members against each listing."""
        members, stored = self.db.collection_count_snapshot(list(roots))
        listed: dict[int, int] = {}
        for root_key, root in roots.items():
            listing = self._favorite_listing(root, scans[root_key])
            for collection_id, keys in members.get(root_key, {}).items():
                found = sum(1 for key in keys if key in listing)
                listed[collection_id] = listed.get(collection_id, 0) + found
        return listed, stored

    @_collection_response(READ_FAILED)
    async def collection_membership(self, request: web.Request) -> web.Response:
        """GET ``?root_index&root_scope&path``: the ids holding that file."""
        query = request.query
        if len(query) != 3 or set(query) != {"root_index", "root_scope", "path"}:
            # len() counts a repeated key (?path=a&path=b) twice.
            return bad_request("Query must name root_index, root_scope and path once")
        raw_index = query["root_index"]
        if not raw_index.isascii() or _ROOT_INDEX.fullmatch(raw_index) is None:
            return bad_request("Invalid gallery root")
        roots = list(self._get_all_output_dirs())
        target = {
            "root_index": int(raw_index),
            "root_scope": query["root_scope"],
            "path": query["path"],
        }
        root_key, key = await self._run_in_executor(
            self._collection_key, target, roots, {}
        )
        ids = await self._run_in_executor(self.db.file_collections, root_key, key)
        return web.json_response({"success": True, "collection_ids": ids})

    # -- writes ------------------------------------------------------------

    @_collection_response(CHANGE_FAILED)
    async def add_collection_items(self, request: web.Request) -> web.Response:
        """POST ``{collection_id | name, items}``: add the listed files.

        Only files the source's listing holds are added; the rest count in
        ``not_found``. A new name with nothing to add creates nothing (404
        ``files-not-found``).
        """
        data = await _body(request, _ADD_TARGETS)
        target = {}
        if "name" in data:
            target["name"] = normalize_collection_name(data["name"])
        else:
            target["collection_id"] = _body_id(data["collection_id"])
        roots = list(self._get_all_output_dirs())
        keys = await self._run_in_executor(self._collection_items, data["items"], roots)
        found, not_found = [], 0
        listings = {}
        for index, root_key, key in keys:
            if index not in listings:
                root = roots[index]
                files = await self._get_gallery_files(root)
                listings[index] = await self._run_in_executor(
                    self._favorite_listing, root, files
                )
            if key in listings[index]:
                found.append((root_key, key))
            else:
                not_found += 1
        result = await self._run_in_executor(
            lambda: self.db.add_to_collection(items=found, **target)
        )
        return web.json_response({"success": True, **result, "not_found": not_found})

    @_collection_response(CHANGE_FAILED)
    async def remove_collection_items(self, request: web.Request) -> web.Response:
        """DELETE ``{collection_id, items}``: listed or not, every named file."""
        data = await _body(request, (_REMOVE_FIELDS,))
        collection_id = _body_id(data["collection_id"])
        roots = list(self._get_all_output_dirs())
        keys = await self._run_in_executor(self._collection_items, data["items"], roots)
        result = await self._run_in_executor(
            self.db.remove_from_collection,
            collection_id,
            [(root_key, key) for _index, root_key, key in keys],
        )
        return web.json_response({"success": True, **result})

    @_collection_response(CHANGE_FAILED)
    async def rename_collection(self, request: web.Request) -> web.Response:
        """PATCH ``{name}``: a case variant of its own name is allowed."""
        collection_id = _path_id(request)
        data = await _body(request, (_RENAME_FIELDS,))
        name = normalize_collection_name(data["name"])
        collection = await self._run_in_executor(
            self.db.rename_collection, collection_id, name
        )
        return web.json_response({"success": True, "collection": collection})

    @_collection_response(CHANGE_FAILED)
    async def delete_collection(self, request: web.Request) -> web.Response:
        """DELETE: the collection and its members; files are never touched."""
        collection_id = _path_id(request)
        await self._run_in_executor(self.db.delete_collection, collection_id)
        return web.json_response({"success": True, "deleted": True})

    # -- request steps -----------------------------------------------------

    def _collection_items(self, items: object, roots: list) -> list[tuple]:
        """``(index, root key, relative key)`` per distinct file, in order.

        Blocking (realpath per root, once per request): run in the executor.
        Every item is checked before anything is written: 400 for a malformed
        shape, 403 for an unsafe path, 409 for a stale scope. Two spellings
        that fold to one key (Windows) count once.
        """
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise _Refused(bad_request(f"items must list 1 to {MAX_ITEMS} images"))
        keys, sources = {}, {}
        for item in items:
            index = item.get("root_index") if isinstance(item, dict) else None
            root_key, key = self._collection_key(item, roots, sources)
            keys.setdefault((root_key, key), index)
        return [(index, root_key, key) for (root_key, key), index in keys.items()]

    def _collection_key(
        self, item: object, roots: list, sources: dict
    ) -> tuple[str, str]:
        """The stored keys of one target, or raise its refusal (blocking).

        *sources* caches ``{index: (scope, root key)}`` for one request, so
        each root's realpath runs once however many items name it.
        """
        target, error = media_target(item, media_message=_MEDIA_MESSAGE)
        if error is not None:
            raise _Refused(error)
        index, scope, relative = target
        if index >= len(roots):
            raise _Refused(_coded(409, "source-changed", SOURCES_CHANGED))
        if index not in sources:
            sources[index] = (
                self._gallery_root_scope(roots[index], index),
                _favorite_root_key(roots[index]),
            )
        current, root_key = sources[index]
        if not hmac.compare_digest(scope, current):
            raise _Refused(_coded(409, "source-changed", SOURCES_CHANGED))
        key = _favorite_relative_key(relative)
        if max(len(root_key), len(key)) > MAX_KEY_LENGTH:
            # A stored key past the schema bound would fail the next restore.
            raise _Refused(_forbidden("Path is too long to add to a collection"))
        return root_key, key
