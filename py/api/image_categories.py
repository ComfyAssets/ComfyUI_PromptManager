"""Image category routes (#332): an image file's own category.

``PUT /prompt_manager/images/category`` sets the own category of up to 500
listed files; ``DELETE`` removes it (listed or not, so a missing file's
category can still be removed); ``GET /prompt_manager/images/categories``
lists the own-category values of files the sources list. Targets are the
Images listing identity (``{root_index, root_scope, path}``, checked by
``media_target``) of an output source; LoRA folders are not sources here.
Instead of ``items`` a request may name ``filter``, the Images listing's own
query ("Select all N matching", #392), resolved now up to 500 images.
Each file is stored under its file key (``images.image_category_key``).
These routes never read or write prompts, recipes or image links.
"""

import os
from pathlib import Path

from aiohttp import web

from .image_tag_jobs import MAX_JOB_TARGETS, _parse_targets
from .images import (
    _favorite_relative_key,
    _favorite_root_key,
    _forbidden,
    _json_body_or_error,
    image_category_key,
)
from .prompts import bad_request

try:
    from ...database.image_categories import MAX_CATEGORY_LENGTH, MAX_KEY_LENGTH
    from ...database.models import normalize_image_path
    from ...utils.validators import validate_category
except ImportError:
    from database.image_categories import MAX_CATEGORY_LENGTH, MAX_KEY_LENGTH
    from database.models import normalize_image_path
    from utils.validators import validate_category

CATEGORY_MEDIA_MESSAGE = "Only media files can have a category"
_PUT_FIELDS = frozenset({"category"})
_DELETE_FIELDS = frozenset()
TOO_MANY = f"Select at most {MAX_JOB_TARGETS} images."
SOURCE_CHANGED_MESSAGE = "The sources changed. Refresh the images and try again."
WRITE_FAILED = "The category could not be changed."
READ_FAILED = "Image categories could not be read."


def _coded(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"success": False, "code": code, "error": message},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _category_value(raw) -> tuple[str | None, web.Response | None]:
    """The stripped category, or a 400 (an empty value is a 400: use DELETE)."""
    if not isinstance(raw, str):
        return None, bad_request("category must be a string")
    value = raw.strip()
    if not value:
        return None, bad_request("category must not be empty")
    if len(value) > MAX_CATEGORY_LENGTH:
        return None, bad_request(
            f"Category cannot exceed {MAX_CATEGORY_LENGTH} characters"
        )
    try:
        value.encode("utf-8")
        validate_category(value)
    except UnicodeEncodeError:
        return None, bad_request("category must be valid text")
    except ValueError as exc:
        return None, bad_request(str(exc))
    return value, None


class _KeyRefused(Exception):
    """A target has no storable file key (outside every source, or too long)."""

    def __init__(self, message: str = "Access denied"):
        super().__init__(message)
        self.message = message


def _file_key(roots, root, relative) -> tuple[str, str]:
    """The target's file key (blocking: resolves the sources)."""
    key = image_category_key(roots, Path(root) / relative)
    if key is None:
        raise _KeyRefused()
    if max(len(key[0]), len(key[1])) > MAX_KEY_LENGTH:
        raise _KeyRefused("Path is too long")
    return key


def _nested_sources(root: Path, sources) -> list[tuple[str, str]]:
    """``[(relative key prefix, source root key)]`` of sources inside *root*.

    Deepest first, so the first prefix that matches is the file's source.
    """
    base = os.path.join(os.path.normcase(os.path.realpath(str(root))), "")
    nested = []
    for source in sources:
        real = os.path.realpath(str(source))
        if not os.path.normcase(real).startswith(base):
            continue
        relative = os.path.relpath(real, os.path.realpath(str(root)))
        nested.append(
            (
                _favorite_relative_key(relative.replace(os.sep, "/")),
                _favorite_root_key(source),
            )
        )
    nested.sort(key=lambda item: item[0].count("/"), reverse=True)
    return nested


def _source_prefix(relative_key: str, nested) -> str | None:
    """The prefix of the deepest nested source holding *relative_key*."""
    for prefix, _source_key in nested:
        if relative_key.startswith(prefix + "/"):
            return prefix
    return None


