"""NL2SQL pipeline — single-function entry point for query processing.

Replaces the 1,297-line ``NL2SQLController`` executor class with a
plain ``process_query()`` async function.  All routing is expressed as
if/else logic; there are no Executors, no WorkflowContext, no handler
decorators, and no message wrapper types.
"""

from __future__ import annotations

import json
import logging
import operator
import re
import uuid
from datetime import datetime
from typing import Any

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryAgent
from models import (
    ChartSeriesDefinition,
    ClarificationRequest,
    MissingParameter,
    NL2SQLRequest,
    NL2SQLResponse,
    ParameterExtractionRequest,
    PromptHint,
    QueryBuilderRequest,
    QueryTemplate,
    ScenarioAssumptionSet,
    ScenarioComputationResult,
    ScenarioIntent,
    ScenarioMetricValue,
    ScenarioVisualizationPayload,
    SQLDraft,
    TableMetadata,
)
from parameter_extractor.extractor import extract_parameters
from parameter_validator.validator import validate_parameters
from pydantic import ValidationError
from query_builder.builder import build_query
from query_validator.validator import validate_query
from shared.column_filter import refine_columns
from shared.error_recovery import build_error_recovery
from shared.scenario_constants import (
    MAX_SCENARIO_CHART_ITEMS,
    MIN_BASELINE_ROWS,
    MIN_DISTINCT_WEEKLY_PERIODS,
)
from shared.scenario_hints import build_clarification_hint, build_drill_down_hints
from shared.scenario_math import aggregate_baseline, compute_delta_pct, compute_scenario_metrics
from shared.scenario_narrative import build_narrative_summary
from shared.substitution import substitute_parameters
from workflow.clients import PipelineClients

logger = logging.getLogger(__name__)

# ── Confidence thresholds ────────────────────────────────────────────────

_CONFIDENCE_THRESHOLD_HIGH = 0.85
_CONFIDENCE_THRESHOLD_LOW = 0.6
_DYNAMIC_CONFIDENCE_THRESHOLD = 0.7


# ── Helper functions (ported from executor.py) ───────────────────────────


def _format_hypothesis_prompt(missing_params: list[MissingParameter]) -> str:
    """Format a hypothesis-first clarification prompt from missing params.

    When a ``best_guess`` is available the prompt reads:

        "It looks like you want **guess** for name. Is that correct,
        or did you mean alt1 or alt2?"

    Otherwise falls back to a plain "What value …?" question.

    Args:
        missing_params: Parameters that need clarification.

    Returns:
        Formatted clarification prompt string.
    """
    parts: list[str] = []
    for mp in missing_params:
        if mp.best_guess:
            alt_text = ""
            if mp.alternatives:
                alt_text = " or ".join(f"**{a}**" for a in mp.alternatives[:3])
                alt_text = f", or did you mean {alt_text}?"
            else:
                alt_text = "?"
            parts.append(
                f"It looks like you want **{mp.best_guess}** "
                f"for {mp.name}. Is that correct{alt_text}"
            )
        elif mp.alternatives:
            opts = ", ".join(mp.alternatives[:5])
            parts.append(f"What value would you like for {mp.name}? Options: {opts}")
        else:
            parts.append(f"What value would you like for {mp.name}?")
    return " ".join(parts)


def _format_confirmation_note(
    parameter_confidences: dict[str, float],
    extracted_parameters: dict[str, Any] | None,
) -> str:
    """Build a confirmation note for medium-confidence parameters.

    Args:
        parameter_confidences: Per-parameter confidence scores.
        extracted_parameters: Resolved parameter values.

    Returns:
        Human-readable confirmation note, or empty string.
    """
    if not extracted_parameters:
        return ""
    confirm_parts: list[str] = []
    for name, score in parameter_confidences.items():
        if _CONFIDENCE_THRESHOLD_LOW <= score < _CONFIDENCE_THRESHOLD_HIGH:
            value = extracted_parameters.get(name)
            if value is not None:
                confirm_parts.append(f"{name}=**{value}**")
    if not confirm_parts:
        return ""
    return f"I assumed {', '.join(confirm_parts)} for these results. Want me to adjust?"


def _format_defaults_for_display(
    defaults_used: dict[str, Any],
) -> dict[str, str]:
    """Format ``defaults_used`` into human-readable descriptions.

    Args:
        defaults_used: Parameter name → default value.

    Returns:
        Parameter name → human-readable description.
    """
    if not defaults_used:
        return {}

    descriptions: dict[str, str] = {}
    for name, value in defaults_used.items():
        if name == "days":
            descriptions[name] = f"last {value} days"
        elif name == "from_date" and isinstance(value, str) and "GETDATE()" in value.upper():
            descriptions[name] = "relative to current date"
        elif name in {"limit", "top"}:
            descriptions[name] = f"showing top {value} results"
        elif name in {"order", "sort"}:
            descriptions[name] = f"sorted {value}"
        else:
            descriptions[name] = str(value)

    return descriptions


def _build_agent_session(agent: Agent | FoundryAgent, conversation_id: str | None) -> AgentSession:
    """Create or reuse an agent session for pipeline LLM calls.

    When a provider conversation ID is available, this reuses the same
    provider conversation so multi-agent calls share one trace.
    """
    if conversation_id:
        return agent.get_session(service_session_id=conversation_id)
    return AgentSession()


# ── Confidence routing ───────────────────────────────────────────────────


def _apply_confidence_routing(draft: SQLDraft) -> SQLDraft:
    """Apply confidence-tier routing to extraction results.

    * ``min_conf < 0.6``  → convert to ``needs_clarification``
      (single-question enforcement: lowest-confidence param only).
    * ``0.6 ≤ min_conf < 0.85`` → set ``needs_confirmation=True``.
    * ``min_conf ≥ 0.85`` → unchanged.

    Only applies to template-sourced drafts with confidence scores.

    Args:
        draft: The extraction result.

    Returns:
        Potentially modified ``SQLDraft``.
    """
    if draft.status != "success" or not draft.parameter_confidences or draft.source != "template":
        return draft

    min_conf = min(draft.parameter_confidences.values())

    if min_conf < _CONFIDENCE_THRESHOLD_LOW:
        return _to_clarification_draft(draft, min_conf)

    if min_conf < _CONFIDENCE_THRESHOLD_HIGH:
        logger.info(
            "Min confidence %.3f in [%.2f, %.2f) — needs_confirmation",
            min_conf,
            _CONFIDENCE_THRESHOLD_LOW,
            _CONFIDENCE_THRESHOLD_HIGH,
        )
        return draft.model_copy(update={"needs_confirmation": True})

    return draft


