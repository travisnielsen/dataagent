"""Parameter extraction logic.

Extracts parameter values from user queries using deterministic
fuzzy matching (fast path) with LLM fallback. Reports progress
via the ``ProgressReporter`` protocol.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from agent_framework import Agent, AgentSession
from models import (
    MissingParameter,
    ParameterDefinition,
    ParameterExtractionRequest,
    ParameterValidation,
    QueryTemplate,
    SQLDraft,
)
from opentelemetry import baggage, trace
from shared.allowed_values_provider import AllowedValuesProvider
from shared.protocols import NoOpReporter, ProgressReporter

logger = logging.getLogger(__name__)

MIN_PARAM_NAME_LENGTH = 2


# ============================================================================
# Deterministic Fuzzy Matching (Step 1 - before LLM)
# ============================================================================


def _fuzzy_match_allowed_value(
    user_query: str,
    allowed_values: list[str],
) -> str | None:
    """Try to match user query words to an allowed value.

    Returns the matched value or None if no confident match.
    """
    query_lower = user_query.lower()

    for allowed in allowed_values:
        allowed_lower = allowed.lower()

        # Exact match (case-insensitive)
        if allowed_lower in query_lower:
            return allowed

        # Pluralization: "supermarkets" -> "Supermarket"
        if allowed_lower + "s" in query_lower:
            return allowed

        # Word stems: "Computer Store" matches "computers"
        words = allowed_lower.split()
        if words:
            first_word = words[0]
            if first_word + "s" in query_lower:
                return allowed
            # Also check without trailing 's' in query: "novelty" -> "Novelty Shop"
            if first_word in query_lower:
                return allowed

    return None


# ============================================================================
# Confidence Scoring
# ============================================================================

# Maps resolution method to base confidence score (from plan.md D1)
_RESOLUTION_CONFIDENCE: dict[str, float] = {
    "exact_match": 1.0,
    "fuzzy_match": 0.85,
    "llm_validated": 0.75,
    "default_value": 0.7,
    "default_policy": 0.7,
    "llm_unvalidated": 0.65,
    "llm_failed_validation": 0.3,
}

# Minimum effective confidence floor per resolution method.
# Deterministic resolutions should never trigger clarification regardless
# of the template's confidence_weight — the weight should primarily
# penalise LLM-based extractions where ambiguity is real.
_RESOLUTION_MIN_CONFIDENCE: dict[str, float] = {
    "exact_match": 0.85,
    "fuzzy_match": 0.6,
    "default_value": 0.6,
    "default_policy": 0.6,
}


def _compute_confidence(resolution_method: str, confidence_weight: float) -> float:
    """Compute effective confidence for a resolved parameter.

    Effective confidence = base_confidence * max(confidence_weight, 0.3).

    Args:
        resolution_method: How the value was resolved (e.g. "exact_match").
        confidence_weight: Per-parameter weight from ParameterDefinition.

    Returns:
        Effective confidence score (0.0-1.0).

    Raises:
        ValueError: If resolution_method is not recognised.
    """
    base = _RESOLUTION_CONFIDENCE.get(resolution_method)
    if base is None:
        raise ValueError(
            f"Unknown resolution method: {resolution_method!r}. "
            f"Valid methods: {sorted(_RESOLUTION_CONFIDENCE)}"
        )
    effective = base * max(confidence_weight, 0.3)
    floor = _RESOLUTION_MIN_CONFIDENCE.get(resolution_method, 0.0)
    return max(effective, floor)


def _has_validation_rules(param: ParameterDefinition) -> bool:
    """Check whether a parameter definition has any validation rules."""
    if not param.validation:
        return False
    v = param.validation
    return bool(v.allowed_values or v.min is not None or v.max is not None or v.regex)


def _value_passes_validation(
    value: str | int | float,
    param: ParameterDefinition,
) -> bool:
    """Check if a value passes a parameter's validation rules.

    Returns True when there are no validation rules or
    all applicable rules pass.
    """
    if not param.validation:
        return True
    v = param.validation

    # Check allowed_values
    if v.allowed_values:
        str_val = str(value).lower()
        if not any(a.lower() == str_val for a in v.allowed_values):
            return False

    # Check min/max for numeric types
    if v.type == "integer":
        try:
            num = int(value)
        except (ValueError, TypeError):
            return False
        in_range = (v.min is None or num >= int(v.min)) and (v.max is None or num <= int(v.max))
        if not in_range:
            return False

    # Check regex
    return not (v.regex and not re.search(v.regex, str(value)))


def _build_parameter_confidences(
    resolution_methods: dict[str, str],
    template: QueryTemplate,
) -> dict[str, float]:
    """Compute per-parameter confidence scores.

    Args:
        resolution_methods: Mapping of param name -> resolution method.
        template: The query template (used for confidence_weight lookup).

    Returns:
        Mapping of param name -> effective confidence score.
    """
    weight_by_name = {p.name: p.confidence_weight for p in template.parameters}
    return {
        name: _compute_confidence(method, weight_by_name.get(name, 1.0))
        for name, method in resolution_methods.items()
    }


def _extract_number_from_query(user_query: str, param_name: str) -> int | None:
    """Extract a number from the user query for count-type parameters.

    Handles patterns like "top 5", "first 10", "last 30 days".
    """
    query_lower = user_query.lower()

    # Common patterns for counts
    patterns = [
        r"top\s+(\d+)",
        r"first\s+(\d+)",
        r"last\s+(\d+)",
        r"(\d+)\s+" + param_name.replace("_", r"\s*"),
    ]

    for pattern in patterns:
        match = re.search(pattern, query_lower)
        if match:
            return int(match.group(1))

    return None


class ExtractionResult:
    """Result of parameter extraction with tracking for defaults and resolution methods."""

    def __init__(self) -> None:
        self.extracted: dict[str, Any] = {}
        self.defaults_used: dict[str, Any] = {}
        self.resolution_methods: dict[str, str] = {}


def _pre_extract_parameters(
    user_query: str,
    template: QueryTemplate,
) -> ExtractionResult:
    """Deterministically extract parameters before calling LLM.

    Returns ExtractionResult with extracted values, defaults used,
    and resolution methods for each resolved parameter.
    """
    result = ExtractionResult()

    for param in template.parameters:
        # Try to match allowed_values
        if param.validation and param.validation.allowed_values:
            match = _fuzzy_match_allowed_value(user_query, param.validation.allowed_values)
            if match:
                result.extracted[param.name] = match
                # Determine if exact or fuzzy
                query_lower = user_query.lower()
                if match.lower() in query_lower:
                    result.resolution_methods[param.name] = "exact_match"
                else:
                    result.resolution_methods[param.name] = "fuzzy_match"
                continue

        # Try to extract numbers for integer params
        if param.validation and param.validation.type == "integer":
            num = _extract_number_from_query(user_query, param.name)
            if num is not None:
                # Validate against min/max (cast to int for comparison)
                min_val = param.validation.min
                max_val = param.validation.max
                if min_val is not None and num < int(min_val):
                    continue
                if max_val is not None and num > int(max_val):
                    continue
                result.extracted[param.name] = num
                result.resolution_methods[param.name] = "exact_match"
                continue

        # Apply default_policy if available
        if param.default_policy is not None and param.name not in result.extracted:
            result.extracted[param.name] = param.default_policy
            result.defaults_used[param.name] = param.default_policy
            result.resolution_methods[param.name] = "default_policy"
            continue

        # Apply default_value if available
        if param.default_value is not None and param.name not in result.extracted:
            result.extracted[param.name] = param.default_value
            result.defaults_used[param.name] = param.default_value
            result.resolution_methods[param.name] = "default_value"

    return result


def _all_required_params_satisfied(
    extracted: dict[str, Any],
    template: QueryTemplate,
) -> bool:
    """Check if all required parameters are satisfied."""
    for param in template.parameters:
        if param.required and param.name not in extracted and param.default_value is None:
            return False
    return True


# ============================================================================
# Prompt Building and LLM Response Parsing
# ============================================================================


def _build_extraction_prompt(user_query: str, template: QueryTemplate) -> str:
    """Build a compact prompt for the LLM to extract parameters.

    Args:
        user_query: The user's original question.
        template: The matched query template.

    Returns:
        A formatted prompt string for the LLM.
    """
    # Calculate adjusted reference date (12 years ago for historical data)
    adjusted_date = datetime.now() - timedelta(days=12 * 365)
    adjusted_date_str = adjusted_date.strftime("%Y-%m-%d")

    # Format parameters compactly - only include what's needed for extraction
    params_info: list[dict[str, Any]] = []
    for param in template.parameters:
        param_desc: dict[str, Any] = {
            "name": param.name,
            "required": param.required,
            "ask_if_missing": param.ask_if_missing,
        }
        if param.default_value is not None:
            param_desc["default"] = param.default_value
        if param.validation:
            v = param.validation
            if v.allowed_values:
                param_desc["allowed_values"] = v.allowed_values
            if v.min is not None:
                param_desc["min"] = v.min
            if v.max is not None:
                param_desc["max"] = v.max
        params_info.append(param_desc)

    return (
        f"Question: {user_query}\n"
        f"Reference date: {adjusted_date_str}\n"
        f"Parameters: {json.dumps(params_info)}\n"
        "\n"
        "Extract parameter values. Respond with JSON only."
    )


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse the LLM's JSON response.

    Args:
        response_text: The raw text response from the LLM.

    Returns:
        Parsed dictionary from the JSON response.
    """
    text = response_text.strip()

    # Try direct JSON parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code fence
    if "```json" in text:
        try:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try to find any JSON object in the response
    json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Return error structure if we can't parse
    return {"status": "error", "error": f"Failed to parse LLM response: {text[:200]}"}


