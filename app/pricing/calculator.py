"""Configuration-driven cost calculator for LLM token usage."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import NamedTuple

logger = logging.getLogger(__name__)


class ModelPricing(NamedTuple):
    """Pricing rates for a model per 1,000,000 tokens."""

    input_cost_per_million: Decimal
    output_cost_per_million: Decimal
    currency: str = "USD"


# Default pricing catalog for known models (rates per 1M tokens)
# Users can override or extend this via configuration or explicit pricing models.
DEFAULT_MODEL_PRICING: dict[tuple[str, str], ModelPricing] = {
    # OpenAI examples (per 1M tokens)
    ("openai", "gpt-4o"): ModelPricing(
        input_cost_per_million=Decimal("2.50"),
        output_cost_per_million=Decimal("10.00"),
    ),
    ("openai", "gpt-4o-mini"): ModelPricing(
        input_cost_per_million=Decimal("0.15"),
        output_cost_per_million=Decimal("0.60"),
    ),
    # Anthropic examples (per 1M tokens)
    ("anthropic", "claude-3-5-sonnet"): ModelPricing(
        input_cost_per_million=Decimal("3.00"),
        output_cost_per_million=Decimal("15.00"),
    ),
    ("anthropic", "claude-3-haiku"): ModelPricing(
        input_cost_per_million=Decimal("0.25"),
        output_cost_per_million=Decimal("1.25"),
    ),
}

ONE_MILLION = Decimal("1000000")


class CostCalculator:
    """Calculates estimated request costs using exact Decimal arithmetic."""

    def __init__(
        self,
        pricing_catalog: dict[tuple[str, str], ModelPricing] | None = None,
    ) -> None:
        self._pricing = pricing_catalog if pricing_catalog is not None else DEFAULT_MODEL_PRICING

    def get_pricing(self, provider: str, model: str) -> ModelPricing | None:
        """Lookup pricing for a specific provider and model."""
        prov_key = provider.lower()
        model_key = model.lower()

        # Exact match (provider, model)
        if (prov_key, model_key) in self._pricing:
            return self._pricing[(prov_key, model_key)]

        # Model prefix/variant fallback match
        for (p, m), pricing in self._pricing.items():
            if p == prov_key and (
                model_key == m or model_key.startswith(f"{m}-") or model_key.startswith(f"{m}:")
            ):
                return pricing

        return None

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> Decimal | None:
        """Calculate estimated monetary cost for the given token metrics.

        Rules:
        1. Local Ollama inference without custom pricing defaults to Decimal("0.000000").
        2. If either input_tokens or output_tokens is None, returns None (do not fabricate).
        3. If pricing is unavailable for cloud providers, returns None (do not fabricate).
        4. When pricing and tokens are available:
           cost = (input_tokens / 1M * input_rate) + (output_tokens / 1M * output_rate)
        """
        prov_lower = provider.lower()

        # Check explicit custom pricing first
        pricing = self.get_pricing(prov_lower, model)

        if pricing is not None:
            if input_tokens is None or output_tokens is None:
                return None
            in_cost = (Decimal(input_tokens) / ONE_MILLION) * pricing.input_cost_per_million
            out_cost = (Decimal(output_tokens) / ONE_MILLION) * pricing.output_cost_per_million
            return (in_cost + out_cost).quantize(Decimal("0.000001"))

        # Local Ollama inference default representation: 0 cost (no external API charge)
        if prov_lower == "ollama":
            if input_tokens is None and output_tokens is None:
                return Decimal("0.000000")
            return Decimal("0.000000")

        # Unknown / unconfigured pricing -> return None
        return None


# Global cost calculator instance
cost_calculator = CostCalculator()
