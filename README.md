# Self-Healing LLM Gateway

A robust, local-first API gateway and proxy engineered to provide high-availability reliability patterns in front of Large Language Model providers. Built with **Python 3.12**, **FastAPI**, **LiteLLM**, **Redis**, **PostgreSQL**, **Prometheus**, and **Grafana**, the gateway delivers OpenAI-compatible endpoints with automated error classification, exponential backoff with full jitter, three-state circuit breakers, logical provider failover, sliding-window rate limiting, token usage accounting, and full observability.

---

## 1. Architectural Overview

The gateway sits between client applications and downstream LLM inference providers, wrapping every request in multi-layer protection and observability pipelines.

```text
Client Application
        │
        ▼
   Nginx Proxy (Port 8000)
        │
        ▼
   FastAPI Gateway Core
        │
        ├── 1. Bearer Authentication & Tenant Identification
        │
        ├── 2. Redis Sliding-Window Rate Limiting (RPM Enforcement)
        │
        ├── 3. Provider Routing & Model Target Selection
        │
        ├── 4. Circuit Breaker State Verification (CLOSED / OPEN / HALF_OPEN)
        │
        ├── 5. Bounded Retry Engine (Exponential Backoff + Jitter)
        │
        ├── 6. Cross-Target Failover Engine
        │
        ▼
   LiteLLM Abstraction Layer
        │
        ▼
   LLM Provider (Local Ollama `qwen2.5:3b` / Deterministic Test Providers)
        │
        ├── 7. Token Usage & Cost Attribution (Durable PostgreSQL Storage)
        │
        ├── 8. Operational Health State Update (Redis Rolling Window)
        │
        └── 9. Observability Emission (Prometheus Metrics, Traces, Structured Logs)
```

### Supporting Infrastructure Topology

```text
                    ┌────────────────────────┐
                    │  Client / API Consumer │
                    └───────────┬────────────┘
                                │ :8000
                                ▼
                    ┌────────────────────────┐
                    │      Nginx Proxy       │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   FastAPI LLM Gateway  │
                    └─────┬──────┬─────┬─────┘
                          │      │     │
            ┌─────────────┘      │     └─────────────┐
            ▼                    ▼                   ▼
  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
  │      Redis       │ │    PostgreSQL    │ │      Ollama      │
  │ Operational State│ │ Durable Storage  │ │ Inference Backend│
  │ (Circuits/Limits)│ │ (Tenants/Usage)  │ │   (qwen2.5:3b)   │
  └──────────────────┘ └──────────────────┘ └──────────────────┘
            ▲                    ▲
            │                    │
            └────────────┬───────┘
                         │
                    ┌────┴───────────────────┐
                    │       Prometheus       │◄───── Scrapes :8000/metrics
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   Grafana Dashboard    │ (Port 3000 / 3002)
                    └────────────────────────┘
```

The Docker Compose deployment encapsulates seven distinct services:

1. **`nginx`** — Public reverse proxy on host port `8000`. Handles ingress and blocks unauthorized internal endpoints (e.g., `/metrics`).
2. **`gateway`** — Core FastAPI application executing routing, error classification, retry, circuit breakers, failover, and metrics collection.
3. **`redis`** — High-performance operational runtime state: distributed circuit breaker state machines, sliding-window rate limit buckets, and rolling latency/success counters.
4. **`postgres`** — Durable relational database storing tenant entities, hashed API keys, and immutable token usage / cost event logs.
5. **`ollama`** — Local inference server hosting the configured default model (`qwen2.5:3b`).
6. **`prometheus`** — Time-series metrics collection scraping internal gateway metrics every 10s.
7. **`grafana`** — Observability dashboards visualizing gateway throughput, error rates, circuit states, and p95 latencies.

---

## 2. Request Lifecycle

Every client interaction follows a deterministic 13-step lifecycle:

1. **Ingress**: Client sends an HTTP `POST /v1/chat/completions` request to the Nginx reverse proxy on port `8000`.
2. **Authentication**: Gateway extracts the `Authorization: Bearer <API_KEY>` header, hashes the secret using SHA-256, and resolves the tenant in PostgreSQL.
3. **Tenant Context**: An immutable `TenantContext` is established and bound to the request scope alongside a generated or propagated `X-Request-ID`.
4. **Rate Limiting**: The Redis sliding-window rate limiter checks if the tenant has exceeded their configured requests-per-minute (RPM) limit. If exceeded, an HTTP `429 Too Many Requests` is returned.
5. **Candidate Routing**: The `Router` inspects the requested model and retrieves ordered candidate `ProviderTarget` configurations matching capabilities and priority.
6. **Circuit Breaker Check**: The `CircuitBreakerManager` verifies the circuit state of the selected provider. If `OPEN`, the target is skipped immediately to avoid cascading upstream latency.
7. **Execution**: The `RetryManager` invokes `LiteLLMService` with a configured per-attempt timeout (`PROVIDER_TIMEOUT`).
8. **Error Classification**: If the provider call fails, the exception is classified into an `ErrorCategory` (`TIMEOUT`, `RATE_LIMITED`, `SERVER_ERROR`, `CONNECTION_ERROR`, `AUTH_ERROR`, etc.) with strict retryability semantics.
9. **Bounded Retry**: If the error is retryable, `RetryManager` pauses using truncated exponential backoff with full jitter and retries on the *same* provider up to `MAX_RETRIES`.
10. **Target Failover**: If retries on the primary provider are exhausted or the circuit trips `OPEN`, the `FailoverManager` shifts execution to the next eligible `ProviderTarget`.
11. **State & Usage Recording**:
    - On success: Circuit breaker records success (transitioning `HALF_OPEN` → `CLOSED`), rolling health latency samples are updated in Redis, and token/cost records are committed to PostgreSQL.
    - On failure: Circuit breaker increments consecutive failure counters (tripping to `OPEN` if threshold is reached).
12. **Telemetry**: Structured JSON log events, OpenTelemetry spans, and Prometheus metric increments are recorded.
13. **Egress**: The OpenAI-compatible JSON response is returned to the client with `X-Request-ID` and latency headers.

---

## 3. Quick Start (Docker Compose)

The entire gateway stack runs locally with zero external API dependencies or paid cloud accounts.

### 1. Clone and Configure
```bash
git clone https://github.com/<owner>/self-healing-llm-gateway.git
cd self-healing-llm-gateway
cp .env.example .env
```

### 2. Start the 7-Service Stack
```bash
docker compose up -d
```

### 3. Pull the Configured Local Model
The default model configured across the stack is **`qwen2.5:3b`**. Download it into the running Ollama container:

```bash
docker compose exec ollama ollama pull qwen2.5:3b
```

> [!TIP]
> To verify that the model is downloaded and ready for inference, run:
> `docker compose exec ollama ollama list`

### 4. Verify System Health & Readiness
Check health endpoints through the public Nginx entrypoint:

```bash
# Liveness Probe
curl -s http://localhost:8000/health

# Readiness Probe (Verifies PostgreSQL & Redis connections)
curl -s http://localhost:8000/ready
```

Expected response for `/ready`:
```json
{"status":"ready","redis":"ready","database":"ready"}
```

### 5. Generate Your Local Gateway API Key
All `/v1/` endpoints require a SHA-256 hashed API key stored in PostgreSQL. Generate a fresh, cryptographically secure local key:

```bash
docker compose exec gateway python scripts/bootstrap_local_demo.py
```

This bootstraps the local `demo_tenant` entity, stores only the SHA-256 hash in PostgreSQL, and prints your plaintext key once to stdout:
```text
============================================================
Self-Healing LLM Gateway — Local Demo API Key Generated
============================================================
Tenant ID : demo_tenant
API Key   : gw_live_...
------------------------------------------------------------
Keep this key private. It is stored hashed and printed once.
============================================================
```

> [!IMPORTANT]
> - No universal or hardcoded keys are seeded.
> - The plaintext key is printed only once upon generation and never stored in the database.
> - Do not commit or share this key.

### 6. Send Your First LLM Request
Use the generated key in the `Authorization: Bearer` header:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <PASTE_YOUR_API_KEY_HERE>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:3b",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in one sentence."}
    ],
    "temperature": 0.7
  }'
