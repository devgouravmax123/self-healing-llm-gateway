# Load Testing & Performance Benchmarks

This document records the empirical results, test configurations, and observations from the k6 load testing suite (Phase 17).

---

## 1. Overview & Architecture

Load testing exercises the complete end-to-end gateway execution pipeline under controlled synthetic workloads:
- **Authentication**: SHA-256 API key hashing and PostgreSQL tenant validation.
- **Rate Limiting**: Redis sliding-window token-bucket enforcement.
- **Routing & Circuit Breakers**: Candidate filtering, circuit state verification, and provider selection.
- **Upstream Execution**: LiteLLM provider adapter dispatching requests to physical/local LLM backends (Ollama).
- **Observability**: Prometheus metrics collection and structured lifecycle logging.

---

## 2. Phase 17.2 — Concurrent Healthy-Provider Benchmark

### Test Description
Measures gateway routing, concurrency handling, and provider dispatch under multi-VU staged load with all components healthy and circuit breakers closed.

### Workload Configuration
- **Script**: `load_tests/concurrency.js`
- **Virtual Users (VUs)**: 10 max
- **Ramping Stages**:
  - Stage 1 (Ramp-up): 10s to 10 VUs
  - Stage 2 (Steady state): 30s at 10 VUs
  - Stage 3 (Ramp-down): 10s to 0 VUs
- **Target Model**: `qwen2.5:3b`
- **Base URL**: `http://localhost:8000`
- **Authentication**: Authenticated Bearer API key (`tenant_default`)
- **Environment Context**:
  - Temporary benchmark tenant RPM: `10,000 RPM` (used exclusively during this run to isolate provider concurrency from rate-limit rejections; normal default of `60 RPM` was restored immediately upon completion).
  - Source files modified for temporary override: **None**
  - Normal rate limit restored after benchmark: **Yes**

### Measured k6 Results

| Metric | Measured Value |
| :--- | ---: |
| **Total Requests** | 502 |
| **Total Iterations** | 502 |
| **Throughput (RPS)** | 10.01 req/s |
| **Average Latency** | 813.06 ms |
| **p50 Latency (Median)** | 915.00 ms |
| **p90 Latency** | 1.04 s |
| **p95 Latency** | 1.07 s |
| **p99 Latency** | ~3.06 s |
| **Minimum Latency** | 162.35 ms |
| **Maximum Latency** | 3.06 s |
| **Error Rate (`http_req_failed`)** | **0.00%** (0 out of 502) |
| **Maximum VUs** | 10 |

### HTTP Status Breakdown
- **2xx (200 OK)**: 502 (100.00%)
- **4xx (Client Errors)**: 0 (0.00%)
- **5xx (Server Errors)**: 0 (0.00%)
- **Other**: 0

### Server-Side Provider Evidence (`/metrics`)
- **Target Provider**: `ollama_default`
- **Model**: `qwen2.5:3b`
- **Provider Executions Dispatched**: 502
- **Successful Provider Executions**: 502 / 502 (100.00%)
- **Provider Errors**: 0
- **Rate Limit Rejections (`gateway_rate_limit_rejections_total`)**: 0
- **Circuit Breaker State**: `CLOSED` (`gateway_circuit_state{provider="ollama_default",state="closed"}: 1.0`)
- **Token Volume Processed**: 17,102 input tokens / 2,012 output tokens

### Threshold Evaluation
- `http_req_failed rate<0.01`: **PASSED** (`0.00%` failure rate).

---

## 3. Prior Diagnostic & Preliminary Runs

For transparency and reproducibility, the preliminary diagnostic iterations prior to the valid benchmark are recorded below:

1. **Authentication Variable Propagation Run (Diagnostic)**:
   - *Observation*: Subshell environment variable misconfiguration prevented the API key from being transmitted.
   - *Result*: 7,534 requests returned `401 Unauthorized` with `gateway_auth_failures_total{reason="invalid_credentials"}: 7534`.
   - *Resolution*: Corrected environment variable export to subprocesses.

