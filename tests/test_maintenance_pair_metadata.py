"""Pair category and tags repair (#550): the ``pair_metadata`` maintenance plan.

Before #546 a Pair node run saved its category and tags on the saved pair
only, so the positive prompt it created stayed Uncategorized and untagged.
``pair_metadata`` reviews those prompts and fills only their empty fields
from the prompt's non-archived pairs when every pair with a value agrees.
It never touches a negative, a saved pair or ``updated_at``, and a prompt
the Pair node did not create, that was edited, archived, or whose pairs
disagree, is kept (``550-design.md``).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import maintenance  # noqa: E402
from database import pair_metadata_repair as repair  # noqa: E402

try:
    from tests.test_maintenance_plans import MaintenanceDatabaseCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_maintenance_plans import MaintenanceDatabaseCase  # noqa: E402

OPERATION = "pair_metadata"


class PairMetadataCase(MaintenanceDatabaseCase):
    def pair(self, positive, negative="", **metadata):
        """A pre-#546 Pair save: the pair gets the metadata, the prompt none."""
        pair_id = self.db.save_prompt_pair(positive, negative, **metadata)
        return pair_id, self.positive_of(pair_id)

    def positive_of(self, pair_id):
        return self.sql(
            "SELECT positive_prompt_id FROM prompt_pairs WHERE id=?", pair_id
        )[0][0]

    def sql(self, statement, *args):
        with self.db.model.get_connection() as conn:
            return [tuple(row) for row in conn.execute(statement, args)]

    def run_sql(self, statement, *args):
        with self.db.model.get_connection() as conn:
            conn.execute(statement, args)

    def category(self, prompt_id):
        return self.sql("SELECT category FROM prompts WHERE id=?", prompt_id)[0][0]

    def tags(self, prompt_id):
        return sorted(
            row[0]
            for row in self.sql(
                "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
                "WHERE pt.prompt_id=?",
                prompt_id,
            )
        )

    def preview(self, operation=OPERATION, roots=None):
        return self.db.preview_maintenance(operation, [])

    def apply(self, draft):
        return self.db.apply_maintenance(
            draft.operation, draft.fingerprint, draft.targets, []
        )

    def edit_stamp(self, prompt_id, updated_at="2099-01-01T00:00:00+00:00"):
        self.run_sql(
            "UPDATE prompts SET updated_at=? WHERE id=?", updated_at, prompt_id
        )

    def details(self, draft):
        return [(row["label"], row["detail"]) for row in draft.details]


