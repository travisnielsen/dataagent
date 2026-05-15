"""Replay client for nightly evaluations.

Replays each row of an evaluation dataset against the deployed Cadence API so
the backend emits OpenTelemetry ``invoke_agent`` spans into Application
Insights. The subsequent trace-based Foundry evaluation reads those spans and
scores the real agent behavior (instead of static dataset text).

The chat endpoint is ``GET /api/chat/stream`` (SSE). Each replay call:

1. Acquires an Azure AD bearer token for the API's app registration audience
   (``api://<AZURE_AD_CLIENT_ID>/.default``) using ``DefaultAzureCredential``
   so the self-hosted runner's user-assigned managed identity is picked up.
2. Issues a GET request with the dataset row's ``query`` as the ``message``
   query parameter and a fresh conversation id.
3. Consumes the SSE stream to completion so the full agent turn (including
   tool calls) is recorded server-side before moving to the next row.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from evaluations.models import DatasetRecord
from evaluations.runner import load_dataset

logger = logging.getLogger(__name__)

DEFAULT_CHAT_PATH = "/api/chat/stream"
DEFAULT_PER_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_INTER_REQUEST_DELAY_SECONDS = 1.0


class ReplayResult:
    """Outcome of a replay run."""

    def __init__(
        self,
        *,
        started_at: datetime,
        completed_at: datetime,
        total: int,
        succeeded: int,
        failed: int,
    ) -> None:
        self.started_at = started_at
        self.completed_at = completed_at
        self.total = total
        self.succeeded = succeeded
        self.failed = failed

    @property
    def lookback_hours(self) -> int:
        """Minimum lookback (rounded up) covering the entire replay window."""
        elapsed_seconds = (self.completed_at - self.started_at).total_seconds()
        return max(1, int(elapsed_seconds // 3600) + 1)


async def _acquire_bearer_token(audience_client_id: str) -> str:
    """Acquire an Entra ID access token for the Cadence API audience.

    Args:
        audience_client_id: Application (client) ID of the API app
            registration. The token is requested for ``api://<id>/.default``
            which matches the audiences accepted by
            ``AzureADAuthMiddleware``.

    Returns:
        Bearer token string.
    """
    try:
        from azure.identity.aio import DefaultAzureCredential  # noqa: PLC0415
    except ImportError as e:
        msg = f"azure-identity is required for replay auth: {e}"
        raise ImportError(msg) from e

    scope = f"api://{audience_client_id}/.default"
    async with DefaultAzureCredential() as credential:
        token = await credential.get_token(scope)
        return token.token


async def _replay_one(
    *,
    client: httpx.AsyncClient,
    chat_url: str,
    record: DatasetRecord,
    bearer_token: str,
) -> bool:
    """Replay a single dataset record against the chat stream endpoint.

    Args:
        client: Async HTTP client.
        chat_url: Absolute URL of the chat stream endpoint.
        record: Dataset record providing the user query.
        bearer_token: Pre-acquired bearer token.

    Returns:
        ``True`` if the request completed without raising.
    """
    conversation_id = uuid.uuid4().hex
    params = {
        "message": record.query,
        "conversation_id": conversation_id,
    }
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "text/event-stream",
    }

    try:
        async with client.stream(
            "GET",
            chat_url,
            params=params,
            headers=headers,
            timeout=DEFAULT_PER_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()
            # Drain the SSE stream so the backend completes the full turn
            # (intent classification + NL2SQL pipeline + final render) before
            # we close the connection.
            async for _ in response.aiter_lines():
                pass
    except httpx.HTTPError:
        logger.exception(
            "Replay request failed: query=%s conversation_id=%s",
            record.query[:80],
            conversation_id,
        )
        return False
    else:
        logger.info(
            "Replayed query: conversation_id=%s query=%s",
            conversation_id,
            record.query[:80],
        )
        return True


async def replay_dataset(
    *,
    dataset_path: Path,
    base_url: str,
    audience_client_id: str,
    chat_path: str = DEFAULT_CHAT_PATH,
    inter_request_delay_seconds: float = DEFAULT_INTER_REQUEST_DELAY_SECONDS,
) -> ReplayResult:
    """Replay every record in a dataset against the Cadence chat endpoint.

    Args:
        dataset_path: Path to the JSONL gold dataset.
        base_url: Base URL of the deployed Cadence API
            (e.g. ``https://cadence-api.<env>.azurecontainerapps.io``).
        audience_client_id: AAD app registration client id used as the token
            audience.
        chat_path: Endpoint path; defaults to ``/api/chat/stream``.
        inter_request_delay_seconds: Politeness delay between requests.

    Returns:
        ``ReplayResult`` capturing start/end timestamps and counts.
    """
    records = load_dataset(dataset_path)
    if not records:
        msg = f"Dataset has no records: {dataset_path}"
        raise RuntimeError(msg)

    bearer_token = await _acquire_bearer_token(audience_client_id)
    chat_url = base_url.rstrip("/") + chat_path

    logger.info(
        "Starting replay: records=%d url=%s audience=%s",
        len(records),
        chat_url,
        audience_client_id,
    )

    started_at = datetime.now(UTC)
    succeeded = 0
    failed = 0

    async with httpx.AsyncClient() as client:
        for index, record in enumerate(records):
            ok = await _replay_one(
                client=client,
                chat_url=chat_url,
                record=record,
                bearer_token=bearer_token,
            )
            if ok:
                succeeded += 1
            else:
                failed += 1
            if index < len(records) - 1 and inter_request_delay_seconds > 0:
                await asyncio.sleep(inter_request_delay_seconds)

    completed_at = datetime.now(UTC)
    result = ReplayResult(
        started_at=started_at,
        completed_at=completed_at,
        total=len(records),
        succeeded=succeeded,
        failed=failed,
    )
    logger.info(
        "Replay complete: total=%d succeeded=%d failed=%d duration=%.1fs",
        result.total,
        result.succeeded,
        result.failed,
        (completed_at - started_at).total_seconds(),
    )
    return result
