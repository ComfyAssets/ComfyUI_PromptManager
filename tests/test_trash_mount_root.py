"""An output folder that is itself a mount point (#543).

The freedesktop per-drive trash is then inside the output root. Only the mount's
own ``.Trash-$uid`` and ``.Trash/$uid`` are allowed, only for a root that is
exactly the mount root; every walker and media route ignores them.

Every class runs on a disposable tree with a fake mount table. With
``PM_TRASH_MOUNT=<empty folder that is a real mount>`` the same tests run on that
real mount (``.github/workflows/portability.yml``, ``native-trash-linux``, or
locally ``unshare -Urm`` over a tmpfs); that folder is the only real place they
touch, and the fake-table-only cases skip.
"""

import asyncio
import errno
import os
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.disk_waits import DISK_BOUND_SECONDS
    from tests.test_api_admin import _write_png
    from tests.test_image_delete_routes import ImageDeleteTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from disk_waits import DISK_BOUND_SECONDS
    from test_api_admin import _write_png
    from test_image_delete_routes import ImageDeleteTestCase

from py.api import images as images_module  # noqa: E402
from py.api.admin import _collect_media_files  # noqa: E402
from py.api.autotag_routes import AutotagRoutesMixin  # noqa: E402
from utils import trash  # noqa: E402
from utils.image_monitor import ImageGenerationHandler  # noqa: E402

POSIX = os.name == "posix" and sys.platform != "darwin"
REAL_MOUNT = os.environ.get("PM_TRASH_MOUNT")
UID = os.getuid() if hasattr(os, "getuid") else -1


def _empty(folder):
    """Remove what a test left inside *folder*, never the folder itself."""
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isdir(path) and not os.path.islink(path):
            os.chmod(path, 0o700)
            shutil.rmtree(path)
        else:
            os.unlink(path)


