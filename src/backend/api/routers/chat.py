"""
Chat API routes with SSE streaming support.

This module provides the chat streaming endpoint using the
DataAssistant + process_query() pattern:
1. Classifies intent (data query, refinement, or conversation)
2. For data queries: calls process_query() directly
3. Supports conversational refinements like "show me for 90 days"
4. Handles clarification requests for missing parameters
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from api.dependencies import get_optional_user_id
from api.step_events import (
    clear_step_queue,
    set_request_user_id,
    set_step_queue,
)
from api.workflow_cache import (
    get_clarification_context,
    store_clarification_context,
)
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from models import ClarificationRequest, NL2SQLRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sanitized_error_event(error: Exception) -> str:
    """Build a sanitized SSE error payload with a correlation ID.

    Logs the full exception server-side and returns a generic message
    to the client so internal details are never leaked.
    """
    correlation_id = uuid.uuid4().hex[:12]
    logger.error("SSE error [%s]: %s", correlation_id, error, exc_info=True)
    payload = {
        "error": "An internal error occurred. Please try again.",
        "correlation_id": correlation_id,
        "done": True,
    }
    return f"data: {json.dumps(payload)}\n\n"


def _format_step_event(step_event: dict) -> dict:
    """Format a step event dict for SSE emission."""
    result: dict = {"step": step_event.get("step"), "done": False}
    if "status" in step_event:
        result["status"] = step_event["status"]
    if "duration_ms" in step_event:
        result["duration_ms"] = step_event["duration_ms"]
    return result


async def generate_clarification_response_stream(
    clarification_ctx: ClarificationRequest,
    message: str,
    request_id: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for a clarification continuation."""
    trace_id = uuid.uuid4().hex[:8]
    logger.debug(
        "[%s] Clarification stream start: request_id=%s incoming_conversation_id=%s user_present=%s",
        trace_id,
        request_id,
        conversation_id,
        bool(user_id),
    )
    from api.session_manager import get_assistant, store_assistant
    from config.settings import get_settings
    from nl2sql_controller.pipeline import process_query
    from shared.protocols import QueueReporter
    from workflow.clients import create_pipeline_clients

    step_queue: asyncio.Queue[dict] = asyncio.Queue()
    set_step_queue(step_queue)
    set_request_user_id(user_id)

    try:
        settings = get_settings()
        reporter = QueueReporter(step_queue)
        clients = create_pipeline_clients(
            settings,
            reporter=reporter,
            conversation_id=conversation_id,
        )

        # Build request incorporating the user's clarification answer
        request = NL2SQLRequest(
            user_query=message,
            is_refinement=True,
            previous_template_json=clarification_ctx.template_json,
            base_params=clarification_ctx.extracted_parameters,
            param_overrides={
                clarification_ctx.parameter_name: message,
            },
        )

        result = await process_query(request, clients)

        # Drain step events
        while not step_queue.empty():
            try:
                evt = step_queue.get_nowait()
                yield (f"data: {json.dumps(_format_step_event(evt))}\n\n")
            except asyncio.QueueEmpty:
                break

        foundry_conversation_id = conversation_id
        logger.debug(
            "[%s] Clarification stream processing complete: resolved_conversation_id=%s result_type=%s",
            trace_id,
            foundry_conversation_id,
            type(result).__name__,
        )

        if isinstance(result, ClarificationRequest):
            # Another clarification needed
            new_id = f"clarify_{uuid.uuid4().hex[:12]}"
            store_clarification_context(new_id, result)

            clarification_data = {
                "needs_clarification": True,
                "clarification": {
                    "request_id": new_id,
                    "parameter_name": result.parameter_name,
                    "prompt": result.prompt,
                    "allowed_values": result.allowed_values,
                },
                "done": False,
            }
            yield f"data: {json.dumps(clarification_data)}\n\n"
            yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")
        else:
            # NL2SQLResponse
            response = result
            tool_call = {
                "tool_name": "nl2sql_query",
                "tool_call_id": (f"nl2sql_clarification_{id(response)}"),
                "args": {},
                "result": {
                    "sql_query": response.sql_query,
                    "sql_response": response.sql_response,
                    "columns": response.columns,
                    "row_count": response.row_count,
                    "confidence_score": (response.confidence_score),
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,
                    "needs_clarification": (response.needs_clarification),
                    "clarification": (
                        response.clarification.model_dump() if response.clarification else None
                    ),
                    "defaults_used": response.defaults_used,
                    "suggestions": [s.model_dump() for s in response.suggestions],
                },
            }

            yield (
                "data: "
                f"{json.dumps({'tool_call': tool_call, 'done': False, 'conversation_id': foundry_conversation_id})}"
                "\n\n"
            )
            yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")

            # Update assistant context if cached
            assistant = get_assistant(foundry_conversation_id)
            if assistant:
                assistant.update_context(
                    response,
                    response.template_json,
                    response.extracted_params,
                )
                assistant.enrich_response(response)
                if foundry_conversation_id:
                    store_assistant(foundry_conversation_id, assistant)

        yield (
            f"data: {json.dumps({'done': True, 'conversation_id': foundry_conversation_id})}\n\n"
        )
        logger.debug(
            "[%s] Clarification stream done event emitted: conversation_id=%s",
            trace_id,
            foundry_conversation_id,
        )

    except (ValueError, RuntimeError, OSError, TypeError) as e:
        logger.error("Clarification error: %s", e, exc_info=True)
        yield _sanitized_error_event(e)
    finally:
        clear_step_queue()