# ============================================================================
# Database Allowed-Values Hydration
# ============================================================================


async def _hydrate_database_allowed_values(
    template: QueryTemplate,
    provider: AllowedValuesProvider,
) -> set[str]:
    """Hydrate allowed values from DB. Returns set of partial-cache param names.

    Iterates ``template.parameters`` and, for each with
    ``allowed_values_source == "database"``, fetches distinct values via
    the ``AllowedValuesProvider``.  Results are written directly into
    ``param.validation.allowed_values`` so that downstream prompt-building
    and fuzzy-matching work transparently.

    Args:
        template: The query template whose parameters may need hydration.
        provider: Provider that fetches allowed values from the database.

    Returns:
        Set of parameter names whose caches are partial (capped).
    """
    partial_cache_params: set[str] = set()

    for param in template.parameters:
        if param.allowed_values_source != "database" or param.table is None or param.column is None:
            continue

        result = await provider.get_allowed_values(param.table, param.column)
        if result is None:
            logger.warning(
                "Could not load allowed values for %s.%s — falling back to LLM-only",
                param.table,
                param.column,
            )
            continue

        if param.validation is None:
            param.validation = ParameterValidation(type="string", allowed_values=result.values)
        else:
            param.validation.allowed_values = result.values

        if result.is_partial:
            partial_cache_params.add(param.name)
            logger.info(
                "Param '%s' has partial cache (%d values) — will skip strict validation",
                param.name,
                len(result.values),
            )

    return partial_cache_params


