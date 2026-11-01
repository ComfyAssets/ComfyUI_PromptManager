"""Pair category and tags repair through the maintenance routes (#550).

``pair_metadata`` is reviewed and applied like the other maintenance plans:
``options`` must be ``{}``, the job is titled "Pair category and tags", the
safety backup follows the ``maintenance.safety_backup`` policy, and the job's
downloadable report lists every filled prompt, never cut at 200 rows.
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.jobs import MAX_OUTCOME_ROWS  # noqa: E402
from py.api.maintenance import MAINTENANCE_TITLES  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

try:
    from tests.test_api_maintenance import WAIT, ApplyTestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_maintenance import WAIT, ApplyTestCase  # noqa: E402

OPERATION = "pair_metadata"
TITLE = "Pair category and tags"


class PairMetadataAPITestCase(ApplyTestCase):
    def pair(self, positive, negative="", **metadata):
        pair_id = self.api.db.save_prompt_pair(positive, negative, **metadata)
        with self.api.db.model.get_connection() as conn:
            prompt = conn.execute(
                "SELECT positive_prompt_id FROM prompt_pairs WHERE id=?", (pair_id,)
            ).fetchone()[0]
        return pair_id, prompt

    def category(self, prompt_id):
        with self.api.db.model.get_connection() as conn:
            return conn.execute(
                "SELECT category FROM prompts WHERE id=?", (prompt_id,)
            ).fetchone()[0]


class TestPairMetadataRoutes(PairMetadataAPITestCase):
    async def test_preview_accepts_the_operation_and_no_options(self):
        self.assertEqual(MAINTENANCE_TITLES[OPERATION], TITLE)
        self.pair("a cat", category="portrait", tags=["a"])
        plan = await self.preview(OPERATION)
        self.assertEqual(plan["operation"], OPERATION)
        self.assertEqual(plan["scope"]["count"], 1)
        self.assertEqual(plan["backup"]["policy"], "on")
        refused = await self.preview(OPERATION, 400, options={"all": True})
        self.assertEqual(refused["code"], "invalid-request")

    async def test_apply_fills_with_a_backup_and_names_it(self):
        _pair, prompt = self.pair("a cat", "neg", category="portrait", tags=["a", "b"])
        plan, summary, job = await self.run_apply(OPERATION)
        self.assertEqual(summary["title"], TITLE)
        self.assertEqual(summary["admission"], "records")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(self.category(prompt), "portrait")
        [backup] = self.backups()
        self.assertEqual(
            job.status_line,
            "Filled 1 prompt from its saved pairs (1 category, 1 tag set) · "
            f"Safety backup {backup}",
        )
        self.assertEqual(
            job.outcomes["changed"][0]["detail"],
            f"Prompt {prompt}: category “portrait”; tags a, b",
        )
        self.assertEqual(job.counters["changed"], 1)

    async def test_protected_prompts_are_counted(self):
        _a, prompt = self.pair("conflict", "n1", category="x")
        self.pair("conflict", "n2", category="y")
        self.pair("fill me", category="portrait")
        plan, _summary, job = await self.run_apply(OPERATION)
        self.assertEqual(plan["protected"]["reasons"], ["1 where pairs disagree"])
        self.assertEqual(job.counters["protected"], 1)
        self.assertIsNone(self.category(prompt))
        # Live test (#550): the count comes with its rows, in the review's wording.
        self.assertEqual(
            job.outcomes["protected"],
            [
                {
                    "path": "",
                    "root": "",
                    "root_index": None,
                    "detail": (
                        f"Prompt {prompt}: kept, pairs disagree on category: x, y"
                    ),
                }
            ],
        )
        report = job.report()["outcomes"]["protected"]
        self.assertEqual((report["count"], report["truncated"]), (1, False))

    async def test_report_lists_every_kept_prompt(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        count = MAX_OUTCOME_ROWS + 5
        for n in range(count):
            _pair, prompt = self.pair(f"kept {n:03d}", category="portrait")
            with self.api.db.model.get_connection() as conn:
                conn.execute(
                    "UPDATE prompts SET updated_at='2099-01-01T00:00:00+00:00' "
                    "WHERE id=?",
                    (prompt,),
                )
        _plan, _summary, job = await self.run_apply(OPERATION)
        self.assertEqual(job.counters["protected"], count)
        self.assertEqual(len(job.outcomes["protected"]), MAX_OUTCOME_ROWS)
        protected = job.report()["outcomes"]["protected"]
        self.assertEqual(
            (len(protected["rows"]), protected["truncated"]), (count, False)
        )
        self.assertTrue(
            protected["rows"][0]["detail"].endswith(
                ": kept, edited after it was saved; its empty category or tags "
                "may be intentional"
            )
        )

    async def test_policy_off_writes_without_a_backup(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        _pair, prompt = self.pair("no backup", category="portrait")
        plan, _summary, job = await self.run_apply(OPERATION)
        self.assertEqual(plan["backup"]["policy"], "off")
        self.assertEqual(job.state, "finished", job.log)
        self.assertIn("No safety backup (the policy is off)", "\n".join(job.log))
        self.assertTrue(job.status_line.endswith("· No safety backup"))
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.category(prompt), "portrait")

    async def test_reviewed_with_the_policy_on_keeps_the_backup(self):
        self.pair("promise", category="portrait")
        plan = await self.preview(OPERATION)
        PromptManagerConfig.SAFETY_BACKUP = False
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(len(self.backups()), 1)

    async def test_plan_changed_writes_nothing(self):
        pair_id, prompt = self.pair("drift", category="portrait")
        plan = await self.preview(OPERATION)
        self.api.db.update_prompt_pair_metadata(pair_id, category="landscape")
        body = await self.apply(plan["token"])
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_event["code"], "plan-changed")
        self.assertIsNone(self.category(prompt))

    async def test_report_lists_every_filled_prompt(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        count = MAX_OUTCOME_ROWS + 5
        for n in range(count):
            self.pair(f"bulk {n:03d}", category="portrait")
        _plan, _summary, job = await self.run_apply(OPERATION)
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(len(job.outcomes["changed"]), MAX_OUTCOME_ROWS)
        response = await self.client.request(
            "GET", f"/prompt_manager/jobs/{job.id}/report"
        )
        self.assertEqual(response.status, 200)
        report = await response.json()
        changed = report["outcomes"]["changed"]
        self.assertEqual(changed["count"], count)
        self.assertEqual(len(changed["rows"]), count)
        self.assertFalse(changed["truncated"])
        self.assertTrue(
            all(
                row["detail"].endswith("category “portrait”") for row in changed["rows"]
            )
        )
