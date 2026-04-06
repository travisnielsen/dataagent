"""Trace-to-dataset pipeline — KQL extraction from Application Insights.

Provides KQL templates for error, latency, and low-eval-score
harvesting, plus sanitization and dataset versioning.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from evaluations.models import DatasetMetadata, DatasetRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KQL templates (T021)
# ---------------------------------------------------------------------------

KQL_ERROR_HARVEST = """\
dependencies
| where timestamp >= ago({days}d)
| where success == false
| where name has "gen_ai"
| project
    timestamp,
    operation_Id,
    name,
    resultCode,
    data,
    customDimensions
| join kind=inner (
    customEvents
    | where name == "gen_ai.chat.completions"
    | project operation_Id, query=tostring(customDimensions["gen_ai.prompt"]),
              response=tostring(customDimensions["gen_ai.completion"])
) on operation_Id
| project timestamp, query, response, error=resultCode
| order by timestamp desc
| take {limit}
"""

KQL_LATENCY_HARVEST = """\
dependencies
| where timestamp >= ago({days}d)
| where name has "gen_ai"
| where duration > {latency_threshold_ms}
| project
    timestamp,
    operation_Id,
    name,
    duration,
    customDimensions
| join kind=inner (
    customEvents
    | where name == "gen_ai.chat.completions"
    | project operation_Id, query=tostring(customDimensions["gen_ai.prompt"]),
              response=tostring(customDimensions["gen_ai.completion"])
) on operation_Id
| project timestamp, query, response, duration
| order by duration desc
| take {limit}
"""

KQL_LOW_EVAL_SCORE_HARVEST = """\
customEvents
| where timestamp >= ago({days}d)
| where name == "evaluation_result"
| where todouble(customDimensions["score"]) < {score_threshold}
| project
    timestamp,
    operation_Id,
    metric=tostring(customDimensions["metric"]),
    score=todouble(customDimensions["score"]),
    query=tostring(customDimensions["query"]),
    response=tostring(customDimensions["response"])
| order by score asc
| take {limit}
"""

KQL_TEMPLATES: dict[str, str] = {
    "errors": KQL_ERROR_HARVEST,
    "latency": KQL_LATENCY_HARVEST,
    "low_eval_score": KQL_LOW_EVAL_SCORE_HARVEST,
}


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


def build_kql_query(
    harvest_type: str,
    *,
    days: int = 7,
    limit: int = 100,
    latency_threshold_ms: int = 5000,
    score_threshold: float = 0.5,
) -> str:
    """Build a KQL query string from a template.

    Args:
        harvest_type: One of ``errors``, ``latency``, ``low_eval_score``.
        days: Lookback period in days.
        limit: Maximum records to harvest.
        latency_threshold_ms: Latency threshold for slow queries.
        score_threshold: Score threshold for low-eval harvest.

    Returns:
        Formatted KQL query string.

    Raises:
        ValueError: If *harvest_type* is not recognized.
    """
    template = KQL_TEMPLATES.get(harvest_type)
    if template is None:
        msg = f"Unknown harvest type: {harvest_type}. Valid: {list(KQL_TEMPLATES)}"
        raise ValueError(msg)
    return template.format(
        days=days,
        limit=limit,
        latency_threshold_ms=latency_threshold_ms,
        score_threshold=score_threshold,
    )
