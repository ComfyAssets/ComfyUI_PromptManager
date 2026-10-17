"""Autocomplete shares vocabulary without mixing pair and prompt identities."""

import os
import tempfile
import unittest

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestPairVocabulary(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = PromptDatabase(os.path.join(directory.name, "vocabulary.db"))
        self.addCleanup(self.db.close_all)

    def prompt(self, text: str, **kwargs) -> int:
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **kwargs
        )

    def suggestions(self, prefix="", context=None, limit=20):
        return self.db.suggest_tags(prefix, context, limit)

    def test_pair_only_categories_and_tags_are_available_without_component_metadata(
        self,
    ):
        pair = self.db.get_prompt_pair(
            self.db.save_prompt_pair(
                "portrait", "blurry", category="Pairs", tags=["studio"]
            )
        )
        self.assertEqual(self.db.get_all_categories(), ["Pairs"])
        self.assertEqual(self.db.get_all_tags(), ["studio"])
        self.assertEqual(self.suggestions("stu"), [{"name": "studio", "count": 1}])
        for name in ("positive_prompt_id", "negative_prompt_id"):
            component = self.db.get_prompt_by_id(pair[name])
            self.assertIsNone(component["category"])
            self.assertEqual(component["tags"], [])

    def test_categories_are_trimmed_deduplicated_and_include_archived_pairs(self):
        self.prompt("standalone", category=" Portrait ")
        pair = self.db.save_prompt_pair(
            "portrait", "", category="Portrait", tags=["archived"]
        )
        self.db.archive_prompt_pair(pair)
        self.db.save_prompt_pair("landscape", "", category=" Landscape ")
        self.db.save_prompt_pair("empty", "", category=" ")
        self.assertEqual(self.db.get_all_categories(), ["Landscape", "Portrait"])
        self.assertEqual(self.db.get_all_tags(), ["archived"])
        self.assertEqual(self.suggestions("arc"), [{"name": "archived", "count": 1}])

    def test_counts_include_distinct_pair_and_prompt_references(self):
        self.prompt("portrait", tags=["shared", "legacy"])
        self.db.save_prompt_pair(
            "portrait", "first negative", tags=["shared", "paired"]
        )
        self.db.save_prompt_pair("portrait", "second negative", tags=["shared"])
        self.assertEqual(self.suggestions("sha"), [{"name": "shared", "count": 3}])
        self.assertEqual(
            self.suggestions("sha", ["paired"]), [{"name": "shared", "count": 1}]
        )
        self.assertEqual(
            self.suggestions("sha", ["legacy"]), [{"name": "shared", "count": 1}]
        )

    def test_context_does_not_join_colliding_pair_and_prompt_ids(self):
        prompt = self.prompt("portrait", tags=["legacy-context", "legacy-only"])
        pair = self.db.save_prompt_pair(
            "portrait", "blurry", tags=["pair-context", "pair-only"]
        )
        self.assertEqual(prompt, pair)
        self.assertEqual(
            self.suggestions("", ["legacy-context"]),
            [{"name": "legacy-only", "count": 1}],
        )
        self.assertEqual(
            self.suggestions("", ["pair-context"]), [{"name": "pair-only", "count": 1}]
        )
        self.assertEqual(self.suggestions("", ["legacy-context", "pair-context"]), [])

    def test_pair_context_matches_all_tags_case_insensitively(self):
        self.db.save_prompt_pair("portrait", "blurry", tags=["One", "two", "three"])
        self.db.save_prompt_pair("other", "blurry", tags=["One", "extra"])
        self.assertEqual(
            self.suggestions("TH", ["one", "TWO", "one"]),
            [{"name": "three", "count": 1}],
        )
        self.assertEqual(self.suggestions("", ["missing"]), [])

    def test_pair_prefix_wildcards_are_literal_and_limit_is_preserved(self):
        self.db.save_prompt_pair(
            "portrait", "", tags=["tag_one", "tag_two", "tag%literal"]
        )
        self.assertEqual(
            self.suggestions("tag_", limit=1), [{"name": "tag_one", "count": 1}]
        )
        self.assertEqual(
            self.suggestions("tag%"), [{"name": "tag%literal", "count": 1}]
        )
        self.assertEqual(self.suggestions("%"), [])


if __name__ == "__main__":
    unittest.main()
