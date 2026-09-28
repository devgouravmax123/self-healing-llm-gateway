"""Provider models and target definitions."""

from pydantic import BaseModel, Field


class ProviderTarget(BaseModel):
    """Represents a configured LLM provider target."""

    id: str = Field(description="Unique identifier for the provider target (e.g., 'ollama_local').")
    provider: str = Field(description="Provider type/name (e.g., 'ollama', 'openai').")
    model: str = Field(
        description="Model name/tag associated with this provider target (e.g., 'qwen2.5:3b')."
    )
    api_base: str | None = Field(
        default=None,
        description="Optional base URL for the provider API.",
    )
    priority: int = Field(
        default=1,
        description="Priority ranking for candidate ordering (lower integer = higher priority).",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this provider target is currently active and eligible for routing.",
    )
