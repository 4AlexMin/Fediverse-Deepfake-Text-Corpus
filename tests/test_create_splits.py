"""Regression tests for leakage-aware split creation and safe reruns."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_splits.py"
SPEC = importlib.util.spec_from_file_location("create_splits", SCRIPT)
splits = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(splits)


def record(identifier, label, source, text):
    return {
        "fixture_id": identifier,
        "label": label,
        "text": text,
        "post_id" if label == 0 else "original_id": source,
    }


class CreateSplitsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input_dir = self.root / "corpus"
        self.input_dir.mkdir()
        self.output_dir = self.root / "splits"
        # A source link, an exact-text link, and another source link connect
        # all six rows, including links across different input shards.
        self.records = [
            record("aH", 0, "A", "a original"),
            record("aG", 1, "A", "bridge B"),
            record("bG", 1, "B", "bridge B"),
            record("bH", 0, "B", "b original"),
            record("cH", 0, "C", "b original"),
            record("cG", 1, "C", "c generated"),
            record("xH", 0, "X", "ambiguous text"),
            record("yG", 1, "Y", "ambiguous text"),
            record("xG", 1, "X", "x clean generated"),
            record("yH", 0, "Y", "y clean human"),
        ]
        for index in range(30):
            self.records.extend([
                record(f"H{index}", 0, f"S{index}", f"human {index}"),
                record(f"G{index}", 1, f"S{index}", f"generated {index}"),
            ])
        for index, name in enumerate(splits.EXPECTED_SHARDS):
            (self.input_dir / name).write_text(
                "".join(json.dumps(row) + "\n" for row in self.records[index::9]),
                encoding="utf-8",
            )

    def snapshot(self, directory):
        return {path.name: path.read_bytes() for path in directory.iterdir()}

    def create(self, **kwargs):
        return splits.create_splits(self.input_dir, self.output_dir, **kwargs)

    def assert_valid_outputs(self, manifest):
        sources, texts, record_splits = {}, {}, {}
        for split, counts in manifest["splits"].items():
            rows = [
                json.loads(line)
                for line in (self.output_dir / f"{split}.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(rows), counts["records"])
            self.assertEqual(sum(row["label"] == 0 for row in rows), counts["hwt_records"])
            self.assertEqual(sum(row["label"] == 1 for row in rows), counts["aigt_records"])
            for row in rows:
                identifier = row["fixture_id"]
                self.assertNotIn(identifier, record_splits)
                record_splits[identifier] = split
                source = row["post_id"] if row["label"] == 0 else row["original_id"]
                self.assertEqual(sources.setdefault(source, split), split)
                self.assertEqual(texts.setdefault(row["text"], split), split)
        self.assertEqual(len(record_splits), manifest["split_records"])
        self.assertEqual(manifest["input_records"], len(self.records))
        self.assertEqual(len(record_splits) + manifest["excluded_records"], len(self.records))
        self.assertEqual(len({record_splits[key] for key in ("aH", "aG", "bG", "bH", "cH", "cG")}), 1)
        return record_splits

    def test_three_way_to_two_way_overwrite_removes_stale_test(self):
        source_before = self.snapshot(self.input_dir)
        self.create(test_fraction=0.2)
        stale_test = self.output_dir / "test.jsonl"
        self.assertTrue(stale_test.read_bytes())
        unrelated = self.output_dir / "notes.txt"
        unrelated.write_text("keep these notes", encoding="utf-8")

        manifest = self.create(overwrite=True)

        self.assertFalse(stale_test.exists())
        self.assertEqual(set(manifest["splits"]), {"train", "validation"})
        self.assert_valid_outputs(manifest)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep these notes")
        self.assertEqual(self.snapshot(self.input_dir), source_before)

    def test_stale_test_alone_requires_overwrite(self):
        self.output_dir.mkdir()
        (self.output_dir / "test.jsonl").write_bytes(b"old test data\n")
        before = self.snapshot(self.output_dir)

        with self.assertRaises(FileExistsError):
            self.create()

        self.assertEqual(self.snapshot(self.output_dir), before)

    def test_stale_test_alone_is_removed_with_overwrite(self):
        self.output_dir.mkdir()
        (self.output_dir / "test.jsonl").write_bytes(b"old test data\n")

        manifest = self.create(overwrite=True)

        self.assertFalse((self.output_dir / "test.jsonl").exists())
        self.assert_valid_outputs(manifest)

    def test_existing_active_outputs_are_preserved_without_overwrite(self):
        self.create()
        before = self.snapshot(self.output_dir)

        with self.assertRaises(FileExistsError):
            self.create(seed=17)

        self.assertEqual(self.snapshot(self.output_dir), before)

    def test_invalid_input_does_not_remove_stale_test(self):
        self.output_dir.mkdir()
        (self.output_dir / "test.jsonl").write_bytes(b"old test data\n")
        before = self.snapshot(self.output_dir)
        (self.input_dir / splits.EXPECTED_SHARDS[0]).write_text("{invalid json\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.create(overwrite=True)

        self.assertEqual(self.snapshot(self.output_dir), before)

    def test_invalid_fraction_does_not_remove_stale_test(self):
        self.output_dir.mkdir()
        (self.output_dir / "test.jsonl").write_bytes(b"old test data\n")
        before = self.snapshot(self.output_dir)

        with self.assertRaises(ValueError):
            self.create(validation_fraction=float("nan"), overwrite=True)

        self.assertEqual(self.snapshot(self.output_dir), before)

    def test_same_seed_is_deterministic_in_new_directory_and_on_overwrite(self):
        self.create(test_fraction=0.2, seed=17)
        before = self.snapshot(self.output_dir)
        other = self.root / "repeat"
        splits.create_splits(self.input_dir, other, test_fraction=0.2, seed=17)
        self.assertEqual(self.snapshot(other), before)

        self.create(test_fraction=0.2, seed=17, overwrite=True)

        self.assertEqual(self.snapshot(self.output_dir), before)

    def test_default_filters_ambiguous_text_and_preserves_transitive_groups(self):
        manifest = self.create(test_fraction=0.2)
        record_splits = self.assert_valid_outputs(manifest)

        self.assertEqual(manifest["excluded_records"], 2)
        self.assertEqual(len(record_splits), 68)
        self.assertNotIn("xH", record_splits)
        self.assertNotIn("yG", record_splits)
        self.assertEqual(manifest["text_statistics"]["cross_label_text_values"], 1)
        self.assertEqual(manifest["text_statistics"]["cross_label_text_rows"], 2)

    def test_keep_ambiguous_text_retains_rows_in_one_split(self):
        manifest = self.create(test_fraction=0.2, exclude_cross_label_text=False)
        record_splits = self.assert_valid_outputs(manifest)

        self.assertEqual(manifest["excluded_records"], 0)
        self.assertEqual(len(record_splits), 70)
        self.assertEqual(record_splits["xH"], record_splits["yG"])

    def test_cli_creates_valid_outputs(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input-dir", str(self.input_dir),
             "--output-dir", str(self.output_dir), "--test-fraction", "0.2"],
            capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output_dir / "split_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["splits"]), {"train", "validation", "test"})
        self.assert_valid_outputs(manifest)


if __name__ == "__main__":
    unittest.main()
