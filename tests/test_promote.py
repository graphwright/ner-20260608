"""Tests for promote.py — truth_status promotion pass."""

import json
import pytest

from promote import promote_record, promote_records, DEFAULT_ASSERT_THRESHOLD, DEFAULT_DISPUTE_THRESHOLD


def _rec(conf: float, status: str = "hypothetical") -> dict:
    return {"id": f"stmt:{conf}", "extraction_confidence": conf, "truth_status": status}


class TestPromoteRecord:
    def test_high_confidence_promotes_to_asserted_true(self):
        r = promote_record(_rec(1.0), DEFAULT_ASSERT_THRESHOLD, DEFAULT_DISPUTE_THRESHOLD)
        assert r["truth_status"] == "asserted_true"

    def test_at_assert_threshold_promotes(self):
        r = promote_record(_rec(0.9), 0.9, 0.7)
        assert r["truth_status"] == "asserted_true"

    def test_mid_confidence_promotes_to_disputed(self):
        r = promote_record(_rec(0.8), DEFAULT_ASSERT_THRESHOLD, DEFAULT_DISPUTE_THRESHOLD)
        assert r["truth_status"] == "disputed"

    def test_at_dispute_threshold_promotes_to_disputed(self):
        r = promote_record(_rec(0.7), 0.9, 0.7)
        assert r["truth_status"] == "disputed"

    def test_low_confidence_stays_hypothetical(self):
        r = promote_record(_rec(0.5), DEFAULT_ASSERT_THRESHOLD, DEFAULT_DISPUTE_THRESHOLD)
        assert r["truth_status"] == "hypothetical"

    def test_zero_confidence_stays_hypothetical(self):
        r = promote_record(_rec(0.0), DEFAULT_ASSERT_THRESHOLD, DEFAULT_DISPUTE_THRESHOLD)
        assert r["truth_status"] == "hypothetical"

    def test_other_fields_preserved(self):
        rec = {"id": "stmt:x", "extraction_confidence": 0.95, "truth_status": "hypothetical",
               "predicate": "Knows", "subject_id": "wiki:Holmes"}
        r = promote_record(rec, 0.9, 0.7)
        assert r["predicate"] == "Knows"
        assert r["subject_id"] == "wiki:Holmes"

    def test_already_correct_status_returns_same_object(self):
        rec = _rec(1.0, status="asserted_true")
        r = promote_record(rec, 0.9, 0.7)
        assert r is rec  # no copy made when unchanged

    def test_custom_thresholds(self):
        assert promote_record(_rec(0.85), 0.8, 0.6)["truth_status"] == "asserted_true"
        assert promote_record(_rec(0.65), 0.8, 0.6)["truth_status"] == "disputed"
        assert promote_record(_rec(0.55), 0.8, 0.6)["truth_status"] == "hypothetical"

    def test_missing_confidence_defaults_to_zero(self):
        r = promote_record({"id": "x", "truth_status": "hypothetical"}, 0.9, 0.7)
        assert r["truth_status"] == "hypothetical"


class TestPromoteRecords:
    def test_empty_list(self):
        assert promote_records([]) == []

    def test_all_records_processed(self):
        records = [_rec(1.0), _rec(0.8), _rec(0.5)]
        result = promote_records(records)
        assert len(result) == 3

    def test_distribution(self):
        records = [_rec(1.0), _rec(0.95), _rec(0.8), _rec(0.7), _rec(0.5), _rec(0.4)]
        result = promote_records(records, assert_threshold=0.9, dispute_threshold=0.7)
        statuses = [r["truth_status"] for r in result]
        assert statuses.count("asserted_true") == 2   # 1.0, 0.95
        assert statuses.count("disputed") == 2        # 0.8, 0.7
        assert statuses.count("hypothetical") == 2    # 0.5, 0.4

    def test_order_preserved(self):
        records = [_rec(float(i) / 10) for i in range(10)]
        result = promote_records(records)
        for orig, prom in zip(records, result):
            assert orig["id"] == prom["id"]


class TestPromoteCLI:
    def test_roundtrip(self, tmp_path):
        import subprocess, sys
        input_file = tmp_path / "triplets.jsonl"
        output_file = tmp_path / "promoted.jsonl"
        records = [_rec(1.0), _rec(0.8), _rec(0.5)]
        with input_file.open("w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

        result = subprocess.run(
            [sys.executable, "src/promote.py",
             "--input", str(input_file),
             "--output", str(output_file)],
            capture_output=True, text=True,
            cwd="/Users/wware/tmp/ner-20260608",
        )
        assert result.returncode == 0
        with output_file.open() as fh:
            out = [json.loads(l) for l in fh]
        assert out[0]["truth_status"] == "asserted_true"
        assert out[1]["truth_status"] == "disputed"
        assert out[2]["truth_status"] == "hypothetical"

    def test_invalid_thresholds_exit_nonzero(self, tmp_path):
        import subprocess, sys
        input_file = tmp_path / "triplets.jsonl"
        input_file.write_text("")
        result = subprocess.run(
            [sys.executable, "src/promote.py",
             "--input", str(input_file),
             "--assert-threshold", "0.5",
             "--dispute-threshold", "0.8"],
            capture_output=True, text=True,
            cwd="/Users/wware/tmp/ner-20260608",
        )
        assert result.returncode != 0
