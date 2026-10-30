"""No route reads a file in a trash folder below its root (#543).

Each route is tried with the trash folder named outright, through a linked file
and through a linked folder, and with a file it must still read as the control.
The Windows spellings run only on Windows, and they do run there: outside a
developer's machine a missing short name or symlink right is a failure.
"""

import ctypes
import os
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    from tests.trash_route_audit import (
        FILE_READER_CALLS,
        KNOWN_FILE_READERS,
        ROUTE_AUDIT,
    )
    from tests.test_api_admin import _write_png
    from tests.test_image_delete_routes import ImageDeleteTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from trash_route_audit import FILE_READER_CALLS, KNOWN_FILE_READERS, ROUTE_AUDIT
    from test_api_admin import _write_png
    from test_image_delete_routes import ImageDeleteTestCase

from py.api.duplicate_review import OUTSIDE_SOURCE, file_problem
from py.api.images import media_thumbnail_rel
from py.api.thumbnail_auto import OVER, _quick_source, _thumbnail_source
from utils.media_folders import in_trash_below_roots, in_trash_folder, is_trash_folder

ON_CI = bool(os.environ.get("GITHUB_ACTIONS"))
NAMED = (".Trash-1000/files/x.png", "$RECYCLE.BIN/x.png", "sub/.Trashes/501/x.png")
LINKED = ("link.png", "linkdir/x.png")


def _short_name(path):
    """The 8.3 spelling of *path* (Windows only)."""
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024)
    return buffer.value


