"""Bounded vocabulary reads count exactly what Library Browse shows."""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.library_commands import LibraryCommandError
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestVocabularyReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "vocabulary.db"))

    def tearDown(self):
        try:
            # Windows refuses to remove a database that still has open handles.
            self.db.close_all()
        finally:
            self.temp.cleanup()

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def read(self, name, **options):
        method = getattr(self.db, name, None)
        self.assertTrue(callable(method), f"PromptDatabase.{name} is missing")
        return method(**options)

    def add_unused(self, *names):
        with self.db.model.get_connection() as conn:
            for name in names:
                conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))

    def all_tags(self):
        rows, after = [], None
        while True:
            page = self.read("list_vocabulary_tags", limit=200, after=after)
            rows.extend(page["tags"])
            if not page["has_more"]:
                return rows
            after = page["next_key"]

    def test_tag_rows_are_registry_rows_in_creation_order_with_unused_at_zero(self):
        self.save("First", tags=["c"])
        self.save("Second", tags=["a"])
        self.save("Third", tags=["b", "a"])
        self.add_unused("z")
        page = self.read("list_vocabulary_tags")
        self.assertEqual([row["name"] for row in page["tags"]], ["c", "a", "b", "z"])
        self.assertEqual(page["total"], 4)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_key"])
        by_name = {row["name"]: row for row in page["tags"]}
        self.assertEqual(by_name["z"]["prompt_count"], 0)
        self.assertEqual(by_name["a"]["prompt_count"], 2)
        self.assertEqual(set(by_name["c"]), {"id", "name", "prompt_count"})

    def test_prompt_count_equals_library_browse_total_for_every_tag(self):
        self.save("Parent only", tags=["parent-only"])
        self.db.save_prompt_pair("Pair cat", "Pair noise", tags=["pair-only"])
        archived = self.db.save_prompt_pair(
            "Archived cat", "Archived noise", tags=["archived-only"]
        )
        self.db.archive_prompt_pair(archived)
        self.save("Legacy blur", tags=["negative-only"])
        self.db.save_prompt_pair("Negative owner", "Legacy blur")
        self.save("Dual dog", tags=["dual"])
        self.db.save_prompt_pair("Dual dog", "Dual noise", tags=["dual"])
        retained = self.save("Retained negative", tags=["retained"])
        self.db.save_prompt_pair("Retained owner", "Retained negative")
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (retained, "Kept"),
            )
        self.add_unused("unused")
        rows = self.all_tags()
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertEqual(
                row["prompt_count"],
                self.db.list_library(tags=[row["name"]])["total"],
                row["name"],
            )
        counts = {row["name"]: row["prompt_count"] for row in rows}
        self.assertEqual(
            counts,
            {
                "parent-only": 1,
                "pair-only": 1,
                "archived-only": 0,
                "negative-only": 0,
                "dual": 1,
                "retained": 1,
                "unused": 0,
            },
        )

    def test_category_rows_match_library_options_and_browse_totals(self):
        self.save("Zeta first", category="Zeta")
        self.db.save_prompt_pair("Alpha owner", "Alpha noise", category="Alpha")
        self.save("Padded", category=" Landscape ")
        self.save("Plain", category="Landscape")
        self.save("Blank", category="   ")
        self.save("Null")
        self.save("Zeta again", category="Zeta")
        archived = self.db.save_prompt_pair(
            "Archived owner", "Archived noise", category="Archived-only"
        )
        self.db.archive_prompt_pair(archived)
        self.save("Legacy negative", category="Negative-only")
        self.db.save_prompt_pair("Negative owner", "Legacy negative")
        page = self.read("list_vocabulary_categories")
        values = [row["value"] for row in page["categories"]]
        self.assertEqual(values, ["Zeta", "Alpha", " Landscape ", "Landscape"])
        self.assertEqual(set(values), set(self.db.get_library_options()["categories"]))
        self.assertEqual(page["total"], 4)
        self.assertFalse(page["has_more"])
        for row in page["categories"]:
            self.assertEqual(set(row), {"value", "prompt_count"})
            self.assertEqual(
                row["prompt_count"],
                self.db.list_library(category=row["value"])["total"],
                row["value"],
            )
        self.assertEqual(page["categories"][0]["prompt_count"], 2)

    def test_category_pages_continue_by_first_use_then_value(self):
        for index in range(5):
            self.save(f"Owner {index}", category=f"C{4 - index}")
        first = self.read("list_vocabulary_categories", limit=2)
        second = self.read(
            "list_vocabulary_categories", limit=2, after=first["next_key"]
        )
        third = self.read(
            "list_vocabulary_categories", limit=2, after=second["next_key"]
        )
        values = [
            row["value"]
            for page in (first, second, third)
            for row in page["categories"]
        ]
        self.assertEqual(values, ["C4", "C3", "C2", "C1", "C0"])
        self.assertEqual(
            [page["has_more"] for page in (first, second, third)], [True, True, False]
        )
        for after in ([1], ["1", "C4"], {"id": 1}, [True, "C4"]):
            with self.assertRaises(ValueError):
                self.read("list_vocabulary_categories", after=after)

    def test_pages_are_bounded_and_continue_without_gaps(self):
        self.add_unused(*[f"tag-{index:03d}" for index in range(250)])
        pages, after = [], None
        for _ in range(3):
            page = self.read("list_vocabulary_tags", limit=100, after=after)
            pages.append(page)
            after = page["next_key"]
        self.assertEqual([len(page["tags"]) for page in pages], [100, 100, 50])
        self.assertEqual([page["has_more"] for page in pages], [True, True, False])
        self.assertEqual({page["total"] for page in pages}, {250})
        names = [row["name"] for page in pages for row in page["tags"]]
        self.assertEqual(names, [f"tag-{index:03d}" for index in range(250)])
        for options in ({"limit": 0}, {"limit": 201}, {"after": "1"}, {"after": 0}):
            with self.assertRaises(ValueError):
                self.read("list_vocabulary_tags", **options)

    def test_reads_share_one_snapshot_and_report_epoch(self):
        self.save("Cat", category="Animal", tags=["cat"])
        epoch = self.db.get_execution_epoch()
        tags = self.read("list_vocabulary_tags")
        categories = self.read("list_vocabulary_categories")
        self.assertEqual(tags["library_epoch"], epoch)
        self.assertEqual(categories["library_epoch"], epoch)
        helper = getattr(self.db, "_vocabulary_tags", None)
        self.assertTrue(callable(helper), "PromptDatabase._vocabulary_tags is missing")
        with self.db.library_snapshot() as (conn, snapshot_epoch, _ceiling):
            inner = helper(conn, snapshot_epoch, limit=10, after=None)
            self.assertEqual(inner["library_epoch"], epoch)
            self.assertTrue(conn.in_transaction)


