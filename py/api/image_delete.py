"""Delete selected images: remove them from the library, or trash them (#480).

``POST /prompt_manager/images/delete/preview {items | filter}`` reviews the
selection for both modes at once and answers a reviewed plan (the System ›
Maintenance contract, ``py/api/maintenance.py``): the token, the counts of
the records each file loses, the refused files with a coded reason (both
modes, and the trash-only ones), the prompts that lose their last image and
the safety backup. It writes nothing. ``items`` are Images listing identities
(at most 500); ``filter`` is "Select all N matching". Each target is matched
to its entry in the current listing (``not-listed`` otherwise), so every
later step uses the walked spelling, never the request's text.

``POST /prompt_manager/maintenance/apply {token, mode}`` runs the plan as the
``image_delete`` job (admission ``records``, not cancellable): the sources
are checked, the promised safety backup is taken, then each file is checked
again (containment and fingerprint) and

- ``mode="library"``: its records are deleted and its removal mark written
  in one transaction; the file stays on disk, hidden;
- ``mode="trash"``: it is moved to the trash first (``utils/trash.py``); a
  refused move leaves the file and its records alone; after a move its
  records are deleted.

No path here unlinks a media file. A failed records write stops the job; the
files after it are not attempted.
"""

import hashlib
import json
import os
import stat as stat_module
from pathlib import Path

from .admin import DELETABLE_MEDIA_EXTENSIONS, _remove_thumbnail
from .duplicate_review import (
    CHANGED_CONTENT,
    GONE,
    LINK,
    OUTSIDE_SOURCE,
    UNREADABLE,
    file_problem,
)
from .images import _favorite_relative_key, _favorite_root_key
from .jobs import IMAGE_DELETE_GROUP_NAMES, IMAGE_DELETE_JOB_KIND, JobBusy
from .job_routes import busy_response
from .maintenance import MAINTENANCE_PREVIEW_LEASE, _fail_job, _failure, backup_note

try:
    from ...database import image_removal_schema
    from ...database.image_removal import mark_hides
    from ...database.annotation_schema import MAX_LOCATOR_LENGTH
    from ...utils import trash
    from ...utils.media_folders import is_trash_folder
except ImportError:
    from database import image_removal_schema
    from database.image_removal import mark_hides
    from database.annotation_schema import MAX_LOCATOR_LENGTH
    from utils import trash
    from utils.media_folders import is_trash_folder

IMAGE_DELETE = "delete_images"
MODES = ("library", "trash")
TITLES = {
    "library": "Delete images (remove from the library)",
    "trash": "Delete images (move to the trash)",
}
MAX_DETAIL_ROWS = 50
# The system moved the file but its place in a trash could not be shown (#499).
TRASH_UNCONFIRMED = "trash-unconfirmed"
RECORDS_FAILED_CODE = "records-failed"

# Reason codes (design §4.4) and the sentence each shows.
REASONS = {
    "not-listed": "No longer listed in Images.",
    "outside": "Outside the output folders.",
    "link": "A link, not a file.",
    "gone": "The file no longer exists.",
    "unreadable": "The file can't be read.",
    "thumbnail": "A generated preview, not an image.",
    "not-media": "Not a media file.",
    "trash-folder": "Inside a trash folder.",
    "path-too-long": "The path is too long.",
    "changed": "The file changed after the review.",
    trash.NO_TRASH: "This drive has no trash.",
    trash.NETWORK: "Network drives can't use the trash.",
    trash.TRASH_IN_ROOT: "This drive's trash is inside an output folder.",
    trash.LOCKED: "The file is open in another program. Close it and try again.",
    trash.FAILED: "The file could not be moved to the trash.",
    trash.NO_PERMISSION: (
        "PromptManager isn't allowed to move this file (no permission, or it is "
        "locked in Finder)."
    ),
    trash.CLOUD_SYNC: (
        "Files in iCloud Drive or a cloud-synced folder can't be moved to the "
        "trash here."
    ),
    TRASH_UNCONFIRMED: (
        "Moved by macOS, but its place in the trash could not be confirmed."
    ),
    trash.WINDOWS_RECYCLE: (
        "Moving files to the Recycle Bin isn't available on Windows: files larger "
        "than the bin's capacity would be deleted permanently."
    ),
    "not-attempted": "Not attempted: the job stopped after an error.",
}
_PROBLEM_CODES = {
    OUTSIDE_SOURCE: "outside",
    LINK: "link",
    GONE: "gone",
    UNREADABLE: "unreadable",
    CHANGED_CONTENT: "changed",
}
SOURCES_CHANGED = "The sources changed since the review; nothing was changed."
RECORDS_FAILED = "The image records could not be removed; the job stopped."


