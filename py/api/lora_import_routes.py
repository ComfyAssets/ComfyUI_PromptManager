"""The staged LoRA reference import as a reviewed Activity job (#221, S10).

``POST /prompt_manager/lora/import/preview`` (body ``{}``) reviews an import:
it lists the LoraManager metadata files and parses them, and counts the
current imports, those a saved pair uses and the example images an import
would download. It writes nothing and requests nothing. It answers a
``Plan`` (the maintenance plan's shape, ``py/api/maintenance.py``) plus
``{lora_files, with_metadata, current_imports, blocked_by_pairs,
planned_downloads, network:{planned_downloads, api_key}}``. A plan that saved
pairs block carries no token (nothing can be applied).

``POST /prompt_manager/lora/import/apply {token}`` starts the job (202
``{job}``): kind ``lora_import``, titled "LoRA reference import", admission
``records``, cancellable while it downloads only. Refused before it starts:
``plan-changed`` (409: the metadata files changed since the review),
``plan-expired``, ``plan-used``, ``storage-changed``, ``busy``,
``insufficient-space`` (as maintenance). The job stages the records
(downloads into the cache first, each counted in ``downloaded`` or
``download_errors``), takes the safety backup the policy promises, then
replaces the collection in one transaction bound to the reviewed imports. Any
failure leaves the previous imports exactly as they were, and the job says so.

The legacy ``POST /lora/scan`` runs the same stages (``stage_lora_import``
and ``replace_lora_imports``) under its SSE event shape.
"""

import asyncio
import os
import sqlite3

from aiohttp import web

from .. import lora_import, lora_utils
from .job_routes import busy_response
from .jobs import LORA_IMPORT_JOB_KIND, JobBusy
from .maintenance import (
    MAX_TOKEN_LENGTH,
    _failure,
    _fail_job,
    _invalid,
    _iso,
    disk_plan,
    short_space_message,
)
from .maintenance_plans import ScopeTooLarge

try:
    from ...database.category_import import EmptyReplacement
    from ...database.maintenance import PlanChanged
    from ...database.pairs import PairDependencyError
except ImportError:  # pragma: no cover - flat test imports
    from database.category_import import EmptyReplacement
    from database.maintenance import PlanChanged
    from database.pairs import PairDependencyError

LORA_OPERATION = "lora_import"
CATEGORY = lora_import.LORA_CATEGORY
SOURCE_CHANGED = (
    "The LoRA metadata files changed since the review; nothing was changed. "
    "Review the import again."
)
IMPORTS_CHANGED = (
    "The imported LoRA prompts changed since the review; the previous imports "
    "were kept. Review the import again."
)
BLOCKED = (
    "Saved pairs use some imported LoRA prompts, so the collection cannot be "
    "replaced; the previous imports were kept."
)
INTEGRATION_CHANGED = (
    "The LoraManager integration changed since the review; nothing was changed. "
    "Review the import again."
)
EMPTY = "No LoRA could be prepared for import; the previous imports were kept."
FAILED = "The import failed; the previous LoRA imports were kept."
CACHE_NOTE = "Images already downloaded stay in the image cache."
CANCELLED = "Cancelled before anything was written; the previous imports were kept."
DISABLED = "LoraManager integration is not enabled"
NO_PATH = "LoraManager path not configured"


def _noun(count, word, plural=None):
    return word if count == 1 else (plural or word + "s")


def _count(count, word, plural=None):
    return f"{count:,} {_noun(count, word, plural)}"


def _import_line(result, skipped):
    parts = [f"Imported {_count(result['imported'], 'LoRA')}"]
    if result["linked"] or result["duplicates"]:
        linked = result["linked"] + result["duplicates"]
        parts.append(f"{linked:,} linked to an existing prompt")
    if skipped:
        parts.append(f"{skipped:,} skipped")
    return " · ".join(parts)


