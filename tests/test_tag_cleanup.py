"""Tag cleanup rules (#618, ``database/tag_cleanup.py``).

Two rules ship: tags containing a comma (removed from every owner and
deleted; a ``lora:`` tag is never one) and tags with no links (deleted). A
tag belongs to the first rule that finds it. A find reads one snapshot; a
plan re-reads each selected finding and protects the ones that changed; the
apply rechecks every target in the caller's transaction and keeps the ones
that changed, with their reason, while the rest still apply.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import tag_cleanup
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed


class TagCleanupCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name))
        self.db_path = str(self.root / "prompts.db")
        self.db = PromptDatabase(self.db_path)
        self.epoch = self.db.get_execution_epoch()

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.root)
        self.temp.cleanup()

    def save(self, text, tags):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), tags=tags
        )

    def add_unused(self, *names):
        with self.db.model.get_connection() as conn:
            for name in names:
                conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))

    def image(self, name, tags, digest="a"):
        locator = os.path.normcase(str(self.root / name))
        self.db.replace_image_tags(
            locator=locator,
            sha256=hashlib.sha256(digest.encode()).hexdigest(),
            size=1,
            mtime_ns=1,
            expected_epoch=self.db.get_execution_epoch(),
            expected_tag_revision=0,
            tags=tags,
        )
        return locator

    def image_with(self, name, tag):
        """An image set carrying ``tag``, which the image API refuses when it
        holds a comma: the link row is written as older builds could."""
        self.image(name, ["placeholder"], digest=name)
        with self.db.model.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
            conn.execute(
                "INSERT INTO media_image_tags(byte_id, tag_id, position) "
                "SELECT b.id, t.id, 1 FROM media_annotation_bytes b, tags t "
                "WHERE t.name=? AND b.sha256=?",
                (tag, hashlib.sha256(name.encode()).hexdigest()),
            )

    def tag_id(self, name):
        with self.db.model.get_connection() as conn:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def names(self):
        with self.db.model.get_connection() as conn:
            return sorted(r[0] for r in conn.execute("SELECT name FROM tags"))

    def find(self):
        return tag_cleanup.find_tag_cleanup(self.db_path)

    def found(self, rule):
        return [f["name"] for f in self.find()["rules"][rule]]

    def plan(self, result, select):
        return tag_cleanup.plan_tag_cleanup(self.db_path, result, select)

    def apply(self, targets):
        with self.db._pair_transaction() as conn:
            return tag_cleanup.apply_tag_cleanup(conn, targets)

    def select_all(self, result, rule):
        return {rule: [f["tag_id"] for f in result["rules"][rule]]}


class TestRegistry(TagCleanupCase):
    def test_rules_ship_in_order_with_their_copy(self):
        self.assertEqual([r.id for r in tag_cleanup.RULES], ["comma", "unused"])
        comma, unused = tag_cleanup.RULES
        self.assertEqual(comma.title, "Tags containing a comma")
        self.assertEqual(unused.title, "Tags with no links")
        for rule in tag_cleanup.RULES:
            for text in (rule.explanation, rule.action_label):
                self.assertTrue(text and "\n" not in text)
        self.assertIs(comma.apply, unused.apply)


class TestFinders(TagCleanupCase):
    def test_comma_rule_finds_ascii_and_fullwidth_commas(self):
        self.save("a", ["score_9, score_8_up", "clean"])
        self.save("b", ["赛博朋克，霓虹", "realistic,"])
        self.assertEqual(
            self.found("comma"), ["realistic,", "score_9, score_8_up", "赛博朋克，霓虹"]
        )

    def test_comma_rule_skips_lora_identity_tags(self):
        # Maintainer ruling Q1: a lora: tag is never a comma finding.
        self.save("a", ["lora:Style, v2", "a, b"])
        self.add_unused("lora:Old, model")
        found = self.find()
        self.assertEqual([f["name"] for f in found["rules"]["comma"]], ["a, b"])
        # The unused rule still lists an unused lora: tag.
        self.assertEqual(
            [f["name"] for f in found["rules"]["unused"]], ["lora:Old, model"]
        )

    def test_unused_rule_counts_prompt_pair_and_image_links(self):
        self.save("a", ["on-prompt"])
        self.db.save_prompt_pair("pos", "neg", tags=["on-pair"])
        self.image("one.png", ["on-image"])
        self.add_unused("nowhere", "Also nowhere")
        self.assertEqual(self.found("unused"), ["Also nowhere", "nowhere"])

    def test_an_autotag_mark_is_not_a_link(self):
        self.image("one.png", [], digest="z")
        with self.db.model.get_connection() as conn:
            byte = conn.execute("SELECT id FROM media_annotation_bytes").fetchone()[0]
            conn.execute("INSERT INTO media_image_autotags(byte_id) VALUES(?)", (byte,))
        self.add_unused("lonely")
        self.assertEqual(self.found("unused"), ["lonely"])

    def test_a_comma_tag_without_links_is_listed_once_under_comma(self):
        self.add_unused("a, b", "plain")
        found = self.find()
        self.assertEqual([f["name"] for f in found["rules"]["comma"]], ["a, b"])
        self.assertEqual([f["name"] for f in found["rules"]["unused"]], ["plain"])
        self.assertEqual(found["totals"], {"comma": 1, "unused": 1})

    def test_findings_carry_counts_saved_views_and_a_links_hash(self):
        prompt = self.save("a", ["x, y"])
        self.save("b", ["x, y"])
        self.db.save_prompt_pair("pos", "neg", tags=["x, y"])
        self.image_with("one.png", "x, y")
        self.add_unused("ghost")
        self.db.create_saved_view(
            "images",
            "Ghosts",
            {
                "q": "",
                "category": "",
                "sort": "newest",
                "source": "all",
                "folder": "",
                "media": "all",
                "tags": ["ghost"],
                "favorites": False,
                "collection": "",
            },
        )
        found = self.find()
        [comma] = found["rules"]["comma"]
        self.assertEqual(
            {k: comma[k] for k in ("rule", "name", "prompts", "pairs", "images")},
            {"rule": "comma", "name": "x, y", "prompts": 2, "pairs": 1, "images": 1},
        )
        self.assertEqual(comma["saved_views"], 0)
        self.assertEqual(comma["tag_id"], self.tag_id("x, y"))
        self.assertRegex(comma["links"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            tag_cleanup.finding_detail(comma), "2 prompts · 1 pair · 1 image"
        )
        [ghost] = found["rules"]["unused"]
        self.assertEqual(ghost["saved_views"], 1)
        self.assertEqual(ghost["links"], tag_cleanup.NO_LINKS)
        self.assertEqual(
            tag_cleanup.finding_detail(ghost), "no links · in 1 saved view"
        )
        self.assertIsNotNone(prompt)

    def test_cap_holds_the_first_findings_by_name_and_totals_stay_exact(self):
        self.add_unused("b", "A", "c", "d")
        with patch.object(tag_cleanup, "MAX_FINDINGS", 2):
            found = self.find()
        self.assertEqual([f["name"] for f in found["rules"]["unused"]], ["A", "b"])
        self.assertEqual(found["totals"]["unused"], 4)

    def test_find_writes_nothing_and_closes_its_connection(self):
        self.add_unused("a, b")
        before = self.names()
        with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
            self.find()
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(self.names(), before)


class TestPlan(TagCleanupCase):
    def test_plan_lists_targets_scope_and_details(self):
        self.save("a", ["x, y"])
        self.save("b", ["x, y", "q, r"])
        self.add_unused("ghost")
        found = self.find()
        draft = self.plan(found, self.select_all(found, "comma"))
        self.assertEqual(draft.operation, "tag_cleanup")
        self.assertEqual(
            [(t["rule"], t["name"]) for t in draft.targets],
            [("comma", "q, r"), ("comma", "x, y")],
        )
        self.assertEqual(
            draft.scope,
            {
                "count": 2,
                "label": "Delete 2 tags (removed from 3 prompts, 0 pairs and 0 images)",
            },
        )
        self.assertEqual(draft.protected, {"count": 0, "reasons": []})
        self.assertEqual(
            draft.media, {"deletes_files": False, "label": "No files are removed"}
        )
        self.assertEqual(
            draft.details[0],
            {"label": "q, r", "detail": "Tags containing a comma · 1 prompt"},
        )
        self.assertTrue(draft.fingerprint.startswith("sha256:"))

    def test_one_tag_reads_in_the_singular(self):
        self.add_unused("ghost")
        found = self.find()
        draft = self.plan(found, self.select_all(found, "unused"))
        self.assertEqual(
            draft.scope["label"],
            "Delete 1 tag (removed from 0 prompts, 0 pairs and 0 images)",
        )

    def test_a_finding_changed_since_the_search_is_protected(self):
        first = self.save("a", ["x, y"])
        second = self.save("b", ["q, r"])
        found = self.find()
        # Moved to another prompt: the count stays 1, the links differ.
        with self.db.model.get_connection() as conn:
            conn.execute(
                "UPDATE prompt_tags SET prompt_id=? WHERE prompt_id=? AND tag_id=?",
                (second, first, self.tag_id("x, y")),
            )
        draft = self.plan(found, self.select_all(found, "comma"))
        self.assertEqual([t["name"] for t in draft.targets], ["q, r"])
        self.assertEqual(
            draft.protected,
            {"count": 1, "reasons": ["1 tag changed since the search"]},
        )
        self.assertEqual(
            [(h["name"], h["reason"]) for h in tag_cleanup.held_rows(draft)],
            [("x, y", "changed since the search")],
        )

    def test_a_renamed_or_deleted_finding_is_protected(self):
        self.add_unused("ghost", "spirit")
        found = self.find()
        with self.db.model.get_connection() as conn:
            conn.execute("UPDATE tags SET name='GHOST' WHERE name='ghost'")
            conn.execute("DELETE FROM tags WHERE name='spirit'")
        draft = self.plan(found, self.select_all(found, "unused"))
        self.assertEqual(draft.targets, [])
        self.assertEqual(
            draft.protected["reasons"], ["2 tags changed since the search"]
        )


class TestApply(TagCleanupCase):
    def planned(self, rule):
        found = self.find()
        return self.plan(found, self.select_all(found, rule)).targets

    def test_apply_deletes_the_tag_and_its_three_kinds_of_links(self):
        prompt = self.save("a", ["x, y", "keep"])
        pair = self.db.save_prompt_pair("pos", "neg", tags=["x, y"])
        self.image_with("one.png", "x, y")
        with self.db.model.get_connection() as conn:
            byte = conn.execute("SELECT id FROM media_annotation_bytes").fetchone()[0]
            conn.execute("INSERT INTO media_image_autotags(byte_id) VALUES(?)", (byte,))
            revision = conn.execute(
                "SELECT image_index_revision FROM media_annotation_metadata"
            ).fetchone()[0]
        targets = self.planned("comma")
        result = self.apply(targets)
        self.assertEqual(result["changed"], 1)
        self.assertEqual([r["name"] for r in result["rows"]], ["x, y"])
        self.assertEqual(result["kept"], [])
        self.assertIsNone(self.tag_id("x, y"))
        with self.db.model.get_connection() as conn:
            for table in ("prompt_tags", "prompt_pair_tags", "media_image_tags"):
                names = conn.execute(
                    f"SELECT t.name FROM {table} x JOIN tags t ON t.id=x.tag_id"
                ).fetchall()
                self.assertNotIn(("x, y",), [tuple(n) for n in names], table)
            self.assertEqual(
                conn.execute(
                    "SELECT tag_revision FROM media_annotation_bytes"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT image_index_revision FROM media_annotation_metadata"
                ).fetchone()[0],
                revision + 1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM media_image_autotags").fetchone()[0],
                1,
            )
        self.assertIsNotNone(self.db.get_prompt_by_id(prompt))
        self.assertIsNotNone(self.db.get_prompt_pair(pair))
        self.assertIn("keep", self.names())

    def test_saved_views_are_never_rewritten(self):
        self.add_unused("ghost")
        state = {
            "q": "",
            "category": "",
            "sort": "newest",
            "tags": ["ghost"],
            "tag_mode": "all",
            "folder": "",
        }
        self.db.create_saved_view("library", "Ghosts", state)
        self.apply(self.planned("unused"))
        [view] = self.db.list_saved_views()
        self.assertEqual(view["state"]["tags"], ["ghost"])

    def test_each_recheck_keeps_the_target_with_its_reason(self):
        self.add_unused("gone", "renamed", "used")
        first = self.save("first", ["moved, tag"])
        second = self.save("second", ["anchor"])
        self.save("third", ["relinked, tag"])
        unused = self.planned("unused")
        comma = self.planned("comma")
        with self.db.model.get_connection() as conn:
            conn.execute("DELETE FROM tags WHERE name='gone'")
            conn.execute("UPDATE tags SET name='Renamed' WHERE name='renamed'")
            conn.execute(
                "INSERT INTO prompt_tags(prompt_id, tag_id) VALUES(?, ?)",
                (first, self.tag_id("used")),
            )
            # Same count, other prompt: its links changed.
            conn.execute(
                "UPDATE prompt_tags SET prompt_id=? WHERE prompt_id=? AND tag_id=?",
                (second, first, self.tag_id("moved, tag")),
            )
        result = self.apply(unused + comma)
        kept = {k["name"]: k["reason"] for k in result["kept"]}
        self.assertEqual(
            kept,
            {
                "gone": "already deleted",
                "renamed": "renamed since the review",
                "used": "it is used now",
                "moved, tag": "its links changed since the review",
            },
        )
        # The rest of the plan still applied.
        self.assertEqual([r["name"] for r in result["rows"]], ["relinked, tag"])
        self.assertEqual(result["changed"], 1)
        self.assertIsNone(self.tag_id("relinked, tag"))

    def test_the_last_tag_is_kept_while_legacy_json_tags_remain(self):
        self.add_unused("only")
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(text, tags) VALUES('legacy', ?)",
                (json.dumps(["old"]),),
            )
        result = self.apply(self.planned("unused"))
        self.assertEqual(
            [(k["name"], k["reason"]) for k in result["kept"]],
            [("only", "kept so the old tag migration does not run again")],
        )
        self.assertEqual(self.names(), ["only"])

    def test_the_last_tag_goes_when_no_legacy_tags_remain(self):
        self.add_unused("only")
        result = self.apply(self.planned("unused"))
        self.assertEqual(result["changed"], 1)
        self.assertEqual(self.names(), [])

    def test_an_exception_rolls_everything_back(self):
        self.add_unused("a", "b")
        targets = self.planned("unused")
        calls = []
        real = tag_cleanup._delete_target

        def fail_second(conn, target):
            calls.append(target["name"])
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            real(conn, target)

        with patch.object(tag_cleanup, "_delete_target", fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.apply(targets)
        self.assertEqual(self.names(), ["a", "b"])

    def test_names_with_quotes_and_percent_signs(self):
        name = 'it\'s 100% "real", ok'
        self.save("a", [name])
        self.add_unused("50%_off")
        self.assertEqual(self.found("comma"), [name])
        self.assertEqual(self.found("unused"), ["50%_off"])
        self.apply(self.planned("comma") + self.planned("unused"))
        self.assertEqual(self.names(), [])


if __name__ == "__main__":
    unittest.main()
