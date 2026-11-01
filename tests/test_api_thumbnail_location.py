"""The thumbnail location setting (#320 PR 2): parsing, validation, source
keys, the marker, the settings payloads and reverse containment.

Spelling rules are tested on both path flavors (``ntpath`` and
``posixpath``) as text; filesystem rules run on the native one.
"""

import json
import ntpath
import os
import posixpath
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("server", MagicMock())

from py.api import preview_location as pl  # noqa: E402
from py.api import preview_store  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class TestParse(unittest.TestCase):
    def test_missing_or_source_is_inside_each_source(self):
        for value in (None, {"mode": "source"}, {}):
            with self.subTest(value=value):
                parsed = pl.parse_location(value)
                self.assertEqual((parsed.mode, parsed.problem), ("source", None))

    def test_a_folder_location_is_kept_as_spelled(self):
        parsed = pl.parse_location(
            {"mode": "folder", "path": "D:\\Thumbs", "id": "x", "install": "i"},
            flavor=ntpath,
        )
        self.assertEqual(
            (parsed.mode, parsed.path, parsed.id, parsed.problem),
            ("folder", "D:\\Thumbs", "x", None),
        )

    def test_invalid_values_never_fall_back_to_the_sources(self):
        for value in (
            "folder",
            {"mode": "elsewhere"},
            {"mode": "folder", "path": "relative", "id": "x"},
            {"mode": "folder", "path": "/abs"},
            {"mode": "folder", "path": "/abs", "id": ""},
            {"mode": "folder", "path": 7, "id": "x"},
        ):
            with self.subTest(value=value):
                parsed = pl.parse_location(value, flavor=posixpath)
                self.assertEqual((parsed.mode, parsed.problem), ("folder", "invalid"))


class TestForm(unittest.TestCase):
    def test_windows_spellings(self):
        cases = {
            "D:\\Thumbs": None,
            "\\\\server\\share\\thumbs": None,
            "C:thumbs": "location-not-absolute",
            "\\thumbs": "location-not-absolute",
            "thumbs": "location-not-absolute",
            "": "location-not-absolute",
            " D:\\Thumbs": "location-not-absolute",
            "\\\\?\\D:\\Thumbs": "location-device-path",
            "\\\\.\\D:\\Thumbs": "location-device-path",
            "D:\\CON\\x": "location-invalid-name",
            "D:\\com1.txt": "location-invalid-name",
            "D:\\thumbs.": "location-invalid-name",
            "D:\\thumbs ": "location-invalid-name",
            "D:\\a|b": "location-invalid-name",
            "D:\\a?b": "location-invalid-name",
            "D:\\a\x01b": "location-invalid-name",
        }
        for path, code in cases.items():
            with self.subTest(path=path):
                self.assertEqual(pl.form_problem(path, flavor=ntpath), code)

    def test_posix_spellings(self):
        cases = {
            "/mnt/fast/thumbs": None,
            "mnt/thumbs": "location-not-absolute",
            "/mnt/nul": "location-invalid-name",
            "/mnt/a:b": "location-invalid-name",
            "//?/x": "location-device-path",
        }
        for path, code in cases.items():
            with self.subTest(path=path):
                self.assertEqual(pl.form_problem(path, flavor=posixpath), code)

    def test_roots_and_home_are_too_broad(self):
        self.assertTrue(pl.is_too_broad("C:\\", home="C:\\Users\\a", flavor=ntpath))
        self.assertTrue(
            pl.is_too_broad("\\\\server\\share", home="C:\\Users\\a", flavor=ntpath)
        )
        self.assertTrue(
            pl.is_too_broad("C:\\users\\A", home="C:\\Users\\a", flavor=ntpath)
        )
        self.assertTrue(pl.is_too_broad("/", home="/home/a", flavor=posixpath))
        self.assertTrue(pl.is_too_broad("/home/a/", home="/home/a", flavor=posixpath))
        self.assertFalse(
            pl.is_too_broad("/home/a/thumbs", home="/home/a", flavor=posixpath)
        )

    def test_long_windows_paths_are_refused_without_long_paths(self):
        short = "D:\\" + "a" * 100
        long = "D:\\" + "a" * 200
        self.assertFalse(pl.too_long_for_windows(short, long_paths=False))
        self.assertTrue(pl.too_long_for_windows(long, long_paths=False))
        self.assertFalse(pl.too_long_for_windows(long, long_paths=True))


