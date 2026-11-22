"""Tag cleanup routes (#618, ``py/api/tag_cleanup.py``).

``POST /prompt_manager/jobs/tag_cleanup`` finds tags to clean up as a ``read``
Activity job; ``GET /prompt_manager/maintenance/tag_cleanup/{job_id}`` answers
every rule with the findings it holds; ``tag_cleanup`` previews the ticked
findings as a reviewed plan whose apply always takes a safety backup first,
whatever the setting, then deletes each target that is unchanged and keeps,
with its reason, each one that changed.
"""

import asyncio
import json
import os

try:
    from tests.test_api_maintenance import APPLY, WAIT, ApplyTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_maintenance import APPLY, WAIT, ApplyTestCase

from py.config import PromptManagerConfig  # noqa: E402

FIND = "/prompt_manager/jobs/tag_cleanup"
FINDINGS = "/prompt_manager/maintenance/tag_cleanup/"


class TagCleanupTestCase(ApplyTestCase):
    def tags(self, *names):
        with self.api.db.model.get_connection() as conn:
            for name in names:
                conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))

    def names(self):
        with self.api.db.model.get_connection() as conn:
            return sorted(r[0] for r in conn.execute("SELECT name FROM tags"))

    def seed(self):
        """Two comma tags (one on a prompt), two unused, one clean in use."""
        self.save("Owner", tags=["a, b", "clean"])
        self.tags("x，y", "ghost", "spirit")

    async def find(self):
        response = await self.client.request("POST", FIND)
        text = await response.text()
        self.assertEqual(response.status, 202, text)
        job = self.api._jobs.get(json.loads(text)["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job

    async def findings(self, job_id, status=200):
        response = await self.client.request("GET", f"{FINDINGS}{job_id}")
        text = await response.text()
        self.assertEqual(response.status, status, text)
        self.assertNotIn(self.tmpdir, text)
        if status == 200:
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return json.loads(text)

    async def select(self, job, **by_rule):
        """The preview body selecting every finding of the named rules."""
        body = await self.findings(job.id)
        rules = {rule["id"]: rule for rule in body["rules"]}
        return {
            "job_id": job.id,
            "select": [
                {
                    "rule": rule,
                    "tag_ids": [f["tag_id"] for f in rules[rule]["findings"]],
                }
                for rule in by_rule
            ],
        }

    async def plan(self, options, status=200):
        return await self.preview("tag_cleanup", status=status, options=options)


class TestFindJob(TagCleanupTestCase):
    async def test_find_is_a_read_job_that_records_one_row_per_tag(self):
        self.seed()
        job = await self.find()
        self.assertEqual(
            (job.kind, job.title, job.admission, job.cancellable),
            ("tag_cleanup", "Find tags to clean up", "read", False),
        )
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters, {"found": 4})
        self.assertEqual(
            [row["detail"] for row in job.outcomes["found"]],
            [
                "a, b · Tags containing a comma",
                "x，y · Tags containing a comma",
                "ghost · Tags with no links",
                "spirit · Tags with no links",
            ],
        )
        self.assertEqual(job.status_line, "Found 4 tags to clean up")
        self.assertIn(job.id, self.api._tag_cleanup_finds)

    async def test_a_clean_library_finds_nothing(self):
        self.save("Owner", tags=["clean"])
        job = await self.find()
        self.assertEqual(job.counters, {"found": 0})
        self.assertEqual(job.status_line, "No tags need cleaning up")

    async def test_a_failed_search_says_so_and_holds_nothing(self):
        from database import tag_cleanup

        original = tag_cleanup.find_tag_cleanup
        tag_cleanup.find_tag_cleanup = lambda path: 1 / 0
        try:
            job = await self.find()
        finally:
            tag_cleanup.find_tag_cleanup = original
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.status_line, "The tag search failed.")
        body = await self.findings(job.id, status=409)
        self.assertEqual(body["code"], "no-groups")

    async def test_a_second_find_while_one_runs_is_busy(self):
        gate = asyncio.Event()

        async def hold(job, db):
            await gate.wait()

        self.api._run_tag_cleanup_find = hold
        response = await self.client.request("POST", FIND)
        self.assertEqual(response.status, 202)
        first = (await response.json())["job"]["id"]
        response = await self.client.request("POST", FIND)
        body = await response.json()
        self.assertEqual((response.status, body["code"]), (409, "busy"))
        reply = await self.findings(first, status=409)
        self.assertEqual(reply["code"], "job-running")
        gate.set()
        await asyncio.wait_for(asyncio.shield(self.api._jobs.get(first).task), WAIT)

    async def test_only_the_last_three_finds_are_held(self):
        jobs = [await self.find() for _ in range(4)]
        self.assertEqual(list(self.api._tag_cleanup_finds), [j.id for j in jobs[1:]])
        body = await self.findings(jobs[0].id, status=404)
        self.assertEqual(body["code"], "not-found")


