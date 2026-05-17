"""
FastAPI dependencies for authentication and shared resources.
"""

import logging
from typing import TYPE_CHECKING, Any, TypedDict, cast

from fastapi import Depends, HTTPException, Request

if TYPE_CHECKING:
    from agent_framework import Agent

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 50


class OwnershipContext(TypedDict):
    """Ownership verification result payload for conversation routes."""

    conversation: Any
    metadata: dict[str, Any]
    user_id: str


def get_user_id(request: Request) -> str:
    """
    Get authenticated user ID from request state.

    Raises HTTPException 401 if not authenticated.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def get_optional_user_id(request: Request) -> str | None:
    """Get user ID from request state, or None if not authenticated."""
    return getattr(request.state, "user_id", None)


def get_project_client(request: Request) -> Any:  # noqa: ANN401
    """
    Get the AIProjectClient from app state.

    The chat_client (FoundryChatClient) stores the project_client internally.

    Raises HTTPException 503 if not initialized.
    """
    chat_client = getattr(request.app.state, "chat_client", None)
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    # Access the underlying AIProjectClient from FoundryChatClient
    if hasattr(chat_client, "project_client"):
        return chat_client.project_client
    return chat_client


def get_agent(request: Request) -> "Agent":
    """
    Get the agent from app state.

    Raises HTTPException 503 if not initialized.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return agent


async def verify_conversation_ownership(
    conversation_id: str,
    user_id: str = Depends(get_user_id),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> OwnershipContext:
    """
    Verify that the current user owns the specified conversation.

    Returns the conversation if ownership is verified.
    Raises HTTPException 403 if access is denied.
    """
    try:
        # Get the OpenAI client from the project client
        openai_client = project_client.get_openai_client()

        # Retrieve the conversation - will fail if it doesn't exist
        conversation = openai_client.conversations.retrieve(conversation_id)

        # For now, we allow access if the thread exists
        # TODO: Implement proper ownership tracking via local storage
        metadata_obj = getattr(conversation, "metadata", {}) or {}
        metadata = metadata_obj if isinstance(metadata_obj, dict) else {}

        logger.info(
            "Conversation access check: conversation_id=%s, current_user=%s",
            conversation_id,
            user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error verifying conversation access for %s", conversation_id)
        # If conversation doesn't exist, return 404
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Conversation not found") from e
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {
            "conversation": conversation,
            "metadata": metadata,
            "user_id": user_id,
        }


def extract_message_text(msg: Any) -> str:  # noqa: ANN401
    """Extract text content from a message object."""
    content = ""
    if hasattr(msg, "content"):
        msg_content = msg.content
        if isinstance(msg_content, str):
            return msg_content
        if isinstance(msg_content, list):
            for part in msg_content:
                if hasattr(part, "text"):
                    text_val = part.text
                    if hasattr(text_val, "value"):
                        content += text_val.value
                    else:
                        content += str(text_val)
                elif isinstance(part, dict) and "text" in part:
                    content += part["text"]
    return content


async def get_conversation_title(
    project_client: object,
    conversation_id: str,
    metadata: dict[str, Any],
) -> str:
    """
    Get conversation title from metadata or first user message.

    Returns "New Chat" if no title can be determined.
    """
    title = metadata.get("title")
    if title:
        return title

    try:
        openai_client = cast(Any, project_client).get_openai_client()
        # List items in the conversation to find first user message
        items = openai_client.conversations.items.list(conversation_id)
        for item in items:
            if hasattr(item, "role") and item.role == "user":
                text = extract_message_text(item)
                if text:
                    return text[:MAX_TITLE_LENGTH] + "..." if len(text) > MAX_TITLE_LENGTH else text
    except (ValueError, RuntimeError, OSError) as e:
        logger.warning("Could not fetch messages for conversation %s: %s", conversation_id, e)

    return "New Chat"