2. **Default Rate-Limit Dominated Run (Diagnostic)**:
   - *Observation*: The standard default `DEFAULT_TENANT_RPM=60` strictly bounded admissions for a single tenant across 10 unpaced looping VUs.
   - *Result*: 60 requests succeeded (`200 OK`), while 3,311 requests were shed with `429 Too Many Requests` (`gateway_rate_limit_rejections_total: 3311`).
   - *Resolution*: Temporarily elevated the benchmark process environment to `DEFAULT_TENANT_RPM=10000` to measure healthy provider concurrency in Phase 17.2, followed by restoring the normal `60 RPM` setting. (Rate limiter behavior under stress is formally tested in Phase 17.4).

---

## 4. Phase 17.3 — Concurrent Provider-Failure / Chaos Benchmark

### Test Description
Measures gateway reliability behavior under concurrent load during deterministic upstream provider failure injection (`SERVER_ERROR` with bounded `failure_count=20`), including exponential backoff retries, circuit breaker failure accumulation, circuit state transitions, fast-failing during `OPEN`, and automatic circuit recovery (`HALF_OPEN` → `CLOSED`) following fault clearance.

### Workload & Chaos Configuration
- **Date/Time**: 2026-09-30T04:01:00+05:30
- **Script**: `load_tests/chaos_load.js`
- **Virtual Users (VUs)**: 10 max
- **Ramping Stages**:
  - Stage 1 (Ramp-up): 10s to 10 VUs
  - Stage 2 (Steady state): 60s at 10 VUs (chaos injection active, then cleared)
  - Stage 3 (Ramp-down): 10s to 0 VUs
- **Target Model**: `qwen2.5:3b`
- **Base URL**: `http://localhost:8000`
- **Authentication**: Authenticated Bearer API key (`tenant_default`)
- **Fault Type**: `SERVER_ERROR` (deterministic upstream HTTP 500)
- **Fault Target**: `ollama_default`
- **Failure Count**: 20 bounded failures injected via `POST /admin/chaos`
- **Environment Context**:
  - Temporary benchmark tenant RPM: `10,000 RPM` (used exclusively during this run; normal default of `60 RPM` was restored immediately upon completion).
  - Chaos enabled for benchmark: `CHAOS_ENABLED=true` with out-of-band `ADMIN_API_KEY`.
  - Normal configuration restored after benchmark: **Yes**

### Measured k6 Results

| Metric | Measured Value |
| :--- | ---: |
| **Total Requests** | 751 |
| **Total Iterations** | 751 |
| **Throughput (RPS)** | 9.39 req/s |
| **Average Latency (All Requests)** | 939.56 ms |
| **Average Latency (200 OK Requests)** | 935.23 ms |
| **p50 Latency (Median)** | 959.19 ms |
| **p90 Latency** | 1.05 s |
| **p95 Latency** | 1.13 s |
| **p99 Latency** | ~4.32 s |
| **Minimum Latency** | 161.04 ms |
| **Maximum Latency** | 4.32 s |
| **Error Rate (`http_req_failed`)** | **0.53%** (4 out of 751) |
| **Maximum VUs** | 10 |

### HTTP Status Breakdown
- **2xx (200 OK)**: 747 (99.47%)
- **5xx (502 / Upstream Server Error)**: 4 (0.53%)
- **4xx (Client Errors)**: 0 (0.00%)
- **Other**: 0

### Server-Side Provider & Observability Evidence (`/metrics`)
- **Target Provider**: `ollama_default`
- **Model**: `qwen2.5:3b`
- **Provider Executions Dispatched**: 768 total physical attempts
  - Successful Provider Executions: 748 (747 successful k6 HTTP 200 requests + 1 pre-chaos authenticated smoke verification request; the smoke probe is not part of the k6 workload)
  - Injected Provider Errors: 20 (`gateway_provider_errors_total{error_category="SERVER_ERROR"}: 20.0`)
