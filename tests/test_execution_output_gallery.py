"""Mutable metadata galleries never own or retarget frozen output references."""

from pathlib import PurePosixPath, PureWindowsPath

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from database.pairs import PairDependencyError
from tests import test_execution_history_database as history_tests
from tests.test_api_admin import AdminAPITestCase
from tests.test_execution_output_database import bundle
from utils.execution_outputs import snapshot_output_roots


class OutputGalleryTests(AdminAPITestCase):
    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def capture(self, filename="first.png", item=0, root=None):
        db = self.api.db
        start = self.start(db, item)
        db.write_execution_start(start)
        output_root = root or self.output_dir
        style = "windows" if isinstance(output_root, PureWindowsPath) else "posix"
        key = snapshot_output_roots(style, str(output_root), None).output_key
        db.write_execution_outputs_for_finish(
            self.finish(start), bundle(filename, root=key, path_style=style)
        )
        return start.run_id, db.get_execution_outputs(start.run_id)

    def assert_evidence(self, run, expected):
        self.assertEqual(self.api.db.get_execution_outputs(run), expected)
        reference = expected["references"][0]
        self.assertEqual(reference["context_membership"], "unknown")
        self.assertNotIn("image_id", reference)
        self.assertNotIn("pair_id", reference)
        self.assertNotIn("prompt_id", reference)

    def gallery(self):
        conn = self.api.db.model.get_connection()
        return {
            table: [
                tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")
            ]
            for table in (
                "prompts",
                "prompt_pairs",
                "generated_images",
                "image_prompt_pairs",
            )
        }

    def test_capture_matches_root_path_style_on_each_platform(self) -> None:
        roots = (
            (PurePosixPath("/output"), "posix"),
            (PureWindowsPath(r"D:\output"), "windows"),
        )
        for item, (root, style) in enumerate(roots):
            with self.subTest(style=style):
                run, evidence = self.capture(item=item, root=root)
                self.assertEqual(evidence["state"], "complete")
                reference = evidence["references"][0]
                self.assertEqual(reference["path_style"], style)
                self.assertEqual(len(reference["root_key"]), 64)
                self.assert_evidence(run, evidence)

    def test_metadata_disabled_or_malformed_still_captures_without_gallery_rows(self):
        for item, metadata in enumerate(({}, {"prompt": "{malformed"})):
            with self.subTest(metadata=metadata):
                path = self.output_dir / f"metadata-{item}.png"
                info = PngInfo()
                for key, value in metadata.items():
                    info.add_text(key, value)
                Image.new("RGB", (2, 2)).save(path, pnginfo=info)
                extracted = self.api._extract_comfyui_metadata(str(path))
                self.assertEqual(
                    self.api._ingest_scanned_file(path, extracted),
                    None if not metadata else "unresolved",
                )
                before = self.gallery()
                run, evidence = self.capture(path.name, item)
                self.assertEqual(evidence["state"], "complete")
                self.assertEqual(self.gallery(), before)
                self.assertEqual(self.api.db.get_recent_prompts()["total"], 0)
                self.assert_evidence(run, evidence)

    def test_scans_before_after_delayed_repeated_and_overwrite_keep_exact_counters(
        self,
    ):
        metadata = {
            "parameters": "cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler"
        }
        db = self.api.db
        captures = []
        for item, before_capture in enumerate((True, False)):
            path = self.output_dir / f"scan-{item}.png"
            path.write_bytes(b"first contents")
            if before_capture:
                first = self.api._ingest_scan_batch_sync([(path, metadata)])
            before = self.gallery()
            run, evidence = self.capture(path.name, item)
            captures.append((run, evidence))
            self.assertEqual(self.gallery(), before)
            if not before_capture:
                first = self.api._ingest_scan_batch_sync([(path, metadata)])
            self.assertEqual(
                first,
                {
                    "processed": 1,
                    "found": 1,
                    "added": 1,
                    "linked": 0,
                    "enriched": 0,
                    "unchanged": 0,
                    "ambiguous": 0,
                    "unresolved": 0,
                    "conflict": 0,
                    "failed": 0,
                    "pairs_added": int(before_capture),
                    "pair_links_added": 1,
                },
            )
            for _ in range(2):
                repeated = self.api._ingest_scan_batch_sync([(path, metadata)])
                self.assertEqual(
                    repeated,
                    {
                        "processed": 1,
                        "found": 1,
                        "added": 0,
                        "linked": 0,
                        "enriched": 0,
                        "unchanged": 1,
                        "ambiguous": 0,
                        "unresolved": 0,
                        "conflict": 0,
                        "failed": 0,
                        "pairs_added": 0,
                        "pair_links_added": 0,
                    },
                )
            path.write_bytes(b"replacement bytes at the same relative path")
            conflict = self.api._ingest_scan_batch_sync(
                [
                    (
                        path,
                        {"parameters": metadata["parameters"].replace("blur", "noise")},
                    )
                ]
            )
            self.assertEqual(conflict["conflict"], 1)
            self.assertEqual(conflict["added"], 0)
            self.assertEqual(db.list_prompt_pairs()["total"], 1)
            self.assert_evidence(run, evidence)
        pair = db.list_prompt_pairs()["pairs"][0]
        self.assertEqual((pair["run_count"], pair["image_count"]), (0, 2))
        self.assertEqual(
            [p["run_count"] for p in db.get_recent_prompts()["prompts"]], [0, 0]
        )
        for run, evidence in captures:
            self.assert_evidence(run, evidence)

    def test_same_relative_path_across_roots_never_retargets(self):
        second_root = self.output_dir / "second-root"
        second_root.mkdir()
        metadata = {
            "parameters": "cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler"
        }
        records = []
        for item, root in enumerate((self.output_dir, second_root)):
            path = root / "same.png"
            path.write_bytes(b"image")
            result = self.api._ingest_scanned_file(path, metadata, detailed=True)
            self.assertEqual(result["status"], "added")
            records.append(self.capture("same.png", item, root))
        self.assertNotEqual(
            records[0][1]["references"][0]["root_key"],
            records[1][1]["references"][0]["root_key"],
        )
        self.assertEqual(self.api.db.list_prompt_pairs()["pairs"][0]["image_count"], 2)
        for root in (self.output_dir, second_root):
            (root / "same.png").unlink()
        second_root.rmdir()
        self.assertEqual(self.api.db.cleanup_missing_images(), 2)
        for run, expected in records:
            self.assert_evidence(run, expected)

    def test_archive_delete_prune_cleanup_and_numeric_id_reuse_keep_evidence(self):
        db = self.api.db
        path = self.output_dir / "first.png"
        path.write_bytes(b"image")
        result = self.api._ingest_scanned_file(
            path,
            {"parameters": "cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler"},
            detailed=True,
        )
        self.assertEqual(
            (result["status"], result["pairs_added"], result["links_added"]),
            ("added", 1, 1),
        )
        image_id, pair_id = result["image_id"], result["pair_id"]
        pair = db.get_prompt_pair(pair_id)
        positive_id = pair["positive_prompt_id"]
        run, evidence = self.capture()
        with self.assertRaises(PairDependencyError):
            db.delete_prompt(positive_id)
        with self.assertRaises(PairDependencyError):
            db.delete_prompt_pair(pair_id)
        self.assertTrue(db.archive_prompt_pair(pair_id))
        self.assertEqual(db.list_prompt_pairs()["total"], 0)
        self.assertEqual(db.list_prompt_pairs(include_archived=True)["total"], 1)
        self.assertEqual(db.get_prompt_pair(pair_id)["run_count"], 0)
        self.assert_evidence(run, evidence)
        self.assertTrue(db.delete_image(image_id))
        self.assertFalse(db.delete_image(image_id))
        self.assertEqual(db.get_image_prompt_pairs(image_id), [])
        self.assertTrue(db.delete_prompt_pair(pair_id))
        self.assertTrue(db.delete_prompt(positive_id))
        self.assertEqual(db.prune_orphaned_prompts(), 1)
        self.assertEqual(db.prune_orphaned_prompts(), 0)
        self.assertEqual(db.cleanup_missing_images(), 0)
        self.assert_evidence(run, evidence)

        # Explicit IDs reproduce restored/imported numeric-ID reuse without assuming
        # SQLite's allocator reuses AUTOINCREMENT identities.
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text,run_count) "
                "VALUES(?, 'replacement prompt', 17)",
                (positive_id,),
            )
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,run_count) "
                "VALUES(?,?,23)",
                (pair_id, positive_id),
            )
            conn.execute(
                "INSERT INTO generated_images "
                "(id,prompt_id,image_path,filename,file_path) "
                "VALUES(?,?,?,'first.png',?)",
                (image_id, positive_id, str(path), str(path)),
            )
            conn.execute(
                "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                "VALUES(?,?,'{}')",
                (image_id, pair_id),
            )
        self.assert_evidence(run, evidence)
        self.assertTrue(db.archive_prompt_pair(pair_id))
        path.unlink()
        self.assertEqual(db.cleanup_missing_images(), 1)
        self.assertEqual(db.get_image_prompt_pairs(image_id), [])
        self.assertTrue(db.delete_prompt_pair(pair_id))
        # Standalone prompt deletion retains its original gallery cascade.
        recreated = db.link_image_to_prompt(positive_id, str(path))
        self.assertGreater(recreated, 0)
        self.assertTrue(db.delete_prompt(positive_id))
        self.assertIsNone(db.get_image_by_id(recreated))
        self.assert_evidence(run, evidence)
