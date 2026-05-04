"""CLI entry point for evaluation runner.

Usage::

    uv run python -m evaluations run --dataset <path> --evaluators <names> --trigger <type>
    uv run python -m evaluations run --dataset <path> --evaluators <names> --trigger ci_pr --gate --cloud
    uv run python -m evaluations harvest --output <dir> [--gold <path>] [--days <n>] [--limit <n>]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from evaluations.config import build_default_config
from evaluations.harvest import harvest_foundry_traces, merge_datasets, persist_dataset
from evaluations.runner import (
    compute_quality_gate,
    load_dataset,
    persist_run_results,
    run_cloud_evaluation,
    run_evaluation,
)

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evaluations",
        description="NL2SQL evaluation runner and dataset harvester",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Run evaluation command
    run_parser = subparsers.add_parser("run", help="Run evaluation on dataset")
    run_parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to JSONL evaluation dataset",
    )
    run_parser.add_argument(
        "--evaluators",
        type=str,
        required=True,
        help="Comma-separated evaluator names",
    )
    run_parser.add_argument(
        "--trigger",
        choices=["ci_pr", "nightly", "manual"],
        default="manual",
        help="What triggered this evaluation run",
    )
    run_parser.add_argument(
        "--gate",
        action="store_true",
        help="Enforce P0 quality gate (exit non-zero on failure)",
    )
    run_parser.add_argument(
        "--output",
        type=Path,
        default=Path(".foundry/results"),
        help="Output directory for results",
    )
    run_parser.add_argument(
        "--cloud",
        action="store_true",
        help="Publish run to Foundry using Azure AI project configuration",
    )

    # Harvest traces command
    harvest_parser = subparsers.add_parser(
        "harvest", help="Harvest traces from Foundry and merge with gold dataset"
    )
    harvest_parser.add_argument(
        "--output",
        type=Path,
        default=Path(".foundry/datasets"),
        help="Output directory for merged dataset",
    )
    harvest_parser.add_argument(
        "--gold",
        type=Path,
        help="Path to gold dataset JSONL (if not provided, traces only)",
    )
    harvest_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Lookback period in days (default: 7)",
    )
    harvest_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum trace records to harvest (default: 100)",
    )
    harvest_parser.add_argument(
        "--no-deduplicate",
        action="store_true",
        help="Skip deduplication of queries",
    )

    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    if args.command == "harvest":
        return await _harvest_main(args)
    if args.command == "run":
        return await _run_main(args)
    logger.error("Unknown command: %s", args.command)
    return 2


async def _run_main(args: argparse.Namespace) -> int:
    config = build_default_config(
        project_endpoint=os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
        judge_model_deployment=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o"),
        dataset_name=os.getenv("AZURE_FOUNDRY_DATASET_NAME", "cadence-eval-gold"),
        dataset_version=os.getenv("AZURE_FOUNDRY_DATASET_VERSION", "v1"),
    )
    evaluator_names = [e.strip() for e in args.evaluators.split(",") if e.strip()]

    logger.info(
        "Starting evaluation: dataset=%s evaluators=%s trigger=%s",
        args.dataset,
        evaluator_names,
        args.trigger,
    )

    if args.cloud:
        run, summary = await run_cloud_evaluation(
            dataset_path=args.dataset,
            evaluator_names=evaluator_names,
            config=config,
            trigger=args.trigger,
        )
    else:
        run, summary = await run_evaluation(
            dataset_path=args.dataset,
            evaluator_names=evaluator_names,
            config=config,
            trigger=args.trigger,
        )

    if summary is None:
        logger.error("Foundry run submitted but no summary was returned")
        return 2

    # Persist results
    args.output.mkdir(parents=True, exist_ok=True)
    persist_run_results(run, summary, results_dir=args.output)

    logger.info("Results saved to %s", args.output)

    # Print summary
    print(f"\n{'=' * 60}")  # noqa: T201
    print(f"Evaluation Run: {run.run_id}")  # noqa: T201
    if run.eval_id:
        print(f"Foundry Studio URL: {run.eval_id}")  # noqa: T201
    print(f"Dataset: {run.dataset_name} ({run.dataset_version})")  # noqa: T201
    print(f"Records: {summary.total_records}")  # noqa: T201
    print(f"Overall: {'PASS' if summary.overall_pass else 'FAIL'}")  # noqa: T201
    print(f"{'=' * 60}")  # noqa: T201

    for metric in summary.metrics:
        status = "PASS" if metric.passed else "FAIL" if metric.passed is False else "N/A"
        print(  # noqa: T201
            f"  {metric.metric:30s} mean={metric.mean_score:.3f} "
            f"pass_rate={metric.pass_rate:.1%} [{status}]"
        )

    if args.gate:
        gate = compute_quality_gate(summary)
        if gate.gate_result == "fail":
            print(f"\nQuality gate FAILED: {gate.failing_metrics}")  # noqa: T201
            return 1
        print("\nQuality gate PASSED")  # noqa: T201

    return 0


async def _harvest_main(args: argparse.Namespace) -> int:
    """Harvest traces from Foundry and merge with gold dataset.

    Creates a mixed dataset combining gold-curated records with
    automatically harvested traces from Foundry.
    """
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        logger.error(
            "AZURE_AI_PROJECT_ENDPOINT not set. "
            "Foundry trace harvesting requires Foundry project configuration."
        )
        return 2

    logger.info(
        "Harvesting traces from Foundry (endpoint=%s, days=%d, limit=%d)",
        project_endpoint,
        args.days,
        args.limit,
    )

    try:
        # Harvest traces from Foundry
        trace_records = await harvest_foundry_traces(
            project_endpoint=project_endpoint,
            days_lookback=args.days,
            limit=args.limit,
        )
        logger.info("Harvested %d trace records from Foundry", len(trace_records))
    except Exception:
        logger.exception("Failed to harvest Foundry traces")
        return 2

    # Load gold dataset if provided
    gold_records = []
    if args.gold:
        if not args.gold.exists():
            logger.error("Gold dataset not found: %s", args.gold)
            return 2
        gold_records = load_dataset(args.gold)
        logger.info("Loaded %d gold records from %s", len(gold_records), args.gold)

    # Merge datasets
    deduplicate = not args.no_deduplicate
    merged_records = merge_datasets(gold_records, trace_records, deduplicate=deduplicate)
    logger.info(
        "Merged dataset: %d gold + %d traces = %d total (deduplicate=%s)",
        len(gold_records),
        len(trace_records),
        len(merged_records),
        deduplicate,
    )

    # Persist merged dataset
    try:
        args.output.mkdir(parents=True, exist_ok=True)

        # Determine version for merged dataset
        from evaluations.harvest import get_next_version  # noqa: PLC0415

        version = get_next_version(args.output, "cadence-eval-mixed")
        output_file = args.output / f"cadence-eval-mixed-{version}.jsonl"

        metadata = persist_dataset(
            merged_records,
            output_path=output_file,
            name="cadence-eval-mixed",
            version=version,
            source="gold+trace_harvested",
        )

        logger.info(
            "Persisted merged dataset: %s (%s)",
            output_file.name,
            metadata.dataset_uri or str(output_file),
        )
        print(f"✓ Mixed dataset: {output_file.name}")  # noqa: T201
        print(f"  Records: {metadata.record_count}")  # noqa: T201
        print(f"  Gold: {len(gold_records)}, Traces: {len(trace_records)}")  # noqa: T201

    except Exception:
        logger.exception("Failed to persist mixed dataset")
        return 2

    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the evaluation CLI.

    Args:
        argv: Optional argument list (defaults to sys.argv).

    Returns:
        Exit code (0 for success, 1 for gate failure).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