```

---

## 4. Local Deployment Modes

The repository provides three distinct execution modes:

| Mode | Command | Description |
| :--- | :--- | :--- |
| **A. Default Local Stack** | `docker compose up -d` | Normal gateway operation with a single Ollama instance, PostgreSQL, Redis, Nginx, Prometheus, and Grafana. |
| **B. Physical Failover Demo** | `docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d` | Spawns two independent, physically isolated Ollama containers (`ollama-primary` and `ollama-secondary`) on the Docker network for genuine process-level failover. See [`docs/PHYSICAL_FAILOVER_DEMO.md`](docs/PHYSICAL_FAILOVER_DEMO.md). |
| **C. Chaos E2E Demo** | `docker compose -f docker-compose.yml -f docker-compose.chaos-demo.yml up -d` | Enables the `/admin/chaos` fault injection engine for deterministic E2E reliability and circuit recovery testing. See [`docs/E2E_DEMO.md`](docs/E2E_DEMO.md). |

---

## 5. Observability & Monitoring

The gateway exports comprehensive metrics, structured logs, and distributed traces.

### Prometheus Metrics
Prometheus scrapes internal gateway metrics from `http://gateway:8000/metrics`. Major metric families include:
- `gateway_requests_total` — Total incoming HTTP requests by path, method, and status.
- `gateway_provider_requests_total` — Physical provider attempts labeled by `provider_id` and `model`.
- `gateway_provider_errors_total` — Provider failure counts labeled by `error_category`.
- `gateway_retries_total` — Count of executed retry attempts.
- `gateway_failovers_total` — Count of cross-target failovers executed.
- `gateway_circuit_state` — Current circuit state gauge (0=CLOSED, 1=OPEN, 2=HALF_OPEN) per provider.
- `gateway_rate_limit_rejections_total` — Number of rate limit rejections per tenant.
- `gateway_tokens_total` & `gateway_estimated_cost_usd_total` — Token usage and cost tracking.

