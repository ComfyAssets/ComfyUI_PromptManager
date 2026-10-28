"""tools/repair_foreign_links.py: the reviewed repair of the #247 dataset links.

The fixture is a library whose foreign (no PromptManager node) dataset run
wrote shared-prefix files that the old scan linked to the wrong branch text.
Recorded history names each file's saver, so the ladder (``recorded-run``)
names the right branch.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel
from database.operations import PromptDatabase
from tests import saver_anchor_graphs as shapes
from tests.test_run_attribution import RecordedRuns
from tools import repair_foreign_links as tool
from utils.file_prompt import Decision
from utils.hashing import generate_prompt_hash

TEXTS = shapes.BRANCH_TEXTS
NAME = "IMG_GEN_{:05}_.png"


def png(path, graph):
    info = PngInfo()
    info.add_text("prompt", json.dumps(graph))
    Image.new("RGB", (4, 4)).save(path, pnginfo=info)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path):
    """The database's logical content (independent of WAL checkpoints)."""
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def not_running(_comfyui_dir, _port):
    return []


class RepairFixture(RecordedRuns, unittest.TestCase):
    """Rows: A relink to a new prompt, B relink to a stored prompt (with a
    pair link), C keep, D file gone, E two savers (skip), F the UNIQUE case
    (a wrong row beside a correct row), H relink from a rated prompt, G a
    PromptManager run (not a candidate)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(assert_closed, self, self.tmp)
        self.addCleanup(PromptModel.reset_schema_cache)
        self.output = os.path.join(self.tmp, "output")
        os.makedirs(os.path.join(self.output, "dataset"))
        self.db_path = os.path.join(self.tmp, "prompts.db")
        self.plan_path = os.path.join(self.tmp, "plan.json")
        self.backups = os.path.join(self.tmp, "backups")
        os.makedirs(self.backups)
        self.files = {}
        for key, number in zip("ABCDEFHG", range(1, 9)):
            path = os.path.join(self.output, "dataset", NAME.format(number))
            png(path, shapes.shared_prefix())
            self.files[key] = path
        db = PromptDatabase(self.db_path)
        try:
            self.build(db)
        finally:
            db.close_all()
        os.remove(self.files["D"])

    def build(self, db):
        save = lambda text, **kw: db.save_prompt(  # noqa: E731
            text=text, prompt_hash=generate_prompt_hash(text), **kw
        )
        self.p0 = save(TEXTS[0], category="scanned", tags=["auto-scanned"])
        self.p2 = save(TEXTS[2], category="scanned", tags=["auto-scanned"])
        self.orphan = save(
            "an old guess",
            category="scanned",
            tags=["auto-scanned"],
            notes="Auto-scanned from IMG_GEN_00001_.png",
        )
        self.rated = save("a rated guess", rating=5)
        link = lambda prompt, key: db.link_image_to_prompt(  # noqa: E731
            prompt, self.files[key], metadata={}
        )
        self.rows = {
            "A": link(self.orphan, "A"),
            "B": link(self.p0, "B"),
            "C": link(self.p0, "C"),
            "D": link(self.p0, "D"),
            "E": link(self.p0, "E"),
            "F": link(self.p0, "F"),
            "F_ok": link(self.p2, "F"),
            "H": link(self.rated, "H"),
            "G": link(self.p0, "G"),
        }
        conn = db.model.get_connection()
        with conn:
            pair = conn.execute(
                "INSERT INTO prompt_pairs(positive_prompt_id) VALUES(?)", (self.p0,)
            ).lastrowid
            conn.execute(
                "INSERT INTO image_prompt_pairs(image_id, pair_id, provenance) "
                "VALUES(?,?,'{}')",
                (self.rows["B"], pair),
            )
        name = lambda key: ("dataset", os.path.basename(self.files[key]))  # noqa
        # Savers of the shared-prefix graph: "13", "23", "33", "43" (branches 1-4)
        self.record_run(
            db,
            self.output,
            [name(k) for k in "CDEAEBFH"],
            [],
            nodes=["13", "13", "13", "23", "23", "33", "33", "43"],
        )
        self.record_run(db, self.output, [name("G")], [TEXTS[0]], item=1, nodes=["23"])

    def dry_run(self, *args):
        out = io.StringIO()
        code = tool.main(
            [
                self.db_path,
                "--output-dir",
                self.output,
                "--plan",
                self.plan_path,
                *args,
            ],
            out=out,
        )
        return code, out.getvalue()

    def apply(self, *args, probe=not_running):
        out = io.StringIO()
        code = tool.main(
            [
                self.db_path,
                "--apply",
                self.plan_path,
                "--comfyui-dir",
                os.path.join(self.tmp, "comfy"),
                "--backup-dir",
                self.backups,
                *args,
            ],
            out=out,
            probe=probe,
        )
        return code, out.getvalue()

    def plan(self):
        with open(self.plan_path, encoding="utf-8") as stream:
            return json.load(stream)

    def write_plan(self, plan):
        with open(self.plan_path, "w", encoding="utf-8") as stream:
            json.dump(plan, stream)

    def query(self, sql, args=()):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def links(self):
        return dict(self.query("SELECT id, prompt_id FROM generated_images"))


class DryRunTests(RepairFixture):
    def test_plan_names_relink_keep_and_skip_rows(self):
        code, out = self.dry_run()
        self.assertEqual(code, 0, out)
        items = {item["row_id"]: item for item in self.plan()["rows"]}
        rows = self.rows
        self.assertNotIn(rows["G"], items, "a PromptManager run is not foreign")
        actions = {key: items[rows[key]]["action"] for key in rows if key != "G"}
        self.assertEqual(
            actions,
            {
                "A": "relink",
                "B": "relink",
                "C": "keep",
                "D": "skip",
                "E": "skip",
                "F": "relink",
                "F_ok": "keep",
                "H": "relink",
            },
        )
        a = items[rows["A"]]
        self.assertEqual(a["current_prompt_id"], self.orphan)
        self.assertEqual(a["current_text_sha256"], sha("an old guess"))
        self.assertEqual(a["target_text"], TEXTS[1])
        self.assertEqual(a["target_hash"], generate_prompt_hash(TEXTS[1]))
        self.assertEqual(a["target_prompt_id"], "new")
        self.assertEqual(a["file"], self.files["A"])
        self.assertEqual(items[rows["B"]]["target_prompt_id"], self.p2)
        self.assertEqual(items[rows["H"]]["target_prompt_id"], "new")
        self.assertEqual(items[rows["D"]]["reason"], "file gone")
        self.assertEqual(
            items[rows["E"]]["reason"], "not decided by the recorded saver"
        )
        self.assertIn("relink: 4", out)
        self.assertIn("keep: 2", out)
        self.assertIn("skip: 2", out)

    def test_orphan_audit_lists_the_prompts_losing_every_link(self):
        self.dry_run()
        plan = self.plan()
        orphans = {o["id"]: o for o in plan["orphans"]}
        self.assertEqual(set(orphans), {self.orphan, self.rated})
        self.assertEqual(orphans[self.orphan]["blockers"], [])
        self.assertTrue(orphans[self.orphan]["safe"])
        self.assertEqual(orphans[self.rated]["blockers"], ["usage or rating"])
        self.assertFalse(orphans[self.rated]["safe"])
        self.assertFalse(plan["delete_orphans"], "off by default")
        self.dry_run("--delete-orphans")
        self.assertTrue(self.plan()["delete_orphans"])

    def test_dry_run_writes_nothing_and_writes_the_markdown_list(self):
        before = file_sha(self.db_path)
        listing = sorted(os.listdir(self.tmp))
        code, out = self.dry_run()
        self.assertEqual(code, 0)
        self.assertEqual(file_sha(self.db_path), before)
        self.assertIn("Dry run: nothing was changed.", out)
        # A read-only reader of a WAL library may leave its empty side files
        sidecars = {"prompts.db-shm", "prompts.db-wal"}
        self.assertEqual(
            sorted(set(os.listdir(self.tmp)) - sidecars),
            sorted(set(listing + ["plan.json", "plan.md"]) - sidecars),
        )
        wal = Path(self.tmp, "prompts.db-wal")
        self.assertEqual(wal.stat().st_size if wal.exists() else 0, 0)
        markdown = Path(self.tmp, "plan.md").read_text(encoding="utf-8")
        self.assertIn(f"| {self.rows['A']} |", markdown)
        self.assertIn("new", markdown)
        self.assertIn("would lose every link", markdown)


class ApplyTests(RepairFixture):
    def setUp(self):
        super().setUp()
        code, out = self.dry_run()
        self.assertEqual(code, 0, out)

    def test_apply_relinks_creates_targets_and_drops_pair_links(self):
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        links = self.links()
        new = dict(self.query("SELECT hash, id FROM prompts"))
        target_a = new[generate_prompt_hash(TEXTS[1])]
        target_h = new[generate_prompt_hash(TEXTS[3])]
        self.assertEqual(links[self.rows["A"]], target_a)
        self.assertEqual(links[self.rows["B"]], self.p2)
        self.assertEqual(links[self.rows["H"]], target_h)
        self.assertEqual(links[self.rows["C"]], self.p0, "keep")
        self.assertEqual(links[self.rows["E"]], self.p0, "skip")
        self.assertEqual(links[self.rows["G"]], self.p0, "not foreign")
        self.assertEqual(
            self.query(
                "SELECT COUNT(*) FROM image_prompt_pairs WHERE image_id=?",
                (self.rows["B"],),
            ),
            [(0,)],
        )
        category, notes = self.query(
            "SELECT category, notes FROM prompts WHERE id=?", (target_a,)
        )[0]
        self.assertEqual(category, "scanned")
        self.assertEqual(
            notes, f"Auto-scanned from {os.path.basename(self.files['A'])}"
        )
        tags = self.query(
            "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.prompt_id=?",
            (target_a,),
        )
        self.assertEqual(tags, [("auto-scanned",)])
        self.assertIn("integrity_check", out)
        self.assertIn("foreign_key_check", out)
        self.assertRegex(out, r"integrity_check\s+ok\s+ok")
        # Orphans are kept unless the plan chose to delete them
        self.assertTrue(self.query("SELECT 1 FROM prompts WHERE id=?", (self.orphan,)))

    def test_unique_case_deletes_the_wrong_row(self):
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        links = self.links()
        self.assertNotIn(self.rows["F"], links)
        self.assertEqual(links[self.rows["F_ok"]], self.p2)
        rows = self.query(
            "SELECT prompt_id FROM generated_images WHERE image_path=?",
            (self.files["F"],),
        )
        self.assertEqual(rows, [(self.p2,)])

    def test_backup_is_taken_before_writing(self):
        before = self.links()
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        names = os.listdir(self.backups)
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^prompts\.db\.pre-247-repair_\d{8}_\d{6}$")
        backup = os.path.join(self.backups, names[0])
        conn = sqlite3.connect(backup)
        try:
            saved = dict(conn.execute("SELECT id, prompt_id FROM generated_images"))
        finally:
            conn.close()
        self.assertEqual(saved, before)
        self.assertIn(f"Backup: {backup}", out)

    def test_backup_beside_the_database_by_default(self):
        out = io.StringIO()
        code = tool.main(
            [
                self.db_path,
                "--apply",
                self.plan_path,
                "--comfyui-dir",
                os.path.join(self.tmp, "comfy"),
            ],
            out=out,
            probe=not_running,
        )
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue(
            any(
                n.startswith("prompts.db.pre-247-repair_") for n in os.listdir(self.tmp)
            )
        )

    def test_refuses_while_comfyui_runs(self):
        before = file_sha(self.db_path)
        code, out = self.apply(probe=lambda _d, _p: ["process 42 (main.py)"])
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", out)
        self.assertIn("process 42", out)
        self.assertEqual(os.listdir(self.backups), [], "no backup either")
        self.assertEqual(file_sha(self.db_path), before)

    def test_rehearsal_skips_the_running_check_on_a_copy_only(self):
        code, out = self.apply("--rehearsal", probe=lambda _d, _p: ["port 8188"])
        self.assertEqual(code, 0, out)
        self.assertIn("rehearsal", out)
        # The live library of that ComfyUI folder: --rehearsal is refused
        live_dir = os.path.join(self.tmp, "comfy", "user", "__promptmanager")
        os.makedirs(live_dir)
        live = os.path.join(live_dir, "prompts.db")
        shutil.copy(self.db_path, live)
        out = io.StringIO()
        code = tool.main(
            [
                live,
                "--apply",
                self.plan_path,
                "--comfyui-dir",
                os.path.join(self.tmp, "comfy"),
                "--rehearsal",
            ],
            out=out,
            probe=lambda _d, _p: [],
        )
        self.assertEqual(code, 2)
        self.assertIn("--rehearsal", out.getvalue())

    def test_drift_refuses_and_changes_nothing(self):
        cases = {
            "row moved": lambda: self.query_write(
                "UPDATE generated_images SET prompt_id=? WHERE id=?",
                (self.p2, self.rows["H"]),
            ),
            "text changed": lambda: self.query_write(
                "UPDATE prompts SET text='an older guess' WHERE id=?", (self.orphan,)
            ),
            "file changed": lambda: png(self.files["B"], shapes.branch_bench()),
            "target stored since": lambda: self.query_write(
                "INSERT INTO prompts(text, hash) VALUES(?,?)",
                (TEXTS[1], generate_prompt_hash(TEXTS[1])),
            ),
        }
        pristine = os.path.join(self.tmp, "pristine.db")
        shutil.copy(self.db_path, pristine)
        image = Path(self.files["B"]).read_bytes()
        for name, drift in cases.items():
            with self.subTest(name):
                shutil.copy(pristine, self.db_path)
                Path(self.files["B"]).write_bytes(image)
                drift()
                before = dump(self.db_path)
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertIn("REFUSED (rolled back, nothing changed)", out)
                self.assertEqual(dump(self.db_path), before)

    def query_write(self, sql, args):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute(sql, args)
        finally:
            conn.close()

    def test_late_failure_rolls_back_every_write(self):
        plan = self.plan()
        plan["delete_orphans"] = True
        self.write_plan(plan)
        # The safe orphan gained a user tag after the dry run
        self.query_write("INSERT INTO tags(name) VALUES('mine')", ())
        self.query_write(
            "INSERT INTO prompt_tags(prompt_id, tag_id) "
            "SELECT ?, id FROM tags WHERE name='mine'",
            (self.orphan,),
        )
        before = self.links()
        prompts = self.query("SELECT COUNT(*) FROM prompts")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn(f"prompt {self.orphan}", out)
        self.assertIn("user tags", out)
        self.assertEqual(self.links(), before, "the relinks were rolled back")
        self.assertEqual(self.query("SELECT COUNT(*) FROM prompts"), prompts)

    def test_orphans_are_deleted_only_when_chosen_and_unreferenced(self):
        plan = self.plan()
        plan["delete_orphans"] = True
        self.write_plan(plan)
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertFalse(self.query("SELECT 1 FROM prompts WHERE id=?", (self.orphan,)))
        self.assertTrue(
            self.query("SELECT 1 FROM prompts WHERE id=?", (self.rated,)),
            "a prompt with a rating is never deleted",
        )
        self.assertIn("orphans_deleted", out)

    def test_an_applied_plan_refuses_a_second_time(self):
        self.assertEqual(self.apply()[0], 0)
        before = dump(self.db_path)
        code, out = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", out)
        self.assertEqual(dump(self.db_path), before)


class ActionTests(unittest.TestCase):
    """Only the recorded saver relinks; a certain step naming the linked
    prompt keeps it; the legacy floor and the rest are skipped."""

    def test_action_by_decision(self):
        linked = generate_prompt_hash("linked")
        cases = [
            (Decision("positive", "other", source="recorded-run"), "relink"),
            (Decision("positive", "linked", source="recorded-run"), "keep"),
            (Decision("positive", "linked", source="inspector"), "keep"),
            (Decision("positive", "other", source="inspector"), "skip"),
            (Decision("positive", "linked", source="legacy-parser"), "skip"),
            (Decision("pair", "linked", source="resolver"), "skip"),
            (Decision("never"), "skip"),
        ]
        for decision, action in cases:
            with self.subTest(decision.source, text=decision.text):
                self.assertEqual(tool.action_of(decision, linked), action)


class CommandLineTests(unittest.TestCase):
    def test_running_probe_reports_a_listening_port(self):
        import socket

        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            found = tool.comfyui_running("/nonexistent/comfy", port)
        self.assertTrue(any(str(port) in item for item in found))

    def test_running_probe_is_quiet_when_nothing_runs(self):
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertEqual(tool.comfyui_running("/nonexistent/comfy", port), [])

    def test_missing_database_is_an_argument_error(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            tool.main(
                ["/nonexistent/prompts.db", "--output-dir", "/tmp"], out=io.StringIO()
            )

    def test_apply_needs_the_comfyui_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "prompts.db")
            Path(db).write_bytes(b"")
            with (
                self.assertRaises(SystemExit),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                tool.main([db, "--apply", "plan.json"], out=io.StringIO())


if __name__ == "__main__":
    unittest.main()
