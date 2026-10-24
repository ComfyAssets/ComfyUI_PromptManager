"""Library location routes (#249 S6b): where it lives, Move library, Keep it here.

The API's database is a node-folder library inside a fake ComfyUI tree; the
resolver is pointed at that tree (``api._library_resolver``) so nothing here
reads the real environment or the repository's node folder.
"""

import asyncio
import json
import os
import sys
import threading
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import library_move as lm  # noqa: E402
from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402
from utils import library_location as ll  # noqa: E402

try:
    from tests.test_api_admin import AdminAPITestCase  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase  # noqa: E402

WAIT = 10
URL = "/prompt_manager/system/library"


class LibraryAPITestCase(AdminAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        # Lower case: a public path is case-folded on Windows (normcase).
        self.ext = os.path.join(str(self.comfy_dir), "custom_nodes", "pm")
        os.makedirs(self.ext)
        self.user = os.path.join(str(self.comfy_dir), "user")
        os.makedirs(self.user)
        self.target = os.path.join(self.user, "__promptmanager")
        sys.modules["folder_paths"].get_user_directory = lambda: self.user
        previous = self.api.db
        self.api.db = PromptDatabase(os.path.join(self.ext, "prompts.db"))
        previous.close_all()
        self.api.db.save_prompt("a library prompt")
        self.fp = sys.modules["folder_paths"]
        self.environ = {}
        self.api._library_resolver = lambda: ll.resolve_library(
            None, environ=self.environ, extension_root=self.ext, folder_paths=self.fp
        )
        ll.clear_override()
        self.addCleanup(ll.clear_override)
        self.addCleanup(PromptModel.close_all_instances)
        self._keep = PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER
        self.addCleanup(
            setattr, PromptManagerConfig, "KEEP_LIBRARY_IN_NODE_FOLDER", self._keep
        )
        PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER = False
        return app

    async def json(self, method, url, status=200, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        self.assertNotIn(self.tmpdir, text)
        return json.loads(text)

    async def finished(self, body):
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job


class TestLocationRoute(LibraryAPITestCase):
    async def test_node_folder_library_offers_the_move(self):
        body = await self.json("GET", URL)
        self.assertEqual(
            body["location"],
            {
                "path": "custom_nodes/pm/prompts.db",
                "source": "node_folder",
                "movable": True,
                "keep": False,
                "offer": True,
                "target": "user/__promptmanager",
                "problem": None,
                "size_bytes": os.path.getsize(self.api.db.model.db_path),
                "target_source": "default",
                "config_moves": True,
            },
        )

    async def test_overview_carries_the_location(self):
        body = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(body["location"]["source"], "node_folder")
        self.assertTrue(body["location"]["offer"])

    async def test_default_folder_library_offers_nothing(self):
        self.api._library_resolver = lambda: ll.LibraryLocation(
            ll.SOURCE_DEFAULT,
            self.api.db.model.db_path,
            os.path.join(self.target, "config.json"),
            self.target,
            self.target,
        )
        location = (await self.json("GET", URL))["location"]
        self.assertEqual((location["source"], location["movable"]), ("default", False))
        self.assertFalse(location["offer"])

    async def test_a_resolved_file_that_is_not_the_live_database_is_never_moved(self):
        self.api._library_resolver = lambda: ll.resolve_library(
            None,
            environ={},
            extension_root=os.path.join(self.tmpdir, "elsewhere"),
            folder_paths=self.fp,
        )
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["source"], "config")
        self.assertFalse(location["movable"])
        refused = await self.json("POST", URL + "/move", 400)
        self.assertEqual(refused["code"], "not-movable")


class TestLocationDetails(LibraryAPITestCase):
    async def test_the_target_and_what_moves_follow_the_environment(self):
        data = os.path.join(self.tmpdir, "pm-data")
        self.environ = {
            ll.DATA_DIR_ENV: data,
            ll.CONFIG_PATH_ENV: os.path.join(self.tmpdir, "mine.json"),
        }
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["target_source"], "environment")
        self.assertFalse(location["config_moves"])
        self.assertEqual(location["problem"], "data-dir-empty")

    async def test_a_configured_library_inside_the_node_folder_is_flagged(self):
        self.api._library_resolver = lambda: ll.LibraryLocation(
            ll.SOURCE_CONFIG,
            self.api.db.model.db_path,
            os.path.join(self.ext, "config.json"),
            self.ext,
        )
        self.api._extension_root = self.ext
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["problem"], "inside-node-folder")
        self.assertFalse(location["movable"])

    async def test_a_startup_after_a_crash_finishes_the_rename(self):
        target_db = os.path.join(self.target, "prompts.db")
        os.makedirs(self.target)
        with open(target_db, "wb") as handle:
            handle.write(b"x")
        lm.write_marker(self.target, ll.MARKER_MOVED, self.api.db.model.db_path)
        old = self.api.db.model.db_path
        self.api.db.model.close_all()
        moved = ll.LibraryLocation(
            ll.SOURCE_DEFAULT,
            target_db,
            os.path.join(self.target, "config.json"),
            self.target,
            self.target,
        )
        self.api._library_resolver = lambda: moved
        with patch.object(self.api.db.model, "db_path", target_db):
            renamed = self.api.finish_interrupted_library_move()
        self.assertEqual(renamed, ["prompts.db.moved-to-__promptmanager"])
        self.assertFalse(os.path.exists(old))