# ============================================================================
# Main extraction entry-point
# ============================================================================


async def extract_parameters(
    request: ParameterExtractionRequest,
    agent: Agent,
    thread: AgentSession,
    reporter: ProgressReporter = NoOpReporter(),
    *,
    allowed_values_provider: AllowedValuesProvider | None = None,
) -> SQLDraft:
    """Extract parameter values from a user query.

    Runs deterministic fuzzy matching first (fast path). Falls back to
    the LLM when deterministic extraction cannot satisfy all required
    parameters.

    Args:
        request: Extraction request containing template and user query.
        agent: Agent used for LLM fallback.
        thread: Existing AgentSession for the LLM conversation.
        reporter: Progress reporter for streaming UI updates.
        allowed_values_provider: Optional provider for database-sourced
            allowed values.

    Returns:
        An ``SQLDraft`` with status ``"success"``,
        ``"needs_clarification"``, or ``"error"``.
    """
    reporter.step_start("Extracting parameters")

    tracer = trace.get_tracer(__name__)
    template = request.template
    with tracer.start_as_current_span("parameter_extraction") as span:
        conv_id = baggage.get_baggage("gen_ai.conversation.id")
        if conv_id:
            span.set_attribute("gen_ai.conversation.id", str(conv_id))
        span.set_attribute("template.id", template.id or "")
        span.set_attribute("template.intent", template.intent or "")
        span.set_attribute(
            "template.required_param_count", sum(1 for p in template.parameters if p.required)
        )
        span.set_attribute("template.total_param_count", len(template.parameters))
        span.set_attribute("extraction.user_query_length", len(request.user_query))
        span.set_attribute(
            "extraction.has_previously_extracted",
            bool(request.previously_extracted),
        )
        try:
            draft = await _extract_parameters_inner(
                request,
                agent,
                thread,
                allowed_values_provider=allowed_values_provider,
            )
        except Exception as exc:
            logger.exception("Parameter extraction error")
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc)[:200])
            reporter.step_end("Extracting parameters")
            return SQLDraft(status="error", source="template", error=str(exc))

        span.set_attribute("extraction.status", draft.status)
        span.set_attribute("extraction.source", draft.source or "")
        span.set_attribute("extraction.param_count", len(draft.extracted_parameters or {}))
        span.set_attribute("extraction.defaults_used_count", len(draft.defaults_used or []))
        # LLM was skipped when all required params were resolved deterministically.
        span.set_attribute(
            "extraction.llm_invoked",
            not _all_required_params_satisfied(draft.extracted_parameters or {}, template)
            or draft.source != "template",
        )
        if draft.parameter_confidences:
            span.set_attribute(
                "extraction.min_confidence",
                min(draft.parameter_confidences.values()),
            )
            span.set_attribute(
                "extraction.max_confidence",
                max(draft.parameter_confidences.values()),
            )
        reporter.step_end("Extracting parameters")
        return draft


