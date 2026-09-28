\# Self-Healing LLM Gateway — Architecture



\## 1. Purpose



This document defines the high-level architecture of the Self-Healing LLM Gateway.



The gateway sits between client applications and one or more LLM providers. Its primary responsibility is to make LLM access more reliable, observable, and controllable.



The architecture is designed around:



\* Reliability

\* Automatic failure detection

\* Automatic failover

\* Circuit breaking

\* Controlled retries

\* Observability

\* Cost and usage tracking

\* Multi-tenant support

\* Local-first development

\* Simple deployment

\* Measurable behavior



This document is the architectural source of truth for the project.



\---



\# 2. High-Level Architecture



```text

&#x20;                        ┌─────────────────────┐

&#x20;                        │    Client / App     │

&#x20;                        └──────────┬──────────┘

&#x20;                                   │

&#x20;                                   │ OpenAI-compatible API

&#x20;                                   ▼

&#x20;                        ┌─────────────────────┐

&#x20;                        │       Nginx         │

&#x20;                        │   Reverse Proxy     │

&#x20;                        └──────────┬──────────┘

&#x20;                                   │

&#x20;                                   ▼

&#x20;             ┌─────────────────────────────────────────┐

&#x20;             │           FastAPI Gateway                │

&#x20;             │                                         │

&#x20;             │  ┌───────────────────────────────────┐  │

&#x20;             │  │ Request Validation                 │  │

&#x20;             │  │ Authentication / Tenant Context   │  │

&#x20;             │  │ Request ID                        │  │

&#x20;             │  │ Rate Limiting                     │  │

&#x20;             │  │ Routing                           │  │

&#x20;             │  │ Circuit Breaker                   │  │

&#x20;             │  │ Timeout / Retry                    │  │

&#x20;             │  │ Failover                          │  │

&#x20;             │  │ Cost Attribution                  │  │

&#x20;             │  └───────────────────────────────────┘  │

&#x20;             └───────────────┬─────────────────────────┘

&#x20;                             │

&#x20;                             ▼

&#x20;                   ┌──────────────────┐

&#x20;                   │     LiteLLM      │

&#x20;                   │ Provider Adapter  │

&#x20;                   └────────┬─────────┘

&#x20;                            │

&#x20;             ┌──────────────┼──────────────┐

&#x20;             │              │              │

&#x20;             ▼              ▼              ▼

&#x20;      ┌────────────┐ ┌────────────┐ ┌────────────┐

&#x20;      │  Ollama A  │ │  Ollama B  │ │  Ollama C  │

&#x20;      │ Local LLM  │ │ Local LLM  │ │ Local LLM  │

&#x20;      └────────────┘ └────────────┘ └────────────┘



&#x20;             ┌─────────────────────────────────┐

&#x20;             │          Operational State      │

&#x20;             │                                 │

&#x20;             │             Redis               │

&#x20;             │  - Circuit state                │

&#x20;             │  - Health counters              │

&#x20;             │  - Rate limits                  │

&#x20;             │  - Temporary operational state  │

&#x20;             └─────────────────────────────────┘



&#x20;             ┌─────────────────────────────────┐

&#x20;             │          Durable Data           │

&#x20;             │                                 │

&#x20;             │          PostgreSQL              │

&#x20;             │  - Tenants                      │

&#x20;             │  - API usage                    │

&#x20;             │  - Token usage                  │

&#x20;             │  - Cost records                 │

&#x20;             │  - Audit/history                │

&#x20;             └─────────────────────────────────┘



&#x20;      ┌──────────────────────┐       ┌──────────────────────┐

&#x20;      │      Prometheus      │──────▶│       Grafana         │

&#x20;      │       Metrics        │       │      Dashboards       │

&#x20;      └──────────────────────┘       └──────────────────────┘



&#x20;      ┌──────────────────────┐

&#x20;      │   OpenTelemetry      │

&#x20;      │       Traces         │

&#x20;      └──────────────────────┘



&#x20;      ┌──────────────────────┐

&#x20;      │   k6 + Chaos Tests   │

&#x20;      │ Failure/Load Testing │

&#x20;      └──────────────────────┘

```



