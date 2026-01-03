"""
Shared models for entities.

These models are used across multiple agents and the workflow.
"""

from pydantic import BaseModel, Field


class NL2SQLResponse(BaseModel):
    """
    Structured response from NL2SQL agent.

    Contains the SQL query, results, and metadata about the execution.
    Used by the workflow to pass data from data_agent to chat_agent.
    """

    sql_query: str = Field(
        default="",
        description="The SQL query that was executed"
    )

    sql_response: list[dict] = Field(
        default_factory=list,
        description="List of row dictionaries from the query result"
    )

    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score from cached query match (0-1)"
    )

    columns: list[str] = Field(
        default_factory=list,
        description="Column names from the result set"
    )

    row_count: int = Field(
        default=0,
        ge=0,
        description="Total number of rows returned"
    )

    used_cached_query: bool = Field(
        default=False,
        description="Whether a pre-cached query was used"
    )

    error: str | None = Field(
        default=None,
        description="Error message if the query failed"
    )