async def _extract_parameters_inner(
    request: ParameterExtractionRequest,
    agent: Agent,
    thread: AgentSession,
    *,
    allowed_values_provider: AllowedValuesProvider | None = None,
) -> SQLDraft:
    """Core extraction logic (no reporter handling).

    Separated so the outer function can guarantee step_start/step_end
    symmetry via try/finally.
    """
    template = request.template
    user_query = request.user_query

    # Hydrate database-sourced allowed values before extraction
    partial_cache_params: set[str] = set()
    if allowed_values_provider:
        partial_cache_params = await _hydrate_database_allowed_values(
            template, allowed_values_provider
        )

    logger.info(
        "Extracting parameters for template '%s' from query: %s",
        template.intent,
        user_query[:100],
    )

    # ================================================================
    # Step 1: Try deterministic fuzzy matching first (fast path)
    # ================================================================
    extraction_result = _pre_extract_parameters(user_query, template)
    extracted_params = extraction_result.extracted
    defaults_used = extraction_result.defaults_used

    # Merge previously extracted params (from prior clarification turns)
    # These take priority since the user already confirmed them.
    if request.previously_extracted:
        for pname, pval in request.previously_extracted.items():
            if pname not in extracted_params:
                extracted_params[pname] = pval
                extraction_result.resolution_methods[pname] = "exact_match"
                logger.info(
                    "  Preserved param '%s' -> '%s' from prior turn",
                    pname,
                    pval,
                )

    if extracted_params:
        logger.info(
            "Deterministic extraction found %d parameters:",
            len(extracted_params),
        )
        for param_name, param_value in extracted_params.items():
            is_default = " (default)" if param_name in defaults_used else ""
            logger.info(
                "  Parameter '%s' -> '%s'%s",
                param_name,
                param_value,
                is_default,
            )

    if _all_required_params_satisfied(extracted_params, template):
        logger.info("All required parameters satisfied via deterministic matching - skipping LLM")
        confidences = _build_parameter_confidences(extraction_result.resolution_methods, template)
        return SQLDraft(
            status="success",
            source="template",
            completed_sql=None,
            user_query=user_query,
            reasoning=template.reasoning,
            template_id=template.id,
            template_json=template.model_dump_json(),
            extracted_parameters=extracted_params,
            defaults_used=defaults_used,
            parameter_definitions=template.parameters,
            parameter_confidences=confidences,
            partial_cache_params=list(partial_cache_params),
        )

    # ================================================================
    # Step 2: Fall back to LLM for complex/ambiguous cases
    # ================================================================
    logger.info("Deterministic matching incomplete - falling back to LLM")

    extraction_prompt = _build_extraction_prompt(user_query, template)
    logger.info("Extraction prompt:\n%s", extraction_prompt)

    response = await agent.run(extraction_prompt, session=thread)

    # Get the response text
    response_text = ""
    for msg in response.messages:
        if hasattr(msg, "contents"):
            for content in msg.contents:
                text_value = getattr(content, "text", None)
                if text_value:
                    response_text = text_value
                    break
            if response_text:
                break

    logger.info(
        "LLM response: %s",
        response_text[:500] if response_text else "(empty)",
    )

    parsed = _parse_llm_response(response_text)
    logger.info(
        "Parsed response: status=%s, params=%s",
        parsed.get("status"),
        parsed.get("extracted_parameters"),
    )

    return _build_sql_draft_from_parsed(
        parsed=parsed,
        extraction_result=extraction_result,
        template=template,
        user_query=user_query,
        partial_cache_params=partial_cache_params,
    )


