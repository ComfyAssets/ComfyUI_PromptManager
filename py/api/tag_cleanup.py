"""Tag cleanup as a find job, its findings and a reviewed plan (#618).

- ``POST /prompt_manager/jobs/tag_cleanup`` (body ignored) starts the find
  (202 ``{job}``): kind ``tag_cleanup``, "Find tags to clean up", admission
  ``read``, not cancellable (one snapshot read), outcome group ``found``. It
  runs every rule of ``database/tag_cleanup.py`` on one read-only snapshot,
  holds the result under the job id (the last ``MAX_HELD_FINDS``), records one
  ``found`` row per tag and writes nothing. 409 ``busy`` while a find or an
  exclusive job runs.
- ``GET /prompt_manager/maintenance/tag_cleanup/{job_id}`` answers every held
  finding at once: ``{success, job:{id, state}, rules:[{id, title,
  explanation, action_label, total, findings:[{tag_id, name, detail}]}]}``,
  at most ``MAX_FINDINGS`` per rule (``total`` is exact). 404 ``not-found``,
  409 ``job-running``, 409 ``no-groups`` (the find failed).
- ``POST /prompt_manager/maintenance/preview {operation:"tag_cleanup",
  options:{job_id, select:[{rule, tag_ids}]}}`` reviews the ticked findings
  (``preview_tag_cleanup``): each selected tag must be a held finding of that
  rule. The plan always takes a safety backup first (policy ``required``,
  maintainer ruling 2026-11-22) and its apply, the ``maintenance`` job "Tag
  cleanup", rechecks each tag and keeps, with its reason, each one that
  changed (``py/api/maintenance.py``).
"""

from aiohttp import web

from .job_routes import busy_response
from .jobs import TAG_CLEANUP_GROUP_NAMES, TAG_CLEANUP_JOB_KIND, JobBusy
from .maintenance import _failure, _invalid

try:
    from ...database import tag_cleanup as rules
except ImportError:
    from database import tag_cleanup as rules

TAG_CLEANUP = rules.OPERATION
FIND_TITLE = "Find tags to clean up"
FIND_FAILED = "The tag search failed."
MAX_HELD_FINDS = 3  # finds whose findings the server keeps for review
MAX_JOB_ID = 64


def _parse_select(entry, seen):
    """``(rule_id, tag_ids, None)`` for one select entry, else a message."""
    if not isinstance(entry, dict) or set(entry) != {"rule", "tag_ids"}:
        return None, None, "Each select entry is {rule, tag_ids}"
    rule_id, tag_ids = entry["rule"], entry["tag_ids"]
    if not isinstance(rule_id, str) or rule_id not in rules.RULE_IDS:
        return None, None, "rule must be one of: " + ", ".join(rules.RULE_IDS)
    if rule_id in seen:
        return None, None, "Each rule is selected once"
    limit = rules.MAX_FINDINGS
    if not isinstance(tag_ids, list) or not 0 < len(tag_ids) <= limit:
        return None, None, f"tag_ids must list 1 to {limit:,} tags"
    for tag_id in tag_ids:
        if type(tag_id) is not int or tag_id < 1:
            return None, None, "tag_ids must be tag ids"
    if len(set(tag_ids)) != len(tag_ids):
        return None, None, "tag_ids must not repeat a tag"
    return rule_id, tag_ids, None


def parse_options(options):
    """``(job_id, {rule_id: [tag_id]}, None)`` or ``(None, None, message)``."""
    if not isinstance(options, dict) or set(options) != {"job_id", "select"}:
        return None, None, "tag_cleanup takes options {job_id, select}"
    job_id, select = options["job_id"], options["select"]
    if not isinstance(job_id, str) or not 0 < len(job_id) <= MAX_JOB_ID:
        return None, None, "job_id must be a tag search's job id"
    if not isinstance(select, list) or not 0 < len(select) <= len(rules.RULES):
        return None, None, f"select must list 1 to {len(rules.RULES)} rules"
    chosen = {}
    for entry in select:
        rule_id, tag_ids, message = _parse_select(entry, chosen)
        if message:
            return None, None, message
        chosen[rule_id] = tag_ids
    return job_id, chosen, None


def _found_rows(result):
    return [
        {"path": "", "detail": f"{f['name']} · {rules.rule(rule_id).title}"}
        for rule_id in rules.RULE_IDS
        for f in result["rules"][rule_id]
    ]


def _public_rules(result):
    return [
        {
            "id": item.id,
            "title": item.title,
            "explanation": item.explanation,
            "action_label": item.action_label,
            "total": result["totals"][item.id],
            "findings": [
                {
                    "tag_id": f["tag_id"],
                    "name": f["name"],
                    "detail": rules.finding_detail(f),
                }
                for f in result["rules"][item.id]
            ],
        }
        for item in rules.RULES
    ]


