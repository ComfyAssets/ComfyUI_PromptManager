"""Skipped tags (#431): the words whose suggested tags are never offered.

The normalizer, the entry validation, the whole-word matcher, the WD14 label
reader and the filter in ``AutoTagService.generate_tag_suggestions``. No model
is loaded: ``_scored_tags`` is replaced by a fixed list.
"""

import os
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from py.tagging_defaults import (
    MAX_SKIPPED_TAG_CHARS,
    MAX_SKIPPED_TAGS,
    SkipMatcher,
    tag_words,
    validate_skipped_tags,
    wd14_label_names,
)

# The design's matching table: (entry, tag, hidden).
MATCHING = (
    ("blurry", "blurry", True),
    ("blurry", "blurry_background", True),
    ("blurry", "very_blurry", True),
    ("blurry", "slightly_blurry_background", True),
    ("blurry", "unblurry", False),
    ("blur", "blurry", False),
    ("blurry background", "blurry_background", True),
    ("blurry background", "Blurry-Background", True),
    ("background blurry", "blurry_background", False),
    ("shirt", "t-shirt", True),
    ("shirt", "shirt_lift", True),
    ("closed eyes", "half-closed_eyes", True),
    ("girl", "1girl", False),
    ("girl", "2girls", False),
    ("girls", "multiple_girls", True),
    ("fate", "saber_(fate)", False),
    ("(fate)", "saber_(fate)", True),
    ("saber", "saber_(fate)", True),
    (":d", ":d", True),
    ("café", unicodedata.normalize("NFD", "café"), True),
)


class TestWords(unittest.TestCase):
    def test_words_split_only_on_space_underscore_and_hyphen(self):
        self.assertEqual(tag_words("Half-closed_eyes"), ("half", "closed", "eyes"))
        self.assertEqual(tag_words("saber_(fate)"), ("saber", "(fate)"))
        self.assertEqual(tag_words("  a__b--c  d "), ("a", "b", "c", "d"))
        self.assertEqual(tag_words("_-- _"), ())
        self.assertEqual(tag_words(":d"), (":d",))

    def test_words_are_nfc_and_case_folded(self):
        self.assertEqual(
            tag_words("CAFÉ"), tag_words(unicodedata.normalize("NFD", "café"))
        )
        self.assertEqual(tag_words("Straße"), ("strasse",))


class TestMatcher(unittest.TestCase):
    def test_every_row_of_the_matching_table(self):
        for entry, tag, hidden in MATCHING:
            with self.subTest(entry=entry, tag=tag):
                self.assertIs(SkipMatcher([entry]).matches(tag), hidden)

    def test_a_tag_without_words_never_matches(self):
        matcher = SkipMatcher(["blurry"])
        for tag in ("", "_", "- _"):
            with self.subTest(tag=tag):
                self.assertFalse(matcher.matches(tag))

    def test_an_empty_matcher_hides_nothing_and_is_false(self):
        self.assertFalse(SkipMatcher())
        self.assertFalse(SkipMatcher([]).matches("blurry"))
        self.assertTrue(SkipMatcher(["blurry"]))

    def test_entries_of_different_lengths_are_all_checked(self):
        matcher = SkipMatcher(["sky", "closed eyes", "very long entry here"])
        self.assertTrue(matcher.matches("blue_sky"))
        self.assertTrue(matcher.matches("half-closed_eyes"))
        self.assertTrue(matcher.matches("a_very_long_entry_here_b"))
        self.assertFalse(matcher.matches("very_long_entry"))

    def test_counting_view_records_the_distinct_names_it_hid(self):
        counting = SkipMatcher(["blurry"]).counting()
        self.assertTrue(counting.matches("blurry"))
        self.assertTrue(counting.matches("Blurry"))
        self.assertTrue(counting.matches("blurry_background"))
        self.assertFalse(counting.matches("sky"))
        self.assertEqual(counting.hidden, {"blurry", "blurry_background"})


class TestValidation(unittest.TestCase):
    def test_entries_are_trimmed_and_kept_as_typed(self):
        self.assertEqual(
            validate_skipped_tags(["  Blurry ", "closed eyes", "(fate)"]),
            ["Blurry", "closed eyes", "(fate)"],
        )
        self.assertEqual(validate_skipped_tags([]), [])

    def test_caps_count_code_points_after_trimming(self):
        self.assertEqual(
            validate_skipped_tags([" " + "🏜" * MAX_SKIPPED_TAG_CHARS + " "]),
            ["🏜" * MAX_SKIPPED_TAG_CHARS],
        )
        entries = [f"word{index}" for index in range(MAX_SKIPPED_TAGS)]
        self.assertEqual(validate_skipped_tags(entries), entries)

    def test_every_refusal_sentence(self):
        cases = (
            ("blurry", "Skipped tags must be a list of words."),
            (
                [f"w{index}" for index in range(MAX_SKIPPED_TAGS + 1)],
                "Skipped tags: at most 200 entries.",
            ),
            (["ok", 3], "Skipped tags: entry 2 must be text."),
            (["ok", "   "], "Skipped tags: entry 2 is empty."),
            (["_ --"], "Skipped tags: entry 1 is empty."),
            (["x" * 51], "Skipped tags: entry 1 must be at most 50 characters."),
            (
                ["a, b"],
                "Skipped tags: entry 1 must not contain a comma or a line break.",
            ),
            (
                ["a\nb"],
                "Skipped tags: entry 1 must not contain a comma or a line break.",
            ),
            (
                ["a\x7fb"],
                "Skipped tags: entry 1 must not contain a comma or a line break.",
            ),
            (
                ["blurry", "sky", "Blurry"],
                "Skipped tags: entry 3 repeats entry 1.",
            ),
            (
                ["blurry background", "blurry-Background"],
                "Skipped tags: entry 2 repeats entry 1.",
            ),
        )
        for value, message in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    validate_skipped_tags(value)
                self.assertEqual(str(caught.exception), message)


