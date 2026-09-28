"""Tests for Phase 02 FastAPI API foundation."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_root_endpoint() -> None:
    """Verify that GET / returns the gateway info and operational status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == settings.app_name
        assert data["version"] == settings.app_version
        assert data["status"] == "operational"
        assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    """Verify that GET /health returns status healthy."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == settings.app_version
        assert data["environment"] == settings.environment


@pytest.mark.asyncio
async def test_ready_endpoint() -> None:
    """Verify that GET /ready returns status ready or degraded with ready=True."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ready", "degraded")
        assert data["ready"] is True


@pytest.mark.asyncio
async def test_chat_completions_valid_request() -> None:
    """Verify that POST /v1/chat/completions accepts a valid OpenAI-compatible request."""
    mock_choice = type(
        "MockMsg", (), {"role": "assistant", "content": "Hello from mock provider!"}
    )()
    mock_resp = type(
        "MockResponse",
        (),
        {
            "id": "chatcmpl-test-foundation",
            "model": "ollama/local-model-a",
            "choices": [
                type(
                    "Choice",
                    (),
                    {"message": mock_choice, "finish_reason": "stop", "index": 0},
                )()
            ],
            "usage": type(
                "Usage",
                (),
                {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            )(),
            "created": 1700000000,
        },
    )()

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_resp
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "local-model-a",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                "temperature": 0.7,
                "max_tokens": 100,
            }
            response = await client.post("/v1/chat/completions", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert data["object"] == "chat.completion"
            assert data["model"] == "local-model-a"
            assert len(data["choices"]) == 1
            assert data["choices"][0]["message"]["role"] == "assistant"
            assert data["choices"][0]["message"]["content"] == "Hello from mock provider!"
            assert data["choices"][0]["finish_reason"] == "stop"
            assert "usage" in data
            assert data["usage"]["total_tokens"] == 30


@pytest.mark.asyncio
async def test_chat_completions_invalid_request() -> None:
    """Verify that POST /v1/chat/completions rejects malformed request bodies."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing required 'messages'
        invalid_payload_1 = {"model": "local-model-a"}
        response1 = await client.post("/v1/chat/completions", json=invalid_payload_1)
        assert response1.status_code == 422

        # Empty messages list
        invalid_payload_2 = {"model": "local-model-a", "messages": []}
        response2 = await client.post("/v1/chat/completions", json=invalid_payload_2)
        assert response2.status_code == 422

        # Invalid message role
        invalid_payload_3 = {
            "model": "local-model-a",
            "messages": [{"role": "invalid_role", "content": "hello"}],
        }
        response3 = await client.post("/v1/chat/completions", json=invalid_payload_3)
        assert response3.status_code == 422

        # Empty message content
        invalid_payload_4 = {
            "model": "local-model-a",
            "messages": [{"role": "user", "content": "   "}],
        }
        response4 = await client.post("/v1/chat/completions", json=invalid_payload_4)
        assert response4.status_code == 422

        # Empty model name
        invalid_payload_5 = {
            "model": "   ",
            "messages": [{"role": "user", "content": "hello"}],
        }
        response5 = await client.post("/v1/chat/completions", json=invalid_payload_5)
        assert response5.status_code == 422


@pytest.mark.asyncio
async def test_request_id_generation_and_propagation() -> None:
    """Verify request ID is generated when absent and preserved when provided."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. When absent, generated automatically
        resp1 = await client.get("/health")
        assert "X-Request-ID" in resp1.headers
        generated_id = resp1.headers["X-Request-ID"]
        assert generated_id.startswith("req_")

        # 2. When supplied in request headers, preserved in response
        custom_id = "req_custom_client_id_12345"
        resp2 = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp2.headers.get("X-Request-ID") == custom_id


@pytest.mark.asyncio
async def test_openapi_docs_endpoint() -> None:
    """Verify that /docs and /openapi.json are accessible."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        docs_resp = await client.get("/docs")
        assert docs_resp.status_code == 200

        openapi_resp = await client.get("/openapi.json")
        assert openapi_resp.status_code == 200
        schema = openapi_resp.json()
        assert "paths" in schema
        assert "/v1/chat/completions" in schema["paths"]
        assert "/health" in schema["paths"]
        assert "/ready" in schema["paths"]
