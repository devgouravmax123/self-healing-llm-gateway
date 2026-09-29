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

## 4. Scope & Interpretation Notes
- **Throughput Statement**: The throughput of `10.01 req/s` represents the measured throughput for this specific 10-VU Phase 17.2 workload on the local host with `qwen2.5:3b`. It does not represent maximum capacity or an infrastructure guarantee.
- **Isolation**: Latencies reflect local end-to-end round-trip execution including database authentication, rate limit checking, gateway routing, and local Ollama inference.