# ============================================================================
# Response building helpers
# ============================================================================


def _build_sql_draft_from_parsed(
    *,
    parsed: dict[str, Any],
    extraction_result: ExtractionResult,
    template: QueryTemplate,
    user_query: str,
    partial_cache_params: set[str],
) -> SQLDraft:
    """Build an ``SQLDraft`` from the parsed LLM response.

    Handles ``"success"``, ``"needs_clarification"``, and error statuses.
    """
    status = parsed.get("status")

    if status == "success":
        return _build_success_draft(
            parsed=parsed,
            extraction_result=extraction_result,
            template=template,
            user_query=user_query,
            partial_cache_params=partial_cache_params,
        )

    if status == "needs_clarification":
        return _build_clarification_draft(
            parsed=parsed,
            extraction_result=extraction_result,
            template=template,
            user_query=user_query,
            partial_cache_params=partial_cache_params,
        )

    return _build_error_draft(
        parsed=parsed,
        template=template,
        user_query=user_query,
        partial_cache_params=partial_cache_params,
    )


def _build_success_draft(
    *,
    parsed: dict[str, Any],
    extraction_result: ExtractionResult,
    template: QueryTemplate,
    user_query: str,
    partial_cache_params: set[str],
) -> SQLDraft:
    """Build an ``SQLDraft`` for a successful LLM extraction."""
    llm_extracted: dict[str, Any] = parsed.get("extracted_parameters", {})

    # Merge: deterministic extractions take priority
    merged_params = dict(extraction_result.extracted)
    merged_params.update({k: v for k, v in llm_extracted.items() if k not in merged_params})

    logger.info("Extracted %d parameters:", len(merged_params))
    for param_name, param_value in merged_params.items():
        logger.info("  Parameter '%s' -> '%s'", param_name, param_value)

    # Build param lookup for resolution method assignment
    param_defs_by_name: dict[str, ParameterDefinition] = {p.name: p for p in template.parameters}

    # Carry forward deterministic resolution methods
    resolution_methods = dict(extraction_result.resolution_methods)

    # Assign LLM resolution methods for new params
    for pname, pval in merged_params.items():
        if pname in resolution_methods:
            continue
        pdef = param_defs_by_name.get(pname)
        if pdef and _has_validation_rules(pdef):
            if _value_passes_validation(pval, pdef):
                resolution_methods[pname] = "llm_validated"
            else:
                resolution_methods[pname] = "llm_failed_validation"
        else:
            resolution_methods[pname] = "llm_unvalidated"

    # Check required ask_if_missing params were actually extracted
    missing_required = _find_missing_required_params(merged_params, template, user_query)

    confidences = _build_parameter_confidences(resolution_methods, template)

    if missing_required:
        logger.info(
            "Converting success to needs_clarification due to %d missing required params",
            len(missing_required),
        )
        return SQLDraft(
            status="needs_clarification",
            source="template",
            user_query=user_query,
            reasoning=template.reasoning,
            template_id=template.id,
            template_json=template.model_dump_json(),
            extracted_parameters=merged_params,
            parameter_definitions=template.parameters,
            missing_parameters=missing_required,
            clarification_prompt=(f"Please provide a value for: {missing_required[0].name}"),
            parameter_confidences=confidences,
            partial_cache_params=list(partial_cache_params),
        )

    return SQLDraft(
        status="success",
        source="template",
        completed_sql=None,
        user_query=user_query,
        reasoning=template.reasoning,
        template_id=template.id,
        template_json=template.model_dump_json(),
        extracted_parameters=merged_params,
        parameter_definitions=template.parameters,
        parameter_confidences=confidences,
        partial_cache_params=list(partial_cache_params),
    )


