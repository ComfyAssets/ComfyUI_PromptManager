"""Per-image tag sets (#249 A2): byte-exact storage in the shared vocabulary."""

import hashlib
import os
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.annotation_schema import annotation_schema_error
from database.library_commands import LibraryCommandError
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash

try:  # guarded so a missing module fails assertions, not test loading
    from database import annotations as annotations_module
    from database.annotations import (
        MAX_IMAGE_TAGS,
        AnnotationCommandError,
        normalize_image_tags,
    )
except ImportError:
    annotations_module = None
    MAX_IMAGE_TAGS = None
    AnnotationCommandError = None
    normalize_image_tags = None

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
DIGEST_A = hashlib.sha256(b"bytes a").hexdigest()
DIGEST_B = hashlib.sha256(b"bytes b").hexdigest()
SIZE = 7
MTIME = 1_700_000_000_123_456_789
ANNOTATION_TABLES = (
    "media_annotation_files",
    "media_annotation_bytes",
    "media_image_tags",
    "media_annotation_metadata",
)
LEGACY_TABLES = (
    "tags",
    "prompt_tags",
    "prompt_pair_tags",
    "generated_images",
    "image_prompt_pairs",
)


class AnnotationTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            annotations_module, "database.annotations is not implemented"
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(assert_closed, self, directory.name)
        self.root = directory.name
        self.db = PromptDatabase(os.path.join(self.root, "annotations.db"))
        self.addCleanup(self.db.close_all)
        self.epoch = self.db.get_execution_epoch()

    def locator(self, name: str) -> str:
        return os.path.normcase(os.path.join(os.path.realpath(self.root), name))

    def replace(self, loc, digest=DIGEST_A, tags=(), *, revision=0, **over):
        options = {
            "locator": loc,
            "sha256": digest,
            "size": SIZE,
            "mtime_ns": MTIME,
            "expected_epoch": self.epoch,
            "expected_tag_revision": revision,
            "tags": list(tags) if isinstance(tags, tuple) else tags,
        }
        options.update(over)
        return self.db.replace_image_tags(**options)

    def add(self, loc, digest=DIGEST_A, tags=(), **over):
        options = {
            "locator": loc,
            "sha256": digest,
            "size": SIZE,
            "mtime_ns": MTIME,
            "expected_epoch": self.epoch,
            "tags": list(tags) if isinstance(tags, tuple) else tags,
        }
        options.update(over)
        return self.db.add_image_tags(**options)

    def read(self, loc, digest=DIGEST_A):
        return self.db.read_image_tags(loc, digest)

    def rows(self, *tables):
        """Every row of each table; ``verified_at`` is a fingerprint, not content."""
        conn = self.db.model.get_connection()
        result = []
        for table in tables:
            columns = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})")
                if row[1] != "verified_at"
            ]
            result.append(
                [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT {','.join(columns)} FROM {table} ORDER BY 1,2"
                    )
                ]
            )
        return result

    def counts(self):
        conn = self.db.model.get_connection()
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ANNOTATION_TABLES
        }

    def index_revision(self):
        return (
            self.db.model.get_connection()
            .execute("SELECT image_index_revision FROM media_annotation_metadata")
            .fetchone()[0]
        )

    def schema_clean(self):
        self.assertIsNone(
            annotation_schema_error(self.db.model.get_connection(), allow_missing=False)
        )


class NormalizeImageTagsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            normalize_image_tags, "database.annotations is not implemented"
        )

    def test_case_insensitive_dedupe_keeps_first_spelling_and_position(self):
        self.assertEqual(
            normalize_image_tags([" Forest ", "mist", "forest", "MIST", "Rain"]),
            ["Forest", "mist", "Rain"],
        )
        self.assertEqual(normalize_image_tags([]), [])
        self.assertEqual(MAX_IMAGE_TAGS, 100)

    def test_limit_and_invalid_names_rejected(self):
        for bad in (
            None,
            "forest",
            {"a": 1},
            [1],
            [None],
            ["a,b"],
            [""],
            ["   "],
            ["x" * 101],
            [f"t{i}" for i in range(MAX_IMAGE_TAGS + 1)],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_image_tags(bad)
        # Duplicates fold before the cap is applied.
        many = [f"t{i}" for i in range(MAX_IMAGE_TAGS)]
        self.assertEqual(normalize_image_tags(many + ["T0", "T99"]), many)


class ReplaceImageTagsTests(AnnotationTestCase):
    def test_first_save_creates_owner_and_byte_atomically_including_empty_set(self):
        loc = self.locator("a.png")
        receipt = self.replace(loc, tags=["Forest", "mist", "forest"])
        self.assertEqual(receipt["tags"], ["Forest", "mist"])
        self.assertEqual(receipt["tag_revision"], 1)
        self.assertEqual(
            receipt,
            {
                "library_epoch": self.epoch,
                "sha256": DIGEST_A,
                "tags": ["Forest", "mist"],
                "tag_revision": 1,
                "replayed": False,
            },
        )
        conn = self.db.model.get_connection()
        files = conn.execute("SELECT * FROM media_annotation_files").fetchall()
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0]["id"], UUID)
        self.assertEqual(files[0]["locator"], loc)
        byte = conn.execute("SELECT * FROM media_annotation_bytes").fetchone()
        self.assertRegex(byte["id"], UUID)
        self.assertEqual(
            (byte["file_id"], byte["sha256"], byte["size"], byte["mtime_ns"]),
            (files[0]["id"], DIGEST_A, SIZE, MTIME),
        )
        self.assertEqual(byte["tag_revision"], 1)
        self.assertEqual(byte["verified_at"], byte["first_seen_at"])
        self.assertEqual(
            [
                tuple(row)
                for row in conn.execute(
                    "SELECT t.name, mit.position FROM media_image_tags mit "
                    "JOIN tags t ON t.id = mit.tag_id ORDER BY mit.position"
                )
            ],
            [("Forest", 0), ("mist", 1)],
        )
        self.assertEqual(self.index_revision(), 1)
        # An explicit empty set is a real save: owner and byte record exist.
        empty = self.replace(self.locator("b.png"), DIGEST_B, [])
        self.assertEqual((empty["tags"], empty["tag_revision"]), ([], 1))
        self.assertEqual(self.counts()["media_annotation_files"], 2)
        self.assertEqual(self.counts()["media_annotation_bytes"], 2)
        self.assertEqual(
            self.db.read_image_tags(self.locator("b.png"), DIGEST_B),
            {"tags": [], "tag_revision": 1, "prior_count": 0},
        )
        self.assertEqual(self.index_revision(), 2)
        self.schema_clean()

    def test_save_changes_only_target_byte_set(self):
        a, b, c = self.locator("a.png"), self.locator("b.png"), self.locator("c.png")
        prompt = self.db.save_prompt(
            "Shared forest",
            tags=["shared"],
            prompt_hash=generate_prompt_hash("Shared forest"),
        )
        pair = self.db.save_prompt_pair("Shared forest", "blurry", tags=["pairtag"])
        image_a = self.db.link_image_to_prompt(prompt, a)
        image_b = self.db.link_image_to_prompt(prompt, b)
        self.db.link_image_to_pair(pair, image_a, {"seed": 1})
        self.db.link_image_to_pair(pair, image_b, {"seed": 2})
        self.replace(b, DIGEST_A, ["sibling"])
        self.replace(c, DIGEST_A, ["copy"])  # equal bytes at another path
        before = self.rows(*LEGACY_TABLES)
        sibling_before = self.db.read_image_tags(b, DIGEST_A)
        copy_before = self.db.read_image_tags(c, DIGEST_A)

        receipt = self.replace(a, DIGEST_A, ["only a"])

        self.assertEqual((receipt["tags"], receipt["tag_revision"]), (["only a"], 1))
        after = self.rows(*LEGACY_TABLES)
        # The registry gained the new name; nothing else moved.
        self.assertEqual(after[1:], before[1:])
        self.assertEqual([row for row in after[0] if row[1] != "only a"], before[0])
        self.assertEqual(self.db.read_image_tags(b, DIGEST_A), sibling_before)
        self.assertEqual(self.db.read_image_tags(c, DIGEST_A), copy_before)
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["shared"])
        self.assertEqual(self.db.get_prompt_pair(pair)["tags"], ["pairtag"])

    def test_changed_bytes_at_same_locator_start_empty_and_keep_prior(self):
        loc = self.locator("a.png")
        self.replace(loc, DIGEST_A, ["old"])
        self.assertEqual(
            self.db.read_image_tags(loc, DIGEST_B),
            {"tags": [], "tag_revision": 0, "prior_count": 1},
        )
        receipt = self.replace(loc, DIGEST_B, ["new"], mtime_ns=MTIME + 5)
        self.assertEqual((receipt["tags"], receipt["tag_revision"]), (["new"], 1))
        self.assertEqual(
            self.db.read_image_tags(loc, DIGEST_A),
            {"tags": ["old"], "tag_revision": 1, "prior_count": 1},
        )
        self.assertEqual(
            self.db.read_image_tags(loc, DIGEST_B),
            {"tags": ["new"], "tag_revision": 1, "prior_count": 1},
        )
        self.assertEqual(self.counts()["media_annotation_files"], 1)
        self.assertEqual(self.counts()["media_annotation_bytes"], 2)
        self.assertEqual(
            self.db.read_image_tags(self.locator("unseen.png"), DIGEST_A),
            {"tags": [], "tag_revision": 0, "prior_count": 0},
        )

    def test_revision_conflict_returns_current_set(self):
        loc = self.locator("a.png")
        self.replace(loc, tags=["a"])
        before = self.rows(*ANNOTATION_TABLES, "tags")
        with self.assertRaises(AnnotationCommandError) as ctx:
            self.replace(loc, tags=["b"], revision=0)
        self.assertEqual(
            (ctx.exception.code, ctx.exception.current["tag_revision"]),
            ("tag-conflict", 1),
        )
        self.assertIsInstance(ctx.exception, LibraryCommandError)
        self.assertEqual(ctx.exception.epoch, self.epoch)
        self.assertEqual(ctx.exception.current["tags"], ["a"])
        self.assertEqual(ctx.exception.current["sha256"], DIGEST_A)
        with self.assertRaises(AnnotationCommandError) as ahead:
            self.replace(loc, tags=["a"], revision=5)
        self.assertEqual(ahead.exception.code, "tag-conflict")
        self.assertEqual(self.rows(*ANNOTATION_TABLES, "tags"), before)
        self.assertFalse(self.db.model.get_connection().in_transaction)

    def test_identical_replay_after_commit_is_success_replayed(self):
        loc = self.locator("a.png")
        first = self.replace(loc, tags=["a", "b"])
        self.assertEqual((first["tag_revision"], first["replayed"]), (1, False))
        before = self.rows(*ANNOTATION_TABLES)
        # The lost-response retry: same set, the revision it was sent against.
        retry = self.replace(loc, tags=["a", "b"], revision=0)
        self.assertEqual(retry, {**first, "replayed": True})
        # Saving the current set at the current revision writes nothing either.
        again = self.replace(loc, tags=["a", "b"], revision=1)
        self.assertEqual(again, {**first, "replayed": True})
        self.assertEqual(self.rows(*ANNOTATION_TABLES), before)
        # Order is part of the set.
        reordered = self.replace(loc, tags=["b", "a"], revision=1)
        self.assertEqual(
            (reordered["tags"], reordered["tag_revision"], reordered["replayed"]),
            (["b", "a"], 2, False),
        )
        self.assertEqual(self.index_revision(), 2)

    def test_replay_with_new_fingerprint_moves_only_the_index_revision(self):
        loc = self.locator("a.png")
        first = self.replace(loc, tags=["a"])
        index = self.index_revision()
        same = self.replace(loc, tags=["a"], revision=1)
        self.assertEqual(same, {**first, "replayed": True})
        self.assertEqual(self.index_revision(), index)  # identical: nothing moved
        touched = self.replace(loc, tags=["a"], revision=1, mtime_ns=MTIME + 5)
        self.assertEqual(touched, {**first, "replayed": True})
        self.assertEqual(self.index_revision(), index + 1)  # currency changed
        self.assertEqual(
            self.db.current_image_tags({loc: (SIZE, MTIME + 5)}), {loc: ["a"]}
        )
        self.assertEqual(self.db.current_image_tags({loc: (SIZE, MTIME)}), {})
        self.assertEqual(self.read(loc)["tag_revision"], 1)

    def test_epoch_checked_inside_transaction(self):
        loc = self.locator("a.png")
        seen = []
        original = PromptDatabase._library_epoch

        def spy(db, conn, expected_epoch):
            seen.append(conn.in_transaction)
            return original(db, conn, expected_epoch)

        with patch.object(PromptDatabase, "_library_epoch", spy):
            self.replace(loc, tags=["a"])
            self.add(loc, tags=["b"])
        self.assertEqual(seen, [True, True])
        stale = "11111111-2222-4333-8444-555555555555"
        for call in (
            lambda: self.replace(
                self.locator("x.png"), tags=["a"], expected_epoch=stale
            ),
            lambda: self.add(self.locator("x.png"), tags=["a"], expected_epoch=stale),
        ):
            with self.assertRaises(LibraryCommandError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "storage-changed")
            self.assertEqual(ctx.exception.epoch, self.epoch)
        self.assertEqual(self.counts()["media_annotation_files"], 1)
        for bad_epoch in (None, 7, "not-a-uuid"):
            with self.assertRaises(ValueError):
                self.replace(loc, tags=["a"], expected_epoch=bad_epoch)

    def test_limit_and_invalid_names_rejected_without_partial_write(self):
        loc = self.locator("a.png")
        before = self.rows(*ANNOTATION_TABLES, "tags")
        for tags in (
            ["ok", "a,b"],
            ["ok", ""],
            ["ok", 3],
            "ok",
            [f"t{i}" for i in range(101)],
        ):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                self.replace(loc, tags=tags)
            with self.subTest(tags=tags, command="add"), self.assertRaises(ValueError):
                self.add(loc, tags=tags)
        for field, value in (
            ("locator", ""),
            ("locator", None),
            ("locator", "x" * 4097),
            ("sha256", DIGEST_A.upper()),
            ("sha256", DIGEST_A[:-1]),
            ("sha256", None),
            ("size", -1),
            ("size", True),
            ("size", "7"),
            ("mtime_ns", 1.5),
            ("mtime_ns", None),
            ("expected_tag_revision", -1),
            ("expected_tag_revision", False),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.replace(loc, tags=["ok"], **{field: value})
        self.assertEqual(self.rows(*ANNOTATION_TABLES, "tags"), before)
        self.assertEqual(self.index_revision(), 0)

    def test_writers_supply_uuid_ids_and_refuse_to_write_without_one(self):
        loc = self.locator("a.png")
        before = self.rows(*ANNOTATION_TABLES)
        with patch.object(annotations_module, "uuid4", return_value=""):
            with self.assertRaises(ValueError):
                self.replace(loc, tags=["a"])
        self.assertEqual(self.rows(*ANNOTATION_TABLES), before)
        self.replace(loc, tags=["a"])
        self.add(self.locator("b.png"), DIGEST_B, ["b"])
        conn = self.db.model.get_connection()
        for table in ("media_annotation_files", "media_annotation_bytes"):
            for (value,) in conn.execute(f"SELECT id FROM {table}"):
                self.assertRegex(value, UUID)
        self.schema_clean()

    def test_never_resolves_owner_through_prompt_lookup(self):
        loc = self.locator("a.png")
        prompt = self.db.save_prompt("Linked", tags=["prompt-tag"])
        self.db.link_image_to_prompt(prompt, loc)
        with patch.object(
            PromptDatabase,
            "get_prompt_id_for_image",
            side_effect=AssertionError("prompt lookup is not an owner"),
        ):
            self.replace(loc, tags=["img"])
            self.add(loc, tags=["more"])
            self.assertEqual(
                self.db.read_image_tags(loc, DIGEST_A)["tags"], ["img", "more"]
            )
            self.assertEqual(
                self.db.current_image_tags({loc: (SIZE, MTIME)}),
                {loc: ["img", "more"]},
            )
            self.assertTrue(
                self.db.refresh_image_fingerprint(loc, DIGEST_A, SIZE, MTIME + 1)
            )
            list(self.db.iter_image_tag_records())
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["prompt-tag"])


