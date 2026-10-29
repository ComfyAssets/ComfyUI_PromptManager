"""#444: the Library's output-folder filter and multi-tag OR."""

import ntpath
import os
import posixpath
import tempfile
import unittest
from pathlib import Path

from database.library_folders import (
    folder_prefixes,
    normalize_folder,
    relative_folder,
)
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestFolderNames(unittest.TestCase):
    def test_normalize_reads_backslashes_and_strips_outer_slashes(self):
        self.assertEqual(normalize_folder(""), "")
        self.assertEqual(normalize_folder("/"), "")
        self.assertEqual(normalize_folder("2026\\08-Aug\\"), "2026/08-Aug")
        self.assertEqual(normalize_folder("/a/b/"), "a/b")
        self.assertEqual(normalize_folder("..foo/b"), "..foo/b")

    def test_normalize_refuses_anything_but_a_folder_below_a_root(self):
        for value in ("a/../b", "./a", "a//b", "C:/x", "c:", "a\x00b", 3, "x" * 1025):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_folder(value)

    def test_posix_prefixes_are_exact_and_end_in_a_separator(self):
        self.assertEqual(
            folder_prefixes(["/out", "/data/out/"], "a/b", posixpath),
            ["/out/a/b/", "/data/out/a/b/"],
        )
        self.assertEqual(folder_prefixes(["/"], "a", posixpath), ["/a/"])
        self.assertEqual(
            folder_prefixes(["/out", "/out/"], "a", posixpath), ["/out/a/"]
        )
        with self.assertRaises(ValueError):
            folder_prefixes([], "a", posixpath)

    def test_windows_prefixes_fold_case_including_non_ascii(self):
        self.assertEqual(
            folder_prefixes(["C:\\ComfyUI\\Output"], "Ä/Sub", ntpath),
            ["c:\\comfyui\\output\\ä\\sub\\"],
        )
        self.assertEqual(folder_prefixes(["D:\\"], "x", ntpath), ["d:\\x\\"])
        self.assertEqual(
            folder_prefixes(["\\\\Server\\Share\\Out"], "a", ntpath),
            ["\\\\server\\share\\out\\a\\"],
        )
        # The stored file_path is normcase(normpath(abspath)), the same fold.
        stored = ntpath.normcase(ntpath.normpath("C:/ComfyUI/output/Ä/sub/x.png"))
        self.assertTrue(
            stored.startswith(
                folder_prefixes(["C:/ComfyUI/Output"], "ä/SUB", ntpath)[0]
            )
        )

    def test_relative_folder_keeps_the_stored_spelling(self):
        self.assertEqual(
            relative_folder("/out/2026/Aug/x.png", ["/out"], posixpath), "2026/Aug"
        )
        self.assertIsNone(relative_folder("/out/x.png", ["/out"], posixpath))
        self.assertIsNone(relative_folder("/outside/x.png", ["/out"], posixpath))
        self.assertIsNone(relative_folder("/output2/a/x.png", ["/out"], posixpath))
        self.assertEqual(
            relative_folder("/out/..foo/x.png", ["/out"], posixpath), "..foo"
        )
        self.assertEqual(
            relative_folder("C:\\Out\\Ä\\Sub\\x.png", ["c:\\out"], ntpath), "Ä/Sub"
        )
        self.assertEqual(relative_folder("D:\\a\\x.png", ["D:\\"], ntpath), "a")


