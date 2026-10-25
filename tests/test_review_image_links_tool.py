"""tools/review_image_links.py: a dry run by default, changes only named images."""

import io
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock

from PIL import Image
from PIL.PngImagePlugin import PngInfo

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from tests.test_prompt_attribution import issue_graph, pair_evidence
from tools import review_image_links as tool
from utils.hashing import generate_prompt_hash


def png(path, prompt=None, evidence=None):
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if evidence is not None:
        info.add_text("comfyassets_prompt_pairs", json.dumps(evidence))
    Image.new("RGB", (4, 4)).save(path, pnginfo=info)


class ReviewToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(assert_closed, self, self.tmp)
        self.output = os.path.join(self.tmp, "output")
        os.makedirs(self.output)
        self.db_path = os.path.join(self.tmp, "prompts.db")
        db = PromptDatabase(self.db_path)
        try:
            save = lambda text: db.save_prompt(  # noqa: E731
                text=text, prompt_hash=generate_prompt_hash(text)
            )
            self.coffee = save("a woman holding a coffee cup")
            self.typed = save("snowy peaks")
            # The six #342 images: only prepend text, linked by the old fallback
            self.guessed = []
            for n in range(9, 12):
                path = os.path.join(self.output, f"moss_{n:05}_.png")
                png(path, issue_graph(), pair_evidence())
                self.guessed.append(
                    db.link_image_to_prompt(self.coffee, path, metadata={})
                )
            # A typed image linked to the wrong prompt, and a correct one
            wrong = os.path.join(self.output, "typed_wrong.png")
            png(wrong, issue_graph(text="snowy peaks"))
            self.wrong = db.link_image_to_prompt(self.coffee, wrong, metadata={})
            right = os.path.join(self.output, "typed_right.png")
            png(right, issue_graph(text="snowy peaks"))
            self.right = db.link_image_to_prompt(self.typed, right, metadata={})
            gone = os.path.join(self.output, "gone.png")
            self.gone = db.link_image_to_prompt(self.coffee, gone, metadata={})
        finally:
            db.close_all()

    def run_tool(self, *args):
        out = io.StringIO()
        code = tool.main([self.db_path, "--output-dir", self.output, *args], out=out)
        return code, out.getvalue()

    def links(self):
        db = PromptDatabase(self.db_path)
        try:
            rows = db.model.get_connection().execute(
                "SELECT id, prompt_id FROM generated_images"
            )
            return {row[0]: row[1] for row in rows}
        finally:
            db.close_all()

    def test_dry_run_judges_and_changes_nothing(self):
        before = self.links()
        ids = f"{self.guessed[0]}-{self.gone}"
        code, out = self.run_tool("--ids", ids, "--json")
        self.assertEqual(code, 0)
        verdicts = {v["image_id"]: v for v in json.loads(out.split("Dry run")[0])}
        for image_id in self.guessed:
            self.assertEqual(verdicts[image_id]["verdict"], "unlink")
            self.assertEqual(verdicts[image_id]["sources"], {})
        self.assertEqual(verdicts[self.wrong]["verdict"], "relink")
        self.assertEqual(verdicts[self.wrong]["target"], self.typed)
        self.assertEqual(verdicts[self.right]["verdict"], "keep")
        self.assertEqual(verdicts[self.gone]["verdict"], "missing")
        self.assertIn("Dry run: nothing was changed.", out)
        self.assertEqual(self.links(), before)
        self.assertEqual(
            sorted(os.listdir(self.tmp)), ["output", "prompts.db"], "no side files"
        )

    def test_prompt_selection_and_text_report(self):
        code, out = self.run_tool("--prompt", str(self.coffee))
        self.assertEqual(code, 0)
        self.assertIn("unlink: 3", out)
        self.assertIn("relink: 1", out)
        self.assertIn("missing: 1", out)
        self.assertIn(f"-> {self.typed}", out)

    def test_apply_backs_up_then_changes_only_named_images(self):
        named = f"{self.guessed[0]}-{self.guessed[1]},{self.wrong}"
        code, out = self.run_tool(
            "--ids", named, "--prompt", str(self.coffee), "--apply"
        )
        self.assertEqual(code, 0)
        self.assertIn("Applied: 1 relinked, 2 unlinked", out)
        links = self.links()
        self.assertNotIn(self.guessed[0], links)
        self.assertNotIn(self.guessed[1], links)
        self.assertEqual(links[self.guessed[2]], self.coffee, "not named: kept")
        self.assertEqual(links[self.wrong], self.typed)
        self.assertEqual(links[self.gone], self.coffee, "missing files are kept")
        backups = [n for n in os.listdir(self.tmp) if ".pre-relink_" in n]
        self.assertEqual(len(backups), 1)
        restored = PromptDatabase(os.path.join(self.tmp, backups[0]))
        try:
            count = (
                restored.model.get_connection()
                .execute("SELECT COUNT(*) FROM generated_images")
                .fetchone()[0]
            )
        finally:
            restored.close_all()
        self.assertEqual(count, 6)

    def test_arguments_are_validated(self):
        for args in (
            [],
            ["--ids", "5-2"],
            ["--ids", "x"],
            ["--prompt", "1", "--apply"],
        ):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                with unittest.mock.patch("sys.stderr", io.StringIO()):
                    self.run_tool(*args)
        with (
            self.assertRaises(SystemExit),
            unittest.mock.patch("sys.stderr", io.StringIO()),
        ):
            tool.main(
                [os.path.join(self.tmp, "none.db"), "--output-dir", "x", "--ids", "1"]
            )
        self.assertEqual(tool.parse_ids(" 3, 1-2 ,"), [1, 2, 3])

    def test_conflicting_sources_are_left_to_the_maintainer(self):
        db = PromptDatabase(self.db_path)
        try:
            handler = tool.ImageGenerationHandler(db, None)
            row = {
                "id": 1,
                "prompt_id": self.coffee,
                "image_path": os.path.join(self.output, "typed_right.png"),
            }
            with unittest.mock.patch.object(
                tool, "certain_prompts", return_value={"scan": 1, "history": 2}
            ):
                verdict = tool.judge(db, handler, row, self.output)
        finally:
            db.close_all()
        self.assertEqual(verdict["verdict"], "conflict")


if __name__ == "__main__":
    unittest.main()
