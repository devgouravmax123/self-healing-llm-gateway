"""Pydantic request schemas for the LLM gateway."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    """A single chat message in a conversation."""

    role: Literal["system", "user", "assistant", "tool", "function"] = Field(
        description="The role of the message author (system, user, assistant, etc.)."
    )
    content: str = Field(description="The text content of the message.")

    @field_validator("content")
    @classmethod
    def validate_content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message content must not be empty or whitespace only.")
        return v


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request schema."""

    model: str = Field(
        description="ID of the model to use.",
        examples=["local-model-a", "llama3.2:3b", "gpt-4o-mini"],
    )
    messages: list[ChatMessage] = Field(
        description="A list of messages comprising the conversation so far.",
        min_length=1,
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature between 0 and 2.",
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Maximum number of tokens to generate.",
    )
    stream: bool | None = Field(
        default=False,
        description="Whether to stream back partial progress.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata for tenant ID, feature tagging, etc.",
    )

    @field_validator("model")
    @classmethod
    def validate_model_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Model name must not be empty.")
        return v
