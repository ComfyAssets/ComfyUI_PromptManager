"""Reusable pairs own their metadata without changing shared prompt content."""

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestPromptPairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.db = PromptDatabase(os.path.join(self.tmp.name, "pairs.db"))
        self.addCleanup(self.db.close_all)

    def save(self, positive="a cat", negative="blurry", **kwargs):
        return self.db.save_prompt_pair(positive, negative, **kwargs)

    def test_pair_dedup_is_ordered_and_empty_negative_is_explicit(self):
        first = self.save()
        self.assertEqual(first, self.save(" A CAT ", " BLURRY "))
        self.assertNotEqual(first, self.save("blurry", "a cat"))
        self.assertNotEqual(first, self.save("a cat", "watermark"))
        empty = self.save("a cat", "")
        self.assertEqual(empty, self.save("a cat", "  "))
        self.assertIsNone(self.db.get_prompt_pair(empty)["negative_prompt_id"])
        self.assertEqual(self.db.get_prompt_pair(empty)["negative_text"], "")
        self.assertNotEqual(empty, first)

    def test_same_text_can_serve_both_roles_and_existing_metadata_survives(self):
        prompt = self.db.save_prompt(
            "a cat",
            category="original",
            tags=["old"],
            prompt_hash=generate_prompt_hash("a cat"),
        )
        pair = self.db.get_prompt_pair(
            self.save("a cat", "a cat", category="pair", tags=["new"])
        )
        self.assertEqual(pair["positive_prompt_id"], prompt)
        self.assertEqual(pair["negative_prompt_id"], prompt)
        self.assertEqual(pair["tags"], ["new"])
        self.assertEqual(self.db.get_prompt_by_id(prompt)["category"], "original")
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["old"])

    def test_repeated_save_preserves_pair_metadata_and_explicit_update_changes_it(self):
        pid = self.save(
            title="Title",
            category="Portrait",
            tags=["studio", "studio"],
            rating=4,
            notes="keep",
        )
        self.assertEqual(pid, self.save(title="overwrite", tags=["bad"], rating=1))
        pair = self.db.get_prompt_pair(pid)
        self.assertEqual(
            (
                pair["title"],
                pair["category"],
                pair["tags"],
                pair["rating"],
                pair["notes"],
            ),
            ("Title", "Portrait", ["studio"], 4, "keep"),
        )
        self.assertTrue(
            self.db.update_prompt_pair_metadata(
                pid, title="", category="", tags=[], notes=""
            )
        )
        pair = self.db.get_prompt_pair(pid)
        self.assertEqual(
            (pair["title"], pair["category"], pair["tags"], pair["notes"]),
            ("", "", [], ""),
        )
        self.assertEqual(pair["rating"], 4)
        self.assertFalse(self.db.update_prompt_pair_metadata(999999, title="missing"))
        self.assertFalse(self.db.update_prompt_pair_metadata(pid))

    def test_list_filters_pagination_archive_and_pair_usage_are_independent(self):
        first = self.save(category="Animals", tags=["studio", "cat"])
        second = self.save("a dog", "blur", category="Animals", tags=["studio"])
        other = self.save("sky", "", category="Outdoor")
        result = self.db.list_prompt_pairs(category="Animals", tags=["studio"], limit=1)
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["has_more"])
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(
            self.db.list_prompt_pairs(text="dog")["pairs"][0]["id"], second
        )
        self.assertEqual(self.db.list_prompt_pairs(tags=["studio", "cat"])["total"], 1)
        self.assertTrue(self.db.archive_prompt_pair(first))
        self.assertEqual(self.db.list_prompt_pairs()["total"], 2)
        self.assertEqual(self.db.list_prompt_pairs(include_archived=True)["total"], 3)
        self.assertTrue(self.db.get_prompt_pair(first)["archived"])
        self.assertTrue(self.db.archive_prompt_pair(first, False))
        self.assertTrue(self.db.record_prompt_pair_use(other, 2))
        pair = self.db.get_prompt_pair(other)
        self.assertEqual(pair["run_count"], 2)
        self.assertIsNotNone(pair["last_used_at"])
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 0
        )

    def test_failure_during_tag_save_rolls_back_pair_and_components(self):
        with patch.object(
            self.db, "_ensure_tags", side_effect=sqlite3.OperationalError("injected")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.save(tags=["studio"])
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(self.db.search_prompts(), [])

    def test_concurrent_same_pair_returns_one_id_and_preserves_first_metadata(self):
        first = self.save(title="original")

        def save(index):
            try:
                return self.save(title=f"writer {index}")
            finally:
                self.db.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            ids = list(executor.map(save, range(24)))
        self.assertEqual(set(ids), {first})
        self.assertEqual(self.db.get_prompt_pair(first)["title"], "original")
        self.assertEqual(self.db.list_prompt_pairs()["total"], 1)

    def test_concurrent_empty_negative_pair_creation_is_atomic(self):
        def save(_):
            try:
                return self.save(negative="")
            finally:
                self.db.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            ids = list(executor.map(save, range(12)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.db.search_prompts()), 1)

    def test_component_edit_and_all_delete_paths_reject_dependencies_atomically(self):
        from database.operations import PairDependencyError

        pair = self.db.get_prompt_pair(self.save())
        positive = pair["positive_prompt_id"]
        image = self.db.link_image_to_prompt(
            positive, os.path.join(self.tmp.name, "keep.png")
        )
        unused = self.db.save_prompt(
            "unused", category="remove", prompt_hash=generate_prompt_hash("unused")
        )
        self.db.update_prompt_metadata(positive, category="remove")
        for action in (
            lambda: self.db.update_prompt_text(positive, "replacement"),
            lambda: self.db.delete_prompt(positive),
            lambda: self.db.bulk_delete_prompts([unused, positive]),
            lambda: self.db.delete_prompts_by_category("remove"),
        ):
            with self.subTest(action=action):
                with self.assertRaises(PairDependencyError):
                    action()
                self.assertIsNotNone(self.db.get_prompt_by_id(unused))
                self.assertEqual(self.db.get_prompt_by_id(positive)["text"], "a cat")
                self.assertIsNotNone(self.db.get_image_by_id(image))
        self.assertTrue(self.db.delete_prompt_pair(pair["id"]))
        self.assertTrue(self.db.update_prompt_text(positive, "replacement"))

    def test_prune_skips_members_even_in_archived_pairs_and_preserves_other_behavior(
        self,
    ):
        pair = self.db.get_prompt_pair(self.save())
        self.db.archive_prompt_pair(pair["id"])
        unused = self.db.save_prompt(
            "unused", prompt_hash=generate_prompt_hash("unused")
        )
        self.assertEqual(self.db.prune_orphaned_prompts(), 1)
        self.assertIsNone(self.db.get_prompt_by_id(unused))
        self.assertIsNotNone(self.db.get_prompt_by_id(pair["negative_prompt_id"]))

    def test_duplicate_cleanup_skips_referenced_group_without_losing_pairs(self):
        pair = self.db.get_prompt_pair(self.save())
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(text,category) VALUES(' A CAT ', 'duplicate')"
            )
        self.assertEqual(self.db.cleanup_duplicates(), 0)
        self.assertEqual(self.db.get_prompt_pair(pair["id"])["positive_text"], "a cat")
        self.assertEqual(len(self.db.search_prompts()), 3)

    def test_image_association_preserves_provenance_and_pair_deletion_requires_archive(
        self,
    ):
        from database.operations import PairDependencyError

        pid = self.save()
        pair = self.db.get_prompt_pair(pid)
        image = self.db.link_image_to_prompt(
            pair["positive_prompt_id"], os.path.join(self.tmp.name, "render.png")
        )
        provenance = {
            "run_id": "abc",
            "negative_text": "actual expanded negative",
            "positive_text": "trigger a cat",
        }
        association = self.db.link_image_to_pair(pid, image, provenance)
        self.assertEqual(
            association,
            self.db.link_image_to_pair(
                pid, image, dict(reversed(list(provenance.items())))
            ),
        )
        self.assertEqual(
            self.db.get_image_prompt_pairs(image)[0]["provenance"], provenance
        )
        self.assertEqual(self.db.get_prompt_pair(pid)["image_count"], 1)
        with self.assertRaises(PairDependencyError):
            self.db.delete_prompt_pair(pid)
        self.assertTrue(self.db.archive_prompt_pair(pid))
        with self.assertRaises(ValueError):
            self.db.link_image_to_pair(99999, image, provenance)
        self.assertTrue(self.db.delete_image(image))
        self.assertEqual(self.db.get_image_prompt_pairs(image), [])
        self.assertTrue(self.db.delete_prompt_pair(pid))
        self.assertIsNotNone(self.db.get_prompt_by_id(pair["positive_prompt_id"]))

    def test_pair_validates_values_before_writing(self):
        for positive, negative, kwargs in (
            ("", "blur", {}),
            ("cat", None, {}),
            ("cat", "blur", {"rating": 6}),
            ("cat", "blur", {"tags": "one"}),
        ):
            with self.subTest(positive=positive, negative=negative, kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    self.save(positive, negative, **kwargs)
        self.assertEqual(self.db.search_prompts(), [])
        for kwargs in ({"limit": 0}, {"offset": -1}, {"limit": True}):
            with self.assertRaises(ValueError):
                self.db.list_prompt_pairs(**kwargs)
        self.assertIsNone(self.db.get_prompt_pair(99999))
        self.assertFalse(self.db.delete_prompt_pair(99999))

    def test_connection_helpers_leave_commit_and_rollback_to_the_caller(self):
        conn = self.db.model.get_connection()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self.db._save_pair(conn, "transient", "temporary", tags=["tag"])
                with self.assertRaisesRegex(RuntimeError, "own transaction"):
                    self.save()
                raise RuntimeError("rollback")
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(self.db.search_prompts(), [])

    def test_metadata_update_failure_rolls_back_prior_fields_and_tags(self):
        pid = self.save(title="keep", tags=["old"])
        with patch.object(
            self.db, "_ensure_tags", side_effect=sqlite3.OperationalError("injected")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.update_prompt_pair_metadata(pid, title="replace", tags=["new"])
        pair = self.db.get_prompt_pair(pid)
        self.assertEqual(pair["title"], "keep")
        self.assertEqual(pair["tags"], ["old"])

    def test_invalid_associations_metadata_and_missing_mutations_are_safe(self):
        pid = self.save()
        other = self.db.save_prompt("other", prompt_hash=generate_prompt_hash("other"))
        image = self.db.link_image_to_prompt(
            other, os.path.join(self.tmp.name, "other.png")
        )
        for provenance in ([], {"number": float("nan")}, {"object": object()}):
            with self.assertRaises(ValueError):
                self.db.link_image_to_pair(pid, image, provenance)
        with self.assertRaisesRegex(ValueError, "positive prompt"):
            self.db.link_image_to_pair(pid, image, {})
        self.assertEqual(self.db.get_image_prompt_pairs(image), [])
        self.assertFalse(self.db.archive_prompt_pair(999999))
        self.assertFalse(self.db.record_prompt_pair_use(999999))
        for times in (0, True):
            with self.assertRaises(ValueError):
                self.db.record_prompt_pair_use(pid, times)
        with self.assertRaises(ValueError):
            self.db.archive_prompt_pair(pid, "yes")
        with self.assertRaises(ValueError):
            self.db.update_prompt_pair_metadata(pid, title=[])
        with self.assertRaises(ValueError):
            self.db.list_prompt_pairs(text=[])