class TestWd14Labels(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "selected_tags.csv"

    def write(self, rows):
        lines = ["tag_id,name,category,count"]
        lines += [f"{i},{name},{category},1" for i, (name, category) in enumerate(rows)]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_only_general_and_character_names_are_read(self):
        self.write(
            [
                ("blurry", "0"),
                ("general", "9"),
                ("saber_(fate)", "4"),
                ("artist", "1"),
                ("café", "0"),
            ]
        )
        self.assertEqual(
            wd14_label_names(self.path), ("blurry", "saber_(fate)", "café")
        )

    def test_the_list_is_read_again_only_when_the_file_changes(self):
        self.write([("blurry", "0")])
        with patch(
            "py.tagging_defaults.read_wd14_label_rows",
            wraps=__import__(
                "py.tagging_defaults", fromlist=["x"]
            ).read_wd14_label_rows,
        ) as reader:
            self.assertEqual(wd14_label_names(self.path), ("blurry",))
            self.assertEqual(wd14_label_names(self.path), ("blurry",))
            self.assertEqual(reader.call_count, 1)
            self.write([("blurry", "0"), ("sky", "0")])
            os.utime(self.path, ns=(1, 2))
            self.assertEqual(wd14_label_names(self.path), ("blurry", "sky"))
            self.assertEqual(reader.call_count, 2)

    def test_a_missing_list_raises_for_the_caller_to_skip(self):
        with self.assertRaises(OSError):
            wd14_label_names(self.path)


class FilterService:
    """``AutoTagService`` with a fixed model output instead of a model."""

    def __init__(self, scored):
        from py.autotag import AutoTagService

        self.service = AutoTagService.__new__(AutoTagService)
        self.service._scored_tags = lambda *args, **kwargs: list(scored)

    def suggest(self, limit, skip=None):
        return self.service.generate_tag_suggestions(
            "image.png", limit=limit, skip=skip
        )


class TestFilter(unittest.TestCase):
    def test_skipped_tags_do_not_use_up_slots(self):
        """WD14: the next-best tags above the thresholds fill the limit."""
        scored = [
            ("blurry", 0.99),
            ("sky", 0.9),
            ("blurry_background", 0.8),
            ("cloud", 0.7),
            ("sun", 0.6),
            ("sea", 0.5),
        ]
        service = FilterService(scored)
        self.assertEqual(
            service.suggest(3, SkipMatcher(["blurry"])),
            [("sky", 0.9), ("cloud", 0.7), ("sun", 0.6)],
        )
        self.assertEqual(service.suggest(3), scored[:3])

    def test_a_language_model_may_return_fewer_than_the_limit(self):
        service = FilterService([("blurry", None), ("sky", None), ("signature", None)])
        self.assertEqual(
            service.suggest(10, SkipMatcher(["blurry", "signature"])), [("sky", None)]
        )

    def test_order_is_invalid_name_then_skip_then_dedupe_then_limit(self):
        hidden = []

        class Recording:
            def matches(self, name):
                hidden.append(name)
                return name.startswith("skip")

        service = FilterService(
            [
                ("bad,name", None),
                ("  Sky ", None),
                ("skip_me", None),
                ("sky", None),
                ("cloud", None),
                ("sun", None),
            ]
        )
        self.assertEqual(
            service.suggest(2, Recording()), [("Sky", None), ("cloud", None)]
        )
        # The refused name never reaches the matcher; trimmed names do; the
        # duplicate is checked after the skip; nothing after the limit.
        self.assertEqual(hidden, ["Sky", "skip_me", "sky", "cloud"])

    def test_the_counting_view_reports_hidden_names(self):
        counting = SkipMatcher(["blurry"]).counting()
        FilterService([("blurry", 0.9), ("sky", 0.8), ("Blurry", 0.7)]).suggest(
            5, counting
        )
        self.assertEqual(counting.hidden, {"blurry"})


if __name__ == "__main__":
    unittest.main()
