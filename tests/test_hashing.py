"""
Tests for hashing utilities.

Tests generate_content_hash and is_duplicate_prompt which are
currently untested. generate_prompt_hash is covered in test_basic.py
but we add edge cases here.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.hashing import (
    generate_prompt_hash,
    generate_content_hash,
    is_duplicate_prompt,
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
        tracker.set_current_prompt("  Hello World  ")
        db.get_prompt_by_hash.assert_called_once_with(
            generate_prompt_hash("  Hello World  ")
        )
        self.assertEqual(tracker.get_current_prompt()["id"], 42)

    def test_node_base_has_no_browser_opener(self):
        from prompt_manager_base import PromptManagerBase

        self.assertFalse(hasattr(PromptManagerBase, "_open_web_interface"))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "restart_gallery.py")))


class TestGenerateContentHash(unittest.TestCase):
    """Test generate_content_hash function."""

    def test_basic_content(self):
        content = {
            "text": "A prompt",
            "category": "nature",
            "tags": ["sky"],
            "workflow_name": "wf1",
        }
        h = generate_content_hash(content)
        self.assertEqual(len(h), 64)  # SHA256 hex digest

    def test_normalization_case_insensitive(self):
        c1 = {"text": "Hello World", "category": "Test"}
        c2 = {"text": "hello world", "category": "test"}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_normalization_strips_whitespace(self):
        c1 = {"text": "  hello  ", "category": "  test  "}
        c2 = {"text": "hello", "category": "test"}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_tag_order_independent(self):
        c1 = {"text": "prompt", "tags": ["beta", "alpha"]}
        c2 = {"text": "prompt", "tags": ["alpha", "beta"]}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_different_text_different_hash(self):
        c1 = {"text": "prompt A"}
        c2 = {"text": "prompt B"}
        self.assertNotEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_different_category_different_hash(self):
        c1 = {"text": "same", "category": "cat1"}
        c2 = {"text": "same", "category": "cat2"}
        self.assertNotEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_different_tags_different_hash(self):
        c1 = {"text": "same", "tags": ["tag1"]}
        c2 = {"text": "same", "tags": ["tag2"]}
        self.assertNotEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_empty_dict(self):
        h = generate_content_hash({})
        self.assertEqual(len(h), 64)

    def test_missing_fields_use_defaults(self):
        c1 = {"text": "hello"}
        c2 = {"text": "hello", "category": None, "tags": [], "workflow_name": None}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_none_category_same_as_empty(self):
        c1 = {"text": "test", "category": None}
        c2 = {"text": "test", "category": ""}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_none_workflow_same_as_empty(self):
        c1 = {"text": "test", "workflow_name": None}
        c2 = {"text": "test", "workflow_name": ""}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_empty_tags_filtered(self):
        c1 = {"text": "test", "tags": ["valid", "", "  "]}
        c2 = {"text": "test", "tags": ["valid"]}
        self.assertEqual(generate_content_hash(c1), generate_content_hash(c2))

    def test_deterministic(self):
        content = {"text": "stable", "tags": ["a", "b"]}
        h1 = generate_content_hash(content)
        h2 = generate_content_hash(content)
        self.assertEqual(h1, h2)


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


if __name__ == "__main__":
    unittest.main()