def _to_clarification_draft(
    draft: SQLDraft,
    min_conf: float,
) -> SQLDraft:
    """Downgrade a successful draft to ``needs_clarification``.

    Picks the single lowest-confidence parameter and builds a
    ``MissingParameter`` entry for it.

    Args:
        draft: Draft with at least one low-confidence parameter.
        min_conf: Pre-computed minimum confidence (for logging).

    Returns:
        Updated ``SQLDraft`` with status ``"needs_clarification"``.
    """
    logger.info(
        "Min confidence %.3f < %.2f — converting to needs_clarification",
        min_conf,
        _CONFIDENCE_THRESHOLD_LOW,
    )

    low_params = sorted(
        [
            (name, score)
            for name, score in draft.parameter_confidences.items()
            if score < _CONFIDENCE_THRESHOLD_LOW
        ],
        key=operator.itemgetter(1),
    )

    # Single-question enforcement: ask only about the lowest-confidence
    # parameter; the rest will be re-evaluated on the next turn.
    ask_now = low_params[:1]
    param_defs = {p.name: p for p in draft.parameter_definitions}

    missing: list[MissingParameter] = []
    for name, score in ask_now:
        current_value = (draft.extracted_parameters or {}).get(name)
        pdef = param_defs.get(name)
        alternatives: list[str] | None = None
        if pdef and pdef.validation and pdef.validation.allowed_values:
            alternatives = [v for v in pdef.validation.allowed_values if v != str(current_value)][
                :5
            ]
        missing.append(
            MissingParameter(
                name=name,
                description=(
                    f"Low confidence ({score:.2f}) — please confirm the value for '{name}'"
                ),
                best_guess=(str(current_value) if current_value is not None else None),
                guess_confidence=score,
                alternatives=alternatives,
            )
        )

    return draft.model_copy(
        update={
            "status": "needs_clarification",
            "missing_parameters": missing,
            "clarification_prompt": _format_hypothesis_prompt(missing),
        },
    )


# ── Response builders ────────────────────────────────────────────────────


def _build_clarification(
    draft: SQLDraft,
) -> ClarificationRequest:
    """Build a ``ClarificationRequest`` from a needs-clarification draft.

    Enforces single-question: only the first missing parameter is asked
    about.  Allowed values are pulled from the parameter definition when
    available.

    Args:
        draft: Draft with ``status="needs_clarification"``.

    Returns:
        A ``ClarificationRequest`` for the caller to surface to the user.
    """
    missing_params = draft.missing_parameters or []
    first_missing = missing_params[0] if missing_params else None

    if not first_missing:
        return ClarificationRequest(
            parameter_name="",
            prompt=(
                draft.clarification_prompt or "I need more information to answer your question."
            ),
        )

    # Resolve allowed values from parameter definitions
    allowed_values: list[str] = []
    for pdef in draft.parameter_definitions:
        if pdef.name == first_missing.name and pdef.validation:
            if pdef.validation.allowed_values:
                allowed_values = pdef.validation.allowed_values
            break

    # Build prompt
    if first_missing.best_guess:
        prompt = _format_hypothesis_prompt(missing_params[:1])
    elif allowed_values:
        prompt = f"Please choose a category: {', '.join(allowed_values)}"
    else:
        prompt = (
            draft.clarification_prompt or "I need a bit more information to answer your question."
        )

    return ClarificationRequest(
        parameter_name=first_missing.name,
        prompt=prompt,
        allowed_values=allowed_values,
        original_question=draft.user_query,
        template_id=draft.template_id or "",
        template_json=draft.template_json or "",
        extracted_parameters=draft.extracted_parameters or {},
    )


def _ambiguous_response(
    search_result: dict[str, Any],
) -> NL2SQLResponse:
    """Build an error response for ambiguous template matches.

    Args:
        search_result: Template search result dict.

    Returns:
        ``NL2SQLResponse`` explaining the ambiguity.
    """
    threshold = search_result.get("confidence_threshold", 0.75)
    all_matches = search_result.get("all_matches", [])
    matching_intents = [
        m.get("intent", "unknown") for m in all_matches[:3] if m.get("score", 0) >= threshold
    ]

    intent_list = ", ".join(f"'{i}'" for i in matching_intents)
    return NL2SQLResponse(
        sql_query="",
        error=(
            "Your question could match multiple query types: "
            f"{intent_list}. Could you please be more specific "
            "about what data you're looking for?"
        ),
        confidence_score=search_result.get("confidence_score", 0),
    )


def _confidence_gate_response(draft: SQLDraft) -> NL2SQLResponse:
    """Return a confirmation prompt for low-confidence dynamic queries.

    The query is *not* executed.  The frontend should display the
    summary and ask the user to confirm before proceeding.

    Args:
        draft: Validated dynamic draft below the confidence threshold.

    Returns:
        ``NL2SQLResponse`` with ``needs_clarification=True``.
    """
    sql = draft.completed_sql or ""
    summary = draft.reasoning or f"Execute: {sql[:150]}"

    return NL2SQLResponse(
        sql_query=sql,
        needs_clarification=True,
        query_summary=summary,
        query_confidence=draft.confidence,
        query_source="dynamic",
        tables_used=draft.tables_used,
        tables_metadata_json=draft.tables_metadata_json,
        original_question=draft.user_query,
    )


# ── Execution ────────────────────────────────────────────────────────────


