"""Shared parity vectors: the workspace's JS facts match ``vectors.json``.

Each ``inspection`` vector's file goes through the real current-file route
pipeline (``inspect_current_file`` then ``encode_inspection_response``). The
exact response text is handed to Node, where the workspace's own
``projectInspectionPayload`` validates it and ``extractGenerationFacts`` reads
it. Python and JS therefore assert the same files with the same expectations.
"""

import json
import shutil
import subprocess  # nosec B404
import tempfile
import unittest
from pathlib import Path

from py.api.inspection_service import inspect_current_file
from utils.inspection_response import encode_inspection_response

try:
    from tests import inspection_parity as parity
    from tests.open_handles import assert_closed
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity
    from open_handles import assert_closed

HARNESS = (
    Path(__file__).resolve().parent / "js" / "support" / "inspection-parity-facts.mjs"
)
LIBRARY = {
    "status": "none",
    "scope": "saved-library",
    "positive_id": None,
    "pair_id": None,
    "title": None,
    "category": None,
    "tags": [],
    "metadata_scope": None,
}
NODE_TIMEOUT_SECONDS = 120


def _encoded(name: str, path: Path) -> str:
    observed = inspect_current_file(
        path, source_label="parity", saved=None, library=LIBRARY
    )
    body = encode_inspection_response(
        image={"root_index": 0, "path": f"{name}.png", "filename": f"{name}.png"},
        root_scope="a" * 64,
        file=observed["file"],
        prompts=observed["prompts"],
        resolution=observed["resolution"],
        inspection=observed["inspection"],
        library=observed["library"],
    )
    return body.decode("utf-8")


def _responses(vectors, directory: Path) -> list:
    responses = []
    for vector in vectors:
        name = vector["fixture"]
        if name in parity.TEST_TIME:
            path = parity.fixture_path(name, directory)
            parity.write_png(path, parity.build(name))
        else:
            path = parity.fixture_path(name)
        responses.append({"name": vector["name"], "body": _encoded(name, path)})
    return responses


class TestParityFacts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(assert_closed, self, temporary.name)
        self.directory = Path(temporary.name)

    def test_vectors_json_facts_match_workspace_js(self):
        node = shutil.which("node")
        if node is None:  # pragma: no cover - CI runners and dev hosts ship Node
            self.skipTest("Node.js is not installed")
        vectors = [v for v in parity.load_vectors() if v["profile"] == "inspection"]
        self.assertTrue(vectors)
        payload = json.dumps(_responses(vectors, self.directory))
        completed = subprocess.run(  # nosec B603 - fixed local harness, no shell
            [node, str(HARNESS)],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        observed = json.loads(completed.stdout)
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(observed[vector["name"]], vector["expect"]["facts"])

    def test_default_vectors_carry_no_facts(self):
        # Facts are a workspace (inspection) concern; default vectors pin the
        # ingestion resolver only.
        for vector in parity.load_vectors():
            if vector["profile"] == "default":
                self.assertIsNone(vector["expect"]["facts"], vector["name"])


if __name__ == "__main__":
    unittest.main()
