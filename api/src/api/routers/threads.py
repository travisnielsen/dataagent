"""
Thread management API routes.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

if TYPE_CHECKING:
    from agent_framework.azure import AzureAIAgentClient

from src.api.models import (
    ThreadData,
    ThreadListResponse,
    UpdateThreadRequest,
    MessageData,
    MessagesResponse,
)
from src.api.dependencies import (
    get_user_id,
    get_chat_client,
    verify_thread_ownership,
    get_thread_title,
    extract_message_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    user_id: str = Depends(get_user_id),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
):
    """
    List threads for the current user (filtered by user_id metadata).
    """
    try:
        user_threads: list[ThreadData] = []

        async for thread in chat_client.agents_client.threads.list(limit=100, order="desc"):
            metadata = getattr(thread, "metadata", {}) or {}
            if metadata.get("user_id") == user_id:
                title = await get_thread_title(chat_client, thread.id, metadata)
                status = metadata.get("status", "regular")
                created_at = thread.created_at.isoformat() if thread.created_at else None

                user_threads.append(ThreadData(
                    thread_id=thread.id,
                    title=title,
                    status=status,
                    created_at=created_at,
                ))

        return ThreadListResponse(threads=user_threads)

    except Exception as e:
        logger.error("Error listing threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{thread_id}", response_model=ThreadData)
async def get_thread(
    thread_id: str,
    ownership: dict = Depends(verify_thread_ownership),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
):
    """
    Get a specific thread by ID.
    """
    thread = ownership["thread"]
    metadata = ownership["metadata"]

    title = await get_thread_title(chat_client, thread_id, metadata)

    return ThreadData(
        thread_id=thread_id,
        title=title,
        status=metadata.get("status", "regular"),
        created_at=thread.created_at.isoformat() if thread.created_at else None,
    )


@router.patch("/{thread_id}")
async def update_thread(
    thread_id: str,
    body: UpdateThreadRequest,
    ownership: dict = Depends(verify_thread_ownership),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
):
    """
    Update thread metadata (title, status).
    """
    metadata = dict(ownership["metadata"])

    if body.title is not None:
        metadata["title"] = body.title
    if body.status is not None:
        metadata["status"] = body.status

    try:
        await chat_client.agents_client.threads.update(thread_id, metadata=metadata)
        return {"success": True}
    except Exception as e:
        logger.error("Error updating thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
):
    """
    Delete a thread.

    Note: _ownership triggers ownership verification before delete.
    """
    try:
        await chat_client.agents_client.threads.delete(thread_id)
        return {"success": True}
    except Exception as e:
        logger.error("Error deleting thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{thread_id}/messages", response_model=MessagesResponse)
async def get_thread_messages(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
):
    """
    Get all messages for a thread.
    Returns messages in chronological order (oldest first).

    Note: _ownership triggers ownership verification.
    """
    try:
        messages: list[MessageData] = []
        seen_content: set[tuple[str, str]] = set()

        async for msg in chat_client.agents_client.messages.list(thread_id=thread_id):
            content = extract_message_text(msg)

            if not content.strip():
                continue

            # Deduplicate by role + content
            content_key = (msg.role.value, content)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)

            messages.append(MessageData(
                id=msg.id,
                role=msg.role.value,
                content=content,
                created_at=msg.created_at.isoformat() if msg.created_at else None,
            ))

        # Reverse to get chronological order (API returns newest first)
        messages.reverse()

        return MessagesResponse(messages=messages)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting messages for thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
