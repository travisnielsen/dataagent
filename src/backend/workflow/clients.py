"""Pipeline client container and Protocol adapters for dependency injection.

``PipelineClients`` bundles every I/O dependency the NL2SQL pipeline
needs. Production code constructs it via ``create_pipeline_clients()``
from real Azure clients; tests construct it from in-memory fakes.

Protocol adapters (``TemplateSearchAdapter``, ``TableSearchAdapter``,
``SqlExecutorAdapter``) wrap the existing Azure client classes so they
satisfy the corresponding ``Protocol`` interfaces.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential
from config.settings import Settings
from models import ParameterDefinition, QueryTemplate, TableColumn, TableMetadata
from shared.allowed_values_provider import AllowedValuesProvider
from shared.clients import AzureSearchClient, AzureSqlClient
from shared.protocols import (
    NoOpReporter,
    ProgressReporter,
    SqlExecutor,
    TableSearchService,
    TemplateSearchService,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hydration helpers (pure functions, no I/O)
# ---------------------------------------------------------------------------

_MIN_AMBIGUITY_RESULTS = 2


def _parse_parameters(params_json: str | list | None) -> list[ParameterDefinition]:
    """Parse stringified JSON into ``ParameterDefinition`` objects.

    Args:
        params_json: JSON string, already-parsed list, or ``None``.

    Returns:
        List of validated ``ParameterDefinition`` instances.
    """
    if params_json is None:
        return []
    if isinstance(params_json, list):
        params_list = params_json
    else:
        try:
            params_list = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to parse parameters JSON: %s", exc)
            return []

    if not isinstance(params_list, list):
        logger.warning("Parameters field is not a list: %s", type(params_list))
        return []

    result: list[ParameterDefinition] = []
    for item in params_list:
        try:
            result.append(ParameterDefinition.model_validate(item))
        except Exception:  # noqa: BLE001
            logger.warning("Skipping unparseable parameter definition")
    return result


def _hydrate_query_template(raw: dict[str, Any]) -> QueryTemplate:
    """Convert a raw search result dict into a ``QueryTemplate``.

    Args:
        raw: Dictionary returned by Azure AI Search.

    Returns:
        Hydrated ``QueryTemplate`` with parsed parameters.
    """
    return QueryTemplate(
        id=raw.get("id", ""),
        intent=raw.get("intent", ""),
        question=raw.get("question", ""),
        sql_template=raw.get("sql_template", ""),
        reasoning=raw.get("reasoning", ""),
        parameters=_parse_parameters(raw.get("parameters")),
        score=raw.get("score", 0.0),
    )


def _hydrate_table_metadata(raw: dict[str, Any]) -> TableMetadata:
    """Convert a raw search result dict into a ``TableMetadata``.

    Args:
        raw: Dictionary returned by Azure AI Search.

    Returns:
        Hydrated ``TableMetadata`` with parsed columns.
    """
    raw_columns = raw.get("columns", [])
    columns = [
        TableColumn(
            name=col.get("name", ""),
            description=col.get("description", ""),
            data_type=col.get("data_type", ""),
            is_nullable=col.get("is_nullable", True),
            is_primary_key=col.get("is_primary_key", False),
            is_foreign_key=col.get("is_foreign_key", False),
            foreign_key_table=col.get("foreign_key_table", ""),
            foreign_key_column=col.get("foreign_key_column", ""),
        )
        for col in raw_columns
        if isinstance(col, dict)
    ]
    return TableMetadata(
        id=raw.get("id", ""),
        table=raw.get("table", ""),
        datasource=raw.get("datasource", ""),
        description=raw.get("description", ""),
        columns=columns,
        score=raw.get("score", 0.0),
    )


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_ALLOWED_TABLES_PATH = Path(__file__).resolve().parents[1] / "config" / "allowed_tables.json"


def load_allowed_tables(path: Path = _ALLOWED_TABLES_PATH) -> frozenset[str]:
    """Load allowed table names from a JSON config file.

    Args:
        path: Filesystem path to a JSON array of table names.

    Returns:
        Frozen set of fully-qualified table names.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the file is empty or malformed.
        TypeError: If the content is not a JSON array of strings.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Allowed tables config not found: {path}. "
            "Create config/allowed_tables.json with a JSON array of table names."
        )
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed allowed_tables.json: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(t, str) for t in data):
        raise TypeError("allowed_tables.json must contain a JSON array of strings")
    if not data:
        raise ValueError("allowed_tables.json must not be empty")
    return frozenset(data)