def _build_clarification_draft(
    *,
    parsed: dict[str, Any],
    extraction_result: ExtractionResult,
    template: QueryTemplate,
    user_query: str,
    partial_cache_params: set[str],
) -> SQLDraft:
    """Build an ``SQLDraft`` for a needs_clarification LLM response."""
    missing = [
        MissingParameter(
            name=mp.get("name", ""),
            description=mp.get("description", ""),
            validation_hint=mp.get("validation_hint", ""),
            best_guess=mp.get("best_guess"),
            guess_confidence=float(mp.get("guess_confidence", 0.0)),
            alternatives=mp.get("alternatives"),
        )
        for mp in parsed.get("missing_parameters", [])
    ]

    llm_extracted: dict[str, Any] = parsed.get("extracted_parameters") or {}
    merged_extracted = dict(extraction_result.extracted)
    merged_extracted.update({k: v for k, v in llm_extracted.items() if k not in merged_extracted})

    return SQLDraft(
        status="needs_clarification",
        source="template",
        user_query=user_query,
        reasoning=template.reasoning,
        template_id=template.id,
        template_json=template.model_dump_json(),
        extracted_parameters=merged_extracted or None,
        parameter_definitions=template.parameters,
        missing_parameters=missing,
        clarification_prompt=parsed.get("clarification_prompt"),
        partial_cache_params=list(partial_cache_params),
    )


def _build_error_draft(
    *,
    parsed: dict[str, Any],
    template: QueryTemplate,
    user_query: str,
    partial_cache_params: set[str],
) -> SQLDraft:
    """Build an ``SQLDraft`` for an error LLM response.

    Checks whether the error can be converted to a clarification
    request when a required ``ask_if_missing`` parameter is involved.
    """
    error_msg = parsed.get("error", "Unknown error during parameter extraction")

    # Check if error is about an ask_if_missing param
    should_clarify = False
    clarify_param: ParameterDefinition | None = None

    for param in template.parameters:
        if not (param.required and param.ask_if_missing):
            continue

        param_name_parts = param.name.lower().replace("_", " ").split()
        error_lower = error_msg.lower()

        matches_error = any(
            part in error_lower for part in param_name_parts if len(part) > MIN_PARAM_NAME_LENGTH
        )
        not_extracted = param.name not in parsed.get("extracted_parameters", {})

        if matches_error or not_extracted:
            should_clarify = True
            clarify_param = param
            logger.info(
                "Error mentions param '%s' or param not extracted "
                "(matches_error=%s, not_extracted=%s)",
                param.name,
                matches_error,
                not_extracted,
            )
            break

    if should_clarify and clarify_param:
        return _error_to_clarification(
            clarify_param=clarify_param,
            parsed=parsed,
            template=template,
            user_query=user_query,
            partial_cache_params=partial_cache_params,
        )

    return SQLDraft(
        status="error",
        source="template",
        user_query=user_query,
        template_id=template.id,
        template_json=template.model_dump_json(),
        parameter_definitions=template.parameters,
        error=error_msg,
    )


