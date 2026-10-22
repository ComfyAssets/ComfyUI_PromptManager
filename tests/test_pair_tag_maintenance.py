"""Global tag maintenance preserves pair and standalone tag relationships."""

import hashlib
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
        self.root = directory.name
        self.db = PromptDatabase(os.path.join(directory.name, "pair-tags.db"))
        self.addCleanup(self.db.close_all)

    def pair(self, positive: str, tags: list) -> int:
        return self.db.save_prompt_pair(positive, "blurry", tags=tags)

    def tags(self, pair_id: int) -> list:
        return self.db.get_prompt_pair(pair_id)["tags"]

    def image(self, name: str, tags: list, digest: str = "a", revision: int = 0):
        """Tag the bytes ``digest`` of the file ``name``; return its locator."""
        locator = os.path.normcase(os.path.join(os.path.realpath(self.root), name))
        replace = getattr(self.db, "replace_image_tags", None)
        self.assertTrue(callable(replace), "replace_image_tags is missing")
        replace(
            locator=locator,
            sha256=hashlib.sha256(digest.encode()).hexdigest(),
            size=1,
            mtime_ns=1,
            expected_epoch=self.db.get_execution_epoch(),
            expected_tag_revision=revision,
            tags=tags,
        )
        return locator

    def image_tags(self, locator: str, digest: str = "a") -> list:
        return self.db.read_image_tags(
            locator, hashlib.sha256(digest.encode()).hexdigest()
        )["tags"]

    def image_rows(self) -> list:
        conn = self.db.model.get_connection()
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT byte_id, tag_id, position FROM media_image_tags ORDER BY 1,3"
            )
        ]

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

    def test_pair_junction_failure_rolls_back_native_rename(self):
        pair = self.pair("portrait", ["source", "target"])
        other = self.pair("landscape", ["source"])
        prompt = self.db.save_prompt("standalone", tags=["source"])
        conn = self.db.model.get_connection()
        before = [
            conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in ("tags", "prompt_tags", "prompt_pair_tags")
        ]
        source = conn.execute("SELECT id FROM tags WHERE name='source'").fetchone()[0]
        conn.execute(
            "CREATE TRIGGER reject_pair_tag_change BEFORE UPDATE "
            "ON prompt_pair_tags BEGIN SELECT RAISE(ABORT,'pair tag failure'); END"
        )
        conn.commit()
        rename = getattr(self.db, "rename_vocabulary_tag", None)
        self.assertTrue(callable(rename), "rename_vocabulary_tag is missing")
        with self.assertRaises(sqlite3.IntegrityError):
            rename(
                source,
                expected_epoch=self.db.get_execution_epoch(),
                name="source",
                new_name="target",
            )
        self.assertFalse(conn.in_transaction)
        after = [
            conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in ("tags", "prompt_tags", "prompt_pair_tags")
        ]
        self.assertEqual(after, before)
        self.assertEqual(self.tags(pair), ["source", "target"])
        self.assertEqual(self.tags(other), ["source"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["source"])

    def test_rename_and_merge_move_image_links_with_dedup(self):
        pair = self.pair("portrait", ["old", "new"])
        both = self.image("both.png", ["new", "old", "keep"])
        only_old = self.image("old.png", ["old"])
        result = self.db.rename_tag_all_prompts("old", "new")
        self.assertEqual(result["affected_count"], 0)  # prompts only, as before
        self.assertEqual(self.tags(pair), ["new"])
        self.assertEqual(self.image_tags(both), ["new", "keep"])
        self.assertEqual(self.image_tags(only_old), ["new"])
        self.assertNotIn("old", self.db.get_all_tags())
        self.db.rename_tag_all_prompts("keep", "kept")
        self.assertEqual(self.image_tags(both), ["new", "kept"])
        merged = self.db.merge_tags(["kept", "new"], "final")
        self.assertEqual(merged["tags_merged"], 2)
        self.assertEqual(merged["affected_count"], 0)
        self.assertEqual(self.image_tags(both), ["final"])
        self.assertEqual(self.image_tags(only_old), ["final"])
        self.assertEqual(self.tags(pair), ["final"])
        epoch = self.db.get_execution_epoch()
        conn = self.db.model.get_connection()
        final = conn.execute("SELECT id FROM tags WHERE name='final'").fetchone()[0]
        native = self.db.rename_vocabulary_tag(
            final, expected_epoch=epoch, name="final", new_name="native"
        )
        self.assertEqual(native["images_changed"], 2)
        self.assertEqual(self.image_tags(both), ["native"])
        self.assertEqual(self.image_tags(only_old), ["native"])
        self.image("again.png", ["native", "merge-me"])
        source = conn.execute("SELECT id FROM tags WHERE name='merge-me'").fetchone()
        receipt = self.db.rename_vocabulary_tag(
            source[0], expected_epoch=epoch, name="merge-me", new_name="native"
        )
        self.assertTrue(receipt["merged"])
        self.assertEqual(receipt["images_changed"], 1)
        self.assertEqual(receipt["tag"]["image_count"], 3)
        self.assertEqual(len(self.image_rows()), 3)

    def test_delete_cascades_image_links_and_keeps_legacy_counts(self):
        pair = self.pair("portrait", ["remove", "keep"])
        prompt = self.db.save_prompt("standalone", tags=["remove", "keep"])
        locator = self.image("a.png", ["keep", "remove"])
        result = self.db.delete_tag_all_prompts("remove")
        self.assertEqual(result["affected_count"], 1)
        self.assertEqual(self.tags(pair), ["keep"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["keep"])
        self.assertEqual(self.image_tags(locator), ["keep"])
        self.assertEqual(len(self.image_rows()), 1)
        epoch = self.db.get_execution_epoch()
        conn = self.db.model.get_connection()
        keep = conn.execute("SELECT id FROM tags WHERE name='keep'").fetchone()[0]
        impact = self.db.vocabulary_tag_impact(keep)["impact"]
        self.assertEqual(impact["images"], 1)
        deleted = self.db.delete_vocabulary_tag(keep, expected_epoch=epoch, name="keep")
        self.assertEqual(
            (
                deleted["components_changed"],
                deleted["pairs_changed"],
                deleted["images_changed"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(self.image_tags(locator), [])
        self.assertEqual(self.image_rows(), [])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM media_annotation_bytes").fetchone()[0], 1
        )

    def test_image_only_and_prior_only_names_participate(self):
        only = self.image("only.png", ["image-only"])
        prior = self.image("prior.png", ["prior-only"], digest="old")
        self.image("prior.png", [], digest="new")  # the file's bytes changed
        self.db.save_prompt("standalone", tags=["target"])
        merged = self.db.merge_tags(["image-only"], "target")
        self.assertEqual((merged["tags_merged"], merged["affected_count"]), (1, 0))
        self.assertEqual(self.image_tags(only), ["target"])
        self.assertEqual(
            self.db.rename_tag_all_prompts("prior-only", "renamed")["affected_count"], 0
        )
        self.assertEqual(self.image_tags(prior, "old"), ["renamed"])
        self.assertEqual(self.image_tags(prior, "new"), [])
        self.assertEqual(set(self.db.get_all_tags()), {"target", "renamed"})
        epoch = self.db.get_execution_epoch()
        conn = self.db.model.get_connection()
        renamed = conn.execute("SELECT id FROM tags WHERE name='renamed'").fetchone()[0]
        receipt = self.db.rename_vocabulary_tag(
            renamed, expected_epoch=epoch, name="renamed", new_name="target"
        )
        self.assertTrue(receipt["merged"])
        self.assertEqual(receipt["images_changed"], 1)
        self.assertEqual(self.image_tags(prior, "old"), ["target"])
        self.assertEqual(receipt["tag"]["image_count"], 2)

    def test_image_junction_failure_rolls_back_prompt_pair_and_name_changes(self):
        pair = self.pair("portrait", ["source", "target"])
        prompt = self.db.save_prompt("standalone", tags=["source"])
        locator = self.image("a.png", ["source"])
        conn = self.db.model.get_connection()
        tables = ("tags", "prompt_tags", "prompt_pair_tags", "media_image_tags")
        before = [
            conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in tables
        ]
        source = conn.execute("SELECT id FROM tags WHERE name='source'").fetchone()[0]
        conn.execute(
            "CREATE TRIGGER reject_image_tag_change BEFORE UPDATE "
            "ON media_image_tags BEGIN SELECT RAISE(ABORT,'image tag failure'); END"
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.rename_vocabulary_tag(
                source,
                expected_epoch=self.db.get_execution_epoch(),
                name="source",
                new_name="target",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.merge_tags(["source"], "target")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.rename_tag_all_prompts("source", "target")
        self.assertFalse(conn.in_transaction)
        after = [
            conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in tables
        ]
        self.assertEqual(after, before)
        self.assertEqual(self.tags(pair), ["source", "target"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["source"])
        self.assertEqual(self.image_tags(locator), ["source"])


if __name__ == "__main__":
    unittest.main()