# ---------------------------------------------------------------------------
# Protocol adapters
# ---------------------------------------------------------------------------


class TemplateSearchAdapter:
    """``TemplateSearchService`` backed by ``AzureSearchClient``.

    Performs vector search against the ``query_templates`` index, hydrates
    results into ``QueryTemplate`` objects, and applies confidence /
    ambiguity thresholds.

    Args:
        confidence_threshold: Minimum cosine-similarity score for a match.
        ambiguity_gap: Minimum gap between 1st and 2nd scores.
    """

    def __init__(self, confidence_threshold: float, ambiguity_gap: float) -> None:
        self._confidence_threshold = confidence_threshold
        self._ambiguity_gap = ambiguity_gap

    async def search(self, user_question: str) -> dict[str, Any]:
        """Search query templates for a matching intent.

        Args:
            user_question: Natural-language question from the user.

        Returns:
            Dict with ``has_high_confidence_match``, ``is_ambiguous``,
            ``best_match``, ``confidence_score``, ``all_matches``, etc.
        """
        logger.info("Searching query templates for: %s", user_question[:100])
        try:
            async with AzureSearchClient(
                index_name="query_templates",
                vector_field="content_vector",
            ) as client:
                results = await client.vector_search(
                    query=user_question,
                    select=[
                        "id",
                        "intent",
                        "question",
                        "sql_template",
                        "reasoning",
                        "parameters",
                    ],
                    top=3,
                )
        except Exception as exc:
            logger.exception("Error searching query templates")
            return self._error_result(str(exc))

        if not results:
            return self._empty_result("No query templates found")

        templates = [_hydrate_query_template(r) for r in results]
        best = templates[0]
        top_score = best.score
        has_high = top_score >= self._confidence_threshold

        # Ambiguity check
        score_gap = top_score  # default: fully unambiguous
        is_ambiguous = False
        if len(templates) >= _MIN_AMBIGUITY_RESULTS:
            second_score = templates[1].score
            score_gap = top_score - second_score
            if has_high:
                is_ambiguous = score_gap < self._ambiguity_gap

        is_valid = has_high and not is_ambiguous

        if not has_high:
            message = (
                f"No high confidence match (score {top_score:.3f} "
                f"< threshold {self._confidence_threshold:.3f})"
            )
        elif is_ambiguous:
            second = templates[1]
            message = (
                f"Ambiguous match: '{best.intent}' (score={top_score:.3f}) "
                f"and '{second.intent}' (score={second.score:.3f}) "
                f"are too similar (gap {score_gap:.3f} "
                f"< {self._ambiguity_gap:.3f})"
            )
        else:
            message = f"High confidence unambiguous match: '{best.intent}'"

        logger.info(
            "Template search: %d results. Best: '%s' score=%.3f "
            "(threshold: %.3f, gap: %.3f, ambiguous: %s, valid: %s)",
            len(results),
            best.intent,
            top_score,
            self._confidence_threshold,
            score_gap,
            is_ambiguous,
            is_valid,
        )

        return {
            "has_high_confidence_match": is_valid,
            "is_ambiguous": is_ambiguous,
            "best_match": best.model_dump() if is_valid else None,
            "confidence_score": top_score,
            "confidence_threshold": self._confidence_threshold,
            "ambiguity_gap": score_gap,
            "ambiguity_gap_threshold": self._ambiguity_gap,
            "all_matches": [t.model_dump() for t in templates],
            "message": message,
        }

    # -- helpers --

    def _empty_result(self, message: str) -> dict[str, Any]:
        return {
            "has_high_confidence_match": False,
            "is_ambiguous": False,
            "best_match": None,
            "confidence_score": 0.0,
            "confidence_threshold": self._confidence_threshold,
            "ambiguity_gap": 0.0,
            "ambiguity_gap_threshold": self._ambiguity_gap,
            "all_matches": [],
            "message": message,
        }

    def _error_result(self, error: str) -> dict[str, Any]:
        result = self._empty_result(f"Error: {error}")
        result["error"] = error
        return result