class TagCleanupMixin:
    """Mixin providing the tag cleanup find, its findings and its plan."""

    def _register_tag_cleanup_routes(self, routes):
        @routes.post("/prompt_manager/jobs/tag_cleanup")
        async def start_tag_cleanup_find_route(request):
            return await self.start_tag_cleanup_find(request)

        @routes.get("/prompt_manager/maintenance/tag_cleanup/{job_id}")
        async def tag_cleanup_findings_route(request):
            return await self.tag_cleanup_findings(request)

    # ── find ──────────────────────────────────────────────────────────

    async def start_tag_cleanup_find(self, request):
        """Start the tag search (202), or 409 naming the job in the way."""
        db = self.db
        try:
            job = self._jobs.start(
                TAG_CLEANUP_JOB_KIND,
                FIND_TITLE,
                admission="read",
                cancellable=False,
                groups=TAG_CLEANUP_GROUP_NAMES,
                runner=lambda job: self._run_tag_cleanup_find(job, db),
            )
        except JobBusy as busy:
            return busy_response(busy)
        self._remember_tag_cleanup_find(job.id)
        return web.json_response({"success": True, "job": job.summary()}, status=202)

    def _remember_tag_cleanup_find(self, job_id):
        """Hold a slot for ``job_id`` (None until it found); keep the last few."""
        finds = self._tag_cleanup_finds
        finds[job_id] = None
        for held in list(finds):
            if self._jobs.get(held) is None:
                del finds[held]
        while len(finds) > MAX_HELD_FINDS:
            del finds[next(iter(finds))]

    async def _run_tag_cleanup_find(self, job, db):
        job.update(status_line="Looking for tags to clean up")
        try:
            result = await self._run_in_executor(
                rules.find_tag_cleanup, db.model.db_path
            )
        except Exception:
            self.logger.exception("Tag search failed")
            job.update(status_line=FIND_FAILED)
            job.log_line(FIND_FAILED)
            job.finish("failed")
            return
        if job.id in self._tag_cleanup_finds:
            self._tag_cleanup_finds[job.id] = result
        count = sum(result["totals"].values())
        job.record_counted("found", count, _found_rows(result))
        line = (
            f"Found {count:,} {'tag' if count == 1 else 'tags'} to clean up"
            if count
            else "No tags need cleaning up"
        )
        job.log_line(line)
        job.update(progress=100, status_line=line)

    # ── findings ──────────────────────────────────────────────────────

    def _held_tag_cleanup(self, job_id):
        """``(result, None)`` for a finished search's findings, else a reply."""
        job = self._jobs.get(job_id)
        finds = self._tag_cleanup_finds
        if job_id not in finds or job is None or job.kind != TAG_CLEANUP_JOB_KIND:
            return None, _failure(
                "No tag search with this id. Search again.", 404, "not-found"
            )
        if job.running:
            return None, _failure(
                "The tag search is still running.", 409, "job-running"
            )
        if finds[job_id] is None:
            return None, _failure(
                "This search did not finish. Search again.", 409, "no-groups"
            )
        return finds[job_id], None

    async def tag_cleanup_findings(self, request):
        job_id = request.match_info["job_id"]
        result, error = self._held_tag_cleanup(job_id)
        if error is not None:
            return error
        job = self._jobs.get(job_id)
        return web.json_response(
            {
                "success": True,
                "job": {"id": job.id, "state": job.state},
                "rules": _public_rules(result),
            },
            headers={"Cache-Control": "no-store"},
        )

    # ── review ────────────────────────────────────────────────────────

    async def preview_tag_cleanup(self, options):
        """The ``tag_cleanup`` plan for the ticked findings of one search."""
        job_id, chosen, message = parse_options(options)
        if message:
            return _invalid(message)
        held, error = self._held_tag_cleanup(job_id)
        if error is not None:
            return error
        for rule_id, tag_ids in chosen.items():
            listed = {f["tag_id"] for f in held["rules"][rule_id]}
            if not listed.issuperset(tag_ids):
                return _invalid("A selected tag is not a finding of its rule")
        db = self.db
        identity = self._database_identity(db)
        try:
            draft = await self._run_in_executor(
                rules.plan_tag_cleanup, db.model.db_path, held, chosen
            )
        except Exception:
            self.logger.exception("Tag cleanup review failed")
            return _failure("Failed to review maintenance", 500)
        return await self._plan_response(
            db,
            identity,
            TAG_CLEANUP,
            draft.fingerprint,
            draft.public(),
            {
                "targets": draft.targets,
                "roots": [],
                "held": rules.held_rows(draft),
                "protected": draft.protected["count"],
                "rows": len(draft.targets),
            },
        )
