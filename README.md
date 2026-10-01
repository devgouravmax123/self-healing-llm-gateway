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

### Verify System Health & Readiness
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

### Generate Your Local Gateway API Key
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

### Send Your First LLM Request
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

---

## 4. Local Deployment Modes

The gateway supports three containerized deployment modes designed for local development, physical failover demonstration, and automated chaos testing.

### A. Default Local Stack
```bash
docker compose up -d
```
Normal local gateway operation using the standard 7-service stack:
- **FastAPI Gateway** (reverse-proxied behind Nginx on port `8000`)
- **Redis** (distributed token-bucket rate limiting and circuit state persistence)
- **PostgreSQL** (tenant metadata, hashed API key authentication, and token usage ledgers)
- **Ollama** (local inference backend serving `qwen2.5:3b`)
- **Prometheus** (metrics scraping on port `9090`)
- **Grafana** (pre-provisioned observability dashboards on port `3002`)
- **Nginx** (reverse proxy and ingress router)

### B. Physical Failover Demo
```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
```
Deploys two isolated Ollama containers (`ollama-primary` and `ollama-secondary`) on the Docker network to demonstrate real process/container termination, bounded retries, circuit breaker tripping, and automatic upstream failover. See [`docs/PHYSICAL_FAILOVER_DEMO.md`](docs/PHYSICAL_FAILOVER_DEMO.md).

### C. Chaos E2E Demo
```bash
docker compose -f docker-compose.yml -f docker-compose.chaos-demo.yml up -d
```
Enables the development-only deterministic chaos injection environment used for end-to-end reliability verification and circuit-recovery testing. See [`docs/E2E_DEMO.md`](docs/E2E_DEMO.md).

---

## 5. 🎬 Self-Healing Physical Failover Demo

This demonstration proves genuine process-level fault tolerance and recovery using two physically isolated Ollama inference containers (`ollama-primary` and `ollama-secondary`) on the Docker network.

```text
Healthy System
      ↓
Normal Request
      ↓
Primary Provider Failure
      ↓
Bounded Retries
      ↓
Circuit Opens
      ↓
Automatic Failover
      ↓
Secondary Provider Serves Request
      ↓
Primary Provider Restored
      ↓
Recovery Probe
      ↓
Circuit Returns to CLOSED
```

### What Makes This a Physical Failover Demo
- **Real Network Socket Drops**: Unlike simulated dashboard mocks, the primary provider container (`llm-gateway-ollama-primary`) is physically stopped using Docker (`docker stop`).
- **Independent Inference Engines**: Both `ollama-primary` and `ollama-secondary` run independent Ollama instances with dedicated storage volumes (`ollama_primary_data` and `ollama_secondary_data`), hosting the `qwen2.5:3b` model.
- **Continuous Gateway Availability**: The gateway, Nginx reverse proxy, PostgreSQL, Redis, Prometheus, and Grafana remain fully operational throughout the outage.
- **Architectural Details**: For full architecture diagrams and component relationships, see [`docs/PHYSICAL_FAILOVER_DEMO.md`](docs/PHYSICAL_FAILOVER_DEMO.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

### Step-by-Step Demonstration Walkthrough

#### Step 1 — Start the Physical Failover Stack
Start the gateway with the multi-container physical failover overlay:
```powershell
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
```
This deploys the 7 standard services plus independent `llm-gateway-ollama-primary` (priority 1) and `llm-gateway-ollama-secondary` (priority 2) containers.

#### Step 2 — Prepare the Model on Both Providers
Download and verify `qwen2.5:3b` in both Ollama instances:
```powershell
docker exec llm-gateway-ollama-primary ollama pull qwen2.5:3b
docker exec llm-gateway-ollama-secondary ollama pull qwen2.5:3b
```
Verify readiness:
```powershell
docker exec llm-gateway-ollama-primary ollama list
docker exec llm-gateway-ollama-secondary ollama list
```

#### Step 3 — Generate a Local Demo API Key
Bootstrap a fresh local demo API key:
```powershell
docker compose exec gateway python scripts/bootstrap_local_demo.py
```
This registers the local `demo_tenant`, stores only the SHA-256 hash in PostgreSQL, and prints your plaintext key once to stdout.

#### Step 4 — Send a Normal Baseline Request
Send an OpenAI-compatible completion request while both providers are healthy:
```powershell
curl -X POST http://localhost:8000/v1/chat/completions `
  -H "Authorization: Bearer <PASTE_YOUR_KEY>" `
  -H "Content-Type: application/json" `
  -d '{
    "model": "qwen2.5:3b",
    "messages": [{"role": "user", "content": "Ping"}]
  }'