class TableSearchAdapter:
    """``TableSearchService`` backed by ``AzureSearchClient``.

    Performs hybrid search against the ``tables`` index, hydrates results
    into ``TableMetadata`` objects, and filters by score threshold.

    Args:
        score_threshold: Minimum relevance score for a table match.
    """

    def __init__(self, score_threshold: float) -> None:
        self._score_threshold = score_threshold

    async def search(self, user_question: str) -> dict[str, Any]:
        """Search for relevant database tables.

        Args:
            user_question: Natural-language question from the user.

        Returns:
            Dict with ``has_matches``, ``tables``, ``table_count``, ``message``.
        """
        logger.info("Searching tables for: %s", user_question[:100])
        try:
            async with AzureSearchClient(
                index_name="tables",
                vector_field="content_vector",
            ) as client:
                results = await client.hybrid_search(
                    query=user_question,
                    select=["id", "table", "datasource", "description", "columns"],
                    top=5,
                )
        except Exception as exc:
            logger.exception("Error searching tables")
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "error": str(exc),
                "message": f"Error: {exc}",
            }

        if not results:
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "message": "No tables found matching the query",
            }

        hydrated = [_hydrate_table_metadata(r) for r in results]
        matching = [t for t in hydrated if t.score >= self._score_threshold]

        if not matching:
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "score_threshold": self._score_threshold,
                "best_score": hydrated[0].score if hydrated else 0.0,
                "message": f"No tables met the score threshold ({self._score_threshold})",
            }

        logger.info(
            "Table search: %d tables above threshold (%.3f). Tables: %s",
            len(matching),
            self._score_threshold,
            [t.table for t in matching],
        )
        return {
            "has_matches": True,
            "tables": [t.model_dump() for t in matching],
            "table_count": len(matching),
            "score_threshold": self._score_threshold,
            "message": f"Found {len(matching)} relevant table(s)",
        }


