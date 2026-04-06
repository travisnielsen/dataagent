"""Unit tests for evaluation runner orchestration and quality gate logic."""

from __future__ import annotations

import json
from pathlib import Path

from evaluations.config import build_default_config
from evaluations.models import EvaluationRun, MetricResult, RunSummary
from evaluations.runner import (
    compute_quality_gate,
    persist_run_results,
    run_cloud_evaluation,
    run_evaluation,
)


def _make_dataset(tmp_path: Path, records: list[dict[str, object]] | None = None) -> Path:
    """Write a minimal JSONL dataset and return the path."""
    if records is None:
        records = [
            {
                "query": "Show top customers",
                "expected_behavior": "Returns ranked customer list",
                "scenario_class": "template",
                "ground_truth_sql": "SELECT TOP 10 * FROM Sales.Customers",
            },
            {
                "query": "What orders were placed last month?",
                "expected_behavior": "Returns recent orders",
                "scenario_class": "dynamic",
            },
            {
                "query": "Who is our best supplier?",
                "expected_behavior": "Asks clarifying question about metric",
                "scenario_class": "clarification",
            },
        ]
    path = tmp_path / "test_dataset.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


# ---------------------------------------------------------------------------
# Evaluation runner (T038)
# ---------------------------------------------------------------------------


class TestRunEvaluation:
    async def test_run_evaluation_basic(self, tmp_path: Path) -> None:
        dataset_path = _make_dataset(tmp_path)
        config = build_default_config()
        run, summary = await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=["intent_resolution", "relevance"],
            config=config,
            trigger="manual",
        )
        assert run.status == "completed"
        assert summary.total_records == 3
        assert len(summary.metrics) == 2

    async def test_run_evaluation_with_custom_evaluators(self, tmp_path: Path) -> None:
        dataset_path = _make_dataset(tmp_path)
        config = build_default_config()
        run, summary = await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=["sql_safety", "param_extraction_correctness"],
            config=config,
        )
        assert run.status == "completed"
        assert summary.total_records == 3

    async def test_run_evaluation_has_run_id(self, tmp_path: Path) -> None:
        dataset_path = _make_dataset(tmp_path)
        config = build_default_config()
        run, summary = await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=["relevance"],
            config=config,
        )
        assert run.run_id
        assert summary.run_id == run.run_id

    async def test_run_evaluation_with_git_metadata(self, tmp_path: Path) -> None:
        dataset_path = _make_dataset(tmp_path)
        config = build_default_config()
        run, _ = await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=["relevance"],
            config=config,
            git_sha="abc123",
            branch="feature-branch",
        )
        assert run.git_sha == "abc123"
        assert run.branch == "feature-branch"


# ---------------------------------------------------------------------------
# Quality gate decision (T041)
# ---------------------------------------------------------------------------


def _make_summary(
    *,
    metrics: list[MetricResult] | None = None,
    overall_pass: bool = True,
) -> RunSummary:
    if metrics is None:
        metrics = [
            MetricResult(
                metric="intent_resolution",
                mean_score=0.90,
                median_score=0.90,
                p5_score=0.80,
                p95_score=0.95,
                pass_rate=0.90,
                sample_count=50,
                threshold=0.85,
                passed=True,
            ),
            MetricResult(
                metric="sql_safety",
                mean_score=0.98,
                median_score=1.0,
                p5_score=0.90,
                p95_score=1.0,
                pass_rate=0.98,
                sample_count=50,
                threshold=0.95,
                passed=True,
            ),
            MetricResult(
                metric="relevance",
                mean_score=0.82,
                median_score=0.85,
                p5_score=0.70,
                p95_score=0.95,
                pass_rate=0.82,
                sample_count=50,
                threshold=0.75,
                passed=True,
            ),
            MetricResult(
                metric="answer_adequacy",
                mean_score=3.5,
                median_score=4.0,
                p5_score=2.0,
                p95_score=5.0,
                pass_rate=0.80,
                sample_count=50,
                threshold=3.0,
                passed=True,
            ),
        ]
    return RunSummary(
        run_id="test-run-123",
        metrics=metrics,
        total_records=50,
        total_passed=45,
        total_failed=5,
        overall_pass=overall_pass,
        failure_count_by_cluster={},
    )