class TestFindingsRoute(TagCleanupTestCase):
    async def test_findings_list_every_rule_with_its_copy(self):
        self.seed()
        job = await self.find()
        body = await self.findings(job.id)
        self.assertTrue(body["success"])
        self.assertEqual(body["job"], {"id": job.id, "state": "finished"})
        comma, unused = body["rules"]
        self.assertEqual(
            {k: comma[k] for k in ("id", "title", "total")},
            {"id": "comma", "title": "Tags containing a comma", "total": 2},
        )
        self.assertTrue(comma["explanation"] and comma["action_label"])
        self.assertEqual(
            [(f["name"], f["detail"]) for f in comma["findings"]],
            [("a, b", "1 prompt"), ("x，y", "no links")],
        )
        self.assertEqual(set(comma["findings"][0]), {"tag_id", "name", "detail"})
        self.assertEqual([f["name"] for f in unused["findings"]], ["ghost", "spirit"])

    async def test_the_total_says_when_the_cap_cut_the_list(self):
        from database import tag_cleanup

        self.tags("a", "b", "c")
        original = tag_cleanup.MAX_FINDINGS
        tag_cleanup.MAX_FINDINGS = 2
        try:
            job = await self.find()
        finally:
            tag_cleanup.MAX_FINDINGS = original
        unused = (await self.findings(job.id))["rules"][1]
        self.assertEqual((unused["total"], len(unused["findings"])), (3, 2))

    async def test_unknown_jobs_are_not_found(self):
        self.assertEqual((await self.findings("nope", status=404))["code"], "not-found")
        other = self.api._jobs.start(
            "health", "Health", admission="read", runner=lambda job: asyncio.sleep(0)
        )
        await other.task
        self.assertEqual(
            (await self.findings(other.id, status=404))["code"], "not-found"
        )


class TestPreview(TagCleanupTestCase):
    async def test_the_plan_names_scope_protection_and_a_required_backup(self):
        self.seed()
        job = await self.find()
        PromptManagerConfig.SAFETY_BACKUP = False
        plan = await self.plan(await self.select(job, comma=True))
        self.assertEqual(plan["operation"], "tag_cleanup")
        self.assertEqual(
            plan["scope"],
            {
                "count": 2,
                "label": "Delete 2 tags (removed from 1 prompt, 0 pairs and 0 images)",
            },
        )
        self.assertEqual(plan["protected"], {"count": 0, "reasons": []})
        self.assertEqual(plan["backup"]["policy"], "required")
        self.assertEqual(
            plan["backup"]["destination_label"], "prompts.db.backup_<date and time>"
        )
        self.assertEqual(plan["backup"]["expected_bytes"], self.database_bytes())
        self.assertEqual(
            plan["media"], {"deletes_files": False, "label": "No files are removed"}
        )
        self.assertEqual(
            [(d["label"], d["detail"]) for d in plan["details"]],
            [
                ("a, b", "Tags containing a comma · 1 prompt"),
                ("x，y", "Tags containing a comma · no links"),
            ],
        )
        self.assertNotIn('"links"', json.dumps(plan))  # the hash stays server-side

    async def test_every_bad_shape_is_invalid(self):
        self.seed()
        job = await self.find()
        good = await self.select(job, comma=True)
        comma_ids = good["select"][0]["tag_ids"]
        cases = [
            [],
            {},
            {"job_id": job.id},
            {**good, "extra": 1},
            {**good, "job_id": ""},
            {**good, "job_id": 3},
            {**good, "job_id": "x" * 65},
            {**good, "select": []},
            {**good, "select": "comma"},
            {**good, "select": [good["select"][0]] * 2},
            {**good, "select": [{"rule": "reboot", "tag_ids": comma_ids}]},
            {**good, "select": [{"rule": "comma"}]},
            {**good, "select": [{"rule": "comma", "tag_ids": comma_ids, "x": 1}]},
            {**good, "select": [{"rule": "comma", "tag_ids": []}]},
            {**good, "select": [{"rule": "comma", "tag_ids": [True]}]},
            {**good, "select": [{"rule": "comma", "tag_ids": ["1"]}]},
            {**good, "select": [{"rule": "comma", "tag_ids": [0]}]},
            {**good, "select": [{"rule": "comma", "tag_ids": comma_ids * 2}]},
            # A tag the search listed under the other rule.
            {**good, "select": [{"rule": "unused", "tag_ids": comma_ids}]},
            {**good, "select": [{"rule": "comma", "tag_ids": [999999]}]},
            {**good, "select": [{"rule": "comma", "tag_ids": list(range(1, 2002))}]},
            {**good, "select": [{"rule": "comma", "tag_ids": comma_ids}] * 3},
        ]
        for options in cases:
            with self.subTest(options=options):
                body = await self.plan(options, status=400)
                self.assertEqual(body["code"], "invalid-request")

    async def test_a_held_find_is_needed(self):
        self.seed()
        options = {"job_id": "nope", "select": [{"rule": "comma", "tag_ids": [1]}]}
        self.assertEqual((await self.plan(options, status=404))["code"], "not-found")

    async def test_hundreds_of_ids_are_not_too_large(self):
        self.tags(*(f"tag {n:04}" for n in range(500)))
        job = await self.find()
        plan = await self.plan(await self.select(job, unused=True))
        self.assertEqual(plan["scope"]["count"], 500)

    async def test_a_finding_changed_since_the_search_is_protected(self):
        self.seed()
        job = await self.find()
        options = await self.select(job, unused=True)
        with self.api.db.model.get_connection() as conn:
            conn.execute("UPDATE tags SET name='Ghost' WHERE name='ghost'")
        plan = await self.plan(options)
        self.assertEqual(
            plan["protected"],
            {"count": 1, "reasons": ["1 tag changed since the search"]},
        )
        self.assertEqual(plan["scope"]["count"], 1)


