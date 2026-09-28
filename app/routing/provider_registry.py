"""Provider registry for managing configured LLM provider targets."""

import logging

from app.core.config import Settings, settings
from app.models.provider import ProviderTarget

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry maintaining configured LLM provider targets."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderTarget] = {}

    def register(self, provider: ProviderTarget) -> None:
        """Register a provider target."""
        self._providers[provider.id] = provider
        logger.debug(
            "Registered provider target: %s (%s/%s)",
            provider.id,
            provider.provider,
            provider.model,
        )

    def get(self, provider_id: str) -> ProviderTarget | None:
        """Retrieve a provider target by its ID."""
        return self._providers.get(provider_id)

    def list_all(self) -> list[ProviderTarget]:
        """List all registered providers."""
        return list(self._providers.values())

    def list_enabled(self) -> list[ProviderTarget]:
        """List all enabled providers."""
        return [p for p in self._providers.values() if p.enabled]

    def clear(self) -> None:
        """Clear all registered providers (useful in tests)."""
        self._providers.clear()

    @classmethod
    def from_settings(cls, config: Settings | None = None) -> "ProviderRegistry":
        """Initialize and populate the registry from application settings."""
        cfg = config or settings
        registry = cls()

        # For Phase 04: single configured default Ollama provider
        if cfg.llm_provider.lower() == "ollama":
            ollama_target = ProviderTarget(
                id="ollama_default",
                provider="ollama",
                model=cfg.ollama_model,
                api_base=cfg.ollama_base_url,
                enabled=True,
            )
            registry.register(ollama_target)
        else:
            default_target = ProviderTarget(
                id=f"{cfg.llm_provider}_default",
                provider=cfg.llm_provider,
                model="default",
                enabled=True,
            )
            registry.register(default_target)

        return registry


# Global default registry instance
provider_registry = ProviderRegistry.from_settings()
