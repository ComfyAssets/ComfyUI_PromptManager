"""
Comprehensive tests for input validation utilities.

Tests all 7 validator functions in utils/validators.py including
edge cases, boundary conditions, and security-related inputs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.validators import (
    validate_prompt_text,
    validate_rating,
    validate_tags,
    validate_category,
    validate_workflow_name,
    sanitize_input,
    parse_tags_string,
)


class TestValidateCategory(unittest.TestCase):
    """Test validate_category function."""

    def test_none_is_valid(self):
        self.assertTrue(validate_category(None))

    def test_empty_string_is_valid(self):
        self.assertTrue(validate_category(""))

    def test_whitespace_only_is_valid(self):
        self.assertTrue(validate_category("   "))

    def test_normal_category(self):
        self.assertTrue(validate_category("landscape"))

    def test_category_with_spaces(self):
        self.assertTrue(validate_category("nature photos"))

    def test_category_with_hyphens_underscores(self):
        self.assertTrue(validate_category("sci-fi_art"))

    def test_unicode_category(self):
        self.assertTrue(validate_category("日本語カテゴリ"))

    def test_max_length_boundary(self):
        self.assertTrue(validate_category("x" * 100))

    def test_exceeds_max_length(self):
        with self.assertRaises(ValueError) as ctx:
            validate_category("x" * 101)
        self.assertIn("100", str(ctx.exception))

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            validate_category(123)

    def test_list_raises(self):
        with self.assertRaises(ValueError):
            validate_category(["category"])

    def test_control_characters_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_category("bad\x00category")
        self.assertIn("control characters", str(ctx.exception))

    def test_tab_character_rejected(self):
        with self.assertRaises(ValueError):
            validate_category("bad\tcategory")

    def test_newline_rejected(self):
        with self.assertRaises(ValueError):
            validate_category("bad\ncategory")


class TestValidateWorkflowName(unittest.TestCase):
    """Test validate_workflow_name function."""

    def test_none_is_valid(self):
        self.assertTrue(validate_workflow_name(None))

    def test_empty_string_is_valid(self):
        self.assertTrue(validate_workflow_name(""))

    def test_whitespace_only_is_valid(self):
        self.assertTrue(validate_workflow_name("   "))

    def test_normal_name(self):
        self.assertTrue(validate_workflow_name("My Workflow"))

    def test_name_with_special_chars(self):
        self.assertTrue(validate_workflow_name("workflow-v2_final (copy)"))

    def test_max_length_boundary(self):
        self.assertTrue(validate_workflow_name("x" * 200))

    def test_exceeds_max_length(self):
        with self.assertRaises(ValueError) as ctx:
            validate_workflow_name("x" * 201)
        self.assertIn("200", str(ctx.exception))

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            validate_workflow_name(42)

    def test_dict_raises(self):
        with self.assertRaises(ValueError):
            validate_workflow_name({"name": "workflow"})


class TestSanitizeInput(unittest.TestCase):
    """Test sanitize_input function."""

    def test_normal_text_unchanged(self):
        self.assertEqual(sanitize_input("hello world"), "hello world")

    def test_strips_outer_whitespace(self):
        self.assertEqual(sanitize_input("  hello  "), "hello")

    def test_removes_null_bytes(self):
        self.assertEqual(sanitize_input("hello\x00world"), "helloworld")

    def test_normalizes_windows_line_endings(self):
        self.assertEqual(sanitize_input("line1\r\nline2"), "line1\nline2")

    def test_normalizes_old_mac_line_endings(self):
        self.assertEqual(sanitize_input("line1\rline2"), "line1\nline2")

    def test_strips_whitespace_per_line(self):
        result = sanitize_input("  line1  \n  line2  ")
        self.assertEqual(result, "line1\nline2")

    def test_limits_consecutive_empty_lines(self):
        text = "a\n\n\n\n\nb"
        result = sanitize_input(text)
        # Max 2 consecutive empty lines = 3 newlines between content
        # 4+ newlines (3+ empty lines) should never appear
        self.assertNotIn("\n\n\n\n", result)
        self.assertIn("a", result)
        self.assertIn("b", result)

    def test_non_string_returns_empty(self):
        self.assertEqual(sanitize_input(None), "")
        self.assertEqual(sanitize_input(123), "")
        self.assertEqual(sanitize_input([]), "")

    def test_empty_string(self):
        self.assertEqual(sanitize_input(""), "")

    def test_preserves_single_newlines(self):
        result = sanitize_input("line1\nline2\nline3")
        self.assertEqual(result, "line1\nline2\nline3")

    def test_mixed_line_endings(self):
        result = sanitize_input("a\r\nb\rc\nd")
        self.assertEqual(result, "a\nb\nc\nd")


class TestParseTagsString(unittest.TestCase):
    """Test parse_tags_string function."""

    def test_simple_comma_separated(self):
        result = parse_tags_string("tag1, tag2, tag3")
        self.assertEqual(result, ["tag1", "tag2", "tag3"])

    def test_extra_whitespace(self):
        result = parse_tags_string("  tag1  ,  tag2  ")
        self.assertEqual(result, ["tag1", "tag2"])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(parse_tags_string(""), [])

    def test_none_returns_empty_list(self):
        self.assertEqual(parse_tags_string(None), [])

    def test_non_string_returns_empty_list(self):
        self.assertEqual(parse_tags_string(123), [])

    def test_single_tag(self):
        self.assertEqual(parse_tags_string("solo"), ["solo"])

    def test_deduplication(self):
        result = parse_tags_string("tag1, tag1, tag2, tag1")
        self.assertEqual(result, ["tag1", "tag2"])

    def test_empty_segments_skipped(self):
        result = parse_tags_string("tag1,,tag2,,,tag3")
        self.assertEqual(result, ["tag1", "tag2", "tag3"])

    def test_max_20_tags(self):
        tags = ", ".join(f"tag{i}" for i in range(25))
        result = parse_tags_string(tags)
        self.assertEqual(len(result), 20)

    def test_strips_null_bytes_from_tags(self):
        result = parse_tags_string("clean\x00tag, normal")
        self.assertIn("cleantag", result)
        self.assertIn("normal", result)


class TestValidatePromptTextEdgeCases(unittest.TestCase):
    """Additional edge cases beyond test_basic.py coverage."""

    def test_exactly_max_length(self):
        self.assertTrue(validate_prompt_text("x" * 10000))

    def test_one_over_max_length(self):
        with self.assertRaises(ValueError):
            validate_prompt_text("x" * 10001)

    def test_single_character(self):
        self.assertTrue(validate_prompt_text("a"))

    def test_unicode_text(self):
        self.assertTrue(validate_prompt_text("日本語プロンプト"))

    def test_multiline_text(self):
        self.assertTrue(validate_prompt_text("line1\nline2\nline3"))

    def test_boolean_raises(self):
        with self.assertRaises(ValueError):
            validate_prompt_text(True)

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            validate_prompt_text(None)


class TestValidateRatingEdgeCases(unittest.TestCase):
    """Additional edge cases beyond test_basic.py coverage."""

    def test_float_raises(self):
        with self.assertRaises(ValueError):
            validate_rating(3.5)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            validate_rating(-1)

    def test_zero_raises(self):
        with self.assertRaises(ValueError):
            validate_rating(0)

    def test_boolean_true_is_int(self):
        # In Python, bool is a subclass of int: True == 1
        # This tests the actual behavior
        self.assertTrue(validate_rating(True))


class TestValidateTagsEdgeCases(unittest.TestCase):
    """Additional edge cases beyond test_basic.py coverage."""

    def test_empty_list_is_valid(self):
        self.assertTrue(validate_tags([]))

    def test_comma_string_parsed(self):
        self.assertTrue(validate_tags("tag1, tag2"))

    def test_control_char_in_tag_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_tags(["bad\x00tag"])
        self.assertIn("control characters", str(ctx.exception))

    def test_tab_in_tag_rejected(self):
        with self.assertRaises(ValueError):
            validate_tags(["bad\ttag"])

    def test_non_string_tag_in_list_rejected(self):
        with self.assertRaises(ValueError):
            validate_tags([123])

    def test_dict_input_rejected(self):
        with self.assertRaises(ValueError):
            validate_tags({"tag": "value"})

    def test_exactly_20_tags(self):
        self.assertTrue(validate_tags([f"tag{i}" for i in range(20)]))

    def test_exactly_50_char_tag(self):
        self.assertTrue(validate_tags(["x" * 50]))

    def test_whitespace_only_tag_rejected(self):
        with self.assertRaises(ValueError):
            validate_tags(["   "])


if __name__ == "__main__":
    unittest.main()
