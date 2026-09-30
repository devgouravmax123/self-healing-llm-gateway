import json
import logging
from urllib.parse import urlparse

from app.core.config import Settings, settings
from app.models.provider import ProviderTarget

logger = logging.getLogger(__name__)


def parse_and_validate_provider_targets(raw_json: str) -> list[ProviderTarget]:
    """Strictly validate and parse JSON-configured provider targets."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PROVIDER_TARGETS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("PROVIDER_TARGETS_JSON must be a JSON array of provider target objects.")

    if not data:
        raise ValueError("PROVIDER_TARGETS_JSON array cannot be empty.")

    targets: list[ProviderTarget] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target at index {idx} must be a JSON object, "
                f"got {type(item).__name__}."
            )

        target_id = item.get("id")
        provider = item.get("provider")
        model = item.get("model")
        api_base = item.get("api_base")
        priority = item.get("priority", 1)
        enabled = item.get("enabled", True)

        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target at index {idx} has invalid or empty 'id'."
            )
        target_id = target_id.strip()

        if target_id in seen_ids:
            raise ValueError(f"PROVIDER_TARGETS_JSON contains duplicate target ID '{target_id}'.")
        seen_ids.add(target_id)

        if not isinstance(provider, str) or not provider.strip():
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target '{target_id}' has invalid or empty 'provider'."
            )

        if not isinstance(model, str) or not model.strip():
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target '{target_id}' has invalid or empty 'model'."
            )

        if not isinstance(priority, int) or priority < 1:
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target '{target_id}' has invalid priority {priority!r} "
                "(must be integer >= 1)."
            )

        if not isinstance(enabled, bool):
            raise ValueError(
                f"PROVIDER_TARGETS_JSON target '{target_id}' has invalid 'enabled' flag "
                "(must be boolean)."
            )

        if api_base is not None:
            if not isinstance(api_base, str) or not api_base.strip():
                raise ValueError(
                    f"PROVIDER_TARGETS_JSON target '{target_id}' has empty 'api_base'."
                )
            parsed_url = urlparse(api_base)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                raise ValueError(
                    f"PROVIDER_TARGETS_JSON target '{target_id}' has invalid api_base URL "
                    f"'{api_base}'. Must be a valid http:// or https:// URL."
                )
            api_base = api_base.strip()

        target = ProviderTarget(
            id=target_id,
            provider=provider.strip(),
            model=model.strip(),
            api_base=api_base,
            priority=priority,
            enabled=enabled,
        )
        targets.append(target)

    return targets


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

        # If PROVIDER_TARGETS_JSON is provided, strictly validate and register all targets
        if cfg.provider_targets_json:
            targets = parse_and_validate_provider_targets(cfg.provider_targets_json)
            for target in targets:
                registry.register(target)
            return registry

        # Default fallback: single configured Ollama / LLM provider
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
