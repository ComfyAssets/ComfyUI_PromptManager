"""Global tag maintenance preserves pair and standalone tag relationships."""

import os
import sqlite3
import tempfile
import unittest

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase


class TestPairTagMaintenance(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(assert_closed, self, directory.name)
        self.db = PromptDatabase(os.path.join(directory.name, "pair-tags.db"))
        self.addCleanup(self.db.close_all)

    def pair(self, positive: str, tags: list) -> int:
        return self.db.save_prompt_pair(positive, "blurry", tags=tags)

    def tags(self, pair_id: int) -> list:
        return self.db.get_prompt_pair(pair_id)["tags"]

    def test_rename_to_existing_tag_preserves_pair_links_and_deduplicates(self):
        first = self.pair("portrait", ["old", "new", "keep"])
        second = self.pair("landscape", ["old"])
        third = self.pair("studio", ["unrelated"])
        prompt = self.db.save_prompt("standalone", tags=["old", "new"])
        result = self.db.rename_tag_all_prompts("old", "new")
        self.assertEqual(result["affected_count"], 1)
        self.assertEqual(self.tags(first), ["keep", "new"])
        self.assertEqual(self.tags(second), ["new"])
        self.assertEqual(self.tags(third), ["unrelated"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["new"])
        self.assertEqual(self.db.list_prompt_pairs(tags=["new"])["total"], 2)
        self.assertNotIn("old", self.db.get_all_tags())

    def test_rename_pair_only_tag_to_new_name_preserves_links(self):
        pair = self.pair("portrait", ["old"])
        self.db.rename_tag_all_prompts("old", "new")
        self.assertEqual(self.tags(pair), ["new"])

    def test_merge_pair_only_source_preserves_its_links(self):
        pair = self.pair("portrait", ["source"])
        other = self.pair("landscape", ["target"])
        self.db.merge_tags(["source"], "target")
        self.assertEqual(self.tags(pair), ["target"])
        self.assertEqual(self.tags(other), ["target"])
        self.assertEqual(self.db.list_prompt_pairs(tags=["target"])["total"], 2)

    def test_merge_multiple_sources_preserves_shared_and_unrelated_tags(self):
        pair = self.pair("portrait", ["first", "second", "target", "keep"])
        other = self.pair("landscape", ["second"])
        prompt = self.db.save_prompt("standalone", tags=["first", "second"])
        self.db.merge_tags(["first", "second"], "target")
        self.assertEqual(self.tags(pair), ["keep", "target"])
        self.assertEqual(self.tags(other), ["target"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["target"])
        self.assertEqual(self.db.list_prompt_pairs(tags=["target"])["total"], 2)

    def test_self_rename_does_not_remove_tag_or_relationships(self):
        pair = self.pair("portrait", ["same"])
        prompt = self.db.save_prompt("standalone", tags=["same"])
        self.db.rename_tag_all_prompts("same", "same")
        self.assertEqual(self.tags(pair), ["same"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["same"])

    def test_merge_skips_target_even_when_it_is_in_source_list(self):
        pair = self.pair("portrait", ["target", "source"])
        prompt = self.db.save_prompt("standalone", tags=["target", "source"])
        self.db.merge_tags(["target", "source", "target"], "target")
        self.assertEqual(self.tags(pair), ["target"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["target"])
        self.assertIn("target", self.db.get_all_tags())

    def test_explicit_global_delete_removes_pair_tags_and_keeps_other_tags(self):
        pair = self.pair("portrait", ["remove", "keep"])
        prompt = self.db.save_prompt("standalone", tags=["remove", "keep"])
        self.db.delete_tag_all_prompts("remove")
        self.assertEqual(self.tags(pair), ["keep"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["keep"])
        self.assertIsNotNone(self.db.get_prompt_pair(pair))

    def test_pair_link_failure_rolls_back_standalone_merge_and_source_delete(self):
        pair = self.pair("portrait", ["source"])
        prompt = self.db.save_prompt("standalone", tags=["source"])
        conn = self.db.model.get_connection()
        conn.execute(
            "CREATE TRIGGER reject_pair_tag_change BEFORE UPDATE "
            "ON prompt_pair_tags BEGIN SELECT RAISE(ABORT,'pair tag failure'); END"
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.merge_tags(["source"], "target")
        self.assertEqual(self.tags(pair), ["source"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["source"])
        self.assertNotIn("target", self.db.get_all_tags())


if __name__ == "__main__":
    unittest.main()
