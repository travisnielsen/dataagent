"""Centralized application settings loaded from environment variables.

All configuration is defined once here. Other modules should import
``get_settings()`` rather than calling ``os.getenv()`` directly.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration backed by environment variables.

    Field names are **lowercased** versions of the env-var names.
    ``pydantic-settings`` maps them automatically (case-insensitive).

    Example::

        settings = Settings()  # reads .env + real env
        endpoint = settings.azure_ai_project_endpoint
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Azure AI / Foundry ------------------------------------------------

    azure_ai_project_endpoint: str = ""
    """Foundry project endpoint URL."""

    azure_ai_model_deployment_name: str = "gpt-4o"
    """Default model deployment shared by all agents unless overridden."""

    azure_ai_orchestrator_model: str | None = None
    """Model override for the orchestrator. Falls back to default."""

    azure_ai_orchestrator_agent_name: str = "cadence-data-assistant"
    """Stable name for the orchestrator agent. Emitted as the ``gen_ai.agent.name``
    OpenTelemetry attribute so that Foundry portal Traces and Application
    Insights can correlate spans with the orchestrator. The same value is used
    as the agent ``id`` unless ``AZURE_AI_ORCHESTRATOR_AGENT_ID`` is set."""

    azure_ai_orchestrator_agent_id: str | None = None
    """Stable identity for the orchestrator agent (``gen_ai.agent.id``). When
    unset, ``azure_ai_orchestrator_agent_name`` is used."""

    azure_ai_param_extractor_model: str | None = None
    """Model override for the parameter extractor. Falls back to default."""

    azure_ai_query_builder_model: str | None = None
    """Model override for the query builder. Falls back to default."""

    azure_ai_embedding_deployment: str = "embedding-small"
    """Embedding model deployment name."""

    azure_client_id: str | None = None
    """Managed-identity client ID (None → system-assigned)."""

    # -- Azure Search ------------------------------------------------------

    azure_search_endpoint: str = ""
    """Azure AI Search service endpoint."""

    # -- Azure SQL ---------------------------------------------------------

    azure_sql_server: str = ""
    """SQL Server hostname."""

    azure_sql_database: str = "WideWorldImporters"
    """Target database name."""

    # -- Thresholds / Tuning -----------------------------------------------

    query_template_confidence_threshold: float = 0.80
    """Minimum score for a template match to be accepted."""

    query_template_ambiguity_gap: float = 0.03
    """Minimum gap between top-two template scores to avoid ambiguity."""

    table_search_threshold: float = 0.03
    """Minimum relevance score for table search results."""

    dynamic_confidence_threshold: float = 0.7
    """Confidence threshold for dynamically built queries."""

    allowed_values_ttl_seconds: int = 300
    """TTL (seconds) for the allowed-values cache."""

    allowed_values_max_cache_entries: int = 50
    """Maximum entries in the allowed-values cache."""

    # -- Operational -------------------------------------------------------

    max_workflow_cache_size: int = 100
    """Upper bound on cached workflow instances."""

    max_session_cache_size: int = 1000
    """Upper bound on cached session objects."""

    enable_instrumentation: bool = False
    """Enable Application Insights tracing."""

    applicationinsights_connection_string: str | None = None
    """App Insights connection string (None → tracing disabled)."""

    enable_sensitive_data: bool = False
    """Include sensitive data in traces / logs."""

    allow_anonymous: bool = False
    """Allow unauthenticated access to the API."""

    # -- Evaluation --------------------------------------------------------

    eval_judge_model_deployment: str = "gpt-4o"
    """Model deployment for LLM-judge evaluators."""

    eval_default_dataset: str = "cadence-eval-gold-v1"
    """Default evaluation dataset name."""


def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Uses ``lru_cache`` semantics via a module-level singleton so the
    ``.env`` file is read at most once per process.

    Returns:
        The global ``Settings`` object.
    """
    return _settings


_settings = Settings()