- **Retries Triggered**: 16 (`gateway_retries_total{provider="ollama_default",reason="SERVER_ERROR"}: 16.0`)
- **Failure & Retry Reconciliation**:
  - **4 client requests** exhausted all 3 physical attempts (1 initial attempt + 2 retries):
    - 12 failed physical attempts
    - 8 retries
    - 4 final HTTP 502 Bad Gateway responses
  - **5 additional concurrent requests** entered the failure window and recovered when chaos tokens expired:
    - 3 requests experienced 2 physical failures (attempt 1/3 and attempt 2/3) and succeeded on retry 2 (attempt 3/3)
    - 2 requests experienced 1 physical failure (attempt 1/3) and succeeded on retry 1 (attempt 2/3)
    - 8 failed physical attempts
    - 8 retries (across failed and successful retry attempts)
    - All 5 requests ultimately returned HTTP 200 OK
  - **Summary**:
    - Total physical provider failures = 20 (`12 + 8`)
    - Total initial failed attempts = 9 (`4 + 5`)
    - Total retries = 16 (`8 + 8`)
    - Total HTTP 502 responses = 4
    - Total requests recovered after intermediate provider failures = 5
    - *Note*: Successful HTTP 200 requests can still contribute provider-error and retry metrics when intermediate physical attempts fail before a subsequent retry succeeds.
- **Circuit Breaker Lifecycle**:
  - `gateway_circuit_state{provider="ollama_default",state="open"}` was observed as the circuit tripped during the 20 injected failures.
  - No benchmark request was observed as a circuit-open fast failure in the recorded run; the circuit reached `OPEN` state, but the benchmark did not produce a measurable client request rejected specifically by an already-open circuit.
  - Upon expiration of the 20 bounded failures and expiration of cooldown, recovery proceeded through `HALF_OPEN` and back to `CLOSED` (`gateway_circuit_state{provider="ollama_default",state="closed"}: 1.0`).
- **Failovers**: `gateway_failovers_total` remained 0 because candidate-to-candidate failover was not benchmarked (only one `ProviderTarget`, `ollama_default`, was configured in the topology).
- **Token Volume Processed**: 25,432 input tokens / 2,992 output tokens processed across the benchmark window.

---

## 5. Phase 17.4 — Rate Limiting Under Load Benchmark

### Test Description
Measures gateway Redis-backed sliding-window rate limiting behavior under concurrent multi-VU load against the standard default rate limit (`DEFAULT_TENANT_RPM=60` with a 60-second window). Demonstrates that requests exceeding the configured tenant quota are fast-rejected with HTTP `429 Too Many Requests` containing standard rate-limit headers (`Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset`), while admitted requests execute with `200 OK`. Validates strict upstream provider isolation (rate-limited requests never reach LiteLLM/Ollama, invoke retries, trip circuit breakers, or consume provider tokens).

### Workload & Rate Limiter Configuration
- **Date/Time**: 2026-09-30T04:19:00+05:30
- **Script**: `load_tests/rate_limit.js`
- **Virtual Users (VUs)**: 10 max
- **Ramping Stages**:
  - Stage 1 (Ramp-up): 10s to 10 VUs
  - Stage 2 (Steady state): 30s at 10 VUs (sustained rate-limit saturation)
  - Stage 3 (Ramp-down): 10s to 0 VUs
- **Total Staged Duration**: 50s
- **Target Model**: `qwen2.5:3b`
- **Base URL**: `http://localhost:8000`
- **Authentication**: Authenticated Bearer API key (`tenant_default`)
- **Rate Limit Setting**: Standard `DEFAULT_TENANT_RPM = 60` (window = 60s)
- **Redis Pre-Test Cleanup**: Prior to the benchmark, residual rate-limit keys for `tenant_default` were cleared (`ratelimit:tenant_default`) to ensure a clean window baseline starting at 0.

### Measured k6 Results

