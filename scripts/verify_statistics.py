#!/usr/bin/env python3
"""Verify the released corpus statistics and public artifact contract."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_METADATA_FILES = {
    "instance_metadata_0.jsonl",
    "instance_metadata_1.jsonl",
    "instance_metadata_2.jsonl",
    "metadata_errors.csv",
}
EXPECTED_ERROR_HEADER = ["Instance", "API_Endpoint", "Error_Reason"]
EXPECTED_SHARD_COUNT = 9
MAX_SHARD_BYTES = 45_000_000
EXPECTED_METADATA_RECORD_COUNT = 263
EXPECTED_SCHEMA = {
    "community_id",
    "generation_method",
    "is_reply",
    "label",
    "language",
    "llm_model",
    "original_id",
    "post_id",
    "text",
}
BASE_FIELDS = {"text", "label", "community_id", "is_reply", "language"}
LANGUAGE_NORMALIZATION = {
    "ja-IM": "ja",
    "en-OU": "en",
    "en-GB": "en",
    "zh-HK": "zh",
    "zh-CN": "zh",
    "zh-TW": "zh",
    "pt-BR": "pt",
}


def parse_expected(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    top_languages: list[dict[str, object]] = []
    section = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[top_languages]":
            section = "top_languages"
            continue
        if "=" not in line:
            raise ValueError(f"Invalid expected-statistics line {line_number}: {raw_line}")
        key, value = (part.strip() for part in line.split("=", 1))
        if section == "top_languages":
            language, hwt, aigt, total = value.split("|")
            top_languages.append(
                {
                    "language": language,
                    "hwt": int(hwt),
                    "aigt": int(aigt),
                    "total": int(total),
                }
            )
        else:
            values[key] = int(value)
    values["top_languages"] = top_languages
    return values


def normalize_language(language: str) -> str:
    return LANGUAGE_NORMALIZATION.get(language, language)


def is_hash(value: object) -> bool:
    return isinstance(value, str) and HASH_PATTERN.fullmatch(value) is not None


def hash_identifier(identifier: str) -> str:
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def stream_jsonl(path: Path):
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON in {path}:{line_number}: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"Expected an object in {path}:{line_number}")
            yield line_number, record


def verify(repo_root: Path, expected_path: Path) -> list[str]:
    expected = parse_expected(expected_path)
    failures: list[str] = []

    def check(name: str, actual: object, wanted: object) -> None:
        if actual != wanted:
            failures.append(f"{name}: expected {wanted!r}, got {actual!r}")

    corpus_dir = repo_root / "corpus"
    expected_shards = [corpus_dir / f"corpus_{index:02d}.jsonl" for index in range(1, 10)]
    actual_shards = sorted(corpus_dir.glob("corpus_*.jsonl"))
    check("public shard names", [path.name for path in actual_shards], [path.name for path in expected_shards])
    check("public shard count", len(actual_shards), EXPECTED_SHARD_COUNT)
    if list(repo_root.glob("corpus_*.jsonl")):
        failures.append("root-level corpus shards must not be present")

    rows = 0
    labels: Counter[int] = Counter()
    communities: set[str] = set()
    languages: set[str] = set()
    public_schema: set[str] = set()
    id_values = {"post_id": set(), "original_id": set()}
    id_present: Counter[str] = Counter()
    language_labels: Counter[tuple[str, int]] = Counter()

    for shard in expected_shards:
        if not shard.exists():
            failures.append(f"missing public shard: {shard.name}")
            continue
        if shard.stat().st_size >= MAX_SHARD_BYTES:
            failures.append(f"{shard.name} is not below {MAX_SHARD_BYTES} bytes")
        try:
            records = stream_jsonl(shard)
            for line_number, record in records:
                rows += 1
                public_schema.update(record)
                if not BASE_FIELDS <= record.keys():
                    failures.append(f"{shard.name}:{line_number} is missing a base field")
                label = record.get("label")
                if label not in (0, 1):
                    failures.append(f"{shard.name}:{line_number} has invalid label {label!r}")
                else:
                    labels[label] += 1
                community = record.get("community_id")
                if not isinstance(community, str):
                    failures.append(f"{shard.name}:{line_number} has invalid community_id")
                else:
                    communities.add(community)
                language = record.get("language")
                if isinstance(language, str):
                    languages.add(language)
                    if language != "unknown" and label in (0, 1):
                        language_labels[(normalize_language(language), label)] += 1
                else:
                    failures.append(f"{shard.name}:{line_number} has invalid language")
                for field in ("post_id", "original_id"):
                    value = record.get(field)
                    if value is not None:
                        id_present[field] += 1
                        if not is_hash(value):
                            failures.append(f"{shard.name}:{line_number}:{field} is not lowercase SHA-256 hex")
                        else:
                            id_values[field].add(value)
                if label == 0 and ("post_id" not in record or "original_id" in record):
                    failures.append(f"{shard.name}:{line_number} has invalid HWT ID fields")
                if label == 1 and ("original_id" not in record or "post_id" in record):
                    failures.append(f"{shard.name}:{line_number} has invalid AIGT ID fields")
        except ValueError as error:
            failures.append(str(error))

    check("total records", rows, expected["total_records"])
    check("HWT records", labels[0], expected["hwt_records"])
    check("AIGT records", labels[1], expected["aigt_records"])
    check("community count", len(communities), expected["community_count"])
    reported_languages = {
        normalize_language(language) for language in languages if language != "unknown"
    }
    check("reported language count", len(reported_languages), expected["reported_language_count"])
    check("public schema", sorted(public_schema), sorted(EXPECTED_SCHEMA))
    if public_schema & {"response_model", "_retry_aigt_index"}:
        failures.append("internal fields are present in the public schema")
    check("post_id presence", id_present["post_id"], labels[0])
    check("original_id presence", id_present["original_id"], labels[1])
    if not id_values["original_id"] <= id_values["post_id"]:
        failures.append("original_id hashes are not a subset of post_id hashes")
    if hash_identifier("same input") != hashlib.sha256(b"same input").hexdigest():
        failures.append("identifier hashing does not match SHA-256 over UTF-8 input")

    actual_top_languages = []
    for language in reported_languages:
        hwt = language_labels[(language, 0)]
        aigt = language_labels[(language, 1)]
        actual_top_languages.append(
            {
                "language": language,
                "hwt": hwt,
                "aigt": aigt,
                "total": hwt + aigt,
            }
        )
    actual_top_languages = sorted(actual_top_languages, key=lambda row: row["total"], reverse=True)[:10]
    check("top-10 language statistics", actual_top_languages, expected["top_languages"])

    metadata_dir = repo_root / "instance_metadata"
    actual_metadata_files = {path.name for path in metadata_dir.iterdir()} if metadata_dir.exists() else set()
    check("instance metadata files", actual_metadata_files, EXPECTED_METADATA_FILES)
    metadata_instances: list[str] = []
    for path in sorted(metadata_dir.glob("instance_metadata_*.jsonl")):
        try:
            for _, record in stream_jsonl(path):
                instance = record.get("instance")
                if not isinstance(instance, str):
                    failures.append(f"{path.name} contains a record without an instance")
                else:
                    metadata_instances.append(instance)
        except ValueError as error:
            failures.append(str(error))
    check("metadata record count", len(metadata_instances), EXPECTED_METADATA_RECORD_COUNT)
    check("unique metadata instances", len(set(metadata_instances)), EXPECTED_METADATA_RECORD_COUNT)
    check("metadata community coverage", set(metadata_instances), communities)

    error_path = metadata_dir / "metadata_errors.csv"
    if error_path.exists():
        with error_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            for _ in reader:
                pass
        check("metadata error header", header, EXPECTED_ERROR_HEADER)
    else:
        failures.append("missing instance_metadata/metadata_errors.csv")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Release repository root (defaults to the parent of scripts/).",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=None,
        help="Expected-statistics file (defaults to expected/corpus_statistics.txt).",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    expected_path = (args.expected or repo_root / "expected" / "corpus_statistics.txt").resolve()
    failures = verify(repo_root, expected_path)
    if failures:
        print("Artifact statistics verification: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Artifact statistics verification: PASS")
    print(f"Repository: {repo_root}")
    print("Claims verified: records, labels, communities, languages, instances, metadata, schema, IDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
