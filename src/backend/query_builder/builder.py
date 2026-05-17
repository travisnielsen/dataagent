"""Query builder logic.

Generates SQL queries from table metadata using LLM analysis.
Reports progress via the ``ProgressReporter`` protocol.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryAgent
from models import QueryBuilderRequest, SQLDraft, TableMetadata
from shared.protocols import NoOpReporter, ProgressReporter

logger = logging.getLogger(__name__)


def _looks_like_sql(value: str) -> bool:
    """Return True when text appears to contain a SQL SELECT/WITH statement."""
    return bool(re.search(r"(?is)\b(select|with)\b", value))


def _find_sql_in_payload(payload: object) -> str | None:
    """Recursively find SQL text in a parsed payload."""
    stack: list[object] = [payload]

    while stack:
        current = stack.pop()

        if isinstance(current, str):
            stripped = current.strip()
            if stripped and _looks_like_sql(stripped):
                return stripped
            continue

        if isinstance(current, list):
            stack.extend(current)
            continue

        if isinstance(current, dict):
            direct_keys = ("completed_sql", "sql_query", "sql", "query")
            for key in direct_keys:
                value = current.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            for key, value in current.items():
                if isinstance(value, str):
                    key_lower = str(key).lower()
                    if any(token in key_lower for token in ("sql", "query")) and _looks_like_sql(
                        value
                    ):
                        return value.strip()
                elif isinstance(value, (dict, list)):
                    stack.append(value)

    return None


def _extract_sql_from_text(text: str) -> str | None:
    """Best-effort extraction of SQL from plain-text model output."""
    # Prefer fenced SQL blocks first
    sql_fence = re.search(r"```sql\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if sql_fence:
        candidate = sql_fence.group(1).strip()
        if candidate:
            return candidate

    # Fallback: find first SELECT/WITH block in text
    statement_match = re.search(
        r"(?is)\b(select|with)\b[\s\S]*",
        text,
    )
    if statement_match:
        candidate = statement_match.group(0).strip()
        # Trim trailing markdown fence if present
        candidate = candidate.split("```")[0].strip()
        if candidate:
            return candidate

    return None


def _build_generation_prompt(user_query: str, tables: list[TableMetadata]) -> str:
    """Build the prompt for the LLM to generate a SQL query.

    Args:
        user_query: The user's original question.
        tables: List of relevant table metadata.

    Returns:
        A formatted prompt string for the LLM.
    """
    tables_info: list[dict[str, Any]] = []
    for table in tables:
        columns_info: list[dict[str, str | bool]] = []
        for col in table.columns:
            col_entry: dict[str, str | bool] = {
                "name": col.name,
                "description": col.description,
            }
            if col.data_type:
                col_entry["data_type"] = col.data_type
            if col.is_primary_key:
                col_entry["is_primary_key"] = True
            if col.is_foreign_key:
                col_entry["is_foreign_key"] = True
                if col.foreign_key_table:
                    col_entry["references"] = f"{col.foreign_key_table}.{col.foreign_key_column}"
            if not col.is_nullable:
                col_entry["nullable"] = False
            columns_info.append(col_entry)
        tables_info.append({
            "table": table.table,
            "description": table.description,
            "columns": columns_info,
        })

    return (
        "Generate a SQL query to answer the following user question.\n"
        "\n"
        "## User Question\n"
        f"{user_query}\n"
        "\n"
        "## Date Semantics\n"
        "The database is historical. For relative date requests (last N days/months/years, "
        "this month/year, recent), treat current date as DATEADD(YEAR, -10, GETDATE()) "
        "instead of GETDATE().\n"
        "\n"
        "## Available Tables\n"
        f"{json.dumps(tables_info, indent=2)}\n"
        "\n"
        "Analyze the user question and generate a valid SQL SELECT query "
        "using only the tables and columns provided above.\n"
        "Respond with a JSON object containing your query and reasoning.\n"
    )


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse the LLM's JSON response.

    Attempts direct JSON parsing, then markdown code-fence extraction,
    and finally a regex search for embedded JSON objects.

    Args:
        response_text: The raw text response from the LLM.

    Returns:
        Parsed dictionary from the JSON response.
    """
    text = response_text.strip()

    # Direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract from markdown code fence
    if "```json" in text:
        try:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

    # Extract from first '{' to last '}' (handles nested JSON objects)
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        try:
            return json.loads(text[obj_start : obj_end + 1])
        except json.JSONDecodeError:
            pass

    # Regex search for any JSON object
    json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return {"status": "error", "error": f"Failed to parse LLM response: {text[:200]}"}