\---



\# 3. Architectural Components



\## 3.1 Client / Application



The client is any application that wants to use an LLM.



Examples:



\* Web application

\* Mobile application

\* Internal backend

\* CLI application

\* Test client



The client communicates with the gateway using an OpenAI-compatible API format.



The client should not need to know which underlying provider is currently serving the request.



For example:



```text

Client

&#x20;  |

&#x20;  | POST /v1/chat/completions

&#x20;  ▼

Gateway

&#x20;  |

&#x20;  | Provider A unavailable

&#x20;  ▼

Provider B

```



The client continues using the same gateway endpoint.



\---



\# 4. Nginx



Nginx acts as the reverse proxy in front of the FastAPI gateway.



Responsibilities:



\* Receive incoming HTTP traffic

\* Forward requests to FastAPI

\* Provide a clear network boundary

\* Handle basic proxy-level configuration

\* Support production-like deployment architecture



Nginx is not responsible for:



\* LLM routing

\* Provider health decisions

\* Circuit breaking

\* Retry logic

\* Cost calculation



Those decisions belong to the gateway.



\---



\# 5. FastAPI Gateway



FastAPI is the central application component.



It is responsible for coordinating the request lifecycle.



The gateway contains the following logical responsibilities.



\## 5.1 Request Validation



Validates incoming requests.



Examples:



\* Required fields

\* Message structure

\* Model name

\* Request metadata

\* Tenant information



Invalid requests should be rejected before reaching an LLM provider.



\---



\## 5.2 Request ID



Every request receives a unique request ID.



Example:



```text

request\_id = req\_8f72c91

```



The request ID is used to correlate:



\* Logs

\* Metrics

\* Traces

\* Provider attempts

\* Retries

\* Failovers

\* Usage records



A single request should be traceable from beginning to end.



\---



\## 5.3 Tenant and Feature Context



Each request should carry identifying operational metadata.



Example:



```text

tenant\_id = tenant\_123

feature = document\_summary

request\_id = req\_8f72c91

```



This allows the system to answer questions such as:



\* Which tenant generated the request?

\* Which application feature generated it?

\* Which provider served it?

\* How many tokens were used?

\* What was the estimated cost?

\* How many failures occurred?



\---



\## 5.4 Rate Limiting



Rate limiting prevents a tenant or client from sending excessive traffic.



Redis will be used for shared rate-limit state.



Example:



```text

Tenant A

&#x20;  │

&#x20;  ├── Request 1

&#x20;  ├── Request 2

&#x20;  ├── Request 3

&#x20;  └── ...



Redis

&#x20;  │

&#x20;  └── tracks request allowance

```



Rate limiting should occur before expensive provider calls.



\---



\# 6. Routing Layer



The routing layer decides which provider/model should receive a request.



A request may have a preferred provider order.



Example:



```text

1\. Ollama Model A

2\. Ollama Model B

3\. Ollama Model C

```



The router considers provider eligibility.



A provider may be skipped when:



\* Circuit is OPEN

\* Provider is unhealthy

\* Provider is temporarily unavailable

\* Provider exceeds configured limits

\* Provider does not satisfy the request



The routing layer should not blindly select the first configured provider.



\---



\# 7. Circuit Breaker



Each provider/model route has an independent circuit breaker.



The circuit has three states:



```text

&#x20;            failure threshold

&#x20;       ┌─────────────────────────┐

&#x20;       │                         ▼

&#x20;    CLOSED ───────────────────▶ OPEN

&#x20;       ▲                         │

&#x20;       │                         │ cooldown

&#x20;       │                         ▼

&#x20;       └──────────────────── HALF-OPEN

&#x20;                successful probe

```



\## CLOSED