class TestClassification(PairMetadataCase):
    def test_fills_empty_category_and_tags_from_the_pair(self):
        pair_id, prompt = self.pair(
            "a cat on a sofa", "blurry", category="portrait", tags=["b", "a"]
        )
        draft = self.preview()
        self.assertEqual(draft.operation, OPERATION)
        self.assertEqual(draft.targets, [[prompt, True, True]])
        self.assertEqual(draft.scope["count"], 1)
        self.assertEqual(
            draft.scope["label"], "1 prompt to fill: 1 category, 1 tag set"
        )
        self.assertEqual(draft.protected, {"count": 0, "reasons": []})
        self.assertEqual(
            draft.media,
            {
                "deletes_files": False,
                "label": "No image file or saved pair is changed.",
            },
        )
        self.assertIn(
            (
                "a cat on a sofa",
                f"Fills category “portrait” and 2 tags: a, b (pair {pair_id})",
            ),
            self.details(draft),
        )
        self.assertEqual(draft.details[-1]["label"], "Record")
        self.assertEqual(draft.details[-1]["detail"], repair.RECORD_NOTE)

    def test_empty_plan(self):
        self.save("plain prompt")
        draft = self.preview()
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.scope["count"], 0)
        self.assertEqual(
            draft.scope["label"], "No Pair prompts are missing a category or tags"
        )
        self.assertEqual(draft.details, [])

    def test_prompt_already_complete_is_not_counted(self):
        _pair, prompt = self.pair("done", category="portrait", tags=["a"])
        self.run_sql("UPDATE prompts SET category='portrait' WHERE id=?", prompt)
        self.db.set_prompt_tags(prompt, ["a"])
        self.edit_stamp(
            prompt, self.sql("SELECT created_at FROM prompts WHERE id=?", prompt)[0][0]
        )
        self.assertEqual(self.preview().scope["count"], 0)

    def test_whitespace_category_is_blank_on_prompt_and_pair(self):
        _p1, blank_prompt = self.pair("ws prompt", category="  portrait  ")
        self.run_sql("UPDATE prompts SET category='   ' WHERE id=?", blank_prompt)
        _p2, blank_pair = self.pair("ws pair", category="   ")
        draft = self.preview()
        self.assertEqual(draft.targets, [[blank_prompt, True, False]])
        self.assertIn("“portrait”", self.details(draft)[0][1])
        self.assertNotIn(blank_pair, [t[0] for t in draft.targets])

    def test_mixed_fields_category_fills_while_tags_conflict(self):
        _a, prompt = self.pair("mixed", "n1", category="portrait", tags=["a"])
        self.pair("mixed", "n2", category="portrait", tags=["b"])
        draft = self.preview()
        self.assertEqual(draft.targets, [[prompt, True, False]])
        self.assertEqual(draft.scope["count"], 1)
        self.assertEqual(draft.protected["count"], 1)
        self.assertEqual(draft.protected["reasons"], ["1 where pairs disagree"])
        self.assertIn(("mixed", "Kept: pairs disagree on tags"), self.details(draft))

    def test_mixed_fields_tags_fill_while_category_conflicts(self):
        _a, prompt = self.pair("mixed2", "n1", category="portrait", tags=["a"])
        self.pair("mixed2", "n2", category="landscape", tags=["a"])
        draft = self.preview()
        self.assertEqual(draft.targets, [[prompt, False, True]])
        self.assertIn(
            ("mixed2", "Kept: pairs disagree on category: landscape, portrait"),
            self.details(draft),
        )

    def test_category_set_and_tags_empty_fills_tags_only(self):
        _a, prompt = self.pair("has cat", category="portrait", tags=["a"])
        self.run_sql("UPDATE prompts SET category='mine' WHERE id=?", prompt)
        draft = self.preview()
        self.assertEqual(draft.targets, [[prompt, False, True]])

    def test_pairs_without_a_value_are_ignored(self):
        _a, prompt = self.pair("partial", "n1", category="portrait", tags=["a"])
        self.pair("partial", "n2")  # no category, no tags
        self.assertEqual(self.preview().targets, [[prompt, True, True]])

    def test_equal_values_across_pairs_fill(self):
        _a, prompt = self.pair("same", "n1", category="portrait", tags=["a", "b"])
        self.pair("same", "n2", category="portrait", tags=["b", "a"])
        self.assertEqual(self.preview().targets, [[prompt, True, True]])

    def test_category_is_case_sensitive(self):
        _a, prompt = self.pair("case", "n1", category="Portrait")
        self.pair("case", "n2", category="portrait")
        draft = self.preview()
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.protected["count"], 1)

    def test_edited_prompt_is_protected(self):
        cases = {
            "later": "2099-01-01T00:00:00+00:00",
            "null": None,
            "legacy": "2020-01-01 10:00:00",
            "garbage": "yesterday",
        }
        for name, stamp in cases.items():
            with self.subTest(name=name):
                _a, prompt = self.pair(f"edited {name}", category="portrait")
                self.edit_stamp(prompt, stamp)
                draft = self.preview()
                self.assertNotIn(prompt, [t[0] for t in draft.targets])
                self.assertIn(
                    (
                        f"edited {name}",
                        "Kept: edited after it was saved; its empty category or "
                        "tags may be intentional",
                    ),
                    self.details(draft),
                )
                self.run_sql(
                    "DELETE FROM prompt_pairs WHERE positive_prompt_id=?", prompt
                )

    def test_first_release_stamps_microseconds_apart_are_never_edited(self):
        _a, prompt = self.pair("caa9649f", category="portrait")
        self.run_sql(
            "UPDATE prompts SET created_at=?, updated_at=? WHERE id=?",
            "2026-10-18T10:00:00.000100+00:00",
            "2026-10-18T10:00:00.000180+00:00",
            prompt,
        )
        self.run_sql(
            "UPDATE prompt_pairs SET created_at=? WHERE positive_prompt_id=?",
            "2026-10-18T10:00:00.000400+00:00",
            prompt,
        )
        self.assertEqual(self.preview().targets, [[prompt, True, False]])

    def test_edited_beats_conflict(self):
        _a, prompt = self.pair("both", "n1", category="portrait")
        self.pair("both", "n2", category="landscape")
        self.edit_stamp(prompt)
        draft = self.preview()
        self.assertEqual(draft.protected["reasons"], ["1 edited after it was saved"])

    def test_archived_prompt_is_protected(self):
        _a, prompt = self.pair("archived", category="portrait")
        self.run_sql(
            "INSERT INTO prompt_archive(prompt_id, archived_at) VALUES(?, ?)",
            prompt,
            "2026-10-31T00:00:00+00:00",
        )
        draft = self.preview()
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.protected["reasons"], ["1 archived in the Library"])
        self.assertIn(
            ("archived", "Kept: archived in the Library"), self.details(draft)
        )

    def test_archived_pairs_are_no_source(self):
        pair_id, prompt = self.pair("old pair", "n1", category="portrait")
        self.db.archive_prompt_pair(pair_id)
        draft = self.preview()
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.protected["count"], 0)
        # An archived pair also never disagrees with a live one.
        self.pair("old pair", "n2", category="landscape")
        self.assertEqual(self.preview().targets, [[prompt, True, False]])
        self.assertIn("landscape", self.details(self.preview())[0][1])

    def test_prompt_not_created_by_its_pair_is_out_of_scope(self):
        prompt = self.save("saved by the node first")
        self.run_sql(
            "UPDATE prompts SET created_at=?, updated_at=? WHERE id=?",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            prompt,
        )
        self.db.save_prompt_pair("saved by the node first", category="portrait")
        draft = self.preview()
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.protected["count"], 0)

    def test_negative_is_never_a_target_and_positive_role_sources_only(self):
        _a, both = self.pair("dual", "shared negative", category="portrait")
        neg = self.sql("SELECT id FROM prompts WHERE text='shared negative'")[0][0]
        # The negative of pair A is the positive of pair B, made later: not
        # Pair-created as a positive, so out of scope.
        self.db.save_prompt_pair("shared negative", category="landscape")
        draft = self.preview()
        self.assertEqual(draft.targets, [[both, True, False]])
        self.assertNotIn(neg, [t[0] for t in draft.targets])

    def test_positive_first_then_negative_is_filled_from_positive_pairs_only(self):
        _a, prompt = self.pair("pos first", category="portrait")
        self.db.save_prompt_pair("another", "pos first", category="landscape")
        draft = self.preview()
        self.assertIn([prompt, True, False], draft.targets)
        self.assertIn("“portrait”", dict(self.details(draft))["pos first"])

    def test_more_than_twenty_tags_is_protected(self):
        tags = [f"t{n:02d}" for n in range(21)]
        _a, prompt = self.pair("many tags", category="portrait", tags=tags)
        draft = self.preview()
        self.assertEqual(draft.targets, [[prompt, True, False]])
        self.assertEqual(
            draft.protected["reasons"], ["1 whose pair has more than 20 tags"]
        )
        self.assertIn(
            ("many tags", "Kept: its pair has more than 20 tags"), self.details(draft)
        )

    def test_protected_rows_come_first_within_fifty(self):
        _c, conflict = self.pair("zz conflict", "n1", category="x")
        self.pair("zz conflict", "n2", category="y")
        for n in range(60):
            self.pair(f"fill {n:02d}", category="portrait")
        draft = self.preview()
        self.assertEqual(len(draft.details), maintenance.MAX_DETAILS)
        self.assertEqual(draft.details[0]["label"], "zz conflict")
        self.assertEqual(draft.details[-1]["label"], "Record")
        self.assertEqual(draft.scope["count"], 60)

    def test_same_library_same_fingerprint_and_pair_use_does_not_change_it(self):
        pair_id, _prompt = self.pair("fp", category="portrait", tags=["a"])
        first = self.preview().fingerprint
        self.db.record_prompt_pair_use(pair_id)
        self.db.update_prompt_pair_metadata(pair_id, notes="a note", title="t")
        self.assertEqual(self.preview().fingerprint, first)


