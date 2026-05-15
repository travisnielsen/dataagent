"""Delete and recreate a specific dataset version in Foundry.

Usage:
    python -m evaluations.refresh_dataset \
        --dataset-name cadence-eval-gold \
        --dataset-version v1 \
        --dataset-file src/evaluations/datasets/cadence-eval-gold-v1.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

import httpx
from azure.identity import AzureCliCredential

from .dataset_provisioner import (
    DatasetFile,
    _create_dataset_version,
    _dataset_exists,
    _delete_dataset_version,
    _normalize_foundry_project_endpoint,
)

logger = logging.getLogger(__name__)

_API_VERSION = "2025-11-15-preview"


async def refresh_dataset_version(
    *,
    dataset_file: Path,
    dataset_name: str,
    dataset_version: str,
    project_endpoint: str,
) -> None:
    """Delete existing dataset version and recreate from file.

    Args:
        dataset_file: Path to the updated JSONL file.
        dataset_name: Foundry dataset name (e.g., "cadence-eval-gold").
        dataset_version: Version string (e.g., "v1").
        project_endpoint: Foundry project endpoint.
    """
    normalized_endpoint = _normalize_foundry_project_endpoint(project_endpoint)

    credential = AzureCliCredential()
    token = (await asyncio.to_thread(credential.get_token, "https://ai.azure.com/.default")).token

    # Use a session with fewer security restrictions for blob uploads
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    ) as client:
        # Check if dataset exists
        exists = await _dataset_exists(
            client=client,
            project_endpoint=normalized_endpoint,
            token=token,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
        )

        if exists:
            logger.info(
                "Deleting existing dataset version: %s/%s",
                dataset_name,
                dataset_version,
            )
            await _delete_dataset_version(
                client=client,
                project_endpoint=normalized_endpoint,
                token=token,
                dataset_name=dataset_name,
                dataset_version=dataset_version,
            )
            logger.info(
                "Successfully deleted dataset version: %s/%s",
                dataset_name,
                dataset_version,
            )
        else:
            logger.info(
                "Dataset version does not exist; will create new: %s/%s",
                dataset_name,
                dataset_version,
            )

        # Create new version from updated file
        logger.info(
            "Creating dataset version from file: %s",
            dataset_file,
        )
        dataset_file_obj = DatasetFile(
            path=dataset_file,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
        )
        await _create_dataset_version(
            client=client,
            project_endpoint=normalized_endpoint,
            token=token,
            dataset_file=dataset_file_obj,
        )
        logger.info(
            "Successfully created dataset version: %s/%s",
            dataset_name,
            dataset_version,
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="refresh_dataset",
        description="Delete and recreate a dataset version in Foundry",
    )
    parser.add_argument(
        "--dataset-name",
        required=True,
        help="Foundry dataset name (e.g., 'cadence-eval-gold')",
    )
    parser.add_argument(
        "--dataset-version",
        required=True,
        help="Dataset version (e.g., 'v1')",
    )
    parser.add_argument(
        "--dataset-file",
        type=Path,
        required=True,
        help="Path to the updated JSONL file",
    )
    parser.add_argument(
        "--project-endpoint",
        default="",
        help="Foundry project endpoint (defaults to AZURE_AI_PROJECT_ENDPOINT)",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    endpoint = args.project_endpoint or os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        logger.error("AZURE_AI_PROJECT_ENDPOINT is required")
        return 2

    if not args.dataset_file.exists():
        logger.error("Dataset file not found: %s", args.dataset_file)
        return 2

    await refresh_dataset_version(
        dataset_file=args.dataset_file,
        dataset_name=args.dataset_name,
        dataset_version=args.dataset_version,
        project_endpoint=endpoint,
    )

    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for dataset refresh CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    import sys

    sys.exit(main())