The provider is considered healthy.



Normal requests are allowed.



Failures are recorded.



\---



\## OPEN



The provider is considered unhealthy.



Normal requests should not be sent to the provider.



Requests should be routed to another eligible provider when possible.



This prevents repeatedly sending traffic to a known failing provider.



\---



\## HALF-OPEN



After the configured recovery period, the system allows a controlled probe.



The purpose is to determine whether the provider has recovered.



\### Probe succeeds



```text

HALF-OPEN → CLOSED

```



Normal traffic resumes.



\### Probe fails



```text

HALF-OPEN → OPEN

```



The provider remains unavailable.



\---



\# 8. Retry Layer



Retries handle temporary failures.



Retries should not happen for every error.



Examples of potentially retryable failures:



\* Timeout

\* Temporary network failure

\* Provider 5xx

\* Rate limit, when appropriate



Examples of generally non-retryable failures:



\* Invalid request

\* Authentication failure

\* Unsupported model

\* Invalid API configuration



Retry behavior should include:



```text

Attempt 1

&#x20;  ↓

Failure

&#x20;  ↓

Backoff

&#x20;  ↓

Attempt 2

&#x20;  ↓

Failure

&#x20;  ↓

Backoff

&#x20;  ↓

Attempt 3

```



The system should use exponential backoff with jitter.



Retry limits must be configurable.



\---



\# 9. Timeout Layer



Every provider request must have a timeout.



A timeout prevents a single provider from holding a gateway request indefinitely.



Example:



```text

Gateway

&#x20;  |

&#x20;  | request

&#x20;  ▼

Provider A

&#x20;  |

&#x20;  | no response

&#x20;  |

&#x20;  | timeout

&#x20;  ▼

Gateway

&#x20;  |

&#x20;  ▼

Failover decision

```



Timeouts must be measured and recorded.



\---



\# 10. Failover



Failover allows the gateway to switch to another provider/model when the current provider cannot successfully serve the request.



Example:



```text

Request

&#x20;  │

&#x20;  ▼

Provider A

&#x20;  │

&#x20;  ├── Timeout

&#x20;  │

&#x20;  ▼

Provider B

&#x20;  │

&#x20;  └── Success

```



The client should receive the successful response from Provider B without needing to understand the internal failure of Provider A.



Every failover must be observable.



The system should record:



\* Original provider

\* Failed provider

\* Failure reason

\* Replacement provider

\* Request ID

\* Timestamp

\* Number of attempts



\---



\# 11. Error Classification



Provider failures should be converted into a normalized internal error taxonomy.



Supported categories:



```text

RATE\_LIMIT

TIMEOUT

SERVER\_ERROR

AUTH\_ERROR

CONTENT\_FILTER

NETWORK\_ERROR

BAD\_REQUEST

UNKNOWN

```



This allows routing and retry decisions to operate on consistent error types instead of provider-specific error formats.



Example:



```text

Provider-specific error

&#x20;       ↓

Error classifier

&#x20;       ↓

TIMEOUT

&#x20;       ↓

Retry / Failover decision

```



\---



\# 12. LiteLLM Layer



LiteLLM provides the abstraction between the gateway and different LLM providers.



The gateway should not contain provider-specific communication logic wherever LiteLLM can provide the abstraction.



Conceptually:



```text

FastAPI Gateway

&#x20;      ↓

&#x20;   LiteLLM

&#x20;      ↓

&#x20;┌─────┼─────┐

&#x20;▼     ▼     ▼

A      B     C

```



This makes adding or changing providers easier.



The gateway remains responsible for reliability decisions such as:



\* Circuit breaking

\* Failover

\* Retry policy

\* Health state

\* Request metadata

\* Observability



LiteLLM remains responsible for provider/model abstraction.



\---



\# 13. Ollama



Ollama provides local LLM inference.



The initial architecture should use local Ollama models so that the complete project can run without mandatory paid API services.



Example:



```text

LiteLLM

&#x20;  ├── Ollama Model A

&#x20;  ├── Ollama Model B

&#x20;  └── Ollama Model C

```



Different local models can be treated as separate routing targets.



External providers may be added later, but they must remain optional.



\---



\# 14. Redis



Redis stores frequently changing operational state.



Redis is appropriate for information that must be accessed quickly and changes frequently.



The gateway may use Redis for:



\* Circuit breaker state

\* Failure counters

\* Rolling health metrics

\* Rate-limit counters

\* Temporary request state

\* Short-lived coordination state



Example:



```text

Gateway

&#x20;  │

&#x20;  ├── read circuit state

&#x20;  ├── update failure count

&#x20;  ├── update health metrics

&#x20;  └── update rate limit

&#x20;         │

&#x20;         ▼

&#x20;       Redis

```



Redis is not the primary durable database for business/audit records.



\---



\# 15. PostgreSQL



PostgreSQL stores durable application data.



Potential data includes:



\* Tenants

\* API keys or API-key metadata

\* Request usage

\* Token usage

\* Cost records

\* Audit records

\* Historical provider events

\* Configuration that must survive restarts



PostgreSQL is used when information needs durable storage.



\---



\# 16. Redis vs PostgreSQL



The project deliberately separates operational state from durable historical data.



| Requirement             | Redis | PostgreSQL |

| ----------------------- | ----- | ---------- |

| Circuit state           | Yes   | No         |

| Rate-limit counters     | Yes   | No         |

| Rolling health counters | Yes   | No         |

| Temporary state         | Yes   | No         |

| Tenant records          | No    | Yes        |

| Usage history           | No    | Yes        |

| Cost history            | No    | Yes        |

| Audit history           | No    | Yes        |

| Durable records         | No    | Yes        |



The exact data model will be defined in `DESIGN.md`.



\---



\# 17. Health Monitoring



The gateway continuously records provider health information.



Important measurements include:



\* Total requests

\* Successful requests

\* Failed requests

\* Success rate

\* Failure rate

\* p50 latency

\* p95 latency

\* p99 latency

\* Error categories

\* Last successful request

\* Last failure

\* Circuit state

\* Failover count



Conceptually:



```text

Provider

&#x20;  │

&#x20;  ├── request

&#x20;  ├── latency

&#x20;  ├── success/failure

&#x20;  └── error type

&#x20;         │

&#x20;         ▼

&#x20;   Health Tracker

&#x20;         │

&#x20;         ▼

&#x20;       Redis

```



Health information is used by the routing and circuit-breaker systems.



\---



\# 18. Prometheus



Prometheus collects numerical metrics from the gateway.



Examples:



```text

gateway\_requests\_total

gateway\_request\_duration\_seconds

gateway\_retries\_total

gateway\_failovers\_total

gateway\_provider\_errors\_total

gateway\_circuit\_state

gateway\_tokens\_total

gateway\_estimated\_cost

```



Metrics should support analysis of:



\* Traffic

\* Reliability

\* Latency

\* Provider health

\* Failovers

\* Retries

\* Circuit behavior

\* Token usage

\* Cost



Metric names are implementation details and may evolve during development.



\---



\# 19. Grafana



Grafana provides dashboards over Prometheus metrics.



The dashboard should make the gateway's behavior visible.



Important dashboard areas:



\### Request Overview



\* Requests per second

\* Success rate

\* Error rate



\### Latency



\* p50

\* p95

\* p99



\### Provider Health



\* Provider success rate

\* Provider failure rate

\* Provider latency

\* Circuit state



\### Reliability



\* Retries

\* Failovers

\* Timeouts

\* Error categories



\### Usage



\* Tokens

\* Estimated cost

\* Usage by tenant

\* Usage by feature



\---



\# 20. OpenTelemetry



OpenTelemetry provides distributed tracing.



A request should be traceable across important operations.



Example:



```text

Request

&#x20;│

&#x20;├── Validation

&#x20;│

&#x20;├── Rate limit check

&#x20;│

&#x20;├── Routing decision

&#x20;│

&#x20;├── Provider A attempt

&#x20;│      └── timeout

&#x20;│

&#x20;├── Provider B attempt

&#x20;│      └── success

&#x20;│

&#x20;└── Usage recording

```



Tracing should make it possible to understand where latency and failures occurred.



\---



\# 21. Structured Logging



The gateway will produce structured JSON logs.



Example conceptual event:



```json

{

&#x20; "request\_id": "req\_8f72c91",

&#x20; "tenant\_id": "tenant\_123",

&#x20; "feature": "document\_summary",

&#x20; "provider": "ollama-a",

&#x20; "model": "model-a",

&#x20; "event": "provider\_timeout",

&#x20; "error\_type": "TIMEOUT"

}

```



Logs should be machine-readable and suitable for debugging.



Sensitive information must not be logged unnecessarily.



\---



\# 22. Cost and Usage Tracking



The gateway records usage for each request.



Possible dimensions:



```text

tenant

feature

request

provider

model

input tokens

output tokens

total tokens

estimated cost

```



For local Ollama models, monetary cost may be represented as zero while token usage and resource usage remain measurable.



For external providers, provider pricing can be configured later.



Cost calculation must be explicit and should not be presented as exact when it is only an estimate.



\---



\# 23. Request Lifecycle



A normal request follows this architecture:



```text

Client

&#x20; │

&#x20; ▼

Nginx

&#x20; │

&#x20; ▼

FastAPI

&#x20; │

&#x20; ├── Validate request

&#x20; ├── Generate/propagate request ID

&#x20; ├── Identify tenant

&#x20; ├── Check rate limit

&#x20; ├── Select provider

&#x20; ├── Check circuit state

&#x20; │

&#x20; ▼

LiteLLM

&#x20; │

&#x20; ▼

Provider

&#x20; │

&#x20; ▼

LLM Response

&#x20; │

&#x20; ├── Record latency

&#x20; ├── Record health

&#x20; ├── Record usage

&#x20; ├── Record cost

&#x20; ├── Emit logs

&#x20; ├── Emit metrics

&#x20; └── Emit trace

&#x20; │

&#x20; ▼

Client

```



\---



\# 24. Failure Request Lifecycle



When the selected provider fails:



```text

Client

&#x20; │

&#x20; ▼

Gateway

&#x20; │

&#x20; ▼

Provider A

&#x20; │

&#x20; ├── Timeout / 5xx / Rate Limit

&#x20; │

&#x20; ▼

Error Classification

&#x20; │

&#x20; ▼

Retry Decision

&#x20; │

&#x20; ├── Retry allowed

&#x20; │       │

&#x20; │       ▼

&#x20; │     Retry

&#x20; │

&#x20; └── Retry exhausted / not allowed

&#x20;         │

&#x20;         ▼

&#x20;      Failover

&#x20;         │

&#x20;         ▼

&#x20;     Provider B

&#x20;         │

&#x20;         ▼

&#x20;       Success

&#x20;         │

&#x20;         ▼

&#x20;       Client

```



The gateway records the complete failure and recovery sequence.



\---



\# 25. Provider State Lifecycle



Each provider/model route maintains its own health state.



```text

&#x20;                   failure threshold

&#x20;             ┌────────────────────────┐

&#x20;             │                        ▼

&#x20;          CLOSED ──────────────────▶ OPEN

&#x20;             ▲                        │

&#x20;             │                        │ cooldown

&#x20;             │                        ▼

&#x20;             └────────────────── HALF-OPEN

&#x20;                   successful probe

```



Example:



```text

Provider A = OPEN

Provider B = CLOSED

Provider C = CLOSED

```



A request should avoid Provider A and consider B/C according to routing policy.



\---



\# 26. Chaos Testing Architecture



The project includes controlled fault injection.