class LoraImportRoutesMixin:
    """Mixin providing the reviewed LoRA import (preview, apply, job)."""

    def _register_lora_import_routes(self, routes):
        @routes.post("/prompt_manager/lora/import/preview")
        async def lora_import_preview_route(request):
            return await self.preview_lora_import(request)

        @routes.post("/prompt_manager/lora/import/apply")
        async def lora_import_apply_route(request):
            return await self.apply_lora_import(request)

    # ── shared stages ─────────────────────────────────────────────────

    async def _lora_source(self):
        """``(path, None)``: the configured, allowed LoraManager path, or
        ``(None, (code, message))`` when the import cannot run."""
        from ..config import IntegrationConfig

        if not IntegrationConfig.LORA_MANAGER_ENABLED:
            return None, ("lora-disabled", DISABLED)
        lm_path = IntegrationConfig.LORA_MANAGER_PATH
        if not lm_path:
            return None, ("lora-path", NO_PATH)
        allowed = await self._run_in_executor(
            lora_utils.resolve_lora_manager_path, lm_path
        )
        if allowed is None:
            from .lora_integration import _LORA_PATH_ERROR

            return None, ("lora-path", _LORA_PATH_ERROR)
        return allowed, None

    async def stage_lora_import(self, files, *, on_record=None, cancelled=None):
        """Stage ``files`` off the loop: records, skips and download counts."""
        from ..config import IntegrationConfig

        cache_dir = await self._run_in_executor(lora_utils.get_lora_image_cache_dir)
        loop = asyncio.get_running_loop()

        def report(index, record, staged):
            if on_record is not None:
                loop.call_soon_threadsafe(on_record, index, record, staged)

        return await self._run_in_executor(
            lambda: lora_import.stage(
                files,
                cache_dir,
                IntegrationConfig.CIVITAI_API_KEY,
                on_record=report,
                cancelled=cancelled or (lambda: False),
                logger=self.logger,
            )
        )

    async def lora_files_unchanged(self, lm_path, expected):
        """Whether the metadata files under ``lm_path`` still fingerprint as
        ``expected`` (listed again, so an added file counts too)."""

        def current():
            return lora_import.fingerprint(lora_import.discover(lm_path))

        try:
            return await self._run_in_executor(current) == expected
        except OSError:
            return False

    async def replace_lora_imports(self, db, staged, *, expected_ids, allow_empty):
        """Swap the collection for ``staged.records`` in one transaction."""
        return await self._run_in_executor(
            lambda: db.replace_category_prompts(
                CATEGORY,
                staged.records,
                expected_ids=expected_ids,
                allow_empty=allow_empty,
            )
        )

    # ── preview ───────────────────────────────────────────────────────

    async def preview_lora_import(self, request):
        data, error = await self._read_maintenance_body(request, ())
        if error is not None:
            return error
        lm_path, refused = await self._lora_source()
        if refused is not None:
            return _failure(refused[1], 400, refused[0])
        db = self.db
        identity = self._database_identity(db)
        try:
            cache_dir = await self._run_in_executor(
                lambda: lora_utils.get_lora_image_cache_dir(create=False)
            )
            found = await self._run_in_executor(lora_import.preview, lm_path, cache_dir)
            state = await self._run_in_executor(db.category_import_state, CATEGORY)
        except Exception:
            self.logger.exception("LoRA import preview failed")
            return _failure("The LoRA import could not be reviewed.", 500)
        if identity != self._database_identity(self.db):
            return _failure(
                "The database was replaced while it was reviewed. Review again.",
                409,
                "storage-changed",
            )
        return await self._lora_plan_response(db, identity, lm_path, found, state)

    async def _lora_plan_response(self, db, identity, lm_path, found, state):
        from ..config import IntegrationConfig

        current = len(state["ids"])
        blocked = state["paired"]
        policy = self._safety_backup_policy()
        try:
            disk, _short, size = await self._run_in_executor(
                disk_plan, db.model.db_path, backup=policy != "off", vacuum=False
            )
        except (OSError, sqlite3.Error):
            self.logger.exception("Could not measure the disk space for a plan")
            return _failure("The LoRA import could not be reviewed.", 500)
        token = None
        expires_at = None
        if not blocked:
            payload = {
                "path": lm_path,
                "fingerprint": found["fingerprint"],
                "expected_ids": state["ids"],
                "empty": found["with_metadata"] == 0,
                "backup": policy,
            }
            try:
                token = self._maintenance_plans.issue(
                    LORA_OPERATION, found["fingerprint"], payload, identity
                )
            except ScopeTooLarge:
                return _failure(
                    "This import is too large to review at once.", 413, "too-large"
                )
            expires_at = _iso(self._maintenance_plans.peek(token).expires_at)
        name = os.path.basename(db.model.db_path)
        downloads = found["planned_downloads"]
        reasons = (
            [f"{_count(blocked, 'imported prompt')} used by saved pairs"]
            if blocked
            else []
        )
        return web.json_response(
            {
                "success": True,
                "operation": LORA_OPERATION,
                "token": token,
                "expires_at": expires_at,
                "lora_files": found["lora_files"],
                "with_metadata": found["with_metadata"],
                "current_imports": current,
                "blocked_by_pairs": blocked,
                "planned_downloads": downloads,
                "scope": {
                    "count": found["with_metadata"],
                    "label": (
                        f"Replace {_count(current, 'imported prompt')} with "
                        f"{_count(found['with_metadata'], 'LoRA')}"
                    ),
                },
                "protected": {"count": blocked, "reasons": reasons},
                "backup": {
                    "policy": policy,
                    "destination_label": f"{name}.backup_<date and time>",
                    "expected_bytes": size if policy != "off" else None,
                },
                "media": {
                    "deletes_files": False,
                    "label": "No files are removed; image links are replaced",
                },
                "network": {
                    "planned_downloads": downloads,
                    "api_key": bool(IntegrationConfig.CIVITAI_API_KEY),
                },
                "details": [{"label": n, "detail": ""} for n in found["names"]],
                "disk": disk,
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── apply ─────────────────────────────────────────────────────────

    async def apply_lora_import(self, request):
        data, error = await self._read_maintenance_body(request, ("token",))
        if error is not None:
            return error
        token = data.get("token")
        if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_LENGTH:
            return _invalid("token must be the plan's token")
        plan, refused = self._peek_plan(token)
        if refused is not None:
            return refused
        if plan.operation != LORA_OPERATION:
            return _failure(
                "This review expired. Review the plan again.", 410, "plan-expired"
            )
        store = self._maintenance_plans
        db = self.db
        if plan.db_identity != self._database_identity(db):
            store.discard(token)
            return _failure(
                "The database was replaced since the review. Review again.",
                409,
                "storage-changed",
            )
        lm_path = plan.payload["path"]
        # M5: the integration must still be on and at the reviewed install.
        current, _refused = await self._lora_source()
        if current != lm_path:
            store.discard(token)
            return _failure(INTEGRATION_CHANGED, 409, "plan-changed")
        try:
            files = await self._run_in_executor(lora_import.discover, lm_path)
            now = await self._run_in_executor(lora_import.fingerprint, files)
            backup = self._backup_promised(plan)
            _disk, short, _size = await self._run_in_executor(
                disk_plan, db.model.db_path, backup=backup, vacuum=False
            )
        except Exception:
            self.logger.exception("LoRA import could not start")
            return _failure(
                "The import could not start. Nothing was changed.", 500, "apply-failed"
            )
        if now != plan.fingerprint:
            store.discard(token)
            return _failure(SOURCE_CHANGED, 409, "plan-changed")
        if short is not None:
            return _failure(short_space_message(short), 507, "insufficient-space")
        plan, refused = self._peek_plan(token)
        if refused is not None:
            return refused
        try:
            job = self._jobs.start(
                LORA_IMPORT_JOB_KIND,
                lora_import.JOB_TITLE,
                admission="records",
                cancellable=True,
                scope={"operation": LORA_OPERATION, "lora_files": len(files)},
                groups=lora_import.GROUPS,
                runner=lambda job: self._run_lora_import(job, db, plan, files, backup),
            )
        except JobBusy as busy:
            return busy_response(busy)
        store.consume(token, LORA_OPERATION, job_id=job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    # ── the job ───────────────────────────────────────────────────────

    async def _run_lora_import(self, job, db, plan, files, backup_on):
        total = len(files)
        job.update(total=total, status_line="Preparing LoRA records")
        job.log_line(f"Found {_count(total, 'LoRA metadata file')}")

        def on_record(index, record, staged):
            job.update(
                processed=index + 1,
                progress=int(90 * (index + 1) / max(total, 1)),
                status_line=f"Prepared {index + 1:,} of {total:,}",
            )
            job.publish({"type": "progress", "progress": job.progress})

        try:
            staged = await self.stage_lora_import(
                files, on_record=on_record, cancelled=lambda: job.cancelled
            )
        except lora_import.ImportCancelled:
            job.log_line(f"{CANCELLED} {CACHE_NOTE}")
            job.update(status_line=CANCELLED)
            job.finish("cancelled")
            return
        except Exception:
            self.logger.exception("LoRA import staging failed")
            return _fail_job(job, "import-failed", f"{FAILED} {CACHE_NOTE}")
        self._count_downloads(job, staged)
        # M4: what staging skipped stays on the job whatever happens next.
        job.record_counted(
            "skipped",
            len(staged.skipped),
            [{"path": "", "detail": name} for name in staged.skipped],
        )
        # Past this point nothing stops halfway: the replace is one transaction.
        # Published, so a view that offered Cancel during the downloads drops it.
        job.cancellable = False
        job.publish({"type": "progress", "progress": job.progress})
        if job.cancelled:
            job.log_line(f"{CANCELLED} {CACHE_NOTE}")
            job.update(status_line=CANCELLED)
            job.finish("cancelled")
            return
        ok, _backup = await self._safety_backup_step(job, db, backup_on)
        if not ok:
            return
        # I1: the files are listed and fingerprinted again right before the
        # replace, so a file changed, removed or added while the downloads ran
        # (LoraManager refreshing its CivitAI info) aborts with nothing deleted.
        if not await self.lora_files_unchanged(plan.payload["path"], plan.fingerprint):
            return _fail_job(job, "plan-changed", f"{SOURCE_CHANGED} {CACHE_NOTE}")
        job.update(status_line="Replacing the LoRA collection", progress=95)
        await self._replace_in_job(job, db, plan, staged)

    def _count_downloads(self, job, staged):
        downloaded = staged.stats.get("downloaded", 0)
        errors = staged.stats.get("download_errors", 0)
        job.record_counted("downloaded", downloaded)
        job.record_counted("download_errors", errors)
        if downloaded or errors:
            job.log_line(
                f"Downloaded {_count(downloaded, 'example image')}"
                + (f"; {errors:,} could not be downloaded" if errors else "")
            )

    async def _replace_in_job(self, job, db, plan, staged):
        try:
            result = await self.replace_lora_imports(
                db,
                staged,
                expected_ids=plan.payload["expected_ids"],
                allow_empty=plan.payload["empty"],
            )
        except PlanChanged:
            return _fail_job(job, "plan-changed", f"{IMPORTS_CHANGED} {CACHE_NOTE}")
        except PairDependencyError:
            return _fail_job(job, "blocked-by-pairs", f"{BLOCKED} {CACHE_NOTE}")
        except EmptyReplacement:
            return _fail_job(job, "nothing-to-import", f"{EMPTY} {CACHE_NOTE}")
        except Exception:
            self.logger.exception("LoRA import failed")
            return _fail_job(job, "import-failed", f"{FAILED} {CACHE_NOTE}")
        rows = {"imported": [], "linked": []}
        for row in result["rows"]:
            group = "imported" if row["outcome"] == "imported" else "linked"
            rows[group].append({"path": "", "detail": row["name"]})
        job.record_counted("imported", result["imported"], rows["imported"])
        job.record_counted(
            "linked", result["linked"] + result["duplicates"], rows["linked"]
        )
        line = _import_line(result, len(staged.skipped))
        if result["removed"]:
            job.log_line(f"Replaced {_count(result['removed'], 'previous import')}")
        job.log_line(line)
        job.update(progress=100, processed=job.total, status_line=line)