class _GateCase(ImageDeleteTestCase):
    async def get_application(self):
        app = await super().get_application()
        self.root = self.output_dir
        _write_png(self.root / "kept.png", prompt_text="kept")
        for relative in NAMED:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            _write_png(self.root / relative, prompt_text="thrown away")
        return app

    def links(self):
        """Links into the trash folder, or a skip where they cannot be made."""
        inside = self.root / ".Trash-1000" / "files"
        try:
            os.symlink(inside / "x.png", self.root / "link.png")
            os.symlink(inside, self.root / "linkdir", target_is_directory=True)
        except OSError:
            if ON_CI:
                raise
            self.skipTest("this machine cannot make symlinks")

    def query(self):
        scope = self.api._gallery_root_scope(self.api._get_all_output_dirs()[0], 0)
        return {"root": "0", "root_scope": scope}

    def body(self, relative):
        return self.target(relative)

    async def status(self, method, url, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        await response.read()
        return response.status

    async def serve(self, relative):
        url = "/prompt_manager/images/serve/" + urllib.parse.quote(relative)
        return await self.status("GET", url, params=self.query())

    async def inspect(self, relative):
        url = "/prompt_manager/images/inspect/" + urllib.parse.quote(relative)
        return await self.status("GET", url, params=self.query())

    async def rescope(self, relative):
        response = await self.client.post(
            "/prompt_manager/images/rescope", json={"target": self.body(relative)}
        )
        return response.status, await response.json()

    async def autotag(self, relative):
        """The target the legacy single/apply bodies pick, by path."""
        target, error = await self.api._select_autotag_target(
            {"root_index": 0, "path": relative}
        )
        return target, error

    async def assert_hidden_everywhere(self, relative):
        self.assertEqual(await self.serve(relative), 404)
        self.assertEqual(await self.inspect(relative), 404)
        self.assertIsNone(self.api._reveal_path(relative, self.root))
        status, body = await self.rescope(relative)
        self.assertEqual((status, body.get("code")), (404, "file-missing"))
        target, error = await self.autotag(relative)
        self.assertIsNone(target)
        self.assertEqual(error.status, 403)
        answer = await self.client.post(
            "/prompt_manager/images/link",
            json={"prompt_id": self.save("p"), "image_path": str(self.root / relative)},
        )
        self.assertEqual(answer.status, 404)


class TestTheGate(_GateCase):
    async def test_the_control_file_still_reads_on_every_route(self):
        self.assertEqual(await self.serve("kept.png"), 200)
        self.assertEqual(await self.inspect("kept.png"), 200)
        self.assertIsNotNone(self.api._reveal_path("kept.png", self.root))
        self.assertEqual((await self.rescope("kept.png"))[0], 200)
        target, _error = await self.autotag("kept.png")
        self.assertIsNotNone(target)
        answer = await self.client.post(
            "/prompt_manager/images/link",
            json={
                "prompt_id": self.save("p"),
                "image_path": str(self.root / "kept.png"),
            },
        )
        self.assertEqual(answer.status, 200)
        observed = await self.client.post(
            "/prompt_manager/image-annotations/observe",
            json={"target": self.body("kept.png")},
        )
        self.assertEqual(observed.status, 200)
        added = await self.client.post(
            "/prompt_manager/collections/items",
            json={"name": "c", "items": [self.body("kept.png")]},
        )
        self.assertLess(added.status, 300)

    async def test_a_named_trash_folder_is_hidden_on_every_route(self):
        for relative in NAMED:
            with self.subTest(relative=relative):
                await self.assert_hidden_everywhere(relative)

    async def test_a_linked_file_or_folder_is_hidden_on_every_route(self):
        self.links()
        for relative in LINKED:
            with self.subTest(relative=relative):
                await self.assert_hidden_everywhere(relative)

    async def test_serving_by_id_autotag_by_id_and_a_prompts_media(self):
        self.links()
        roots = self.api._get_all_output_dirs()
        for relative in (*NAMED, *LINKED):
            with self.subTest(relative=relative):
                image_id = self.api.db.link_image_to_prompt(
                    str(self.save(f"linked {relative}")), str(self.root / relative)
                )
                url = f"/prompt_manager/images/{image_id}/file"
                self.assertEqual(await self.status("GET", url), 404)
                target, error = await self.api._select_autotag_target(
                    {"image_id": image_id}
                )
                self.assertIsNone(target)
                self.assertEqual(error.status, 403)
                records = [{"image_path": str(self.root / relative)}]
                self.assertEqual(self.api._library_media(records, roots), [])
        kept = [{"image_path": str(self.root / "kept.png")}]
        self.assertEqual(len(self.api._library_media(kept, roots)), 1)

    async def test_annotations_refuse_a_trash_path_and_a_link(self):
        self.links()
        url = "/prompt_manager/image-annotations/observe"
        for relative in NAMED:
            with self.subTest(relative=relative):
                body = {"target": self.body(relative)}
                self.assertEqual(await self.status("POST", url, json=body), 404)
        for relative in LINKED:
            with self.subTest(relative=relative):
                body = {"target": self.body(relative)}
                status = await self.status("POST", url, json=body)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)

    async def test_collections_refuse_a_trash_path(self):
        for relative in NAMED:
            with self.subTest(relative=relative):
                answer = await self.client.post(
                    "/prompt_manager/collections/items",
                    json={"name": "c", "items": [self.body(relative)]},
                )
                self.assertEqual(answer.status, 404)

    async def test_the_exact_path_listing_does_not_name_it(self):
        self.links()
        for relative in (*NAMED, *LINKED):
            with self.subTest(relative=relative):
                response = await self.client.get(
                    "/prompt_manager/images/output",
                    params={"mode": "workspace", "path": relative},
                )
                page = await response.json()
                self.assertEqual(page.get("images", []), [])

    async def test_no_preview_is_made_through_a_linked_folder(self):
        self.links()
        preview = Path(media_thumbnail_rel(Path("linkdir/x.png"))).parts
        self.assertIsNone(_thumbnail_source(self.root, preview))
        kept = Path(media_thumbnail_rel(Path("kept.png"))).parts
        self.assertIsNotNone(_thumbnail_source(self.root, kept))

    async def test_the_preview_fallback_past_the_cap_is_gated_too(self):
        # The gate's OVER branch looks the source up by name and serves it.
        self.links()
        gate = self.api._preview_gate()
        with patch.object(gate, "run", AsyncMock(return_value=OVER)):
            linked = media_thumbnail_rel(Path("linkdir/x.png"))
            self.assertEqual(await self.serve(linked), 404)
            kept = media_thumbnail_rel(Path("kept.png"))
            self.assertEqual(await self.serve(kept), 200)
        self.assertIsNone(
            _quick_source(
                self.root, Path(media_thumbnail_rel(Path("linkdir/x.png"))).parts
            )
        )

    async def test_the_legacy_duplicate_delete_never_unlinks_from_a_trash(self):
        self.links()
        for relative in (*NAMED, *LINKED):
            with self.subTest(relative=relative):
                reason = self.api._delete_one_duplicate(relative, self.root)
                self.assertEqual(reason, "it is in a trash folder")
                self.assertTrue((self.root / relative).exists())

    async def test_a_link_inside_a_trash_folder_is_never_removed(self):
        self.links()
        link = self.root / ".Trash-1000" / "files" / "back.png"
        os.symlink(self.root / "kept.png", link)
        reason = self.api._delete_one_duplicate(".Trash-1000/files/back.png", self.root)
        self.assertEqual(reason, "it is in a trash folder")
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.root / "kept.png").exists())

    async def test_the_legacy_delete_removes_the_link_itself_and_its_own_preview(self):
        self.links()
        os.symlink(self.root / "kept.png", self.root / "alias.png")
        thumbs = {}
        for name in ("kept.png", "alias.png"):
            thumbs[name] = self.root / media_thumbnail_rel(Path(name))
            thumbs[name].parent.mkdir(parents=True, exist_ok=True)
            thumbs[name].write_bytes(b"preview")
        self.assertIsNone(self.api._delete_one_duplicate("alias.png", self.root))
        self.assertFalse((self.root / "alias.png").is_symlink())
        self.assertTrue((self.root / "kept.png").is_file())
        self.assertFalse(thumbs["alias.png"].exists())
        self.assertTrue(thumbs["kept.png"].exists())

    async def test_a_linked_folder_out_of_the_root_removes_nothing_there(self):
        self.links()
        outside = self.outside_dir / "elsewhere"
        outside.mkdir(parents=True)
        os.symlink(self.root / "kept.png", outside / "back.png")
        os.symlink(outside, self.root / "out", target_is_directory=True)
        reason = self.api._delete_one_duplicate("out/back.png", self.root)
        self.assertEqual(reason, "outside output directory")
        self.assertTrue((outside / "back.png").is_symlink())

    async def test_a_duplicate_removal_review_refuses_a_trash_folder(self):
        path = self.root / ".Trash-1000" / "files" / "x.png"
        problem = file_problem(
            path, self.root, None, path.stat().st_size, hash_check=False
        )
        self.assertEqual(problem, OUTSIDE_SOURCE)

    async def test_a_source_named_like_a_trash_folder_still_reads(self):
        source = self.root / ".Trash-77"
        source.mkdir()
        _write_png(source / "mine.png", prompt_text="mine")
        self.api._get_all_output_dirs = lambda: [source]
        self.assertEqual(await self.serve("mine.png"), 200)
        self.assertEqual(await self.inspect("mine.png"), 200)
        self.assertEqual((await self.rescope("mine.png"))[0], 200)
        self.assertIsNotNone((await self.autotag("mine.png"))[0])

    async def test_delete_still_moves_a_listed_file(self):
        await self.review(["kept.png"])
        _plan, job = await self.delete(["kept.png"], "trash")
        self.assertEqual(job.detail()["counters"]["trashed"], 1)
        self.assertFalse((self.root / "kept.png").exists())


