"""
Tests for PromptSearchList output filtering.

Tests the newline collapsing, multi-part (Clip_) filtering,
and LoRA-only filtering applied to search results.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- Helpers: replicate the filter logic from prompt_search_list.py ---

LORA_ONLY = re.compile(r"^(\s*<lora:[^>]+>\s*)+$")


def apply_filters(texts, skip_multipart=True):
    """Apply the same filtering chain as PromptSearchList.search()."""
    # Collapse newlines
    result = [" ".join(t.split()) for t in texts if t]

    # Filter multi-part
    if skip_multipart:
        result = [p for p in result if not re.search(r"Clip_\d+", p)]

    # Filter LoRA-only
    result = [p for p in result if not LORA_ONLY.match(p)]

    return result


class TestNewlineCollapse(unittest.TestCase):
    """Newlines must be collapsed so StringOutputList treats each DB entry as one prompt."""

    def test_single_line_unchanged(self):
        result = apply_filters(["a beautiful landscape"])
        self.assertEqual(result, ["a beautiful landscape"])

    def test_multiline_collapsed_to_spaces(self):
        result = apply_filters(["line one\nline two\nline three"])
        self.assertEqual(result, ["line one line two line three"])

    def test_tabs_and_extra_spaces_collapsed(self):
        result = apply_filters(["word1  \t  word2\n\nword3"])
        self.assertEqual(result, ["word1 word2 word3"])

    def test_leading_trailing_whitespace_stripped(self):
        result = apply_filters(["  padded prompt  \n"])
        self.assertEqual(result, ["padded prompt"])


class TestMultipartFilter(unittest.TestCase):
    """Prompts containing Clip_N markers should be filtered when skip_multipart=True."""

    def test_clip_markers_filtered(self):
        texts = [
            "Clip_1 a video prompt Clip_2 continuation",
            "a normal prompt",
        ]
        result = apply_filters(texts, skip_multipart=True)
        self.assertEqual(result, ["a normal prompt"])

    def test_clip_markers_kept_when_disabled(self):
        texts = ["Clip_1 a video prompt Clip_2 continuation"]
        result = apply_filters(texts, skip_multipart=False)
        self.assertEqual(len(result), 1)

    def test_clip_in_word_filtered(self):
        # "Clip_3" anywhere in text should match
        texts = ["some text with Clip_3 marker"]
        result = apply_filters(texts, skip_multipart=True)
        self.assertEqual(result, [])

    def test_clip_without_number_not_filtered(self):
        texts = ["clip art style painting"]
        result = apply_filters(texts, skip_multipart=True)
        self.assertEqual(result, ["clip art style painting"])


class TestLoraOnlyFilter(unittest.TestCase):
    """Prompts that are only LoRA tags with no actual content should be filtered."""

    def test_single_lora_tag_filtered(self):
        result = apply_filters(["<lora:some_model:0.7>"])
        self.assertEqual(result, [])

    def test_multiple_lora_tags_filtered(self):
        result = apply_filters(["<lora:model_a:1> <lora:model_b:0.5>"])
        self.assertEqual(result, [])

    def test_lora_with_content_kept(self):
        result = apply_filters(["<lora:style:0.8> a beautiful painting"])
        self.assertEqual(result, ["<lora:style:0.8> a beautiful painting"])

    def test_content_with_lora_in_middle_kept(self):
        result = apply_filters(["masterpiece, <lora:fix:1>, best quality"])
        self.assertEqual(result, ["masterpiece, <lora:fix:1>, best quality"])

    def test_lora_with_newlines_collapsed_then_filtered(self):
        # After collapsing newlines, this becomes a single line of LoRA tags
        result = apply_filters(["<lora:a:1>\n<lora:b:1>"])
        self.assertEqual(result, [])

    def test_empty_string_filtered(self):
        result = apply_filters([""])
        self.assertEqual(result, [])


class TestFilterCombination(unittest.TestCase):
    """Test all filters working together on a mixed batch."""

    def test_mixed_batch(self):
        texts = [
            "a good prompt",
            "<lora:style:1>",
            "Clip_1\nfirst segment\nClip_2\nsecond segment",
            "<lora:model:0.5> nice painting, flowers",
            "another\ngood\nprompt",
            "<lora:a:1> <lora:b:1>",
        ]
        result = apply_filters(texts)
        self.assertEqual(
            result,
            [
                "a good prompt",
                "<lora:model:0.5> nice painting, flowers",
                "another good prompt",
            ],
        )


if __name__ == "__main__":
    unittest.main()
