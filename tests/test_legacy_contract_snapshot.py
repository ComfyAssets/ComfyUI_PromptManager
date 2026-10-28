"""The legacy pages' backend contract does not move under System S13 (#330).

S13 points the legacy admin and gallery buttons at native System and removes
their duplicate dialogs, changing no backend file. This snapshot pins what the
legacy pages talk to: every registered route (method and path) and the key
paths of ``GET /prompt_manager/settings``, the one route whose legacy request
body S13 shrinks. Record again only with a reason in the PR body:

    PM_RECORD_LEGACY_SNAPSHOT=1 python3 -m unittest tests.test_legacy_contract_snapshot

Cutover (#334) removes this test and its fixture.
"""

import json
import os
import unittest
from pathlib import Path

try:
    from tests.test_api_admin import AdminAPITestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase

from py.config import GalleryConfig  # noqa: E402

SNAPSHOT = (
    Path(__file__).resolve().parent / "fixtures" / "legacy_contract_snapshot.json"
)
RECORD_ENV = "PM_RECORD_LEGACY_SNAPSHOT"
SETTINGS = "/prompt_manager/settings"
DEPTH = 2
CHANGED = (
    "the legacy contract changed; if the change is intended (a new route, a new"
    " settings key), record it with PM_RECORD_LEGACY_SNAPSHOT=1 and say why in"
    " the PR body"
)


def key_paths(value, prefix="", depth=1):
    """The key paths of a JSON object to ``DEPTH`` levels.

    ``{"a": {"b": 1}}`` gives ``a`` and ``a.b``; the items of a list of
    objects give the union of their keys as ``a[].b``.
    """
    paths = set()
    for key, child in value.items():
        path = f"{prefix}{key}"
        paths.add(path)
        if depth >= DEPTH:
            continue
        if isinstance(child, dict):
            paths |= key_paths(child, f"{path}.", depth + 1)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    paths |= key_paths(item, f"{path}[].", depth + 1)
    return paths


class TestLegacyContractSnapshot(AdminAPITestCase):
    """Routes and settings keys against tests/fixtures/legacy_contract_snapshot.json.

    The base case isolates config.json (``PROMPT_MANAGER_CONFIG_PATH`` in the
    temp tree), stubs ComfyUI's ``folder_paths`` and resets the gallery roots;
    nothing here reads the maintainer's install.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api._get_all_output_dirs = lambda: [str(self.output_dir)]
        GalleryConfig.MONITORING_DIRECTORIES = [str(self.output_dir)]

    def _routes(self):
        return sorted(
            {
                f"{route.method} {route.resource.canonical}"
                for route in self.app.router.routes()
                if route.method != "HEAD"
            }
        )

    async def _settings_keys(self):
        resp = await self.client.request("GET", SETTINGS)
        body = await resp.json()
        self.assertEqual(resp.status, 200, body)
        return sorted(key_paths(body))

    def _snapshot(self):
        with open(SNAPSHOT, encoding="utf-8") as f:
            return json.load(f)

    def _record(self, key, value):
        recorded = self._snapshot() if SNAPSHOT.exists() else {}
        recorded[key] = value
        with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(recorded, f, indent=2)
            f.write("\n")

    async def test_routes_match_the_snapshot(self):
        routes = self._routes()
        if os.environ.get(RECORD_ENV) == "1":
            self._record("routes", routes)
        self.assertGreater(len(routes), 50)
        self.assertEqual(routes, self._snapshot()["routes"], CHANGED)

    async def test_settings_response_keeps_its_keys(self):
        keys = await self._settings_keys()
        if os.environ.get(RECORD_ENV) == "1":
            self._record("settings_keys", keys)
        self.assertIn("settings.infinite_scroll", keys)
        self.assertEqual(keys, self._snapshot()["settings_keys"], CHANGED)


class TestKeyPaths(unittest.TestCase):
    def test_depth_and_list_items(self):
        value = {
            "success": True,
            "settings": {"a": 1, "deep": {"hidden": 1}},
            "rows": [{"path": "x"}, {"label": "y"}],
        }
        self.assertEqual(
            sorted(key_paths(value)),
            [
                "rows",
                "rows[].label",
                "rows[].path",
                "settings",
                "settings.a",
                "settings.deep",
                "success",
            ],
        )


if __name__ == "__main__":
    unittest.main()
