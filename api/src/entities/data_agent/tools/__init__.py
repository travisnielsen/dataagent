"""
Tools for the data agent.

Provides AI-callable functions for:
- Searching cached SQL queries
- Executing SQL against the database
"""

from .search import search_cached_queries
from .sql import execute_sql

__all__ = ["search_cached_queries", "execute_sql"]