class TestLibraryFolderAndAnyTag(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.temp.name))
        self.root = base / "output"
        self.root.mkdir()
        self.db = PromptDatabase(str(base / "folders.db"))

    def tearDown(self):
        try:
            self.db.close_all()
        finally:
            self.temp.cleanup()

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def link(self, prompt_id, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        self.db.link_image_to_prompt(prompt_id, str(path))

    def texts(self, **options):
        return sorted(item["text"] for item in self.db.list_library(**options)["items"])

    def in_folder(self, folder, roots=None):
        return self.texts(
            folder_prefixes=folder_prefixes(roots or [str(self.root)], folder)
        )

    def test_folder_matches_its_descendants_only(self):
        self.link(self.save("deep"), "a", "b", "x.png")
        self.link(self.save("top"), "a", "y.png")
        self.link(self.save("sibling"), "ab", "z.png")
        self.link(self.save("literal"), "100%_off", "w.png")
        self.save("no images")
        self.assertEqual(self.in_folder("a"), ["deep", "top"])
        self.assertEqual(self.in_folder("a/b"), ["deep"])
        self.assertEqual(self.in_folder("ab"), ["sibling"])
        self.assertEqual(self.in_folder("100%_off"), ["literal"])
        self.assertEqual(self.in_folder("1"), [])
        cut = self.db.list_library(
            folder_prefixes=folder_prefixes([str(self.root)], "a"), limit=1
        )
        self.assertEqual(cut["total"], 2)

    def test_folder_combines_with_text_category_and_tags(self):
        self.link(self.save("red cat", category="Art", tags=["cat"]), "a", "1.png")
        self.link(self.save("red dog", category="Art", tags=["dog"]), "a", "2.png")
        self.link(self.save("red bird", category="Art", tags=["cat"]), "b", "3.png")
        prefixes = folder_prefixes([str(self.root)], "a")
        self.assertEqual(
            self.texts(folder_prefixes=prefixes, tags=["cat"]), ["red cat"]
        )
        self.assertEqual(
            self.texts(folder_prefixes=prefixes, text="dog", category="Art"),
            ["red dog"],
        )

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_folder_listing_and_match_through_a_symlinked_root(self):
        link = self.root.parent / "linked"
        os.symlink(self.root, link)
        prompt = self.save("via link")
        (self.root / "a").mkdir()
        (self.root / "a" / "x.png").write_bytes(b"x")
        self.db.link_image_to_prompt(prompt, str(link / "a" / "x.png"))
        spellings = [str(self.root), str(link)]
        self.assertEqual(self.in_folder("a", spellings), ["via link"])
        self.assertEqual(self.in_folder("a", [str(self.root)]), [])
        self.assertEqual(self.db.get_library_options(spellings)["folders"], ["a"])

    def test_folder_listing_offers_ancestors_and_skips_outside_images(self):
        self.link(self.save("deep"), "2026", "08-Aug", "x.png")
        self.link(self.save("root image"), "y.png")
        outside = Path(self.temp.name) / "elsewhere" / "z.png"
        outside.parent.mkdir()
        outside.write_bytes(b"x")
        self.db.link_image_to_prompt(self.save("outside"), str(outside))
        options = self.db.get_library_options([str(self.root)])
        self.assertEqual(options["folders"], ["2026", "2026/08-Aug"])
        self.assertNotIn("folders", self.db.get_library_options())

    def test_folder_combines_with_untagged(self):
        self.link(self.save("plain in a"), "a", "1.png")
        self.link(self.save("tagged in a", tags=["t"]), "a", "2.png")
        self.link(self.save("plain in b"), "b", "3.png")
        prefixes = folder_prefixes([str(self.root)], "a")
        self.assertEqual(
            self.texts(folder_prefixes=prefixes, tag_mode="untagged"), ["plain in a"]
        )

    def test_any_tag_mode_is_the_union_across_parent_and_pair(self):
        self.save("parent a", tags=["a"])
        self.save("parent b", tags=["b"])
        self.save("both", tags=["a", "b"])
        self.save("neither", tags=["c"])
        self.db.save_prompt_pair("pair b", "noise", tags=["b"])
        split = self.save("split", tags=["a"])
        self.db.save_prompt_pair("split", "split noise", tags=["b"])
        archived = self.db.save_prompt_pair("archived b", "old", tags=["b"])
        self.db.archive_prompt_pair(archived)
        self.assertIsInstance(split, int)
        self.assertEqual(
            self.texts(tags=["a", "b"], tag_mode="any"),
            ["both", "pair b", "parent a", "parent b", "split"],
        )
        self.assertEqual(self.texts(tags=["a", "b"], tag_mode="all"), ["both"])
        self.assertEqual(self.texts(tags=["a", "b"]), ["both"])
        for tags in (["a"], ["b"], ["c"]):
            self.assertEqual(
                self.texts(tags=tags, tag_mode="any"), self.texts(tags=tags)
            )
        self.assertEqual(
            self.texts(tags=["a", "b"], tag_mode="any", text="parent"),
            ["parent a", "parent b"],
        )
        self.assertEqual(self.texts(tag_mode="any"), self.texts())
        for mode in ("none", None, "ANY"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.db.list_library(tag_mode=mode)


if __name__ == "__main__":
    unittest.main()
