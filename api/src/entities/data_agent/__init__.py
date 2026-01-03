"""
Data Agent - NL2SQL agent for database exploration.

This module exports 'agent' for DevUI auto-discovery.

The agent:
1. Searches for cached queries matching user questions
2. Executes SQL against the Wide World Importers database
3. Returns structured results

Usage with DevUI:
    devui ./src/entities/data_agent
"""

from .agent import agent, load_prompt


def get_agent():
    """
    Get the NL2SQL agent.

    Returns:
        Configured ChatAgent with SQL tools
    """
    return agent


# Export for programmatic access and DevUI discovery
__all__ = ["agent", "get_agent", "load_prompt"]
__all__ = ["get_agent", "load_prompt", "execute_sql", "search_cached_queries"]
