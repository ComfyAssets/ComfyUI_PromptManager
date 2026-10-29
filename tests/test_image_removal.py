"""Removing one file's records from the library, and removal marks (#480)."""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.image_removal_schema import (
    image_removals_schema_error,
    migrate_image_removals,
)
from database.models import PromptModel
from database.operations import PromptDatabase


def _locator(path):
    return os.path.normcase(os.path.realpath(str(path)))


class _RemovalCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(assert_closed, self, self.temp.name)
        self.addCleanup(PromptModel.reset_schema_cache)
        base = Path(self.temp.name)
        self.root = base / "output"
        self.root.mkdir()
        self.alias = base / "alias"
        self.alias.symlink_to(self.root, target_is_directory=True)
        self.db = PromptDatabase(str(base / "prompts.db"))
        self.addCleanup(self.db.close_all)
        self.epoch = self.db.get_execution_epoch()

    def file(self, name, data=b"image-bytes"):
        path = self.root / name
        path.write_bytes(data)
        return path

    def removal(self, path):
        root_key = os.path.normcase(os.path.realpath(str(self.root)))
        return {
            "spellings": [str(path), str(self.alias / path.name)],
            "locator": _locator(path),
            "favorites": [(root_key, path.name.lower())],
            "category": (root_key, path.name.lower()),
        }

    def link(self, path, text="a prompt", spelled=None):
        prompt_id = self.db.save_prompt(text)
        image_id = self.db.link_image_to_prompt(str(prompt_id), str(spelled or path))
        self.assertTrue(image_id)
        return prompt_id, image_id

    def tag(self, path, tags):
        data = path.read_bytes()
        info = os.stat(path)
        return self.db.replace_image_tags(
            locator=_locator(path),
            sha256=hashlib.sha256(data).hexdigest(),
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            expected_epoch=self.epoch,
            expected_tag_revision=0,
            tags=tags,
        )

    def queue(self, path):
        data = path.read_bytes()
        info = os.stat(path)
        return self.db.record_review_item(
            locator=_locator(path),
            spelled_path=os.path.realpath(str(path)),
            sha256=hashlib.sha256(data).hexdigest(),
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            epoch=self.epoch,
            resolver_version=1,
            evidence={"candidates": []},
            candidate_count=0,
            limited=True,
        )

    def count(self, sql, *params):
        conn = sqlite3.connect(self.db.model.db_path)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def decorate(self, path):
        """Give *path* one record of every kind."""
        removal = self.removal(path)
        root_key, relative = removal["favorites"][0]
        self.tag(path, ["portrait"])
        self.db.set_image_favorite(root_key, relative)
        self.db.set_image_categories([removal["category"]], "keepers")
        self.db.add_to_collection(items=[(root_key, relative)], name="Set")
        return removal


