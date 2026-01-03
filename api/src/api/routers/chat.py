"""
Chat API routes with SSE streaming support.

This module provides chat endpoints that work with the workflow-based agent architecture.
The workflow processes user messages through:
1. ChatAgentExecutor - receives user input
2. NL2SQLAgentExecutor - processes data queries
3. ChatAgentExecutor - renders structured response
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, TYPE_CHECKING

from fastapi import APIRouter, Query, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse

from agent_framework import (
    ChatMessage,
    Role,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
    WorkflowRunState,
)

if TYPE_CHECKING:
    from agent_framework import ChatAgent, Workflow

from src.api.models import ChatRequest
from src.api.dependencies import get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def generate_workflow_streaming_response(
    workflow: "Workflow",
    message: str,
    incoming_thread_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response from the workflow using workflow events.

    The workflow runs:
    1. ChatAgentExecutor receives the user message
    2. NL2SQLAgentExecutor processes the query
    3. ChatAgentExecutor renders the final response

    The output is a JSON structure with 'text' and 'thread_id' from Foundry.
    """
    try:
        logger.info("Starting workflow stream for message: %s", message[:100])

        # Create a ChatMessage to send to the workflow
        user_message = ChatMessage(role=Role.USER, text=message)

        # Track if we've received output
        output_received = False
        foundry_thread_id = incoming_thread_id  # Fall back to incoming if not returned

        async for event in workflow.run_stream(user_message):
            if isinstance(event, WorkflowOutputEvent):
                # This is the final rendered response from ChatAgentExecutor
                # It's a JSON structure with 'text' and 'thread_id'
                output_data = event.data
                if isinstance(output_data, str):
                    try:
                        # Parse the structured output
                        parsed = json.loads(output_data)
                        output_text = parsed.get("text", output_data)
                        foundry_thread_id = parsed.get("thread_id") or foundry_thread_id
                    except json.JSONDecodeError:
                        # Fallback if not JSON (backward compatibility)
                        output_text = output_data

                    # Stream the output in chunks for better UX
                    chunk_size = 50
                    for i in range(0, len(output_text), chunk_size):
                        chunk = output_text[i:i + chunk_size]
                        yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                        await asyncio.sleep(0.01)
                    output_received = True

            elif isinstance(event, WorkflowStatusEvent):
                if event.state == WorkflowRunState.IDLE:
                    logger.info("Workflow completed")
                    break

        if not output_received:
            yield f"data: {json.dumps({'content': 'No response generated', 'done': False})}\n\n"

        # Include thread_id in the done signal for the frontend
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"

    except (ValueError, RuntimeError, OSError, TypeError) as e:
        logger.error("Workflow error: %s", e, exc_info=True)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"


async def generate_streaming_response(
    agent: "ChatAgent",
    thread_id: str | None,
    message: str,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response using the WorkflowAgent.

    The agent wraps a workflow that processes messages through multiple executors.
    The final output is a JSON structure with 'text' and 'thread_id' from Foundry.
    """
    try:
        # Get or create thread
        thread = agent.get_new_thread(service_thread_id=thread_id)
        logger.info("Thread created: service_thread_id=%s", thread.service_thread_id)

        # Set metadata for new threads only
        thread_metadata = None
        if not thread_id:
            thread_metadata = {}
            if user_id:
                thread_metadata["user_id"] = user_id
            if title:
                thread_metadata["title"] = title

        logger.info(
            "Running with user_id=%s, incoming thread_id=%s, title=%s, metadata=%s",
            user_id, thread_id, title, thread_metadata
        )

        # Stream the response - WorkflowAgent.run_stream yields AgentRunResponseUpdate
        # The executor's final output is a JSON object with 'text' and 'thread_id'
        update_count = 0
        foundry_thread_id = thread_id  # Default to incoming thread_id
        async for update in agent.run_stream(message, thread=thread, metadata=thread_metadata):
            update_count += 1
            # AgentRunResponseUpdate has .text property that extracts text from contents
            text = update.text if hasattr(update, 'text') else None
            logger.debug("Stream update #%d: text=%s", update_count, text[:50] if text else None)
            if text:
                # Check if this is structured JSON output from the executor
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "text" in parsed:
                        # Extract the actual text and Foundry thread ID
                        output_text = parsed.get("text", "")
                        foundry_thread_id = parsed.get("thread_id") or foundry_thread_id
                        logger.info("Extracted Foundry thread_id: %s", foundry_thread_id)
                        # Stream the text content
                        if output_text:
                            chunk_size = 50
                            for i in range(0, len(output_text), chunk_size):
                                chunk = output_text[i:i + chunk_size]
                                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                                await asyncio.sleep(0.01)
                    else:
                        # Not structured output, stream as-is
                        yield f"data: {json.dumps({'content': text, 'done': False})}\n\n"
                        await asyncio.sleep(0.01)
                except json.JSONDecodeError:
                    # Not JSON, stream as plain text
                    yield f"data: {json.dumps({'content': text, 'done': False})}\n\n"
                    await asyncio.sleep(0.01)

        logger.info("Run complete after %d updates: foundry_thread_id=%s", update_count, foundry_thread_id)

        # Send the done signal with the Foundry thread ID
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"
        logger.info("Sent done signal to client")

    except (ValueError, RuntimeError, OSError) as e:
        logger.error("Error: %s", e)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"


@router.get("/stream")
async def chat_stream(
    request: Request,
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Foundry thread ID (omit for new thread)"),
    title: str | None = Query(None, description="Thread title (for new threads only)"),
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    SSE streaming chat with workflow-based agent architecture.

    The workflow processes messages through:
    1. ChatAgentExecutor - receives user input
    2. NL2SQLAgentExecutor - processes data queries
    3. ChatAgentExecutor - renders structured response

    Thread support:
    - Omit thread_id for new conversation - Foundry will create one
    - Include thread_id to continue existing conversation
    - Response includes thread_id for use in subsequent requests
    """
    agent = getattr(request.app.state, "agent", None)

    if agent is None:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': 'Agent not initialized', 'done': True})}\n\n"]),
            media_type="text/event-stream",
        )

    # Use the WorkflowAgent which supports threads
    return StreamingResponse(
        generate_streaming_response(agent, thread_id, message, user_id, title),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("")
async def chat(
    chat_request: ChatRequest,
    request: Request,
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    Non-streaming chat with workflow-based agent architecture and thread support.

    The workflow processes messages through Chat -> NL2SQL -> Chat executors.
    Threads maintain conversation history across requests.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    try:
        # Get or create thread for conversation continuity
        thread = agent.get_new_thread(service_thread_id=chat_request.thread_id)

        # Set user_id metadata for new threads only
        thread_metadata = {"user_id": user_id} if user_id and not chat_request.thread_id else None

        # Run the agent with thread support
        response = await agent.run(chat_request.message, thread=thread, metadata=thread_metadata)

        return {
            "response": response.text or str(response),
            "thread_id": thread.service_thread_id,
        }
    except (ValueError, RuntimeError, OSError) as e:
        logger.error("Agent error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
