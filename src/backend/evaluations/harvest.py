"""Trace-to-dataset pipeline — Foundry native trace export.

Harvests conversation traces directly from Foundry project sessions
instead of querying Application Insights. Provides sanitization and
dataset versioning for mixed gold+trace evaluation strategies.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evaluations.models import DatasetMetadata, DatasetRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Foundry trace export (T021)
# ---------------------------------------------------------------------------

_USER_ROLES = {"user", "human"}
_ASSISTANT_ROLES = {"assistant", "agent"}


async def harvest_foundry_traces(
    *,
    project_endpoint: str,
    days_lookback: int = 7,
    limit: int = 100,
) -> list[DatasetRecord]:
    """Harvest conversation traces from Foundry project.

    Queries the Foundry project for recent sessions and extracts
    query+response pairs to create DatasetRecords.

    Args:
        project_endpoint: Foundry project endpoint URL.
        days_lookback: Lookback period for sessions (default: 7 days).
        limit: Maximum records to harvest (default: 100).

    Returns:
        List of DatasetRecord objects extracted from Foundry traces.

    Raises:
        ImportError: If azure-ai-projects is not installed.
        RuntimeError: If Foundry API call fails.
    """
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    except ImportError as e:
        msg = "azure-ai-projects not installed. Install with: pip install azure-ai-projects"
        raise ImportError(msg) from e

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=project_endpoint, credential=credential)

    records: list[DatasetRecord] = []
    cutoff_time = datetime.now(UTC) - timedelta(days=days_lookback)

    try:
        # Query recent sessions from Foundry
        logger.info(
            "Querying Foundry traces from %s (lookback: %d days)",
            project_endpoint,
            days_lookback,
        )

        # Foundry SDK trace/session query API
        # Note: Full trace export API integration is in progress with Foundry team
        # For now, attempt to access available session data through agents API
        sessions = []
        try:
            # Attempt to get evaluation runs which may contain conversation data
            if hasattr(client, "agents") and hasattr(client.agents, "list_runs"):
                runs_response = client.agents.list_runs(limit=limit)  # type: ignore[attr-defined]
                runs_list = (
                    runs_response.data if hasattr(runs_response, "data") else [runs_response]
                )
                sessions.extend(
                    run
                    for run in runs_list
                    if hasattr(run, "created_at") and run.created_at >= cutoff_time
                )
                logger.info("Found %d sessions in Foundry", len(sessions))
        except (AttributeError, TypeError):
            logger.warning(
                "Foundry session API not available or returned unexpected format. "
                "Trace harvesting may require newer SDK version or Foundry project configuration."
            )
            return []

        # Extract conversation pairs from sessions
        for session in sessions:
            records.extend(_extract_records_from_session(session))

        records = records[:limit]
        logger.info("Harvested %d records from Foundry traces", len(records))

    except Exception:
        logger.exception("Failed to harvest Foundry traces")
        msg = "Foundry trace harvest failed"
        raise RuntimeError(msg) from None
    else:
        return records


def _extract_records_from_session(session: object) -> list[DatasetRecord]:
    """Extract DatasetRecords from a Foundry session object.

    Args:
        session: Session object from Foundry API.

    Returns:
        List of DatasetRecord objects.
    """
    records: list[DatasetRecord] = []

    # Attempt to access messages attribute on the session object
    # For Foundry SDK objects, messages should be an attribute
    if hasattr(session, "messages"):
        messages = getattr(session, "messages", None)
        if isinstance(messages, list):
            records.extend(_extract_records_from_messages(messages))

    return records


def _extract_records_from_messages(
    messages: list[dict[str, Any]],
) -> list[DatasetRecord]:
    """Extract query+response pairs from message list.

    Pairs consecutive user/assistant messages to create dataset records.

    Args:
        messages: List of message dictionaries with role and content.

    Returns:
        List of DatasetRecord objects.
    """
    records: list[DatasetRecord] = []

    user_message = None
    for msg in messages:
        if not isinstance(msg, dict):  # type: ignore[arg-type]
            continue

        role = msg.get("role", "").lower()
        content = msg.get("content", "").strip()

        if not content:
            continue

        if role in _USER_ROLES:
            user_message = content
        elif role in _ASSISTANT_ROLES and user_message:
            # Create a record for this query+response pair
            try:
                record = DatasetRecord(
                    query=user_message,
                    expected_behavior="User received valid response",
                    context=None,
                    ground_truth_sql=None,
                    ground_truth_params=None,
                    scenario_class="conversation",
                    conversation=[
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": content},
                    ],
                )
                records.append(record)
                user_message = None  # Reset for next pair
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to create record from messages: %s", e)
                continue

    return records


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

# Patterns to detect and mask sensitive data
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_PATTERN = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")


def sanitize_text(text: str) -> str:
    """Remove or mask sensitive patterns from text.

    Args:
        text: Raw text that may contain sensitive data.

    Returns:
        Text with sensitive patterns replaced by redaction markers.
    """
    text = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_PATTERN.sub("[REDACTED_PHONE]", text)
    text = _SSN_PATTERN.sub("[REDACTED_SSN]", text)
    text = _CREDIT_CARD_PATTERN.sub("[REDACTED_CC]", text)
    return text  # noqa: RET504


def sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a single trace-harvested record.

    Args:
        record: Raw record dictionary from trace harvesting.

    Returns:
        Sanitized copy of the record.
    """
    sanitized = dict(record)
    for field in ("query", "response", "context", "expected_behavior"):
        if field in sanitized and isinstance(sanitized[field], str):
            sanitized[field] = sanitize_text(sanitized[field])
    return sanitized