class AddImageTagsTests(AnnotationTestCase):
    def test_add_merges_without_removing_and_caps_at_100(self):
        loc = self.locator("a.png")
        self.replace(loc, tags=["Forest", "mist"])
        receipt = self.add(loc, tags=["forest", "Rain", "rain"])
        self.assertEqual(
            receipt,
            {
                "library_epoch": self.epoch,
                "sha256": DIGEST_A,
                "tags": ["Forest", "mist", "Rain"],
                "tag_revision": 2,
                "replayed": False,
                "added": 1,
            },
        )
        nothing = self.add(loc, tags=["MIST"])
        self.assertEqual(
            (nothing["tags"], nothing["tag_revision"], nothing["replayed"]),
            (["Forest", "mist", "Rain"], 2, True),
        )
        self.assertEqual(nothing["added"], 0)
        before = self.rows(*ANNOTATION_TABLES, "tags")
        with self.assertRaises(AnnotationCommandError) as ctx:
            self.add(loc, tags=[f"t{i}" for i in range(98)])
        self.assertEqual(ctx.exception.code, "tag-limit")
        self.assertEqual(ctx.exception.current["tag_revision"], 2)
        self.assertEqual(self.rows(*ANNOTATION_TABLES, "tags"), before)
        exact = self.add(loc, tags=[f"t{i}" for i in range(97)])
        self.assertEqual((len(exact["tags"]), exact["added"]), (100, 97))
        # An unseen file: add creates the owner and byte record like a save.
        fresh = self.add(self.locator("b.png"), DIGEST_B, ["one"])
        self.assertEqual(
            (fresh["tags"], fresh["tag_revision"], fresh["added"]), (["one"], 1, 1)
        )
        self.schema_clean()