class SqlExecutorAdapter:
    """``SqlExecutor`` backed by ``AzureSqlClient``.

    Each ``execute()`` call opens and closes a fresh database connection
    (matching the existing per-call pattern in the ``@tool`` function).

    Args:
        server: Azure SQL server hostname.
        database: Database name.
    """

    def __init__(self, server: str, database: str) -> None:
        self._server = server
        self._database = database

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a read-only SQL query.

        Args:
            query: SQL SELECT statement, optionally with ``?`` placeholders.
            params: Bind-parameter values (or ``None``).

        Returns:
            Result dict with ``success``, ``columns``, ``rows``,
            ``row_count``, and ``error`` keys.
        """
        try:
            async with AzureSqlClient(
                server=self._server,
                database=self._database,
                read_only=True,
            ) as client:
                return await client.execute_query(query, params)
        except Exception as exc:
            logger.exception("SQL execution error")
            return {
                "success": False,
                "error": str(exc),
                "columns": [],
                "rows": [],
                "row_count": 0,
            }


# ---------------------------------------------------------------------------
# PipelineClients dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineClients:
    """Immutable bundle of all I/O dependencies for the NL2SQL pipeline.

    All fields use Protocol types, enabling full dependency injection.
    Production code passes real Azure clients; tests pass fakes.

    Args:
        param_extractor_agent: Agent for parameter extraction LLM calls.
        query_builder_agent: Agent for SQL generation LLM calls.
        template_search: Service for searching query templates.
        table_search: Service for searching table metadata.
        sql_executor: Service for executing SQL queries.
        reporter: Progress reporter for streaming UI updates.
        allowed_tables: Set of fully-qualified table names for query validation.
        allowed_values_provider: Optional provider for database-sourced allowed values.
        conversation_id: Optional provider conversation ID for multi-agent trace continuity.
    """

    param_extractor_agent: Agent
    query_builder_agent: Agent
    template_search: TemplateSearchService
    table_search: TableSearchService
    sql_executor: SqlExecutor
    reporter: ProgressReporter
    allowed_tables: frozenset[str]
    allowed_values_provider: AllowedValuesProvider | None = None
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_pipeline_clients(
    settings: Settings,
    reporter: ProgressReporter | None = None,
    conversation_id: str | None = None,
) -> PipelineClients:
    """Build a ``PipelineClients`` from application ``Settings``.

    Loads prompts from disk, creates ``Agent`` instances via the
    updated agent factories, wraps Azure clients in Protocol adapters,
    and reads the allowed-tables config file.  No module-level singletons
    are created; each call produces a fresh, self-contained bundle.

    Args:
        settings: Centralised application configuration.
        reporter: Optional progress reporter.  Defaults to ``NoOpReporter``.
        conversation_id: Optional provider conversation ID to reuse for all
            pipeline agent calls within a user session.

    Returns:
        Fully-initialised ``PipelineClients`` ready for ``process_query()``.
    """
    # -- Credential --------------------------------------------------------
    credential = (
        DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id)
        if settings.azure_client_id
        else DefaultAzureCredential()
    )

    # -- LLM clients -------------------------------------------------------
    extractor_model = (
        settings.azure_ai_param_extractor_model or settings.azure_ai_model_deployment_name
    )
    builder_model = settings.azure_ai_query_builder_model or settings.azure_ai_model_deployment_name

    extractor_llm = FoundryChatClient(
        project_endpoint=settings.azure_ai_project_endpoint,
        credential=credential,
        model=extractor_model,
    )
    builder_llm = FoundryChatClient(
        project_endpoint=settings.azure_ai_project_endpoint,
        credential=credential,
        model=builder_model,
    )

    # NOTE: conversation_id is no longer settable as a client attribute in
    # agent-framework 1.4.x. Workflow LLMs propagate the orchestrator's
    # conversation_id implicitly via the Foundry project's Responses API
    # session when the user-facing orchestrator agent shares the same
    # project. If per-pipeline-call conversation correlation is required,
    # pass it via ``default_options={"conversation_id": conversation_id}``
    # when constructing the per-agent Agent (see create_param_extractor_agent).

    # -- Prompts (loaded once from disk) -----------------------------------
    from parameter_extractor.agent import (  # noqa: PLC0415
        create_param_extractor_agent,
    )
    from parameter_extractor.agent import (  # noqa: PLC0415
        load_prompt as load_extractor_prompt,
    )
    from query_builder.agent import (  # noqa: PLC0415
        create_query_builder_agent,
    )
    from query_builder.agent import (  # noqa: PLC0415
        load_prompt as load_builder_prompt,
    )

    extractor_prompt = load_extractor_prompt()
    builder_prompt = load_builder_prompt()

    # -- Agents ------------------------------------------------------------
    param_agent = create_param_extractor_agent(extractor_llm, extractor_prompt)
    builder_agent = create_query_builder_agent(builder_llm, builder_prompt)

    # -- Protocol adapters -------------------------------------------------
    template_search = TemplateSearchAdapter(
        confidence_threshold=settings.query_template_confidence_threshold,
        ambiguity_gap=settings.query_template_ambiguity_gap,
    )
    table_search = TableSearchAdapter(
        score_threshold=settings.table_search_threshold,
    )
    sql_executor = SqlExecutorAdapter(
        server=settings.azure_sql_server,
        database=settings.azure_sql_database,
    )

    # -- Allowed tables ----------------------------------------------------
    allowed_tables = load_allowed_tables()

    # -- AllowedValuesProvider ---------------------------------------------
    avp = AllowedValuesProvider(
        server=settings.azure_sql_server,
        database=settings.azure_sql_database,
        ttl_seconds=settings.allowed_values_ttl_seconds,
        max_entries=settings.allowed_values_max_cache_entries,
    )

    return PipelineClients(
        param_extractor_agent=param_agent,
        query_builder_agent=builder_agent,
        template_search=template_search,
        table_search=table_search,
        sql_executor=sql_executor,
        reporter=reporter or NoOpReporter(),
        allowed_tables=allowed_tables,
        allowed_values_provider=avp,
        conversation_id=conversation_id,
    )