def _error_to_clarification(
    *,
    clarify_param: ParameterDefinition,
    parsed: dict[str, Any],
    template: QueryTemplate,
    user_query: str,
    partial_cache_params: set[str],
) -> SQLDraft:
    """Convert an error into a clarification request."""
    logger.info(
        "Converting error to clarification for parameter '%s' (ask_if_missing=true)",
        clarify_param.name,
    )

    clarify_allowed: list[str] = []
    if clarify_param.validation and hasattr(clarify_param.validation, "allowed_values"):
        clarify_allowed = clarify_param.validation.allowed_values or []

    best_guess: str | None = None
    guess_confidence = 0.0
    alternatives: list[str] | None = None
    if clarify_allowed:
        fuzzy = _fuzzy_match_allowed_value(user_query, clarify_allowed)
        if fuzzy:
            best_guess = fuzzy
            guess_confidence = 0.6
        remaining = [v for v in clarify_allowed if v != best_guess]
        alternatives = remaining[:5]

    missing = [
        MissingParameter(
            name=clarify_param.name,
            description=(f"Please select a valid value for '{clarify_param.name}'"),
            validation_hint=(
                f"Allowed values: {', '.join(clarify_allowed)}" if clarify_allowed else ""
            ),
            best_guess=best_guess,
            guess_confidence=guess_confidence,
            alternatives=alternatives,
        )
    ]

    clarification_prompt = (
        f"Please choose from: {', '.join(clarify_allowed)}"
        if clarify_allowed
        else f"Please provide a value for '{clarify_param.name}'"
    )

    return SQLDraft(
        status="needs_clarification",
        source="template",
        user_query=user_query,
        reasoning=template.reasoning,
        template_id=template.id,
        template_json=template.model_dump_json(),
        extracted_parameters=parsed.get("extracted_parameters", {}),
        parameter_definitions=template.parameters,
        missing_parameters=missing,
        clarification_prompt=clarification_prompt,
        partial_cache_params=list(partial_cache_params),
    )


def _find_missing_required_params(
    extracted_params: dict[str, Any],
    template: QueryTemplate,
    user_query: str,
) -> list[MissingParameter]:
    """Identify required ``ask_if_missing`` params not in *extracted_params*."""
    missing: list[MissingParameter] = []

    for param in template.parameters:
        if not (
            param.required
            and param.ask_if_missing
            and (param.name not in extracted_params or not extracted_params.get(param.name))
        ):
            continue

        param_allowed: list[str] = []
        if param.validation and hasattr(param.validation, "allowed_values"):
            param_allowed = param.validation.allowed_values or []

        best_guess: str | None = None
        guess_confidence = 0.0
        alternatives: list[str] | None = None
        if param_allowed:
            fuzzy = _fuzzy_match_allowed_value(user_query, param_allowed)
            if fuzzy:
                best_guess = fuzzy
                guess_confidence = 0.6
            remaining = [v for v in param_allowed if v != best_guess]
            alternatives = remaining[:5]

        missing.append(
            MissingParameter(
                name=param.name,
                description=f"Please provide a value for '{param.name}'",
                validation_hint=(
                    f"Allowed values: {', '.join(param_allowed)}" if param_allowed else ""
                ),
                best_guess=best_guess,
                guess_confidence=guess_confidence,
                alternatives=alternatives,
            )
        )
        logger.warning(
            "LLM returned success but required param '%s' (ask_if_missing=true) was not extracted",
            param.name,
        )

    return missing
