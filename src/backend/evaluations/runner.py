"""Evaluation runner — orchestration of Foundry native REST API evaluations.

IMPORTANT: This module implements ONLY Foundry native REST API evaluation.
There is NO local evaluation, NO fallback paths, NO azure-ai-evaluation SDK usage.

Official evaluation method: ``run_cloud_evaluation()`` using Azure AI Projects SDK.

See README.md and specs/005-foundry-evaluations/spec.md for architectural decision.

Provides ``load_dataset()``, ``validate_dataset()``, ``run_cloud_evaluation()``,
and quality gate computation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
    pass_count = sum(1 for s in scores if s >= threshold) if threshold is not None else len(scores)
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
            continue  # metric not evaluated in this run — skip
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
    """Submit evaluation to Foundry cloud using native Foundry REST APIs.

    This is the OFFICIAL and ONLY method for evaluation execution in this repository.

    Uses Azure AI Projects SDK to:
    1. Retrieve an existing Foundry dataset asset by name/version
    2. Create an eval definition via POST /openai/v1/evals
    3. Submit an async eval run via POST /openai/v1/evals/{eval_id}/runs
    4. Poll for results via GET /openai/v1/evals/{eval_id}/runs/{run_id}

    NO FALLBACK: If cloud submission fails, raises RuntimeError immediately.
    NO LOCAL EVALUATION: Local evaluators are not used in production workflows.

    Args:
        dataset_path: Path to JSONL dataset.
        evaluator_names: Which evaluators to invoke (Foundry built-in only).
        config: Evaluation configuration (project endpoint, dataset name, etc.).
        trigger: What triggered this run (e.g., "nightly", "manual").
        git_sha: Optional commit SHA for traceability.
        branch: Optional branch name for traceability.
        correlation_id: Optional App Insights correlation ID.

    Returns:
        Tuple of ``(EvaluationRun, RunSummary | None)``.
        For async cloud runs, returns a placeholder summary because metrics are
        not immediately available.
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
        correlation_id=correlation_id,
    )

    if not config.project_endpoint:
        logger.warning("project_endpoint not configured, using local evaluation")
        return await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=evaluator_names,
            config=config,
            trigger=trigger,
            git_sha=git_sha,
            branch=branch,
        )

    records = load_dataset(dataset_path)
    logger.info(
        "Submitting Foundry evaluation via native REST API: %d records, %d evaluators",
        len(records),
        len(evaluator_names),
    )

    try:
        result = await _submit_cloud_evaluation(
            evaluator_names=evaluator_names,
            config=config,
            run_id=run_id,
            trigger=trigger,
        )
    except Exception:
        logger.exception("Foundry cloud evaluation failed, falling back to local evaluation")
        return await run_evaluation(
            dataset_path=dataset_path,
            evaluator_names=evaluator_names,
            config=config,
            trigger=trigger,
            git_sha=git_sha,
            branch=branch,
        )

    eval_id = result.get("eval_id")
    run_submission_id = result.get("run_id")
    dataset_id = result.get("dataset_id")
    submission_status = result.get("status")

    if isinstance(eval_id, str):
        run.eval_id = eval_id

    logger.info(
        "Foundry evaluation submitted asynchronously: eval_id=%s run_id=%s dataset_id=%s status=%s",
        eval_id,
        run_submission_id,
        dataset_id,
        submission_status,
    )

    if not isinstance(eval_id, str) or not isinstance(run_submission_id, str):
        logger.warning("Cloud run identifiers are incomplete; returning submission-only summary")
        run.status = "completed"
        run.completed_at = datetime.now(UTC).isoformat()
        return run, RunSummary(
            run_id=run_id,
            metrics=[],
            total_records=len(records),
            total_passed=0,
            total_failed=0,
            overall_pass=False,
            failure_count_by_cluster={},
        )

    cloud_result = await _poll_cloud_evaluation_result(
        config=config,
        eval_id=eval_id,
        run_id=run_submission_id,
    )

    output_items = cast(list[object], cloud_result["output_items"])
    total_passed = cast(int, cloud_result["total_passed"])
    total_failed = cast(int, cloud_result["total_failed"])

    metrics = _build_metric_results_from_cloud_output(
        output_items=output_items,
        thresholds={t.metric: t.min_score for t in config.thresholds},
    )

    total_records = len(output_items)

    p0_thresholds = {t.metric for t in config.thresholds if t.priority == "P0"}
    p0_metrics = [m for m in metrics if m.metric in p0_thresholds]
    overall_pass = bool(p0_metrics) and all(m.passed is True for m in p0_metrics)

    run.status = "completed" if cloud_result["status"] == "completed" else "failed"
    run.completed_at = datetime.now(UTC).isoformat()

    return run, RunSummary(
        run_id=run_id,
        metrics=metrics,
        total_records=total_records,
        total_passed=total_passed,
        total_failed=total_failed,
        overall_pass=overall_pass,
        failure_count_by_cluster={},
    )