class TestLocationThroughASymlinkedNodeFolder(LibraryAPITestCase):
    """custom_nodes/<name> is a symlink to a checkout outside ComfyUI.

    The library location reads relative to ComfyUI through the node folder's
    own (unresolved) place in custom_nodes, not as a bare file name; the
    shared ``_public_path`` (sources, jobs) keeps resolving symlinks.
    """

    async def get_application(self):
        app = await super().get_application()
        self.real = os.path.join(self.tmpdir, "checkout", "ComfyUI_PromptManager")
        os.makedirs(self.real)
        self.link = os.path.join(str(self.comfy_dir), "custom_nodes", "promptmanager")
        try:
            os.symlink(self.real, self.link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks not permitted here: {exc}")
        previous = self.api.db
        self.api.db = PromptDatabase(os.path.join(self.link, "prompts.db"))
        previous.close_all()
        self.api.db.save_prompt("a library prompt")
        self.api._library_resolver = lambda: ll.resolve_library(
            None, environ=self.environ, extension_root=self.link, folder_paths=self.fp
        )
        self.api._extension_root = self.link
        return app

    async def test_location_names_the_node_folder_in_custom_nodes(self):
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["source"], "node_folder")
        self.assertEqual(location["path"], "custom_nodes/promptmanager/prompts.db")
        self.assertEqual(location["target"], "user/__promptmanager")
        overview = await self.json("GET", "/prompt_manager/system/overview")
        self.assertEqual(
            overview["location"]["path"], "custom_nodes/promptmanager/prompts.db"
        )

    async def test_a_resolved_spelling_of_the_database_reads_the_same(self):
        from py.api import library_location as routes

        resolved = os.path.join(os.path.realpath(self.real), "prompts.db")
        self.assertEqual(
            routes.node_folder_public_path(resolved, self.link, []),
            "custom_nodes/promptmanager/prompts.db",
        )

    async def test_the_shared_public_path_is_unchanged(self):
        self.assertEqual(
            self.api._public_path(os.path.join(self.link, "prompts.db")), "prompts.db"
        )