def sanitize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize a batch of trace-harvested records.

    Args:
        records: List of raw record dictionaries.

    Returns:
        List of sanitized record dictionaries.
    """
    return [sanitize_record(r) for r in records]


# ---------------------------------------------------------------------------
# Dataset versioning and persistence (T023)
# ---------------------------------------------------------------------------


def get_next_version(output_dir: Path, name_prefix: str) -> str:
    """Determine the next version tag for a dataset.

    Args:
        output_dir: Directory containing existing dataset files.
        name_prefix: Dataset name prefix (e.g., ``cadence-traces``).

    Returns:
        Next version string (e.g., ``v2`` if ``v1`` exists).
    """
    max_version = 0
    if output_dir.exists():
        for f in output_dir.glob(f"{name_prefix}-v*.jsonl"):
            match = re.search(r"-v(\d+)\.jsonl$", f.name)
            if match:
                max_version = max(max_version, int(match.group(1)))
    return f"v{max_version + 1}"


def persist_dataset(
    records: list[DatasetRecord],
    *,
    output_path: Path,
    name: str,
    version: str,
    source: str = "trace_harvested",
) -> DatasetMetadata:
    """Write records to JSONL and generate metadata.

    Args:
        records: Validated dataset records.
        output_path: File path for the JSONL output.
        name: Dataset name.
        version: Version tag.
        source: Source type.

    Returns:
        Generated ``DatasetMetadata``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(record.model_dump_json() + "\n")

    from evaluations.runner import generate_metadata  # noqa: PLC0415

    return generate_metadata(
        records,
        name=name,
        version=version,
        source=source,
        stage="traces",
    )


def merge_datasets(
    gold_records: list[DatasetRecord],
    trace_records: list[DatasetRecord],
    *,
    deduplicate: bool = True,
) -> list[DatasetRecord]:
    """Merge gold-curated and trace-harvested datasets.

    Creates mixed strategy dataset combining authoritative gold records
    with automatically harvested traces. Optionally deduplicates based
    on query text.

    Args:
        gold_records: Curated gold standard records.
        trace_records: Harvested trace records.
        deduplicate: Remove exact query duplicates (default: True).

    Returns:
        Merged dataset with gold records first, then unique traces.
    """
    merged = list(gold_records)

    if not deduplicate:
        return merged + trace_records

    # Deduplication: track queries we've already seen
    gold_queries = {r.query.strip().lower() for r in gold_records}
    seen = gold_queries.copy()

    for record in trace_records:
        normalized_query = record.query.strip().lower()
        if normalized_query not in seen:
            merged.append(record)
            seen.add(normalized_query)

    return merged
