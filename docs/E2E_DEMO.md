# Phase 20 — End-to-End Reliability Demonstration

## 1. Overview & Honesty Statement

This document describes the End-to-End (E2E) reliability demonstration for the **Self-Healing LLM Gateway**.

> [!IMPORTANT]
> **Honesty Notice**:
> The automated test suite uses deterministic test-only `ProviderTarget` definitions (`test_provider_a` and `test_provider_b`) to prove the complete multi-attempt, failover, and circuit lifecycle with 100% reproducibility.
> The live operational demonstration uses two logical `ProviderTarget` identities (`ollama_primary` and `ollama_secondary`) backed by the containerized local Ollama service. It demonstrates gateway-level target routing, error classification, retry backoff, circuit breaking, failover, and self-healing recovery, **not** physical hardware or datacenter redundancy.

---

## 2. Canonical E2E Scenarios

The test suite in [`tests/test_e2e_reliability.py`](../tests/test_e2e_reliability.py) and the demonstration script in [`scripts/demo_e2e_reliability.py`](../scripts/demo_e2e_reliability.py) implement and verify five canonical reliability scenarios:

### Scenario A — Healthy Request Execution
- **Flow**: Client (`POST /v1/chat/completions`) -> Nginx -> Gateway (Authentication -> Rate Limiter -> Router -> Circuit Breaker CLOSED -> LiteLLM Execution -> Usage Persistence in PostgreSQL -> Prometheus Metrics -> Structured Logs -> OpenTelemetry Trace).
- **Outcome**: Returns `HTTP 200 OK` with valid message choices, tokens consumed, and zero failover events.

### Scenario B — Provider Failure & Automatic Failover
- **Flow**: Primary candidate (`test_provider_a` / priority 1) encounters a transient failure (`TIMEOUT` / `SERVER_ERROR`).
- **Resilience Behavior**:
  1. Primary target executes attempt 1 and fails.
  2. RetryManager calculates exponential backoff + jitter and executes bounded retries on the primary target.
  3. Retries exhaust without success -> circuit manager records failure event.
  4. FailoverManager catches the failure, logs `failover_started`, increments `gateway_failovers_total`, and routes to secondary candidate (`test_provider_b` / priority 2).
  5. Secondary target executes successfully.
- **Outcome**: Client receives transparent `HTTP 200 OK` without client-side retry or disruption.

### Scenario C — Circuit Breaker Trips (CLOSED -> OPEN)
- **Flow**: Repeated failures against a provider target exceed `CIRCUIT_FAILURE_THRESHOLD`.
- **Resilience Behavior**:
  1. Circuit state transitions to `OPEN`.
  2. Prometheus gauge `gateway_circuit_state{state="open"}` sets to `1.0`.
  3. Router candidate selector (`check_circuit=True`) filters out the open provider.
  4. Subsequent requests fast-bypass the failing provider without making physical network calls or inflating error metrics.

### Scenario D — Self-Healing Circuit Recovery (OPEN -> HALF-OPEN -> CLOSED)
- **Flow**: After `CIRCUIT_COOLDOWN_SECONDS` elapses:
  1. The circuit enters `HALF_OPEN` state.
  2. Exactly 1 probe request is permitted through the atomic Redis Lua lock; concurrent requests are safely blocked from swamping the recovering provider.
  3. The probe request succeeds.
  4. `circuit_breaker_manager.record_success()` resets failure counters and transitions circuit state back to `CLOSED`.
  5. Normal priority-ordered routing automatically resumes.

### Scenario E — All Providers Unavailable / Controlled Degradation
- **Flow**: All candidate providers fail or have circuits `OPEN`.
- **Resilience Behavior**:
  1. Candidates list is exhausted.
  2. Router raises `NoHealthyProviderError`.
  3. Gateway returns controlled `HTTP 503 Service Unavailable` with structured JSON error details.
  4. Infinite retry loops and cascade amplification are strictly prevented.

---

## 3. How to Run the Verifications

### 3.1 Run Automated E2E Test Suite
```powershell
uv run pytest tests/test_e2e_reliability.py -v
```

### 3.2 Run Live Demonstration Script
The live E2E demonstration exercises the `/admin/chaos` endpoint to deterministically inject transient errors and verify self-healing recovery.

1. **Start the chaos-enabled stack**:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.chaos-demo.yml up -d
   ```

2. **Generate your local API key** (if not already created):
   ```bash
   docker compose exec gateway python scripts/bootstrap_local_demo.py
   ```

3. **Execute the demonstration script**:
   ```bash
   python scripts/demo_e2e_reliability.py --url http://localhost:8000 --tenant-key <PASTE_YOUR_API_KEY>
   ```

---

## 4. Observability Evidence

During each demonstration scenario, verify observability metrics and logs:

- **Prometheus Metrics** (`http://localhost:9090`):
  - `gateway_requests_total`: Total requests received by the gateway.
  - `gateway_retries_total`: Retries executed per provider and error reason.
  - `gateway_failovers_total`: Successful failover transitions (`from_provider` -> `to_provider`).
  - `gateway_circuit_state`: One-hot state gauge (`closed`, `open`, `half_open`).
  - `gateway_tokens_total`: Input and output tokens delivered.
  - `gateway_estimated_cost_usd_total`: Attributed cost.

- **Structured Logs**:
  - `request_received`: Initial ingress log with `request_id`, `tenant_id`, and `model`.
  - `provider_selected`: Router target selection with priority step.
  - `retry_started`: Attempt index, delay duration, and categorized reason.
  - `failover_started`: Source provider and destination target.
  - `circuit_opened`: Failure threshold breach and state change.
  - `request_completed`: Final response duration and status.

- **OpenTelemetry Traces**:
  - Spans: `gateway.request` -> `routing.select` -> `provider.attempt` -> `failover.transition` -> `usage.record`.
