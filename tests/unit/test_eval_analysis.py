"""Unit tests for failure clustering and delta comparison."""

from __future__ import annotations

import pytest
from evaluations.analysis import classify_failure, cluster_failures, compare_runs
from evaluations.models import FailureRecord, MetricResult, RunSummary


def _make_failure(
    *,
    query: str = "test query",
    cluster: str = "intent_misroute",
    failing_metrics: dict[str, float] | None = None,
    record_index: int = 0,
) -> FailureRecord:
    return FailureRecord(
        record_index=record_index,
        query=query,
        failing_metrics=failing_metrics or {"intent_resolution": 0.3},
        cluster=cluster,  # type: ignore[arg-type]
    )


def _make_metric(
    name: str,
    mean: float,
    *,
    threshold: float | None = None,
    passed: bool | None = None,
) -> MetricResult:
    return MetricResult(
        metric=name,
        mean_score=mean,
        median_score=mean,
        p5_score=mean - 0.1,
        p95_score=mean + 0.1,
        pass_rate=0.9,
        sample_count=50,
        threshold=threshold,
        passed=passed,
    )


def _make_summary(
    run_id: str,
    metrics: list[MetricResult],
) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        metrics=metrics,
        total_records=50,
        total_passed=45,
        total_failed=5,
        overall_pass=True,
        failure_count_by_cluster={},
    )


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_intent_resolution_maps_to_intent_misroute(self) -> None:
        assert classify_failure({"intent_resolution": 0.3}) == "intent_misroute"

    def test_sql_safety_maps_to_safety_violation(self) -> None:
        assert classify_failure({"sql_safety": 0.0}) == "safety_violation"

    def test_relevance_maps_to_poor_answer_quality(self) -> None:
        assert classify_failure({"relevance": 0.4}) == "poor_answer_quality"

    def test_unknown_metric_defaults(self) -> None:
        assert classify_failure({"unknown_metric": 0.1}) == "poor_answer_quality"


# ---------------------------------------------------------------------------
# Failure clustering
# ---------------------------------------------------------------------------


class TestClusterFailures:
    def test_empty_failures(self) -> None:
        assert cluster_failures([]) == []

    def test_single_cluster(self) -> None:
        failures = [
            _make_failure(query="q1", cluster="intent_misroute"),
            _make_failure(query="q2", cluster="intent_misroute"),
        ]
        clusters = cluster_failures(failures)
        assert len(clusters) == 1
        assert clusters[0].cluster_type == "intent_misroute"
        assert clusters[0].count == 2
        assert clusters[0].percentage == pytest.approx(100.0)

    def test_multiple_clusters_sorted_by_count(self) -> None:
        failures = [
            _make_failure(cluster="intent_misroute"),
            _make_failure(cluster="intent_misroute"),
            _make_failure(cluster="intent_misroute"),
            _make_failure(cluster="safety_violation"),
        ]
        clusters = cluster_failures(failures)
        assert len(clusters) == 2
        assert clusters[0].cluster_type == "intent_misroute"
        assert clusters[0].count == 3
        assert clusters[1].cluster_type == "safety_violation"
        assert clusters[1].count == 1

    def test_representative_queries_capped_at_5(self) -> None:
        failures = [
            _make_failure(query=f"query {i}", cluster="extraction_error") for i in range(10)
        ]
        clusters = cluster_failures(failures)
        assert len(clusters[0].representative_queries) == 5

    def test_severity_assigned(self) -> None:
        failures = [_make_failure(cluster="safety_violation")]
        clusters = cluster_failures(failures)
        assert clusters[0].severity == "critical"

    def test_remediation_target_assigned(self) -> None:
        failures = [_make_failure(cluster="extraction_error")]
        clusters = cluster_failures(failures)
        assert "parameter_extractor" in clusters[0].remediation_target


# ---------------------------------------------------------------------------
# Delta comparison
# ---------------------------------------------------------------------------


class TestCompareRuns:
    def test_improvement_detected(self) -> None:
        before = _make_summary("run-1", [_make_metric("relevance", 0.70)])
        after = _make_summary("run-2", [_make_metric("relevance", 0.85)])
        delta = compare_runs(before, after, dataset_version="v1")
        assert delta.overall_improved
        assert len(delta.regressions) == 0
        assert delta.deltas[0].improved
        assert delta.deltas[0].delta > 0

    def test_regression_detected(self) -> None:
        before = _make_summary("run-1", [_make_metric("relevance", 0.85)])
        after = _make_summary("run-2", [_make_metric("relevance", 0.70)])
        delta = compare_runs(before, after, dataset_version="v1")
        assert not delta.overall_improved
        assert "relevance" in delta.regressions

    def test_no_change(self) -> None:
        before = _make_summary("run-1", [_make_metric("relevance", 0.80)])
        after = _make_summary("run-2", [_make_metric("relevance", 0.80)])
        delta = compare_runs(before, after, dataset_version="v1")
        assert not delta.deltas[0].statistically_significant
        assert len(delta.regressions) == 0

    def test_multiple_metrics(self) -> None:
        before = _make_summary(
            "run-1",
            [_make_metric("relevance", 0.70), _make_metric("sql_safety", 0.95)],
        )
        after = _make_summary(
            "run-2",
            [_make_metric("relevance", 0.85), _make_metric("sql_safety", 0.90)],
        )
        delta = compare_runs(before, after, dataset_version="v1")
        assert len(delta.deltas) == 2
        # relevance improved, sql_safety regressed
        assert "sql_safety" in delta.regressions

    def test_new_metric_in_after(self) -> None:
        before = _make_summary("run-1", [_make_metric("relevance", 0.80)])
        after = _make_summary(
            "run-2",
            [_make_metric("relevance", 0.80), _make_metric("new_metric", 0.90)],
        )
        delta = compare_runs(before, after, dataset_version="v1")
        assert len(delta.deltas) == 2