class TestPredicates(unittest.TestCase):
    def test_trailing_dots_and_spaces_are_ignored(self):
        for name in ("$RECYCLE.BIN.", "$recycle.bin ", ".Trash-1000. ", ".Trashes..."):
            self.assertTrue(is_trash_folder(name), name)
        for name in ("trash.", "...", " ", ".Trash-x."):
            self.assertFalse(is_trash_folder(name), name)

    def test_either_separator(self):
        self.assertTrue(in_trash_folder("a\\.Trash\\b.png"))
        self.assertFalse(in_trash_folder(".Trash"))

    def test_a_path_no_root_visibly_holds_fails_closed(self):
        self.assertTrue(in_trash_below_roots("/x/.Trash-1/files/a.png", ["/other"]))
        self.assertFalse(in_trash_below_roots("/x/a.png", ["/other"]))


@unittest.skipUnless(os.name == "nt", "Windows spellings of one folder")
class TestWindowsSpellings(_GateCase):
    def spellings(self):
        spelled = [
            "$RECYCLE.BIN./x.png",
            "$recycle.bin/x.png",
            ".TRASH-1000/files/x.png",
            ".Trash-1000./files/x.png",
        ]
        short = _short_name(self.root / ".Trash-1000")
        if os.path.basename(short).casefold() == ".trash-1000":
            if ON_CI:
                self.fail("the job's work disk gave no 8.3 short name")
            self.skipTest("this disk makes no 8.3 short names")
        spelled.append(os.path.basename(short) + "/files/x.png")
        return spelled

    async def test_every_spelling_reaches_the_file_and_is_hidden(self):
        for relative in self.spellings():
            with self.subTest(relative=relative):
                self.assertTrue((self.root / relative).is_file(), "spelling is real")
                await self.assert_hidden_everywhere(relative)

    async def test_a_folder_spelled_with_a_trailing_space_is_hidden_by_name(self):
        # Measured on the Windows job (run 41123443497): unlike a trailing dot,
        # this spelling does not reach the file through a longer path. The name
        # check still answers for it, whatever a volume does.
        await self.assert_hidden_everywhere("$RECYCLE.BIN /x.png")

    async def test_a_root_spelled_in_another_case_does_not_fail_open(self):
        inside = self.root / ".Trash-1000" / "files" / "x.png"
        self.assertTrue(in_trash_below_roots(inside, [str(self.root).upper()]))
        kept = self.root / "kept.png"
        self.assertFalse(in_trash_below_roots(kept, [str(self.root).upper()]))


class TestEveryRouteIsClassified(_GateCase):
    async def test_a_new_route_must_say_how_it_treats_media_files(self):
        registered = {
            f"{route.method} {route.resource.canonical}"
            for route in self.app.router.routes()
            if route.method != "HEAD"
            and route.resource is not None
            and route.resource.canonical.startswith("/prompt_manager")
        }
        audited = {route for routes in ROUTE_AUDIT.values() for route in routes}
        self.assertEqual(
            registered ^ audited,
            set(),
            "add the route to tests/trash_route_audit.py with its media check",
        )

    def test_a_file_reader_in_the_api_layer_is_a_known_one(self):
        # A new way to open a media file must be looked at for the same gate.
        api = Path(__file__).resolve().parent.parent / "py" / "api"
        found = set()
        for module in api.glob("*.py"):
            text = module.read_text(encoding="utf-8")
            found.update(
                f"{module.name}:{call}" for call in FILE_READER_CALLS if call in text
            )
        self.assertEqual(found ^ KNOWN_FILE_READERS, set())