Chaos testing is used to verify that self-healing behavior actually works.



Supported simulated failures may include:



\* Provider timeout

\* Provider 500 error

\* Provider 429 error

\* Artificial latency

\* Network-like failure



Conceptually:



```text

&#x20;                   ┌──────────────┐

&#x20;                   │ Chaos Control│

&#x20;                   └──────┬───────┘

&#x20;                          │

&#x20;                          ▼

Client → Gateway → Provider

&#x20;                   │

&#x20;                   └── injected failure

```



Chaos functionality must:



\* Be disabled by default

\* Be restricted to development/test environments

\* Require explicit activation

\* Never be exposed as an unrestricted production capability



\---



\# 27. Load Testing



k6 is used to generate controlled traffic.



Example:



```text

k6

&#x20;│

&#x20;│ many requests

&#x20;▼

Nginx

&#x20;│

&#x20;▼

Gateway

&#x20;│

&#x20;▼

Providers

```



Load tests measure actual system behavior.



The project must not claim performance numbers before running the corresponding test.



Important measurements include:



\* Requests per second

\* p50 latency

\* p95 latency

\* p99 latency

\* Error rate

\* Gateway overhead

\* Failover behavior under load



\---



\# 28. Deployment Architecture



Docker Compose will be used for local deployment.



Conceptually:



```text

docker-compose

│

├── nginx

├── gateway

├── redis

├── postgres

├── prometheus

├── grafana

└── ollama

```



The goal is that a developer can start the complete environment locally without requiring paid infrastructure.



\---



\# 29. Network Boundaries



The architecture should separate externally accessible services from internal services.



Conceptually:



```text

&#x20;                External

&#x20;                   │

&#x20;                   ▼

&#x20;                 Nginx

&#x20;                   │

&#x20;                   ▼

&#x20;                Gateway

&#x20;                   │

&#x20;         ┌─────────┼─────────┐

&#x20;         ▼         ▼         ▼

&#x20;       Redis   PostgreSQL  LiteLLM

&#x20;                             │

&#x20;                             ▼

&#x20;                          Ollama

```



Monitoring systems may access internal service metrics but should not unnecessarily be exposed publicly.



Administrative and chaos endpoints must have additional protection.



\---



\# 30. Failure Domains



The architecture treats different failures differently.



| Failure                   | Primary Response                                       |

| ------------------------- | ------------------------------------------------------ |

| Invalid request           | Reject                                                 |

| Authentication error      | Reject / do not retry                                  |

| Rate limit                | Retry or failover according to policy                  |

| Timeout                   | Retry / failover                                       |

| Provider 5xx              | Retry / failover                                       |

| Network error             | Retry / failover                                       |

| Content filter            | Return appropriate provider response                   |

| Provider unavailable      | Circuit breaker / failover                             |

| All providers unavailable | Return controlled gateway error                        |

| Redis unavailable         | Follow explicitly defined degraded-mode policy         |

| PostgreSQL unavailable    | Preserve request path where safe; record failure       |

| Gateway restart           | Recover state from persistent systems where applicable |



Detailed policies will be defined in `DESIGN.md` and `RULES.md`.



\---



\# 31. Separation of Responsibilities



The architecture follows clear ownership boundaries.



| Component        | Primary Responsibility        |

| ---------------- | ----------------------------- |

| Nginx            | Reverse proxy                 |

| FastAPI          | Gateway orchestration         |

| Router           | Provider selection            |

| Circuit breaker  | Provider state and protection |

| Retry manager    | Retry policy                  |

| Error classifier | Normalize failures            |

| LiteLLM          | Provider abstraction          |

| Ollama           | Local LLM inference           |

| Redis            | Operational state             |

| PostgreSQL       | Durable data                  |

| Prometheus       | Metrics collection            |

| Grafana          | Metrics visualization         |

| OpenTelemetry    | Distributed tracing           |

| k6               | Load testing                  |

