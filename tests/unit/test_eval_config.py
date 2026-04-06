"""Unit tests for evaluation config loading, validation, and default contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluations.config import (
    BUILTIN_EVALUATOR_PROFILES,
    CUSTOM_EVALUATOR_PROFILES,
    DEFAULT_THRESHOLDS,
    PIPELINE_TARGETS,
    EvaluatorRef,
    ThresholdRule,
    build_default_config,
    load_config,
    save_config,
)
from pydantic import ValidationError


class TestThresholdRule:
    def test_valid_threshold(self) -> None:
        rule = ThresholdRule(metric="intent_resolution", min_score=0.85, priority="P0")
        assert rule.metric == "intent_resolution"
        assert rule.min_score == pytest.approx(0.85)
        assert rule.priority == "P0"

    def test_priority_literal_validation(self) -> None:
        with pytest.raises(ValidationError):
            ThresholdRule(metric="m", min_score=0.5, priority="P3")  # type: ignore[arg-type]


class TestEvaluatorRef:
    def test_valid_ref(self) -> None:
        ref = EvaluatorRef(name="relevance", type="builtin")
        assert ref.name == "relevance"
        assert ref.version == "v1"

    def test_custom_type(self) -> None:
        ref = EvaluatorRef(name="sql_safety", type="custom_code", version="v2")
        assert ref.type == "custom_code"
        assert ref.version == "v2"


class TestEvaluationConfig:
    def test_build_default_config(self) -> None:
        config = build_default_config(project_endpoint="https://example.com")
        assert config.config_version == "v1"
        assert config.project_endpoint == "https://example.com"
        assert len(config.evaluators) > 0
        assert len(config.thresholds) > 0

    def test_default_config_has_all_evaluators(self) -> None:
        config = build_default_config()
        evaluator_names = {e.name for e in config.evaluators}
        for profile in BUILTIN_EVALUATOR_PROFILES:
            assert profile.name in evaluator_names
        for profile in CUSTOM_EVALUATOR_PROFILES:
            assert profile.name in evaluator_names

    def test_default_thresholds_cover_p0_metrics(self) -> None:
        p0_metrics = {t.metric for t in DEFAULT_THRESHOLDS if t.priority == "P0"}
        assert "intent_resolution" in p0_metrics
        assert "sql_safety" in p0_metrics
        assert "relevance" in p0_metrics
        assert "answer_adequacy" in p0_metrics

    def test_every_pipeline_target_has_evaluator(self) -> None:
        config = build_default_config()
        evaluator_names = {e.name for e in config.evaluators}
        for metrics in PIPELINE_TARGETS.values():
            for metric in metrics:
                assert metric in evaluator_names, f"Metric {metric} not in evaluators"


class TestConfigPersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        config = build_default_config(project_endpoint="https://test.cognitiveservices.azure.com")
        path = tmp_path / "eval_config.json"
        save_config(config, path)
        loaded = load_config(path)
        assert loaded == config

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_config(path)

    def test_load_invalid_schema_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_schema.json"
        path.write_text('{"config_version": "v1"}', encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(path)

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        config = build_default_config()
        path = tmp_path / "nested" / "dir" / "config.json"
        save_config(config, path)
        assert path.exists()


class TestBuiltinEvaluatorProfiles:
    def test_phase1_profiles_exist(self) -> None:
        names = {p.name for p in BUILTIN_EVALUATOR_PROFILES}
        assert "intent_resolution" in names
        assert "task_adherence" in names
        assert "tool_call_accuracy" in names
        assert "relevance" in names
        assert "indirect_attack" in names

    def test_all_phase1(self) -> None:
        for profile in BUILTIN_EVALUATOR_PROFILES:
            assert profile.phase == 1


class TestCustomEvaluatorProfiles:
    def test_phase2_profiles_exist(self) -> None:
        names = {p.name for p in CUSTOM_EVALUATOR_PROFILES}
        assert "sql_safety" in names
        assert "param_extraction_correctness" in names
        assert "answer_adequacy" in names
        assert "clarification_quality" in names

    def test_all_phase2(self) -> None:
        for profile in CUSTOM_EVALUATOR_PROFILES:
            assert profile.phase == 2


class TestPipelineTargets:
    def test_all_components_have_targets(self) -> None:
        assert "DataAssistant" in PIPELINE_TARGETS
        assert "ParameterExtractor" in PIPELINE_TARGETS
        assert "QueryValidator" in PIPELINE_TARGETS
        assert "QueryBuilder" in PIPELINE_TARGETS

    def test_each_target_has_metrics(self) -> None:
        for target, metrics in PIPELINE_TARGETS.items():
            assert len(metrics) > 0, f"{target} has no metrics"
