"""#443 (D152): delete selected Library prompts; paired ones are archived.

The DB commands, and what every reader does with an archived parent.
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.execution_history_schema import rotate_history_epoch
from database.library_commands import LibraryCommandError
from database.library_folders import folder_prefixes
from database.operations import PromptDatabase
from database.prompt_archive import TABLE as ARCHIVE
from utils.hashing import generate_prompt_hash


def save(db, text, **metadata):
    return db.save_prompt(text, prompt_hash=generate_prompt_hash(text), **metadata)


def rows(db, sql, params=()):
    with db.model.get_connection() as conn:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]


def archived_ids(db):
    return sorted(row[0] for row in rows(db, f"SELECT prompt_id FROM {ARCHIVE}"))


def prompt_ids(db):
    return sorted(row[0] for row in rows(db, "SELECT id FROM prompts"))


_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _stable(value):
    return "<time>" if isinstance(value, str) and _STAMP.match(value) else value


def table_dump(db):
    """Every row of every table (times masked), to compare two databases."""
    tables = [
        row[0]
        for row in rows(
            db,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name!='execution_history_metadata' "
            "ORDER BY name",
        )
    ]
    dump = {}
    for table in tables:
        dump[table] = sorted(
            (tuple(map(_stable, row)) for row in rows(db, f"SELECT * FROM {table}")),
            key=repr,
        )
    return dump


class LibraryDeleteCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Cleanups run last-in first-out and after every cleanup a test adds,
        # so a database a test opens (and closes via addCleanup) is closed
        # before the folder goes; Windows cannot delete an open file (#510).
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = PromptDatabase(str(self.root / "delete.db"))
        self.addCleanup(self.db.close_all)
        self.epoch = self.db.get_library_options()["library_epoch"]

    def paired(self, positive, negative="blur"):
        pair = self.db.save_prompt_pair(positive, negative)
        found = self.db.get_prompt_pair(pair)
        return found["positive_prompt_id"], found["negative_prompt_id"], pair

    def image(self, prompt_id, name):
        path = self.root / name
        path.write_bytes(b"png")
        return self.db.link_image_to_prompt(prompt_id, str(path))

    def apply(self, ids, epoch=None, review=None):
        """Review ``ids`` (or use ``review``), then write what was reviewed."""
        review = review or self.classify(ids)
        return self.db.archive_or_delete_library_prompts(
            review["archive"],
            review["delete"],
            expected_epoch=epoch or self.epoch,
            candidates=review["fingerprints"],
        )

    def classify(self, ids, epoch=None):
        return self.db.classify_library_delete(ids, expected_epoch=epoch or self.epoch)

    def restore(self, ids, epoch=None):
        return self.db.restore_library_prompts(ids, expected_epoch=epoch or self.epoch)


class TestClassifyAndApply(LibraryDeleteCase):
    def test_each_class_in_order(self):
        plain = save(self.db, "Plain")
        protected = save(self.db, "Guarded", tags=["__protected__"])
        protected_paired, _neg, _pair = self.paired("Guarded pair")
        self.db.bulk_add_tags([protected_paired], ["__protected__"])
        positive, negative_only, _pair = self.paired("Cat", "only negative")
        both = save(self.db, "Both roles")
        self.image(both, "both.png")  # a parent through its image
        self.db.save_prompt_pair("Other", "Both roles")
        archived_pair_positive, _n, old_pair = self.paired("Old")
        self.db.archive_prompt_pair(old_pair, True)
        already, _n2, _p2 = self.paired("Already")
        self.apply([already])
        self.image(plain, "plain.png")
        ids = [
            plain,
            protected,
            protected_paired,
            positive,
            negative_only,
            both,
            archived_pair_positive,
            already,
            99999,
        ]
        result = self.classify(ids)
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "delete": [plain],
                "archive": [positive, both, archived_pair_positive],
                "protected": [protected, protected_paired],
                "already_archived": [already],
                "not_archived": [],
                "not_found": [negative_only, 99999],
                "image_links": 1,
                "fingerprints": result["fingerprints"],
                # #482: the archive ids Delete permanently could remove (with pairs).
                "deletable": [positive, archived_pair_positive],
                "review_digests": result["review_digests"],
                "paired_counts": {
                    "pairs": 2,
                    "archived_pairs": 1,
                    "variants": 2,
                    # "only negative" is itself selected; only "blur" is kept.
                    "kept_negatives": 1,
                },
                "paired_image_links": 0,
            },
        )
        self.assertEqual(list(result["fingerprints"]), [plain])
        self.assertEqual(
            list(result["review_digests"]), [positive, archived_pair_positive]
        )

    def test_apply_archives_paired_deletes_unpaired_and_skips_protected(self):
        plain = save(self.db, "Plain", tags=["red"])
        protected = save(self.db, "Guarded", tags=["__protected__"])
        positive, negative, pair = self.paired("Cat")
        self.image(plain, "a.png")
        self.image(positive, "b.png")
        before = rows(self.db, "SELECT updated_at FROM prompts WHERE id=?", (positive,))
        result = self.apply([plain, protected, positive, 99999])
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "deleted": 1,
                "archived": 1,
                "already_archived": 0,
                "not_archived": 0,
                "not_found": 0,
                "changed": 0,
                "image_links": 1,
                "pairs_removed": 0,
            },
        )
        self.assertEqual(prompt_ids(self.db), sorted([protected, positive, negative]))
        self.assertEqual(archived_ids(self.db), [positive])
        # Archive keeps everything: the pair, its image link and the prompt row.
        self.assertEqual(
            len(rows(self.db, "SELECT 1 FROM prompt_pairs WHERE id=?", (pair,))), 1
        )
        self.assertEqual(
            len(
                rows(
                    self.db,
                    "SELECT 1 FROM generated_images WHERE prompt_id=?",
                    (positive,),
                )
            ),
            1,
        )
        after = rows(self.db, "SELECT updated_at FROM prompts WHERE id=?", (positive,))
        self.assertNotEqual(before, after)
        # The image file stays on disk.
        self.assertTrue((self.root / "a.png").exists())

    def test_delete_has_exactly_the_legacy_row_effects(self):
        twin = PromptDatabase(str(self.root / "twin.db"))
        self.addCleanup(twin.close_all)
        twin_epoch = twin.get_library_options()["library_epoch"]
        for db in (self.db, twin):
            a = save(db, "Alpha", tags=["x", "y"], category="c")
            save(db, "Beta")
            path = self.root / "shared.png"
            path.write_bytes(b"png")
            db.link_image_to_prompt(a, str(path), {"workflow": {"nodes": []}})
        self.db.bulk_delete_prompts([a])
        twin.archive_or_delete_library_prompts(
            [], [a], expected_epoch=twin_epoch, candidates=None
        )
        self.assertEqual(table_dump(self.db), table_dump(twin))

    def test_already_archived_and_restore(self):
        positive, _neg, _pair = self.paired("Cat")
        self.apply([positive])
        again = self.classify([positive])
        self.assertEqual(
            (again["archive"], again["already_archived"]), ([], [positive])
        )
        # Archived between the review and the write: counted, not archived twice.
        other, _n, _p = self.paired("Dog")
        review = self.classify([other])
        self.apply([other])
        late = self.apply([], review=review)
        self.assertEqual((late["archived"], late["already_archived"]), (0, 1))
        plain = save(self.db, "Plain")
        result = self.restore([positive, plain, 99999])
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "restored": 1,
                "unchanged": 1,
                "not_found": 1,
            },
        )
        self.assertEqual(archived_ids(self.db), [other])

    def test_the_write_executes_only_what_was_reviewed(self):
        plain = save(self.db, "Plain")
        guarded = save(self.db, "Guarded later")
        touched = save(self.db, "Touched later")
        review = self.classify([plain, guarded, touched])
        # Between the review and the write: one becomes protected, one is used.
        self.db.bulk_add_tags([guarded], ["__protected__"])
        self.db.record_prompt_use(touched)
        result = self.apply([], review=review)
        self.assertEqual((result["deleted"], result["changed"]), (1, 2))
        self.assertEqual(prompt_ids(self.db), sorted([guarded, touched]))
        self.assertEqual(archived_ids(self.db), [])

    def test_a_new_image_link_after_the_review_keeps_the_prompt(self):
        plain = save(self.db, "Plain")
        review = self.classify([plain])
        self.image(plain, "late.png")
        self.assertEqual(self.apply([], review=review)["changed"], 1)
        self.assertIn(plain, prompt_ids(self.db))

    def test_reviewed_deletes_without_a_backup_are_kept(self):
        plain = save(self.db, "Plain")
        review = self.classify([plain])
        result = self.db.archive_or_delete_library_prompts(
            [], [plain], expected_epoch=self.epoch, candidates={}
        )
        self.assertEqual((result["deleted"], result["changed"]), (0, 1))
        self.assertEqual(review["delete"], [plain])

    def test_a_reviewed_delete_that_became_paired_is_archived(self):
        plain = save(self.db, "Plain")
        review = self.classify([plain])
        self.db.save_prompt_pair("Plain", "late negative")
        result = self.apply([], review=review)
        self.assertEqual((result["deleted"], result["archived"]), (0, 1))

    def test_a_reviewed_archive_that_lost_its_pair_is_archived_not_deleted(self):
        positive, _negative, pair = self.paired("Cat")
        review = self.classify([positive])
        self.db.delete_prompt_pair(pair)
        result = self.apply([], review=review)
        self.assertEqual((result["deleted"], result["archived"]), (0, 1))
        self.assertEqual(archived_ids(self.db), [positive])

    def test_one_id_in_both_lists_or_no_ids_is_refused(self):
        plain = save(self.db, "Plain")
        for archive, delete in (([plain], [plain]), ([], [])):
            with self.subTest(archive=archive, delete=delete):
                with self.assertRaises(ValueError):
                    self.db.archive_or_delete_library_prompts(
                        archive, delete, expected_epoch=self.epoch, candidates=None
                    )

    def test_archiving_repairs_a_missing_or_stale_hash(self):
        missing, _n1, _p1 = self.paired("Hashless")
        stale, _n2, _p2 = self.paired("Stale hash")
        with self.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET hash=NULL WHERE id=?", (missing,))
            conn.execute("UPDATE prompts SET hash='stale' WHERE id=?", (stale,))
        self.apply([missing, stale])
        for pid, text in ((missing, "Hashless"), (stale, "Stale hash")):
            self.assertEqual(
                rows(self.db, "SELECT hash FROM prompts WHERE id=?", (pid,)),
                [(generate_prompt_hash(text),)],
            )
            # So every ingestion lookup finds the archived row.
            self.assertEqual(save(self.db, text), pid)

    def test_a_canonical_hash_another_row_holds_is_left_alone(self):
        stale, _n, _p = self.paired("Twin text")
        with self.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET hash='stale' WHERE id=?", (stale,))
        twin = save(self.db, "twin text")
        self.apply([stale])
        self.assertEqual(
            rows(self.db, "SELECT hash FROM prompts WHERE id=?", (stale,)), [("stale",)]
        )
        self.assertEqual(save(self.db, "Twin text"), twin)

    def test_restore_unhides_protected_and_non_parent_archived_prompts(self):
        guarded, _n, _p = self.paired("Guarded")
        self.apply([guarded])
        self.db.bulk_add_tags([guarded], ["__protected__"])
        negative_only = save(self.db, "Only a negative")
        self.db.save_prompt_pair("Owner", "Only a negative")
        with self.db.model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {ARCHIVE} VALUES (?, '2026-10-28T00:00:00+00:00')",
                (negative_only,),
            )
        result = self.restore([guarded, negative_only])
        self.assertEqual(result["restored"], 2)
        self.assertEqual(archived_ids(self.db), [])

    def test_restored_storage_changes_nothing(self):
        plain = save(self.db, "Plain")
        positive, _neg, _pair = self.paired("Cat")
        rotate_history_epoch(self.db.model.get_connection())
        for call in (
            lambda: self.apply([plain, positive]),
            lambda: self.classify([plain]),
            lambda: self.restore([positive]),
        ):
            with self.assertRaises(LibraryCommandError) as caught:
                call()
            self.assertEqual(caught.exception.code, "storage-changed")
        self.assertIn(plain, prompt_ids(self.db))
        self.assertEqual(archived_ids(self.db), [])

    def test_a_failure_mid_write_rolls_everything_back(self):
        plain = save(self.db, "Plain")
        positive, _neg, _pair = self.paired("Cat")
        with self.db.model.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER fail_delete BEFORE DELETE ON prompts "
                "BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
        with self.assertRaises(sqlite3.DatabaseError):
            self.apply([positive, plain])
        self.assertIn(plain, prompt_ids(self.db))
        self.assertEqual(archived_ids(self.db), [])

    def test_many_ids_are_handled_in_batches(self):
        ids = [save(self.db, f"Many {i}") for i in range(1000)]
        result = self.apply(ids)
        self.assertEqual(result["deleted"], 1000)

    def test_rejects_invalid_arguments(self):
        plain = save(self.db, "Plain")
        for ids, epoch in (([], self.epoch), ([True], self.epoch), ([plain], "x")):
            for call in (self.apply, self.classify, self.restore):
                with self.subTest(ids=ids, epoch=epoch, call=call.__name__):
                    with self.assertRaises(ValueError):
                        call(ids, epoch)


class TestArchivedReaders(LibraryDeleteCase):
    """What each reader does with an archived parent (design §4.4)."""

    def setUp(self):
        super().setUp()
        self.kept, _, _ = self.paired("Kept cat", "kept blur")
        self.gone, _, _ = self.paired("Gone cat", "gone blur")
        for pid, category, tag in ((self.kept, "keep", "k"), (self.gone, "gone", "g")):
            self.db.update_prompt_metadata(pid, category=category, tags=[tag])
        self.apply([self.gone])

    def ids_of(self, page):
        return [item["id"] for item in page["items"]]

    def test_listing_search_and_totals(self):
        active = self.db.list_library()
        self.assertEqual(self.ids_of(active), [self.kept])
        self.assertEqual(
            (active["total"], active["variant_total"], active["archived_total"]),
            (1, 1, 1),
        )
        archived = self.db.list_library(shelf="archived")
        self.assertEqual(self.ids_of(archived), [self.gone])
        self.assertEqual((archived["total"], archived["variant_total"]), (1, 1))
        self.assertEqual(self.ids_of(self.db.list_library(text="Gone")), [])
        self.assertEqual(
            self.ids_of(self.db.list_library(text="Gone", shelf="archived")),
            [self.gone],
        )
        self.assertEqual(self.ids_of(self.db.list_library(tags=["g"])), [])
        self.assertEqual(self.db.list_library(category="gone")["archived_total"], 1)
        with self.assertRaises(ValueError):
            self.db.list_library(shelf="all")

    def test_the_444_filters_and_tag_counts_hide_it(self):
        # #444's folder filter, tag match and tag ranking keep #443's shelves.
        bare, _bn, _bp = self.paired("Bare cat", "bare blur")
        self.apply([bare])
        folder = self.root / "out" / "cats"
        folder.mkdir(parents=True)
        for pid, name in ((self.kept, "kept.png"), (self.gone, "gone.png")):
            self.image(pid, str(Path("out") / "cats" / name))
        prefixes = folder_prefixes([str(self.root / "out")], "cats")
        self.assertEqual(
            self.ids_of(self.db.list_library(folder_prefixes=prefixes)), [self.kept]
        )
        self.assertEqual(
            self.ids_of(
                self.db.list_library(folder_prefixes=prefixes, shelf="archived")
            ),
            [self.gone],
        )
        self.assertEqual(
            self.ids_of(self.db.list_library(tags=["k", "g"], tag_mode="any")),
            [self.kept],
        )
        self.assertEqual(self.ids_of(self.db.list_library(tag_mode="untagged")), [])
        self.assertEqual(
            self.ids_of(self.db.list_library(tag_mode="untagged", shelf="archived")),
            [bare],
        )
        tags = self.db.list_vocabulary_tags(sort="prompts")
        self.assertEqual(
            {row["name"]: row["prompt_count"] for row in tags["tags"]},
            {"k": 1, "g": 0},
        )
        self.assertEqual(tags["untagged_count"], 0)
        self.assertEqual(
            self.db.list_vocabulary_tags(search="g", sort="prompts")["tags"][0][
                "prompt_count"
            ],
            0,
        )

    def test_detail_and_options(self):
        self.assertTrue(self.db.get_library_positive(self.gone)["positive"]["archived"])
        self.assertFalse(
            self.db.get_library_positive(self.kept)["positive"]["archived"]
        )
        # Suggestions keep archived parents' categories: the archived view
        # filters by them.
        self.assertEqual(self.db.get_library_options()["categories"], ["gone", "keep"])

    def test_counts(self):
        self.assertEqual(self.db.library_counts()["positive_prompts"], 1)
        self.assertEqual(self.db.library_counts()["negative_variants"], 1)
        tags = {
            row["name"]: row["prompt_count"]
            for row in self.db.list_vocabulary_tags()["tags"]
        }
        self.assertEqual((tags["k"], tags["g"]), (1, 0))
        categories = {
            row["value"]: row["prompt_count"]
            for row in self.db.list_vocabulary_categories()["categories"]
        }
        # A value only archived parents hold stays listed with 0, as a tag does.
        self.assertEqual(categories, {"keep": 1, "gone": 0})

    def test_restore_summary_and_export(self):
        from database.restore_stage import library_summary

        summary = library_summary(self.db.model.get_connection())
        self.assertEqual(summary["positives"], 1)
        exported = {
            item["positive_text"]: item["archived"]
            for item in self.db.export_library()["positives"]
        }
        self.assertEqual(exported, {"Kept cat": False, "Gone cat": True})

    def test_usage_statistics_keep_archived_runs(self):
        self.db.record_prompt_use(self.gone, 3)
        runs = {row["category"]: row["runs"] for row in self.db.usage_by_category()}
        self.assertEqual(runs["gone"], 3)

    def test_runs_node_saves_and_new_prompt_find_the_archived_row(self):
        count = len(prompt_ids(self.db))
        self.assertTrue(self.db.record_prompt_use(self.gone, 2))
        existing = self.db.get_prompt_by_hash(generate_prompt_hash("Gone cat"))
        self.assertEqual(existing["id"], self.gone)
        self.assertEqual(save(self.db, "Gone cat"), self.gone)
        self.db.save_prompt_pair("Gone cat", "new blur")
        self.assertEqual(len(prompt_ids(self.db)), count + 1)  # only the new negative
        self.assertEqual(archived_ids(self.db), [self.gone])

    def test_legacy_reads_still_list_it_and_legacy_delete_still_refuses(self):
        from database.pairs import PairDependencyError

        texts = [p["text"] for p in self.db.get_recent_prompts(limit=50)["prompts"]]
        self.assertIn("Gone cat", texts)
        with self.assertRaises(PairDependencyError):
            self.db.bulk_delete_prompts([self.gone])

    def test_as_another_parents_negative_it_stays_a_visible_variant(self):
        owner, _neg, pair = self.paired("Owner cat", "Gone cat")
        detail = self.db.get_library_positive(owner)
        self.assertEqual([v["id"] for v in detail["variants"]], [pair])
        self.assertEqual(self.ids_of(self.db.list_library(text="Gone")), [owner])

    def test_maintenance_never_prunes_an_archived_prompt(self):
        lonely = save(self.db, "Lonely")
        with self.db.model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {ARCHIVE} VALUES (?, '2026-10-28T00:00:00+00:00')",
                (lonely,),
            )
            orphaned = [row[0] for row in conn.execute(self.orphaned_sql()).fetchall()]
        self.assertNotIn(lonely, orphaned)

    @staticmethod
    def orphaned_sql():
        from database.maintenance import ORPHANED_PROMPTS_SQL

        return ORPHANED_PROMPTS_SQL

    def test_bulk_metadata_writes_still_reach_an_archived_parent(self):
        result = self.db.bulk_set_library_category(
            [self.gone], "moved", expected_epoch=self.epoch
        )
        self.assertEqual(result["updated"], 1)


if __name__ == "__main__":
    unittest.main()


class TestPairedDelete(LibraryDeleteCase):
    """#482 (D160): Delete permanently also removes a prompt's own saved pairs."""

    def pair_rows(self, positive):
        return rows(
            self.db,
            "SELECT id FROM prompt_pairs WHERE positive_prompt_id=? ORDER BY id",
            (positive,),
        )

    def pair_link(self, pair, image):
        return self.db.link_image_to_pair(pair, image, {"resolver_version": 1})

    def delete_paired(self, ids, review, archive=None, epoch=None):
        """Write ``ids`` as Delete permanently, with the digests ``review`` gave."""
        archive = review["archive"] if archive is None else archive
        return self.db.archive_or_delete_library_prompts(
            archive,
            review["delete"],
            expected_epoch=epoch or self.epoch,
            candidates=review["fingerprints"],
            delete_paired={pid: review["review_digests"][pid] for pid in ids},
        )

    def test_classify_marks_deletable_and_archive_only_with_pair_counts(self):
        cat, cat_negative, _pair = self.paired("Cat", "blur")
        self.db.save_prompt_pair("Cat", "grain")
        archived_pair = self.db.save_prompt_pair("Cat", "noise")
        self.db.archive_prompt_pair(archived_pair, True)
        shared = save(self.db, "Shared")
        self.image(shared, "shared.png")
        self.db.save_prompt_pair("Dog", "Shared")  # used as another positive's negative
        review = self.classify([cat, shared])
        self.assertEqual(review["archive"], [cat, shared])
        self.assertEqual(review["deletable"], [cat])
        self.assertEqual(list(review["review_digests"]), [cat])
        self.assertRegex(review["review_digests"][cat], r"^[0-9a-f]{64}$")
        self.assertEqual(
            review["paired_counts"],
            {"pairs": 3, "archived_pairs": 1, "variants": 3, "kept_negatives": 3},
        )
        self.assertEqual(review["paired_image_links"], 0)

    def test_an_empty_negative_pair_is_deleted_with_its_prompt(self):
        # Live prompt 7896: an auto-created pair with an explicitly empty negative.
        pair = self.db.save_prompt_pair("été", "")
        prompt = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        review = self.classify([prompt])
        self.assertEqual(review["deletable"], [prompt])
        self.assertEqual(
            review["paired_counts"],
            {"pairs": 1, "archived_pairs": 0, "variants": 0, "kept_negatives": 0},
        )
        result = self.delete_paired([prompt], review)
        self.assertEqual((result["deleted"], result["archived"]), (1, 0))
        self.assertEqual(result["pairs_removed"], 1)
        self.assertNotIn(prompt, prompt_ids(self.db))
        self.assertEqual(self.pair_rows(prompt), [])
        self.assertEqual(rows(self.db, "PRAGMA foreign_key_check"), [])

    def test_rows_removed_are_the_legacy_pair_deletes_then_the_prompt_delete(self):
        twin = PromptDatabase(str(self.root / "twin.db"))
        self.addCleanup(twin.close_all)
        twin_epoch = twin.get_library_options()["library_epoch"]
        made = {}
        for db in (self.db, twin):
            plain = db.save_prompt_pair("Owl", "", tags=["night"], title="Owl")
            with_negative = db.save_prompt_pair("Owl", "dark", rating=4)
            hidden = db.save_prompt_pair("Owl", "fog")
            db.archive_prompt_pair(hidden, True)
            owl = db.get_prompt_pair(plain)["positive_prompt_id"]
            path = self.root / "owl.png"
            path.write_bytes(b"png")
            image = db.link_image_to_prompt(owl, str(path), {"workflow": {"nodes": []}})
            db.link_image_to_pair(with_negative, image, {"resolver_version": 1})
            save(db, "Bystander", tags=["night"])
            made[id(db)] = (owl, [plain, with_negative, hidden])
        owl, pairs = made[id(self.db)]
        review = self.classify([owl])
        self.delete_paired([owl], review)
        twin_owl, twin_pairs = made[id(twin)]
        with twin.model.get_connection() as conn:
            conn.execute(
                "DELETE FROM image_prompt_pairs WHERE pair_id IN (SELECT id FROM "
                "prompt_pairs WHERE positive_prompt_id=?)",
                (twin_owl,),
            )
            conn.commit()
        for pair in twin_pairs:
            self.assertTrue(twin.delete_prompt_pair(pair))
        twin.archive_or_delete_library_prompts(
            [], [twin_owl], expected_epoch=twin_epoch, candidates=None
        )
        self.assertEqual(table_dump(self.db), table_dump(twin))
        # The negatives stay, and the image file stays on disk.
        self.assertTrue((self.root / "owl.png").exists())
        texts = [row[0] for row in rows(self.db, "SELECT text FROM prompts")]
        self.assertLessEqual({"dark", "fog", "Bystander"}, set(texts))

    def test_a_negative_used_by_another_positive_is_archived_never_deleted(self):
        shared = save(self.db, "Shared")
        self.image(shared, "s.png")
        self.db.save_prompt_pair("Dog", "Shared")
        review = self.classify([shared])
        self.assertEqual(review["deletable"], [])
        result = self.db.archive_or_delete_library_prompts(
            [shared],
            [],
            expected_epoch=self.epoch,
            candidates={},
            delete_paired={shared: "0" * 64},
        )
        self.assertEqual((result["deleted"], result["archived"]), (0, 1))
        self.assertEqual(result["changed"], 1)
        self.assertEqual(archived_ids(self.db), [shared])

    def test_a_positive_and_its_own_negative_selected_together(self):
        cat, blur, _pair = self.paired("Cat", "blur")
        self.image(blur, "blur.png")  # the negative is a Library prompt too
        review = self.classify([cat, blur])
        self.assertEqual(review["deletable"], [cat])
        result = self.delete_paired([cat], review)
        self.assertEqual((result["deleted"], result["archived"]), (1, 1))
        self.assertEqual(archived_ids(self.db), [blur])
        self.assertNotIn(cat, prompt_ids(self.db))

    def test_a_self_pair_is_deleted(self):
        pair = self.db.save_prompt_pair("Same", "Same")
        same = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        review = self.classify([same])
        self.assertEqual(review["deletable"], [same])
        self.assertEqual(review["paired_counts"]["kept_negatives"], 0)
        self.assertEqual(self.delete_paired([same], review)["deleted"], 1)
        self.assertNotIn(same, prompt_ids(self.db))

    def test_drift_after_the_review_archives_and_counts_changed(self):
        cases = {
            "pair added": lambda p: self.db.save_prompt_pair("Fox", "new"),
            "pair edited": lambda p: self.db.update_prompt_pair_metadata(
                self.pair_rows(p)[0][0], title="Renamed"
            ),
            "pairs gone": lambda p: self.db.delete_prompt_pair(self.pair_rows(p)[0][0]),
            "now another's negative": lambda p: self.db.save_prompt_pair("Hen", "Fox"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                fox, _neg, _pair = self.paired("Fox", "mist " + name)
                review = self.classify([fox])
                self.assertEqual(review["deletable"], [fox])
                change(fox)
                result = self.delete_paired([fox], review)
                self.assertEqual(
                    (result["deleted"], result["archived"], result["changed"]),
                    (0, 1, 1),
                )
                self.assertIn(fox, archived_ids(self.db))
                self.restore([fox])
                with self.db.model.get_connection() as conn:
                    conn.execute(
                        "DELETE FROM prompt_pairs WHERE positive_prompt_id IN "
                        "(SELECT id FROM prompts WHERE text IN ('Hen','Fox'))"
                    )
                    conn.commit()

    def test_delete_paired_must_name_reviewed_archive_ids(self):
        fox, _neg, _pair = self.paired("Fox")
        review = self.classify([fox])
        with self.assertRaises(ValueError):
            self.db.archive_or_delete_library_prompts(
                [],
                [],
                expected_epoch=self.epoch,
                candidates={},
                delete_paired={fox: review["review_digests"][fox]},
            )
        with self.assertRaises(ValueError):
            self.db.archive_or_delete_library_prompts(
                [fox],
                [],
                expected_epoch=self.epoch,
                candidates={},
                delete_paired={fox: "not a digest"},
            )


class TestArchivedShelfDelete(LibraryDeleteCase):
    """#492: Delete… on the archived shelf deletes archived prompts permanently."""

    def archive(self, *ids):
        with self.db.model.get_connection() as conn:
            conn.executemany(
                f"INSERT INTO {ARCHIVE}(prompt_id, archived_at) VALUES (?, ?)",
                [(pid, "2026-10-29T00:00:00Z") for pid in ids],
            )
            conn.commit()

    def shelf_classify(self, ids):
        return self.db.classify_library_delete(
            ids, expected_epoch=self.epoch, shelf="archived"
        )

    def shelf_write(self, review, deletes=None, paired=None):
        """What the archived shelf's client sends: only what it deletes."""
        deletes = review["delete"] if deletes is None else deletes
        paired = review["deletable"] if paired is None else paired
        return self.db.archive_or_delete_library_prompts(
            list(paired),
            list(deletes),
            expected_epoch=self.epoch,
            candidates=review["fingerprints"],
            delete_paired={pid: review["review_digests"][pid] for pid in paired},
            shelf="archived",
        )

    def test_classify_on_the_archived_shelf(self):
        owl, _neg, _pair = self.paired("Owl", "dark")
        shared = save(self.db, "Shared")
        self.image(shared, "shared.png")
        self.db.save_prompt_pair("Dog", "Shared")  # another positive's negative
        loose = save(self.db, "Loose")
        self.image(loose, "loose.png")
        guarded = save(self.db, "Guarded", tags=["__protected__"])
        self.image(guarded, "guarded.png")
        shown = save(self.db, "Shown")
        self.image(shown, "shown.png")
        self.archive(owl, shared, loose, guarded)
        review = self.shelf_classify([owl, shared, loose, guarded, shown, 999999])
        self.assertEqual(review["archive"], [owl, shared])
        self.assertEqual(review["deletable"], [owl])
        self.assertEqual(review["delete"], [loose])
        self.assertEqual(review["protected"], [guarded])
        self.assertEqual(review["not_archived"], [shown])
        self.assertEqual(review["already_archived"], [])
        self.assertEqual(review["not_found"], [999999])
        self.assertEqual(review["image_links"], 1)
        # The active shelf is unchanged: archived prompts are only counted there.
        active = self.classify([owl, loose, shown])
        self.assertEqual(active["already_archived"], [owl, loose])
        self.assertEqual(active["not_archived"], [])
        self.assertEqual(active["delete"], [shown])

    def test_the_write_deletes_archived_prompts_and_their_pairs(self):
        owl, owl_negative, pair = self.paired("Owl", "dark")
        loose = save(self.db, "Loose")
        self.image(loose, "loose.png")
        self.archive(owl, loose)
        result = self.shelf_write(self.shelf_classify([owl, loose]))
        self.assertEqual(
            (result["deleted"], result["archived"], result["changed"]), (2, 0, 0)
        )
        self.assertEqual((result["pairs_removed"], result["not_archived"]), (1, 0))
        self.assertNotIn(owl, prompt_ids(self.db))
        self.assertNotIn(loose, prompt_ids(self.db))
        self.assertIn(owl_negative, prompt_ids(self.db))
        self.assertIsNone(self.db.get_prompt_pair(pair))
        self.assertEqual(archived_ids(self.db), [])
        self.assertEqual(rows(self.db, "PRAGMA foreign_key_check"), [])

    def test_drift_leaves_the_prompt_archived_and_untouched(self):
        owl, _neg, pair = self.paired("Owl", "dark")
        hen, _hen_negative, hen_pair = self.paired("Hen", "fog")
        loose = save(self.db, "Loose")
        self.image(loose, "loose.png")
        self.archive(owl, hen, loose)
        review = self.shelf_classify([owl, hen, loose])
        before = rows(self.db, "SELECT id,hash,updated_at FROM prompts ORDER BY id")
        self.db.update_prompt_pair_metadata(pair, notes="edited after the review")
        # Reviewed as unpaired, it gains a pair before the write.
        self.db.save_prompt_pair("Loose", "grain")
        # A pair removed since: no longer what the review promised.
        with self.db.model.get_connection() as conn:
            conn.execute("DELETE FROM prompt_pairs WHERE id=?", (hen_pair,))
            conn.commit()
        result = self.shelf_write(review)
        self.assertEqual(
            (result["deleted"], result["archived"], result["changed"]), (0, 0, 3)
        )
        self.assertEqual(archived_ids(self.db), sorted([owl, hen, loose]))
        self.assertEqual(
            rows(self.db, "SELECT id,hash,updated_at FROM prompts ORDER BY id")[
                : len(before)
            ],
            before,
        )

    def test_a_prompt_restored_since_the_review_is_kept(self):
        owl, _neg, _pair = self.paired("Owl", "dark")
        self.archive(owl)
        review = self.shelf_classify([owl])
        self.restore([owl])
        result = self.shelf_write(review)
        self.assertEqual((result["deleted"], result["not_archived"]), (0, 1))
        self.assertIn(owl, prompt_ids(self.db))
        self.assertEqual(archived_ids(self.db), [])

    def test_the_negative_of_a_deleted_positive_stays_archived(self):
        # #482's rule, kept on this shelf: X is only Dog's negative.
        dog, shared, _pair = self.paired("Dog", "Shared")
        self.image(shared, "shared.png")
        self.archive(dog, shared)
        review = self.shelf_classify([dog, shared])
        self.assertEqual(review["deletable"], [dog])
        self.assertEqual(review["archive"], [dog, shared])
        result = self.shelf_write(review)
        self.assertEqual((result["deleted"], result["pairs_removed"]), (1, 1))
        self.assertEqual(archived_ids(self.db), [shared])

    def test_mixed_outcomes_are_each_counted_once(self):
        owl, _neg, _pair = self.paired("Owl", "dark")
        hen, _neg2, hen_pair = self.paired("Hen", "fog")
        loose = save(self.db, "Loose")
        self.image(loose, "loose.png")
        guarded = save(self.db, "Guarded", tags=["__protected__"])
        self.image(guarded, "guarded.png")
        gone = save(self.db, "Gone")
        self.image(gone, "gone.png")
        self.archive(owl, hen, loose, guarded, gone)
        review = self.shelf_classify([owl, hen, loose, guarded, gone])
        self.db.update_prompt_pair_metadata(hen_pair, notes="drifted")
        self.restore([loose])
        with self.db.model.get_connection() as conn:
            conn.execute("DELETE FROM generated_images WHERE prompt_id=?", (gone,))
            conn.execute("DELETE FROM prompts WHERE id=?", (gone,))
            conn.commit()
        result = self.shelf_write(review, deletes=[loose, gone])
        self.assertEqual(
            {k: result[k] for k in ("deleted", "changed", "not_found", "not_archived")},
            {"deleted": 1, "changed": 1, "not_found": 1, "not_archived": 1},
        )
        self.assertNotIn(owl, prompt_ids(self.db))
        self.assertEqual(archived_ids(self.db), sorted([hen, guarded]))

    def test_shelf_must_be_active_or_archived(self):
        owl, _neg, _pair = self.paired("Owl")
        with self.assertRaises(ValueError):
            self.db.classify_library_delete(
                [owl], expected_epoch=self.epoch, shelf="trash"
            )
        with self.assertRaises(ValueError):
            self.db.archive_or_delete_library_prompts(
                [owl], [], expected_epoch=self.epoch, candidates={}, shelf="trash"
            )
