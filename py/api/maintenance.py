"""Maintenance routes: reviewed, read-only plans (#249 System S6).

``POST /prompt_manager/maintenance/preview {operation, options:{}}`` reviews
one operation (``duplicate_prompts``, ``optimize``, ``unused_components`` or
``missing_links``; ``database/maintenance.py``) off the event loop and
answers a ``Plan``: ``{success, token, operation, expires_at, scope:{count,
label}, protected:{count, reasons}, backup:{policy, destination_label},
media:{deletes_files, label}, details:[≤50 {label, detail}]}``. The preview
writes nothing. The plan's fingerprint, its targets and the sources it judged
stay in the server's ``PlanStore`` under the token, bound to the database the
preview read (a restore that replaces it invalidates the plan).

The operation enum is strict, ``options`` must be an empty object and the
body is capped at ``PREVIEW_BODY_MAX`` bytes. Errors are ``{success:false,
code, error}`` with ``invalid-request`` (400), ``too-large`` (413: the body,
or a plan over the store's budget), ``storage-changed`` (409: the database
was replaced while the preview read it) or a null code (500); none carries a
server path or exception text.
"""

import datetime
import os

from aiohttp import web

from .maintenance_plans import ScopeTooLarge

try:
    from ...database.maintenance import MAINTENANCE_OPERATIONS
except ImportError:
    from database.maintenance import MAINTENANCE_OPERATIONS

PREVIEW_BODY_MAX = 4096
# The concept's Maintenance row titles, which name each operation's job.
MAINTENANCE_TITLES = {
    "duplicate_prompts": "Duplicate prompt text",
    "optimize": "Optimize database",
    "unused_components": "Unused prompt components",
    "missing_links": "Missing-file links",
}
_OPERATION_ERROR = "operation must be one of: " + ", ".join(MAINTENANCE_OPERATIONS)


def _failure(message, status, code=None, **extra):
    return web.json_response(
        {"success": False, "code": code, "error": message, **extra}, status=status
    )


def _invalid(message):
    return _failure(message, 400, "invalid-request")


def _iso(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()


class MaintenanceRoutesMixin:
    """Mixin providing the /prompt_manager/maintenance plan endpoints."""

    def _register_maintenance_routes(self, routes):
        @routes.post("/prompt_manager/maintenance/preview")
        async def maintenance_preview_route(request):
            return await self.preview_maintenance(request)

    def _database_identity(self, db):
        """What binds a plan to one database: the swap generation and its file."""
        path = os.path.normcase(os.path.realpath(db.model.db_path))
        return (self._db_generation, path)

    @staticmethod
    def _safety_backup_policy():
        """``on`` or ``off``: whether an apply takes a safety backup first."""
        return "on"

    async def _read_maintenance_body(self, request, allowed):
        """``(data, None)`` for a capped JSON object with only ``allowed`` keys."""
        data, error = await self._read_json_body(request, max_bytes=PREVIEW_BODY_MAX)
        if error is not None:
            if error.status == 413:
                return None, _failure("Request body too large", 413, "too-large")
            return None, _invalid("Request body must be a JSON object")
        unknown = sorted(set(data) - set(allowed))
        if unknown:
            return None, _invalid("Unknown field: " + ", ".join(unknown)[:200])
        return data, None

    async def preview_maintenance(self, request):
        data, error = await self._read_maintenance_body(
            request, ("operation", "options")
        )
        if error is not None:
            return error
        operation = data.get("operation")
        options = data.get("options", {})
        if not isinstance(operation, str) or operation not in MAINTENANCE_OPERATIONS:
            return _invalid(_OPERATION_ERROR)
        if not isinstance(options, dict) or options:
            return _invalid(f"{operation} takes no options")
        db = self.db
        identity = self._database_identity(db)
        try:
            roots = (
                await self._run_in_executor(self._health_roots)
                if operation == "missing_links"
                else []
            )
            draft = await self._run_in_executor(
                db.preview_maintenance, operation, roots
            )
        except Exception:
            self.logger.exception(f"Maintenance preview of {operation} failed")
            return _failure("Failed to review maintenance", 500)
        if identity != self._database_identity(self.db):
            return _failure(
                "The database was replaced while it was reviewed. Review again.",
                409,
                "storage-changed",
            )
        policy = self._safety_backup_policy()
        store = self._maintenance_plans
        try:
            token = store.issue(
                operation,
                draft.fingerprint,
                {
                    "targets": draft.targets,
                    "roots": roots,
                    "protected": draft.protected["count"],
                    "backup": policy,
                },
                identity,
            )
        except ScopeTooLarge:
            return _failure(
                "This plan is too large to review at once.", 413, "too-large"
            )
        plan = store.peek(token, operation)
        name = os.path.basename(db.model.db_path)
        return web.json_response(
            {
                "success": True,
                "token": token,
                "expires_at": _iso(plan.expires_at),
                **draft.public(),
                "backup": {
                    "policy": policy,
                    "destination_label": f"{name}.backup_<date and time>",
                },
            },
            headers={"Cache-Control": "no-store"},
        )