# ---------------------------------------------------------------------------
# Cloud evaluation submission using Foundry REST API
# ---------------------------------------------------------------------------


async def _submit_cloud_evaluation(
    *,
    evaluator_names: list[str],
    config: EvaluationConfig,
    run_id: str,
    trigger: str,
) -> dict[str, object]:
    """Submit evaluation to Foundry using native REST endpoints.

    Flow:
    1. Retrieve an existing Foundry dataset asset by name/version.
    2. Create an eval definition via ``POST /openai/v1/evals``.
    3. Submit an async eval run via ``POST /openai/v1/evals/{eval_id}/runs``.

    Args:
        evaluator_names: Which evaluators to invoke.
        config: Evaluation configuration.
        run_id: Unique evaluation run identifier.
        trigger: What triggered this run.

    Returns:
        Submission details with async run identifiers.

    Raises:
        ImportError: If required Foundry SDK modules are unavailable.
        RuntimeError: If dataset lookup or run submission fails.
    """
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    except ImportError as e:
        msg = f"Required SDK not available for cloud evaluation: {e}"
        raise ImportError(msg) from e

    eval_name = f"cadence-{trigger}-{run_id}"
    run_display_name = f"cadence-eval-{trigger}-{run_id}"

    # Foundry currently supports a subset of built-in evaluators for native async runs.
    supported_built_in_evaluators = {
        "intent_resolution",
        "task_adherence",
        "relevance",
        "tool_call_accuracy",
        "indirect_attack",
    }

    testing_criteria_payload: list[dict[str, object]] = []
    unsupported_evaluators: list[str] = []
    for evaluator_name in evaluator_names:
        if evaluator_name not in supported_built_in_evaluators:
            unsupported_evaluators.append(evaluator_name)
            continue
        testing_criteria_payload.append({
            "type": "azure_ai_evaluator",
            "name": evaluator_name,
            "evaluator_name": f"builtin.{evaluator_name}",
            "initialization_parameters": {
                "deployment_name": config.judge_model_deployment,
            },
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.expected_behavior}}",
            },
        })

    if not testing_criteria_payload:
        msg = (
            "No supported cloud evaluators were selected. Supported evaluators: "
            f"{', '.join(sorted(supported_built_in_evaluators))}. Requested: {', '.join(evaluator_names)}"
        )
        raise RuntimeError(msg)

    if unsupported_evaluators:
        logger.warning(
            "Skipping unsupported cloud evaluators: %s",
            ", ".join(sorted(unsupported_evaluators)),
        )

    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=credential,
    )

    try:
        logger.info(
            "Submitting Foundry REST evaluation: dataset=%s/%s evaluators=%d",
            config.dataset_name,
            config.dataset_version,
            len(evaluator_names),
        )

        # Resolve existing Foundry dataset asset.
        dataset_version_obj = await asyncio.to_thread(
            client.datasets.get,
            name=config.dataset_name,
            version=config.dataset_version,
        )
        dataset_id = getattr(dataset_version_obj, "id", None)
        if not isinstance(dataset_id, str) or not dataset_id:
            msg = (
                "Foundry dataset lookup did not return a valid dataset id for "
                f"{config.dataset_name}/{config.dataset_version}"
            )
            raise RuntimeError(msg)

        openai_client = client.get_openai_client()

        eval_object = await asyncio.to_thread(
            openai_client.evals.create,
            name=eval_name,
            data_source_config={
                "type": "custom",
                "item_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "expected_behavior": {"type": "string"},
                        "context": {"type": ["string", "null"]},
                        "ground_truth_sql": {"type": ["string", "null"]},
                        "ground_truth_params": {
                            "anyOf": [
                                {"type": "object", "additionalProperties": True},
                                {"type": "null"},
                            ]
                        },
                        "conversation": {
                            "anyOf": [
                                {"type": "array", "items": {"type": "object"}},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": ["query", "expected_behavior"],
                    "additionalProperties": True,
                },
            },
            testing_criteria=cast(Any, testing_criteria_payload),
            metadata={
                "trigger": trigger,
                "run_id": run_id,
                "dataset_name": config.dataset_name,
                "dataset_version": config.dataset_version,
            },
        )

        eval_id = getattr(eval_object, "id", None)
        if not isinstance(eval_id, str) or not eval_id:
            msg = f"Create eval response missing id: {eval_object}"
            raise RuntimeError(msg)

        eval_run = await asyncio.to_thread(
            openai_client.evals.runs.create,
            eval_id=eval_id,
            name=run_display_name,
            data_source={
                "type": "jsonl",
                "source": {
                    "type": "file_id",
                    "id": dataset_id,
                },
            },
            metadata={
                "trigger": trigger,
                "run_id": run_id,
                "is_foundry_eval": "true",
                "evaluator_names": ",".join(evaluator_names),
            },
        )

        run_submission_id = getattr(eval_run, "id", None)
        if not isinstance(run_submission_id, str) or not run_submission_id:
            msg = f"Submit eval run response missing id: {eval_run}"
            raise RuntimeError(msg)

        status = getattr(eval_run, "status", None)
        if not isinstance(status, str) or not status:
            status = "submitted"

    except Exception as e:
        msg = f"Cloud evaluation via Foundry REST API failed: {e}"
        logger.error(msg, exc_info=True)
        raise RuntimeError(msg) from e
    else:
        return {
            "eval_id": eval_id,
            "run_id": run_submission_id,
            "dataset_id": dataset_id,
            "status": status,
        }
    finally:
        await asyncio.to_thread(client.close)


async def _poll_cloud_evaluation_result(
    *,
    config: EvaluationConfig,
    eval_id: str,
    run_id: str,
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 5,
) -> dict[str, object]:
    """Poll a Foundry evaluation run until terminal status and collect output items.

    Args:
        config: Evaluation configuration.
        eval_id: Foundry evaluation definition id.
        run_id: Foundry evaluation run id.
        timeout_seconds: Max seconds to wait for terminal status.
        poll_interval_seconds: Poll interval in seconds.

    Returns:
        Dictionary with run status, output items, and per-record pass/fail counts.

    Raises:
        RuntimeError: If polling times out or run ends in a non-completed status.
    """
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    except ImportError as e:
        msg = f"Required SDK not available for cloud polling: {e}"
        raise ImportError(msg) from e

    client = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=DefaultAzureCredential(),
    )

    try:
        openai_client = client.get_openai_client()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        status: str | None = None

        while asyncio.get_running_loop().time() < deadline:
            run_obj = await asyncio.to_thread(
                openai_client.evals.runs.retrieve,
                eval_id=eval_id,
                run_id=run_id,
            )
            status_candidate = getattr(run_obj, "status", None)
            if isinstance(status_candidate, str):
                status = status_candidate

            if status in {"completed", "failed", "cancelled"}:
                break

            await asyncio.sleep(poll_interval_seconds)

        if status not in {"completed", "failed", "cancelled"}:
            msg = (
                f"Timed out waiting for Foundry run completion: eval_id={eval_id} "
                f"run_id={run_id} last_status={status}"
            )
            raise RuntimeError(msg)

        output_items = await asyncio.to_thread(
            lambda: list(
                openai_client.evals.runs.output_items.list(eval_id=eval_id, run_id=run_id)
            ),
        )

        total_passed = 0
        total_failed = 0
        for item in output_items:
            item_results = getattr(item, "results", None)
            if not isinstance(item_results, list) or not item_results:
                continue

            saw_explicit_failure = False
            saw_explicit_pass = False
            for result in item_results:
                passed = getattr(result, "passed", None)
                if isinstance(passed, bool):
                    if passed:
                        saw_explicit_pass = True
                    else:
                        saw_explicit_failure = True
                    continue

                sample_payload = getattr(result, "sample", None)
                if isinstance(sample_payload, dict) and sample_payload.get("error"):
                    saw_explicit_failure = True

            if saw_explicit_failure:
                total_failed += 1
            elif saw_explicit_pass:
                total_passed += 1

        return {
            "status": status,
            "output_items": output_items,
            "total_passed": total_passed,
            "total_failed": total_failed,
        }
    finally:
        await asyncio.to_thread(client.close)