```
- **Observed State**: Request routes to `ollama_primary` (priority 1).
- **Circuit Breaker**: `ollama_primary` = CLOSED, `ollama_secondary` = CLOSED.
- **Outcome**: Returns `HTTP 200 OK`.

#### Step 5 — Physically Stop the Primary Provider
Simulate a total crash of the primary provider:
```powershell
docker stop llm-gateway-ollama-primary
```
Verify that `llm-gateway-ollama-primary` is terminated while the gateway and `llm-gateway-ollama-secondary` remain running.

#### Step 6 — Send the Same Client Request
Send the exact same request again without modifying client configuration:
```powershell
curl -X POST http://localhost:8000/v1/chat/completions `
  -H "Authorization: Bearer <PASTE_YOUR_KEY>" `
  -H "Content-Type: application/json" `
  -d '{
    "model": "qwen2.5:3b",
    "messages": [{"role": "user", "content": "Ping"}]
  }'
```
- **Observed Outcome**: Client receives transparent `HTTP 200 OK` with valid completion text.

#### Step 7 — Retry and Automatic Failover Behavior
During Step 6, the gateway automatically executes:
```text
Primary failure (connection refused / socket timeout)
      ↓
Bounded retries on primary (exponential backoff + jitter)
      ↓
Retry exhaustion (MAX_RETRIES reached)
      ↓
Failover engine activates: ollama_primary → ollama_secondary (reason=retry_exhausted)
      ↓
Secondary provider executes request successfully
```

#### Step 8 — Circuit Breaker Protects Failing Provider
Because consecutive failures on `ollama_primary` reached `CIRCUIT_FAILURE_THRESHOLD`:
- **`ollama_primary` Circuit**: Transitions to **OPEN**.
- **`ollama_secondary` Circuit**: Remains **CLOSED**.
- **Protection**: Subsequent requests instantly bypass the dead primary socket without wasting timeout latency, routing directly to `ollama_secondary`.

#### Step 9 — Observe Operational Evidence in Grafana
Open Grafana at **[http://localhost:3002](http://localhost:3002)** (`admin` / `admin`) to observe live telemetry:
- **Circuit State Panel**: Shows `ollama_primary` state gauge set to OPEN (`1.0`).
- **Failovers Metric**: `gateway_failovers_total{from_provider="ollama_primary", to_provider="ollama_secondary"}` incremented.
- **Provider Request Counts**: Shows requests shifting from primary to secondary.
- **Latency & Error Breakdown**: Records connection error categories and per-attempt timing.

#### Step 10 — Restore the Primary Provider
Restart the primary Ollama container:
```powershell
docker start llm-gateway-ollama-primary
```
Wait a few seconds for container health check to report healthy (`docker ps` shows `healthy`).

#### Step 11 — Self-Healing Recovery Probe
After `CIRCUIT_COOLDOWN_SECONDS` elapses, send another request:
```powershell
curl -X POST http://localhost:8000/v1/chat/completions `
  -H "Authorization: Bearer <PASTE_YOUR_KEY>" `
  -H "Content-Type: application/json" `
  -d '{
    "model": "qwen2.5:3b",
    "messages": [{"role": "user", "content": "Ping"}]
  }'
```
- **Recovery Lifecycle**: The circuit enters `HALF_OPEN` state, permitting a single probe request through an atomic concurrency lock.
- **Probe Success**: When the probe succeeds against restored `ollama_primary`, `circuit_breaker_manager.record_success()` resets failure counters and restores the circuit to **CLOSED**.

> [!NOTE]
> The `HALF_OPEN` recovery probe is intentionally transient and may occur between Prometheus scrape intervals. Grafana metrics will reflect the restored **CLOSED** state. Application logs provide granular evidence of the recovery probe.

#### Step 12 — Final Verified State
- **`ollama_primary` Circuit**: **CLOSED** (accepting normal primary traffic).
- **`ollama_secondary` Circuit**: **CLOSED** (healthy standby).
- **Routing**: Priority-1 traffic automatically returns to the primary provider.

---

### What This Demo Proves
- **Local Reproducibility**: Real multi-process provider failures can be reproduced entirely on a local workstation with zero cloud dependencies.
- **Failure Detection & Containment**: Socket drops and timeouts are caught and classified within bounded execution budgets.
- **Provider Shielding**: Circuit breakers trip to OPEN, preventing dead providers from amplifying system latency.
- **Zero Client Modification**: Clients communicate with a single OpenAI-compatible endpoint; failover and retries happen transparently.
- **Automated Recovery**: After the failed provider is restored, the gateway automatically performs the recovery probe and returns the provider circuit to CLOSED without requiring a gateway restart or manual routing change.
- **Full Observability**: Every transition, attempt, retry, and failover is measurable in Prometheus and Grafana.

---

### Run the Complete Automated Script
You can also run the entire 12-step sequence automatically with live step verification and Prometheus metric assertions:

```powershell
# 1. Start the stack and pull models
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
docker exec llm-gateway-ollama-primary ollama pull qwen2.5:3b
docker exec llm-gateway-ollama-secondary ollama pull qwen2.5:3b

# 2. Bootstrap API key
docker compose exec gateway python scripts/bootstrap_local_demo.py

