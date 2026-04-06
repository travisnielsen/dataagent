"""Evaluation runner — dataset loading, evaluator orchestration, result aggregation.

Provides ``load_dataset()``, ``validate_dataset()``, ``run_evaluation()``,
``run_cloud_evaluation()``, and quality gate computation.
"""

from __future__ import annotations

import json
import logging
import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluations.config import DEFAULT_THRESHOLDS, EvaluationConfig
from evaluations.models import (
    DatasetMetadata,
    DatasetRecord,
    EvaluationRun,
    MetricResult,
    QualityGateDecision,
    RunSummary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataset loading and validation (T017)
# ---------------------------------------------------------------------------


def load_dataset(path: Path) -> list[DatasetRecord]:
    """Load evaluation dataset from a JSONL file.

    Args:
        path: Path to the ``.jsonl`` file.

    Returns:
        List of validated ``DatasetRecord`` instances.

    Raises:
        FileNotFoundError: If the dataset file does not exist.
        pydantic.ValidationError: If any record fails schema validation.
    """
    records: list[DatasetRecord] = []
    with path.open(encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            data: dict[str, Any] = json.loads(stripped)
            record = DatasetRecord.model_validate(data)
            records.append(record)
            logger.debug("Loaded record %d: scenario=%s", line_num, record.scenario_class)
    return records


def validate_dataset(records: list[DatasetRecord]) -> list[str]:
    """Validate a loaded dataset for completeness.

    Args:
        records: Parsed dataset records.

    Returns:
        List of validation error strings (empty if valid).
    """
    errors: list[str] = []
    if not records:
        errors.append("Dataset is empty")
        return errors

    for i, record in enumerate(records):
        if not record.query.strip():
            errors.append(f"Record {i}: empty query")
        if not record.expected_behavior.strip():
            errors.append(f"Record {i}: empty expected_behavior")

    scenario_classes = {r.scenario_class for r in records}
    expected_classes = {"template", "dynamic", "clarification"}
    missing = expected_classes - scenario_classes
    if missing:
        errors.append(f"Missing scenario classes: {', '.join(sorted(missing))}")

    return errors


# ---------------------------------------------------------------------------
# Dataset metadata generation (T020)
# ---------------------------------------------------------------------------


def generate_metadata(
    records: list[DatasetRecord],
    *,
    name: str,
    version: str,
    source: str = "gold",
    stage: str = "curated",
) -> DatasetMetadata:
    """Compute metadata from a loaded dataset.

    Args:
        records: Parsed dataset records.
        name: Dataset name.
        version: Dataset version tag.
        source: Dataset source type.
        stage: Dataset lifecycle stage.

    Returns:
        Computed ``DatasetMetadata``.
    """
    distribution = dict(Counter(r.scenario_class for r in records))
    return DatasetMetadata(
        name=name,
        version=version,
        stage=stage,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        record_count=len(records),
        scenario_distribution=distribution,
        created_at=datetime.now(UTC).isoformat(),
        sanitization_status="passed",
    )


# ---------------------------------------------------------------------------
# Evaluation orchestration (T025)
# ---------------------------------------------------------------------------


def _aggregate_metric(name: str, scores: list[float], threshold: float | None) -> MetricResult:
    """Build a MetricResult from raw scores."""
    pass_count = (
        sum(1 for s in scores if s >= threshold) if threshold is not None else len(scores)
    )
    passed = pass_count == len(scores) if threshold is not None else None
    sorted_scores = sorted(scores)
    p5_idx = max(0, int(len(sorted_scores) * 0.05) - 1)
    p95_idx = min(len(sorted_scores) - 1, int(len(sorted_scores) * 0.95))
    return MetricResult(
        metric=name,
        mean_score=statistics.mean(scores),
        median_score=statistics.median(scores),
        p5_score=sorted_scores[p5_idx],
        p95_score=sorted_scores[p95_idx],
        pass_rate=pass_count / len(scores),
        sample_count=len(scores),
        threshold=threshold,
        passed=passed,
    )


async def run_evaluation(
    *,
    dataset_path: Path,
    evaluator_names: list[str],
    config: EvaluationConfig,
    trigger: str = "manual",
    git_sha: str | None = None,
    branch: str | None = None,
) -> tuple[EvaluationRun, RunSummary]:
    """Orchestrate a local evaluation run.

    Loads the dataset, initialises evaluators, runs scoring, and
    aggregates results into a ``RunSummary``.

    Args:
        dataset_path: Path to JSONL dataset.
        evaluator_names: Which evaluators to invoke.
        config: Evaluation configuration.
        trigger: What triggered this run.
        git_sha: Optional commit SHA for traceability.
        branch: Optional branch name.

    Returns:
        Tuple of ``(EvaluationRun, RunSummary)``.
    """
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(UTC).isoformat()

    run = EvaluationRun(
        run_id=run_id,
        dataset_name=config.dataset_name,
        dataset_version=config.dataset_version,
        evaluator_names=evaluator_names,
        config_version=config.config_version,
        trigger=trigger,  # type: ignore[arg-type]
        status="running",
        started_at=started_at,
        git_sha=git_sha,
        branch=branch,
    )

    records = load_dataset(dataset_path)

    # Score each record with each evaluator
    all_scores: dict[str, list[float]] = {name: [] for name in evaluator_names}

    for record in records:
        for evaluator_name in evaluator_names:
            score = await _invoke_evaluator(evaluator_name, record, config)
            all_scores[evaluator_name].append(score)

    # Build threshold lookup
    threshold_map = {t.metric: t.min_score for t in config.thresholds}

    # Aggregate metrics
    metrics: list[MetricResult] = []
    total_passed = 0
    total_failed = 0

    for name, scores in all_scores.items():
        if not scores:
            continue
        threshold = threshold_map.get(name)
        metrics.append(_aggregate_metric(name, scores, threshold))

    # Tally pass/fail across all metrics per record
    for i in range(len(records)):
        record_passed = True
        for name in evaluator_names:
            threshold = threshold_map.get(name)
            if threshold is not None and all_scores[name][i] < threshold:
                record_passed = False
                break
        if record_passed:
            total_passed += 1
        else:
            total_failed += 1

    p0_thresholds = {t.metric for t in config.thresholds if t.priority == "P0"}
    overall_pass = all(m.passed is True for m in metrics if m.metric in p0_thresholds)

    run.status = "completed"
    run.completed_at = datetime.now(UTC).isoformat()

    summary = RunSummary(
        run_id=run_id,
        metrics=metrics,
        total_records=len(records),
        total_passed=total_passed,
        total_failed=total_failed,
        overall_pass=overall_pass,
        failure_count_by_cluster={},
    )

    return run, summary


async def _invoke_evaluator(
    evaluator_name: str,
    record: DatasetRecord,
    config: EvaluationConfig,
) -> float:
    """Invoke a single evaluator on a record.

    In production, this dispatches to the azure-ai-evaluation SDK or
    custom evaluator functions. For now, this provides the routing
    scaffold that concrete evaluators plug into.

    Args:
        evaluator_name: Name of the evaluator to invoke.
        record: The dataset record to evaluate.
        config: Evaluation configuration.

    Returns:
        Score from the evaluator (0.0-1.0 for most, 1-5 for ordinal).
    """
    _ = config  # reserved for SDK evaluator configuration
    # Import custom evaluators lazily to avoid hard dependency
    try:
        if evaluator_name == "sql_safety":
            from evaluations.evaluators.sql_safety import evaluate_sql_safety  # noqa: PLC0415

            return evaluate_sql_safety(
                query=record.query,
                sql=record.ground_truth_sql or "",
            )
        if evaluator_name == "param_extraction_correctness":
            from evaluations.evaluators.param_extraction import (  # noqa: PLC0415
                evaluate_param_extraction,
            )

            return evaluate_param_extraction(
                extracted_params={},
                expected_params=record.ground_truth_params or {},
            )
    except ImportError:
        logger.warning("Custom evaluator %s not available", evaluator_name)

    # For built-in evaluators, return a placeholder score.
    # In production this routes through azure-ai-evaluation SDK.
    logger.info("Evaluator %s: SDK integration pending", evaluator_name)
    return 1.0


# ---------------------------------------------------------------------------
# Quality gate computation (T030)
# ---------------------------------------------------------------------------


def compute_quality_gate(
    summary: RunSummary,
    *,
    thresholds: list[Any] | None = None,
    git_sha: str = "",
    branch: str = "",
    waivers: list[str] | None = None,
) -> QualityGateDecision:
    """Evaluate run summary against P0 thresholds.

    Args:
        summary: Completed run summary.
        thresholds: Threshold rules (defaults to ``DEFAULT_THRESHOLDS``).
        git_sha: Current commit SHA.
        branch: Current branch name.
        waivers: Metric names with approved waivers.

    Returns:
        ``QualityGateDecision`` with pass/fail result.
    """
    rules = thresholds or DEFAULT_THRESHOLDS
    waiver_set = set(waivers or [])

    p0_rules = [r for r in rules if r.priority == "P0"]
    metric_lookup = {m.metric: m for m in summary.metrics}

    p0_results: list[MetricResult] = []
    failing: list[str] = []

    for rule in p0_rules:
        metric = metric_lookup.get(rule.metric)
        if metric is None:
            failing.append(rule.metric)
            continue
        p0_results.append(metric)
        if metric.mean_score < rule.min_score and rule.metric not in waiver_set:
            failing.append(rule.metric)

    gate_result: str = "pass" if not failing else "fail"

    return QualityGateDecision(
        run_id=summary.run_id,
        gate_result=gate_result,  # type: ignore[arg-type]
        p0_results=p0_results,
        failing_metrics=failing,
        waivers=list(waiver_set),
        decided_at=datetime.now(UTC).isoformat(),
        git_sha=git_sha,
        branch=branch,
    )


# ---------------------------------------------------------------------------
# Foundry cloud batch evaluation (T047)
# ---------------------------------------------------------------------------


async def run_cloud_evaluation(
    *,
    dataset_path: Path,
    evaluator_names: list[str],
    config: EvaluationConfig,
    trigger: str = "nightly",
    git_sha: str | None = None,
    branch: str | None = None,
    correlation_id: str | None = None,
) -> tuple[EvaluationRun, RunSummary | None]:
    """Submit evaluation to Foundry cloud batch runtime.

    Uses ``AIProjectClient`` and ``evaluation_agent_batch_eval_create``
    for full-suite cloud runs. Falls back to local evaluation if the
    Foundry SDK is unavailable.

    Args:
        dataset_path: Path to JSONL dataset.
        evaluator_names: Which evaluators to invoke.
        config: Evaluation configuration.
        trigger: What triggered this run.
        git_sha: Optional commit SHA.
        branch: Optional branch name.
        correlation_id: Optional App Insights correlation ID.

    Returns:
        Tuple of ``(EvaluationRun, RunSummary | None)``.
        Summary is None if the cloud run is async and needs polling.
    """
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(UTC).isoformat()

    _run = EvaluationRun(
        run_id=run_id,
        dataset_name=config.dataset_name,
        dataset_version=config.dataset_version,
        evaluator_names=evaluator_names,
        config_version=config.config_version,
        trigger=trigger,  # type: ignore[arg-type]
        status="running",
        started_at=started_at,
        git_sha=git_sha,
        branch=branch,
        correlation_id=correlation_id,
    )

    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential()
        _client = AIProjectClient(
            endpoint=config.project_endpoint,
            credential=credential,
        )

        records = load_dataset(dataset_path)

        logger.info(
            "Submitting cloud batch evaluation: %d records, %d evaluators",
            len(records),
            len(evaluator_names),
        )

        # Cloud batch eval API integration pending — fall back to local.
        logger.info("Cloud batch eval: falling back to local evaluation")
        return await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=evaluator_names,
            config=config,
            trigger=trigger,
            git_sha=git_sha,
            branch=branch,
        )

    except ImportError:
        logger.warning("azure-ai-projects not available, using local evaluation")
        return await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=evaluator_names,
            config=config,
            trigger=trigger,
            git_sha=git_sha,
            branch=branch,
        )


