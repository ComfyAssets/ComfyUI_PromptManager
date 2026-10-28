"""The System browser hooks only ever reset their own server's config.json (#249 S3)
and decide JPEG XL availability whatever the host has installed (#371)."""

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_mock_server = mock.MagicMock()
_mock_server.PromptServer.instance.routes = mock.MagicMock()
sys.modules.setdefault("server", _mock_server)

from py.api import thumbnail_encode  # noqa: E402
from tests.browser.workspace_system_fixture import (  # noqa: E402
    register_system_commands,
)


class TestWorkspaceSystemFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "server"
        self.root.mkdir()

    def test_refuses_a_config_path_outside_its_root(self):
        elsewhere = Path(self.tmp.name) / "config.json"
        elsewhere.write_bytes(b'{"real": true}')
        for value in (str(elsewhere), None):
            env = {
                k: v for k, v in os.environ.items() if k != "PROMPT_MANAGER_CONFIG_PATH"
            }
            if value is not None:
                env["PROMPT_MANAGER_CONFIG_PATH"] = value
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, env, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "PROMPT_MANAGER_CONFIG_PATH"):
                    register_system_commands(None, None, None, self.root)
        self.assertEqual(elsewhere.read_bytes(), b'{"real": true}')

    def test_no_encoder_holds_even_when_the_host_has_one(self):
        """``{available: false}`` survives the settings read's re-probe (#371)."""
        config = self.root / "config.json"
        env = {**os.environ, "PROMPT_MANAGER_CONFIG_PATH": str(config)}
        api = types.SimpleNamespace()
        app, routes = web.Application(), web.RouteTableDef()
        real_detect = thumbnail_encode._detect
        with mock.patch.dict(os.environ, env, clear=True):
            reset = register_system_commands(app, routes, api, self.root)
        app.add_routes(routes)
        self.addCleanup(thumbnail_encode.reset_encoders)
        self.addCleanup(setattr, thumbnail_encode, "_detect", real_detect)

        async def scenario():
            async with TestClient(TestServer(app)) as client:

                async def post(available):
                    response = await client.post(
                        "/__test__/system/jxl-encoder", json={"available": available}
                    )
                    self.assertEqual(response.status, 200)

                await post(False)
                status = thumbnail_encode.encoder_status()["jxl"]
                self.assertFalse(status["available"])
                self.assertIsNone(
                    thumbnail_encode.detect_jxl_encoder(recheck_missing=True)
                )
                await post(True)
                status = thumbnail_encode.encoder_status()["jxl"]
                self.assertEqual(status["encoder"], "cjxl")
                await post(False)

        # The host's real detector finds an encoder (Pillow can save JPEG XL).
        with mock.patch.object(thumbnail_encode, "_pillow_can_save_jxl", lambda: True):
            asyncio.run(scenario())
            # Reset gives detection back to the host.
            reset()
            self.assertIs(thumbnail_encode._detect, real_detect)
            status = thumbnail_encode.encoder_status()["jxl"]
            self.assertEqual(status["encoder"], "pillow")


if __name__ == "__main__":
    unittest.main()