def restore_as_a_file_manager_does(top, trash_dir, name):
    """Put back one trashed file from its ``.trashinfo``, as the spec's readers do."""
    info_path = os.path.join(trash_dir, "info", name + ".trashinfo")
    with open(info_path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    recorded = next(line[5:] for line in lines if line.startswith("Path="))
    relative = os.fsdecode(urllib.parse.unquote_to_bytes(recorded))
    # A top-directory trash records a path relative to the mount (spec 1.0).
    assert not os.path.isabs(relative), recorded
    destination = os.path.join(top, relative)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.rename(os.path.join(trash_dir, "files", name), destination)
    os.unlink(info_path)
    return destination


@unittest.skipUnless(POSIX, "freedesktop trash is the Linux/BSD backend")
class MountRootBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pm-trash-root-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data_home = os.path.join(self.tmp, "xdg")
        os.makedirs(self.data_home)
        if REAL_MOUNT:
            self.assertTrue(os.path.ismount(REAL_MOUNT), "PM_TRASH_MOUNT is no mount")
            self.drive = os.path.realpath(REAL_MOUNT)
            self.addCleanup(_empty, self.drive)
            self.backend = trash.FreedesktopBackend(data_home=self.data_home)
        else:
            self.drive = os.path.join(self.tmp, "drive")
            os.makedirs(self.drive)
            self.backend = self._fake_table()
        self.dash = os.path.join(self.drive, f".Trash-{UID}")

    def _fake_table(self, *extra, uid=None):
        mounts = ["/", os.path.realpath(self.drive), *extra]
        return trash.FreedesktopBackend(
            data_home=self.data_home, mounts=lambda: list(mounts), uid=uid
        )

    def _file(self, relative="a.png", content=b"png-bytes"):
        path = os.path.join(self.drive, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def _assert_refused(self, path, roots, code):
        self.assertEqual(self.backend.available(path, roots), code)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, roots)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(os.path.lexists(path))

    def _sticky_shared(self):
        shared = os.path.join(self.drive, ".Trash")
        os.mkdir(shared)
        os.chmod(shared, 0o1777)
        return shared

    # ── the two allowed folders ───────────────────────────────────────

    def test_a_mount_root_output_uses_its_dash_uid_trash(self):
        path = self._file()
        identity = os.lstat(path)
        self.assertIsNone(self.backend.available(path, [self.drive]))
        self.assertEqual(os.listdir(self.drive), ["a.png"], "available wrote nothing")
        label = self.backend.move(path, [self.drive])
        self.assertEqual(label, f".Trash-{UID} on its drive")
        self.assertFalse(os.path.lexists(path))
        moved = os.lstat(os.path.join(self.dash, "files", "a.png"))
        self.assertEqual(
            (moved.st_dev, moved.st_ino), (identity.st_dev, identity.st_ino)
        )
        self.assertEqual(stat.S_IMODE(os.lstat(self.dash).st_mode), 0o700)

    def test_a_mount_root_output_uses_a_sticky_shared_trash_first(self):
        shared = self._sticky_shared()
        path = self._file()
        label = self.backend.move(path, [self.drive])
        self.assertEqual(label, f"{os.path.join('.Trash', str(UID))} on its drive")
        self.assertTrue(
            os.path.exists(os.path.join(shared, str(UID), "files", "a.png"))
        )
        self.assertFalse(os.path.lexists(self.dash))

    def test_a_nested_output_root_beside_the_mount_root_is_fine(self):
        path = self._file("sub/a.png")
        roots = [self.drive, os.path.join(self.drive, "sub")]
        self.assertIsNone(self.backend.available(path, roots))
        self.backend.move(path, roots)
        self.assertTrue(os.path.exists(os.path.join(self.dash, "files", "a.png")))

    # ── the spec's checks stay ────────────────────────────────────────

    def test_a_shared_trash_without_the_sticky_bit_is_passed_over(self):
        shared = os.path.join(self.drive, ".Trash")
        os.mkdir(shared, 0o777)
        os.chmod(shared, 0o777)
        path = self._file()
        self.backend.move(path, [self.drive])
        self.assertEqual(os.listdir(shared), [])
        self.assertTrue(os.path.exists(os.path.join(self.dash, "files", "a.png")))

    def test_a_shared_trash_that_is_a_link_is_passed_over(self):
        real = os.path.join(self.tmp, "sticky")
        os.mkdir(real)
        os.chmod(real, 0o1777)
        os.symlink(real, os.path.join(self.drive, ".Trash"))
        path = self._file()
        self.backend.move(path, [self.drive])
        self.assertEqual(os.listdir(real), [])
        self.assertTrue(os.path.exists(os.path.join(self.dash, "files", "a.png")))

    def test_a_dash_uid_link_into_the_root_is_refused(self):
        os.mkdir(os.path.join(self.drive, "images"))
        os.symlink(os.path.join(self.drive, "images"), self.dash)
        path = self._file("images/a.png")
        self._assert_refused(path, [self.drive], trash.TRASH_IN_ROOT)
        self.assertEqual(os.listdir(os.path.join(self.drive, "images")), ["a.png"])

    def test_a_dash_uid_link_out_of_the_root_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        os.symlink(elsewhere, self.dash)
        path = self._file()
        self._assert_refused(path, [self.drive], trash.NO_TRASH)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_uid_link_in_the_shared_trash_is_never_followed(self):
        shared = self._sticky_shared()
        os.mkdir(os.path.join(self.drive, "images"))
        os.symlink(os.path.join(self.drive, "images"), os.path.join(shared, str(UID)))
        path = self._file("images/a.png")
        self._assert_refused(path, [self.drive], trash.TRASH_IN_ROOT)

    def test_a_files_folder_that_is_a_link_is_refused(self):
        os.mkdir(self.dash, 0o700)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        os.symlink(elsewhere, os.path.join(self.dash, "files"))
        path = self._file()
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, [self.drive])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.listdir(elsewhere), [])

    @unittest.skipIf(REAL_MOUNT, "needs the fake mount table")
    def test_a_trash_folder_owned_by_someone_else_is_refused(self):
        other = UID + 1
        os.mkdir(os.path.join(self.drive, f".Trash-{other}"), 0o700)
        backend = self._fake_table(uid=other)
        path = self._file()
        self.assertEqual(backend.available(path, [self.drive]), trash.NO_TRASH)
        self.assertTrue(os.path.exists(path))

    def test_a_rename_across_drives_is_refused_and_nothing_is_copied(self):
        def cross(*_args):
            raise OSError(errno.EXDEV, "cross-device")

        backend = trash.FreedesktopBackend(
            data_home=self.data_home,
            mounts=lambda: ["/", os.path.realpath(self.drive)],
            rename=cross,
        )
        path = self._file()
        with self.assertRaises(trash.TrashRefused) as caught:
            backend.move(path, [self.drive])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.listdir(os.path.join(self.dash, "files")), [])
        self.assertEqual(os.listdir(os.path.join(self.dash, "info")), [])

    # ── any other trash inside a root stays refused ───────────────────

    def test_a_root_above_the_mount_is_refused(self):
        path = self._file()
        above = os.path.dirname(self.drive)
        self._assert_refused(path, [above], trash.TRASH_IN_ROOT)
        self._assert_refused(path, [self.drive, above], trash.TRASH_IN_ROOT)
        self.assertFalse(os.path.lexists(self.dash))

    def test_a_source_named_like_the_trash_folder_is_refused(self):
        source = self.dash
        path = self._file(f".Trash-{UID}/a.png")
        self._assert_refused(path, [source], trash.TRASH_IN_ROOT)
        self._assert_refused(path, [self.drive, source], trash.TRASH_IN_ROOT)

    def test_a_source_inside_the_trash_folder_is_refused(self):
        source = os.path.join(self.dash, "files")
        os.makedirs(source)
        path = self._file()
        self._assert_refused(path, [self.drive, source], trash.TRASH_IN_ROOT)
        self.assertEqual(os.listdir(source), [])

    def test_a_source_that_is_the_shared_trash_is_refused(self):
        shared = self._sticky_shared()
        path = self._file()
        self._assert_refused(path, [self.drive, shared], trash.TRASH_IN_ROOT)

    @unittest.skipIf(REAL_MOUNT, "needs the fake mount table")
    def test_a_mount_nested_under_the_root_is_refused(self):
        # The file's mount is not the root: its trash is "any other location".
        inner = os.path.join(self.drive, "inner")
        os.mkdir(inner)
        backend = self._fake_table(os.path.realpath(inner))
        path = self._file("inner/a.png")
        self.assertEqual(backend.available(path, [self.drive]), trash.TRASH_IN_ROOT)
        # ...and the same inner mount as its own root is fine.
        self.assertIsNone(backend.available(path, [inner]))

    @unittest.skipIf(REAL_MOUNT, "needs the fake mount table")
    def test_the_home_trash_inside_a_mount_root_output_stays_refused(self):
        backend = trash.FreedesktopBackend(
            data_home=os.path.join(self.drive, "xdg"),
            mounts=lambda: ["/", os.path.realpath(self.drive)],
        )
        path = self._file()
        self.assertEqual(backend.available(path, [self.drive]), trash.TRASH_IN_ROOT)

    # ── restoring ─────────────────────────────────────────────────────

    def test_the_recorded_path_is_relative_to_the_mount(self):
        path = self._file("sub dir/b c%é.png")
        self.backend.move(path, [self.drive])
        with open(
            os.path.join(self.dash, "info", "b c%é.png.trashinfo"), encoding="utf-8"
        ) as handle:
            info = handle.read()
        self.assertIn("Path=sub%20dir/b%20c%25%C3%A9.png\n", info)

    def test_a_restored_file_returns_to_its_place_with_its_bytes(self):
        path = self._file("sub/a.png", b"original")
        identity = os.lstat(path).st_ino
        self.backend.move(path, [self.drive])
        back = restore_as_a_file_manager_does(self.drive, self.dash, "a.png")
        self.assertEqual(back, path)
        self.assertEqual(os.lstat(path).st_ino, identity)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"original")
        self.assertEqual(os.listdir(os.path.join(self.dash, "files")), [])


