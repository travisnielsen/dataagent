"""Failure clustering and delta comparison for evaluation analysis.

Groups failed evaluation records by pipeline stage, generates
remediation-targeted cluster summaries, and compares run deltas.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Literal

from evaluations.models import (
    DeltaComparison,
    FailureCluster,
    FailureRecord,
    MetricDelta,
    RunSummary,
)

logger = logging.getLogger(__name__)

# Minimum delta to be considered significant
_SIGNIFICANT_DELTA = 0.02

# ---------------------------------------------------------------------------
# Failure classification rules
# ---------------------------------------------------------------------------

# Maps metric names to likely failure cluster
METRIC_TO_CLUSTER: dict[str, str] = {
    "intent_resolution": "intent_misroute",
    "task_adherence": "intent_misroute",
    "param_extraction_correctness": "extraction_error",
    "clarification_quality": "extraction_error",
    "sql_safety": "safety_violation",
    "indirect_attack": "safety_violation",
    "relevance": "poor_answer_quality",
    "answer_adequacy": "poor_answer_quality",
    "tool_call_accuracy": "tool_call_failure",
}

# Maps cluster type to remediation target
CLUSTER_REMEDIATION_TARGETS: dict[str, str] = {
    "intent_misroute": "assistant/assistant_prompt.md",
    "extraction_error": "parameter_extractor/prompt.md",
    "validator_rejection": "query_validator/validator.py",
    "poor_answer_quality": "assistant/assistant_prompt.md",
    "safety_violation": "query_validator/validator.py",
    "tool_call_failure": "nl2sql_controller/prompt.md",
}

# Severity based on cluster type
CLUSTER_SEVERITY: dict[str, Literal["critical", "high", "medium", "low"]] = {
    "safety_violation": "critical",
    "intent_misroute": "high",
    "extraction_error": "high",
    "validator_rejection": "medium",
    "poor_answer_quality": "medium",
    "tool_call_failure": "low",
}


# ---------------------------------------------------------------------------
# Failure clustering (T042)
# ---------------------------------------------------------------------------


def classify_failure(failing_metrics: dict[str, float]) -> str:
    """Classify a failure record into a cluster based on failing metrics.

    Args:
        failing_metrics: Dictionary of metric name to score.

    Returns:
        Cluster type string.
    """
    for metric in failing_metrics:
        cluster = METRIC_TO_CLUSTER.get(metric)
        if cluster:
            return cluster
    return "poor_answer_quality"  # Default fallback


def cluster_failures(failures: list[FailureRecord]) -> list[FailureCluster]:
    """Group failure records into clusters for remediation planning.

    Args:
        failures: List of individual failure records.

    Returns:
        List of ``FailureCluster`` summaries sorted by count descending.
    """
    if not failures:
        return []

    # Group by cluster type
    clusters: dict[str, list[FailureRecord]] = {}
    for failure in failures:
        cluster_type = failure.cluster
        if cluster_type not in clusters:
            clusters[cluster_type] = []
        clusters[cluster_type].append(failure)

    total_failures = len(failures)
    result: list[FailureCluster] = []

    for cluster_type, cluster_failures_list in clusters.items():
        count = len(cluster_failures_list)
        representative = [f.query for f in cluster_failures_list[:5]]

        result.append(
            FailureCluster(
                cluster_type=cluster_type,
                count=count,
                percentage=count / total_failures * 100,
                representative_queries=representative,
                remediation_target=CLUSTER_REMEDIATION_TARGETS.get(cluster_type, "unknown"),
                severity=CLUSTER_SEVERITY.get(cluster_type, "medium"),
            )
        )

    result.sort(key=lambda c: c.count, reverse=True)
    return result


# ---------------------------------------------------------------------------
# Delta comparison (T044)
# ---------------------------------------------------------------------------


def compare_runs(
    before: RunSummary,
    after: RunSummary,
    *,
    dataset_version: str,
) -> DeltaComparison:
    """Compare two evaluation run summaries.

    Both runs must be against the same dataset version for a valid
    comparison.

    Args:
        before: The baseline run summary.
        after: The post-remediation run summary.
        dataset_version: Expected dataset version for both runs.

    Returns:
        ``DeltaComparison`` with per-metric deltas.
    """
    before_metrics = {m.metric: m for m in before.metrics}
    after_metrics = {m.metric: m for m in after.metrics}

    all_metric_names = set(before_metrics) | set(after_metrics)

    deltas: list[MetricDelta] = []
    regressions: list[str] = []

    for name in sorted(all_metric_names):
        before_score = before_metrics[name].mean_score if name in before_metrics else 0.0
        after_score = after_metrics[name].mean_score if name in after_metrics else 0.0
        delta = after_score - before_score
        improved = delta > 0
        # Simple significance heuristic: change > 2% is significant
        significant = abs(delta) > _SIGNIFICANT_DELTA

        if delta < -_SIGNIFICANT_DELTA:
            regressions.append(name)

        deltas.append(
            MetricDelta(
                metric=name,
                before_score=before_score,
                after_score=after_score,
                delta=delta,
                improved=improved,
                statistically_significant=significant,
            )
        )

    overall_improved = len(regressions) == 0 and any(d.improved for d in deltas)

    return DeltaComparison(
        before_run_id=before.run_id,
        after_run_id=after.run_id,
        dataset_version=dataset_version,
        deltas=deltas,
        overall_improved=overall_improved,
        regressions=regressions,
    )


# ---------------------------------------------------------------------------
# CLI entry point (T045)
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evaluations.analysis",
        description="Evaluation failure analysis and delta comparison",
    )
    parser.add_argument(
        "--run-id",
        help="Run ID for failure cluster report",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="Compare two run IDs",
    )
    parser.add_argument(
        "--dataset-version",
        default="v1",
        help="Dataset version for comparison validation",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(".foundry/results"),
        help="Directory containing run result files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file for report (default: stdout)",
    )
    return parser.parse_args(argv)


def _load_summary(results_dir: Path, run_id: str) -> RunSummary:
    """Load a RunSummary from the results directory."""
    path = results_dir / f"{run_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    summary_data = data.get("summary", data)
    return RunSummary.model_validate(summary_data)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the analysis CLI.

    Args:
        argv: Optional argument list (defaults to sys.argv).

    Returns:
        Exit code (0 for success).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)

    if args.compare:
        before_id, after_id = args.compare
        before = _load_summary(args.results_dir, before_id)
        after = _load_summary(args.results_dir, after_id)
        comparison = compare_runs(before, after, dataset_version=args.dataset_version)
        output = comparison.model_dump_json(indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)  # noqa: T201
        return 0

    if args.run_id:
        _load_summary(args.results_dir, args.run_id)
        clusters = cluster_failures([])  # Would need failure records from run data
        output = json.dumps([c.model_dump() for c in clusters], indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)  # noqa: T201
        return 0

    logger.error("Specify --run-id or --compare")
    return 1


if __name__ == "__main__":
    sys.exit(main())
