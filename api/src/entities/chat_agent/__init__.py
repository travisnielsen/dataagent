"""
Chat Agent - User-facing agent that renders data results.

This module exports 'agent' for DevUI auto-discovery.

The chat agent:
1. Receives structured data results from the data agent
2. Formats and presents data clearly to the user
3. Provides helpful context about the results

Usage with DevUI:
    devui ./src/entities/chat_agent
"""

from .agent import agent, load_prompt


def get_agent():
    """
    Get the Chat agent.

    Returns:
        Configured ChatAgent for user-facing interactions
    """
    return agent


# Export for programmatic access and DevUI discovery
__all__ = ["agent", "get_agent", "load_prompt"]
