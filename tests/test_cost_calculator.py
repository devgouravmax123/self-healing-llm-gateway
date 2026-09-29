"""Unit tests for CostCalculator and pricing resolution."""

from decimal import Decimal

from app.pricing.calculator import CostCalculator, ModelPricing


class TestCostCalculator:
    """Test suite for token cost calculation logic."""

    def test_known_pricing_calculation(self) -> None:
        """Verify cost calculation with configured input/output pricing per 1M tokens."""
        pricing_catalog = {
            ("openai", "gpt-4o"): ModelPricing(
                input_cost_per_million=Decimal("2.50"),
                output_cost_per_million=Decimal("10.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)

        # 1,000 input tokens = (1000 / 1_000_000) * 2.50 = 0.0025
        # 500 output tokens = (500 / 1_000_000) * 10.00 = 0.0050
        # Total = 0.007500
        cost = calc.calculate_cost(
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost == Decimal("0.007500")

    def test_zero_tokens_calculation(self) -> None:
        """Verify 0 tokens result in 0.000000 cost."""
        pricing_catalog = {
            ("openai", "gpt-4o"): ModelPricing(
                input_cost_per_million=Decimal("2.50"),
                output_cost_per_million=Decimal("10.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)

        cost = calc.calculate_cost(
            provider="openai",
            model="gpt-4o",
            input_tokens=0,
            output_tokens=0,
        )
        assert cost == Decimal("0.000000")

    def test_missing_input_tokens_returns_none(self) -> None:
        """Verify that when input tokens is None, cost is not fabricated and returns None."""
        pricing_catalog = {
            ("openai", "gpt-4o"): ModelPricing(
                input_cost_per_million=Decimal("2.50"),
                output_cost_per_million=Decimal("10.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)

        cost = calc.calculate_cost(
            provider="openai",
            model="gpt-4o",
            input_tokens=None,
            output_tokens=500,
        )
        assert cost is None

    def test_missing_output_tokens_returns_none(self) -> None:
        """Verify that when output tokens is None, cost is not fabricated and returns None."""
        pricing_catalog = {
            ("openai", "gpt-4o"): ModelPricing(
                input_cost_per_million=Decimal("2.50"),
                output_cost_per_million=Decimal("10.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)

        cost = calc.calculate_cost(
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=None,
        )
        assert cost is None

    def test_unpriced_cloud_model_returns_none(self) -> None:
        """Verify that an unpriced cloud provider/model returns None (never guesses pricing)."""
        calc = CostCalculator(pricing_catalog={})
        cost = calc.calculate_cost(
            provider="custom_cloud",
            model="unpriced-model",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost is None

    def test_ollama_default_zero_cost(self) -> None:
        """Verify Ollama inference defaults to zero cost when no custom pricing is configured."""
        calc = CostCalculator(pricing_catalog={})
        cost = calc.calculate_cost(
            provider="ollama",
            model="qwen2.5:3b",
            input_tokens=100,
            output_tokens=50,
        )
        assert cost == Decimal("0.000000")

    def test_ollama_custom_pricing_overrides_default(self) -> None:
        """Verify custom pricing for Ollama is respected if explicitly configured."""
        pricing_catalog = {
            ("ollama", "custom-enterprise-model"): ModelPricing(
                input_cost_per_million=Decimal("1.00"),
                output_cost_per_million=Decimal("2.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)
        cost = calc.calculate_cost(
            provider="ollama",
            model="custom-enterprise-model",
            input_tokens=1000000,
            output_tokens=1000000,
        )
        assert cost == Decimal("3.000000")
