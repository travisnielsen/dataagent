"""Inspect Foundry evaluation run status with detailed diagnostics.

Usage:
    uv run python -m evaluations.inspect_run_status \
      --eval-id <eval_id> \
      --run-id <evalrun_id>

    uv run python -m evaluations.inspect_run_status \
      --eval-name cadence-eval-v1 \
      --latest-trigger nightly
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Sequence
from typing import Any, cast

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        argv: Optional argv override.

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        prog="inspect_run_status",
        description="Inspect a Foundry evaluation run with detailed diagnostics",
    )
    parser.add_argument(
        "--project-endpoint",
        default="",
        help="Foundry project endpoint (defaults to AZURE_AI_PROJECT_ENDPOINT)",
    )
    parser.add_argument(
        "--eval-id",
        default="",
        help="Evaluation definition id (e.g., eval_abc123)",
    )
    parser.add_argument(
        "--eval-name",
        default="",
        help="Evaluation definition name to resolve to eval_id",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Evaluation run id (e.g., evalrun_abc123)",
    )
    parser.add_argument(
        "--latest-trigger",
        choices=["nightly", "manual", "ci_pr"],
        default="",
        help="Select latest run matching trigger if --run-id not provided",
    )
    parser.add_argument(
        "--max-output-items",
        type=int,
        default=10,
        help="Max output items to include in response payload diagnostics",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object (machine-readable)",
    )
    return parser.parse_args(argv)


async def _resolve_eval_id(
    *,
    openai_client: object,
    eval_id: str,
    eval_name: str,
) -> str:
    """Resolve evaluation id from explicit id or name.

    Args:
        openai_client: OpenAI client from AIProjectClient.
        eval_id: Explicit evaluation id.
        eval_name: Evaluation definition name.

    Returns:
        Resolved evaluation id.

    Raises:
        ValueError: If no evaluation id could be resolved.
    """
    if eval_id:
        return eval_id

    if not eval_name:
        raise ValueError("Either --eval-id or --eval-name is required")

    oa_client = cast(Any, openai_client)
    evals = list(await asyncio.to_thread(oa_client.evals.list))
    for eval_obj in evals:
        if getattr(eval_obj, "name", "") == eval_name:
            candidate = getattr(eval_obj, "id", "")
            if isinstance(candidate, str) and candidate:
                return candidate

    raise ValueError(f"Evaluation name not found: {eval_name}")


async def _resolve_run_id(
    *,
    openai_client: object,
    eval_id: str,
    run_id: str,
    latest_trigger: str,
) -> str:
    """Resolve run id from explicit id or latest run criteria.

    Args:
        openai_client: OpenAI client from AIProjectClient.
        eval_id: Evaluation definition id.
        run_id: Explicit run id.
        latest_trigger: Trigger filter for latest run.

    Returns:
        Resolved run id.

    Raises:
        ValueError: If no run id could be resolved.
    """
    if run_id:
        return run_id

    oa_client = cast(Any, openai_client)
    runs = list(await asyncio.to_thread(oa_client.evals.runs.list, eval_id=eval_id))
    if not runs:
        raise ValueError(f"No runs found for eval_id={eval_id}")

    if latest_trigger:
        for run_obj in runs:
            metadata = getattr(run_obj, "metadata", None)
            if isinstance(metadata, dict) and metadata.get("trigger") == latest_trigger:
                candidate = getattr(run_obj, "id", "")
                if isinstance(candidate, str) and candidate:
                    return candidate
        raise ValueError(f"No runs found for eval_id={eval_id} with trigger={latest_trigger}")

    fallback = getattr(runs[0], "id", "")
    if isinstance(fallback, str) and fallback:
        return fallback

    raise ValueError(f"Could not resolve a run id for eval_id={eval_id}")


def _safe_get(obj: object, name: str) -> object | None:
    """Safely access an attribute and normalize callables.

    Args:
        obj: Source object.
        name: Attribute name.

    Returns:
        Attribute value or None.
    """
    value = getattr(obj, name, None)
    if callable(value):
        return None
    return value


def _to_jsonable(value: object) -> object:
    """Convert SDK objects and nested structures into JSON-safe values.

    Args:
        value: Any value.

    Returns:
        JSON-serializable value.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_to_jsonable(v) for v in value]

    for method_name in ("model_dump", "as_dict", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _to_jsonable(method())
            except Exception:
                logger.debug("Serialization method failed: %s", method_name, exc_info=True)

    attrs: dict[str, object] = {}
    for attr_name in dir(value):
        if attr_name.startswith("_"):
            continue
        attr_value = getattr(value, attr_name, None)
        if callable(attr_value):
            continue
        if attr_name == "additional_properties":
            continue
        attrs[attr_name] = _to_jsonable(attr_value)

    if attrs:
        return attrs

    return str(value)


def _summarize_output_items(output_items: Sequence[object], max_items: int) -> dict[str, object]:
    """Build diagnostics summary for output items.

    Args:
        output_items: Foundry output items.
        max_items: Maximum output items to include in details.

    Returns:
        Summary dictionary.
    """
    failed_items = 0
    passed_items = 0
    sample_errors: list[dict[str, object]] = []

    for item in output_items:
        results = _safe_get(item, "results")
        if not isinstance(results, list):
            continue

        item_failed = False
        item_passed = False
        for result in results:
            passed = _safe_get(result, "passed")
            if isinstance(passed, bool):
                if passed:
                    item_passed = True
                else:
                    item_failed = True

            sample = _safe_get(result, "sample")
            if isinstance(sample, dict) and sample.get("error"):
                item_failed = True
                sample_errors.append({
                    "item_id": _safe_get(item, "id"),
                    "metric": _safe_get(result, "name") or _safe_get(result, "metric"),
                    "error": _to_jsonable(sample.get("error")),
                })

        if item_failed:
            failed_items += 1
        elif item_passed:
            passed_items += 1

    item_details = [_to_jsonable(item) for item in output_items[:max_items]]

    return {
        "count": len(output_items),
        "passed_items": passed_items,
        "failed_items": failed_items,
        "sample_errors": sample_errors[:max_items],
        "items_preview": item_details,
    }


async def inspect_run_status(argv: list[str] | None = None) -> dict[str, object]:
    """Inspect a Foundry eval run and return detailed diagnostics.

    Args:
        argv: Optional argv list.

    Returns:
        Detailed run inspection dictionary.

    Raises:
        ValueError: On missing required inputs or unresolved resources.
    """
    args = _parse_args(argv)
    endpoint = args.project_endpoint or os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT is required")

    client = AIProjectClient(endpoint=endpoint, credential=AzureCliCredential())
    try:
        openai_client = client.get_openai_client()

        eval_id = await _resolve_eval_id(
            openai_client=openai_client,
            eval_id=args.eval_id,
            eval_name=args.eval_name,
        )

        run_id = await _resolve_run_id(
            openai_client=openai_client,
            eval_id=eval_id,
            run_id=args.run_id,
            latest_trigger=args.latest_trigger,
        )

        run_obj = await asyncio.to_thread(
            openai_client.evals.runs.retrieve,
            eval_id=eval_id,
            run_id=run_id,
        )

        output_items = await asyncio.to_thread(
            lambda: list(openai_client.evals.runs.output_items.list(eval_id=eval_id, run_id=run_id))
        )

        eval_obj = await asyncio.to_thread(openai_client.evals.retrieve, eval_id=eval_id)

        run_json = _to_jsonable(run_obj)
        eval_json = _to_jsonable(eval_obj)

        result_counts = _safe_get(run_obj, "result_counts")
        output_summary = _summarize_output_items(output_items, max_items=args.max_output_items)

        testing_criteria_raw = _safe_get(eval_obj, "testing_criteria")
        testing_criteria = testing_criteria_raw if isinstance(testing_criteria_raw, list) else []

        diagnostics: dict[str, object] = {
            "project_endpoint": endpoint,
            "eval": {
                "id": eval_id,
                "name": _safe_get(eval_obj, "name"),
                "testing_criteria_count": len(testing_criteria),
                "testing_criteria": _to_jsonable(testing_criteria),
            },
            "run": {
                "id": run_id,
                "name": _safe_get(run_obj, "name"),
                "status": _safe_get(run_obj, "status"),
                "created_at": _safe_get(run_obj, "created_at"),
                "metadata": _to_jsonable(_safe_get(run_obj, "metadata")),
                "error": _to_jsonable(_safe_get(run_obj, "error")),
                "result_counts": _to_jsonable(result_counts),
                "result_counts_total": _safe_get(result_counts, "total") if result_counts else 0,
                "per_testing_criteria_results": _to_jsonable(
                    _safe_get(run_obj, "per_testing_criteria_results")
                ),
            },
            "output_items": output_summary,
            "raw": {
                "run": run_json,
                "eval": eval_json,
            },
        }
        return diagnostics
    finally:
        await asyncio.to_thread(client.close)


def _print_human_readable(diagnostics: dict[str, object]) -> None:
    """Print concise human-readable diagnostics.

    Args:
        diagnostics: Inspection payload.
    """
    run_obj = diagnostics.get("run", {})
    run = run_obj if isinstance(run_obj, dict) else {}
    eval_obj = diagnostics.get("eval", {})
    eval_info = eval_obj if isinstance(eval_obj, dict) else {}
    output_obj = diagnostics.get("output_items", {})
    output_items = output_obj if isinstance(output_obj, dict) else {}

    print(f"eval: {eval_info.get('name')} ({eval_info.get('id')})")  # noqa: T201
    print(f"run: {run.get('name')} ({run.get('id')})")  # noqa: T201
    print(f"status: {run.get('status')}")  # noqa: T201
    print(f"created_at: {run.get('created_at')}")  # noqa: T201
    print(f"metadata: {json.dumps(run.get('metadata', {}), indent=2)}")  # noqa: T201
    print(f"error: {json.dumps(run.get('error'), indent=2)}")  # noqa: T201
    print(f"result_counts: {json.dumps(run.get('result_counts', {}), indent=2)}")  # noqa: T201
    print(  # noqa: T201
        "per_testing_criteria_results: "
        f"{json.dumps(run.get('per_testing_criteria_results', []), indent=2)}"
    )
    print(f"output_items_count: {output_items.get('count')}")  # noqa: T201
    print(f"output_items_passed: {output_items.get('passed_items')}")  # noqa: T201
    print(f"output_items_failed: {output_items.get('failed_items')}")  # noqa: T201
    print(  # noqa: T201
        f"output_item_sample_errors: {json.dumps(output_items.get('sample_errors', []), indent=2)}"
    )


async def _main(argv: list[str] | None = None) -> int:
    """Program entrypoint.

    Args:
        argv: Optional argv list.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    diagnostics = await inspect_run_status(argv)

    if args.json:
        print(json.dumps(diagnostics, indent=2, sort_keys=True))  # noqa: T201
    else:
        _print_human_readable(diagnostics)

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI wrapper for inspect_run_status.

    Args:
        argv: Optional argv list.

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    import sys

    sys.exit(main())
