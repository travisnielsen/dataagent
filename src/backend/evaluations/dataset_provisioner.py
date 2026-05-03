"""Provision evaluation datasets into Foundry using New Foundry dataset APIs.

This module treats dataset versions as immutable. Existing versions are
reused only when the remote dataset bytes match the local file exactly.
If content changes, callers must publish a new version by renaming the
local file (for example ``cadence-eval-gold-v2.jsonl``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger(__name__)

_DATASET_FILENAME_PATTERN = re.compile(r"^(?P<name>.+)-(?P<version>v\d+)\.jsonl$")
_API_VERSION = "2025-11-15-preview"


@dataclass(slots=True)
class DatasetFile:
    """Represents a local dataset file and parsed Foundry asset coordinates."""

    path: Path
    dataset_name: str
    dataset_version: str


@dataclass(slots=True)
class SyncSummary:
    """Summary of dataset sync outcomes."""

    created: int = 0
    skipped_existing: int = 0
    invalid_files: int = 0


def parse_dataset_file(path: Path) -> DatasetFile | None:
    """Parse dataset file name into Foundry dataset name and version.

    Args:
        path: Dataset JSONL file path.

    Returns:
        Parsed DatasetFile, or None if the filename format is invalid.
    """
    match = _DATASET_FILENAME_PATTERN.match(path.name)
    if match is None:
        return None

    return DatasetFile(
        path=path,
        dataset_name=match.group("name"),
        dataset_version=match.group("version"),
    )


def _normalize_foundry_project_endpoint(project_endpoint: str) -> str:
    """Normalize Foundry endpoint to the services.ai.azure.com domain."""
    return project_endpoint.replace(
        ".cognitiveservices.azure.com",
        ".services.ai.azure.com",
    ).rstrip("/")


def _build_blob_upload_url(sas_uri: str, blob_name: str) -> str:
    """Build blob upload URL by appending blob name to container-level SAS URI."""
    parsed = urlsplit(sas_uri)
    upload_path = f"{parsed.path.rstrip('/')}/{blob_name}"
    return urlunsplit((parsed.scheme, parsed.netloc, upload_path, parsed.query, ""))


async def _dataset_exists(
    *,
    client: httpx.AsyncClient,
    project_endpoint: str,
    token: str,
    dataset_name: str,
    dataset_version: str,
) -> bool:
    """Check whether a dataset version already exists in Foundry."""
    response = await client.get(
        f"{project_endpoint}/datasets/{dataset_name}/versions/{dataset_version}",
        params={"api-version": _API_VERSION},
        headers={"Authorization": f"Bearer {token}"},
    )

    if response.status_code == httpx.codes.OK:
        return True
    if response.status_code == httpx.codes.NOT_FOUND:
        return False

    response.raise_for_status()
    return False


async def _create_dataset_version(
    *,
    client: httpx.AsyncClient,
    project_endpoint: str,
    token: str,
    dataset_file: DatasetFile,
) -> None:
    """Create a dataset asset version in Foundry and upload its content."""
    start_response = await client.post(
        f"{project_endpoint}/datasets/{dataset_file.dataset_name}/versions/"
        f"{dataset_file.dataset_version}/startPendingUpload",
        params={"api-version": _API_VERSION},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={},
    )
    start_response.raise_for_status()
    start_payload = start_response.json()

    sas_uri = cast_required_str(start_payload, ["blobReference", "credential", "sasUri"])
    consumption_blob_uri = cast_required_str(
        start_payload, ["blobReferenceForConsumption", "blobUri"]
    )

    upload_url = _build_blob_upload_url(sas_uri, dataset_file.path.name)
    file_bytes = dataset_file.path.read_bytes()

    upload_response = await client.put(
        upload_url,
        headers={
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "application/jsonl",
        },
        content=file_bytes,
    )
    upload_response.raise_for_status()

    dataset_payload = {
        "name": dataset_file.dataset_name,
        "version": dataset_file.dataset_version,
        "displayName": dataset_file.dataset_name,
        "description": "Cadence evaluation dataset",
        "type": "uri_file",
        "dataUri": f"{consumption_blob_uri.rstrip('/')}/{dataset_file.path.name}",
    }

    create_response = await client.put(
        f"{project_endpoint}/datasets/{dataset_file.dataset_name}/versions/{dataset_file.dataset_version}",
        params={"api-version": _API_VERSION},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=dataset_payload,
    )
    create_response.raise_for_status()


async def _get_dataset_blob_url(
    *,
    client: httpx.AsyncClient,
    project_endpoint: str,
    token: str,
    dataset_name: str,
    dataset_version: str,
) -> str:
    """Get a readable blob URL for an existing dataset version."""
    response = await client.post(
        f"{project_endpoint}/datasets/{dataset_name}/versions/{dataset_version}/credentials",
        params={"api-version": _API_VERSION},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={},
    )
    response.raise_for_status()
    payload = response.json()
    blob_uri = cast_required_str(payload, ["blobReferenceForConsumption", "blobUri"])
    sas_uri = cast_required_str(payload, ["blobReferenceForConsumption", "credential", "sasUri"])
    query = urlsplit(sas_uri).query
    return urlunsplit((
        urlsplit(blob_uri).scheme,
        urlsplit(blob_uri).netloc,
        urlsplit(blob_uri).path,
        query,
        "",
    ))


async def _dataset_content_matches(
    *,
    client: httpx.AsyncClient,
    project_endpoint: str,
    token: str,
    dataset_file: DatasetFile,
) -> bool:
    """Check whether the remote dataset bytes match the local file bytes."""
    blob_url = await _get_dataset_blob_url(
        client=client,
        project_endpoint=project_endpoint,
        token=token,
        dataset_name=dataset_file.dataset_name,
        dataset_version=dataset_file.dataset_version,
    )
    response = await client.get(blob_url)
    response.raise_for_status()
    return response.content == dataset_file.path.read_bytes()


def cast_required_str(payload: dict[str, Any], path: list[str]) -> str:
    """Extract a required nested string value from a payload."""
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            joined = ".".join(path)
            raise KeyError(f"Missing required field: {joined}")
        current = current[key]

    if not isinstance(current, str) or not current:
        joined = ".".join(path)
        raise ValueError(f"Expected non-empty string for field: {joined}")

    return current


def _collect_jsonl_files(datasets_dir: Path) -> list[Path]:
    """Collect all .jsonl files in directory (synchronous path operations)."""
    if not datasets_dir.exists():
        raise FileNotFoundError(f"Datasets directory not found: {datasets_dir}")

    return sorted(path for path in datasets_dir.glob("*.jsonl") if path.is_file())


async def sync_datasets(
    *,
    datasets_dir: Path,
    project_endpoint: str,
) -> SyncSummary:
    """Provision all dataset files in a directory into Foundry.

    Existing dataset versions are skipped. Missing versions are created.

    Args:
        datasets_dir: Directory containing ``*-vN.jsonl`` files.
        project_endpoint: Foundry project endpoint.

    Returns:
        Sync summary with created/skipped counts.
    """
    # Collect files synchronously before async operations
    jsonl_files = _collect_jsonl_files(datasets_dir)

    normalized_endpoint = _normalize_foundry_project_endpoint(project_endpoint)

    credential = DefaultAzureCredential()
    try:
        token = (await credential.get_token("https://ai.azure.com/.default")).token
    finally:
        await credential.close()

    summary = SyncSummary()

    async with httpx.AsyncClient(timeout=120) as client:
        for dataset_path in jsonl_files:
            parsed = parse_dataset_file(dataset_path)
            if parsed is None:
                summary.invalid_files += 1
                logger.warning(
                    "Skipping dataset with invalid filename format (expected <name>-vN.jsonl): %s",
                    dataset_path,
                )
                continue

            exists = await _dataset_exists(
                client=client,
                project_endpoint=normalized_endpoint,
                token=token,
                dataset_name=parsed.dataset_name,
                dataset_version=parsed.dataset_version,
            )
            if exists:
                matches_remote = await _dataset_content_matches(
                    client=client,
                    project_endpoint=normalized_endpoint,
                    token=token,
                    dataset_file=parsed,
                )
                if not matches_remote:
                    msg = (
                        "Dataset version already exists with different content: "
                        f"{parsed.dataset_name}/{parsed.dataset_version}. "
                        "Bump the repo filename version and re-run provisioning."
                    )
                    raise RuntimeError(msg)

                summary.skipped_existing += 1
                logger.info(
                    "Dataset already exists with identical content; skipping: %s/%s",
                    parsed.dataset_name,
                    parsed.dataset_version,
                )
                continue

            await _create_dataset_version(
                client=client,
                project_endpoint=normalized_endpoint,
                token=token,
                dataset_file=parsed,
            )
            summary.created += 1
            logger.info(
                "Created dataset in Foundry: %s/%s",
                parsed.dataset_name,
                parsed.dataset_version,
            )

    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dataset_provisioner",
        description="Provision evaluation datasets into Foundry",
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=Path("src/backend/evaluations/datasets"),
        help="Path to dataset JSONL files",
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

    summary = await sync_datasets(datasets_dir=args.datasets_dir, project_endpoint=endpoint)

    logger.info(
        "Dataset sync complete: created=%d skipped_existing=%d invalid_files=%d",
        summary.created,
        summary.skipped_existing,
        summary.invalid_files,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for dataset provisioning CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