class TestPreviewWritesNothing(PairMetadataCase):
    def test_preview_reads_one_read_only_snapshot(self):
        self.pair("cat", category="portrait", tags=["a"])
        before = self.sql("SELECT * FROM prompts ORDER BY id")
        tags = self.sql("SELECT * FROM prompt_tags ORDER BY prompt_id, tag_id")
        connect = maintenance.sqlite3.connect
        opened = []

        def spy(*args, **kwargs):
            opened.append(args[0])
            return connect(*args, **kwargs)

        with patch.object(maintenance.sqlite3, "connect", side_effect=spy):
            self.preview()
        self.assertEqual(len(opened), 1)
        self.assertTrue(str(opened[0]).endswith("?mode=ro"))
        self.assertEqual(self.sql("SELECT * FROM prompts ORDER BY id"), before)
        self.assertEqual(
            self.sql("SELECT * FROM prompt_tags ORDER BY prompt_id, tag_id"), tags
        )


class TestApply(PairMetadataCase):
    def snapshot_pairs(self):
        return (
            self.sql("SELECT * FROM prompt_pairs ORDER BY id"),
            self.sql("SELECT * FROM prompt_pair_tags ORDER BY pair_id, tag_id"),
        )

    def test_apply_fills_only_empty_fields_and_leaves_pairs_alone(self):
        _a, prompt = self.pair("apply me", "neg", category="portrait", tags=["a", "b"])
        _b, partial = self.pair("partial", "n1", category="portrait", tags=["a"])
        self.pair("partial", "n2", category="portrait", tags=["c"])
        neg = self.sql("SELECT id FROM prompts WHERE text='neg'")[0][0]
        pairs = self.snapshot_pairs()
        stamps = self.sql("SELECT id, created_at, updated_at FROM prompts ORDER BY id")
        state = self.sql("SELECT * FROM prompt_library_state ORDER BY prompt_id")
        tag_rows = self.sql("SELECT id, name FROM tags ORDER BY id")
        draft = self.preview()
        result = self.apply(draft)
        self.assertEqual(result["changed"], 2)
        self.assertEqual(
            result["rows"],
            [
                {"prompt_id": prompt, "category": "portrait", "tags": ["a", "b"]},
                {"prompt_id": partial, "category": "portrait"},
            ],
        )
        self.assertEqual(result["categories"], 2)
        self.assertEqual(result["tag_sets"], 1)
        self.assertEqual(
            (self.category(prompt), self.tags(prompt)), ("portrait", ["a", "b"])
        )
        self.assertEqual((self.category(partial), self.tags(partial)), ("portrait", []))
        self.assertEqual((self.category(neg), self.tags(neg)), (None, []))
        self.assertEqual(self.snapshot_pairs(), pairs)
        self.assertEqual(
            self.sql("SELECT id, created_at, updated_at FROM prompts ORDER BY id"),
            stamps,
        )
        self.assertEqual(
            self.sql("SELECT * FROM prompt_library_state ORDER BY prompt_id"), state
        )
        self.assertEqual(self.sql("SELECT id, name FROM tags ORDER BY id"), tag_rows)

    def test_second_run_finds_nothing_and_a_partial_fill_completes_later(self):
        _a, prompt = self.pair("partial", "n1", category="portrait", tags=["a"])
        p2, _same = self.pair("partial", "n2", category="portrait", tags=["c"])
        self.apply(self.preview())
        again = self.preview()
        self.assertEqual(again.scope["count"], 0)
        self.assertEqual(again.protected["count"], 1)  # tags still disagree
        self.db.update_prompt_pair_metadata(p2, tags=["a"])
        self.apply(self.preview())
        self.assertEqual(self.tags(prompt), ["a"])
        self.assertEqual(self.preview().protected["count"], 0)

    def test_drift_raises_plan_changed_and_writes_nothing(self):
        def pair_category(ids):
            self.db.update_prompt_pair_metadata(ids[0], category="landscape")

        def prompt_category(ids):
            self.run_sql("UPDATE prompts SET category='mine' WHERE id=?", ids[1])

        def new_pair(ids):
            self.db.save_prompt_pair("drift", "other neg", category="landscape")

        def tag_rename(ids):
            tag = self.sql("SELECT id FROM tags WHERE name='a'")[0][0]
            self.run_sql("UPDATE tags SET name='renamed' WHERE id=?", tag)

        def edited(ids):
            self.edit_stamp(ids[1])

        for change in (pair_category, prompt_category, new_pair, tag_rename, edited):
            with self.subTest(change=change.__name__):
                ids = self.pair("drift", "n", category="portrait", tags=["a"])
                draft = self.preview()
                change(ids)
                before = self.sql("SELECT id, category FROM prompts ORDER BY id")
                tags = self.sql("SELECT * FROM prompt_tags ORDER BY prompt_id")
                with self.assertRaises(maintenance.PlanChanged):
                    self.apply(draft)
                self.assertEqual(
                    self.sql("SELECT id, category FROM prompts ORDER BY id"), before
                )
                self.assertEqual(
                    self.sql("SELECT * FROM prompt_tags ORDER BY prompt_id"), tags
                )
                self.run_sql("DELETE FROM prompt_pairs")
                self.run_sql("DELETE FROM prompts")
                self.run_sql("DELETE FROM tags")

    def test_pair_run_between_preview_and_apply_still_applies(self):
        pair_id, prompt = self.pair("busy", category="portrait")
        draft = self.preview()
        self.db.record_prompt_pair_use(pair_id)
        self.apply(draft)
        self.assertEqual(self.category(prompt), "portrait")

    def test_guard_failure_rolls_back_everything(self):
        _a, first = self.pair("first", category="portrait")
        _b, second = self.pair("second", category="portrait")
        draft = self.preview()
        real = repair._fill_categories

        def racing(conn, fills):
            # Another writer's value appears inside the transaction.
            conn.execute("UPDATE prompts SET category='x' WHERE id=?", (second,))
            return real(conn, fills)

        with patch.object(repair, "_fill_categories", side_effect=racing):
            with self.assertRaises(maintenance.PlanChanged):
                self.apply(draft)
        self.assertIsNone(self.category(first))
        self.assertIsNone(self.category(second))

    def test_more_than_two_hundred_fills_are_all_returned(self):
        for n in range(205):
            self.pair(f"bulk {n:03d}", category="portrait")
        result = self.apply(self.preview())
        self.assertEqual(result["changed"], 205)
        self.assertEqual(len(result["rows"]), 205)

    def test_batches_bind_few_ids(self):
        for n in range(5):
            self.pair(f"batch {n}", category="portrait", tags=["a"])
        with patch.object(maintenance, "ID_BATCH", 2):
            draft = self.preview()
            result = self.apply(draft)
        self.assertEqual(result["changed"], 5)


if __name__ == "__main__":
    unittest.main()