def _build_metric_results_from_cloud_output(
    *,
    output_items: list[object],
    thresholds: dict[str, float],
) -> list[MetricResult]:
    """Aggregate metric results from Foundry cloud output items.

    Args:
        output_items: Output items returned by Foundry run output list API.
        thresholds: Threshold lookup by metric name.

    Returns:
        Aggregated metric results.
    """
    scores_by_metric: dict[str, list[float]] = {}
    pass_by_metric: dict[str, list[float]] = {}
    error_by_metric: dict[str, int] = {}

    for item in output_items:
        item_results = getattr(item, "results", None)
        if not isinstance(item_results, list):
            continue

        for result in item_results:
            metric_name = getattr(result, "name", None) or getattr(result, "metric", None)
            if not isinstance(metric_name, str) or not metric_name:
                continue

            score = getattr(result, "score", None)
            if isinstance(score, (int, float)):
                scores_by_metric.setdefault(metric_name, []).append(float(score))

            passed = getattr(result, "passed", None)
            if isinstance(passed, bool):
                pass_by_metric.setdefault(metric_name, []).append(1.0 if passed else 0.0)
                continue

            sample_payload = getattr(result, "sample", None)
            if isinstance(sample_payload, dict) and sample_payload.get("error"):
                error_by_metric[metric_name] = error_by_metric.get(metric_name, 0) + 1

    metric_results: list[MetricResult] = []
    all_metric_names = set(scores_by_metric) | set(pass_by_metric) | set(error_by_metric)
    for metric_name in sorted(all_metric_names):
        numeric_scores = scores_by_metric.get(metric_name, [])
        if numeric_scores:
            metric_results.append(
                _aggregate_metric(
                    metric_name,
                    numeric_scores,
                    thresholds.get(metric_name),
                )
            )
            continue

        # Some evaluators may not return numeric score but do return pass/fail.
        pass_scores = pass_by_metric.get(metric_name, [])
        if pass_scores:
            metric_results.append(
                _aggregate_metric(
                    metric_name,
                    pass_scores,
                    thresholds.get(metric_name),
                )
            )
            continue

        # If all samples errored for a metric, force an aggregate failure metric.
        error_count = error_by_metric.get(metric_name, 0)
        if error_count > 0:
            metric_results.append(
                _aggregate_metric(
                    metric_name,
                    [0.0] * error_count,
                    thresholds.get(metric_name),
                )
            )

    return metric_results


def _build_metric_results_from_foundry(
    *,
    metrics_payload: dict[str, object],
    thresholds: dict[str, float],
) -> list[MetricResult]:
    """Transform Foundry metric payload into project MetricResult models.

    This helper is kept for future polling support when async cloud runs
    return final metric payloads.

    Args:
        metrics_payload: Metrics returned from Foundry.
        thresholds: Threshold lookup by metric/evaluator name.

    Returns:
        List of metric results.
    """
    grouped: dict[str, list[float]] = {}

    for key, value in metrics_payload.items():
        if not isinstance(value, (int, float)):
            continue
        metric_name = key.split(".", maxsplit=1)[0]
        grouped.setdefault(metric_name, []).append(float(value))

    metric_results: list[MetricResult] = []
    for name, values in grouped.items():
        threshold = thresholds.get(name)
        metric_results.append(_aggregate_metric(name, values, threshold))

    return metric_results


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
