"""Pydantic models for evaluation datasets, runs, results, and analysis.

All evaluation data structures are defined here. Models follow the
contracts in ``specs/005-foundry-evaluations/contracts/``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Dataset models (T006)
# ---------------------------------------------------------------------------


class DatasetRecord(BaseModel):
    """Single row in an evaluation dataset (JSONL format)."""

    query: str
    expected_behavior: str
    context: str | None = None
    ground_truth_sql: str | None = None
    ground_truth_params: dict[str, Any] | None = None
    scenario_class: Literal["template", "dynamic", "clarification", "what_if", "conversation"]
    conversation: list[dict[str, Any]] | None = None


class DatasetMetadata(BaseModel):
    """Metadata for a versioned dataset."""

    name: str
    version: str
    stage: Literal["seed", "traces", "curated", "prod"]
    source: Literal["gold", "trace_harvested"]
    record_count: int
    scenario_distribution: dict[str, int]
    created_at: str
    sanitization_status: Literal["passed", "failed", "pending"]
    dataset_uri: str | None = None


# ---------------------------------------------------------------------------
# Evaluation run models (T007)
# ---------------------------------------------------------------------------


class EvaluationRun(BaseModel):
    """Record of a single evaluation run."""

    run_id: str
    eval_id: str | None = None
    dataset_name: str
    dataset_version: str
    evaluator_names: list[str]
    config_version: str
    trigger: Literal["ci_pr", "nightly", "manual"]
    status: Literal["running", "completed", "failed", "cancelled"]
    started_at: str
    completed_at: str | None = None
    git_sha: str | None = None
    branch: str | None = None
    correlation_id: str | None = None


class MetricResult(BaseModel):
    """Result for a single metric across all dataset records."""

    metric: str
    mean_score: float
    median_score: float
    p5_score: float
    p95_score: float
    pass_rate: float
    sample_count: int
    threshold: float | None = None
    passed: bool | None = None


class RunSummary(BaseModel):
    """Aggregated results from a completed evaluation run."""

    run_id: str
    metrics: list[MetricResult]
    total_records: int
    total_passed: int
    total_failed: int
    overall_pass: bool
    failure_count_by_cluster: dict[str, int]


# ---------------------------------------------------------------------------
# Failure and analysis models (T008)
# ---------------------------------------------------------------------------


class FailureRecord(BaseModel):
    """Single failed evaluation record with diagnostic context."""

    record_index: int
    query: str
    response: str | None = None
    expected_behavior: str | None = None
    failing_metrics: dict[str, float]
    cluster: Literal[
        "intent_misroute",
        "extraction_error",
        "validator_rejection",
        "poor_answer_quality",
        "safety_violation",
        "tool_call_failure",
    ]
    diagnostic_context: str | None = None


class FailureCluster(BaseModel):
    """Grouped failures for remediation planning."""

    cluster_type: str
    count: int
    percentage: float
    representative_queries: list[str]
    remediation_target: str
    severity: Literal["critical", "high", "medium", "low"]


class MetricDelta(BaseModel):
    """Change in a single metric between two runs."""

    metric: str
    before_score: float
    after_score: float
    delta: float
    improved: bool
    statistically_significant: bool


class DeltaComparison(BaseModel):
    """Comparison between two evaluation runs."""

    before_run_id: str
    after_run_id: str
    dataset_version: str
    deltas: list[MetricDelta]
    overall_improved: bool
    regressions: list[str]


class QualityGateDecision(BaseModel):
    """CI quality gate decision."""

    run_id: str
    gate_result: Literal["pass", "fail", "error"]
    p0_results: list[MetricResult]
    failing_metrics: list[str]
    waivers: list[str]
    decided_at: str
    git_sha: str
    branch: str