class TestApply(TagCleanupTestCase):
    async def run_plan(self, options):
        plan = await self.plan(options)
        body = await self.post(APPLY, {"token": plan["token"]}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return plan, job

    async def test_apply_takes_a_backup_even_with_the_setting_off(self):
        self.seed()
        find = await self.find()
        PromptManagerConfig.SAFETY_BACKUP = False
        plan, job = await self.run_plan(await self.select(find, comma=True))
        self.assertEqual(
            (job.kind, job.title, job.admission, job.cancellable),
            ("maintenance", "Tag cleanup", "records", False),
        )
        self.assertEqual(job.state, "finished", job.log)
        [backup] = self.backups()
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, backup)))
        self.assertEqual(job.counters, {"changed": 2, "protected": 0, "errors": 0})
        self.assertEqual(job.status_line, f"Deleted 2 tags · Safety backup {backup}")
        self.assertEqual(
            [row["detail"] for row in job.complete["changed"]],
            [
                "Deleted tag “a, b” (Tags containing a comma) · removed from 1 prompt",
                "Deleted tag “x，y” (Tags containing a comma)",
            ],
        )
        # Applying one rule leaves the other rule's tags.
        self.assertEqual(self.names(), ["clean", "ghost", "spirit"])
        self.assertEqual(self.api.db.search_prompts()[0]["tags"], ["clean"])

    async def test_changed_targets_are_kept_one_by_one_and_the_rest_apply(self):
        self.seed()
        find = await self.find()
        plan = await self.plan(await self.select(find, unused=True))
        with self.api.db.model.get_connection() as conn:
            conn.execute("DELETE FROM tags WHERE name='spirit'")
        body = await self.post(APPLY, {"token": plan["token"]}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters, {"changed": 1, "protected": 1, "errors": 0})
        self.assertEqual(
            [row["detail"] for row in job.complete["protected"]],
            ["Kept tag “spirit”: already deleted"],
        )
        self.assertEqual(self.names(), ["a, b", "clean", "x，y"])

    async def test_findings_protected_at_review_are_in_the_job(self):
        self.seed()
        find = await self.find()
        options = await self.select(find, unused=True)
        with self.api.db.model.get_connection() as conn:
            conn.execute("UPDATE tags SET name='Ghost' WHERE name='ghost'")
        _plan, job = await self.run_plan(options)
        self.assertEqual(job.counters["protected"], 1)
        self.assertEqual(
            [row["detail"] for row in job.complete["protected"]],
            ["Kept tag “ghost”: changed since the search"],
        )

    async def test_a_plan_applies_once_and_expires(self):
        self.seed()
        find = await self.find()
        plan, _job = await self.run_plan(await self.select(find, comma=True))
        used = await self.post(APPLY, {"token": plan["token"]}, 409)
        self.assertEqual(used["code"], "plan-used")
        expired = await self.post(APPLY, {"token": "f" * 32}, 410)
        self.assertEqual(expired["code"], "plan-expired")

    async def test_a_failed_job_offers_the_maintenance_section(self):
        self.seed()
        find = await self.find()
        plan = await self.plan(await self.select(find, comma=True))
        self.api.db.apply_maintenance = lambda *args: 1 / 0
        body = await self.post(APPLY, {"token": plan["token"]}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.status_line, "Maintenance failed; nothing was changed.")
        self.assertEqual(job.scope["operation"], "tag_cleanup")
        self.assertIn("a, b", self.names())


class TestOperationEnum(TagCleanupTestCase):
    async def test_the_operation_error_names_tag_cleanup(self):
        body = await self.preview("reboot", status=400)
        self.assertIn("tag_cleanup", body["error"])