async def generate_orchestrator_streaming_response(
    message: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response using the DataAssistant + process_query()."""
    del title  # TODO: Use for conversation metadata
    import time

    trace_id = uuid.uuid4().hex[:8]

    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from api.session_manager import get_assistant, store_assistant
    from assistant import DataAssistant, load_assistant_prompt
    from azure.identity.aio import DefaultAzureCredential
    from config.settings import get_settings
    from nl2sql_controller.pipeline import (
        process_query,
        process_scenario_query,
    )
    from shared.protocols import QueueReporter
    from shared.scenario_constants import (
        TELEMETRY_EVENT_SCENARIO_ROUTED,
    )
    from workflow.clients import create_pipeline_clients

    step_queue: asyncio.Queue[dict] = asyncio.Queue()
    set_step_queue(step_queue)
    set_request_user_id(user_id)

    try:
        logger.debug(
            "[%s] Chat stream start: incoming_conversation_id=%s user_present=%s message_chars=%d",
            trace_id,
            conversation_id,
            bool(user_id),
            len(message),
        )
        settings = get_settings()

        # Get or create DataAssistant for this session
        assistant = get_assistant(conversation_id)
        logger.debug(
            "[%s] Session cache lookup: hit=%s conversation_id=%s",
            trace_id,
            bool(assistant),
            conversation_id,
        )

        if assistant is None:
            endpoint = settings.azure_ai_project_endpoint
            if not endpoint:
                raise ValueError("AZURE_AI_PROJECT_ENDPOINT not set")

            client_id = settings.azure_client_id
            if client_id:
                credential = DefaultAzureCredential(
                    managed_identity_client_id=client_id,
                )
            else:
                credential = DefaultAzureCredential()

            orchestrator_model = (
                settings.azure_ai_orchestrator_model or settings.azure_ai_model_deployment_name
            )

            ai_client = FoundryChatClient(
                project_endpoint=endpoint,
                credential=credential,
                model=orchestrator_model,
            )

            effective_conversation_id = conversation_id
            if not effective_conversation_id:
                try:
                    logger.debug(
                        "[%s] No incoming conversation_id; pre-creating provider conversation",
                        trace_id,
                    )
                    openai_client = ai_client.project_client.get_openai_client()
                    created_conversation = await openai_client.conversations.create()
                    created_id = getattr(created_conversation, "id", None)
                    if isinstance(created_id, str) and created_id:
                        effective_conversation_id = created_id
                        logger.info(
                            "[%s] Pre-created provider conversation_id=%s for first turn",
                            trace_id,
                            created_id,
                        )
                    else:
                        logger.warning(
                            "[%s] Provider pre-create returned invalid conversation id: %r",
                            trace_id,
                            created_id,
                        )
                except (ValueError, RuntimeError, OSError, TypeError) as creation_error:
                    logger.warning(
                        "[%s] Could not pre-create provider conversation: %s",
                        trace_id,
                        creation_error,
                    )
            else:
                logger.debug(
                    "[%s] Reusing inbound conversation_id: %s",
                    trace_id,
                    effective_conversation_id,
                )

            # Resolve orchestrator agent identity for portal trace correlation.
            # These values populate the gen_ai.agent.id / gen_ai.agent.name
            # OTel attributes emitted by AgentTelemetryLayer, which the Foundry
            # portal Traces view (and Application Insights) read to populate
            # the "Agent" column on each chat-turn span.
            orchestrator_agent_name = settings.azure_ai_orchestrator_agent_name
            orchestrator_agent_id = (
                settings.azure_ai_orchestrator_agent_id or orchestrator_agent_name
            )

            agent = Agent(
                client=ai_client,
                id=orchestrator_agent_id,
                name=orchestrator_agent_name,
                instructions=load_assistant_prompt(),
            )

            assistant = DataAssistant(agent, effective_conversation_id)
            logger.debug(
                "[%s] Created new DataAssistant for conversation_id=%s (agent_id=%s)",
                trace_id,
                effective_conversation_id,
                orchestrator_agent_id,
            )

        # Step 1: Classify intent
        yield (f"data: {json.dumps({'step': 'Analyzing request...', 'status': 'started'})}\n\n")

        classify_start = time.time()
        classification = await assistant.classify_intent(message)
        classify_ms = int((time.time() - classify_start) * 1000)
        logger.debug(
            "[%s] Classification complete: intent=%s assistant_conversation_id=%s duration_ms=%d",
            trace_id,
            classification.intent,
            assistant.conversation_id,
            classify_ms,
        )

        yield (
            "data: "
            f"{json.dumps({'step': 'Analyzing request...', 'status': 'completed', 'duration_ms': classify_ms})}"
            "\n\n"
        )

        if classification.intent == "conversation":
            yield (
                f"data: {json.dumps({'step': 'Generating response...', 'status': 'started'})}\n\n"
            )
            convo_start = time.time()
            response_text = await assistant.handle_conversation(message)
            convo_ms = int((time.time() - convo_start) * 1000)
            yield (
                "data: "
                f"{json.dumps({'step': 'Generating response...', 'status': 'completed', 'duration_ms': convo_ms})}"
                "\n\n"
            )

            output = {
                "text": response_text,
                "conversation_id": assistant.conversation_id,
            }
            yield f"data: {json.dumps(output)}\n\n"

            # Append scenario discovery hints when the LLM signals capability inquiry
            if classification.scenario_discovery:
                from shared.scenario_hints import build_discoverability_hint

                hint = build_discoverability_hint()
                tool_call = {
                    "tool_name": "scenario_analysis",
                    "tool_call_id": f"discovery_{uuid.uuid4().hex[:8]}",
                    "args": {},
                    "result": {
                        "mode": "discovery",
                        "scenario_type": "",
                        "assumptions": [],
                        "metrics": [],
                        "summary_totals": {},
                        "data_limitations": [],
                        "visualization": None,
                        "narrative": None,
                        "prompt_hints": [hint.model_dump()],
                    },
                }
                yield (
                    "data: "
                    f"{json.dumps({'tool_call': tool_call, 'done': False, 'conversation_id': assistant.conversation_id})}"
                    "\n\n"
                )

            logger.debug(
                "[%s] Conversation response emitted: assistant_conversation_id=%s",
                trace_id,
                assistant.conversation_id,
            )
        elif classification.intent == "scenario" and classification.scenario_intent:
            # Scenario (what-if) processing
            assumption_set = assistant.build_scenario_assumption_set(
                classification.scenario_intent,
                message,
            )

            logger.info(
                "[%s] %s: confidence=%.3f patterns=%s scenario_type=%s",
                trace_id,
                TELEMETRY_EVENT_SCENARIO_ROUTED,
                classification.scenario_intent.confidence,
                classification.scenario_intent.detected_patterns,
                assumption_set.scenario_type,
            )

            reporter = QueueReporter(step_queue)
            clients = create_pipeline_clients(
                settings,
                reporter=reporter,
                conversation_id=assistant.conversation_id,
            )

            result = await process_scenario_query(
                classification.scenario_intent,
                assumption_set,
                message,
                clients,
            )

            # Drain step events
            while not step_queue.empty():
                try:
                    evt = step_queue.get_nowait()
                    yield (f"data: {json.dumps(_format_step_event(evt))}\n\n")
                except asyncio.QueueEmpty:
                    break

            # T025: Emit scenario_analysis tool result
            if result.scenario_result:
                tool_call = {
                    "tool_name": "scenario_analysis",
                    "tool_call_id": f"scenario_{id(result)}",
                    "args": {},
                    "result": {
                        "mode": "scenario",
                        "scenario_type": result.scenario_type,
                        "assumptions": [
                            a.model_dump() for a in (result.scenario_assumptions or [])
                        ],
                        "metrics": [m.model_dump() for m in result.scenario_result.metrics],
                        "summary_totals": (result.scenario_result.summary_totals),
                        "data_limitations": (result.scenario_result.data_limitations),
                        "visualization": (
                            result.scenario_visualization.model_dump()
                            if result.scenario_visualization
                            else None
                        ),
                        "narrative": (
                            result.scenario_narrative.model_dump()
                            if result.scenario_narrative
                            else None
                        ),
                        "prompt_hints": [h.model_dump() for h in (result.scenario_hints or [])],
                    },
                }
                yield (
                    "data: "
                    f"{json.dumps({'tool_call': tool_call, 'done': False, 'conversation_id': assistant.conversation_id})}"
                    "\n\n"
                )
            elif result.scenario_hints:
                # Incomplete scenario: no computation, just clarification hints
                tool_call = {
                    "tool_name": "scenario_analysis",
                    "tool_call_id": f"scenario_{id(result)}",
                    "args": {},
                    "result": {
                        "mode": "scenario",
                        "scenario_type": result.scenario_type,
                        "assumptions": [],
                        "metrics": [],
                        "summary_totals": {},
                        "data_limitations": [],
                        "visualization": None,
                        "narrative": None,
                        "prompt_hints": [h.model_dump() for h in result.scenario_hints],
                    },
                }
                yield (
                    "data: "
                    f"{json.dumps({'tool_call': tool_call, 'done': False, 'conversation_id': assistant.conversation_id})}"
                    "\n\n"
                )
            else:
                output = assistant.render_response(result)
                yield f"data: {json.dumps(output)}\n\n"

            if assistant.conversation_id:
                store_assistant(assistant.conversation_id, assistant)

            logger.debug(
                "[%s] Scenario response emitted: conversation_id=%s is_scenario=%s",
                trace_id,
                assistant.conversation_id,
                result.is_scenario,
            )
        else:
            # Data query or refinement
            nl2sql_request = assistant.build_nl2sql_request(classification)

            reporter = QueueReporter(step_queue)
            clients = create_pipeline_clients(
                settings,
                reporter=reporter,
                conversation_id=assistant.conversation_id,
            )

            result = await process_query(nl2sql_request, clients)

            # Drain step events
            while not step_queue.empty():
                try:
                    evt = step_queue.get_nowait()
                    yield (f"data: {json.dumps(_format_step_event(evt))}\n\n")
                except asyncio.QueueEmpty:
                    break

            if isinstance(result, ClarificationRequest):
                req_id = f"clarify_{uuid.uuid4().hex[:12]}"
                store_clarification_context(req_id, result)

                clarification_data = {
                    "needs_clarification": True,
                    "clarification": {
                        "request_id": req_id,
                        "parameter_name": result.parameter_name,
                        "prompt": result.prompt,
                        "allowed_values": (result.allowed_values),
                    },
                    "conversation_id": assistant.conversation_id,
                    "done": False,
                }
                yield (f"data: {json.dumps(clarification_data)}\n\n")
                yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")

                if assistant.conversation_id:
                    store_assistant(assistant.conversation_id, assistant)

                yield (
                    f"data: {json.dumps({'done': True, 'conversation_id': assistant.conversation_id})}\n\n"
                )
                return

            # NL2SQLResponse
            response = result
            assistant.update_context(
                response,
                response.template_json,
                response.extracted_params,
            )
            assistant.enrich_response(response)

            if assistant.conversation_id:
                store_assistant(assistant.conversation_id, assistant)

            output = assistant.render_response(response)
            yield f"data: {json.dumps(output)}\n\n"
            logger.debug(
                "[%s] Data response emitted: assistant_conversation_id=%s",
                trace_id,
                assistant.conversation_id,
            )

        yield (
            f"data: {json.dumps({'done': True, 'conversation_id': assistant.conversation_id})}\n\n"
        )
        logger.debug(
            "[%s] Done event emitted: conversation_id=%s", trace_id, assistant.conversation_id
        )

    except Exception as e:
        logger.error(
            "[%s] DataAssistant error for incoming_conversation_id=%s: %s",
            trace_id,
            conversation_id,
            e,
            exc_info=True,
        )
        yield _sanitized_error_event(e)

    finally:
        clear_step_queue()


@router.get("/stream")
async def chat_stream(
    message: str = Query(..., description="User message"),
    conversation_id: str | None = Query(None, description="Conversation ID"),
    title: str | None = Query(None, description="Conversation title"),
    request_id: str | None = Query(None, description="Request ID for clarification"),
    user_id: str | None = Depends(get_optional_user_id),
) -> StreamingResponse:
    """SSE streaming chat with DataAssistant architecture."""
    trace_id = uuid.uuid4().hex[:8]
    logger.debug(
        "[%s] chat_stream request: conversation_id=%s request_id=%s user_present=%s",
        trace_id,
        conversation_id,
        request_id,
        bool(user_id),
    )
    if request_id:
        clarification_ctx = get_clarification_context(request_id)
        if clarification_ctx:
            return StreamingResponse(
                generate_clarification_response_stream(
                    clarification_ctx,
                    message,
                    request_id,
                    conversation_id,
                    user_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        logger.warning(
            "No clarification context for request_id=%s",
            request_id,
        )

    return StreamingResponse(
        generate_orchestrator_streaming_response(message, conversation_id, user_id, title),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
