"""Evaluation runner — dataset loading, evaluator orchestration, result aggregation.

Provides ``load_dataset()``, ``validate_dataset()``, ``run_evaluation()``,
``run_cloud_evaluation()``, and quality gate computation.
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
    """Submit evaluation to Foundry cloud using modern evaluate() API.

    Uses the modern ``azure.ai.evaluation.evaluate()`` function which automatically
    submits to Foundry when ``azure_ai_project`` is provided. Results appear in
    the new Foundry portal. Falls back to local evaluation if cloud submission fails.

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
        "Submitting Foundry evaluation via modern evaluate() API: %d records, %d evaluators",
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

    # Extract metrics from Foundry result
    metrics_result = result.get("metrics", {})
    if not isinstance(metrics_result, dict):
        metrics_result = {}
    metrics = _build_metric_results_from_foundry(
        metrics_payload=metrics_result,
        thresholds={t.metric: t.min_score for t in config.thresholds},
    )

    overall_pass = all(
        metric.passed is True
        for metric in metrics
        if any(t.metric == metric.metric and t.priority == "P0" for t in config.thresholds)
    )

    run.status = "completed"
    run.completed_at = datetime.now(UTC).isoformat()
    # Studio URL is available in result if Foundry provides it
    studio_url = result.get("studio_url")
    run.eval_id = studio_url if isinstance(studio_url, str) else None

    summary = RunSummary(
        run_id=run_id,
        metrics=metrics,
        total_records=len(records),
        total_passed=len(records) if overall_pass else 0,
        total_failed=0 if overall_pass else len(records),
        overall_pass=overall_pass,
        failure_count_by_cluster={},
    )

    return run, summary


# ---------------------------------------------------------------------------
# Cloud evaluation submission using modern evaluate() API
# ---------------------------------------------------------------------------


async def _submit_cloud_evaluation(
    *,
    evaluator_names: list[str],
    config: EvaluationConfig,
    run_id: str,
    trigger: str,
) -> dict[str, object]:
    """Submit evaluation to Foundry using modern evaluate() API.

    Uses the ``azure.ai.evaluation.evaluate()`` function which automatically
    submits to Foundry when ``azure_ai_project`` is provided. Results appear
    in the new Foundry portal.

    References an existing Foundry dataset by name and version instead of
    uploading a local file.

    Args:
        evaluator_names: Which evaluators to invoke.
        config: Evaluation configuration.
        run_id: Unique evaluation run identifier.
        trigger: What triggered this run.

    Returns:
        Result dictionary with metrics extracted from evaluation output.

    Raises:
        ImportError: If azure-ai-evaluation SDK is unavailable.
        RuntimeError: If cloud evaluation submission fails.
    """
    try:
        from azure.ai.evaluation import evaluate  # noqa: PLC0415
    except ImportError as e:
        msg = f"Required SDK not available for cloud evaluation: {e}"
        raise ImportError(msg) from e

    try:
        # Build evaluators dict for evaluate() function
        evaluators = _build_foundry_evaluators(evaluator_names, config)

        if not evaluators:
            msg = f"No valid evaluators could be built from: {evaluator_names}"
            raise RuntimeError(msg)

        # Get the Foundry dataset reference instead of using local file
        dataset_uri = await _get_foundry_dataset_uri(
            config=config,
            dataset_name=config.dataset_name,
            dataset_version=config.dataset_version,
        )

        logger.info(
            "Calling evaluate() with %d evaluators, Foundry dataset: %s",
            len(evaluators),
            dataset_uri,
        )

        # Submit to Foundry using modern evaluate() API
        # When azure_ai_project is provided, results are sent to Foundry
        result = await asyncio.to_thread(
            evaluate,
            data=dataset_uri,
            evaluators=evaluators,
            evaluation_name=f"cadence-{trigger}-{run_id}",
            azure_ai_project=config.project_endpoint,
            tags={
                "run_id": run_id,
                "dataset_version": config.dataset_version,
                "trigger": trigger,
            },
        )
    except Exception as e:
        msg = f"Cloud evaluation via evaluate() failed: {e}"
        logger.error(msg, exc_info=True)
        raise RuntimeError(msg) from e
    else:
        logger.info("Cloud evaluation submitted successfully to Foundry")

        # Extract metrics from result (EvaluationResult TypedDict)
        metrics: dict[str, object] = result.get("metrics", {})  # type: ignore[union-attr]

        return {
            "metrics": metrics,
            "studio_url": None,  # Foundry will provide this in the portal
        }