| Chaos module     | Controlled fault injection    |



No component should silently take over responsibilities belonging to another component without documenting the architectural change.



\---



\# 32. Data Flow Principle



The gateway follows this general principle:



```text

Request

&#x20;  ↓

Validate

&#x20;  ↓

Identify

&#x20;  ↓

Control

&#x20;  ↓

Route

&#x20;  ↓

Execute

&#x20;  ↓

Recover if necessary

&#x20;  ↓

Observe

&#x20;  ↓

Record

&#x20;  ↓

Respond

```



Reliability logic should happen before and around provider execution rather than being implemented as an afterthought.



\---



\# 33. Scalability Direction



Version 1 is designed primarily for local development and portfolio demonstration.



The architecture should nevertheless avoid designs that prevent future horizontal scaling.



For example:



\* Operational shared state should not exist only in process memory.

\* Circuit state should be shareable through Redis.

\* Rate limits should not depend on one gateway process.

\* Requests should not depend on local filesystem state.

\* Durable records should be stored in PostgreSQL.



Future deployment could run multiple gateway instances:



```text

&#x20;                Load Balancer

&#x20;                      │

&#x20;         ┌────────────┼────────────┐

&#x20;         ▼            ▼            ▼

&#x20;     Gateway 1    Gateway 2    Gateway 3

&#x20;         │            │            │

&#x20;         └────────────┼────────────┘

&#x20;                      │

&#x20;             ┌────────┴────────┐

&#x20;             ▼                 ▼

&#x20;           Redis           PostgreSQL

```



Horizontal scaling is a future capability, not a requirement for the first implementation.



\---



\# 34. Architecture Principles



The following principles govern architectural decisions:



1\. Reliability over unnecessary complexity.

2\. Local-first development.

3\. No mandatory paid infrastructure.

4\. Shared operational state should be externalized.

5\. Durable data should be persisted.

6\. Failures must be observable.

7\. Reliability behavior must be measurable.

8\. Retry must be controlled.

9\. Failover must be explicit.

10\. Circuit breaking must prevent repeated calls to unhealthy providers.

11\. Security boundaries must be explicit.

12\. Components should have clear responsibilities.

13\. Infrastructure should be reproducible with Docker Compose.

14\. Performance claims must be based on measurements.

15\. Architectural changes must be documented in `DECISIONS.md`.



\---



\# 35. Architecture Success Criteria



The architecture is considered successful when the system can demonstrate:



```text

Healthy Request

&#x20;     ↓

Provider A

&#x20;     ↓

Success

```



and:



```text

Provider A Failure

&#x20;     ↓

Failure Detection

&#x20;     ↓

Retry / Classification

&#x20;     ↓

Circuit Opens

&#x20;     ↓

Failover

&#x20;     ↓

Provider B

&#x20;     ↓

Success

```



and later:



```text

Provider A Recovery

&#x20;     ↓

Cooldown

&#x20;     ↓

HALF-OPEN

&#x20;     ↓

Probe

&#x20;     ↓

Success

&#x20;     ↓

CLOSED

&#x20;     ↓

Normal Traffic

```



All major stages must be visible through appropriate logs, metrics, and traces.



\---



\# 36. Architectural Source of Truth



The following documents complement this architecture:



```text

PRD.md

&#x20;   ↓

What the system must achieve



ARCHITECTURE.md

&#x20;   ↓

How the system is structured



DESIGN.md

&#x20;   ↓

How individual components are implemented



RULES.md

&#x20;   ↓

Constraints and engineering rules



TASKS.md

&#x20;   ↓

What needs to be built



MEMORY.md

&#x20;   ↓

Important project context



SECURITY.md

&#x20;   ↓

Security requirements



DECISIONS.md

&#x20;   ↓

Why important architectural decisions were made

```



If implementation conflicts with this architecture, the implementation should not silently redefine the architecture.



The change should first be evaluated and, when accepted, documented in `DECISIONS.md`.



