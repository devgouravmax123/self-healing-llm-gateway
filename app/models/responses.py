"""Pydantic response schemas for the LLM gateway."""

import time
from typing import Literal

from pydantic import BaseModel, Field


class ChatCompletionMessageResponse(BaseModel):
    """Message output contained within a choice."""

    role: str = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    """A single choice generated for a chat completion request."""

    index: int = 0
    message: ChatCompletionMessageResponse
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = "stop"


class CompletionUsage(BaseModel):
    """Usage statistics for the completion request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response schema."""

    id: str = Field(description="Unique identifier for the completion response.")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: CompletionUsage | None = Field(default_factory=CompletionUsage)


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    """Readiness check response schema."""

    status: str
    ready: bool
    details: dict[str, str] = Field(default_factory=dict)
