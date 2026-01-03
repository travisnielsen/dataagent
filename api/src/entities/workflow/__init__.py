"""
Data Agent Workflow - Orchestrates Chat and NL2SQL agents.

This module exports 'workflow' for DevUI auto-discovery.

The workflow:
1. ChatAgentExecutor receives user messages
2. Forwards to NL2SQLAgentExecutor for data queries
3. NL2SQLAgentExecutor returns structured results
4. ChatAgentExecutor renders results for the user

Usage with DevUI:
    devui ./src/entities
"""

from .workflow import workflow, chat_executor, chat_client
from .builder import build_data_agent_workflow


def get_workflow():
    """
    Get the data agent workflow.

    Returns:
        Tuple of (workflow, chat_executor, chat_client)
    """
    return workflow, chat_executor, chat_client


# Export for programmatic access and DevUI discovery
__all__ = ["workflow", "chat_executor", "chat_client", "get_workflow", "build_data_agent_workflow"]

