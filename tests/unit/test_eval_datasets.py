"""Unit tests for dataset loading, validation, metadata, and sanitization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluations.harvest import (
    get_next_version,
    merge_datasets,
    persist_dataset,
    sanitize_records,
    sanitize_text,
)
from evaluations.models import DatasetRecord
from evaluations.runner import generate_metadata, load_dataset, validate_dataset


def _make_record(**overrides: object) -> dict[str, object]:
    """Build a minimal valid dataset record dict."""
    base: dict[str, object] = {
        "query": "Show top customers",
        "expected_behavior": "Returns ranked customer list",
        "scenario_class": "template",
    }
    base.update(overrides)
    return base


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Dataset loading (T017)
# ---------------------------------------------------------------------------


class TestLoadDataset:
    def test_load_valid_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        _write_jsonl(path, [_make_record(), _make_record(scenario_class="dynamic")])
        records = load_dataset(path)
        assert len(records) == 2
        assert records[0].scenario_class == "template"
        assert records[1].scenario_class == "dynamic"

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        records = load_dataset(path)
        assert records == []

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_dataset(tmp_path / "missing.jsonl")

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_dataset(path)

    def test_load_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "blanks.jsonl"
        content = json.dumps(_make_record()) + "\n\n" + json.dumps(_make_record()) + "\n"
        path.write_text(content, encoding="utf-8")
        records = load_dataset(path)
        assert len(records) == 2

    def test_load_with_optional_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "full.jsonl"
        record = _make_record(
            ground_truth_sql="SELECT 1",
            ground_truth_params={"x": "val"},
            context="some context",
        )
        _write_jsonl(path, [record])
        loaded = load_dataset(path)
        assert loaded[0].ground_truth_sql == "SELECT 1"
        assert loaded[0].ground_truth_params == {"x": "val"}


# ---------------------------------------------------------------------------
# Dataset validation (T017)
# ---------------------------------------------------------------------------


class TestValidateDataset:
    def test_valid_dataset(self, tmp_path: Path) -> None:
        records = [
            DatasetRecord(**_make_record(scenario_class="template")),
            DatasetRecord(**_make_record(scenario_class="dynamic")),
            DatasetRecord(**_make_record(scenario_class="clarification")),
        ]
        errors = validate_dataset(records)
        assert errors == []

    def test_empty_dataset(self) -> None:
        errors = validate_dataset([])
        assert "Dataset is empty" in errors

    def test_empty_query(self) -> None:
        records = [DatasetRecord(**_make_record(query="  "))]
        errors = validate_dataset(records)
        assert any("empty query" in e for e in errors)

    def test_missing_scenario_classes(self) -> None:
        records = [DatasetRecord(**_make_record(scenario_class="template"))]
        errors = validate_dataset(records)
        assert any("Missing scenario classes" in e for e in errors)


# ---------------------------------------------------------------------------
# Metadata generation (T020)
# ---------------------------------------------------------------------------


class TestGenerateMetadata:
    def test_metadata_from_records(self) -> None:
        records = [
            DatasetRecord(**_make_record(scenario_class="template")),
            DatasetRecord(**_make_record(scenario_class="template")),
            DatasetRecord(**_make_record(scenario_class="dynamic")),
        ]
        meta = generate_metadata(records, name="test-dataset", version="v1")
        assert meta.record_count == 3
        assert meta.scenario_distribution == {"template": 2, "dynamic": 1}
        assert meta.name == "test-dataset"
        assert meta.version == "v1"
        assert meta.sanitization_status == "passed"


class TestSanitization:
    def test_sanitize_email(self) -> None:
        result = sanitize_text("Contact user@example.com for help")
        assert "[REDACTED_EMAIL]" in result
        assert "user@example.com" not in result

    def test_sanitize_phone(self) -> None:
        result = sanitize_text("Call 555-123-4567")
        assert "[REDACTED_PHONE]" in result

    def test_sanitize_ssn(self) -> None:
        result = sanitize_text("SSN: 123-45-6789")
        assert "[REDACTED_SSN]" in result

    def test_sanitize_credit_card(self) -> None:
        result = sanitize_text("Card: 4111 1111 1111 1111")
        assert "[REDACTED_CC]" in result

    def test_sanitize_records_batch(self) -> None:
        raw = [
            {"query": "Email: test@test.com", "response": "Call 555-000-1234"},
        ]
        sanitized = sanitize_records(raw)
        assert "[REDACTED_EMAIL]" in sanitized[0]["query"]
        assert "[REDACTED_PHONE]" in sanitized[0]["response"]

    def test_clean_text_unchanged(self) -> None:
        text = "Show me orders from last week"
        assert sanitize_text(text) == text


class TestVersioning:
    def test_next_version_empty_dir(self, tmp_path: Path) -> None:
        assert get_next_version(tmp_path, "cadence-traces") == "v1"

    def test_next_version_with_existing(self, tmp_path: Path) -> None:
        (tmp_path / "cadence-traces-v1.jsonl").touch()
        (tmp_path / "cadence-traces-v2.jsonl").touch()
        assert get_next_version(tmp_path, "cadence-traces") == "v3"

    def test_persist_dataset(self, tmp_path: Path) -> None:
        records = [DatasetRecord(**_make_record())]
        output = tmp_path / "out" / "dataset.jsonl"
        meta = persist_dataset(records, output_path=output, name="test", version="v1")
        assert output.exists()
        assert meta.record_count == 1
        assert meta.source == "trace_harvested"


# ---------------------------------------------------------------------------
# Merge datasets (T021 - Foundry trace strategy)
# ---------------------------------------------------------------------------


class TestMergeDatasets:
    def test_merge_gold_and_traces(self) -> None:
        gold = [
            DatasetRecord(query="Q1", expected_behavior="E1", scenario_class="template"),
            DatasetRecord(query="Q2", expected_behavior="E2", scenario_class="dynamic"),
        ]
        traces = [
            DatasetRecord(query="Q3", expected_behavior="E3", scenario_class="conversation"),
            DatasetRecord(query="Q4", expected_behavior="E4", scenario_class="conversation"),
        ]
        merged = merge_datasets(gold, traces, deduplicate=False)
        assert len(merged) == 4
        assert merged[0].query == "Q1"
        assert merged[3].query == "Q4"

    def test_merge_with_deduplication(self) -> None:
        gold = [
            DatasetRecord(query="Q1", expected_behavior="E1", scenario_class="template"),
            DatasetRecord(query="Q2", expected_behavior="E2", scenario_class="dynamic"),
        ]
        traces = [
            DatasetRecord(query="Q2", expected_behavior="E2_trace", scenario_class="conversation"),
            DatasetRecord(query="Q3", expected_behavior="E3", scenario_class="conversation"),
        ]
        merged = merge_datasets(gold, traces, deduplicate=True)
        assert len(merged) == 3
        assert merged[0].query == "Q1"
        assert merged[1].query == "Q2"
        assert merged[2].query == "Q3"
        # Gold version should be kept
        assert merged[1].scenario_class == "dynamic"

    def test_merge_case_insensitive_dedup(self) -> None:
        gold = [
            DatasetRecord(query="Show Customers", expected_behavior="E1", scenario_class="template")
        ]
        traces = [
            DatasetRecord(
                query="show customers", expected_behavior="E2", scenario_class="conversation"
            )
        ]
        merged = merge_datasets(gold, traces, deduplicate=True)
        assert len(merged) == 1
