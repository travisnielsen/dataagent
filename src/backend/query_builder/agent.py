"""
Query Builder Agent - Standalone agent for testing.

The agent generates SQL queries from table metadata when no
pre-defined template matches the user's question.
"""

import os
from pathlib import Path

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential


def load_prompt() -> str:
    """Load the prompt from prompt.md in this folder."""
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def create_query_builder_agent(
    client: FoundryChatClient,
    instructions: str,
) -> Agent:
    """Create a query builder Agent.

    Args:
        client: Azure AI client for LLM access.
        instructions: Agent system prompt text.

    Returns:
        Configured Agent for query building.
    """
    return Agent(
        name="query-builder-agent",
        instructions=instructions,
        client=client,
    )


def _create_agent() -> Agent:
    """Create the query builder agent."""
    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create chat client with Azure credential
    # Use AZURE_CLIENT_ID for user-assigned managed identity in Container Apps
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        credential = DefaultAzureCredential()

    # Use the same model as parameter extractor (or a dedicated one if configured)
    default_model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    query_builder_model = os.getenv("AZURE_AI_QUERY_BUILDER_MODEL", default_model)

    chat_client = FoundryChatClient(
        project_endpoint=endpoint,
        credential=credential,
        model=query_builder_model,
    )

    # Load instructions
    instructions = load_prompt()

    # Create agent (no tools needed - this is pure LLM reasoning)
    return Agent(
        name="query-builder-agent",
        instructions=instructions,
        client=chat_client,
    )


# Create agent at module level
agent = _create_agent()
