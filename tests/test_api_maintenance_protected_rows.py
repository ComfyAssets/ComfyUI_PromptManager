"""Protected maintenance items are listed in Activity, not only counted (#586).

Each record a maintenance job keeps gets one ``protected`` row with its
reason, in the wording of that operation's review, and the job's
downloadable report lists every one of them (never cut at 200 rows). A kept
image link names its file, so Activity can still open and locate it.
"""

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.jobs import MAX_OUTCOME_ROWS  # noqa: E402
from py.config import GalleryConfig, PromptManagerConfig  # noqa: E402

try:
    from tests.test_api_maintenance import ApplyTestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_maintenance import ApplyTestCase  # noqa: E402


def _record(detail):
    return {"path": "", "root": "", "root_index": None, "detail": detail}


class TestProtectedRows(ApplyTestCase):
    def duplicate(self, text, created_at="2999-01-01"):
        with self.api.db.model.get_connection() as conn:
            return conn.execute(
                "INSERT INTO prompts(text, created_at) VALUES(?, ?)",
                (text.upper(), created_at),
            ).lastrowid

    def assert_complete(self, job, count):
        self.assertEqual(job.counters["protected"], count)
        report = job.report()["outcomes"]["protected"]
        self.assertEqual(
            (report["count"], len(report["rows"]), report["truncated"]),
            (count, count, False),
        )

    async def test_duplicate_prompts_lists_each_kept_copy(self):
        paired = self.save("A dog")
        self.api.db.save_prompt_pair("A dog", "")
        paired_copy = self.duplicate("A dog")
        titled = self.save("A bird")
        titled_copy = self.duplicate("A bird")
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (titled_copy, "Bird recipe"),
            )
        plan, _summary, job = await self.run_apply("duplicate_prompts")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            sorted(row["detail"] for row in job.outcomes["protected"]),
            sorted(
                [
                    f"Prompt {paired}: kept, belongs to a saved pair",
                    f"Prompt {paired_copy}: kept, belongs to a saved pair",
                    f"Prompt {titled}: kept, a copy has its own Library title",
                    f"Prompt {titled_copy}: kept, a copy has its own Library title",
                ]
            ),
        )
        self.assertEqual(plan["protected"]["count"], 4)
        self.assert_complete(job, 4)

    async def test_unused_components_lists_each_kept_prompt_with_its_reasons(self):
        self.save("Orphan")
        tagged = self.save("Kept", tags=["__protected__"])
        self.api.db.save_prompt_pair("Paired", "")
        paired = self.api.db.search_prompts(text="Paired")[0]["id"]
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_tags(prompt_id, tag_id) "
                "SELECT ?, id FROM tags WHERE name='__protected__'",
                (paired,),
            )
        plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(
            sorted(job.outcomes["protected"], key=lambda row: row["detail"]),
            sorted(
                [
                    _record(f"Prompt {tagged}: kept, tagged __protected__"),
                    _record(
                        f"Prompt {paired}: kept, in a saved pair; "
                        "tagged __protected__"
                    ),
                ],
                key=lambda row: row["detail"],
            ),
        )
        self.assertEqual(plan["protected"]["count"], 2)
        self.assert_complete(job, 2)

    async def test_report_lists_every_kept_prompt(self):
        PromptManagerConfig.SAFETY_BACKUP = False
        count = MAX_OUTCOME_ROWS + 5
        for n in range(count):
            self.save(f"Kept {n:03d}", tags=["__protected__"])
        _plan, _summary, job = await self.run_apply("unused_components")
        self.assertEqual(len(job.outcomes["protected"]), MAX_OUTCOME_ROWS)
        self.assert_complete(job, count)

    async def test_missing_links_lists_each_kept_link_with_its_file(self):
        unmounted = Path(self.tmpdir) / "unmounted"
        unmounted.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir), str(unmounted)]
        prompt = self.save("Links")
        self.image(prompt, "gone.png", exists=False)
        self.image(prompt, "sub/away.png", folder=unmounted, exists=False)
        self.image(prompt, "x.png", folder=Path(self.tmpdir) / "elsewhere")
        shutil.rmtree(unmounted)
        plan, _summary, job = await self.run_apply("missing_links")
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(plan["protected"]["count"], 2)
        rows = sorted(job.outcomes["protected"], key=lambda row: row["path"])
        self.assertEqual(
            [(row["path"], row["detail"]) for row in rows],
            [
                ("sub/away.png", "Kept: link under an unavailable source"),
                ("x.png", "Kept: link outside the image sources"),
            ],
        )
        # The gone source keeps the path under its label; outside every
        # source the row names the file only.
        self.assertEqual((rows[0]["root"], rows[0]["root_index"]), ("unmounted", None))
        self.assertEqual((rows[1]["root"], rows[1]["root_index"]), ("", None))
        self.assertNotIn(self.tmpdir, str(job.report()))
        self.assert_complete(job, 2)
