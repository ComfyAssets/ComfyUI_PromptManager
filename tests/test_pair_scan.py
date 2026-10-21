"""Metadata scanning enriches known pairs and conservatively leaves conflicts."""

import json
from pathlib import Path

from tests.test_api_admin import AdminAPITestCase


def graph(positive="cat", negative="blur"):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


def sampled_graph(positive="cat", negative="blur"):
    """A KSampler with a model link, as real workflows have."""
    g = graph(positive, negative)
    g["4"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}}
    g["3"]["inputs"]["model"] = ["4", 0]
    return g


class TestPairScan(AdminAPITestCase):
    def test_pair_scan_repeated_is_unchanged_and_empty_negative_is_supported(self):
        path = self.output_dir / "pair.png"
        meta = {"prompt": json.dumps(graph(negative=""))}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "unchanged")
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["negative_text"], "")
        self.assertEqual(pairs[0]["run_count"], 0)

    def _scanned(self, name, prompt_graph):
        meta = {"prompt": json.dumps(prompt_graph)}
        return self.api._ingest_scanned_file(self.output_dir / name, meta)

    def _texts(self):
        return [p["text"] for p in self.api.db.get_recent_prompts()["prompts"]]

    def test_samplers_sharing_one_prompt_link_that_prompt(self):
        # #249: every branch resolves to one exact pair, so the pair is ingested.
        g = sampled_graph()
        g["5"] = {**g["3"]}
        self.assertEqual(self._scanned("multi.png", g), "added")
        self.assertEqual(sorted(self._texts()), ["blur", "cat"])
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(
            [(p["positive_text"], p["negative_text"]) for p in pairs], [("cat", "blur")]
        )

    def test_samplers_with_different_prompts_link_the_first_sampler_prompt(self):
        # The prompt the inspector and the legacy panels show for this file.
        g = sampled_graph()
        g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "dog"}}
        g["12"] = {**g["3"], "inputs": {**g["3"]["inputs"], "positive": ["6", 0]}}
        self.assertEqual(self._scanned("different.png", g), "added")
        self.assertEqual(self._texts(), ["cat"])

    def test_an_unsupported_pass_through_still_links_the_traced_prompt(self):
        g = sampled_graph()
        g["7"] = {
            "class_type": "MyConditioningTweak",
            "inputs": {"conditioning": ["1", 0]},
        }
        g["3"]["inputs"]["positive"] = ["7", 0]
        g["5"] = {**g["3"]}
        self.assertEqual(self._scanned("tweak.png", g), "added")
        self.assertEqual(self._texts(), ["cat"])

    def test_a_graph_without_a_traceable_prompt_saves_nothing(self):
        g = sampled_graph()
        g["5"] = {**g["3"]}
        for sampler in ("3", "5"):
            g[sampler]["inputs"]["positive"] = ["9", 0]
        g["9"] = {
            "class_type": "BasicGuider",
            "inputs": {"width": 2048, "height": 2048},
        }
        self.assertEqual(self._scanned("untraceable.png", g), "ambiguous")
        self.assertEqual(self.api.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(self._texts(), [])

    def test_numeric_widgets_are_never_stored_as_a_prompt(self):
        # The pre-redesign scan stored "2048" from a resolution widget here.
        g = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ["8", 0]}},
            "8": {"class_type": "DisplayText", "inputs": {"text_size": 2048}},
            "3": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {"guider": ["4", 0], "sigmas": ["5", 0]},
            },
            "4": {"class_type": "BasicGuider", "inputs": {"conditioning": ["1", 0]}},
        }
        self.assertIn(
            self._scanned("numbers.png", g), ("ambiguous", "unresolved", "found")
        )
        self.assertEqual(self._texts(), [])

    def test_batch_exposes_pair_counts_and_actual_unchanged_status(self):
        meta = {"parameters": "cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler"}
        batch = [(Path(self.output_dir / "a.png"), meta)]
        first = self.api._ingest_scan_batch_sync(batch)
        self.assertEqual(first["pairs_added"], 1)
        self.assertEqual(first["pair_links_added"], 1)
        second = self.api._ingest_scan_batch_sync(batch)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["linked"], 0)
        self.assertEqual(second["pairs_added"], 0)

    def test_legacy_single_prompt_second_pass_not_reported_as_new_link(self):
        meta = {"prompt": json.dumps({"1": graph()["1"]})}
        path = self.output_dir / "old.png"
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "unchanged")

    async def test_cancel_finishes_at_safe_boundary_and_restart_is_idempotent(self):
        from py.api.scan_job import ScanJob

        job = ScanJob()
        self.api._scan_job = job
        response = await self.client.post("/prompt_manager/scan/cancel")
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["cancellation_requested"])
        counts = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
        )
        self.assertEqual(counts["processed"], 0)
        self.assertEqual(self.api.db.list_prompt_pairs()["total"], 0)
        job.finish()
        self.api._scan_job = ScanJob()
        first = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
        )
        self.assertEqual(first["pairs_added"], 1)
        self.assertEqual(
            self.api._ingest_scan_batch_sync(
                [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
            )["unchanged"],
            1,
        )

    def test_legacy_found_outcome_is_not_counted_twice(self):
        result = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "blank.png", {"parameters": "Negative prompt: blur"})]
        )
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["found"], 1)

    def test_ambiguous_a1111_does_not_fall_back_to_legacy_guess(self):
        meta = {
            "parameters": (
                "cat\nNegative prompt: blur\nNegative prompt: noise"
                "\nSteps: 20, Sampler: Euler"
            )
        }
        self.assertEqual(
            self.api._ingest_scanned_file(self.output_dir / "ambiguous.png", meta),
            "ambiguous",
        )
        self.assertEqual(self.api.db.get_recent_prompts()["prompts"], [])

    def test_raw_png_workflow_is_stored_as_object_and_repeat_is_unchanged(self):
        path = self.output_dir / "workflow.png"
        workflow = {"nodes": [{"id": 1, "type": "PromptManagerPair"}]}
        metadata = {"prompt": json.dumps(graph()), "workflow": json.dumps(workflow)}
        self.assertEqual(self.api._ingest_scanned_file(path, metadata), "added")
        self.assertEqual(
            self.api.db.get_image_prompt_info(str(path))["workflow_data"], workflow
        )
        self.assertEqual(self.api._ingest_scanned_file(path, metadata), "unchanged")