async def build_query(
    request: QueryBuilderRequest,
    agent: Agent | FoundryAgent,
    thread: AgentSession,
    reporter: ProgressReporter = NoOpReporter(),
) -> SQLDraft:
    """Generate a SQL query from table metadata via LLM analysis.

    Builds a generation prompt from the request's tables and user query,
    invokes the LLM agent, then parses the response into an ``SQLDraft``.

    Args:
        request: Contains user_query, tables, and retry_count.
        agent: Chat agent configured with query-builder instructions.
        thread: Conversation thread to use for the LLM call.
        reporter: Progress reporter for streaming UI updates.

    Returns:
        An ``SQLDraft`` with status ``"success"`` or ``"error"``.
    """
    step_name = "Generating SQL"
    reporter.step_start(step_name)

    try:
        tables = request.tables
        user_query = request.user_query
        retry_count = request.retry_count

        logger.info(
            "Building query from %d tables for: %s (retry=%d)",
            len(tables),
            user_query[:100],
            retry_count,
        )

        generation_prompt = _build_generation_prompt(user_query, tables)

        response = await agent.run(generation_prompt, session=thread)

        # Extract response text from agent messages
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

        if not response_text:
            fallback_text = getattr(response, "text", "")
            response_text = fallback_text if isinstance(fallback_text, str) else ""

        if not response_text:
            logger.warning("QueryBuilder returned empty response text")

        parsed = _parse_llm_response(response_text)
        status = str(parsed.get("status", "")).lower()
        completed_sql = _find_sql_in_payload(parsed)
        if not completed_sql and response_text:
            completed_sql = _extract_sql_from_text(response_text)
        has_completed_sql = isinstance(completed_sql, str) and bool(completed_sql.strip())
        parser_generated_error = status == "error" and str(parsed.get("error", "")).startswith(
            "Failed to parse LLM response:"
        )

        tables_metadata_json = json.dumps([t.model_dump() for t in tables])

        success_statuses = {"success", "ok", "completed", "done"}
        if status in success_statuses or (
            has_completed_sql and (status not in {"error", "failed"} or parser_generated_error)
        ):
            raw_confidence = parsed.get("confidence", 0.5)
            try:
                confidence = max(0.0, min(1.0, float(raw_confidence)))
            except (TypeError, ValueError):
                confidence = 0.5

            return SQLDraft(
                status="success",
                source="dynamic",
                completed_sql=completed_sql,
                user_query=user_query,
                retry_count=retry_count,
                reasoning=parsed.get("reasoning"),
                tables_used=parsed.get("tables_used", []),
                tables_metadata_json=tables_metadata_json,
                confidence=confidence,
            )

        error_message = parsed.get("error")
        if not error_message:
            keys = sorted(parsed.keys())
            logger.warning(
                "QueryBuilder returned unexpected schema (status=%s, keys=%s, sample=%s)",
                status or "<missing>",
                keys,
                response_text[:300],
            )
            error_message = "Query generation failed due to unexpected model response format"

        return SQLDraft(
            status="error",
            source="dynamic",
            user_query=user_query,
            retry_count=retry_count,
            error=error_message,
            tables_used=parsed.get("tables_used", []),
            tables_metadata_json=tables_metadata_json,
        )

    except Exception as exc:
        logger.exception("Query generation error")
        return SQLDraft(
            status="error",
            source="dynamic",
            error=str(exc),
        )
    finally:
        reporter.step_end(step_name)
