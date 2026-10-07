#!/usr/bin/env python3
"""Create leakage-aware train/validation[/test] splits of the released corpus."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

EXPECTED_SHARDS = tuple(f"corpus_{index:02d}.jsonl" for index in range(1, 10))
SPLIT_NAMES = ("train", "validation", "test")


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def load_records(input_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    paths = sorted(input_dir.glob("corpus_*.jsonl"))
    observed_names = tuple(path.name for path in paths)
    if observed_names != EXPECTED_SHARDS:
        raise ValueError(
            "Input must contain corpus_01.jsonl through corpus_09.jsonl; "
            f"found {list(observed_names)}"
        )

    records = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON in {path.name}:{line_number}: {error}") from error
                if not isinstance(record, dict):
                    raise ValueError(f"Expected an object in {path.name}:{line_number}")
                label = record.get("label")
                if type(label) is not int or label not in (0, 1):
                    raise ValueError(f"Invalid label in {path.name}:{line_number}: {label!r}")
                text = record.get("text")
                if not isinstance(text, str):
                    raise ValueError(f"Invalid text in {path.name}:{line_number}")
                identifier_field = "post_id" if label == 0 else "original_id"
                identifier = record.get(identifier_field)
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError(
                        f"Missing {identifier_field} in {path.name}:{line_number}"
                    )
                records.append(record)
    return records, list(observed_names)


def build_components(
    records: list[dict[str, Any]],
) -> tuple[UnionFind, dict[str, int], dict[str, int]]:
    groups = UnionFind(len(records))
    first_by_source_id: dict[str, int] = {}
    first_by_text: dict[str, int] = {}
    text_counts: Counter[str] = Counter()
    text_label_masks: dict[str, int] = defaultdict(int)

    for index, record in enumerate(records):
        label = record["label"]
        source_id = record["post_id"] if label == 0 else record["original_id"]
        previous = first_by_source_id.setdefault(source_id, index)
        groups.union(index, previous)

        text = record["text"]
        previous = first_by_text.setdefault(text, index)
        groups.union(index, previous)
        text_counts[text] += 1
        text_label_masks[text] |= 1 << label

    text_statistics = {
        "unique_texts": len(text_counts),
        "repeated_text_values": sum(count > 1 for count in text_counts.values()),
        "duplicate_rows_beyond_first": sum(count - 1 for count in text_counts.values()),
        "cross_label_text_values": sum(mask == 3 for mask in text_label_masks.values()),
        "cross_label_text_rows": sum(
            text_counts[text] for text, mask in text_label_masks.items() if mask == 3
        ),
    }
    cross_label_texts = {
        text for text, mask in text_label_masks.items() if mask == 3
    }
    return groups, text_statistics, {text: 1 for text in cross_label_texts}


def assign_groups(
    records: list[dict[str, Any]],
    groups: UnionFind,
    validation_fraction: float,
    test_fraction: float,
    seed: int,
    exclude_cross_label_text: bool = True,
    cross_label_texts: set[str] | None = None,
) -> tuple[dict[str, list[int]], dict[str, Any]]:
    if not math.isfinite(validation_fraction) or not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be finite and between 0 and 1")
    if not math.isfinite(test_fraction) or test_fraction < 0.0 or test_fraction >= 1.0:
        raise ValueError("test_fraction must be finite and in [0, 1)")
    train_fraction = 1.0 - validation_fraction - test_fraction
    if train_fraction <= 0.0:
        raise ValueError("validation_fraction + test_fraction must be less than 1")

    cross_label_texts = cross_label_texts or set()
    excluded_indices = {
        index for index, record in enumerate(records)
        if exclude_cross_label_text and record["text"] in cross_label_texts
    }
    split_names = ["train", "validation"]
    fractions = {"train": train_fraction, "validation": validation_fraction}
    if test_fraction > 0.0:
        split_names.append("test")
        fractions["test"] = test_fraction

    components: dict[int, list[int]] = defaultdict(list)
    eligible_counts = Counter()
    for index, record in enumerate(records):
        if index in excluded_indices:
            continue
        root = groups.find(index)
        components[root].append(index)
        eligible_counts[record["label"]] += 1
    if not components:
        raise ValueError("No records remain after applying the split filters")

    targets = {
        split: {
            label: eligible_counts[label] * fraction
            for label in (0, 1)
        }
        for split, fraction in fractions.items()
    }
    current = {
        split: {0: 0, 1: 0}
        for split in split_names
    }
    component_items = list(components.items())
    rng = random.Random(seed)
    rng.shuffle(component_items)
    component_items.sort(key=lambda item: -len(item[1]))
    assignment: dict[int, str] = {}

    for root, indices in component_items:
        component_labels = Counter(records[index]["label"] for index in indices)
        costs = {}
        for split in split_names:
            cost = 0.0
            for label in (0, 1):
                target = max(targets[split][label], 1.0)
                before = current[split][label] - targets[split][label]
                after = current[split][label] + component_labels[label] - targets[split][label]
                cost += (after * after - before * before) / (target * target)
            costs[split] = cost
        best_cost = min(costs.values())
        best_splits = [
            split for split, cost in costs.items()
            if math.isclose(cost, best_cost, rel_tol=0.0, abs_tol=1e-12)
        ]
        chosen_split = rng.choice(best_splits)
        assignment[root] = chosen_split
        for label in (0, 1):
            current[chosen_split][label] += component_labels[label]

    indices_by_split = {split: [] for split in split_names}
    component_counts = Counter()
    for index, record in enumerate(records):
        if index in excluded_indices:
            continue
        root = groups.find(index)
        split = assignment[root]
        indices_by_split[split].append(index)
    for root, split in assignment.items():
        component_counts[split] += 1

    split_statistics = {}
    for split, indices in indices_by_split.items():
        labels = Counter(records[index]["label"] for index in indices)
        split_statistics[split] = {
            "records": len(indices),
            "hwt_records": labels[0],
            "aigt_records": labels[1],
            "groups": component_counts[split],
        }

    manifest = {
        "split_seed": seed,
        "fractions": fractions,
        "grouping": ["shared post_id/original_id source hash", "exact text equality"],
        "text_comparison": "Exact, case-sensitive string equality; no normalization.",
        "cross_label_exact_text_excluded": exclude_cross_label_text,
        "cross_label_exact_text_values": len(cross_label_texts),
        "cross_label_exact_text_rows": len(excluded_indices) if exclude_cross_label_text else sum(
            1 for record in records if record["text"] in cross_label_texts
        ),
        "excluded_records": len(excluded_indices),
        "input_records": len(records),
        "split_records": len(records) - len(excluded_indices),
        "splits": split_statistics,
    }
    return indices_by_split, manifest


def create_splits(
    input_dir: Path,
    output_dir: Path,
    validation_fraction: float = 0.2,
    test_fraction: float = 0.0,
    seed: int = 42,
    exclude_cross_label_text: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    input_dir = input_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if input_dir == output_dir:
        raise ValueError("output_dir must be different from input_dir")

    records, input_shards = load_records(input_dir)
    groups, text_statistics, cross_label_text_map = build_components(records)
    indices_by_split, manifest = assign_groups(
        records,
        groups,
        validation_fraction,
        test_fraction,
        seed,
        exclude_cross_label_text,
        set(cross_label_text_map),
    )
    manifest["input_shards"] = input_shards
    manifest["text_statistics"] = text_statistics

    output_names = [f"{split}.jsonl" for split in indices_by_split] + ["split_manifest.json"]
    existing = [output_dir / name for name in output_names if (output_dir / name).exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Split outputs already exist; choose a new output directory or pass --overwrite:\n"
            + "\n".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    for split, indices in indices_by_split.items():
        path = output_dir / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for index in indices:
                stream.write(json.dumps(records[index], ensure_ascii=False) + "\n")

    manifest_path = output_dir / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=repo_root / "corpus")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--test-fraction", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--keep-cross-label-exact-text",
        action="store_true",
        help="Keep exact text groups that occur with both labels (default: exclude them).",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = create_splits(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        validation_fraction=args.validation_fraction,
        test_fraction=args.test_fraction,
        seed=args.seed,
        exclude_cross_label_text=not args.keep_cross_label_exact_text,
        overwrite=args.overwrite,
    )
    print(f"Created split files under {args.output_dir.expanduser().resolve()}")
    print(f"Grouping components: {manifest['grouping']}")
    print(f"Excluded records: {manifest['excluded_records']}")
    for split, counts in manifest["splits"].items():
        print(
            f"{split}: {counts['records']} records "
            f"({counts['hwt_records']} HWT, {counts['aigt_records']} AIGT) "
            f"in {counts['groups']} groups"
        )
    print("Wrote split_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