# 3. Run automated physical failover script
python scripts/demo_physical_failover.py --api-key <PASTE_YOUR_API_KEY>
```

---

### Demo Evidence — Step-by-Step Screenshots

#### 01 — Baseline Normal Request (Healthy Primary Provider)
![Baseline Normal Request](docs/images/demo/01_baseline_normal_request.png)
*A client completion request sent to `http://localhost:8000/v1/chat/completions` using the bootstrapped demo API key returns `HTTP 200 OK` from `qwen2.5:3b` via the healthy primary provider.*

#### 02 — Healthy Baseline State in Grafana (Traffic & Latency)
![Healthy Baseline State - Traffic & Latency](docs/images/demo/02_grafana_healthy_baseline_top.png)
*Grafana overview displaying initial throughput with `HTTP 200` responses and upstream execution latency metrics while both `ollama_primary` and `ollama_secondary` circuit breakers are in the `[closed]` Active state.*

#### 03 — Healthy Baseline State in Grafana (Reliability & Token Counts)
![Healthy Baseline State - Reliability & Tokens](docs/images/demo/03_grafana_healthy_baseline_bottom.png)
*Grafana reliability panels showing 0 retries, 0 failovers, and delivered input/output tokens during initial baseline operation with healthy providers.*

#### 04 — Failover Execution & Primary Circuit Tripped to OPEN (Traffic & Circuits)
![Failover Circuit OPEN - Traffic & Circuits](docs/images/demo/04_grafana_failover_circuit_open_top.png)
*Following the physical shutdown of `llm-gateway-ollama-primary`, client requests continue succeeding (`HTTP 200`). Grafana shows `ollama_primary [open]` after connection errors, while `ollama_secondary [closed]` actively takes over upstream traffic.*

#### 05 — Failover Transitions & Secondary Token Delivery in Grafana
![Failover Transitions & Tokens](docs/images/demo/05_grafana_failover_circuit_open_bottom.png)
*Grafana reliability and token panels registering retry activity, the failover transition metric (`ollama_primary → ollama_secondary (retry_exhausted)`), and token delivery continuing through the secondary provider.*

#### 06 — Transparent Client Response During Primary Outage
![Transparent Failover Response](docs/images/demo/06_transparent_failover_response.png)
*Client sends the exact same completion request while `llm-gateway-ollama-primary` is down. The gateway handles the failure internally, returning `HTTP 200 OK` with model response seamlessly.*

#### 07 — Restoring Primary Provider Container
![Docker Start Primary Container](docs/images/demo/07_docker_start_primary_recovery.png)
*Restarting `llm-gateway-ollama-primary` via `docker start`. The container status progresses through `health: starting` before returning to healthy status alongside all running services.*

#### 08 — Recovery Request Execution
![Recovery Request Response](docs/images/demo/08_recovery_probe_response.png)
*Subsequent client request sent after primary restoration executes successfully (`HTTP 200 OK`), initiating probe recovery on the primary inference provider.*

#### 09 — Restored System & Closed Circuit Breakers in Grafana
![Restored Circuits Closed](docs/images/demo/09_grafana_recovered_circuit_closed_top.png)
*Grafana traffic and circuit breaker state panel showing both `ollama_primary [closed]` and `ollama_secondary [closed]` back in the Active state following successful recovery.*

#### 10 — Post-Recovery Metrics & Token Delivery in Grafana
![Post-Recovery Metrics](docs/images/demo/10_grafana_recovered_metrics_bottom.png)
*Grafana reliability and token panels illustrating the stabilized system: failover rates return to 0, primary provider traffic resumes, and total delivered tokens reach 164 input / 24 output.*

#### 11 — Verified Primary Execution Following Full Recovery
![Post-Recovery Execution](docs/images/demo/11_post_recovery_request_success.png)
*Client completion request confirms stable primary routing following system recovery, returning `HTTP 200 OK` with model content `"Primary provider is working."`.*

#### 12 — Steady-State Operational Telemetry in Grafana (Top Panels)
![Steady-State Top Panels](docs/images/demo/12_grafana_steady_state_traffic_top.png)
*Grafana top view displaying steady-state traffic metrics, normalized upstream execution latency across models, and active CLOSED circuit breaker status.*

#### 13 — Steady-State Reliability & Token Accounting in Grafana (Bottom Panels)
![Steady-State Bottom Panels](docs/images/demo/13_grafana_steady_state_overview_bottom.png)
*Grafana lower view showing 0 error dispatch rates, zero active failovers during steady-state operation, and consistent cumulative token metrics.*

#### 14 — Physical Container Termination via Docker CLI
![Docker Stop Primary Command](docs/images/demo/14_docker_stop_primary_failover_trigger.png)
*PowerShell console showing the physical execution of `docker stop llm-gateway-ollama-primary` and `docker compose ps` verifying that `llm-gateway-ollama-primary` was taken offline while the gateway and secondary instances remained running.*

---

## 6. Observability & Monitoring

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