class ImageRemovalRecordsTests(_RemovalCase):
    def test_counts_and_removes_every_record_under_every_spelling(self):
        path = self.file("a.png")
        other = self.file("b.png", b"other")
        orphan, _ = self.link(path, "only this image")
        shared, _ = self.link(path, "two images")
        self.db.link_image_to_prompt(str(shared), str(other))
        # A record under the symlinked folder spelling.
        self.link(path, "aliased", spelled=self.alias / "a.png")
        removal = self.decorate(path)
        self.assertTrue(self.queue(path))

        counted = self.db.count_removal_records([removal])
        self.assertEqual(
            counted["records"],
            {
                "prompt_links": 3,
                "pair_links": 0,
                "image_tags": 1,
                "auto_tagged": 0,
                "categories": 1,
                "collections": 1,
                "favorites": 1,
                "review_items": 1,
            },
        )
        self.assertEqual(counted["orphaned_prompts"]["count"], 2)

        removed = self.db.remove_image_records(removal)
        self.assertEqual(removed["prompt_links"], 3)
        self.assertEqual(removed["review_items"], 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 1)
        for table in (
            "media_image_tags",
            "media_annotation_files",
            "media_review_items",
            "image_favorites",
            "image_categories",
            "image_collection_items",
        ):
            with self.subTest(table=table):
                self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table}"), 0)
        # Prompts and the collection itself stay.
        self.assertEqual(self.count("SELECT COUNT(*) FROM prompts"), 3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM image_collections"), 1)
        self.assertTrue(path.exists())
        self.assertIsNotNone(self.db.get_prompt_by_id(orphan))

    def test_a_kept_review_item_keeps_the_annotation_rows(self):
        path = self.file("a.png")
        removal = self.decorate(path)
        self.assertTrue(self.queue(path))
        conn = sqlite3.connect(self.db.model.db_path)
        try:
            # A revised item is not settled; it still cites the bytes, whose
            # foreign key is ON DELETE RESTRICT.
            conn.execute("UPDATE media_review_items SET revision=1")
            conn.commit()
        finally:
            conn.close()
        removed = self.db.remove_image_records(removal)
        self.assertEqual(removed["review_items"], 0)
        self.assertEqual(removed["image_tags"], 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_image_tags"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_review_items"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_annotation_files"), 1)

    def test_mark_is_written_with_the_records_and_bumps_the_revision(self):
        path = self.file("a.png")
        self.link(path)
        before = self.db.removal_revision()
        info = os.stat(path)
        self.db.remove_image_records(
            self.removal(path), mark=(info.st_size, info.st_mtime_ns)
        )
        self.assertEqual(
            self.db.removal_marks(),
            {_locator(path): (info.st_size, info.st_mtime_ns)},
        )
        self.assertEqual(self.db.removal_revision(), before + 1)
        self.assertEqual(self.db.clear_removal_marks([_locator(path)]), 1)
        self.assertEqual(self.db.removal_marks(), {})
        self.assertEqual(self.db.removal_revision(), before + 2)
        self.assertEqual(self.db.clear_removal_marks([_locator(path)]), 0)

    def test_a_failure_rolls_back_everything(self):
        path = self.file("a.png")
        self.link(path)
        removal = self.decorate(path)
        broken = {**removal, "category": ("only-one-value",)}
        with self.assertRaises(sqlite3.Error):
            self.db.remove_image_records(broken, mark=(1, 1))
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM media_image_tags"), 1)
        self.assertEqual(self.db.removal_marks(), {})

    def test_parity_with_the_legacy_single_delete(self):
        path = self.file("a.png")
        _prompt, image_id = self.link(path)
        twin = PromptDatabase(str(Path(self.temp.name) / "twin.db"))
        try:
            twin_prompt = twin.save_prompt("a prompt")
            twin_image = twin.link_image_to_prompt(str(twin_prompt), str(path))
            self.assertTrue(twin.delete_image(twin_image))
            self.db.remove_image_records(self.removal(path))
            for table in (
                "generated_images",
                "image_prompt_pairs",
                "workflow_documents",
            ):
                with self.subTest(table=table):
                    conn = sqlite3.connect(twin.model.db_path)
                    try:
                        legacy = conn.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                    finally:
                        conn.close()
                    self.assertEqual(
                        self.count(f"SELECT COUNT(*) FROM {table}"), legacy
                    )
        finally:
            twin.close_all()
        self.assertIsNotNone(image_id)


class RemovalMarkGateTests(_RemovalCase):
    """A marked file is never linked again; any record write shows it again."""

    def hide(self, path):
        info = os.stat(path)
        self.db.remove_image_records(
            self.removal(path), mark=(info.st_size, info.st_mtime_ns)
        )

    def test_a_marked_file_is_not_linked_again(self):
        path = self.file("a.png")
        self.hide(path)
        prompt_id = self.db.save_prompt("scanned again")
        self.assertEqual(self.db.link_image_to_prompt(str(prompt_id), str(path)), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 0)
        self.assertIn(_locator(path), self.db.removal_marks())

    def test_a_changed_file_drops_its_stale_mark_and_links(self):
        path = self.file("a.png")
        self.hide(path)
        path.write_bytes(b"new bytes, new size")
        prompt_id = self.db.save_prompt("a new image at the same path")
        self.assertTrue(self.db.link_image_to_prompt(str(prompt_id), str(path)))
        self.assertEqual(self.db.removal_marks(), {})

    def test_explicit_record_writes_clear_the_mark(self):
        writers = {
            "favorite": lambda path, key: self.db.set_image_favorite(*key),
            "category": lambda path, key: self.db.set_image_categories([key], "c"),
            "collection": lambda path, key: self.db.add_to_collection(
                items=[key], name="Set"
            ),
            "tags": lambda path, key: self.tag(path, ["t"]),
        }
        for kind, write in writers.items():
            with self.subTest(kind=kind):
                path = self.file(f"{kind}.png", kind.encode())
                self.hide(path)
                self.assertIn(_locator(path), self.db.removal_marks())
                write(path, self.removal(path)["favorites"][0])
                self.assertNotIn(_locator(path), self.db.removal_marks())


class ImageRemovalSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_missing_is_allowed_only_for_old_backups(self):
        self.assertIsNone(image_removals_schema_error(self.conn))
        self.assertIsNotNone(
            image_removals_schema_error(self.conn, allow_missing=False)
        )

    def test_install_is_valid_and_idempotent(self):
        migrate_image_removals(self.conn)
        migrate_image_removals(self.conn)
        self.assertIsNone(image_removals_schema_error(self.conn, allow_missing=False))
        self.assertEqual(
            self.conn.execute("SELECT revision FROM image_removals_meta").fetchall(),
            [(0,)],
        )

    def test_invalid_values_and_objects_are_refused(self):
        migrate_image_removals(self.conn)
        self.conn.execute("INSERT INTO image_removals VALUES ('', 1, 1, 'now')")
        self.assertIn("invalid values", image_removals_schema_error(self.conn))
        self.conn.execute("DELETE FROM image_removals")
        self.conn.execute("CREATE INDEX extra ON image_removals(size)")
        self.assertIn("unsupported", image_removals_schema_error(self.conn))

    def test_a_foreign_table_definition_is_refused(self):
        self.conn.execute("CREATE TABLE image_removals (locator TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            migrate_image_removals(self.conn)


if __name__ == "__main__":
    unittest.main()
