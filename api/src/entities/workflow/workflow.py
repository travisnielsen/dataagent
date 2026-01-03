"""
Data Agent Workflow - Orchestrates Chat and NL2SQL agents.

This module exports 'workflow' for DevUI auto-discovery.

The workflow:
1. ChatAgentExecutor receives user messages
2. Forwards to NL2SQLAgentExecutor for data queries
3. NL2SQLAgentExecutor returns structured results
4. ChatAgentExecutor renders results for the user
"""

import os

from agent_framework import WorkflowBuilder
from agent_framework_azure_ai import AzureAIAgentClient
from azure.identity.aio import DefaultAzureCredential

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from chat_agent.executor import ChatAgentExecutor  # type: ignore[import-not-found]
    from data_agent.executor import NL2SQLAgentExecutor  # type: ignore[import-not-found]
except ImportError:
    from src.entities.chat_agent.executor import ChatAgentExecutor
    from src.entities.data_agent.executor import NL2SQLAgentExecutor


def _create_workflow():
    """
    Create the data agent workflow.

    Returns:
        Tuple of (workflow, chat_executor, chat_client)
    """
    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create chat client with Azure credential
    credential = DefaultAzureCredential()
    _chat_client = AzureAIAgentClient(
        endpoint=endpoint,
        credential=credential,
    )

    # Create executors
    _chat_executor = ChatAgentExecutor(_chat_client)
    nl2sql_executor = NL2SQLAgentExecutor(_chat_client)

    # Build the workflow
    # Chat -> NL2SQL -> Chat (render)
    _workflow = (
        WorkflowBuilder()
        .add_edge(_chat_executor, nl2sql_executor)  # User message -> NL2SQL
        .add_edge(nl2sql_executor, _chat_executor)  # NL2SQL response -> Chat render
        .set_start_executor(_chat_executor)
        .build()
    )

    return _workflow, _chat_executor, _chat_client


# Create workflow at module level for DevUI discovery
workflow, chat_executor, chat_client = _create_workflow()
