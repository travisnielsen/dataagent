"""
Pydantic models for API request/response schemas.
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request body for non-streaming chat endpoint."""
    message: str
    thread_id: str | None = None


class ThreadData(BaseModel):
    """Thread information returned by thread endpoints."""
    thread_id: str
    title: str | None = None
    status: str = "regular"  # "regular" or "archived"
    created_at: str | None = None


class ThreadListResponse(BaseModel):
    """Response for listing threads."""
    threads: list[ThreadData]


class UpdateThreadRequest(BaseModel):
    """Request body for updating thread metadata."""
    title: str | None = None
    status: str | None = None


class MessageData(BaseModel):
    """Individual message in a thread."""
    id: str
    role: str
    content: str
    created_at: str | None = None


class MessagesResponse(BaseModel):
    """Response for getting thread messages."""
    messages: list[MessageData]