class TestQualityGate:
    def test_gate_pass(self) -> None:
        summary = _make_summary()
        gate = compute_quality_gate(summary, git_sha="abc", branch="main")
        assert gate.gate_result == "pass"
        assert gate.failing_metrics == []

    def test_gate_fail_on_regression(self) -> None:
        metrics = [
            MetricResult(
                metric="intent_resolution",
                mean_score=0.70,  # Below 0.85 threshold
                median_score=0.70,
                p5_score=0.50,
                p95_score=0.80,
                pass_rate=0.60,
                sample_count=50,
                threshold=0.85,
                passed=False,
            ),
            MetricResult(
                metric="sql_safety",
                mean_score=0.98,
                median_score=1.0,
                p5_score=0.90,
                p95_score=1.0,
                pass_rate=0.98,
                sample_count=50,
                threshold=0.95,
                passed=True,
            ),
            MetricResult(
                metric="relevance",
                mean_score=0.82,
                median_score=0.85,
                p5_score=0.70,
                p95_score=0.95,
                pass_rate=0.82,
                sample_count=50,
                threshold=0.75,
                passed=True,
            ),
            MetricResult(
                metric="answer_adequacy",
                mean_score=3.5,
                median_score=4.0,
                p5_score=2.0,
                p95_score=5.0,
                pass_rate=0.80,
                sample_count=50,
                threshold=3.0,
                passed=True,
            ),
        ]
        summary = _make_summary(metrics=metrics, overall_pass=False)
        gate = compute_quality_gate(summary, git_sha="abc", branch="main")
        assert gate.gate_result == "fail"
        assert "intent_resolution" in gate.failing_metrics

    def test_gate_waiver_overrides_failure(self) -> None:
        metrics = [
            MetricResult(
                metric="intent_resolution",
                mean_score=0.70,
                median_score=0.70,
                p5_score=0.50,
                p95_score=0.80,
                pass_rate=0.60,
                sample_count=50,
                threshold=0.85,
                passed=False,
            ),
            MetricResult(
                metric="sql_safety",
                mean_score=0.98,
                median_score=1.0,
                p5_score=0.90,
                p95_score=1.0,
                pass_rate=0.98,
                sample_count=50,
                threshold=0.95,
                passed=True,
            ),
            MetricResult(
                metric="relevance",
                mean_score=0.82,
                median_score=0.85,
                p5_score=0.70,
                p95_score=0.95,
                pass_rate=0.82,
                sample_count=50,
                threshold=0.75,
                passed=True,
            ),
            MetricResult(
                metric="answer_adequacy",
                mean_score=3.5,
                median_score=4.0,
                p5_score=2.0,
                p95_score=5.0,
                pass_rate=0.80,
                sample_count=50,
                threshold=3.0,
                passed=True,
            ),
        ]
        summary = _make_summary(metrics=metrics, overall_pass=False)
        gate = compute_quality_gate(
            summary,
            git_sha="abc",
            branch="main",
            waivers=["intent_resolution"],
        )
        assert gate.gate_result == "pass"

    def test_gate_skips_unevaluated_metrics(self) -> None:
        summary = _make_summary(metrics=[])
        gate = compute_quality_gate(summary, git_sha="abc", branch="main")
        assert gate.gate_result == "pass"
        assert gate.failing_metrics == []


# ---------------------------------------------------------------------------
# Cloud evaluation fallback (T052)
# ---------------------------------------------------------------------------


class TestCloudEvaluation:
    async def test_cloud_eval_falls_back_to_local(self, tmp_path: Path) -> None:
        """When azure-ai-projects is not configured, falls back to local."""
        dataset_path = _make_dataset(tmp_path)
        config = build_default_config()
        run, summary = await run_cloud_evaluation(
            dataset_path=dataset_path,
            evaluator_names=["relevance"],
            config=config,
            trigger="nightly",
        )
        assert run.status == "completed"
        assert summary is not None
        assert summary.total_records == 3


# ---------------------------------------------------------------------------
# Result persistence (T052)
# ---------------------------------------------------------------------------


class TestPersistResults:
    def test_persist_run_results(self, tmp_path: Path) -> None:
        run = EvaluationRun(
            run_id="test-persist-123",
            dataset_name="test",
            dataset_version="v1",
            evaluator_names=["relevance"],
            config_version="v1",
            trigger="manual",
            status="completed",
            started_at="2026-01-01T00:00:00Z",
        )
        summary = _make_summary()
        result_path = persist_run_results(run, summary, results_dir=tmp_path)
        assert result_path.exists()
        assert (tmp_path / "latest-summary.json").exists()

        # Verify JSON content is valid
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["run"]["run_id"] == "test-persist-123"
        assert "summary" in data