def lexical_problem(relative: str) -> str | None:
    """The refusal code a root-relative POSIX path earns before any file access."""
    parts = relative.split("/")
    if "thumbnails" in parts[:-1]:
        return "thumbnail"
    if any(is_trash_folder(part) for part in parts[:-1]):
        return "trash-folder"
    if os.path.splitext(parts[-1])[1].lower() not in DELETABLE_MEDIA_EXTENSIONS:
        return "not-media"
    return None


def file_check(path, root, relative, *, expected=None):
    """``(code, lstat)``: why *path* may not be deleted, or ``(None, lstat)``.

    Text first (inside the root, not a preview or trash folder, a media
    file), then ``duplicate_review.file_problem``: a regular file, not a
    link, whose resolved path is still inside the resolved root. With
    *expected* (``{size, mtime_ns, inode}`` from the review) the file must
    still be those bytes. Every handle is closed when this returns.
    """
    code = lexical_problem(relative)
    if code:
        return code, None
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "gone", None
    except OSError:
        return "unreadable", None
    if getattr(info, "st_file_attributes", 0) & getattr(
        stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        return "link", info
    if expected is None:
        problem = file_problem(path, root, None, info.st_size, hash_check=False)
    else:
        problem = file_problem(
            path,
            root,
            None,
            expected["size"],
            hash_check=False,
            mtime_ns=expected["mtime_ns"],
            inode=expected["inode"],
        )
    if problem:
        return _PROBLEM_CODES.get(problem, "outside"), info
    if len(os.path.normcase(os.path.realpath(path))) > MAX_LOCATOR_LENGTH:
        return "path-too-long", info
    return None, info


def _row(target, code):
    return {
        "path": target["rel"],
        "root_label": target["root_label"],
        "code": code,
        "detail": REASONS[code],
    }


def _summary(rows):
    counts = {}
    for row in rows:
        counts[row["code"]] = counts.get(row["code"], 0) + 1
    return {"count": len(rows), "counts": counts, "rows": rows[:MAX_DETAIL_ROWS]}


def _fingerprint(targets) -> str:
    material = [
        (t["root_index"], t["rel"], t["size"], t["mtime_ns"], t["inode"])
        for t in targets
    ]
    return hashlib.sha256(json.dumps(material).encode("utf-8")).hexdigest()


class ImageDeleteMixin:
    """The Images Delete… review and its apply job (#480)."""

    def _register_image_delete_routes(self, routes):
        # A fixed path beside the dynamic /images/{image_id} routes: aiohttp
        # (3.10+) looks fixed paths up first (see image_categories.py).
        @routes.post("/prompt_manager/images/delete/preview")
        async def preview_image_delete_route(request):
            return await self.preview_image_delete(request)

    # ── review ────────────────────────────────────────────────────────

    async def preview_image_delete(self, request):
        """Review the selection under the maintenance preview lease."""
        try:
            async with self._jobs.lease(MAINTENANCE_PREVIEW_LEASE):
                return await self._preview_image_delete(request)
        except JobBusy as busy:
            return busy_response(busy)

    async def _preview_image_delete(self, request):
        _data, targets, roots, error = await self._category_request(
            request, frozenset()
        )
        if error is not None:
            return error
        db = self.db
        identity = self._database_identity(db)
        try:
            files = {}
            for index in dict.fromkeys(t[0] for t in targets):
                files[index] = await self._get_gallery_files(roots[index])
            review = await self._run_in_executor(
                self._review_image_delete, db, roots, targets, files
            )
        except Exception:
            self.logger.exception("Image delete review failed")
            return _failure("The review failed. Check server logs for details.", 500)
        return await self._plan_response(
            db,
            identity,
            IMAGE_DELETE,
            _fingerprint(review["targets"]),
            review["public"],
            {"targets": review["targets"]},
        )

    def _review_image_delete(self, db, roots, targets, files):
        """Every target checked now (blocking); ``{targets, public}``."""
        listings, seen = {}, set()
        acted, refused, trash_refused = [], [], []
        spellings = self._source_spellings()
        root_texts = [str(root) for root in roots]
        for index, _scope, relative in targets:
            root = roots[index]
            if index not in listings:
                listings[index] = self._favorite_listing(root, files[index])
            spelled = listings[index].get(_favorite_relative_key(relative))
            target = {
                "root_index": index,
                "root_scope": _scope,
                "root": str(root),
                "root_label": self._public_path(root),
                "rel": spelled or relative,
            }
            if spelled is None:
                refused.append(_row(target, "not-listed"))
                continue
            absolute = os.path.join(str(root), *spelled.split("/"))
            code, info = file_check(absolute, str(root), spelled)
            if code:
                refused.append(_row(target, code))
                continue
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                continue  # one physical file named twice (nested roots, hardlinks)
            seen.add(identity)
            target.update(self._removal_of(absolute, info, spellings))
            target["trash"] = trash.trash_available(absolute, root_texts)
            if target["trash"]:
                trash_refused.append(_row(target, target["trash"]))
            acted.append(target)
        counted = db.count_removal_records([t["removal"] for t in acted])
        library_count = len(acted)
        public = {
            "operation": IMAGE_DELETE,
            "scope": {"count": library_count, "label": "images"},
            "library": {"count": library_count},
            "trash": {"count": library_count - len(trash_refused)},
            "protected": {"count": len(refused), "reasons": []},
            "refused": _summary(refused),
            "trash_refused": _summary(trash_refused),
            "records": counted["records"],
            "orphaned_prompts": counted["orphaned_prompts"],
            "media": {"deletes_files": False, "label": "Chosen in the review"},
            "details": [],
        }
        return {"targets": acted, "public": public}

    @staticmethod
    def _removal_of(absolute, info, spellings):
        favorites = [
            (_favorite_root_key(root), _favorite_relative_key(relative))
            for root, relative in spellings.favorites(absolute).items()
        ]
        return {
            "abs": absolute,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "inode": [info.st_dev, info.st_ino],
            "removal": {
                "spellings": list(spellings.of(absolute).values()),
                "locator": os.path.normcase(os.path.realpath(absolute)),
                "favorites": favorites,
                "category": spellings.category_key(absolute),
            },
        }

    # ── apply ─────────────────────────────────────────────────────────

    def _start_image_delete(self, db, plan, mode, backup_on):
        """Register the delete job for a reviewed plan (on the loop)."""
        return self._jobs.start(
            IMAGE_DELETE_JOB_KIND,
            TITLES[mode],
            admission="records",
            cancellable=False,
            scope={"operation": IMAGE_DELETE, "mode": mode},
            groups=IMAGE_DELETE_GROUP_NAMES,
            runner=lambda job: self._run_image_delete(job, db, plan, mode, backup_on),
        )

    async def _run_image_delete(self, job, db, plan, mode, backup_on):
        targets = plan.payload["targets"]
        unchanged = await self._run_in_executor(self._sources_unchanged, targets)
        if not unchanged:
            return _fail_job(job, "sources-changed", SOURCES_CHANGED)
        ok, backup = await self._safety_backup_step(job, db, backup_on)
        if not ok:
            return
        try:
            await self._delete_images(job, db, targets, mode, backup)
        finally:
            if backup:
                await self._prune_backup_pool(job, db)

    async def _delete_images(self, job, db, targets, mode, backup):
        job.update(total=len(targets), status_line=TITLES[mode])
        roots = [str(root) for root in self._get_all_output_dirs()]
        done_group = "trashed" if mode == "trash" else "removed"
        changed, failed = 0, None
        for done, target in enumerate(targets, start=1):
            if failed:
                self._record_delete(job, "not_changed", target, "not-attempted")
                continue
            group, code, label = await self._run_in_executor(
                self._delete_one_image, db, target, mode, roots
            )
            if group == "errors":
                failed = code or RECORDS_FAILED_CODE
                text = REASONS[code] if code else RECORDS_FAILED
                job.record(
                    "errors",
                    {
                        "path": target["rel"],
                        "root": target["root_label"],
                        "root_index": target["root_index"],
                        "root_scope": target["root_scope"],
                        "detail": text,
                    },
                    f"! {target['root_label']}/{target['rel']} · {text}",
                )
            elif group == done_group:
                changed += 1
                detail = (
                    f"Moved to the {label}" if label else "Removed from the library"
                )
                self._record_delete(job, group, target, None, detail)
            else:
                self._record_delete(job, group, target, code)
            job.update(processed=done, progress=int(done / len(targets) * 100))
        if changed:
            self.invalidate_gallery_cache()
        verb = "Moved" if mode == "trash" else "Removed"
        where = "to the trash" if mode == "trash" else "from the library"
        line = f"{verb} {changed:,} of {len(targets):,} images {where}"
        job.log_line(line)
        job.update(progress=100, status_line=f"{line} · {backup_note(backup)}")
        if failed:
            # "records-failed" names every stop at an errors row (the page's
            # "Stopped after N of M" toast); the row says which error it was.
            job.scope["failure"] = RECORDS_FAILED_CODE
            job.publish({"type": "error", "code": failed, "message": line})
            job.finish("failed")

    @staticmethod
    def _record_delete(job, group, target, code, detail=None):
        text = detail or REASONS[code]
        row = {
            "path": target["rel"],
            "root": target["root_label"],
            "root_index": target["root_index"],
            "root_scope": target["root_scope"],
            "code": code,
            "detail": text,
        }
        mark = {"removed": "-", "trashed": "-"}.get(group, "=")
        job.record(
            group, row, f"{mark} {target['root_label']}/{target['rel']} · {text}"
        )

    def _delete_one_image(self, db, target, mode, roots):
        """``(group, code, trash label)`` for one reviewed file (blocking)."""
        code, _info = file_check(
            target["abs"],
            target["root"],
            target["rel"],
            expected={
                "size": target["size"],
                "mtime_ns": target["mtime_ns"],
                "inode": target["inode"],
            },
        )
        if code:
            return "not_changed", code, None
        label = None
        if mode == "trash":
            try:
                label = trash.move_to_trash(target["abs"], roots)
            except trash.TrashRefused as refused:
                code = refused.code if refused.code in REASONS else trash.FAILED
                self.logger.info(
                    "Not moved to the trash (%s): %s %s",
                    code,
                    target["rel"],
                    refused.detail,
                )
                return "not_changed", code, None
            except trash.TrashUnconfirmed as unconfirmed:
                # The file left its folder: keep its records, stop the job.
                self.logger.warning(
                    "Trash move not confirmed: %s %s", target["rel"], unconfirmed.detail
                )
                return "errors", TRASH_UNCONFIRMED, None
            mark = None
        else:
            mark = (target["size"], target["mtime_ns"])
        try:
            db.remove_image_records(target["removal"], mark=mark)
        except Exception:
            self.logger.exception("Removing an image's records failed")
            return "errors", None, label
        _remove_thumbnail(Path(target["root"]), Path(target["rel"]), self.logger)
        return ("trashed" if mode == "trash" else "removed"), None, label

    # ── the workspace listing ─────────────────────────────────────────

    def _removal_marks(self) -> dict:
        """The removal marks, cached until a mark changes or the database does.

        A listing page reads no table while nothing changed: the cache follows
        this process's marks generation and the database identity (a restore).
        """
        key = (self._database_identity(self.db), image_removal_schema.generation())
        cached = getattr(self, "_marks_cache", None)
        if cached is None or cached[0] != key:
            cached = (key, self.db.removal_marks())
            self._marks_cache = cached
        return cached[1]

    def _hidden_entries(self, listings) -> set:
        """Normalized paths of listed files a removal mark hides (blocking).

        Only marked locators are looked at, and only those files are
        ``lstat``'ed: a mark hides a file while its size and ``mtime_ns`` are
        still the ones it was removed with.
        """
        marks = self._removal_marks()
        if not marks:
            return set()
        located = self._workspace_locators(listings)
        return {
            entry.normalized
            for locator, entry in ((locator, located.get(locator)) for locator in marks)
            if entry is not None and mark_hides(marks, locator, entry.path)
        }

    def _removed_paths(self, paths) -> set:
        """The absolute paths among *paths* a removal mark hides (blocking)."""
        marks = self._removal_marks()
        if not marks:
            return set()
        return {
            str(path)
            for path in paths
            if mark_hides(marks, os.path.normcase(os.path.realpath(str(path))), path)
        }

    def _removal_check_under(self, root):
        """``hidden(path) -> bool`` for files a walk of *root* found (blocking).

        A walk never follows a link below *root*, so a file's locator is
        ``normcase(join(realpath(root), relative))``: no ``realpath`` per
        file, and an ``lstat`` only for a marked one. Without marks it
        answers False at once.
        """
        marks = self._removal_marks()
        if not marks:
            return lambda _path: False
        root = os.fspath(root)
        real = os.path.realpath(root)

        def hidden(path):
            relative = os.path.relpath(os.fspath(path), root)
            locator = os.path.normcase(os.path.join(real, relative))
            return mark_hides(marks, locator, path)

        return hidden

    def _without_removed(self, root, paths) -> list:
        """*paths* found by a walk of *root*, less the hidden ones (blocking)."""
        removed = self._removal_check_under(root)
        return [path for path in paths if not removed(path)]


def parse_mode(value):
    """The apply's ``mode``, or None when it is not one of MODES."""
    return value if isinstance(value, str) and value in MODES else None