class CurrentImageTagsTests(AnnotationTestCase):
    def test_current_tags_require_matching_fingerprint(self):
        loc = self.locator("a.png")
        other = self.locator("b.png")
        self.replace(loc, tags=["a"])
        self.replace(other, DIGEST_B, [], mtime_ns=MTIME + 9)
        self.assertEqual(
            self.db.current_image_tags({loc: (SIZE, MTIME), other: (SIZE, MTIME + 9)}),
            {loc: ["a"], other: []},
        )
        self.assertEqual(self.db.current_image_tags({loc: (SIZE, MTIME + 1)}), {})
        self.assertEqual(self.db.current_image_tags({loc: (SIZE + 1, MTIME)}), {})
        self.assertEqual(self.db.current_image_tags({}), {})
        self.assertEqual(
            self.db.current_image_tags({self.locator("none.png"): (SIZE, MTIME)}), {}
        )
        # Refreshing the verified fingerprint makes the record current again.
        self.assertTrue(
            self.db.refresh_image_fingerprint(loc, DIGEST_A, SIZE, MTIME + 1)
        )
        self.assertFalse(self.db.refresh_image_fingerprint(loc, DIGEST_B, SIZE, MTIME))
        self.assertFalse(
            self.db.refresh_image_fingerprint(
                self.locator("x.png"), DIGEST_A, SIZE, MTIME
            )
        )
        self.assertEqual(
            self.db.current_image_tags({loc: (SIZE, MTIME + 1)}), {loc: ["a"]}
        )
        self.assertEqual(self.db.current_image_tags({loc: (SIZE, MTIME)}), {})
        self.assertEqual(
            self.db.read_image_tags(loc, DIGEST_A),
            {"tags": ["a"], "tag_revision": 1, "prior_count": 0},
        )
        for bad in (None, [], {loc: (SIZE,)}, {loc: (SIZE, 1.5)}, {3: (SIZE, MTIME)}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.db.current_image_tags(bad)

    def test_newest_verification_wins_on_equal_fingerprints(self):
        loc = self.locator("a.png")
        stamps = iter(f"2026-10-22T00:00:0{i}.000000+00:00" for i in range(10))
        with patch.object(annotations_module, "utc_now_iso", side_effect=stamps):
            self.replace(loc, DIGEST_A, ["old bytes"])
            self.replace(loc, DIGEST_B, ["new bytes"])  # same size and mtime
        self.assertEqual(
            self.db.current_image_tags({loc: (SIZE, MTIME)}), {loc: ["new bytes"]}
        )
        with patch.object(
            annotations_module,
            "utc_now_iso",
            return_value="2026-10-22T00:01:00.000000+00:00",
        ):
            self.assertTrue(
                self.db.refresh_image_fingerprint(loc, DIGEST_A, SIZE, MTIME)
            )
        self.assertEqual(
            self.db.current_image_tags({loc: (SIZE, MTIME)}), {loc: ["old bytes"]}
        )

    def test_lookup_is_chunked_and_records_stream_in_position_order(self):
        locators = [self.locator(f"{i:04}.png") for i in range(503)]
        self.replace(locators[0], tags=["z", "y"])
        self.replace(locators[502], DIGEST_B, ["x"], mtime_ns=MTIME + 2)
        self.replace(locators[250], DIGEST_B, [], mtime_ns=MTIME + 1)
        statements = []
        conn = self.db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            current = self.db.current_image_tags(
                {
                    loc: (SIZE, MTIME + (2 if i == 502 else 0))
                    for i, loc in enumerate(locators)
                }
            )
        finally:
            conn.set_trace_callback(None)
        self.assertEqual(current, {locators[0]: ["z", "y"], locators[502]: ["x"]})
        lookups = [s for s in statements if "media_annotation_files" in s]
        self.assertEqual(len(lookups), 2)
        self.assertEqual(
            list(self.db.iter_image_tag_records()),
            [
                (locators[0], SIZE, MTIME, ["z", "y"]),
                (locators[250], SIZE, MTIME + 1, []),
                (locators[502], SIZE, MTIME + 2, ["x"]),
            ],
        )


class AnnotationIsolationTests(AnnotationTestCase):
    def test_cascade_from_vocabulary_delete_keeps_byte_records(self):
        loc = self.locator("a.png")
        self.replace(loc, tags=["gone", "kept"])
        self.db.delete_tag_all_prompts("gone")
        self.assertEqual(
            self.db.read_image_tags(loc, DIGEST_A),
            {
                "tags": ["kept"],
                "tag_revision": 2,
                "prior_count": 0,
            },  # moved by the delete
        )
        self.schema_clean()

    def test_junction_failure_leaves_nothing_behind(self):
        loc = self.locator("a.png")
        self.replace(loc, tags=["a"])
        conn = self.db.model.get_connection()
        conn.execute(
            "CREATE TRIGGER reject_image_tag BEFORE INSERT ON media_image_tags "
            "BEGIN SELECT RAISE(ABORT,'image tag failure'); END"
        )
        conn.commit()
        before = self.rows(*ANNOTATION_TABLES, "tags")
        with self.assertRaises(sqlite3.IntegrityError):
            self.replace(loc, tags=["b"], revision=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add(self.locator("new.png"), DIGEST_B, ["c"])
        self.assertFalse(conn.in_transaction)
        self.assertEqual(self.rows(*ANNOTATION_TABLES, "tags"), before)


if __name__ == "__main__":
    unittest.main()
