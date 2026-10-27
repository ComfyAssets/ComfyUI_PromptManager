"""Legacy parity gate for the inspection resolver (#381, the PR #170 floor).

The legacy parser (``utils.generation_params``, the port of the browser's
``extractGenerationParams``) is the floor: a graph whose positive prompt it
found, and that the inspection resolver resolved to the same text, must keep
resolving to that text. ``legacy-agreement.json`` records those graphs per
corpus: the generation-params vectors, the inspection parity fixtures, the
local resolver golden and selection cases, and the unknown-node shapes of
#381. A graph may join the list, never leave it; regenerate with::

    python3 -m tests.test_legacy_parity_gate --write
"""

import argparse
import json
import unittest
from pathlib import Path

from utils import prompt_pair_resolver as resolver
from utils.generation_params import (
    GenerationParamsError,
    extract_generation_params,
    parse_js_json,
)

try:
    from tests import inspection_parity as parity
    from tests import local_resolver_cases as cases
    from tests import local_resolver_selection_cases as selection
    from tests import unknown_node_graphs as unknown
except ImportError:  # discovered with ``-s tests``
    import inspection_parity as parity
    import local_resolver_cases as cases
    import local_resolver_selection_cases as selection
    import unknown_node_graphs as unknown

HERE = Path(__file__).resolve().parent
AGREEMENT_PATH = HERE / "fixtures" / "local-inspect" / "legacy-agreement.json"
VECTORS_PATH = HERE / "fixtures" / "generation_params" / "vectors.json"
NOT_FOUND = "No prompt found"


def _parsed(value):
    return parse_js_json(value) if isinstance(value, str) else value


def corpora() -> dict:
    """Corpus name to ``{case name: (prompt graph, workflow or None)}``."""
    vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    named = {**cases.golden_cases(), **selection.selection_cases()}
    return {
        "generation_params": {
            item["name"]: (_parsed(item["prompt"]), _parsed(item.get("workflow")))
            for item in vectors
        },
        "inspection_parity": {
            name: (parity.build(name)["prompt"], parity.build(name)["workflow"])
            for name in parity.COMMITTED
        },
        "local_resolver": {
            name: (_parsed(records["prompt"]), _parsed(records.get("workflow")))
            for name, records in named.items()
            if "prompt" in records
        },
        "unknown_nodes": {
            name: (graph, None) for name, graph in unknown.shapes().items()
        },
    }


def legacy_positive(graph, workflow):
    """The positive prompt the legacy parser found, or ``None``."""
    try:
        found = extract_generation_params({"prompt": graph, "workflow": workflow})
    except GenerationParamsError:
        return None
    text = found["positivePrompt"]
    return None if text == NOT_FOUND else text


def agreeing(graphs: dict) -> list:
    """Names whose inspection positive equals the legacy parser's, sorted."""
    names = []
    for name, (graph, workflow) in graphs.items():
        if not isinstance(graph, dict):
            continue
        expected = legacy_positive(graph, workflow)
        if expected is None:
            continue
        metadata = {"prompt": graph}
        if workflow is not None:
            metadata["workflow"] = workflow
        result = resolver.resolve_prompt_pair(
            metadata, profile=resolver.INSPECTION_PROFILE
        )
        positive = result["positive"]
        if (
            result["state"] == "resolved"
            and isinstance(positive, dict)
            and positive["text"] == expected
        ):
            names.append(name)
    return sorted(names)


class LegacyParityGateTest(unittest.TestCase):
    def test_no_graph_leaves_the_legacy_agreement(self):
        recorded = json.loads(AGREEMENT_PATH.read_text(encoding="utf-8"))
        current = {name: agreeing(graphs) for name, graphs in corpora().items()}
        self.assertEqual(set(recorded), set(current))
        for corpus, names in recorded.items():
            with self.subTest(corpus=corpus):
                lost = sorted(set(names) - set(current[corpus]))
                self.assertEqual(lost, [], "these graphs no longer match legacy")

    def test_every_unknown_node_shape_legacy_reads_still_agrees(self):
        graphs = corpora()["unknown_nodes"]
        legacy = {name for name, pair in graphs.items() if legacy_positive(*pair)}
        self.assertLessEqual(legacy, set(agreeing(graphs)))

    def test_the_reported_graph_reads_better_than_legacy(self):
        # Legacy found no positive here (cf0cef3 showed the positive text as
        # the negative); the floor is met, and the inspector now reads it.
        graph = unknown.any_switch_graph()
        self.assertIsNone(legacy_positive(graph, None))
        result = resolver.resolve_prompt_pair(
            {"prompt": graph}, profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(result["positive"]["text"], unknown.POSITIVE)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="record the agreement")
    if parser.parse_args().write:
        current = {name: agreeing(graphs) for name, graphs in corpora().items()}
        text = json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False)
        AGREEMENT_PATH.write_text(text + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
