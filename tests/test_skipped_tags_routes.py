"""Skipped tags on every suggestion path, and their preview (#431).

Tag image (``/image-annotations/suggest``), Auto-tag selected (the
``image-tags`` job), the System batch (the ``autotag`` job), the legacy
``/autotag/start`` and ``/autotag/single`` all hide a suggested tag whose
words contain a skipped entry; a job runs with the list it started with.
``POST /autotag/skipped-tags/preview`` counts what a list would hide among
the library's tags and the downloaded WD14 label lists, writing nothing.
"""

import tempfile
from pathlib import Path

try:
    from test_autotag_legacy import LegacyTestCase
except ImportError:
    from tests.test_autotag_legacy import LegacyTestCase

from py.config import AutotagConfig

PREVIEW = "/prompt_manager/autotag/skipped-tags/preview"


class SkippedTestCase(LegacyTestCase):
    async def get_application(self):
        app = await super().get_application()
        self.addCleanup(
            setattr, AutotagConfig, "SKIPPED_TAGS", AutotagConfig.SKIPPED_TAGS
        )
        AutotagConfig.SKIPPED_TAGS = ()
        return app

    def job_lines(self, job):
        return [line.split(" ", 1)[1] for line in job.log]


class TestEveryPathHidesSkippedTags(SkippedTestCase):
    async def test_tag_image_suggestions_omit_skipped_tags_and_count_them(self):
        AutotagConfig.SKIPPED_TAGS = ("sandstone", "tag 001")
        observed = await self.observed()

        body = await self.suggest_ok(self.suggest_body(observed))

        names = [row["name"] for row in body["suggestions"]]
        self.assertNotIn("sandstone", names)
        self.assertNotIn("tag_001", names)
        # Skipped tags use up no slot: the next ones fill the limit.
        self.assertEqual(len(names), 10)
        self.assertEqual(names[:3], ["desert", "tag_000", "tag_002"])
        self.assertEqual(body["skipped"], 2)

    async def test_tag_image_without_skipped_tags_reports_zero(self):
        observed = await self.observed()
        body = await self.suggest_ok(self.suggest_body(observed))
        self.assertEqual(body["suggestions"][0]["name"], "sandstone")
        self.assertEqual(body["skipped"], 0)

    async def test_auto_tag_selected_runs_with_the_list_of_its_start(self):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))
        AutotagConfig.SKIPPED_TAGS = ("desert",)

        def save_mid_run(_path):
            # A save while the job runs does not change that job.
            AutotagConfig.SKIPPED_TAGS = ("sandstone",)

        self.tagger.on_generate = save_mid_run
        options = {
            "general_threshold": 0.35,
            "character_threshold": 0.85,
            "suggestion_limit": 2,
        }

        job = await self.run_job(self.auto_body(["a.png", "b.png"], options=options))

        self.assertEqual(job.state, "finished")
        self.assertEqual(self.stored("a.png")["tags"], ["sandstone", "tag_000"])
        self.assertEqual(self.stored("b.png")["tags"], ["sandstone", "tag_000"])
        self.assertIn("Skipped tags: 1 entry", self.job_lines(job))

    async def test_the_system_batch_hides_skipped_tags_and_logs_the_list(self):
        self.image("a.png", (1, 2, 3), sees=["bird", "blurry_background", "sky"])
        AutotagConfig.SKIPPED_TAGS = ("blurry", "watermark")

        job = await self.run_batch(existing="merge")

        self.assertEqual(self.stored("a.png")["tags"], ["bird", "sky"])
        self.assertIn("Skipped tags: 2 entries", self.job_lines(job))

        AutotagConfig.SKIPPED_TAGS = ()
        self.image("b.png", (4, 5, 6), sees=["blurry"])
        await self.relist()
        job = await self.run_batch(existing="merge")
        self.assertEqual(self.stored("b.png")["tags"], ["blurry"])
        self.assertIn("Skipped tags: none", self.job_lines(job))

    async def test_the_legacy_batch_hides_skipped_tags(self):
        self.image("a.png", (1, 2, 3), sees=["bird", "signature"])
        AutotagConfig.SKIPPED_TAGS = ("signature",)

        response, frames = await self.legacy()

        self.assertEqual(response.status, 200)
        self.assertEqual(frames[-1]["type"], "complete", frames[-1])
        self.assertEqual(self.stored("a.png")["tags"], ["bird"])

    async def test_the_legacy_single_review_hides_skipped_tags(self):
        prompt = self.prompt("a prompt")
        image = self.link(
            prompt, self.image("a.png", (1, 2, 3), sees=["bird", "t-shirt"])
        )
        AutotagConfig.SKIPPED_TAGS = ("shirt",)

        status, data = await self.single({"image_id": image, "model_type": "wd14-vit"})

        self.assertEqual(status, 200, data)
        self.assertEqual(data["tags"], ["bird"])

    async def test_hand_typed_and_stored_tags_are_never_filtered(self):
        path = self.image("a.png", (1, 2, 3), sees=["bird"])
        self.saved_set("a.png", path.read_bytes(), ["blurry_hand_tag"])
        AutotagConfig.SKIPPED_TAGS = ("blurry",)

        await self.run_batch(existing="merge")
        job = await self.run_job(self.add_body(["a.png"], ["blurry"]))

        self.assertEqual(job.state, "finished")
        self.assertEqual(
            self.stored("a.png")["tags"], ["blurry_hand_tag", "bird", "blurry"]
        )