class TestNodeFolderPublicPath(LibraryAPITestCase):
    def display(self, path, root=None, custom_nodes=()):
        from py.api import library_location as routes

        return routes.node_folder_public_path(path, root or self.ext, custom_nodes)

    async def test_a_plain_node_folder_reads_relative_to_custom_nodes(self):
        self.assertEqual(
            self.display(os.path.join(self.ext, "prompts.db")),
            "custom_nodes/pm/prompts.db",
        )
        self.assertEqual(
            self.display(os.path.join(self.ext, "data", "prompts.db")),
            "custom_nodes/pm/data/prompts.db",
        )

    async def test_a_custom_nodes_entry_from_comfyui_names_the_folder(self):
        nodes = os.path.join(self.tmpdir, "more_nodes")
        root = os.path.join(nodes, "pm")
        self.assertEqual(
            self.display(os.path.join(root, "prompts.db"), root, [nodes]),
            "more_nodes/pm/prompts.db",
        )

    async def test_anything_else_is_not_a_node_folder_path(self):
        # Outside the node folder, a parent that is no custom_nodes folder, or
        # a spelling that climbs out: the caller falls back to _public_path.
        self.assertIsNone(self.display(os.path.join(self.user, "prompts.db")))
        loose = os.path.join(self.tmpdir, "somewhere", "pm")
        self.assertIsNone(self.display(os.path.join(loose, "prompts.db"), loose))
        self.assertIsNone(self.display(os.path.join(self.ext, "..", "prompts.db")))
        self.assertIsNone(self.display(""))
        self.assertIsNone(self.display(None))

    async def test_a_library_outside_the_node_folder_keeps_the_public_path(self):
        self.api._library_resolver = lambda: ll.LibraryLocation(
            ll.SOURCE_DEFAULT,
            self.api.db.model.db_path,
            os.path.join(self.target, "config.json"),
            self.target,
            self.target,
        )
        self.api._extension_root = os.path.join(self.tmpdir, "elsewhere")
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["path"], "custom_nodes/pm/prompts.db")


class TestMoveRoute(LibraryAPITestCase):
    async def test_move_runs_as_an_exclusive_job_and_switches_the_live_library(self):
        body = await self.json("POST", URL + "/move", 202)
        summary = body["job"]
        self.assertEqual(summary["kind"], "library_move")
        self.assertEqual(summary["title"], "Move library")
        self.assertEqual(summary["admission"], "exclusive")
        self.assertFalse(summary["cancellable"])
        job = await self.finished(body)
        self.assertEqual(job.state, "finished", job.status_line)
        self.assertEqual(
            self.api.db.model.db_path, os.path.join(self.target, "prompts.db")
        )
        self.assertIn("user/__promptmanager", job.status_line)
        self.assertIn("1 prompt", job.status_line)
        self.assertNotIn(self.tmpdir, json.dumps(job.report()))
        location = (await self.json("GET", URL))["location"]
        self.assertEqual(location["source"], "default")
        self.assertEqual(location["path"], "user/__promptmanager/prompts.db")
        self.assertFalse(location["offer"])
        prompts = await self.json("GET", "/prompt_manager/stats")
        self.assertEqual(prompts["stats"]["total_prompts"], 1)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.ext, "prompts.db.moved-to-__promptmanager")
            )
        )

    async def test_refusals_are_coded_without_paths(self):
        with patch.object(lm, "_free_bytes", return_value=1024):
            short = await self.json("POST", URL + "/move", 507)
        self.assertEqual(short["code"], "insufficient-space")
        os.makedirs(self.target)
        with open(os.path.join(self.target, "prompts.db"), "wb") as handle:
            handle.write(b"another library")
        taken = await self.json("POST", URL + "/move", 409)
        self.assertEqual(taken["code"], "target-has-library")
        self.assertEqual(self.api._jobs.list(), [])

    async def test_move_is_refused_while_another_job_runs_and_blocks_others(self):
        gate = asyncio.Event()

        async def scanning(job):
            await gate.wait()

        self.api._jobs.start(
            "scan", "Metadata scan", admission="records", runner=scanning
        )
        busy = await self.json("POST", URL + "/move", 409)
        self.assertEqual(busy["code"], "busy")
        gate.set()
        await asyncio.sleep(0)
        for job in self.api._jobs.list():
            if job.task is not None:
                await asyncio.wait_for(asyncio.shield(job.task), WAIT)

        release = threading.Event()
        entered = threading.Event()
        real = lm.move_library

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        with patch.object(lm, "move_library", side_effect=gated):
            body = await self.json("POST", URL + "/move", 202)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, WAIT)
            refused = await self.json("POST", "/prompt_manager/system/health", 409)
            self.assertEqual(refused["running"]["title"], "Move library")
            release.set()
            job = await self.finished(body)
        self.assertEqual(job.state, "finished", job.status_line)

    async def test_a_failed_move_ends_truthfully_and_keeps_the_old_library(self):
        with patch.object(
            lm, "move_library", side_effect=lm.MoveFailed("verify-failed")
        ):
            body = await self.json("POST", URL + "/move", 202)
            job = await self.finished(body)
        self.assertEqual(job.state, "failed")
        self.assertIn("did not pass verification", job.status_line)
        self.assertIn("still in the node folder", job.status_line)
        self.assertEqual(
            self.api.db.model.db_path, os.path.join(self.ext, "prompts.db")
        )
        with patch.object(lm, "move_library", side_effect=OSError("disk gone")):
            body = await self.json("POST", URL + "/move", 202)
            job = await self.finished(body)
        self.assertEqual(job.state, "failed")
        self.assertNotIn("disk gone", job.status_line)
        self.assertIn("still in the node folder", job.status_line)

    async def test_old_file_left_in_place_is_reported(self):
        real = lm.retire_old_files
        with patch.object(lm, "retire_old_files", return_value=([], "old-in-use")):
            body = await self.json("POST", URL + "/move", 202)
            job = await self.finished(body)
        self.assertEqual(job.state, "finished")
        self.assertIn("left in the node folder", job.status_line)
        self.assertTrue(callable(real))