### Grafana Dashboards
- **Grafana URL**: [http://localhost:3002](http://localhost:3002)
- **Default Credentials**: `admin` / `admin`
- **Provisioned Dashboard**: *Self-Healing LLM Gateway Overview* (live visualization of request throughput, error classification breakdown, circuit state indicators, and p95/p99 latency trends).

---

## 7. Chaos Engineering & Reliability Verification

The gateway includes an optional, secure Chaos Injection API for validating self-healing mechanisms under realistic failure conditions.

```text
POST /chaos/inject
Authorization: Bearer $ADMIN_API_KEY
Content-Type: application/json

{
  "provider_id": "test_provider_a",
  "error_category": "SERVER_ERROR",
  "status_code": 500,
  "duration_seconds": 60,
  "failure_rate": 1.0
}
```

### End-to-End Reliability Test Scenarios
Phase 20 provides automated and live verification covering five canonical failure scenarios documented in [`docs/E2E_DEMO.md`](docs/E2E_DEMO.md):

1. **Scenario 1 — Baseline Healthy Inference**: Clean execution with zero retries.
2. **Scenario 2 — Transient Provider Failure & Retry**: Simulated network hiccups trigger exponential backoff and transparent recovery on the same provider.
3. **Scenario 3 — Provider Failure & Circuit Tripping**: Consecutive server errors trip circuit to `OPEN`, immediately shielding the failing target.
4. **Scenario 4 — Cross-Provider Failover**: Traffic automatically diverts to secondary candidate when primary circuit is `OPEN`.
5. **Scenario 5 — All-Provider Failure Handling**: All targets fail; gateway returns controlled, clean HTTP 503 response without hanging or unbounded loops.

---

## 8. Development, Testing, and Quality Assurance

### Local Development Environment Setup
```bash
# Install dependencies into isolated virtual environment via uv
uv sync

# Run the complete test suite
uv run pytest -q

# Code formatting and linting checks
uv run ruff check .
uv run ruff format --check .

# Static type analysis
uv run mypy app tests
```

### Note on Host-Based Test Execution (17 Skipped Tests)
When running `uv run pytest` directly from the host workstation:
- **257 tests pass**
- **17 tests skip**

**Explanation**: In Phase 18, PostgreSQL (`5432`) and Redis (`6379`) were intentionally encapsulated inside the private `llm-gateway-network` Docker bridge network with no exposed host ports for security isolation. The 17 live integration tests dynamically probe for reachable databases and cleanly skip when executed outside the container network. **Skipped tests are not counted as passed.**

### Running All 274 Tests in the Docker Compose Network
To execute the complete test suite including all 17 live PostgreSQL, Redis, Alembic migration, and usage tracking integration tests against isolated test databases (`llm_gateway_test` and Redis DB 15):

```bash
docker run --rm \
  --network llm-gateway-network \
  -v "${PWD}:/app" \
  -w /app \
  -e DATABASE_URL="postgresql+asyncpg://postgres:postgres@postgres:5432/llm_gateway_test" \
  -e REDIS_URL="redis://redis:6379/15" \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -c "uv sync --frozen --extra dev && uv run pytest -q"
```

**Results in Docker Network**: **274 passed** (0 skipped, 0 failed).

---

## 9. Measured Load-Testing Results

During Phase 17, comprehensive load testing was executed across four distinct scenarios using k6. Full benchmark methodology, hardware specifications, and metrics are documented in [`docs/LOAD_TESTING.md`](docs/LOAD_TESTING.md).

| Benchmark Phase | Scenario Description | Concurrency (VUs) | Total Requests | Success Rate | Measured p95 Latency |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Phase 17.1** | Baseline Lightweight Load | 1 VU | 300 | 100% | 1.83 ms |
| **Phase 17.2** | Concurrency Baseline | 10 VUs | 3,000 | 100% | 4.96 ms |
| **Phase 17.3** | Chaos Fault Injection & Failover | 5 VUs | 1,000 | 100% | 6.82 ms |
| **Phase 17.4** | Sliding-Window Rate Limit Threshold | 10 VUs | 1,000 | 100% (Accurate 429s) | 2.10 ms |

---

## 10. Security Controls

Security architecture is documented in [`docs/SECURITY.md`](docs/SECURITY.md):

- **Authentication**: Tenant API keys are hashed with SHA-256 before database storage; raw API keys are never stored or logged in plain text.
- **Tenant Isolation**: All rate limiting, usage tracking, and access contexts are strictly partitioned by tenant ID.
- **Sensitive Data Redaction**: Gateway log formatters automatically redact Authorization tokens, API keys, and sensitive headers.
- **Administrative Protection**: Chaos injection and maintenance endpoints are guarded by a dedicated `ADMIN_API_KEY` and disabled by default in production.
- **Network Isolation**: PostgreSQL, Redis, and Ollama daemons are bound strictly to the internal Docker network and not exposed to the public internet.

---

## 11. Documentation Index

| Topic | Document Path | Summary |
| :--- | :--- | :--- |
| **Product Requirements** | [`docs/PRD.md`](docs/PRD.md) | Authoritative product requirements, goals, and core capabilities |
| **System Architecture** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | End-to-end component topology and service communication flow |
| **Component Design** | [`docs/DESIGN.md`](docs/DESIGN.md) | Circuit breaker state machine, retry math, and failover design |
| **Security Architecture** | [`docs/SECURITY.md`](docs/SECURITY.md) | Threat modeling, tenant isolation, and credential protection |
| **Deployment Guide** | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker Compose operational setup, service management, and troubleshooting |
| **Load Testing** | [`docs/LOAD_TESTING.md`](docs/LOAD_TESTING.md) | Benchmark records, methodology, and empirical measurements |
| **E2E Reliability Demo** | [`docs/E2E_DEMO.md`](docs/E2E_DEMO.md) | Canonical reliability scenarios and live demonstration walk-through |
| **Architecture Decisions** | [`docs/DECISIONS.md`](docs/DECISIONS.md) | Chronological record of architectural decisions (ADRs 1–20) |
| **Implementation Tasks** | [`docs/TASKS.md`](docs/TASKS.md) | Incremental task tracking across all project phases |

---

## 12. Project Status & Limitations

**Status**: Phases 1–21 Completed.

The project provides a containerized demonstration of self-healing gateway behavior with automated reliability tests, observability dashboards, chaos engineering injection, and empirical load-testing results.

### Documented Limitations
1. **Single Backend Instance**: The local Docker Compose setup utilizes a single Ollama container; multi-target routing demonstrates gateway-level logical candidate failover, not multi-node physical infrastructure redundancy.
2. **Local Evaluation Scope**: Performance measurements reflect a local containerized environment and should serve as comparative baselines rather than theoretical cloud-scale capacity limits.