async def _execute_and_respond(
    draft: SQLDraft,
    clients: PipelineClients,
) -> NL2SQLResponse:
    """Execute SQL and build the final ``NL2SQLResponse``.

    Applies column refinement for dynamic queries and builds
    human-readable metadata (defaults, confirmation notes).

    Args:
        draft: Validated draft ready for execution.
        clients: Pipeline I/O dependencies.

    Returns:
        ``NL2SQLResponse`` with query results or an error.
    """
    completed_sql = draft.completed_sql or ""

    clients.reporter.step_start("Executing query")
    exec_query = draft.exec_sql or completed_sql
    exec_params = draft.exec_params or None
    sql_result = await clients.sql_executor.execute(exec_query, exec_params)
    clients.reporter.step_end("Executing query")

    query_source = "template" if draft.template_id else "dynamic"
    confidence = (
        _CONFIDENCE_THRESHOLD_HIGH
        if draft.template_id
        else max(draft.confidence, _CONFIDENCE_THRESHOLD_LOW + 0.1)
    )
    query_confidence = draft.confidence if draft.source == "dynamic" else 0.0

    defaults_description = _format_defaults_for_display(draft.defaults_used)

    confirmation_note = ""
    if draft.needs_confirmation:
        confirmation_note = _format_confirmation_note(
            draft.parameter_confidences,
            draft.extracted_parameters,
        )

    # Column refinement for dynamic queries
    result_columns: list[str] = sql_result.get("columns", [])
    result_rows: list[dict] = sql_result.get("rows", [])
    hidden_columns: list[str] = []

    if draft.source == "dynamic" and sql_result.get("success"):
        refinement = refine_columns(
            columns=result_columns,
            rows=result_rows,
            user_query=draft.user_query,
            sql=completed_sql,
        )
        result_columns = refinement.columns
        hidden_columns = refinement.hidden_columns

    return NL2SQLResponse(
        sql_query=completed_sql,
        sql_response=result_rows,
        columns=result_columns,
        row_count=sql_result.get("row_count", 0),
        confidence_score=confidence,
        query_confidence=query_confidence,
        hidden_columns=hidden_columns,
        query_source=query_source,
        error=(sql_result.get("error") if not sql_result.get("success") else None),
        defaults_used=defaults_description,
        confirmation_note=confirmation_note,
        template_json=draft.template_json,
        extracted_params=draft.extracted_parameters or {},
        tables_used=draft.tables_used,
        tables_metadata_json=draft.tables_metadata_json,
        original_question=draft.user_query,
    )


# ── Template pipeline ────────────────────────────────────────────────────


