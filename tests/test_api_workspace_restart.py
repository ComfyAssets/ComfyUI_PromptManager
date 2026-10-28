"""A ComfyUI restart keeps an open workspace page's scopes and cursors (#430).

A restart is a new ``PromptManagerAPI`` on the same database in a new
process. Here a second API instance on its own aiohttp server stands in for
it (``tests/test_workspace_keys.py`` also derives the scope in a real child
process); it must accept every ``root_scope`` and listing cursor the first one
handed out, while a changed root list or key still refuses them.
"""

import base64
import json
import os
import sys
from unittest.mock import patch
from urllib.parse import quote

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from test_api_images import ImageAPITestCase, make_png
except ImportError:  # imported as tests.test_api_workspace_restart
    from tests.test_api_images import ImageAPITestCase, make_png

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from utils.workspace_keys import KEY_FILE_NAME, WorkspaceKeys  # noqa: E402

LISTING = "/prompt_manager/images/output"


def _payload(token):
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    return json.loads(raw[32:])


class TestRestartKeepsTheOpenPage(ImageAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        for index in range(3):
            make_png(self.output_dir / f"image_{index}.png")
        return app

    async def first_page(self, client=None, **params):
        response = await (client or self.client).get(
            LISTING, params={"mode": "workspace", "limit": "1", **params}
        )
        self.assertEqual(response.status, 200)
        return await response.json()

    async def restarted(self):
        """A second API on the same database and roots, on its own server."""
        api = PromptManagerAPI(db=PromptDatabase(self._temp_db.name))
        api._get_all_output_dirs = lambda: [self.output_dir]
        api._find_comfyui_output_dir = lambda: str(self.output_dir)
        app = web.Application()
        routes = web.RouteTableDef()
        api.add_routes(routes)
        app.router.add_routes(routes)
        client = TestClient(TestServer(app))
        await client.start_server()

        async def close():
            await client.close()
            api.close_auto_thumbnails()
            api.db.close_all()

        self.addAsyncCleanup(close)
        return api, client

    async def test_a_restart_keeps_every_held_scope_and_cursor_valid(self):
        page = await self.first_page()
        image, cursor = page["images"][0], page["next_cursor"]
        self.assertTrue(
            os.path.isfile(
                os.path.join(os.path.dirname(self._temp_db.name), KEY_FILE_NAME)
            )
        )
        _api, client = await self.restarted()

        more = await client.get(
            LISTING, params={"mode": "workspace", "limit": "1", "cursor": cursor}
        )
        self.assertEqual(more.status, 200, await more.text())
        self.assertNotEqual((await more.json())["images"][0]["path"], image["path"])

        inspected = await client.get(
            f"/prompt_manager/images/inspect/{quote(image['path'])}",
            params={"root": "0", "root_scope": image["root_scope"]},
        )
        self.assertEqual(inspected.status, 200, await inspected.text())
        served = await client.get(
            f"/prompt_manager/images/serve/{quote(image['path'])}",
            params={"root": "0", "root_scope": image["root_scope"]},
        )
        self.assertEqual(served.status, 200)
        relisted = await self.first_page(client)
        self.assertEqual(relisted["images"][0]["root_scope"], image["root_scope"])

    async def test_a_key_change_refuses_the_cursor_as_stale(self):
        cursor = (await self.first_page())["next_cursor"]
        rotated = WorkspaceKeys(b"n" * 32, b"m" * 32, False)
        with patch.object(self.api, "_workspace_keys", return_value=rotated):
            response = await self.client.get(
                LISTING, params={"mode": "workspace", "limit": "1", "cursor": cursor}
            )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "cursor-stale")

    async def test_renumbered_roots_refuse_the_cursor_as_stale(self):
        cursor = (await self.first_page())["next_cursor"]
        other = self.comfy_dir / "other"
        other.mkdir()
        self.api._get_all_output_dirs = lambda: [other, self.output_dir]
        response = await self.client.get(
            LISTING, params={"mode": "workspace", "limit": "1", "cursor": cursor}
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "cursor-stale")

    async def test_another_listing_refuses_the_cursor_as_stale(self):
        cursor = (await self.first_page())["next_cursor"]
        response = await self.client.get(
            LISTING,
            params={"mode": "workspace", "limit": "1", "cursor": cursor, "q": "x"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "cursor-stale")

    async def test_a_malformed_cursor_is_a_plain_bad_request(self):
        # A 32-byte signature over a payload is well formed: a wrong one is stale.
        for cursor in ("!!", "a" * 5000, base64.urlsafe_b64encode(b"x" * 20).decode()):
            with self.subTest(cursor=cursor[:10]):
                response = await self.client.get(
                    LISTING,
                    params={"mode": "workspace", "limit": "1", "cursor": cursor},
                )
                self.assertEqual(response.status, 400)
                self.assertNotEqual((await response.json()).get("code"), "cursor-stale")

    async def test_the_cursor_carries_no_digest_of_the_root_paths(self):
        cursor = (await self.first_page())["next_cursor"]
        payload = _payload(cursor)
        # v2: the listing scope (it hashes every absolute root) is in the MAC only.
        self.assertEqual(set(payload), {"v", "key"})
        self.assertEqual(payload["v"], 2)

    async def test_another_database_folder_gets_other_scopes(self):
        page = await self.first_page()
        other_db = self.comfy_dir / "elsewhere" / "prompts.db"
        other_db.parent.mkdir()
        api = PromptManagerAPI(db=PromptDatabase(str(other_db)))
        self.addCleanup(api.db.close_all)
        self.addCleanup(api.close_auto_thumbnails)
        self.assertNotEqual(
            api._gallery_root_scope(self.output_dir, 0), page["images"][0]["root_scope"]
        )


# A fresh interpreter: the API on the given database, as after a restart.
_CHILD = """
import json, sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, sys.argv[1])
from tests.host_stubs import install_folder_paths_stub
install_folder_paths_stub()
sys.modules.setdefault("server", MagicMock())
from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from py.api.images import _workspace_cursor_key
api = PromptManagerAPI(db=PromptDatabase(sys.argv[2]))
scope = api._gallery_root_scope(Path(sys.argv[3]), 0)
secret = api._workspace_keys().cursor
key = _workspace_cursor_key(sys.argv[4], "s" * 64, "newest", secret)
api.close_auto_thumbnails()
api.db.close_all()
print(json.dumps({"scope": scope, "key": list(key)}))
"""


class TestRestartInANewProcess(ImageAPITestCase):
    """The real thing: two other interpreters on the same database."""

    def child(self, cursor):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _CHILD,
                repo,
                self._temp_db.name,
                str(self.output_dir),
                cursor,
            ],
            cwd=self.comfy_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    async def test_new_processes_derive_the_same_scope_and_accept_the_cursor(self):
        from py.api.images import _workspace_cursor

        make_png(self.output_dir / "image.png")
        listing = await self.client.get(LISTING, params={"mode": "workspace"})
        scope = (await listing.json())["images"][0]["root_scope"]
        key = (-12.5, "image.png", "0" * 64, 0)
        cursor = _workspace_cursor(key, "s" * 64, self.api._workspace_keys().cursor)
        for _restart in range(2):
            self.assertEqual(self.child(cursor), {"scope": scope, "key": list(key)})
