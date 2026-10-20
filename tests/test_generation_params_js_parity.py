"""The Python generation-parameter parser equals the browser parser.

Golden vectors (tests/fixtures/generation_params/vectors.json) hold the browser
parser's results for every named case and every PNG fixture, so this runs on
machines without Node. With Node, the live browser parser is compared too and
the vectors are checked for staleness. Regenerate them with:

    python tests/test_generation_params_js_parity.py --write-vectors
"""

import json
import math
import shutil
import subprocess  # nosec B404 - runs the repository's own Node oracle
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.generation_params import (  # noqa: E402
    GenerationParamsError,
    extract_from_records,
)

try:
    from tests.generation_params_cases import CASES  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from generation_params_cases import CASES  # noqa: E402

VECTORS = ROOT / "tests" / "fixtures" / "generation_params" / "vectors.json"
ORACLE = ROOT / "tests" / "js" / "support" / "generation-params-oracle.mjs"
FIXTURE_DIRS = [ROOT / "tests" / "fixtures"]
NODE = shutil.which("node")


def fixture_cases():
    """Text records of every PNG fixture, keyed by its repository path."""
    from PIL import Image

    cases = {}
    for directory in FIXTURE_DIRS:
        for path in sorted(directory.rglob("*.png")):
            with Image.open(path) as image:
                info = {k: v for k, v in image.info.items() if isinstance(v, str)}
            if "prompt" in info or "workflow" in info:
                name = path.relative_to(ROOT).as_posix()
                cases[name] = {
                    "prompt": info.get("prompt"),
                    "workflow": info.get("workflow"),
                }
    return cases


def all_cases():
    return {**{f"case:{k}": v for k, v in CASES.items()}, **fixture_cases()}


def run_oracle(cases):
    """Browser parser results for case records, in order."""
    lines = "".join(json.dumps(case) + "\n" for case in cases)
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [NODE, str(ORACLE)],
        input=lines,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=600,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


def run_python(case):
    try:
        return {
            "ok": True,
            "result": extract_from_records(case["prompt"], case["workflow"]),
        }
    except GenerationParamsError:
        return {"ok": False}


def js_equal(left, right):
    """Equality as JavaScript values: 1 == 1.0, but true is not 1."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        # Every JavaScript number is a double; compare as doubles.
        left, right = float(left), float(right)
        return left == right or (math.isnan(left) and math.isnan(right))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            js_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            js_equal(left[k], right[k]) for k in left
        )
    return type(left) is type(right) and left == right


def write_vectors():
    cases = all_cases()
    results = run_oracle(cases.values())
    vectors = [
        {"name": name, **case, "expected": result}
        for (name, case), result in zip(cases.items(), results)
    ]
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    VECTORS.write_text(json.dumps(vectors, indent=1, sort_keys=True) + "\n", "utf-8")
    print(f"wrote {len(vectors)} vectors to {VECTORS.relative_to(ROOT)}")


class GoldenVectors(unittest.TestCase):
    def test_every_vector_matches(self):
        vectors = json.loads(VECTORS.read_text("utf-8"))
        self.assertGreater(len(vectors), len(CASES))
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                actual = run_python(vector)
                self.assertTrue(
                    js_equal(actual, vector["expected"]), (actual, vector["expected"])
                )

    def test_vectors_cover_every_case_and_fixture(self):
        names = {vector["name"] for vector in json.loads(VECTORS.read_text("utf-8"))}
        self.assertEqual(names, set(all_cases()))


@unittest.skipUnless(NODE, "Node is required to run the browser parser")
class LiveBrowserParser(unittest.TestCase):
    def test_live_results_match_python_and_the_vectors(self):
        cases = all_cases()
        stored = {
            vector["name"]: vector["expected"]
            for vector in json.loads(VECTORS.read_text("utf-8"))
        }
        for (name, case), expected in zip(cases.items(), run_oracle(cases.values())):
            with self.subTest(case=name):
                self.assertTrue(
                    js_equal(run_python(case), expected), (run_python(case), expected)
                )
                self.assertTrue(
                    js_equal(stored[name], expected),
                    "vectors are stale: regenerate them",
                )


if __name__ == "__main__":
    if "--write-vectors" in sys.argv:
        write_vectors()
    else:
        unittest.main()