_TAG_TABLES = ('"tags"', '"prompt_tags"', '"prompt_pair_tags"', '"sqlite_sequence"')


class TestVocabularyTagCommands(unittest.TestCase):
    """Guarded tag create, rename, merge and delete across both junctions."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "commands.db"))
        self.epoch = self.db.get_execution_epoch()

    def tearDown(self):
        try:
            self.db.close_all()
        finally:
            self.temp.cleanup()

    def command(self, method_name, /, *args, **options):
        method = getattr(self.db, method_name, None)
        self.assertTrue(callable(method), f"PromptDatabase.{method_name} is missing")
        return method(*args, **options)

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def tag_id(self, name):
        with self.db.model.get_connection() as conn:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def links(self, tag_name):
        with self.db.model.get_connection() as conn:
            components = conn.execute(
                "SELECT pt.prompt_id FROM prompt_tags pt JOIN tags t "
                "ON t.id=pt.tag_id WHERE t.name=? ORDER BY 1",
                (tag_name,),
            ).fetchall()
            pairs = conn.execute(
                "SELECT ppt.pair_id FROM prompt_pair_tags ppt JOIN tags t "
                "ON t.id=ppt.tag_id WHERE t.name=? ORDER BY 1",
                (tag_name,),
            ).fetchall()
        return [row[0] for row in components], [row[0] for row in pairs]

    def junction_rows(self):
        with self.db.model.get_connection() as conn:
            return (
                conn.execute("SELECT * FROM tags ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM prompt_tags ORDER BY 1,2").fetchall(),
                conn.execute("SELECT * FROM prompt_pair_tags ORDER BY 1,2").fetchall(),
            )

    def other_rows(self):
        """Every row outside the tag registry and junctions, timestamps included."""
        conn = self.db.model.get_connection()
        return [
            line
            for line in conn.iterdump()
            if line.startswith("INSERT INTO")
            and not line.startswith(tuple(f"INSERT INTO {t}" for t in _TAG_TABLES))
        ]

    def changes(self):
        return self.db.model.get_connection().total_changes

    def owners(self, tag="moody"):
        """Parent, active pair, archived pair and negative-only component."""
        parent = self.save("Parent sky", category="Scenery", tags=[tag])
        self.db.link_image_to_prompt(parent, str(Path(self.temp.name) / "a.png"))
        active = self.db.save_prompt_pair("Active sky", "Active noise", tags=[tag])
        archived = self.db.save_prompt_pair(
            "Archived sky", "Archived noise", tags=[tag]
        )
        self.db.archive_prompt_pair(archived)
        negative = self.save("Legacy blur", tags=[tag])
        self.db.save_prompt_pair("Negative owner", "Legacy blur")
        return parent, negative, active, archived

    def test_create_inserts_unused_tag_and_reuses_exact_existing(self):
        self.save("Owner", tags=["other"])
        before = self.junction_rows()
        changes = self.changes()
        created = self.command(
            "create_vocabulary_tag", "  sunset ", expected_epoch=self.epoch
        )
        self.assertEqual(self.changes() - changes, 1)
        self.assertEqual(created["library_epoch"], self.epoch)
        self.assertTrue(created["created"])
        self.assertEqual(
            created["tag"],
            {"id": self.tag_id("sunset"), "name": "sunset", "prompt_count": 0},
        )
        after = self.junction_rows()
        self.assertEqual(after[1:], before[1:])
        changes = self.changes()
        again = self.command(
            "create_vocabulary_tag", "sunset", expected_epoch=self.epoch
        )
        self.assertEqual(self.changes(), changes)
        self.assertFalse(again["created"])
        self.assertEqual(again["tag"], created["tag"])
        other = self.command(
            "create_vocabulary_tag", "Sunset", expected_epoch=self.epoch
        )
        self.assertTrue(other["created"])
        self.assertNotEqual(other["tag"]["id"], created["tag"]["id"])
        self.assertIn("sunset", self.db.get_all_tags())

    def test_create_validates_names(self):
        before = self.junction_rows()
        for name in ("", "   ", "x" * 51, "bad\x01", "a,b", 7, None, ["a"]):
            with self.assertRaises(ValueError, msg=repr(name)):
                self.command("create_vocabulary_tag", name, expected_epoch=self.epoch)
        with self.assertRaises(ValueError):
            self.command("create_vocabulary_tag", "ok", expected_epoch="nope")
        self.assertEqual(self.junction_rows(), before)

    def test_rename_to_new_name_keeps_id_and_all_links(self):
        parent, negative, active, archived = self.owners()
        tag_id = self.tag_id("moody")
        others = self.other_rows()
        result = self.command(
            "rename_vocabulary_tag",
            tag_id,
            expected_epoch=self.epoch,
            name="moody",
            new_name=" dramatic ",
        )
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "merged": False,
                "tag": {"id": tag_id, "name": "dramatic", "prompt_count": 2},
                "components_changed": 2,
                "pairs_changed": 2,
            },
        )
        self.assertIsNone(self.tag_id("moody"))
        self.assertEqual(
            self.links("dramatic"), ([parent, negative], [active, archived])
        )
        self.assertEqual(self.db.get_prompt_pair(archived)["tags"], ["dramatic"])
        self.assertEqual(self.other_rows(), others)

    def test_rename_to_existing_merges_both_junctions_without_duplicates(self):
        both = self.save("Both", tags=["old", "new"])
        only_old = self.save("Old only", tags=["old"])
        pair_both = self.db.save_prompt_pair("Pair both", "Noise", tags=["old", "new"])
        pair_old = self.db.save_prompt_pair("Pair old", "Noise", tags=["old"])
        self.db.archive_prompt_pair(pair_old)
        source, target = self.tag_id("old"), self.tag_id("new")
        others = self.other_rows()
        result = self.command(
            "rename_vocabulary_tag",
            source,
            expected_epoch=self.epoch,
            name="old",
            new_name="new",
        )
        self.assertTrue(result["merged"])
        self.assertEqual(result["tag"]["id"], target)
        self.assertEqual(result["tag"]["name"], "new")
        self.assertEqual(result["tag"]["prompt_count"], 3)
        self.assertEqual(
            (result["components_changed"], result["pairs_changed"]), (2, 2)
        )
        self.assertIsNone(self.tag_id("old"))
        self.assertEqual(self.links("new"), ([both, only_old], [pair_both, pair_old]))
        self.assertEqual(self.db.get_prompt_by_id(both)["tags"], ["new"])
        self.assertEqual(self.db.get_prompt_pair(pair_both)["tags"], ["new"])
        self.assertEqual(self.other_rows(), others)

    def test_case_only_rename_is_a_rename(self):
        prompt = self.save("Kitten", tags=["Cat"])
        tag_id = self.tag_id("Cat")
        result = self.command(
            "rename_vocabulary_tag",
            tag_id,
            expected_epoch=self.epoch,
            name="Cat",
            new_name="cat",
        )
        self.assertFalse(result["merged"])
        self.assertEqual(
            result["tag"], {"id": tag_id, "name": "cat", "prompt_count": 1}
        )
        self.assertEqual(self.db.get_prompt_by_id(prompt)["tags"], ["cat"])

    def test_stale_identity_and_epoch_write_nothing(self):
        self.owners()
        tag_id = self.tag_id("moody")
        before = (self.junction_rows(), self.other_rows())
        changes = self.changes()
        for tag, name in ((tag_id, "Moody"), (tag_id + 99, "moody")):
            for method, extra in (
                ("rename_vocabulary_tag", {"new_name": "fresh"}),
                ("delete_vocabulary_tag", {}),
            ):
                with self.assertRaises(LibraryCommandError) as caught:
                    self.command(
                        method, tag, expected_epoch=self.epoch, name=name, **extra
                    )
                self.assertEqual(caught.exception.code, "vocabulary-conflict")
                self.assertEqual(caught.exception.epoch, self.epoch)
        stale = "00000000-0000-4000-8000-000000000000"
        with patch.object(type(self.db), "_vocabulary_tag", create=True) as lookup:
            for method, args, extra in (
                ("create_vocabulary_tag", ("fresh",), {}),
                (
                    "rename_vocabulary_tag",
                    (tag_id,),
                    {"name": "moody", "new_name": "x"},
                ),
                ("delete_vocabulary_tag", (tag_id,), {"name": "moody"}),
            ):
                with self.assertRaises(LibraryCommandError) as caught:
                    self.command(method, *args, expected_epoch=stale, **extra)
                self.assertEqual(caught.exception.code, "storage-changed")
                self.assertEqual(caught.exception.epoch, self.epoch)
            lookup.assert_not_called()
        for new_name in ("moody", " moody "):
            with self.assertRaises(ValueError):
                self.command(
                    "rename_vocabulary_tag",
                    tag_id,
                    expected_epoch=self.epoch,
                    name="moody",
                    new_name=new_name,
                )
        for bad_id in (0, -1, "1", True, 2**63):
            with self.assertRaises(ValueError):
                self.command(
                    "delete_vocabulary_tag",
                    bad_id,
                    expected_epoch=self.epoch,
                    name="moody",
                )
        self.assertEqual(self.changes(), changes)
        self.assertEqual((self.junction_rows(), self.other_rows()), before)

    def test_delete_removes_name_and_links_only(self):
        self.owners()
        self.save("Bystander", tags=["keep"])
        tag_id = self.tag_id("moody")
        impact = self.command("vocabulary_tag_impact", tag_id)
        self.assertEqual(
            impact,
            {
                "library_epoch": self.epoch,
                "impact": {
                    "positive_prompts": 1,
                    "other_prompts": 1,
                    "negative_variants": 1,
                    "archived_negative_variants": 1,
                },
            },
        )
        self.assertIsNone(self.command("vocabulary_tag_impact", tag_id + 99))
        others = self.other_rows()
        result = self.command(
            "delete_vocabulary_tag", tag_id, expected_epoch=self.epoch, name="moody"
        )
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "deleted": True,
                "components_changed": 2,
                "pairs_changed": 2,
            },
        )
        self.assertIsNone(self.tag_id("moody"))
        self.assertEqual(self.links("moody"), ([], []))
        self.assertEqual(self.other_rows(), others)
        self.assertEqual(len(self.links("keep")[0]), 1)
        with self.assertRaises(LibraryCommandError) as caught:
            self.command(
                "delete_vocabulary_tag", tag_id, expected_epoch=self.epoch, name="moody"
            )
        self.assertEqual(caught.exception.code, "vocabulary-conflict")


_OLD_STAMP = "2000-01-01T00:00:00+00:00"
_DML = re.compile(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


class TestVocabularyCategoryCommands(unittest.TestCase):
    """Category preview and token-guarded rename/consolidation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "categories.db"))
        self.epoch = self.db.get_execution_epoch()

    def tearDown(self):
        try:
            self.db.close_all()
        finally:
            self.temp.cleanup()

    def command(self, method_name, /, **options):
        method = getattr(self.db, method_name, None)
        self.assertTrue(callable(method), f"PromptDatabase.{method_name} is missing")
        return method(**options)

    def preview(self, source="Scenery", target="Landscape"):
        return self.command(
            "preview_vocabulary_category",
            expected_epoch=self.epoch,
            source=source,
            target=target,
        )

    def apply(self, token, source="Scenery", target="Landscape"):
        return self.command(
            "apply_vocabulary_category",
            expected_epoch=self.epoch,
            source=source,
            target=target,
            impact_token=token,
        )

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def owners(self, source="Scenery"):
        """2 eligible, 1 negative-only component; 2 active, 1 archived pair."""
        first = self.save("Parent one", category=source)
        second = self.save("Parent two", category=source)
        negative = self.save("Legacy blur", category=source)
        self.db.save_prompt_pair("Negative owner", "Legacy blur")
        active = [
            self.db.save_prompt_pair(
                f"Active {n}",
                f"Noise {n}",
                category=source,
                title=f"Title {n}",
                tags=[f"tag-{n}"],
                rating=n,
                notes=f"Notes {n}",
            )
            for n in (1, 2)
        ]
        archived = self.db.save_prompt_pair(
            "Archived sky", "Archived noise", category=source, title="Kept"
        )
        self.db.archive_prompt_pair(archived)
        return [first, second], negative, active, archived

    def age_rows(self):
        with self.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET updated_at=?", (_OLD_STAMP,))
            conn.execute("UPDATE prompt_pairs SET updated_at=?", (_OLD_STAMP,))

    def rows(self, table):
        with self.db.model.get_connection() as conn:
            return {
                row["id"]: dict(row)
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")
            }

    def snapshot(self):
        return self.rows("prompts"), self.rows("prompt_pairs")

    def traced(self, call):
        statements = []
        original = self.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.db.model, "get_connection", side_effect=trace_connection
        ):
            result = call()
        conn = original()
        conn.set_trace_callback(None)
        return result, statements

    def test_preview_counts_every_owner_type(self):
        self.owners()
        self.save("Elsewhere", category="Other")
        before = self.snapshot()
        result, statements = self.traced(self.preview)
        self.assertEqual(
            set(result),
            {
                "library_epoch",
                "source",
                "target",
                "target_exists",
                "impact",
                "impact_token",
            },
        )
        self.assertEqual(result["library_epoch"], self.epoch)
        self.assertEqual((result["source"], result["target"]), ("Scenery", "Landscape"))
        self.assertEqual(
            result["impact"],
            {
                "positive_prompts": 2,
                "other_prompts": 1,
                "negative_variants": 2,
                "archived_negative_variants": 1,
            },
        )
        self.assertFalse(result["target_exists"])
        self.assertRegex(result["impact_token"], r"\A[0-9a-f]{64}\Z")
        self.assertEqual([q for q in statements if _DML.match(q)], [])
        self.assertEqual(self.snapshot(), before)
        trimmed = self.preview(target="  Other  ")
        self.assertEqual(trimmed["target"], "Other")
        self.assertTrue(trimmed["target_exists"])
        self.assertNotEqual(trimmed["impact_token"], result["impact_token"])
        self.assertEqual(self.preview(), result)

    def test_apply_renames_exact_value_on_components_and_all_pairs(self):
        components, negative, active, archived = self.owners()
        variant = self.save("Padded", category=" Scenery ")
        bystander = self.save("Bystander", category="Other")
        self.age_rows()
        prompts_before, pairs_before = self.snapshot()
        preview = self.preview()
        receipt = self.apply(preview["impact_token"])
        self.assertEqual(
            receipt,
            {
                "library_epoch": self.epoch,
                "source": "Scenery",
                "target": "Landscape",
                "consolidated": False,
                "impact": preview["impact"],
            },
        )
        prompts_after, pairs_after = self.snapshot()
        changed_prompts = {*components, negative}
        changed_pairs = {*active, archived}
        for table_before, table_after, changed in (
            (prompts_before, prompts_after, changed_prompts),
            (pairs_before, pairs_after, changed_pairs),
        ):
            self.assertNotIn("Scenery", [r["category"] for r in table_after.values()])
            self.assertEqual(set(table_after), set(table_before))
            for row_id, row in table_after.items():
                old = table_before[row_id]
                if row_id in changed:
                    self.assertEqual(row["category"], "Landscape")
                    self.assertNotEqual(row["updated_at"], _OLD_STAMP)
                    expected = {**old, "category": "Landscape"}
                    expected["updated_at"] = row["updated_at"]
                    self.assertEqual(row, expected)
                else:
                    self.assertEqual(row, old)
        self.assertEqual(prompts_after[variant]["category"], " Scenery ")
        self.assertEqual(prompts_after[bystander]["category"], "Other")
        for pair_id in active:
            pair = self.db.get_prompt_pair(pair_id)
            self.assertEqual(pair["category"], "Landscape")
            self.assertEqual(len(pair["tags"]), 1)
        self.assertEqual(pairs_after[archived]["archived"], 1)
        self.assertEqual(pairs_after[archived]["title"], "Kept")
        # Consolidating into an existing value reports it.
        merge = self.preview(source=" Scenery ", target="Other")
        self.assertTrue(merge["target_exists"])
        receipt = self.apply(merge["impact_token"], source=" Scenery ", target="Other")
        self.assertTrue(receipt["consolidated"])
        self.assertEqual(
            receipt["impact"],
            {
                "positive_prompts": 1,
                "other_prompts": 0,
                "negative_variants": 0,
                "archived_negative_variants": 0,
            },
        )
        self.assertEqual(self.rows("prompts")[variant]["category"], "Other")

    def test_token_changes_with_membership_eligibility_archive_and_target_presence(
        self,
    ):
        components, negative, active, _archived = self.owners()

        def add_owner():
            self.save("Late owner", category="Scenery")

        def archive_pair():
            self.db.archive_prompt_pair(active[0])

        def promote_negative():
            with self.db.model.get_connection() as conn:
                conn.execute(
                    "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                    (negative, "Promoted"),
                )

        def create_target():
            self.save("Target owner", category="Landscape")

        for mutate in (add_owner, archive_pair, promote_negative, create_target):
            with self.subTest(mutate.__name__):
                token = self.preview()["impact_token"]
                mutate()
                before = self.snapshot()
                changes = self.db.model.get_connection().total_changes
                with self.assertRaises(LibraryCommandError) as caught:
                    self.apply(token)
                self.assertEqual(caught.exception.code, "vocabulary-conflict")
                self.assertEqual(caught.exception.epoch, self.epoch)
                self.assertEqual(self.db.model.get_connection().total_changes, changes)
                self.assertEqual(self.snapshot(), before)
        token = self.preview()["impact_token"]
        with self.db.model.get_connection() as conn:
            conn.execute(
                "UPDATE prompts SET notes='edited' WHERE id=?", (components[0],)
            )
        self.assertTrue(self.apply(token)["consolidated"])
        self.assertEqual(self.rows("prompts")[components[0]]["category"], "Landscape")

    def test_category_command_guards(self):
        self.owners()
        before = self.snapshot()
        stale = "00000000-0000-4000-8000-000000000000"
        token = self.preview()["impact_token"]
        with self.assertRaises(LibraryCommandError) as caught:
            self.command(
                "preview_vocabulary_category",
                expected_epoch=stale,
                source="Scenery",
                target="Landscape",
            )
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertEqual(caught.exception.epoch, self.epoch)
        with self.assertRaises(LibraryCommandError) as caught:
            self.command(
                "apply_vocabulary_category",
                expected_epoch=stale,
                source="Scenery",
                target="Landscape",
                impact_token=token,
            )
        self.assertEqual(caught.exception.code, "storage-changed")
        for source in ("Missing", "scenery", " Scenery"):
            with self.assertRaises(LibraryCommandError) as caught:
                self.preview(source=source)
            self.assertEqual(caught.exception.code, "vocabulary-conflict")
        for source, target in (
            ("Scenery", ""),
            ("Scenery", "   "),
            ("Scenery", "x" * 101),
            ("Scenery", "bad\x01"),
            ("Scenery", "Scenery"),
            ("Scenery", " Scenery "),
            ("Scenery", None),
            ("Scenery", 7),
            (None, "Landscape"),
            ("", "Landscape"),
            ("   ", "Landscape"),
        ):
            with self.assertRaises(ValueError, msg=(source, target)):
                self.preview(source=source, target=target)
        for bad in ("", "A" * 64, token[:-1], None, 7):
            with self.assertRaises(ValueError):
                self.apply(bad)
        self.assertEqual(self.snapshot(), before)

    def test_category_apply_failure_rolls_back(self):
        self.owners()
        token = self.preview()["impact_token"]
        with self.db.model.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER abort_pairs BEFORE UPDATE ON prompt_pairs "
                "BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
        before = self.snapshot()
        with self.assertRaises(sqlite3.Error):
            self.apply(token)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("Scenery", [r["category"] for r in before[0].values()])
        self.assertFalse(self.db.model.get_connection().in_transaction)


if __name__ == "__main__":
    unittest.main()
