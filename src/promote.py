"""
promote.py

Promotion pass — updates truth_status in bohemia_triplets.jsonl based on
extraction_confidence.

LLM extraction leaves all triplets as ``hypothetical`` by conservative
default. This pass applies threshold rules to promote records:

    extraction_confidence >= --assert-threshold   →  asserted_true
    extraction_confidence >= --dispute-threshold  →  disputed
    below --dispute-threshold                     →  hypothetical (unchanged)

Defaults (tuned to the Bohemia dataset confidence distribution):
    --assert-threshold   0.9   (covers ~90% of records at 0.9 or 1.0)
    --dispute-threshold  0.7   (catches the 0.8 band as disputed)

Reads:
    bohemia_triplets.jsonl   — output of triplets.py

Writes:
    bohemia_triplets.jsonl   — in-place update (override with --output)

Usage:
    python promote.py
    python promote.py --input bohemia_triplets.jsonl --output promoted.jsonl
    python promote.py --assert-threshold 0.95 --dispute-threshold 0.8
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


DEFAULT_ASSERT_THRESHOLD = 0.9
DEFAULT_DISPUTE_THRESHOLD = 0.7


def promote_record(record: dict, assert_threshold: float, dispute_threshold: float) -> dict:
    conf = float(record.get("extraction_confidence", 0.0))
    if conf >= assert_threshold:
        new_status = "asserted_true"
    elif conf >= dispute_threshold:
        new_status = "disputed"
    else:
        new_status = "hypothetical"
    if record.get("truth_status") == new_status:
        return record
    return {**record, "truth_status": new_status}


def promote_records(
    records: list[dict],
    assert_threshold: float = DEFAULT_ASSERT_THRESHOLD,
    dispute_threshold: float = DEFAULT_DISPUTE_THRESHOLD,
) -> list[dict]:
    return [promote_record(r, assert_threshold, dispute_threshold) for r in records]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote triplet truth_status by extraction_confidence."
    )
    parser.add_argument("--input", default="bohemia_triplets.jsonl", type=Path,
                        help="Input triplets JSONL (default: %(default)s)")
    parser.add_argument("--output", default=None, type=Path,
                        help="Output file (default: overwrite --input)")
    parser.add_argument("--assert-threshold", type=float, default=DEFAULT_ASSERT_THRESHOLD,
                        metavar="F",
                        help="Min confidence for asserted_true (default: %(default)s)")
    parser.add_argument("--dispute-threshold", type=float, default=DEFAULT_DISPUTE_THRESHOLD,
                        metavar="F",
                        help="Min confidence for disputed (default: %(default)s)")
    args = parser.parse_args()

    if args.assert_threshold <= args.dispute_threshold:
        parser.error("--assert-threshold must be greater than --dispute-threshold")

    output_path = args.output or args.input

    with args.input.open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]

    promoted = promote_records(records, args.assert_threshold, args.dispute_threshold)

    before = Counter(r["truth_status"] for r in records)
    after = Counter(r["truth_status"] for r in promoted)
    print(f"Before: {dict(before)}", file=sys.stderr)
    print(f"After:  {dict(after)}", file=sys.stderr)
    changed = sum(1 for a, b in zip(records, promoted) if a["truth_status"] != b["truth_status"])
    print(f"Changed {changed} of {len(records)} records → {output_path}", file=sys.stderr)

    with output_path.open("w", encoding="utf-8") as fh:
        for r in promoted:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
