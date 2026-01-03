"""
Workflow builder for the data agent workflow.
"""

import logging

from agent_framework import WorkflowBuilder
from agent_framework_azure_ai import AzureAIAgentClient

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from chat_agent.executor import ChatAgentExecutor  # type: ignore[import-not-found]
    from data_agent.executor import NL2SQLAgentExecutor  # type: ignore[import-not-found]
except ImportError:
    from src.entities.chat_agent.executor import ChatAgentExecutor
    from src.entities.data_agent.executor import NL2SQLAgentExecutor

logger = logging.getLogger(__name__)


def build_data_agent_workflow(chat_client: AzureAIAgentClient):
    """
    Build the data agent workflow.

    Creates a workflow where:
    1. ChatAgentExecutor receives user input
    2. Forwards question to NL2SQLAgentExecutor
    3. NL2SQLAgentExecutor processes and returns structured data
    4. ChatAgentExecutor renders the response

    Args:
        chat_client: The Azure AI agent client

    Returns:
        Tuple of (workflow, chat_executor) for use in the API
    """
    # Create executors
    chat_executor = ChatAgentExecutor(chat_client)
    nl2sql_executor = NL2SQLAgentExecutor(chat_client)

    # Build the workflow
    # Chat -> NL2SQL -> Chat (render)
    workflow = (
        WorkflowBuilder()
        .add_edge(chat_executor, nl2sql_executor)  # User message -> NL2SQL
        .add_edge(nl2sql_executor, chat_executor)  # NL2SQL response -> Chat render
        .set_start_executor(chat_executor)
        .build()
    )

    logger.info("Data agent workflow built: Chat <-> NL2SQL")

    return workflow, chat_executor
