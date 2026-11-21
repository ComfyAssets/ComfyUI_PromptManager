"""The browser resolver proves values from the same categories as the server.

#662 (N1): ``PROVEN_VALUE_CATEGORIES`` (utils/inspection_semantics.py) names
the semantics tables whose outputs always hold a value (#657). The Python
resolver reads it through ``Semantics.proves_value``; the browser resolver
(web/js/workspace/local-resolver.mjs) reads its copy in
local-resolver-support.mjs. This fails when the two copies drift apart.
"""

import json
import os
import shutil
import subprocess  # nosec B404 - fixed local Node command, no shell
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import inspection_semantics  # noqa: E402

SUPPORT = ROOT / "web" / "js" / "workspace" / "local-resolver-support.mjs"
PRINT_CONSTANTS = (
    "const m = await import(process.argv[1]);"
    "console.log(JSON.stringify({categories: [...m.PROVEN_VALUE_CATEGORIES],"
    " byClass: [...m.PROVEN_BY_CLASS]}));"
)


class TestProvenValueParity(unittest.TestCase):
    def browser_constants(self):
        node = shutil.which("node")
        if node is None:  # pragma: no cover - CI runners and dev hosts ship Node
            if os.environ.get("CI"):
                self.fail("Node.js is required in CI for the resolver parity test")
            self.skipTest("Node.js is not installed")
        completed = subprocess.run(  # nosec B603
            [node, "--input-type=module", "-e", PRINT_CONSTANTS, SUPPORT.as_uri()],
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        return json.loads(completed.stdout)

    def test_the_browser_proves_the_same_categories_keyed_the_same_way(self):
        found = self.browser_constants()
        proven = inspection_semantics.PROVEN_VALUE_CATEGORIES
        self.assertEqual(set(found["categories"]), set(proven))
        self.assertEqual(len(found["categories"]), len(proven))
        by_class = proven & set(inspection_semantics._CLASS_CATEGORIES)
        self.assertEqual(set(found["byClass"]), by_class)


if __name__ == "__main__":
    unittest.main()
