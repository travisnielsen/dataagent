"""
Data Agent - NL2SQL agent for database exploration.

The agent:
1. Searches for cached queries matching user questions
2. Executes SQL against the Wide World Importers database
3. Returns structured results
"""

import os
from pathlib import Path

from agent_framework import ChatAgent
from agent_framework_azure_ai import AzureAIAgentClient
from azure.identity.aio import DefaultAzureCredential

from .tools import execute_sql, search_cached_queries


def load_prompt() -> str:
    """Load the prompt from prompt.md in this folder."""
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def _create_agent() -> ChatAgent:
    """Create the data agent."""
    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create chat client with Azure credential
    credential = DefaultAzureCredential()
    chat_client = AzureAIAgentClient(
        endpoint=endpoint,
        credential=credential,
    )

    # Load instructions
    instructions = load_prompt()

    # Create agent with SQL tools
    return ChatAgent(
        name="data-agent",
        instructions=instructions,
        chat_client=chat_client,
        tools=[search_cached_queries, execute_sql],
    )


# Create agent at module level for DevUI discovery
agent = _create_agent()
