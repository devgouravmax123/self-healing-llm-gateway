"""Unit tests for UsageTracker token extraction, attribution, and safety."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.pricing.calculator import CostCalculator, ModelPricing
from app.usage.tracker import UsageTracker


class TestUsageTracker:
    """Test suite for UsageTracker extraction and attribution."""

    @pytest.mark.asyncio
    async def test_normal_usage_extraction_and_attribution(self) -> None:
        """Verify normal token extraction, tenant/feature attribution, and repository call."""
        mock_repo = AsyncMock()
        pricing_catalog = {
            ("openai", "gpt-4o"): ModelPricing(
                input_cost_per_million=Decimal("2.50"),
                output_cost_per_million=Decimal("10.00"),
            ),
        }
        calc = CostCalculator(pricing_catalog=pricing_catalog)
        tracker = UsageTracker(calculator=calc, repository=mock_repo)

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hello")],
            metadata={"tenant_id": "tenant_123", "feature": "summarization"},
        )
        target = ProviderTarget(
            id="openai_primary",
            provider="openai",
            model="gpt-4o",
            api_base="https://api.openai.com",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-1",
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="Hi")
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
            ),
        )

        await tracker.record_usage(
            request=req,
            request_id="req_test_123",
            target=target,
            response=resp,
        )

        mock_repo.create_usage_record.assert_awaited_once_with(
            request_id="req_test_123",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_123",
            feature="summarization",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            estimated_cost=Decimal("0.007500"),
            requested_model="gpt-4o",
            started_at=None,
            latency_ms=None,
            completed_at=None,
        )

    @pytest.mark.asyncio
    async def test_missing_usage_preserves_none(self) -> None:
        """Verify that when response.usage is None, all token counts and cost remain None."""
        mock_repo = AsyncMock()
        tracker = UsageTracker(repository=mock_repo)

        req = ChatCompletionRequest(
            model="custom-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        target = ProviderTarget(
            id="custom_provider",
            provider="custom_provider",
            model="custom-model",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-2",
            model="custom-model",
            choices=[],
            usage=None,
        )

        await tracker.record_usage(
            request=req,
            request_id="req_test_456",
            target=target,
            response=resp,
        )

        mock_repo.create_usage_record.assert_awaited_once_with(
            request_id="req_test_456",
            provider="custom_provider",
            model="custom-model",
            tenant_id=None,
            feature="chat",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            estimated_cost=None,
            requested_model="custom-model",
            started_at=None,
            latency_ms=None,
            completed_at=None,
        )

    @pytest.mark.asyncio
    async def test_partially_missing_total_tokens_preserved_as_none(self) -> None:
        """Verify provider-reported missing total_tokens is NOT automatically summed."""
        mock_repo = AsyncMock()
        tracker = UsageTracker(repository=mock_repo)

        req = ChatCompletionRequest(
            model="ollama-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        target = ProviderTarget(
            id="ollama_local",
            provider="ollama",
            model="qwen2.5:3b",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-3",
            model="ollama-model",
            choices=[],
            usage=CompletionUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=None,  # Missing total
            ),
        )

        await tracker.record_usage(
            request=req,
            request_id="req_test_789",
            target=target,
            response=resp,
        )

        mock_repo.create_usage_record.assert_awaited_once_with(
            request_id="req_test_789",
            provider="ollama",
            model="qwen2.5:3b",
            tenant_id=None,
            feature="chat",
            input_tokens=100,
            output_tokens=50,
            total_tokens=None,
            estimated_cost=Decimal("0.000000"),
            requested_model="ollama-model",
            started_at=None,
            latency_ms=None,
            completed_at=None,
        )

    @pytest.mark.asyncio
    async def test_repository_exception_does_not_raise(self) -> None:
        """Verify repository persistence exceptions are caught and never propagate to caller."""
        mock_repo = AsyncMock()
        mock_repo.create_usage_record.side_effect = Exception("DB connection refused")

        tracker = UsageTracker(repository=mock_repo)
        req = ChatCompletionRequest(
            model="qwen2.5:3b",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        target = ProviderTarget(
            id="ollama_local",
            provider="ollama",
            model="qwen2.5:3b",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-4",
            model="qwen2.5:3b",
            choices=[],
            usage=CompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        # Should complete gracefully without raising
        await tracker.record_usage(
            request=req,
            request_id="req_test_safe",
            target=target,
            response=resp,
        )

    @pytest.mark.asyncio
    async def test_timing_and_timestamps_forwarded(self) -> None:
        """Verify timing metrics are forwarded unchanged to usage repository."""
        from datetime import UTC, datetime

        mock_repo = AsyncMock()
        tracker = UsageTracker(repository=mock_repo)

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hi")],
            metadata={"tenant_id": "tenant_xyz"},
        )
        target = ProviderTarget(
            id="openai_primary",
            provider="openai",
            model="gpt-4o",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-fwd",
            model="gpt-4o",
            choices=[],
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )

        t_start = datetime(2026, 10, 1, 15, 29, 58, tzinfo=UTC)
        t_comp = datetime(2026, 10, 1, 15, 30, 0, tzinfo=UTC)
        await tracker.record_usage(
            request=req,
            request_id="req_test_fwd",
            target=target,
            response=resp,
            started_at=t_start,
            latency_ms=88.5,
            completed_at=t_comp,
        )

        mock_repo.create_usage_record.assert_awaited_once_with(
            request_id="req_test_fwd",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_xyz",
            feature="chat",
            input_tokens=5,
            output_tokens=10,
            total_tokens=15,
            estimated_cost=tracker.calculator.calculate_cost(
                provider="openai", model="gpt-4o", input_tokens=5, output_tokens=10
            ),
            requested_model="gpt-4o",
            started_at=t_start,
            latency_ms=88.5,
            completed_at=t_comp,
        )
