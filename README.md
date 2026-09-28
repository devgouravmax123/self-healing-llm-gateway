# Self-Healing LLM Gateway

A reliable, local-first LLM API Gateway built with Python, FastAPI, LiteLLM, Redis, and PostgreSQL. It provides automatic error classification, retry logic with exponential backoff and jitter, circuit breaker mechanics, and provider failover to ensure maximum uptime and resilience.

## Current Development Phase

**Phase 01 — Project Foundation**

Currently establishing the base Python 3.12 environment, dependency structure, testing framework, code formatting, type checking, and configuration standards.

## Development Process

The project is built incrementally across strictly defined phases according to the design documents in `docs/`:

1. **Phase 01:** Project Foundation (Tooling, Python 3.12, uv, Ruff, mypy, pytest)
2. **Phase 02:** FastAPI API Foundation
3. **Phase 03:** LLM Provider Integration (LiteLLM & Ollama)
4. **Phase 04:** Provider Registry & Routing
5. **Phase 05:** Error Classification
6. **Phase 06:** Timeout & Retry
7. **Phase 07:** Circuit Breaker
8. **Phase 08:** Failover Mechanics
9. ... *(See `docs/TASKS.md` for full implementation phases)*

## Quick Start (Phase 01 Tooling)

### Requirements
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

### Environment Setup
```bash
# Install dependencies into virtual environment
uv sync

# Run the test suite
uv run pytest

# Run linting and code formatting checks
uv run ruff check .
uv run ruff format --check .

# Run type checker
uv run mypy .
```