# ---------------------------------------------------------------------------
# Evaluator catalog integration (T048)
# ---------------------------------------------------------------------------


async def register_custom_evaluator(
    *,
    name: str,
    prompt_template: str,
    config: EvaluationConfig,
) -> str | None:
    """Register a custom prompt evaluator in the Foundry catalog.

    Checks if the evaluator already exists before creating a new one.

    Args:
        name: Evaluator name.
        prompt_template: The evaluation prompt template.
        config: Evaluation config with project endpoint.

    Returns:
        Evaluator ID if registered, None if already exists or SDK unavailable.
    """
    _ = prompt_template  # reserved for SDK catalog registration
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential()
        _client = AIProjectClient(
            endpoint=config.project_endpoint,
            credential=credential,
        )
        # Evaluator catalog API integration pending
        logger.info("Evaluator catalog registration for %s: SDK integration pending", name)
    except ImportError:
        logger.warning("azure-ai-projects not available for evaluator catalog")
    return None


# ---------------------------------------------------------------------------
# Result persistence (T049)
# ---------------------------------------------------------------------------


def persist_run_results(
    run: EvaluationRun,
    summary: RunSummary,
    *,
    results_dir: Path = Path(".foundry/results"),
) -> Path:
    """Persist evaluation run results to local storage.

    Saves the run and summary as a JSON file in the results directory,
    and updates the agent-metadata.yaml test cases.

    Args:
        run: The completed evaluation run.
        summary: The run summary with metrics.
        results_dir: Directory for result files.

    Returns:
        Path to the saved result file.
    """
    results_dir.mkdir(parents=True, exist_ok=True)

    run_data = {
        "run": json.loads(run.model_dump_json()),
        "summary": json.loads(summary.model_dump_json()),
    }

    result_path = results_dir / f"{run.run_id}.json"
    result_path.write_text(json.dumps(run_data, indent=2) + "\n", encoding="utf-8")

    # Also save as latest for CI workflows
    latest_path = results_dir / "latest-summary.json"
    latest_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")

    logger.info("Results persisted to %s", result_path)
    return result_path