| Metric | Measured Value |
| :--- | ---: |
| **Total Requests** | 4,316 |
| **Total Iterations** | 4,316 |
| **Throughput (RPS)** | 86.31 req/s |
| **Average Latency (All Requests)** | 93.66 ms |
| **Average Latency (200 OK Requests)** | 501.45 ms |
| **Average Latency (429 Rate-Limited Requests)** | 87.92 ms |
| **p50 Latency (Median)** | 83.66 ms |
| **p90 Latency** | 115.23 ms |
| **p95 Latency** | 152.72 ms |
| **Minimum Latency** | 12.05 ms |
| **Maximum Latency** | 2.48 s |
| **HTTP 429 Rejection Rate (`http_req_failed`)** | **98.61%** (4,256 out of 4,316) |
| **HTTP 200 Success Count** | **60** |
| **Other HTTP Status Errors** | **0** |
| **Maximum VUs** | 10 |

### HTTP Status Breakdown
- **2xx (200 OK)**: 60 (1.39%)
- **4xx (429 Too Many Requests)**: 4,256 (98.61%)
- **5xx (Server Errors)**: 0 (0.00%)
- **Other**: 0

### Rate-Limit Header Validation
All 4,256 HTTP 429 responses satisfied 100% of k6 checks for standard rate-limit headers:
- `Retry-After`: Present on all 429 responses (calculated dynamically from the oldest timestamp in the active sliding window log).
- `X-RateLimit-Limit`: `60`
- `X-RateLimit-Remaining`: `0`
- `X-RateLimit-Reset`: Present on all 429 responses (Unix epoch timestamp).
- `X-Request-ID`: Present on all 429 responses.

### Server-Side Provider & Observability Evidence (`/metrics`)

#### Metric Deltas Across Benchmark Window:
- **HTTP Gateway Requests**:
  - `gateway_requests_total{status_code="429"}` delta: **+4,256** (matches k6 429 count exactly)
  - `gateway_requests_total{status_code="200"}` delta: **+60** (matches k6 200 count exactly)
  - Total HTTP gateway requests delta: **+4,316** (matches k6 total requests)
- **Rate-Limit Rejections**:
  - `gateway_rate_limit_rejections_total{reason="limit_exceeded"}` delta: **+4,256** (matches k6 429 count exactly)
- **Provider Execution Isolation**:
  - `gateway_provider_requests_total{provider="ollama_default",status="success"}` delta: **+60** (exactly equal to the 60 admitted requests)
  - `gateway_provider_errors_total` delta: **0**
  - `gateway_retries_total` delta: **0**
  - `gateway_failovers_total` delta: **0**
  - `gateway_circuit_state{provider="ollama_default",state="closed"}`: **1.0** (circuit remained CLOSED throughout the benchmark)

---

## 6. Scope & Interpretation Notes
- **Rate-Limiter Correctness**: Under aggressive concurrency (10 looping VUs generating 86.31 req/s), the Redis atomic sliding-window log strictly bounded admissions to the configured quota (60 requests admitted in the 60s window).
- **Upstream Isolation**: Fast rejection at the gateway entry point prevented 4,256 excess requests from reaching Ollama / LiteLLM, maintaining upstream provider health and avoiding queue buildup.
- **Topology Limitation**: Candidate-to-candidate failover was not benchmarked because only one ProviderTarget (`ollama_default`) was configured. As per architecture guidelines, candidate failover was not simulated via duplicate or fake provider endpoints.
- **Reliability Validation**: The gateway suite has now empirically demonstrated healthy concurrency (Phase 17.2), provider failure recovery and circuit breaking (Phase 17.3), and atomic tenant rate-limiting under load (Phase 17.4).
- **Throughput Statement**: The throughput of `86.31 req/s` represents the measured throughput for this specific 10-VU Phase 17.4 rate-limiting workload on the local host with `qwen2.5:3b`. It reflects gateway admission and fast-rejection throughput rather than provider generation capacity.
