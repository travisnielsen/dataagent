"""
Query Builder Agent - Standalone agent for testing.

The agent generates SQL queries from table metadata when no
pre-defined template matches the user's question.

Uses ``FoundryAgent`` so chat-turn spans carry an ``agent_reference``
to the hosted PromptAgent record ("query-builder-agent") in the
Microsoft Foundry project, enabling per-agent trace correlation in
the portal Traces view. The ``instructions`` argument flows through
as a per-request system message, so this file's ``prompt.md`` remains
the runtime source of truth even when the portal record's stored
prompt drifts.
"""

import os
from pathlib import Path

from agent_framework.foundry import FoundryAgent
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential

QUERY_BUILDER_AGENT_NAME = "query-builder-agent"


def load_prompt() -> str:
    """Load the prompt from prompt.md in this folder."""
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def create_query_builder_agent(
    *,
    project_endpoint: str,
    credential: AsyncTokenCredential,
    instructions: str,
) -> FoundryAgent:
    """Create a query builder ``FoundryAgent`` bound to the portal record.

    Args:
        project_endpoint: Microsoft Foundry project endpoint URL.
        credential: Async Azure token credential.
        instructions: Agent system prompt text (sent as per-request override).

    Returns:
        Configured ``FoundryAgent`` for SQL query building.
    """
    return FoundryAgent(
        project_endpoint=project_endpoint,
        credential=credential,
        agent_name=QUERY_BUILDER_AGENT_NAME,
        # agent_version omitted -> SDK resolves latest portal version
        name=QUERY_BUILDER_AGENT_NAME,
        instructions=instructions,
    )


def _create_agent() -> FoundryAgent:
    """Create the query builder agent (used for standalone testing)."""
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Use AZURE_CLIENT_ID for user-assigned managed identity in Container Apps
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        credential: AsyncTokenCredential = DefaultAzureCredential(
            managed_identity_client_id=client_id
        )
    else:
        credential = DefaultAzureCredential()

    return create_query_builder_agent(
        project_endpoint=endpoint,
        credential=credential,
        instructions=load_prompt(),
    )


# Create agent at module level
agent = _create_agent()
