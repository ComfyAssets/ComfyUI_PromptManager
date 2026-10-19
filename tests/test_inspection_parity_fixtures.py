"""Committed synthetic inspection parity fixtures stay reproducible and referenced."""

import json
import tempfile
import unittest
from pathlib import Path

try:
    from tests import inspection_parity as parity
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity


class InspectionParityFixtureTests(unittest.TestCase):
    def test_committed_fixtures_match_builders(self):
        # Decoded text chunks, not bytes: zlib output differs between platforms.
        for name in parity.COMMITTED:
            with self.subTest(fixture=name):
                path = parity.fixture_path(name)
                self.assertTrue(path.is_file(), f"missing committed fixture {path}")
                self.assertLess(path.stat().st_size, parity.MAX_FIXTURE_BYTES)
                self.assertEqual(
                    parity.read_text_chunks(path),
                    parity.expected_text_chunks(parity.build(name)),
                )

    def test_no_uncommitted_files_in_fixture_directory(self):
        expected = {f"{name}.png" for name in parity.COMMITTED} | {"vectors.json"}
        present = {path.name for path in parity.FIXTURE_DIR.iterdir()}
        self.assertEqual(present, expected)

    def test_writer_round_trips_extra_raw_chunks(self):
        for name in ("f15", "f16"):
            built = parity.build(name)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"{name}.png"
                parity.write_png(path, built)
                chunks = parity.read_text_chunks(path)
            with self.subTest(fixture=name):
                self.assertEqual(chunks, parity.expected_text_chunks(built))
                self.assertGreater(len(chunks), 2)

    def test_duplicate_fixtures_have_the_intended_shape(self):
        identical = parity.read_text_chunks(parity.fixture_path("f15"))
        self.assertEqual(len(identical), 8)
        self.assertEqual(len(set(identical)), 2)
        conflicting = [
            text
            for key, text in parity.read_text_chunks(parity.fixture_path("f16"))
            if key == "prompt"
        ]
        self.assertEqual(len(conflicting), 2)
        self.assertNotEqual(conflicting[0], conflicting[1])

    def test_nan_fixture_keeps_comfyui_spelling_outside_inputs(self):
        prompt = dict(parity.read_text_chunks(parity.fixture_path("f14")))["prompt"]
        self.assertIn('"is_changed": [NaN]', prompt)
        self.assertIn('"_meta": {"title": "CheckpointLoaderSimple"}', prompt)
        for node in json.loads(prompt).values():
            for value in node["inputs"].values():
                self.assertEqual(value, value, "NaN stays outside inputs")

    def test_builders_are_deterministic_and_fresh(self):
        for name in parity.COMMITTED + parity.TEST_TIME:
            with self.subTest(fixture=name):
                first, second = parity.build(name), parity.build(name)
                self.assertEqual(
                    parity.expected_text_chunks(first),
                    parity.expected_text_chunks(second),
                )
                if first["prompt"] is not None:
                    first["prompt"].clear()
                    self.assertTrue(parity.build(name)["prompt"])
        with self.assertRaises(KeyError):
            parity.build("f99")

    def test_test_time_shapes_exceed_commit_size(self):
        sizes = {
            name: sum(
                len(text) for _, text in parity.expected_text_chunks(parity.build(name))
            )
            for name in parity.TEST_TIME
        }
        self.assertGreater(sizes["f01"], 100_000)
        self.assertGreater(sizes["f04"], 2_500_000)
        self.assertGreater(sizes["f05"], 900_000)

    def test_vectors_reference_existing_fixtures(self):
        vectors = parity.load_vectors()
        self.assertTrue(vectors)
        names = [vector["name"] for vector in vectors]
        self.assertEqual(len(names), len(set(names)), "vector names are unique")
        known = set(parity.COMMITTED) | set(parity.TEST_TIME)
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                self.assertIn(vector["fixture"], known)
                if vector["fixture"] in parity.COMMITTED:
                    self.assertTrue(parity.fixture_path(vector["fixture"]).is_file())
                self.assertIn(vector["profile"], ("default", "inspection"))
                self.assertEqual(set(vector["expect"]), set(parity.EXPECT_KEYS))
                facts = vector["expect"]["facts"]
                if vector["profile"] == "default":
                    # Facts are a property of the inspection response only.
                    self.assertIsNone(facts)
                else:
                    self.assertEqual(set(facts), set(parity.FACT_FIELDS))
                    for fact in facts.values():
                        self.assertEqual(set(fact), {"status", "value"})
        covered = {
            vector["fixture"] for vector in vectors if vector["profile"] == "inspection"
        }
        self.assertEqual(covered, set(parity.COMMITTED))

    def test_vectors_match_current_resolution(self):
        """Baseline vectors pin today's outcomes; behavior changes edit them."""
        for vector in parity.load_vectors():
            if vector["fixture"] not in parity.COMMITTED:
                continue
            with self.subTest(vector=vector["name"]):
                expected = dict(vector["expect"])
                expected.pop("facts")
                self.assertEqual(parity.observe(vector), expected)


if __name__ == "__main__":
    unittest.main()