class ImageCategoryRoutesMixin:
    """Routes for images' own categories (#332)."""

    def _register_image_category_routes(self, routes):
        # Fixed paths beside DELETE /prompt_manager/images/{image_id}, as the
        # favorite routes are. Registered after it, they still win: aiohttp
        # (3.10+) looks fixed paths up before dynamic resources
        # (test_delete_category_never_reaches_delete_image).
        @routes.put("/prompt_manager/images/category")
        async def set_image_category_route(request):
            return await self.set_image_category(request)

        @routes.delete("/prompt_manager/images/category")
        async def clear_image_category_route(request):
            return await self.clear_image_category(request)

        @routes.get("/prompt_manager/images/categories")
        async def list_image_categories_route(request):
            return await self.list_image_categories(request)

    # -- reading ---------------------------------------------------------------

    def _own_category_map(self, sources, listings) -> dict:
        """``{normalized listed path: own category}`` for the listed files (blocking).

        One primary-key range read per source; each stored row is matched to
        the listing of every selected root that holds its source, so the work
        follows the stored rows, not the files. A file under a nested source
        is keyed by the deepest one (``image_category_key``). A row whose file
        is gone matches nothing. A failed read raises.
        """
        sources = list(sources)
        rows = self.db.own_categories_of(_favorite_root_key(s) for s in sources)
        if not rows:
            return {}
        by_source: dict[str, dict[str, str]] = {}
        for (root_key, relative), category in rows.items():
            by_source.setdefault(root_key, {})[relative] = category
        found = {}
        for _index, root, files in listings:
            listing = self._favorite_listing(root, files)
            root_key = _favorite_root_key(root)
            # normalize_image_path(root / listed) without a call per file: the
            # listed spellings are clean relative POSIX paths. Windows abspath
            # also drops a component's trailing dots and spaces, so it keeps
            # the call there.
            base = os.path.join(normalize_image_path(str(root)), "")

            def normalized(listed, base=base, root=root):
                if os.name == "nt":
                    return normalize_image_path(str(root / listed))
                return base + os.path.normcase(listed.replace("/", os.sep))

            nested = [
                item for item in _nested_sources(root, sources) if item[1] != root_key
            ]
            for relative, category in by_source.get(root_key, {}).items():
                listed = listing.get(relative)
                if listed is not None and _source_prefix(relative, nested) is None:
                    found[normalized(listed)] = category
            for prefix, source_key in nested:
                for relative, category in by_source.get(source_key, {}).items():
                    full = f"{prefix}/{relative}"
                    listed = listing.get(full)
                    if listed is not None and _source_prefix(full, nested) == prefix:
                        found[normalized(listed)] = category
        return found

    async def _source_listings(self, roots) -> list[tuple]:
        return [
            (index, root, await self._get_gallery_files(root))
            for index, root in enumerate(roots)
        ]

    async def list_image_categories(self, request):
        """GET: the own-category values of listed files, sorted."""
        try:
            roots = list(self._get_all_output_dirs())
            listings = await self._source_listings(roots)
            found = await self._run_in_executor(self._own_category_map, roots, listings)
        except Exception:
            self.logger.exception("Image categories read failed")
            return _coded(500, "categories-failed", READ_FAILED)
        return web.json_response(
            {"success": True, "categories": sorted(set(found.values()))},
            headers={"Cache-Control": "no-store"},
        )

    # -- writing ---------------------------------------------------------------

    async def _filter_targets(self, raw):
        """``(targets, None)`` for an Images listing filter, else ``(None, error)``.

        "Select all N matching" (#392) sends the listing's own query; it is
        resolved now, up to 500 images (a 400 ``too-many-images`` above).
        """
        from .images import ListingSourceChanged, parse_listing_filter

        try:
            selection = await self.resolve_listing_filter(
                parse_listing_filter(raw), MAX_JOB_TARGETS
            )
        except ListingSourceChanged:
            return None, _coded(409, "source-changed", SOURCE_CHANGED_MESSAGE)
        except ValueError as exc:
            return None, bad_request(str(exc))
        if selection.truncated:
            return None, _coded(400, "too-many-images", TOO_MANY)
        return tuple(selection.targets), None

    async def _category_request(self, request, fields: frozenset):
        """``(data, targets, roots, None)`` or ``(None, None, None, error)``.

        The images are ``items`` (1 to 500 targets) or ``filter`` (all images
        an Images listing filter admits), besides *fields*.
        """
        data, error = await _json_body_or_error(request)
        if error is not None:
            return None, None, None, error
        names = set(data) - fields
        if set(data) & fields != fields or names not in ({"items"}, {"filter"}):
            return (
                None,
                None,
                None,
                bad_request(
                    "Request must contain "
                    + ", ".join(sorted(fields))
                    + (" and " if fields else "")
                    + "either items or filter"
                ),
            )
        if "filter" in data:
            targets, error = await self._filter_targets(data["filter"])
            if error is not None:
                return None, None, None, error
            return data, targets, list(self._get_all_output_dirs()), None
        items = data["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_JOB_TARGETS:
            return (
                None,
                None,
                None,
                bad_request(f"items must list 1 to {MAX_JOB_TARGETS} images"),
            )
        targets, error = _parse_targets(items, media_message=CATEGORY_MEDIA_MESSAGE)
        if error is not None:
            return None, None, None, error
        try:
            for _index, _scope, relative in targets:
                relative.encode("utf-8")
        except UnicodeEncodeError:
            return None, None, None, bad_request("path must be valid text")
        roots = list(self._get_all_output_dirs())
        for index, scope in dict.fromkeys((t[0], t[1]) for t in targets):
            if index >= len(roots):
                return None, None, None, bad_request("Invalid gallery root")
            error = self._gallery_scope_error(
                {"root_scope": scope, "root": str(index)}, roots
            )
            if error is not None:
                if error.status == 409:
                    error = _coded(409, "source-changed", SOURCE_CHANGED_MESSAGE)
                return None, None, None, error
        return data, targets, roots, None

    def _listed_file_keys(self, roots, targets, files) -> tuple[list, list]:
        """``(listed keys, unlisted keys)`` of the targets (blocking).

        A target counts as listed when its source's listing holds it; its key
        then follows the listed spelling. Targets folding to one key count
        once, and a key that is listed through any target is listed.
        """
        listings, listed, missing = {}, {}, {}
        for index, _scope, relative in targets:
            root = roots[index]
            if index not in listings:
                listings[index] = self._favorite_listing(root, files[index])
            spelled = listings[index].get(_favorite_relative_key(relative))
            key = _file_key(roots, root, spelled or relative)
            (missing if spelled is None else listed)[key] = True
        return list(listed), [key for key in missing if key not in listed]

    async def set_image_category(self, request):
        """PUT ``{items, category}``: set the own category of listed files."""
        data, targets, roots, error = await self._category_request(request, _PUT_FIELDS)
        if error is not None:
            return error
        category, error = _category_value(data["category"])
        if error is not None:
            return error
        try:
            files = {}
            for index in dict.fromkeys(t[0] for t in targets):
                files[index] = await self._get_gallery_files(roots[index])
            listed_keys, missing_keys = await self._run_in_executor(
                self._listed_file_keys, roots, targets, files
            )
        except _KeyRefused as refused:
            return _forbidden(refused.message)
        except Exception:
            self.logger.exception("Image category update failed")
            return _coded(500, "category-failed", WRITE_FAILED)
        try:
            if not listed_keys:
                return _coded(
                    404,
                    "files-not-found",
                    "None of the selected images could be found.",
                )
            counts = await self._run_in_executor(
                self.db.set_image_categories, listed_keys, category
            )
        except Exception:
            self.logger.exception("Image category update failed")
            return _coded(500, "category-failed", WRITE_FAILED)
        return web.json_response(
            {
                "success": True,
                "category": category,
                "updated": counts["updated"],
                "unchanged": counts["unchanged"],
                "not_found": len(missing_keys),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def clear_image_category(self, request):
        """DELETE ``{items}``: remove the own category, listed or not."""
        _data, targets, roots, error = await self._category_request(
            request, _DELETE_FIELDS
        )
        if error is not None:
            return error
        try:
            keys = await self._run_in_executor(
                lambda: list(
                    dict.fromkeys(_file_key(roots, roots[i], r) for i, _s, r in targets)
                )
            )
            cleared = await self._run_in_executor(self.db.clear_image_categories, keys)
        except _KeyRefused as refused:
            return _forbidden(refused.message)
        except Exception:
            self.logger.exception("Image category removal failed")
            return _coded(500, "category-failed", WRITE_FAILED)
        return web.json_response(
            {"success": True, "cleared": cleared},
            headers={"Cache-Control": "no-store"},
        )