@unittest.skipUnless(POSIX, "freedesktop trash is the Linux/BSD backend")
class MountRootLayoutTests(ImageDeleteTestCase):
    """The real backend over the routes: nothing lists the trash, restore relinks."""

    async def get_application(self):
        app = await super().get_application()
        self.data_home = Path(self.tmpdir) / "xdg"
        self.data_home.mkdir()
        if REAL_MOUNT:
            self.assertTrue(os.path.ismount(REAL_MOUNT), "PM_TRASH_MOUNT is no mount")
            self.output_dir = Path(os.path.realpath(REAL_MOUNT))
            self.addCleanup(_empty, str(self.output_dir))
            self.api._get_all_output_dirs = lambda: [self.output_dir]
            mounts = None
        else:
            drive = os.path.realpath(self.output_dir)
            mounts = lambda: ["/", drive]  # noqa: E731
        trash.BACKEND = trash.FreedesktopBackend(
            data_home=str(self.data_home), mounts=mounts
        )
        self.trash_dir = self.output_dir / f".Trash-{UID}"
        return app

    async def _trashed_one(self):
        """``kept.png`` stays; ``gone.png`` was moved to the drive's trash."""
        _write_png(self.output_dir / "kept.png", prompt_text="a kept prompt")
        _write_png(self.output_dir / "gone.png", prompt_text="a thrown prompt")
        _plan, job = await self.delete(["gone.png"], "trash")
        self.assertEqual(job.detail()["counters"]["trashed"], 1, job.detail())
        self.assertTrue((self.trash_dir / "files" / "gone.png").is_file())
        self.assertFalse((self.output_dir / "gone.png").exists())

    async def _json(self, method, url, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        self.assertLess(response.status, 500, await response.text())
        return await response.json()

    async def _scan(self, scope="all"):
        answer = await self._json(
            "POST", "/prompt_manager/jobs/scan", json={"scope": scope}
        )
        task = self.api._scan_job.task
        await asyncio.wait_for(asyncio.shield(task), DISK_BOUND_SECONDS)
        return answer

    def _linked_paths(self):
        return {
            os.path.basename(row[0])
            for row in self._rows("SELECT image_path FROM generated_images")
        }

    def _rows(self, sql):
        import sqlite3

        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    async def test_the_review_can_move_files_on_a_mount_root_output(self):
        _write_png(self.output_dir / "a.png", prompt_text="p")
        plan = await self.review(["a.png"])
        self.assertEqual(plan["trash"], {"count": 1})
        self.assertEqual(plan["trash_refused"]["count"], 0)
        self.assertFalse(self.trash_dir.exists(), "the review wrote nothing")

    async def test_refresh_listing_and_legacy_gallery_ignore_the_trash(self):
        await self._trashed_one()
        self.assertEqual(await self.listed(), ["kept.png"])
        rescan = await self._json("POST", "/prompt_manager/gallery/rescan")
        self.assertNotIn(".Trash", str(rescan))
        legacy = await self._json("GET", "/prompt_manager/images/output")
        self.assertNotIn(".Trash", str(legacy))
        folders = await self._json("GET", "/prompt_manager/gallery/subfolders")
        self.assertNotIn(".Trash", str(folders))

    async def test_scan_folder_and_its_walkers_ignore_the_trash(self):
        await self._trashed_one()
        await self._scan()
        self.assertEqual(self._linked_paths(), {"kept.png"})
        collected = _collect_media_files([self.output_dir], [".png"])
        self.assertEqual([Path(p).name for p in collected], ["kept.png"])
        found, _truncated = AutotagRoutesMixin._collect_scan_files(self.output_dir)
        self.assertEqual([p.name for p in found], ["kept.png"])

    async def test_thumbnail_plans_ignore_the_trash(self):
        await self._trashed_one()
        thumbs = self.output_dir / "thumbnails"
        for mode in ("missing", "rebuild"):
            plan = self.api._plan_thumbnails(self.output_dir, thumbs, mode=mode)
            sources = [src.name for src, _dst, _video in plan["targets"]]
            self.assertEqual(sources, ["kept.png"])

    async def test_the_autotag_listing_ignores_the_trash(self):
        await self._trashed_one()
        with patch.object(
            self.api, "_find_comfyui_output_dir", return_value=str(self.output_dir)
        ):
            body = await self._json("GET", "/prompt_manager/scan_output_dir")
        self.assertEqual([i["filename"] for i in body["images"]], ["kept.png"])

    async def test_the_monitor_ignores_the_trash_with_nested_roots(self):
        await self._trashed_one()
        monitor = ImageGenerationHandler.__new__(ImageGenerationHandler)
        monitor.supported_extensions = (".png",)
        root = str(self.output_dir)
        monitor.watched_roots = (root, os.path.join(root, "sub"))
        inside = os.path.join(str(self.trash_dir), "files", "gone.png")
        self.assertFalse(monitor.is_image_file(inside))
        self.assertTrue(monitor.is_image_file(os.path.join(root, "new.png")))
        self.assertTrue(monitor.is_image_file(os.path.join(root, "sub", "n.png")))

    async def test_a_source_below_a_trash_named_folder_lists_on_its_own(self):
        # The Codex P2 from #502: a source named like a trash folder stays a
        # source, while the outer root does not list it.
        source = self.output_dir / ".Trash-77"
        source.mkdir()
        _write_png(source / "mine.png", prompt_text="mine")
        _write_png(self.output_dir / "kept.png", prompt_text="kept")
        outer = [
            Path(e.path).name for e in images_module._walk_media(self.output_dir, 100)
        ]
        self.assertEqual(outer, ["kept.png"])
        inner = [Path(e.path).name for e in images_module._walk_media(source, 100)]
        self.assertEqual(inner, ["mine.png"])
        monitor = ImageGenerationHandler.__new__(ImageGenerationHandler)
        monitor.supported_extensions = (".png",)
        monitor.watched_roots = (str(self.output_dir), str(source))
        self.assertTrue(monitor.is_image_file(str(source / "new.png")))
        self.assertFalse(
            monitor.is_image_file(str(self.output_dir / ".Trash-1000" / "x.png"))
        )

    async def test_the_media_routes_do_not_serve_a_trashed_file(self):
        await self._trashed_one()
        relative = f".Trash-{UID}/files/gone.png"
        roots = self.api._get_all_output_dirs()
        scope = self.api._gallery_root_scope(roots[0], 0)
        query = {"root": "0", "root_scope": scope}
        served = await self.client.get(
            f"/prompt_manager/images/serve/{relative}", params=query
        )
        self.assertEqual(served.status, 404)
        preview = await self.client.get(
            f"/prompt_manager/images/serve/thumbnails/{relative}", params=query
        )
        self.assertEqual(preview.status, 404)
        self.assertFalse((self.output_dir / "thumbnails" / f".Trash-{UID}").exists())
        inspected = await self.client.get(
            f"/prompt_manager/images/inspect/{relative}", params=query
        )
        self.assertEqual(inspected.status, 404)
        self.assertIsNone(self.api._reveal_path(relative, roots[0]))
        self.assertIsNotNone(self.api._reveal_path("kept.png", roots[0]))

    async def test_a_record_pointing_into_the_trash_is_not_served(self):
        await self._trashed_one()
        inside = self.trash_dir / "files" / "gone.png"
        image_id = self.api.db.link_image_to_prompt(
            str(self.save("linked by hand")), str(inside)
        )
        response = await self.client.get(f"/prompt_manager/images/{image_id}/file")
        self.assertEqual(response.status, 404)

    async def test_a_trashed_file_cannot_be_linked_or_listed_under_a_prompt(self):
        await self._trashed_one()
        inside = self.trash_dir / "files" / "gone.png"
        prompt_id = self.save("linked by hand")
        refused = await self.client.post(
            "/prompt_manager/images/link",
            json={"prompt_id": prompt_id, "image_path": str(inside)},
        )
        self.assertEqual(refused.status, 404)
        self.assertEqual(self._linked_paths(), set())
        # A record from before this check still lists nothing.
        self.api.db.link_image_to_prompt(str(prompt_id), str(inside))
        page = self.api._library_media(
            self._rows_as_records(), self.api._get_all_output_dirs()
        )
        self.assertEqual(page, [])

    def _rows_as_records(self):
        rows = self._rows("SELECT image_path FROM generated_images")
        return [{"image_path": row[0]} for row in rows]

    async def test_a_source_named_like_a_trash_folder_is_still_served(self):
        source = self.output_dir / ".Trash-77"
        source.mkdir()
        _write_png(source / "mine.png", prompt_text="mine")
        self.api._get_all_output_dirs = lambda: [source]
        scope = self.api._gallery_root_scope(source, 0)
        served = await self.client.get(
            "/prompt_manager/images/serve/mine.png",
            params={"root": "0", "root_scope": scope},
        )
        self.assertEqual(served.status, 200)

    async def test_a_restored_file_lists_and_links_as_before(self):
        await self._trashed_one()
        await self._scan()
        self.assertEqual(self._linked_paths(), {"kept.png"})
        back = restore_as_a_file_manager_does(
            str(self.output_dir), str(self.trash_dir), "gone.png"
        )
        self.assertEqual(back, str(self.output_dir / "gone.png"))
        self.assertEqual(sorted(await self.listed()), ["gone.png", "kept.png"])
        await self._scan("new")
        self.assertEqual(self._linked_paths(), {"gone.png", "kept.png"})
        prompts = self._rows(
            "SELECT p.text FROM generated_images g JOIN prompts p ON p.id = g.prompt_id"
            " WHERE g.image_path LIKE '%gone.png'"
        )
        self.assertEqual(prompts, [("a thrown prompt",)])