class TestSourceKeys(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(pl.slug("ComfyUI Output"), "comfyui-output")
        self.assertEqual(pl.slug("Ünïcödé_Fôlder"), "unicode_folder")
        self.assertEqual(pl.slug(""), "source")
        self.assertEqual(pl.slug("!!!"), "source")
        self.assertEqual(len(pl.slug("x" * 80)), 24)

    def test_keys_are_a_slug_plus_a_hash(self):
        key = pl.source_key("D:\\AI\\Output", "d:\\ai\\output", {})
        self.assertRegex(key, r"^output-[0-9a-f]{8}$")
        self.assertRegex(pl.source_key("D:\\", "d:\\", {}), r"^source-[0-9a-f]{8}$")
        self.assertRegex(
            pl.source_key("\\\\nas\\share\\Out", "\\\\nas\\share\\out", {}),
            r"^out-[0-9a-f]{8}$",
        )

    def test_a_taken_key_grows_its_hash(self):
        first = pl.source_key("/a/out", "/a/out", {})
        taken = {first: "/someone/else"}
        second = pl.source_key("/a/out", "/a/out", taken)
        self.assertRegex(second, r"^out-[0-9a-f]{12}$")
        taken[second] = "/another"
        self.assertRegex(
            pl.source_key("/a/out", "/a/out", taken), r"^out-[0-9a-f]{16}$"
        )
        self.assertEqual(pl.source_key("/a/out", "/a/out", {first: "/a/out"}), first)

    def test_lookup_by_spelling_identity_then_file_id(self):
        keys = {
            "out-0123abcd": {
                "path": "D:\\Out",
                "identity": "d:\\out",
                "dev_ino": [1, 2],
            },
            "lib-0123abcd": {"path": "E:\\Lib", "identity": "e:\\lib", "dev_ino": None},
        }
        self.assertEqual(pl.find_key(keys, "D:\\Out", "x", None), "out-0123abcd")
        self.assertEqual(pl.find_key(keys, "Z:\\Out", "d:\\out", None), "out-0123abcd")
        # A letter spelling and its UNC spelling: the same file id.
        self.assertEqual(
            pl.find_key(keys, "\\\\nas\\out", "\\\\nas\\out", (1, 2)), "out-0123abcd"
        )
        self.assertIsNone(pl.find_key(keys, "F:\\New", "f:\\new", (9, 9)))


class TestMarker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name

    def test_init_creates_once_and_adopts_after(self):
        first = pl.init_folder(self.folder, created_by_pm=True)
        self.assertEqual(pl.init_folder(self.folder), first)
        marker = pl.read_marker(self.folder)
        self.assertEqual(marker["id"], first)
        self.assertTrue(marker["created_by_pm"])
        self.assertEqual(marker["version"], 1)

    def test_writes_merge_what_another_writer_added(self):
        pl.init_folder(self.folder)
        pl.write_marker(
            self.folder, lambda m: m["keys"].update({"a-0123abcd": {"install": "1"}})
        )
        pl.write_marker(
            self.folder, lambda m: m["keys"].update({"b-0123abcd": {"install": "2"}})
        )
        self.assertEqual(
            sorted(pl.read_marker(self.folder)["keys"]), ["a-0123abcd", "b-0123abcd"]
        )
        # No temp file is left beside it.
        self.assertEqual(os.listdir(self.folder), [pl.MARKER_NAME])

    def test_two_processes_adding_keys_at_once_keep_every_key(self):
        """Installs sharing one folder (bot review, P2): the read-modify-write
        of the marker is locked across processes, so no writer drops another's
        key. Each child widens the window by pausing inside ``mutate``."""
        import subprocess

        pl.init_folder(self.folder)
        child = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from py.api import preview_location as pl\n"
            "folder, tag, start = sys.argv[2], sys.argv[3], float(sys.argv[4])\n"
            "while time.time() < start: time.sleep(0.001)\n"
            "for n in range(8):\n"
            "    def add(m, n=n):\n"
            "        time.sleep(0.01)\n"
            "        m['keys'][f'{tag}-{n:08x}'] = {'install': tag}\n"
            "    pl.write_marker(folder, add)\n"
        )
        import time

        start = str(time.time() + 1.0)
        procs = [
            subprocess.Popen(  # nosec B603 - this interpreter, fixed arguments
                [sys.executable, "-c", child, str(REPO), self.folder, tag, start]
            )
            for tag in ("a", "b", "c")
        ]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=60), 0)
        keys = pl.read_marker(self.folder)["keys"]
        self.assertEqual(len(keys), 24, sorted(keys))
        self.assertEqual(os.listdir(self.folder), [pl.MARKER_NAME])

    def test_a_lock_left_by_a_crashed_writer_is_taken_over_when_stale(self):
        pl.init_folder(self.folder)
        lock = os.path.join(self.folder, pl.MARKER_LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write("crashed")
        old = pl.time.time() - pl.MARKER_LOCK_STALE - 5
        os.utime(lock, (old, old))
        pl.write_marker(self.folder, lambda m: m["keys"].update({"a-0123abcd": {}}))
        self.assertIn("a-0123abcd", pl.read_marker(self.folder)["keys"])
        self.assertFalse(os.path.exists(lock))

    def test_a_held_lock_times_out_without_writing(self):
        pl.init_folder(self.folder)
        lock = os.path.join(self.folder, pl.MARKER_LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write("held")
        with patch.object(pl, "MARKER_LOCK_TIMEOUT", 0.05):
            with self.assertRaises(TimeoutError):
                pl.write_marker(self.folder, lambda m: m["keys"].update({"x": {}}))
        self.assertTrue(os.path.exists(lock))  # never taken from its holder
        self.assertEqual(pl.read_marker(self.folder)["keys"], {})

    def test_a_folder_without_the_marker_is_missing(self):
        self.assertEqual(pl.folder_state(self.folder, "x")[:2], (False, "missing"))
        missing = os.path.join(self.folder, "gone")
        self.assertEqual(pl.folder_state(missing, "x")[:2], (False, "missing"))
        marker_id = pl.init_folder(self.folder)
        self.assertEqual(pl.folder_state(self.folder, marker_id)[:2], (True, None))
        self.assertEqual(
            pl.folder_state(self.folder, "other")[:2], (False, "marker-mismatch")
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_a_linked_folder_is_not_available(self):
        real = os.path.join(self.folder, "real")
        os.mkdir(real)
        marker_id = pl.init_folder(real)
        link = os.path.join(self.folder, "link")
        try:
            os.symlink(real, link, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        self.assertEqual(pl.folder_state(link, marker_id)[:2], (False, "invalid"))


class TestValidation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.realpath(self._tmp.name)
        self.source = os.path.join(self.base, "output")
        self.node = os.path.join(self.base, "node")
        os.mkdir(self.source)
        os.mkdir(self.node)
        self.context = pl.Context(
            roots=(self.source,),
            node_folder=self.node,
            default_path=os.path.join(self.node, "data", "thumbnails"),
            home=os.path.join(self.base, "home"),
            environ={},
            long_paths=True,
            folder_paths=None,
        )

    def problem(self, path, purpose="move", **changes):
        context = self.context
        if changes:
            context = pl.Context(**{**context.__dict__, **changes})
        return pl.location_problem(path, purpose=purpose, context=context)

    def test_an_empty_new_folder_beside_the_sources_is_accepted(self):
        self.assertIsNone(self.problem(os.path.join(self.base, "thumbs")))
        os.mkdir(os.path.join(self.base, "empty"))
        Path(self.base, "empty", "Thumbs.db").write_bytes(b"")
        self.assertIsNone(self.problem(os.path.join(self.base, "empty")))

    def test_codes(self):
        cases = {
            "relative": "location-not-absolute",
            os.path.join(self.source, "thumbs"): "location-served",
            os.path.join(self.node, "thumbs"): "location-node-folder",
            self.base: "location-contains-source",
            os.path.join(self.base, "no", "parent"): "location-parent-missing",
        }
        for path, code in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.problem(path), code)

    def test_the_default_may_sit_in_the_node_folder_and_make_its_parents(self):
        self.assertIsNone(self.problem(self.context.default_path))

    def test_the_folder_in_use_is_the_same(self):
        folder = os.path.join(self.base, "thumbs")
        os.mkdir(folder)
        self.assertEqual(self.problem(folder, current_path=folder), "location-same")

    def test_a_folder_with_other_files_is_not_empty_unless_it_is_ours(self):
        folder = os.path.join(self.base, "full")
        os.mkdir(folder)
        Path(folder, "photo.jpg").write_bytes(b"x")
        self.assertEqual(self.problem(folder), "location-not-empty")
        pl.init_folder(folder)
        self.assertIsNone(self.problem(folder))

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX modes")
    def test_a_folder_that_refuses_writes(self):
        folder = os.path.join(self.base, "ro")
        os.mkdir(folder)
        os.chmod(folder, 0o500)
        self.addCleanup(os.chmod, folder, 0o700)
        self.assertEqual(self.problem(folder), "location-not-writable")

    def test_a_stat_that_does_not_answer_is_unavailable(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck(*_args):
            release.wait(10)

        with patch.object(pl, "_filesystem_problem", stuck):
            code = pl.location_problem(
                os.path.join(self.base, "thumbs"),
                purpose="move",
                context=self.context,
                timeout=0.1,
            )
        self.assertEqual(code, "location-unavailable")

    def test_a_long_windows_path(self):
        path = os.path.join(self.base, "t" * 220)
        self.assertEqual(self.problem(path, long_paths=False), "location-path-too-long")

    def test_every_code_has_a_message_without_a_path(self):
        for code, (status, text) in pl.MESSAGES.items():
            with self.subTest(code=code):
                self.assertIn(status, (400, 409, 507))
                self.assertTrue(text.endswith("."))


class TestNoLegacySaveCallers(unittest.TestCase):
    def test_save_to_file_has_no_callers(self):
        offenders = []
        for folder in ("py", "utils", "database"):
            for path in (REPO / folder).rglob("*.py"):
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    code = line.split("#", 1)[0]
                    if (
                        re.search(r"\bsave_to_file\(", code)
                        and "def save_to_file" not in code
                    ):
                        if "PromptManagerConfig.save_to_file('" in line:
                            continue  # its own docstring example
                        offenders.append(f"{path.relative_to(REPO)}:{number}")
        self.assertEqual(offenders, [])


try:
    from tests.test_api_images import ImageAPITestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import ImageAPITestCase


class TestLocationRoutes(ImageAPITestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.addCleanup(preview_store.configure, None)

    def use_folder(self):
        folder = self.comfy_dir / "thumbs"
        folder.mkdir()
        marker_id = pl.init_folder(str(folder), created_by_pm=True)
        preview_store.configure(
            {"mode": "folder", "path": str(folder), "id": marker_id}
        )
        return folder

    async def test_settings_get_names_the_location_with_its_path(self):
        folder = self.use_folder()
        resp = await self.client.get("/prompt_manager/settings")
        location = (await resp.json())["thumbnail_location"]
        self.assertEqual(location["mode"], "folder")
        self.assertEqual(location["path"], str(folder))
        self.assertTrue(location["available"])
        self.assertIsNone(location["problem"])
        self.assertIn("default_path", location)

    async def test_settings_post_never_changes_the_location(self):
        resp = await self.client.post(
            "/prompt_manager/settings",
            json={"thumbnail_location": {"mode": "folder", "path": "/x"}},
        )
        self.assertEqual(resp.status, 409)
        body = await resp.json()
        self.assertEqual(body["code"], "location-needs-move")
        self.assertNotIn("/x", body["error"])

    async def test_status_has_the_location_but_no_path(self):
        folder = self.use_folder()
        resp = await self.client.get("/prompt_manager/images/thumbnails/status")
        body = await resp.text()
        self.assertNotIn(str(folder), body)
        self.assertEqual(
            json.loads(body)["location"],
            {"mode": "folder", "available": True, "problem": None},
        )

    async def test_a_source_inside_or_around_the_folder_is_refused(self):
        from py.api import sources

        folder = self.use_folder()
        inside = folder / "sub"
        inside.mkdir()
        with patch(
            "py.config.GalleryConfig.gallery_root_problem",
            classmethod(lambda cls, path: (None, "")),
        ):
            for path in (inside, folder, self.comfy_dir):
                with self.subTest(path=path):
                    code, reason = sources.source_problem(str(path))
                    self.assertEqual(code, "holds-thumbnails")
                    self.assertEqual(reason, pl.HOLDS_THUMBNAILS_REASON)
            self.assertEqual(sources.source_problem(str(self.output_dir))[0], None)

    async def test_a_library_move_into_the_folder_is_refused(self):
        from py.api import library_location as routes

        folder = self.use_folder()
        self.assertTrue(routes._inside_thumbnail_folder(str(folder / "lib")))
        self.assertFalse(routes._inside_thumbnail_folder(str(self.output_dir)))

    def test_startup_makes_a_location_inside_a_source_invalid(self):
        inside = self.output_dir / "thumbs"
        inside.mkdir()
        marker_id = pl.init_folder(str(inside))
        from py.config import PromptManagerConfig

        value = {"mode": "folder", "path": str(inside), "id": marker_id}
        with patch.object(PromptManagerConfig, "THUMBNAIL_LOCATION", value):
            location = self.api.start_preview_location()
        self.assertEqual((location.available, location.problem), (False, "invalid"))


if __name__ == "__main__":
    unittest.main()