class TestKeepRoute(LibraryAPITestCase):
    async def test_keep_it_here_is_persisted_and_stops_the_offer(self):
        body = await self.json("POST", URL + "/keep", 200, json={"keep": True})
        self.assertTrue(body["location"]["keep"])
        self.assertFalse(body["location"]["offer"])
        self.assertTrue(body["location"]["movable"])
        with open(self.config_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["library"], {"keep_in_node_folder": True})
        self.assertTrue(PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER)
        undone = await self.json("POST", URL + "/keep", 200, json={"keep": False})
        self.assertTrue(undone["location"]["offer"])

    async def test_keep_needs_a_boolean(self):
        for payload in ({"keep": "yes"}, {}, [True]):
            bad = await self.json("POST", URL + "/keep", 400, json=payload)
            self.assertEqual(bad["code"], "invalid-request")
        self.assertFalse(os.path.exists(self.config_path))

    async def test_unwritable_config_reports_not_saved(self):
        with patch("py.config_store._write_atomically", side_effect=OSError("ro")):
            body = await self.json("POST", URL + "/keep", 500, json={"keep": True})
        self.assertEqual(body["code"], "config-write-failed")
        self.assertFalse(PromptManagerConfig.KEEP_LIBRARY_IN_NODE_FOLDER)


class TestLegacyRoutesDuringAMove(LibraryAPITestCase):
    async def test_legacy_restore_is_refused_while_the_move_runs(self):
        from aiohttp import FormData

        release = threading.Event()
        entered = threading.Event()
        real = lm.move_library

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        with patch.object(lm, "move_library", side_effect=gated):
            body = await self.json("POST", URL + "/move", 202)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, entered.wait, WAIT)
            form = FormData()
            form.add_field("database_file", b"SQLite format 3\x00", filename="x.db")
            response = await self.client.request(
                "POST", "/prompt_manager/restore", data=form
            )
            self.assertEqual(response.status, 409, await response.text())
            release.set()
            await self.finished(body)
