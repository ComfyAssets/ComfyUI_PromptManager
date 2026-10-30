"""Return removed images to the library (#498).

``POST /prompt_manager/images/removed/return {items | filter}`` names files a
removal mark hides (#480, "Remove from the library only"): ``items`` are 1 to
500 listing identities, ``filter`` the removed view's listing query
(``removed: "1"`` plus the view's other parameters, at most 500 images). Only
this route takes ``removed``: every other bulk route refuses it, because an
explicit write there would show a file again without re-reading it.

The route answers 202 ``{job, returned, not_removed}`` after starting a
``scan`` job titled "Return images to the library" (admission ``records``:
409 ``busy`` beside a scan, restore or delete, nothing changed). The job
first deletes the marks that still hide their file (same size and
``mtime_ns``), in one transaction, then scans exactly those files with the
scan's own pipeline: prompts, links, pairs and Needs review items come back
from the files' embedded metadata. Image tags, auto-tag marks, the own
category, collection memberships and the favorite were deleted by the remove
and do not come back. No file is moved, written or deleted.
"""

import os
from pathlib import Path

from aiohttp import web

from .image_categories import TOO_MANY, _coded
from .images import (
    ListingSourceChanged,
    _favorite_relative_key,
    _json_body_or_error,
    parse_listing_filter,
)
from .image_tag_jobs import MAX_JOB_TARGETS
from .jobs import JobBusy
from .job_routes import busy_response

try:
    from ...database.image_removal import mark_hides
except ImportError:
    from database.image_removal import mark_hides

NONE_REMOVED = "None of the selected images are removed from the library."
SOURCE_CHANGED = "The image sources changed. Reload Images and try again."


def _bad(message):
    return web.json_response({"success": False, "error": message}, status=400)


class ImageReturnMixin:
    """Return to library (#498): clear the marks, then scan those files."""

    def _register_image_return_routes(self, routes):
        # A fixed path beside the dynamic /images/{image_id} routes.
        @routes.post("/prompt_manager/images/removed/return")
        async def return_removed_images_route(request):
            return await self.return_removed_images(request)

    async def return_removed_images(self, request):
        data, error = await _json_body_or_error(request)
        if error is not None:
            return error
        if set(data) == {"filter"}:
            targets, roots, error = await self._removed_filter_targets(data["filter"])
        else:
            _data, targets, roots, error = await self._category_request(
                request, frozenset(), data=data
            )
        if error is not None:
            return error
        files = {}
        for index in dict.fromkeys(t[0] for t in targets):
            files[index] = await self._get_gallery_files(roots[index])
        returned, not_removed = await self._run_in_executor(
            self._hidden_targets, roots, targets, files
        )
        if not returned:
            return _coded(400, "no-images", NONE_REMOVED)
        try:
            job = self._start_scan_job(
                files="all", output_dirs=list(roots), returned=returned
            )
        except JobBusy as busy:
            return busy_response(busy)
        return web.json_response(
            {
                "success": True,
                "job": job.summary(),
                "returned": len(returned),
                "not_removed": not_removed,
            },
            status=202,
        )

    async def _removed_filter_targets(self, raw):
        """``(targets, roots, None)`` for the removed view's filter, else an error."""
        if not isinstance(raw, dict) or raw.get("removed") != "1":
            return None, None, _bad('filter must hold removed: "1"')
        rest = {key: value for key, value in raw.items() if key != "removed"}
        try:
            parse_listing_filter(rest)
            selection = await self.resolve_listing_filter(
                {**rest, "removed": "1"}, MAX_JOB_TARGETS
            )
        except ListingSourceChanged:
            return None, None, _coded(409, "source-changed", SOURCE_CHANGED)
        except ValueError as exc:
            return None, None, _bad(str(exc))
        if selection.truncated:
            return None, None, _coded(400, "too-many-images", TOO_MANY)
        if not selection.targets:
            return None, None, _coded(400, "no-images", NONE_REMOVED)
        return tuple(selection.targets), list(self._get_all_output_dirs()), None

    def _hidden_targets(self, roots, targets, files):
        """``([(path, locator)], not_removed)``: the targets a mark hides now.

        Each target is matched to its listed spelling (the walk's). One
        physical file named twice (nested roots) is returned once; a target
        that is not listed or not hidden counts in ``not_removed``.
        """
        marks = self._removal_marks()
        listings, seen, returned, not_removed = {}, set(), [], 0
        for index, _scope, relative in targets:
            root = roots[index]
            if index not in listings:
                listings[index] = self._favorite_listing(root, files[index])
            spelled = listings[index].get(_favorite_relative_key(relative))
            if spelled is None:
                not_removed += 1
                continue
            path = os.path.join(str(root), *spelled.split("/"))
            locator = os.path.normcase(os.path.realpath(path))
            if locator in seen:
                continue
            if not mark_hides(marks, locator, path):
                not_removed += 1
                continue
            seen.add(locator)
            returned.append((Path(path), locator))
        return returned, not_removed

    def _clear_returned_marks(self, returned) -> list:
        """The paths whose marks this deleted (blocking; one transaction)."""
        cleared = self.db.clear_hiding_marks(
            [(locator, str(path)) for path, locator in returned]
        )
        return [path for path, locator in returned if locator in cleared]
