"""CLI entry point for evaluation runner.

Usage::

    uv run python -m evaluations --dataset <path> --evaluators <names> --trigger <type>
    uv run python -m evaluations --dataset <path> --evaluators <names> --trigger ci_pr --gate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from evaluations.config import build_default_config
from evaluations.runner import compute_quality_gate, run_evaluation

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evaluations",
        description="NL2SQL evaluation runner",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to JSONL evaluation dataset",
    )
    parser.add_argument(
        "--evaluators",
        type=str,
        required=True,
        help="Comma-separated evaluator names",
    )
    parser.add_argument(
        "--trigger",
        choices=["ci_pr", "nightly", "manual"],
        default="manual",
        help="What triggered this evaluation run",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Enforce P0 quality gate (exit non-zero on failure)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".foundry/results"),
        help="Output directory for results",
    )
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    config = build_default_config()
    evaluator_names = [e.strip() for e in args.evaluators.split(",") if e.strip()]

    logger.info(
        "Starting evaluation: dataset=%s evaluators=%s trigger=%s",
        args.dataset,
        evaluator_names,
        args.trigger,
    )

    run, summary = await run_evaluation(
        dataset_path=args.dataset,
        evaluator_names=evaluator_names,
        config=config,
        trigger=args.trigger,
    )

    # Persist results
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path = args.output / "latest-summary.json"
    summary_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")

    run_path = args.output / f"{run.run_id}.json"
    run_data = {
        "run": json.loads(run.model_dump_json()),
        "summary": json.loads(summary.model_dump_json()),
    }
    run_path.write_text(json.dumps(run_data, indent=2) + "\n", encoding="utf-8")

    logger.info("Results saved to %s", args.output)

    # Print summary
    print(f"\n{'=' * 60}")  # noqa: T201
    print(f"Evaluation Run: {run.run_id}")  # noqa: T201
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
