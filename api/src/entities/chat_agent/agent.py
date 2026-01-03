"""
Chat Agent - User-facing agent that renders data results.

The chat agent:
1. Receives structured data results from the data agent
2. Formats and presents data clearly to the user
3. Provides helpful context about the results
"""

import os
from pathlib import Path

from agent_framework import ChatAgent
from agent_framework_azure_ai import AzureAIAgentClient
from azure.identity.aio import DefaultAzureCredential


def load_prompt() -> str:
    """Load the prompt from prompt.md in this folder."""
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def _create_agent() -> ChatAgent:
    """Create the chat agent."""
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

    # Create agent (no tools - this agent just renders responses)
    return ChatAgent(
        name="chat-agent",
        instructions=instructions,
        chat_client=chat_client,
    )


# Create agent at module level for DevUI discovery
agent = _create_agent()
