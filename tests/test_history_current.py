"""Current locations require a native namespace and independent authorization."""

import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_api_images import make_png
from database.operations import PromptDatabase
from py.config import GalleryConfig
from utils.execution_outputs import snapshot_output_roots


class CurrentLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.output = self.base / "output"
        self.temp = self.base / "temp"
        self.output.mkdir()
        self.temp.mkdir()
        self.roots = [self.output]
        self.host_output = str(self.output)
        self.host_temp = str(self.temp)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch(
                "folder_paths.get_output_directory",
                lambda: self.host_output,
                create=True,
            )
        )
        self.stack.enter_context(
            patch(
                "folder_paths.get_temp_directory", lambda: self.host_temp, create=True
            )
        )
        self.stack.enter_context(
            patch.object(
                GalleryConfig, "allowed_gallery_parents", lambda: [str(self.base)]
            )
        )
        self.stack.enter_context(
            patch.object(GalleryConfig, "path_anchors", lambda: [str(self.base)])
        )
        self.api = SimpleNamespace(_get_all_output_dirs=lambda: list(self.roots))

    def module(self, name="py.api.history_current"):
        self.assertIsNotNone(
            importlib.util.find_spec(name), f"missing current-location adapter {name}"
        )
        return importlib.import_module(name)

    def reference(self, **overrides):
        result = {
            "folder_type": "output",
            "subfolder": "",
            "filename": "image.png",
            "path_style": "windows" if os.name == "nt" else "posix",
            "root_key": snapshot_output_roots(
                "windows" if os.name == "nt" else "posix",
                self.host_output,
                self.host_temp,
            ).output_key,
        }
        result.update(overrides)
        return result

    def resolve(self, reference=None, gallery=None):
        mod = self.module()
        scope = mod.snapshot_current_output_scope(self.api)
        return mod.resolve_current_candidate(
            reference or self.reference(), scope, gallery or {}
        ).public

    def test_lexical_alias_is_not_replaced_by_canonical_root_key(self):
        mod = self.module()
        path = make_png(self.output / "image.png")
        alias = self.base / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        self.host_output = str(alias)
        reference = self.reference()
        scope = mod.snapshot_current_output_scope(self.api)
        with patch.object(
            Path, "stat", side_effect=AssertionError("locator performed IO")
        ):
            self.assertEqual(
                mod.candidate_locator(reference, scope), str(alias / path.name)
            )
        candidate = mod.resolve_current_candidate(reference, scope, {}).public
        self.assertEqual(candidate["status"], "available")
        self.assertEqual(candidate["gallery_match"], "none")
        self.assertRegex(candidate["location_key"], r"^[0-9a-f]{64}$")
        wrong = self.reference(
            root_key=snapshot_output_roots("posix", str(self.output), None).output_key
        )
        self.assertEqual(
            mod.resolve_current_candidate(wrong, scope, {}).public["reason"],
            "namespace_mismatch",
        )

    def test_null_foreign_relocated_and_unsafe_descriptors_have_allowed_control(self):
        make_png(self.output / "image.png")
        self.assertEqual(self.resolve()["status"], "available")
        for changes, reason in (
            ({"root_key": None}, "root_unavailable"),
            ({"root_key": "a" * 64}, "namespace_mismatch"),
            (
                {"path_style": "windows", "subfolder": "sub", "root_key": "b" * 64},
                "foreign_path_style",
            ),
            ({"subfolder": "../outside"}, "namespace_mismatch"),
            ({"filename": "/absolute.png"}, "namespace_mismatch"),
        ):
            with self.subTest(changes=changes):
                candidate = self.resolve(self.reference(**changes))
                self.assertEqual(
                    (candidate["status"], candidate["reason"]), ("unavailable", reason)
                )
                self.assertNotIn("media", candidate)

    def test_temp_requires_current_gallery_authorization(self):
        make_png(self.temp / "image.png")
        reference = self.reference(
            folder_type="temp",
            root_key=snapshot_output_roots(
                "posix", self.host_output, self.host_temp
            ).temp_key,
        )
        self.assertEqual(self.resolve(reference)["reason"], "not_authorized")
        self.roots.append(self.temp)
        self.assertEqual(self.resolve(reference)["status"], "available")

    def test_supported_regular_readable_control_and_distinct_failures(self):
        path = make_png(self.output / "image.png")
        self.assertEqual(self.resolve()["status"], "available")
        path.unlink()
        self.assertEqual(self.resolve()["reason"], "missing")
        path.mkdir()
        self.assertEqual(self.resolve()["reason"], "not_regular_file")
        path.rmdir()
        path.write_bytes(b"media")
        with patch("os.access", return_value=False):
            self.assertEqual(self.resolve()["reason"], "unreadable")
        (self.output / "notes.txt").write_text("private")
        self.assertEqual(
            self.resolve(self.reference(filename="notes.txt"))["reason"],
            "unsupported_media",
        )

    def test_symlink_escape_never_authorizes_another_namespace(self):
        allowed = make_png(self.output / "image.png")
        outside = make_png(self.temp / "outside.png")
        self.roots.append(self.temp)
        self.assertEqual(self.resolve()["status"], "available")
        allowed.unlink()
        allowed.symlink_to(outside)
        self.assertEqual(self.resolve()["reason"], "not_authorized")

    def test_scope_tracks_configuration_targets_and_policy_but_not_bytes(self):
        mod = self.module()
        path = make_png(self.output / "image.png")
        first = mod.snapshot_current_output_scope(self.api)
        location = mod.resolve_current_candidate(self.reference(), first, {}).public[
            "location_key"
        ]
        path.write_bytes(b"replacement")
        same = mod.snapshot_current_output_scope(self.api)
        self.assertEqual(same.current_scope, first.current_scope)
        self.assertEqual(
            mod.resolve_current_candidate(self.reference(), same, {}).public[
                "location_key"
            ],
            location,
        )
        self.roots.append(self.temp)
        changed = mod.snapshot_current_output_scope(self.api)
        self.assertNotEqual(changed.current_scope, first.current_scope)
        self.roots.reverse()
        self.assertEqual(
            mod.snapshot_current_output_scope(self.api).current_scope,
            changed.current_scope,
        )
        with patch.object(
            GalleryConfig, "allowed_gallery_parents", lambda: [str(self.output)]
        ):
            self.assertNotEqual(
                mod.snapshot_current_output_scope(self.api).current_scope,
                changed.current_scope,
            )
        alias = self.base / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        self.host_output = str(alias)
        before = mod.snapshot_current_output_scope(self.api)
        alias.unlink()
        alias.symlink_to(self.temp, target_is_directory=True)
        self.assertNotEqual(
            mod.snapshot_current_output_scope(self.api).current_scope,
            before.current_scope,
        )

    def test_gallery_projection_is_snapshot_owned_exact_and_ambiguous(self):
        mod = self.module("database.history_gallery")
        db = PromptDatabase(str(self.base / "gallery.db"))
        self.addCleanup(db.close_all)
        path = make_png(self.output / "image.png")
        prompt = db.save_prompt("first", prompt_hash="first-hash")
        other = db.save_prompt("second", prompt_hash="second-hash")
        image = db.link_image_to_prompt(prompt, str(path))
        db.link_image_to_prompt(other, str(self.temp / path.name))
        conn = db.model.get_connection()
        with self.assertRaises(Exception):
            mod.read_history_gallery(conn, (str(path),))
        with db.recorded_history_snapshot():
            projected = mod.read_history_gallery(conn, (str(path),))
            self.assertTrue(conn.in_transaction)
            self.assertEqual(projected[str(path)]["gallery_match"], "unique")
            self.assertEqual(projected[str(path)]["gallery_image_id"], image)
        db.link_image_to_prompt(other, str(path))
        with db.recorded_history_snapshot():
            projected = mod.read_history_gallery(conn, (str(path),))
            self.assertEqual(projected[str(path)]["gallery_match"], "ambiguous")
            self.assertIsNone(projected[str(path)]["gallery_image_id"])
            self.assertIsNone(projected[str(path)]["saved"])
            self.assertEqual(projected[str(path)]["library"]["status"], "ambiguous")
            with self.assertRaises(ValueError):
                mod.read_history_gallery(
                    conn, tuple(str(path) + str(i) for i in range(101))
                )

    def test_gallery_display_is_bounded_without_loading_workflow_or_prompt(self):
        mod = self.module("database.history_gallery")
        db = PromptDatabase(str(self.base / "gallery.db"))
        self.addCleanup(db.close_all)
        path = make_png(self.output / "image.png")
        prompt = db.save_prompt(
            "secret prompt" * 10000, prompt_hash="large-hash", category="猫" * 12000
        )
        image = db.link_image_to_prompt(prompt, str(path))
        with db.model.get_connection() as conn:
            conn.execute(
                "UPDATE generated_images SET workflow_data=?,prompt_metadata=? "
                "WHERE id=?",
                ("private" * 100000, "private" * 100000, image),
            )
        statements = []
        conn.set_trace_callback(statements.append)
        self.addCleanup(conn.set_trace_callback, None)
        with db.recorded_history_snapshot():
            result = mod.read_history_gallery(conn, (str(path),))
        display = result[str(path)]["library"]
        self.assertEqual(display["status"], "limited")
        self.assertEqual(display["tags"], [])
        self.assertFalse(
            any(
                "workflow_data" in sql or "prompt_metadata" in sql or "p.text" in sql
                for sql in statements
            )
        )
        self.assertFalse(any(sql.startswith("COMMIT") for sql in statements))

    def test_gallery_queries_stop_after_two_owners_and_two_pair_associations(self):
        mod = self.module("database.history_gallery")
        db = PromptDatabase(str(self.base / "gallery.db"))
        self.addCleanup(db.close_all)
        path = make_png(self.output / "image.png")
        unique = make_png(self.output / "unique.png")
        prompt = db.save_prompt("owner", prompt_hash="owner")
        image = db.link_image_to_prompt(prompt, str(unique))
        with db.model.get_connection() as conn:
            for i in range(12):
                owner = conn.execute(
                    "INSERT INTO prompts(text,hash) VALUES(?,?)",
                    (str(i), str(i)),
                ).lastrowid
                pair = conn.execute(
                    "INSERT INTO prompt_pairs"
                    "(positive_prompt_id,negative_prompt_id,title) "
                    "VALUES(?,?,?)",
                    (prompt, owner, str(i)),
                ).lastrowid
                conn.execute(
                    "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                    "VALUES(?,?,'{}')",
                    (image, pair),
                )
                conn.execute(
                    "INSERT INTO generated_images"
                    "(prompt_id,image_path,filename,file_path) "
                    "VALUES(?,?,'image.png',?)",
                    (owner, str(path), str(path)),
                )
        fetched = {"owners": 0, "pairs": 0}

        def row_factory(cursor, values):
            names = {column[0] for column in cursor.description}
            if names == {"id", "prompt_id", "category"}:
                fetched["owners"] += 1
            elif names == {"id", "positive_prompt_id", "title", "category"}:
                fetched["pairs"] += 1
            return sqlite3.Row(cursor, values)

        conn.row_factory = row_factory
        with db.recorded_history_snapshot():
            result = mod.read_history_gallery(conn, (str(path), str(unique)))
        self.assertEqual(fetched, {"owners": 3, "pairs": 2})
        self.assertEqual(result[str(path)]["gallery_match"], "ambiguous")
        self.assertEqual(result[str(unique)]["gallery_match"], "unique")
        self.assertEqual(result[str(unique)]["library"]["status"], "limited")
        self.assertIsNone(result[str(unique)]["library"].get("pair_id"))

    def test_host_root_failure_and_removal_do_not_search_another_root(self):
        make_png(self.output / "image.png")
        make_png(self.temp / "image.png")
        self.roots.append(self.temp)
        reference = self.reference()
        self.assertEqual(self.resolve(reference)["status"], "available")
        with patch("folder_paths.get_output_directory", side_effect=OSError("private")):
            self.assertEqual(self.resolve(reference)["reason"], "root_unavailable")
        (self.output / "image.png").unlink()
        self.output.rmdir()
        self.assertEqual(self.resolve(reference)["reason"], "root_unavailable")

    def test_unusable_lexical_namespace_changes_still_change_scope(self):
        mod = self.module()
        make_png(self.output / "image.png")
        reference = self.reference()
        self.assertEqual(self.resolve(reference)["status"], "available")
        self.host_output = "relative-one"
        first = mod.snapshot_current_output_scope(self.api)
        self.assertEqual(
            mod.resolve_current_candidate(reference, first, {}).public["reason"],
            "root_unavailable",
        )
        self.host_output = "relative-two"
        self.assertNotEqual(
            mod.snapshot_current_output_scope(self.api).current_scope,
            first.current_scope,
        )

    def test_duplicate_owner_lookup_does_not_sort_the_entire_saved_population(self):
        mod = self.module("database.history_gallery")
        db = PromptDatabase(str(self.base / "gallery.db"))
        self.addCleanup(db.close_all)
        path = str(self.output / "image.png")
        with db.model.get_connection() as conn:
            for item in range(512):
                owner = conn.execute(
                    "INSERT INTO prompts(text) VALUES(?)", (str(item),)
                ).lastrowid
                conn.execute(
                    "INSERT INTO generated_images"
                    "(prompt_id,image_path,filename,file_path) "
                    "VALUES(?,?,'image.png',?)",
                    (owner, path, path),
                )
        steps = 0

        def bounded():
            nonlocal steps
            steps += 1
            return int(steps > 20)

        conn.set_progress_handler(bounded, 100)
        try:
            with db.recorded_history_snapshot():
                result = mod.read_history_gallery(conn, (path,))
        except sqlite3.OperationalError as error:
            self.fail(f"Two-owner lookup exceeded bounded query work: {error}")
        finally:
            conn.set_progress_handler(None, 0)
        self.assertEqual(result[path]["gallery_match"], "ambiguous")

    def test_two_same_pair_links_and_later_different_pair_withhold_pair_choice(self):
        mod = self.module("database.history_gallery")
        db = PromptDatabase(str(self.base / "gallery.db"))
        self.addCleanup(db.close_all)
        path = make_png(self.output / "image.png")
        pair = db.save_prompt_pair("owner", "negative one")
        other_pair = db.save_prompt_pair("owner", "negative two")
        owner = db.get_prompt_pair(pair)["positive_prompt_id"]
        image = db.link_image_to_prompt(owner, str(path))
        with db.model.get_connection() as conn:
            for pair_id, provenance in (
                (pair, "{}"),
                (pair, '{"source":"second"}'),
                (other_pair, "{}"),
            ):
                conn.execute(
                    "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                    "VALUES(?,?,?)",
                    (image, pair_id, provenance),
                )
        with db.recorded_history_snapshot():
            result = mod.read_history_gallery(conn, (str(path),))[str(path)]
        self.assertEqual(result["gallery_match"], "unique")
        self.assertEqual(result["gallery_image_id"], image)
        self.assertEqual(result["library"]["status"], "limited")
        self.assertIsNone(result["library"].get("pair_id"))
        self.assertIsNone(result["saved"].get("pair"))


