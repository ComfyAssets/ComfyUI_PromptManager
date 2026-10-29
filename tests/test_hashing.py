"""
Tests for hashing utilities.

Tests is_duplicate_prompt and generate_prompt_hash, which are
currently untested. generate_prompt_hash is covered in test_basic.py
but we add edge cases here.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.background_stops import stop_tracker  # noqa: E402
from utils.hashing import (
    generate_prompt_hash,
    is_duplicate_prompt,
    STRIPPED_CODE_POINTS,
    stored_prompt_text,
    stored_prompt_text_sql,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSingleHashImplementation(unittest.TestCase):
    """generate_prompt_hash is the only prompt hash; nothing re-implements it."""

    def test_node_base_delegates_to_generate_prompt_hash(self):
        from prompt_manager_base import PromptManagerBase

        node = PromptManagerBase.__new__(PromptManagerBase)
        self.assertEqual(node._generate_hash("  A  "), generate_prompt_hash("  A  "))
        self.assertEqual(node._generate_hash("  A  "), generate_prompt_hash("a"))

    def test_tracker_and_monitor_use_generate_prompt_hash(self):
        for relative in (
            os.path.join("utils", "prompt_tracker.py"),
            os.path.join("utils", "image_monitor.py"),
            "prompt_manager_base.py",
        ):
            with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
                source = f.read()
            self.assertFalse("hashlib.sha256" in source, f"inline hash in {relative}")
            self.assertTrue("generate_prompt_hash" in source, relative)

    def test_tracker_finds_an_existing_prompt_by_the_shared_hash(self):
        import unittest.mock as mock

        from utils.prompt_tracker import PromptTracker

        db = mock.Mock()
        db.get_prompt_by_hash.return_value = {"id": 42}
        tracker = PromptTracker(db)
        self.addCleanup(stop_tracker, self, tracker)
        tracker.set_current_prompt("  Hello World  ")
        db.get_prompt_by_hash.assert_called_once_with(
            generate_prompt_hash("  Hello World  ")
        )
        self.assertEqual(tracker.get_current_prompt()["id"], 42)

    def test_node_base_has_no_browser_opener(self):
        from prompt_manager_base import PromptManagerBase

        self.assertFalse(hasattr(PromptManagerBase, "_open_web_interface"))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "restart_gallery.py")))


class TestIsDuplicatePrompt(unittest.TestCase):
    """Test is_duplicate_prompt function."""

    def test_identical_texts_are_duplicates(self):
        self.assertTrue(is_duplicate_prompt("hello", "hello"))

    def test_case_insensitive(self):
        self.assertTrue(is_duplicate_prompt("Hello World", "hello world"))

    def test_whitespace_insensitive(self):
        self.assertTrue(is_duplicate_prompt("  hello  ", "hello"))

    def test_different_texts_not_duplicates(self):
        self.assertFalse(is_duplicate_prompt("text A", "text B"))

    def test_threshold_parameter_accepted(self):
        # threshold is unused but should not cause errors
        self.assertTrue(is_duplicate_prompt("same", "same", threshold=0.5))

    def test_empty_strings_are_duplicates(self):
        self.assertTrue(is_duplicate_prompt("", ""))

    def test_unicode_comparison(self):
        self.assertTrue(is_duplicate_prompt("日本語", "日本語"))
        self.assertFalse(is_duplicate_prompt("日本語", "中文"))


class TestGeneratePromptHashEdgeCases(unittest.TestCase):
    """Additional edge cases for generate_prompt_hash."""

    def test_non_string_raises_type_error(self):
        with self.assertRaises(TypeError):
            generate_prompt_hash(123)

    def test_none_raises_type_error(self):
        with self.assertRaises(TypeError):
            generate_prompt_hash(None)

    def test_empty_string(self):
        h = generate_prompt_hash("")
        self.assertEqual(len(h), 64)

    def test_unicode_hash(self):
        h = generate_prompt_hash("émojis 🎨 日本語")
        self.assertEqual(len(h), 64)

    def test_very_long_text(self):
        h = generate_prompt_hash("x" * 100000)
        self.assertEqual(len(h), 64)


class TestStoredPromptText(unittest.TestCase):
    """SQLite strips exactly the characters Python's str.strip() does (#438)."""

    WHITESPACE = "".join(c for c in map(chr, range(0x110000)) if c.isspace())

    def sql_strip(self, value):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            return conn.execute(
                f"SELECT {stored_prompt_text_sql('?')}", (value,)
            ).fetchone()[0]
        finally:
            conn.close()

    def test_code_points_are_exactly_python_whitespace(self):
        self.assertEqual("".join(map(chr, STRIPPED_CODE_POINTS)), self.WHITESPACE)

    def test_python_rule_is_the_hash_strip(self):
        self.assertEqual(stored_prompt_text("  a lighthouse\n"), "a lighthouse")
        self.assertEqual(
            generate_prompt_hash(stored_prompt_text(" A b\u3000")),
            generate_prompt_hash(" A b\u3000"),
        )

    def test_sql_strips_every_python_whitespace_character(self):
        for char in self.WHITESPACE:
            with self.subTest(code=hex(ord(char))):
                value = f"{char}a{char}b{char}"
                self.assertEqual(self.sql_strip(value), stored_prompt_text(value))
        mixed = self.WHITESPACE + "a lighthouse é" + self.WHITESPACE
        self.assertEqual(self.sql_strip(mixed), "a lighthouse é")

    def test_sql_keeps_what_python_keeps(self):
        for value in ("\u200ba\u200b", "\ufeffa", "a\u0301", "", "x"):
            with self.subTest(value=repr(value)):
                self.assertEqual(self.sql_strip(value), stored_prompt_text(value))


if __name__ == "__main__":
    unittest.main()