async def _run_template_pipeline(
    extraction_req: ParameterExtractionRequest,
    template: QueryTemplate,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Shared extraction → validation → execution pipeline.

    Used by both the fresh-template and template-refinement paths.

    Args:
        extraction_req: Prepared extraction request.
        template: The query template being used.
        clients: Pipeline I/O dependencies.

    Returns:
        Final response or clarification request.
    """
    # 1. Extract parameters
    thread = _build_agent_session(clients.param_extractor_agent, clients.conversation_id)
    draft = await extract_parameters(
        extraction_req,
        clients.param_extractor_agent,
        thread,
        clients.reporter,
        allowed_values_provider=clients.allowed_values_provider,
    )

    # 2. Confidence routing
    draft = _apply_confidence_routing(draft)

    if draft.status == "needs_clarification":
        return _build_clarification(draft)

    if draft.status != "success":
        return NL2SQLResponse(
            sql_query="",
            error=draft.error or "Extraction failed",
            query_source="template",
        )

    # 3. SQL substitution (if extractor didn't build SQL itself)
    if not draft.completed_sql and draft.extracted_parameters:
        pq = substitute_parameters(template.sql_template, draft.extracted_parameters)
        draft = draft.model_copy(
            update={
                "completed_sql": pq.display_sql,
                "exec_sql": pq.exec_sql,
                "exec_params": list(pq.exec_params),
            },
        )

    if not draft.completed_sql:
        return NL2SQLResponse(
            sql_query="",
            error="SQL draft succeeded but no SQL was generated",
            query_source="template",
        )

    # 4. Validate parameters
    draft = validate_parameters(draft)
    if draft.parameter_violations:
        violation_summary = "; ".join(draft.parameter_violations)
        return NL2SQLResponse(
            sql_query="",
            error=f"Parameter validation failed: {violation_summary}",
            query_source="template",
        )

    # 5. Validate query
    draft = validate_query(draft, set(clients.allowed_tables))
    if draft.query_violations:
        error_msg, suggestions = build_error_recovery(draft.query_violations, draft.tables_used)
        return NL2SQLResponse(
            sql_query="",
            error=error_msg,
            query_source="template",
            error_suggestions=suggestions,
        )

    # 6. Execute
    return await _execute_and_respond(draft, clients)


async def _template_path(
    request: NL2SQLRequest,
    template: QueryTemplate,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Process a high-confidence template match.

    Args:
        request: The user's original request.
        template: Matched query template.
        clients: Pipeline I/O dependencies.

    Returns:
        Final response or clarification request.
    """
    extraction_req = ParameterExtractionRequest(
        user_query=request.user_query,
        template=template,
    )
    return await _run_template_pipeline(extraction_req, template, clients)


async def _handle_template_refinement(
    request: NL2SQLRequest,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Handle a refinement that re-uses a previous template.

    Merges ``base_params`` with ``param_overrides`` and feeds the
    combined set as ``previously_extracted`` so the extractor preserves
    already-confirmed values.

    Args:
        request: Refinement request with ``previous_template_json``.
        clients: Pipeline I/O dependencies.

    Returns:
        Final response or clarification request.
    """
    try:
        template_data = json.loads(request.previous_template_json or "{}")
        template = QueryTemplate.model_validate(template_data)
    except (json.JSONDecodeError, ValidationError):
        logger.exception("Failed to parse previous template, falling back to new query")
        fallback = request.model_copy(
            update={
                "is_refinement": False,
                "previous_template_json": None,
            },
        )
        return await process_query(fallback, clients)

    # Build enriched query with override hints
    enriched_query = request.user_query
    if request.param_overrides:
        hints = ", ".join(f"{k}={v}" for k, v in request.param_overrides.items())
        enriched_query = f"{request.user_query} (Use these values: {hints})"

    # Merge previously-extracted base params with overrides
    merged_params = dict(request.base_params or {})
    if request.param_overrides:
        merged_params.update(request.param_overrides)

    extraction_req = ParameterExtractionRequest(
        user_query=enriched_query,
        template=template,
        previously_extracted=merged_params,
    )
    return await _run_template_pipeline(extraction_req, template, clients)


# ── Dynamic pipeline ─────────────────────────────────────────────────────


async def _retry_dynamic_query(
    draft: SQLDraft,
    tables: list[TableMetadata],
    request: NL2SQLRequest,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Retry a failed dynamic query once with validation feedback.

    If the retry also fails (or max retries exceeded) an error
    response with recovery suggestions is returned.

    Args:
        draft: The draft whose query validation failed.
        tables: Table metadata for regeneration.
        request: The original user request.
        clients: Pipeline I/O dependencies.

    Returns:
        Final response, confidence gate, or error with recovery tips.
    """
    violations = draft.query_violations

    if draft.retry_count >= 1:
        error_msg, suggestions = build_error_recovery(violations, draft.tables_used)
        return NL2SQLResponse(
            sql_query="",
            error=error_msg,
            query_source="dynamic",
            error_suggestions=suggestions,
        )

    violation_list = "; ".join(violations)
    enriched_query = (
        f"{draft.user_query}\n\n"
        "[IMPORTANT: Your previous query was rejected for "
        f"validation errors: {violation_list}. Please generate a "
        "corrected SQL query that addresses these issues.]"
    )

    retry_req = QueryBuilderRequest(
        user_query=enriched_query,
        tables=tables,
        retry_count=draft.retry_count + 1,
    )

    thread = _build_agent_session(clients.query_builder_agent, clients.conversation_id)
    retry_draft = await build_query(
        retry_req,
        clients.query_builder_agent,
        thread,
        clients.reporter,
    )

    if retry_draft.status != "success":
        error_msg, suggestions = build_error_recovery(violations, draft.tables_used)
        return NL2SQLResponse(
            sql_query="",
            error=error_msg,
            query_source="dynamic",
            error_suggestions=suggestions,
        )

    # Validate the retried query
    retry_draft = validate_query(retry_draft, set(clients.allowed_tables))
    if retry_draft.query_violations:
        error_msg, suggestions = build_error_recovery(
            retry_draft.query_violations, retry_draft.tables_used
        )
        return NL2SQLResponse(
            sql_query="",
            error=error_msg,
            query_source="dynamic",
            error_suggestions=suggestions,
        )

    # Confidence gate
    if (
        retry_draft.source == "dynamic"
        and retry_draft.confidence < _DYNAMIC_CONFIDENCE_THRESHOLD
        and not request.is_refinement
    ):
        return _confidence_gate_response(retry_draft)

    return await _execute_and_respond(retry_draft, clients)


async def _dynamic_path(
    request: NL2SQLRequest,
    search_result: dict[str, Any],
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Generate and execute a dynamic SQL query via QueryBuilder.

    Used when no high-confidence template match is found.

    Args:
        request: The user's original request.
        search_result: Template search result (for score context).
        clients: Pipeline I/O dependencies.

    Returns:
        Final response, confidence gate, or error.
    """
    confidence_score = search_result.get("confidence_score", 0)

    clients.reporter.step_start("Searching tables")
    table_result = await clients.table_search.search(request.user_query)
    clients.reporter.step_end("Searching tables")

    if not table_result.get("has_matches") or not table_result.get("tables"):
        return NL2SQLResponse(
            sql_query="",
            error=(
                "I couldn't find a matching query pattern or relevant "
                "tables for your question. Could you please rephrase "
                "or provide more details about what data you're "
                "looking for?"
            ),
            confidence_score=confidence_score,
        )

    tables = [TableMetadata.model_validate(t) for t in table_result["tables"]]

    builder_req = QueryBuilderRequest(
        user_query=request.user_query,
        tables=tables,
    )

    thread = _build_agent_session(clients.query_builder_agent, clients.conversation_id)
    draft = await build_query(
        builder_req,
        clients.query_builder_agent,
        thread,
        clients.reporter,
    )

    if draft.status != "success":
        return NL2SQLResponse(
            sql_query="",
            error=draft.error or "Query generation failed",
            query_source="dynamic",
        )

    # Validate query
    draft = validate_query(draft, set(clients.allowed_tables))
    if draft.query_violations:
        return await _retry_dynamic_query(draft, tables, request, clients)

    # Confidence gate for dynamic queries
    if (
        draft.source == "dynamic"
        and draft.confidence < _DYNAMIC_CONFIDENCE_THRESHOLD
        and not request.is_refinement
    ):
        return _confidence_gate_response(draft)

    return await _execute_and_respond(draft, clients)


async def _handle_dynamic_refinement(
    request: NL2SQLRequest,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Handle a refinement for a dynamic (non-template) query.

    Re-uses table metadata from the previous query when available,
    otherwise falls back to a fresh table search.

    Args:
        request: Refinement request with ``previous_sql``.
        clients: Pipeline I/O dependencies.

    Returns:
        Final response or error.
    """
    confirmation_shortcut = await _handle_dynamic_confirmation_shortcut(request, clients)
    if confirmation_shortcut is not None:
        return confirmation_shortcut

    tables: list[TableMetadata] = []

    if request.previous_tables_json:
        try:
            tables_data = json.loads(request.previous_tables_json)
            tables = [TableMetadata.model_validate(t) for t in tables_data]
            logger.info(
                "Re-using %d tables from previous query context",
                len(tables),
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Failed to parse previous tables JSON: %s", exc)

    if not tables:
        clients.reporter.step_start("Searching tables")
        table_result = await clients.table_search.search(request.user_query)
        clients.reporter.step_end("Searching tables")
        if table_result.get("has_matches") and table_result.get("tables"):
            tables = [TableMetadata.model_validate(t) for t in table_result["tables"]]

    if not tables:
        return NL2SQLResponse(
            sql_query="",
            error=("Unable to refine query \u2014 no table metadata available."),
            query_source="dynamic",
        )

    enriched_query = (
        "Modify this previous query based on the user's request."
        "\n\n"
        f"Previous question: {request.previous_question}\n"
        f"Previous SQL: {request.previous_sql}\n\n"
        f"User's refinement request: {request.user_query}\n\n"
        "Generate a new SQL query that applies the user's requested "
        "changes to the previous query."
    )

    builder_req = QueryBuilderRequest(
        user_query=enriched_query,
        tables=tables,
    )

    thread = _build_agent_session(clients.query_builder_agent, clients.conversation_id)
    draft = await build_query(
        builder_req,
        clients.query_builder_agent,
        thread,
        clients.reporter,
    )

    if draft.status != "success":
        return NL2SQLResponse(
            sql_query="",
            error=draft.error or "Query generation failed",
            query_source="dynamic",
        )

    draft = validate_query(draft, set(clients.allowed_tables))
    if draft.query_violations:
        return await _retry_dynamic_query(draft, tables, request, clients)

    # Skip confidence gate for refinements
    return await _execute_and_respond(draft, clients)


async def _handle_dynamic_confirmation_shortcut(
    request: NL2SQLRequest,
    clients: PipelineClients,
) -> NL2SQLResponse | None:
    """Handle pending dynamic confirmation accept/reprompt shortcuts.

    Returns an ``NL2SQLResponse`` when a shortcut path was taken,
    otherwise ``None`` to continue normal dynamic refinement.
    """
    if request.previous_sql and request.reprompt_pending_confirmation:
        logger.info("Pending dynamic confirmation not resolved; re-prompting confirmation gate")
        reprompt_draft = SQLDraft(
            status="success",
            source="dynamic",
            completed_sql=request.previous_sql,
            user_query=request.previous_question or request.user_query,
            reasoning=(
                "Please confirm this query before I run it. "
                "Use Run this query, or tell me what to revise."
            ),
            tables_used=request.previous_tables or [],
            tables_metadata_json=request.previous_tables_json,
            confidence=max(_DYNAMIC_CONFIDENCE_THRESHOLD - 0.01, 0.0),
        )
        return _confidence_gate_response(reprompt_draft)

    if request.previous_sql and request.confirm_previous_sql:
        logger.info("Accepted pending dynamic confirmation; executing previous SQL directly")
        direct_draft = SQLDraft(
            status="success",
            source="dynamic",
            completed_sql=request.previous_sql,
            user_query=request.previous_question or request.user_query,
            tables_used=request.previous_tables or [],
            tables_metadata_json=request.previous_tables_json,
            confidence=_DYNAMIC_CONFIDENCE_THRESHOLD,
        )
        direct_draft = validate_query(direct_draft, set(clients.allowed_tables))
        if direct_draft.query_violations:
            error_msg, suggestions = build_error_recovery(
                direct_draft.query_violations,
                direct_draft.tables_used,
            )
            return NL2SQLResponse(
                sql_query="",
                error=error_msg,
                query_source="dynamic",
                error_suggestions=suggestions,
            )
        return await _execute_and_respond(direct_draft, clients)

    return None


# ── Scenario baseline configuration ──────────────────────────────────────

# Maps scenario_type → (sql, metric_key, dimension_key, date_column).
# These are internally-constructed trusted queries executed directly.
_SCENARIO_BASELINE_CONFIG: dict[str, tuple[str, str, str, str | None]] = {
    "price_delta": (
        "SELECT sg.StockGroupName, "
        "SUM(il.ExtendedPrice) AS Revenue "
        "FROM Sales.InvoiceLines il "
        "JOIN Warehouse.StockItems si "
        "ON il.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "GROUP BY sg.StockGroupName "
        "ORDER BY Revenue DESC",
        "Revenue",
        "StockGroupName",
        None,
    ),
    "demand_delta": (
        "SELECT sg.StockGroupName, "
        "SUM(ol.Quantity) AS Units "
        "FROM Sales.OrderLines ol "
        "JOIN Warehouse.StockItems si "
        "ON ol.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "GROUP BY sg.StockGroupName "
        "ORDER BY Units DESC",
        "Units",
        "StockGroupName",
        None,
    ),
    "supplier_cost_delta": (
        "SELECT sc.SupplierCategoryName, "
        "SUM(pol.ExpectedUnitPricePerOuter "
        "* pol.OrderedOuters) AS Cost "
        "FROM Purchasing.PurchaseOrderLines pol "
        "JOIN Purchasing.PurchaseOrders po "
        "ON pol.PurchaseOrderID = po.PurchaseOrderID "
        "JOIN Purchasing.Suppliers s "
        "ON po.SupplierID = s.SupplierID "
        "JOIN Purchasing.SupplierCategories sc "
        "ON s.SupplierCategoryID = sc.SupplierCategoryID "
        "GROUP BY sc.SupplierCategoryName "
        "ORDER BY Cost DESC",
        "Cost",
        "SupplierCategoryName",
        None,
    ),
    "inventory_policy_delta": (
        "SELECT sg.StockGroupName, "
        "SUM(sih.QuantityOnHand) AS Units "
        "FROM Warehouse.StockItemHoldings sih "
        "JOIN Warehouse.StockItems si "
        "ON sih.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "GROUP BY sg.StockGroupName "
        "ORDER BY Units DESC",
        "Units",
        "StockGroupName",
        None,
    ),
}


# ── Drill-down baseline configuration ────────────────────────────────────

# Maps scenario_type → (sql_template, metric_key, dimension_key, date_column).
# SQL contains a ``?`` placeholder for the parent group name (bind-parameter).
# These break the parent category into individual items within that group.
_SCENARIO_DRILLDOWN_CONFIG: dict[str, tuple[str, str, str, str | None]] = {
    "price_delta": (
        "SELECT si.StockItemName, "
        "SUM(il.ExtendedPrice) AS Revenue "
        "FROM Sales.InvoiceLines il "
        "JOIN Warehouse.StockItems si "
        "ON il.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "WHERE sg.StockGroupName = ? "
        "GROUP BY si.StockItemName "
        "ORDER BY Revenue DESC",
        "Revenue",
        "StockItemName",
        None,
    ),
    "demand_delta": (
        "SELECT si.StockItemName, "
        "SUM(ol.Quantity) AS Units "
        "FROM Sales.OrderLines ol "
        "JOIN Warehouse.StockItems si "
        "ON ol.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "WHERE sg.StockGroupName = ? "
        "GROUP BY si.StockItemName "
        "ORDER BY Units DESC",
        "Units",
        "StockItemName",
        None,
    ),
    "supplier_cost_delta": (
        "SELECT s.SupplierName, "
        "SUM(pol.ExpectedUnitPricePerOuter "
        "* pol.OrderedOuters) AS Cost "
        "FROM Purchasing.PurchaseOrderLines pol "
        "JOIN Purchasing.PurchaseOrders po "
        "ON pol.PurchaseOrderID = po.PurchaseOrderID "
        "JOIN Purchasing.Suppliers s "
        "ON po.SupplierID = s.SupplierID "
        "JOIN Purchasing.SupplierCategories sc "
        "ON s.SupplierCategoryID = sc.SupplierCategoryID "
        "WHERE sc.SupplierCategoryName = ? "
        "GROUP BY s.SupplierName "
        "ORDER BY Cost DESC",
        "Cost",
        "SupplierName",
        None,
    ),
    "inventory_policy_delta": (
        "SELECT si.StockItemName, "
        "SUM(sih.QuantityOnHand) AS Units "
        "FROM Warehouse.StockItemHoldings sih "
        "JOIN Warehouse.StockItems si "
        "ON sih.StockItemID = si.StockItemID "
        "JOIN Warehouse.StockItemStockGroups sisg "
        "ON si.StockItemID = sisg.StockItemID "
        "JOIN Warehouse.StockGroups sg "
        "ON sisg.StockGroupID = sg.StockGroupID "
        "WHERE sg.StockGroupName = ? "
        "GROUP BY si.StockItemName "
        "ORDER BY Units DESC",
        "Units",
        "StockItemName",
        None,
    ),
}


def detect_sparse_signal(
    rows: list[dict[str, Any]],
    date_column: str | None = None,
) -> list[str]:
    """Check for sparse-signal conditions (FR-010, SC-009).

    Returns human-readable limitation descriptions when the
    baseline data is too thin for reliable scenario analysis.

    Args:
        rows: Baseline query result rows.
        date_column: Optional column name containing date values.

    Returns:
        List of limitation descriptions (empty when data is
        sufficient).
    """
    limitations: list[str] = []

    if len(rows) < MIN_BASELINE_ROWS:
        limitations.append(
            f"Baseline contains {len(rows)} group(s) "
            f"(minimum {MIN_BASELINE_ROWS} required for "
            "meaningful comparison)"
        )

    if date_column and rows:
        weeks: set[tuple[int, int]] = set()
        for row in rows:
            date_val = row.get(date_column)
            if date_val is None:
                continue
            dt: datetime | None = None
            if isinstance(date_val, str):
                try:
                    dt = datetime.fromisoformat(date_val)
                except ValueError:
                    continue
            elif isinstance(date_val, datetime):
                dt = date_val
            if dt:
                iso = dt.isocalendar()
                weeks.add((iso[0], iso[1]))

        if len(weeks) < MIN_DISTINCT_WEEKLY_PERIODS:
            limitations.append(
                f"Data covers {len(weeks)} weekly periods "
                f"(minimum {MIN_DISTINCT_WEEKLY_PERIODS} "
                "required)"
            )

    return limitations


def _detect_date_column(columns: list[str]) -> str | None:
    """Find a date-like column by name pattern."""
    for col in columns:
        if any(kw in col.lower() for kw in ("date", "time", "period")):
            return col
    return None


def _extract_pct_from_query(user_query: str) -> float | None:
    """Extract a percentage value from a user query string.

    Handles common English phrasing such as ``"5%"``,
    ``"+5%"``, ``"5 percent"``.  Adjusts sign when negative
    indicator words are present.

    Args:
        user_query: Raw user message.

    Returns:
        Extracted percentage or ``None``.
    """
    patterns = [
        r"([+-]?\d+(?:\.\d+)?)\s*%",
        r"([+-]?\d+(?:\.\d+)?)\s*percent",
    ]
    for pattern in patterns:
        m = re.search(pattern, user_query, re.IGNORECASE)
        if m:
            value = float(m.group(1))
            neg = r"\b(decrease|drop|reduce|lower|decline|cut)\b"
            if re.search(neg, user_query, re.IGNORECASE) and value > 0:
                value = -value
            return value
    return None


def build_visualization_payload(
    result: ScenarioComputationResult,
    dimension_key: str,
    scenario_type: str,
) -> ScenarioVisualizationPayload:
    """Transform a computation result into a chart-ready payload.

    Args:
        result: Computed scenario metrics.
        dimension_key: X-axis grouping key name.
        scenario_type: Scenario category (used for chart_type).

    Returns:
        ``ScenarioVisualizationPayload`` ready for frontend
        rendering.
    """
    chart_type: str = "bar"
    if scenario_type in {"demand_delta", "inventory_policy_delta"}:
        chart_type = "bar"

    series = [
        ChartSeriesDefinition(
            key="baseline",
            label="Baseline",
            kind="baseline",
        ),
        ChartSeriesDefinition(
            key="scenario",
            label="Scenario",
            kind="scenario",
        ),
    ]

    rows: list[dict[str, str | int | float | bool | None]] = [
        {
            dimension_key: m.dimension_key,
            "baseline": m.baseline,
            "scenario": m.scenario,
        }
        for m in result.metrics
    ]

    friendly_dim = dimension_key.replace("_", " ").title()
    labels = {
        "baseline": "Current (Baseline)",
        "scenario": "Projected (Scenario)",
        dimension_key: friendly_dim,
    }

    return ScenarioVisualizationPayload(
        chart_type=chart_type,
        x_key=dimension_key,
        series=series,
        rows=rows,
        labels=labels,
    )


def _compute_summary_totals(
    metrics: list[ScenarioMetricValue],
    metric_key: str,
) -> dict[str, float]:
    """Build summary totals from computed scenario metrics."""
    total_baseline = sum(m.baseline for m in metrics)
    total_scenario = sum(m.scenario for m in metrics)
    total_delta_abs = total_scenario - total_baseline
    total_delta_pct = compute_delta_pct(total_baseline, total_delta_abs)
    mk = metric_key.lower()
    return {
        f"total_{mk}_baseline": total_baseline,
        f"total_{mk}_scenario": total_scenario,
        "total_delta_abs": total_delta_abs,
        "total_delta_pct": total_delta_pct,
    }


# ── Scenario entry point ────────────────────────────────────────────────


def _detect_group_scope(
    user_query: str,
    group_names: list[str],
) -> str | None:
    """Detect if the user query targets a specific group by name.

    Performs case-insensitive substring matching of known group
    names against the user query.  Returns the matching group
    name or ``None`` when no scope is detected.
    """
    query_lower = user_query.lower()
    # Match longest names first to avoid partial matches
    for name in sorted(group_names, key=len, reverse=True):
        if name.lower() in query_lower:
            return name
    return None


async def _run_drilldown_query(
    scoped_group: str,
    assumption_set: ScenarioAssumptionSet,
    pct_delta: float | None,
    abs_delta: float | None,
    clients: PipelineClients,
) -> NL2SQLResponse | None:
    """Execute an item-level drill-down query for a scoped group.

    Runs a parameterised SQL query that breaks the parent-level
    group into individual items, computes scenario metrics per
    item, and returns results without further drill-down hints.

    Returns ``None`` if no drill-down config exists for the
    scenario type (falls back to parent behaviour).
    """
    drilldown_config = _SCENARIO_DRILLDOWN_CONFIG.get(assumption_set.scenario_type)
    if not drilldown_config:
        return None

    dd_sql, dd_metric, dd_dimension, dd_date_col = drilldown_config

    clients.reporter.step_start("Retrieving item-level data")
    sql_result = await clients.sql_executor.execute(dd_sql, [scoped_group])
    clients.reporter.step_end("Retrieving item-level data")

    if not sql_result.get("success"):
        return NL2SQLResponse(
            sql_query=dd_sql,
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            error=sql_result.get("error", "Drill-down query failed"),
        )

    rows: list[dict[str, Any]] = sql_result.get("rows", [])
    columns: list[str] = sql_result.get("columns", [])

    if not rows:
        return NL2SQLResponse(
            sql_query=dd_sql,
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            error=f"No item-level data for {scoped_group}",
        )

    if not dd_date_col:
        dd_date_col = _detect_date_column(columns)
    data_limitations = detect_sparse_signal(rows, dd_date_col)

    aggregates = aggregate_baseline(rows, dd_metric, dd_dimension)
    metrics = compute_scenario_metrics(
        aggregates,
        dd_metric,
        pct_delta=pct_delta,
        abs_delta=abs_delta,
    )
    metrics = _limit_to_top_n(metrics, dd_metric)

    summary_totals = _compute_summary_totals(metrics, dd_metric)
    computation_result = ScenarioComputationResult(
        request_id=str(uuid.uuid4()),
        scenario_type=assumption_set.scenario_type,
        metrics=metrics,
        summary_totals=summary_totals,
        data_limitations=data_limitations,
    )
    viz = build_visualization_payload(
        computation_result,
        dd_dimension,
        assumption_set.scenario_type,
    )
    narrative = build_narrative_summary(computation_result)

    return NL2SQLResponse(
        sql_query=dd_sql,
        sql_response=rows,
        columns=columns,
        row_count=len(rows),
        is_scenario=True,
        scenario_type=assumption_set.scenario_type,
        scenario_assumptions=(assumption_set.assumptions or None),
        scenario_result=computation_result,
        scenario_narrative=narrative,
        scenario_visualization=viz,
        scenario_hints=None,
    )


def _limit_to_top_n(
    metrics: list[ScenarioMetricValue],
    metric_key: str,
) -> list[ScenarioMetricValue]:
    """Keep top N metrics by baseline and bucket the rest as 'Other'."""
    metrics.sort(key=operator.attrgetter("baseline"), reverse=True)
    if len(metrics) <= MAX_SCENARIO_CHART_ITEMS:
        return metrics
    top = metrics[:MAX_SCENARIO_CHART_ITEMS]
    rest = metrics[MAX_SCENARIO_CHART_ITEMS:]
    other_baseline = sum(m.baseline for m in rest)
    other_scenario = sum(m.scenario for m in rest)
    other_delta_abs = other_scenario - other_baseline
    other_delta_pct = compute_delta_pct(other_baseline, other_delta_abs)
    top.append(
        ScenarioMetricValue(
            metric=metric_key,
            dimension_key="Other",
            baseline=other_baseline,
            scenario=other_scenario,
            delta_abs=other_delta_abs,
            delta_pct=other_delta_pct,
        )
    )
    return top


def _build_scenario_hints(
    assumption_set: ScenarioAssumptionSet,
) -> list[PromptHint]:
    """Build prompt hints based on assumption completeness."""
    hints: list[PromptHint] = []

    if not assumption_set.is_complete and assumption_set.missing_requirements:
        hints.append(
            build_clarification_hint(
                assumption_set.missing_requirements,
                assumption_set.scenario_type,
            )
        )

    return hints


async def process_scenario_query(
    scenario_intent: ScenarioIntent,
    assumption_set: ScenarioAssumptionSet,
    user_query: str,
    clients: PipelineClients,
) -> NL2SQLResponse:
    """Process a scenario (what-if) query with full computation.

    Executes a baseline SQL query, checks for sparse-signal
    conditions, applies assumption transforms, and assembles
    structured scenario results with visualization data.

    Args:
        scenario_intent: Classified intent with confidence metadata.
        assumption_set: Assumptions from the user prompt.
        user_query: The original user message.
        clients: Pipeline I/O dependencies.

    Returns:
        ``NL2SQLResponse`` with scenario computation fields
        populated.
    """
    logger.info(
        "Scenario query: confidence=%.3f patterns=%s type=%s complete=%s query=%.80s",
        scenario_intent.confidence,
        scenario_intent.detected_patterns,
        assumption_set.scenario_type,
        assumption_set.is_complete,
        user_query,
    )

    clients.reporter.step_start("Processing scenario request")

    # 0. Resolve baseline config for the scenario type
    config = _SCENARIO_BASELINE_CONFIG.get(
        assumption_set.scenario_type,
    )
    if not config:
        clients.reporter.step_end("Processing scenario request")
        return NL2SQLResponse(
            sql_query="",
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            error=(f"Unsupported scenario type: {assumption_set.scenario_type}"),
        )

    baseline_sql, metric_key, dimension_key, date_col = config

    # 1. Determine assumption values before deciding whether to proceed
    pct_delta: float | None = None
    abs_delta: float | None = None

    for assumption in assumption_set.assumptions:
        if assumption.unit == "pct":
            pct_delta = assumption.value
            break
        if assumption.unit in {"absolute", "days", "count"}:
            abs_delta = assumption.value
            break

    # Fallback: extract percentage from user query text
    if pct_delta is None and abs_delta is None:
        pct_delta = _extract_pct_from_query(user_query)

    # 2. If no values could be extracted, return clarification hints and wait
    if pct_delta is None and abs_delta is None:
        scenario_hints = _build_scenario_hints(assumption_set)
        clients.reporter.step_end("Processing scenario request")
        return NL2SQLResponse(
            sql_query="",
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            scenario_hints=scenario_hints or None,
        )

    # 3. Execute baseline query
    clients.reporter.step_start("Retrieving baseline data")
    sql_result = await clients.sql_executor.execute(baseline_sql)
    clients.reporter.step_end("Retrieving baseline data")

    if not sql_result.get("success"):
        clients.reporter.step_end("Processing scenario request")
        return NL2SQLResponse(
            sql_query=baseline_sql,
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            error=sql_result.get("error", "Baseline query failed"),
        )

    baseline_rows: list[dict[str, Any]] = sql_result.get("rows", [])
    columns: list[str] = sql_result.get("columns", [])

    if not baseline_rows:
        clients.reporter.step_end("Processing scenario request")
        return NL2SQLResponse(
            sql_query=baseline_sql,
            is_scenario=True,
            scenario_type=assumption_set.scenario_type,
            error="No baseline data for scenario analysis",
        )

    # 4. Detect date column and check sparse signal (T047)
    if not date_col:
        date_col = _detect_date_column(columns)
    data_limitations = detect_sparse_signal(
        baseline_rows,
        date_col,
    )

    # 5. Aggregate baseline by dimension
    aggregates = aggregate_baseline(
        baseline_rows,
        metric_key,
        dimension_key,
    )

    # 5b. Detect group scope — when user targets a specific group
    #     (e.g. a drill-down click), run item-level query instead.
    scoped_group = _detect_group_scope(user_query, list(aggregates.keys()))
    if scoped_group:
        result = await _run_drilldown_query(
            scoped_group,
            assumption_set,
            pct_delta,
            abs_delta,
            clients,
        )
        if result is not None:
            clients.reporter.step_end("Processing scenario request")
            return result

    # 6. Compute scenario metrics
    metrics = compute_scenario_metrics(
        aggregates,
        metric_key,
        pct_delta=pct_delta,
        abs_delta=abs_delta,
    )

    # 6b. Limit to top N groups, bucket remainder as "Other"
    metrics = _limit_to_top_n(metrics, metric_key)

    # 7. Summary totals (computed from ALL original metrics before bucketing)
    summary_totals = _compute_summary_totals(metrics, metric_key)

    # 8. Assemble ScenarioComputationResult (T023)
    computation_result = ScenarioComputationResult(
        request_id=str(uuid.uuid4()),
        scenario_type=assumption_set.scenario_type,
        metrics=metrics,
        summary_totals=summary_totals,
        data_limitations=data_limitations,
    )

    # 9. Build visualization payload (T024)
    viz = build_visualization_payload(
        computation_result,
        dimension_key,
        assumption_set.scenario_type,
    )

    # 10. Build narrative summary (T032)
    narrative = build_narrative_summary(computation_result)

    clients.reporter.step_end("Processing scenario request")

    # 11. Build drill-down hints for top-level queries
    return NL2SQLResponse(
        sql_query=baseline_sql,
        sql_response=baseline_rows,
        columns=columns,
        row_count=len(baseline_rows),
        is_scenario=True,
        scenario_type=assumption_set.scenario_type,
        scenario_assumptions=(assumption_set.assumptions or None),
        scenario_result=computation_result,
        scenario_narrative=narrative,
        scenario_visualization=viz,
        scenario_hints=[
            build_drill_down_hints(
                [m.dimension_key for m in metrics if m.dimension_key != "Other"],
                assumption_set.scenario_type,
                pct_delta if pct_delta is not None else (abs_delta or 0.0),
            )
        ],
    )


# ── Public entry point ───────────────────────────────────────────────────


async def process_query(
    request: NL2SQLRequest,
    clients: PipelineClients,
) -> NL2SQLResponse | ClarificationRequest:
    """Run the full NL2SQL pipeline for a single user turn.

    Routing overview:

    1. **Refinements** — if ``request.is_refinement`` is set, re-uses
       the previous template or SQL context.
    2. **Template search** — looks for a high-confidence template match.
    3. **Template path** — extracts parameters, validates, executes.
    4. **Dynamic path** — generates SQL from table metadata when no
       template matches.

    All I/O is performed through ``clients``; no global state is read
    or written.

    Args:
        request: The user's question or refinement request.
        clients: Injectable I/O dependencies.

    Returns:
        ``NL2SQLResponse`` on success/error, or ``ClarificationRequest``
        when user input is needed.
    """
    try:
        # 1. Handle refinements
        if request.is_refinement:
            if request.previous_template_json:
                return await _handle_template_refinement(request, clients)
            if request.previous_sql:
                return await _handle_dynamic_refinement(request, clients)

        # 2. Search for matching template
        clients.reporter.step_start("Understanding intent")
        search_result = await clients.template_search.search(request.user_query)
        clients.reporter.step_end("Understanding intent")

        # 3. Route based on search result
        if search_result.get("has_high_confidence_match") and search_result.get("best_match"):
            template = QueryTemplate.model_validate(search_result["best_match"])
            logger.info(
                "High confidence template match: '%s' (score: %.3f, gap: %.3f)",
                template.intent,
                template.score,
                search_result.get("ambiguity_gap", 0.0),
            )
            return await _template_path(request, template, clients)

        if search_result.get("is_ambiguous"):
            logger.info(
                "Ambiguous template match (gap: %.3f < %.3f)",
                search_result.get("ambiguity_gap", 0),
                search_result.get("ambiguity_gap_threshold", 0.05),
            )
            return _ambiguous_response(search_result)

        # 4. No high-confidence match — dynamic query generation
        logger.info(
            "No high confidence match (score: %.3f). Attempting dynamic query generation.",
            search_result.get("confidence_score", 0),
        )
        return await _dynamic_path(request, search_result, clients)

    except (ValueError, RuntimeError, OSError, ValidationError) as exc:
        logger.exception("NL2SQL pipeline error: %s", type(exc).__name__)
        return NL2SQLResponse(
            sql_query="",
            error="An error occurred processing your query. Please try again.",
        )