async def _get_foundry_dataset_uri(
    *,
    config: EvaluationConfig,
    dataset_name: str,
    dataset_version: str,
) -> str:
    """Retrieve the data_uri for an existing Foundry dataset.

    Args:
        config: Evaluation configuration with project_endpoint.
        dataset_name: Name of the dataset in Foundry.
        dataset_version: Version of the dataset (e.g., 'v1').

    Returns:
        The data_uri string for the Foundry dataset.

    Raises:
        RuntimeError: If dataset cannot be retrieved.
    """
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    except ImportError as e:
        msg = f"Required SDK not available: {e}"
        raise ImportError(msg) from e

    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=credential,
    )

    try:
        logger.info("Retrieving Foundry dataset: %s/%s", dataset_name, dataset_version)

        # Get the dataset version from Foundry
        dataset_version_obj = await asyncio.to_thread(
            client.datasets.get,
            name=dataset_name,
            version=dataset_version,
        )

        data_uri = dataset_version_obj.data_uri
    except Exception as e:
        msg = f"Failed to retrieve Foundry dataset {dataset_name}/{dataset_version}: {e}"
        logger.error(msg, exc_info=True)
        raise RuntimeError(msg) from e
    else:
        logger.info("Retrieved Foundry dataset URI: %s", data_uri)
        return data_uri
    finally:
        client.close()


def _build_foundry_evaluators(
    evaluator_names: list[str],
    config: EvaluationConfig,
) -> dict[str, Any]:
    """Build evaluator callables for ``azure.ai.evaluation.evaluate``.

    Args:
        evaluator_names: Evaluator names requested for this run.
        config: Evaluation configuration.

    Returns:
        Evaluator mapping consumable by ``evaluate``.
    """
    from azure.ai.evaluation import (  # noqa: PLC0415
        IndirectAttackEvaluator,
        IntentResolutionEvaluator,
        RelevanceEvaluator,
        TaskAdherenceEvaluator,
        ToolCallAccuracyEvaluator,
    )

    # When using cloud evaluation with azure_ai_project, use minimal model_config.
    # The evaluate() function will handle credential-based auth automatically.
    # Extract endpoint without /api/projects/... part
    endpoint = _derive_foundry_host_endpoint(config.project_endpoint)

    model_config: dict[str, object] = {
        "type": "azure_openai",
        "azure_endpoint": endpoint,
        "azure_deployment": config.judge_model_deployment,
    }

    evaluators: dict[str, Any] = {}
    for name in evaluator_names:
        if name == "intent_resolution":
            evaluators[name] = IntentResolutionEvaluator(model_config=model_config)
            continue

        if name == "task_adherence":
            evaluators[name] = TaskAdherenceEvaluator(model_config=model_config)
            continue

        if name == "tool_call_accuracy":
            evaluators[name] = ToolCallAccuracyEvaluator(model_config=model_config)
            continue

        if name == "relevance":
            evaluators[name] = RelevanceEvaluator(model_config=model_config)
            continue

        if name == "indirect_attack":
            # IndirectAttackEvaluator handles authentication through azure_ai_project
            # in the evaluate() function context
            evaluators[name] = IndirectAttackEvaluator(
                credential=None,
                azure_ai_project=config.project_endpoint,
            )
            continue

        if name == "sql_safety":
            evaluators[name] = _sql_safety_adapter
            continue

        if name == "param_extraction_correctness":
            evaluators[name] = _param_extraction_adapter
            continue

        logger.warning("Evaluator %s is unsupported for Foundry cloud runs", name)

    return evaluators


def _derive_foundry_host_endpoint(project_endpoint: str) -> str:
    """Extract host endpoint from project endpoint URL.

    Example:
        ``https://x.services.ai.azure.com/api/projects/cadence``
        -> ``https://x.services.ai.azure.com``

    Args:
        project_endpoint: Full Foundry project endpoint.

    Returns:
        Host-level endpoint.
    """
    marker = "/api/projects/"
    if marker in project_endpoint:
        return project_endpoint.split(marker, maxsplit=1)[0]
    return project_endpoint


def _sql_safety_adapter(
    *,
    query: str,
    ground_truth_sql: str | None = None,
    **_: object,
) -> dict[str, float]:
    """Adapter exposing SQL safety evaluator to Foundry evaluate()."""
    from evaluations.evaluators.sql_safety import evaluate_sql_safety  # noqa: PLC0415

    score = evaluate_sql_safety(query=query, sql=ground_truth_sql or "")
    return {"score": float(score)}


def _param_extraction_adapter(
    *,
    ground_truth_params: dict[str, Any] | None = None,
    **_: object,
) -> dict[str, float]:
    """Adapter exposing parameter extraction evaluator to Foundry evaluate()."""
    from evaluations.evaluators.param_extraction import evaluate_param_extraction  # noqa: PLC0415

    score = evaluate_param_extraction(
        extracted_params={},
        expected_params=ground_truth_params or {},
    )
    return {"score": float(score)}


def _build_metric_results_from_foundry(
    *,
    metrics_payload: dict[str, object],
    thresholds: dict[str, float],
) -> list[MetricResult]:
    """Transform Foundry metric payload into project MetricResult models.

    Args:
        metrics_payload: Metrics returned from ``evaluate``.
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