def write_labels(directory, subdir, rows):
    path = Path(directory) / subdir / "selected_tags.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["tag_id,name,category,count"]
    lines += [f"{i},{name},{category},1" for i, (name, category) in enumerate(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestPreview(SkippedTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        models = tempfile.TemporaryDirectory()
        self.addCleanup(models.cleanup)
        self.models_dir = Path(models.name)
        self.tagger.models_dir = self.models_dir
        for name in ("Blurry", "portrait", "my_signature_style"):
            self.prompt(f"prompt with {name}", tags=[name])

    async def preview(self, entries):
        response = await self.client.post(PREVIEW, json={"entries": entries})
        return response.status, await self.json_of(response)

    async def test_library_tags_only_when_no_label_list_is_downloaded(self):
        status, data = await self.preview(["blurry", "signature", "watermark"])

        self.assertEqual(status, 200, data)
        self.assertEqual(
            data,
            {
                "success": True,
                "count": 2,
                "sample": ["Blurry", "my_signature_style"],
                "sample_limit": 12,
                "per_entry": [
                    {"entry": "blurry", "count": 1},
                    {"entry": "signature", "count": 1},
                    {"entry": "watermark", "count": 0},
                ],
                "sources": {"library": 3, "wd14": []},
            },
        )

    async def test_both_label_lists_join_the_library_tags(self):
        write_labels(
            self.models_dir,
            "WD14-SwinV2",
            [
                ("blurry", "0"),
                ("blurry_background", "0"),
                ("watermark", "0"),
                ("general", "9"),
                ("saber_(fate)", "4"),
            ],
        )
        write_labels(
            self.models_dir,
            "WD14-ViT",
            [
                ("blurry_foreground", "0"),
                ("watermark", "0"),
                ("fate_(series)", "3"),
            ],
        )

        status, data = await self.preview(["blurry", "watermark", "(fate)", "general"])

        self.assertEqual(status, 200, data)
        # Library spellings first (case-folded duplicates are one tag), then the
        # label lists' names, each part sorted case-folded; rating and copyright
        # rows are never suggested, so never counted.
        self.assertEqual(
            data["sample"],
            [
                "Blurry",
                "blurry_background",
                "blurry_foreground",
                "saber_(fate)",
                "watermark",
            ],
        )
        self.assertEqual(data["count"], 5)
        self.assertEqual(
            data["per_entry"],
            [
                {"entry": "blurry", "count": 3},
                {"entry": "watermark", "count": 1},
                {"entry": "(fate)", "count": 1},
                {"entry": "general", "count": 0},
            ],
        )
        self.assertEqual(
            data["sources"], {"library": 3, "wd14": ["WD14 SwinV2", "WD14 ViT"]}
        )

    async def test_the_sample_holds_at_most_twelve(self):
        write_labels(
            self.models_dir,
            "WD14-SwinV2",
            [(f"sky_{index:02d}", "0") for index in range(20)],
        )
        status, data = await self.preview(["sky"])
        self.assertEqual(status, 200, data)
        self.assertEqual(data["count"], 20)
        self.assertEqual(data["sample"], [f"sky_{index:02d}" for index in range(12)])

    async def test_a_bad_list_is_refused_with_the_save_sentence(self):
        for body, message in (
            ({"entries": ["sky", "Sky"]}, "Skipped tags: entry 2 repeats entry 1."),
            (
                {"entries": ["a,b"]},
                "Skipped tags: entry 1 must not contain a comma or a line break.",
            ),
            ({"entries": "sky"}, "Skipped tags must be a list of words."),
        ):
            with self.subTest(body=body):
                response = await self.client.post(PREVIEW, json=body)
                self.assertEqual(response.status, 400)
                self.assertEqual((await response.json())["error"], message)
        response = await self.client.post(PREVIEW, json={"entries": [], "x": 1})
        self.assertEqual(response.status, 400)

    async def test_preview_loads_no_model_opens_no_image_and_writes_nothing(self):
        write_labels(self.models_dir, "WD14-SwinV2", [("blurry", "0")])
        self.image("a.png", (1, 2, 3))
        before = self.prompt_rows()

        status, data = await self.preview(["blurry"])

        self.assertEqual(status, 200, data)
        self.assertEqual(self.tagger.load_calls, [])
        self.assertEqual(self.tagger.read_paths, [])
        self.assertEqual(self.prompt_rows(), before)
        self.assertEqual(self.api._jobs.list(), [])

    async def test_preview_is_busy_while_an_exclusive_job_runs(self):
        async with self.api._jobs.hold(
            "restore", "Restore library", admission="exclusive"
        ):
            status, data = await self.preview(["blurry"])
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "busy")
        self.assertEqual(data["running"]["title"], "Restore library")


if __name__ == "__main__":
    import unittest

    unittest.main()