class HistoryImportTests(unittest.TestCase):
    def test_api_import_does_not_require_a_running_host_server(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, types; "
                "sys.modules['folder_paths'] = types.ModuleType('folder_paths'); "
                "import py.api",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_minimal_host_fixture_restores_getters_after_real_route_control(self):
        program = """
import io
from pathlib import Path
import sys
import types
import unittest

host = types.ModuleType("folder_paths")
original = lambda: "unused-minimal-host-output"
host.get_output_directory = original
sys.modules["folder_paths"] = host
from tests.test_api_history_outputs import HistoryOutputAPITests
case = HistoryOutputAPITests(
    "test_complete_zero_incomplete_unavailable_and_not_captured_are_distinct"
)
try:
    runner = unittest.TextTestRunner(stream=io.StringIO())
    result = runner.run(unittest.TestSuite([case]))
    assert host.get_output_directory is original, "fixture leaked host getter"
    assert not hasattr(host, "get_temp_directory"), "fixture leaked optional getter"
    assert result.wasSuccessful(), result.errors
finally:
    if hasattr(case, "api"):
        case.api.db.close_all()
    if hasattr(case, "_anchor_patch"):
        case._anchor_patch.stop()
    if hasattr(case, "_temp_db"):
        for suffix in ("", "-wal", "-shm"):
            Path(case._temp_db.name + suffix).unlink(missing_ok=True)
    if hasattr(case, "_output_tmp"):
        case._output_tmp.cleanup()
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
