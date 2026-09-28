\# Self-Healing LLM Gateway — Detailed Design



\## 1. Purpose



This document defines the detailed technical design of the Self-Healing LLM Gateway.



`ARCHITECTURE.md` describes the major system components and their relationships.



This document defines:



\* Application modules

\* Request lifecycle

\* Internal interfaces

\* Provider routing

\* Circuit breaker behavior

\* Retry behavior

\* Health tracking

\* Redis data

\* PostgreSQL data

\* Observability

\* Error handling

\* Configuration

\* Testing boundaries



This document should be used as the implementation blueprint.



\---



\# 2. Design Goals



The implementation should prioritize:



1\. Clear separation of responsibilities.

2\. Small, testable components.

3\. Explicit failure handling.

4\. Shared operational state through Redis.

5\. Durable records through PostgreSQL.

6\. Provider abstraction through LiteLLM.

7\. Observable request execution.

8\. Configuration through environment variables.

9\. Local development through Docker Compose.

10\. No unnecessary infrastructure complexity.



\---



\# 3. Proposed Project Structure



The application should use the following structure:



```text

llm-gateway/

│

├── app/

│   ├── \_\_init\_\_.py

│   ├── main.py

│   │

│   ├── api/

│   │   ├── \_\_init\_\_.py

│   │   ├── routes\_chat.py

│   │   ├── routes\_health.py

│   │   └── routes\_admin.py

│   │

│   ├── core/

│   │   ├── \_\_init\_\_.py

│   │   ├── config.py

│   │   ├── logging.py

│   │   ├── exceptions.py

│   │   └── request\_context.py

│   │

│   ├── models/

│   │   ├── \_\_init\_\_.py

│   │   ├── requests.py

│   │   ├── responses.py

│   │   └── provider.py

│   │

│   ├── routing/

│   │   ├── \_\_init\_\_.py

│   │   ├── router.py

│   │   ├── provider\_registry.py

│   │   └── routing\_policy.py

│   │

│   ├── reliability/

│   │   ├── \_\_init\_\_.py

│   │   ├── circuit\_breaker.py

│   │   ├── retry.py

│   │   ├── timeout.py

│   │   ├── failover.py

│   │   └── error\_classifier.py

│   │

│   ├── providers/

│   │   ├── \_\_init\_\_.py

│   │   └── litellm\_client.py

│   │

│   ├── health/

│   │   ├── \_\_init\_\_.py

│   │   ├── health\_tracker.py

│   │   └── health\_metrics.py

│   │

│   ├── storage/

│   │   ├── \_\_init\_\_.py

│   │   ├── redis.py

│   │   ├── postgres.py

│   │   └── repositories/

│   │

│   ├── observability/

│   │   ├── \_\_init\_\_.py

│   │   ├── metrics.py

│   │   ├── tracing.py

│   │   └── events.py

│   │

│   ├── usage/

│   │   ├── \_\_init\_\_.py

│   │   └── usage\_tracker.py

│   │

│   └── chaos/

│       ├── \_\_init\_\_.py

│       └── fault\_injection.py

│

├── tests/

│   ├── unit/

│   ├── integration/

│   └── failure/

│

├── docs/

│

├── alembic/

│

├── docker/

│

├── main.py

├── pyproject.toml

├── Dockerfile

├── docker-compose.yml

├── .env.example

├── .gitignore

└── README.md

```



The exact structure may evolve during implementation, but responsibility boundaries should remain clear.



\---



\# 4. Application Entry Point



The application entry point is:



```text

app/main.py

```



Responsibilities:



\* Create the FastAPI application.

\* Register routes.

\* Configure middleware.

\* Initialize required services.

\* Configure observability.

\* Handle application startup/shutdown.



Business logic should not be placed directly inside `main.py`.



\---



\# 5. API Layer



The API layer handles HTTP concerns.



Primary endpoint:



```text

POST /v1/chat/completions

```



Additional endpoints:



```text

GET /health

GET /ready

```



Administrative endpoints may include:



```text

GET /admin/providers

GET /admin/circuits

POST /admin/chaos

```



Administrative endpoints must not be publicly exposed without authentication/authorization.



\---



\# 6. Request Models



Pydantic models will validate incoming requests.



Example conceptual request:



```json

{

&#x20; "model": "local-model-a",

&#x20; "messages": \[

&#x20;   {

&#x20;     "role": "user",

&#x20;     "content": "Hello"

&#x20;   }

&#x20; ],

&#x20; "metadata": {

&#x20;   "tenant\_id": "tenant\_123",

&#x20;   "feature": "chat"

&#x20; }

}

```



The exact OpenAI-compatible schema will be implemented incrementally.



The gateway should preserve compatibility with common OpenAI-style clients where practical.



\---



\# 7. Request Context



Each request should have an internal request context.



Conceptual structure:



```text

RequestContext



request\_id

tenant\_id

feature

model

selected\_provider

attempt\_number

start\_time

```



The context is passed through the request lifecycle.



This avoids passing unrelated parameters separately through every function.



\---



\# 8. Request Lifecycle



The main request flow is:



```text

HTTP Request

&#x20;    │

&#x20;    ▼

Validation

&#x20;    │

&#x20;    ▼

Request Context

&#x20;    │

&#x20;    ▼

Rate Limit Check

&#x20;    │

&#x20;    ▼

Routing

&#x20;    │

&#x20;    ▼

Circuit Check

&#x20;    │

&#x20;    ▼

Provider Attempt

&#x20;    │

&#x20;    ├───────────────┐

&#x20;    │               │

&#x20;  Success          Failure

&#x20;    │               │

&#x20;    │               ▼

&#x20;    │        Error Classification

&#x20;    │               │

&#x20;    │               ▼

&#x20;    │        Retry Decision

&#x20;    │               │

&#x20;    │          ┌────┴────┐

&#x20;    │          │         │

&#x20;    │        Retry     Failover

&#x20;    │          │         │

&#x20;    │          └────┬────┘

&#x20;    │               │

&#x20;    ▼               ▼

Record Result

&#x20;    │

&#x20;    ▼

Update Health

&#x20;    │

&#x20;    ▼

Record Usage

&#x20;    │

&#x20;    ▼

Emit Metrics / Logs / Trace

&#x20;    │

&#x20;    ▼

HTTP Response

```



\---



\# 9. Provider Model



A provider target represents a specific LLM route.



Conceptual structure:



```text

ProviderTarget



id

name

provider

model

priority

enabled

timeout

max\_retries

```



Example:



```text

Provider A

provider = ollama

model = llama3

priority = 1



Provider B

provider = ollama

model = mistral

priority = 2

```



A provider target is the unit used by:



\* Routing

\* Health tracking

\* Circuit breaking

\* Failover

\* Metrics



\---



\# 10. Provider Registry



The provider registry maintains the configured provider targets.



Example configuration:



```text

providers:

&#x20; - id: ollama\_a

&#x20;   provider: ollama

&#x20;   model: model\_a

&#x20;   priority: 1



&#x20; - id: ollama\_b

&#x20;   provider: ollama

&#x20;   model: model\_b

&#x20;   priority: 2



&#x20; - id: ollama\_c

&#x20;   provider: ollama

&#x20;   model: model\_c

&#x20;   priority: 3

```



The registry should provide operations such as:



```text

get\_provider(id)

list\_enabled\_providers()

get\_candidates(request)

```



The registry should not decide whether a provider is currently healthy.



That responsibility belongs to the health/circuit-breaker system.



\---



\# 11. Routing Policy



The router selects an eligible provider.



Basic version:



```text

1\. Get configured providers.

2\. Remove disabled providers.

3\. Remove providers whose circuit is OPEN.

4\. Apply request/model constraints.

5\. Order remaining providers.

6\. Select the first eligible provider.

```



Example:



```text

Configured:



A → priority 1 → OPEN

B → priority 2 → CLOSED

C → priority 3 → CLOSED



Selection:



A → skipped

B → selected

```



Routing must be deterministic unless a different strategy is explicitly configured.



\---



\# 12. Circuit Breaker Design



Circuit breaker state:



```text

CLOSED

OPEN

HALF\_OPEN

```



Each provider target has an independent circuit.



Conceptual object:



```text

CircuitBreaker



provider\_id

state

failure\_count

success\_count

opened\_at

last\_failure\_at

half\_open\_at

```



\---



\# 13. Circuit Breaker State Rules



\## CLOSED



Normal operation.



Every eligible request may be sent.



On successful request:



```text

failure\_count → reduced/reset according to policy

```



On qualifying failure:



```text

failure\_count += 1

```



When failure threshold is reached:



```text

CLOSED → OPEN

```



\---



\## OPEN



Requests should not be sent normally.



The circuit remains open until the configured cooldown period expires.



After cooldown:



```text

OPEN → HALF\_OPEN

```



\---



\## HALF\_OPEN



Only a controlled number of probe requests should be allowed.



The initial implementation should allow one probe at a time.



If the probe succeeds:



```text

HALF\_OPEN → CLOSED

```



If the probe fails:



```text

HALF\_OPEN → OPEN

```



This prevents a recovering provider from being flooded immediately.



\---



\# 14. Circuit Breaker Storage



Circuit state must not exist only in Python process memory.



Redis should store the operational state.



Conceptual Redis key:



```text

circuit:{provider\_id}

```



Example value:



```json

{

&#x20; "state": "OPEN",

&#x20; "failure\_count": 5,

&#x20; "opened\_at": "2026-09-28T18:00:00Z"

}

```



The exact serialization format can be changed during implementation.



State transitions should be designed to avoid race conditions when multiple gateway instances access the same circuit.



\---



\# 15. Circuit Breaker Thresholds



Initial configuration should be configurable.



Example:



```text

failure\_threshold = 5

open\_duration = 30 seconds

half\_open\_max\_probes = 1

```



These values are starting configuration, not guaranteed optimal values.



They must be adjustable without modifying business logic.



\---



\# 16. Retry Design



Retries belong to the reliability layer.



The retry manager determines:



```text

Should this error be retried?

How many times?

How long should we wait?

```



Example:



```text

max\_retries = 2

base\_delay = 0.5 seconds

max\_delay = 5 seconds

```



\---



\# 17. Exponential Backoff



Retry delay should increase between attempts.



Conceptually:



```text

Attempt 1

&#x20;  ↓

0.5 sec

&#x20;  ↓

Attempt 2

&#x20;  ↓

1 sec

&#x20;  ↓

Attempt 3

```



Jitter should be added so that multiple requests do not retry simultaneously.



Conceptual formula:



```text

delay = min(max\_delay, base\_delay × 2^attempt)

delay\_with\_jitter = delay × random\_factor

```



The exact implementation should use a bounded jitter strategy.



\---



\# 18. Retry Eligibility



Not all failures should trigger retries.



Initial classification:



| Error          | Retry                       |

| -------------- | --------------------------- |

| TIMEOUT        | Yes                         |

| NETWORK\_ERROR  | Yes                         |

| SERVER\_ERROR   | Yes                         |

| RATE\_LIMIT     | Conditional                 |

| AUTH\_ERROR     | No                          |

| BAD\_REQUEST    | No                          |

| CONTENT\_FILTER | No                          |

| UNKNOWN        | Conservative / configurable |



Retry behavior must be tested independently.



\---



\# 19. Timeout Design



Provider requests must have explicit timeouts.



The timeout should be configurable per provider.



Example:



```text

provider\_a\_timeout = 30 seconds

provider\_b\_timeout = 30 seconds

```



Timeouts should produce the normalized:



```text

TIMEOUT

```



error category.



Timeouts must contribute to provider health statistics.



\---



\# 20. Error Classification



Provider-specific exceptions should be converted into internal error categories.



Interface:



```text

class ErrorClassifier:



&#x20;   classify(exception) -> GatewayError

```



Conceptual result:



```text

GatewayError



error\_type

message

retryable

provider

status\_code

original\_exception

```



The internal error object should contain enough information for:



\* Retry

\* Failover

\* Circuit breaker

\* Logging

\* Metrics



\---



\# 21. Failover Design



Failover occurs when the current provider cannot successfully complete the request and another eligible provider exists.



Example:



```text

Provider A

&#x20;  │

&#x20;  └── timeout

&#x20;       │

&#x20;       ▼

Error Classifier

&#x20;       │

&#x20;       ▼

Retry / Failover Decision

&#x20;       │

&#x20;       ▼

Provider B

&#x20;       │

&#x20;       └── success

```



The failover manager should:



1\. Mark the failed attempt.

2\. Update provider health.

3\. Update circuit state.

4\. Select another eligible provider.

5\. Execute the request.

6\. Record the failover event.



\---



\# 22. Failover Loop Protection



The system must prevent infinite provider loops.



Example:



```text

A → B → C

```



A provider should not be selected repeatedly for the same request unless explicitly allowed.



Each request should maintain an attempt history.



Conceptual:



```text

attempted\_providers = {

&#x20;   "provider\_a",

&#x20;   "provider\_b"

}

```



If all eligible providers have already been attempted:



```text

No provider available

```



The gateway should return a controlled error.



\---



\# 23. Maximum Attempts



A global maximum attempt count should protect the gateway from excessive work.



Example:



```text

max\_total\_attempts = 5

```



The actual number should be configurable.



The system must consider both:



\* Retry attempts

\* Failover attempts



when calculating the total attempt limit.



\---



\# 24. Provider Health Tracker



The health tracker records provider performance.



Conceptual data:



```text

ProviderHealth



provider\_id

total\_requests

successful\_requests

failed\_requests

latencies

error\_counts

last\_success

last\_failure

failover\_count

```



The health tracker should expose calculations for:



```text

success\_rate

failure\_rate

p50\_latency

p95\_latency

p99\_latency

```



\---



\# 25. Rolling Health Window



Health metrics should use a rolling time window where appropriate.



Example:



```text

window = last 5 minutes

```



This prevents an old failure from permanently affecting the provider's current health.



The exact window and aggregation strategy should remain configurable.



\---



\# 26. Latency Percentiles



The system should track:



```text

p50

p95

p99

```



Meaning:



\* p50 = median latency

\* p95 = latency under which approximately 95% of requests fall

\* p99 = latency under which approximately 99% of requests fall



These metrics are more useful than average latency alone because they show tail behavior.



\---



\# 27. Redis Design



Redis is used for fast-changing operational state.



Initial logical namespaces:



```text

circuit:{provider\_id}

health:{provider\_id}

ratelimit:{tenant\_id}

```



Possible future namespaces:



```text

lock:{resource}

probe:{provider\_id}

```



Keys should have documented expiration policies where appropriate.



\---



\# 28. Redis Atomicity



Operations that modify shared state must consider concurrent gateway instances.



Examples:



\* Circuit transitions

\* Failure counters

\* Rate-limit counters

\* Half-open probe ownership



Redis atomic operations, transactions, or Lua scripts may be used when required.



Do not assume a simple read-modify-write operation is safe under concurrency.



\---



\# 29. PostgreSQL Design



PostgreSQL stores durable records.



Initial logical entities:



```text

tenants

requests

usage\_records

provider\_events

audit\_events

```



\---



\# 30. Tenant Table



Conceptual fields:



```text

tenants



id

name

status

created\_at

updated\_at

```



Tenant IDs should be stable and unique.



\---



\# 31. Request Record



A request record should allow historical investigation.



Conceptual fields:



```text

requests



id

request\_id

tenant\_id

feature

requested\_model

final\_provider

final\_model

status

started\_at

completed\_at

latency\_ms

attempt\_count

```



The database should not store sensitive prompt content by default.



\---



\# 32. Usage Record



Conceptual fields:



```text

usage\_records



id

request\_id

tenant\_id

feature

provider

model

input\_tokens

output\_tokens

total\_tokens

estimated\_cost

created\_at

```



Usage data should support aggregation by:



\* Tenant

\* Feature

\* Provider

\* Model

\* Time



\---



\# 33. Provider Event Record



Important provider events may be stored for historical analysis.



Examples:



```text

provider\_timeout

provider\_rate\_limit

provider\_server\_error

provider\_success

circuit\_opened

circuit\_closed

failover

```



Conceptual fields:



```text

provider\_events



id

request\_id

provider\_id

event\_type

error\_type

latency\_ms

timestamp

metadata

```



High-volume operational metrics should remain in Prometheus/Redis rather than forcing every event into PostgreSQL.



\---



\# 34. Database vs Metrics



Not every measurement belongs in PostgreSQL.



Use Prometheus for:



```text

High-volume numerical metrics

```



Use Redis for:



```text

Fast-changing operational state

```



Use PostgreSQL for:



```text

Durable historical/business records

```



This separation prevents PostgreSQL from becoming the storage layer for every operational event.



\---



\# 35. Usage Tracking Flow



After a provider response:



```text

Provider Response

&#x20;     │

&#x20;     ▼

Extract Usage

&#x20;     │

&#x20;     ▼

Calculate Estimated Cost

&#x20;     │

&#x20;     ├── Redis/metrics

&#x20;     │

&#x20;     └── PostgreSQL

```



For local Ollama inference:



```text

estimated\_cost = 0

```



unless a configured internal pricing model is introduced.



Token usage should still be recorded when available.



\---



\# 36. Cost Calculation



Cost calculation should be isolated from provider execution.



Conceptual interface:



```text

calculate\_cost(

&#x20;   provider,

&#x20;   model,

&#x20;   input\_tokens,

&#x20;   output\_tokens

)

```



Pricing should be configuration-driven.



Do not hard-code provider pricing throughout the application.



If pricing is unavailable:



```text

estimated\_cost = null

```



rather than inventing a value.



\---



\# 37. Observability Design



Every request should generate three forms of observability where applicable:



```text

Logs

Metrics

Traces

```



They should share the same:



```text

request\_id

```



This allows correlation.



\---



\# 38. Structured Log Events



Important events include:



```text

request\_received

request\_rejected

rate\_limit\_rejected

provider\_selected

provider\_request\_started

provider\_success

provider\_timeout

provider\_error

retry\_started

failover\_started

circuit\_opened

circuit\_half\_open

circuit\_closed

request\_completed

request\_failed

```



Logs should contain relevant context without exposing sensitive data.



\---



\# 39. Metrics Design



Initial metric categories:



\### Requests



```text

request\_total

request\_duration

```



\### Provider



```text

provider\_request\_total

provider\_request\_duration

provider\_error\_total

```



\### Reliability



```text

retry\_total

failover\_total

circuit\_transition\_total

```



\### Usage



```text

tokens\_total

estimated\_cost\_total

```



Metric labels should be controlled carefully.



High-cardinality values such as raw request IDs should not be used as Prometheus labels.



\---



\# 40. Tracing Design



A request should produce a root span.



Provider calls should create child spans.



Conceptual:



```text

gateway.request

&#x20;  │

&#x20;  ├── rate\_limit.check

&#x20;  ├── routing.select

&#x20;  ├── provider.attempt

&#x20;  │      └── retry/failure

&#x20;  ├── provider.attempt

&#x20;  │      └── success

&#x20;  └── usage.record

```



Trace attributes may include:



```text

request\_id

tenant\_id

provider

model

attempt

error\_type

```



Sensitive prompt/response content should not automatically be included.



\---



\# 41. Rate Limiting Design



Rate limiting will initially use a Redis-backed fixed-window or token-bucket approach.



The exact algorithm should be selected during implementation based on simplicity and correctness.



Conceptual:



```text

Tenant

&#x20;  │

&#x20;  ▼

Rate Limit Check

&#x20;  │

&#x20;  ├── Allowed → continue

&#x20;  │

&#x20;  └── Rejected → HTTP 429

```



Rate limits should be configurable per tenant or policy.



\---



\# 42. Authentication Design



The gateway should support API-key authentication for client requests.



Conceptually:



```text

Authorization Header

&#x20;       │

&#x20;       ▼

Authentication

&#x20;       │

&#x20;       ▼

tenant\_id

&#x20;       │

&#x20;       ▼

Request Context

```



API keys must not be stored in plaintext where avoidable.



The exact authentication storage mechanism will be defined during implementation.



\---



\# 43. Security Boundaries



The following endpoints require special protection:



```text

/admin/\*

/chaos/\*

```



Chaos functionality must never be enabled by default.



Secrets must come from environment variables or secret management mechanisms.



Never commit:



```text

API keys

passwords

database credentials

tokens

private secrets

```



to Git.



\---



\# 44. Chaos Injection Design



Chaos injection is intended for development and testing.



Possible faults:



```text

TIMEOUT

SERVER\_ERROR

RATE\_LIMIT

LATENCY

NETWORK\_ERROR

```



Example:



```text

POST /admin/chaos

```



Conceptual configuration:



```json

{

&#x20; "provider\_id": "ollama\_a",

&#x20; "fault": "TIMEOUT",

&#x20; "duration\_seconds": 60

}

```



The implementation must ensure that chaos configuration cannot accidentally remain enabled in a production environment.



\---



\# 45. Health Endpoints



\## `/health`



Indicates whether the gateway process is alive.



Example:



```json

{

&#x20; "status": "ok"

}

```



This should be lightweight.



\---



\## `/ready`



Indicates whether required dependencies are available enough for the gateway to operate.



Possible checks:



```text

Redis

PostgreSQL

Provider configuration

```



Readiness should be designed carefully so that a single degraded LLM provider does not necessarily make the entire gateway unavailable.



\---



\# 46. Configuration



Configuration should be environment-driven.



Example:



```text

APP\_ENV=development



REDIS\_URL=redis://redis:6379/0



DATABASE\_URL=postgresql+asyncpg://...



DEFAULT\_TIMEOUT\_SECONDS=30



MAX\_RETRIES=2



CIRCUIT\_FAILURE\_THRESHOLD=5



CIRCUIT\_OPEN\_SECONDS=30



MAX\_TOTAL\_ATTEMPTS=5

```



Configuration should be loaded through a centralized settings module.



Application code should not repeatedly read environment variables directly.



\---



\# 47. Dependency Management



The project will use:



```text

Python 3.12

uv

```



Dependencies should be declared in:



```text

pyproject.toml

```



Dependency versions should be managed deliberately.



Avoid installing packages without documenting why they are required.



\---



\# 48. Async Design



FastAPI endpoints will use asynchronous execution where appropriate.



I/O operations should be asynchronous where supported:



```text

HTTP calls

Redis

PostgreSQL

LLM provider requests

```



CPU-heavy operations should not block the event loop.



The implementation should avoid unnecessary `async` usage where no asynchronous operation exists.



\---



\# 49. LiteLLM Client Interface



The gateway should interact with LiteLLM through a dedicated client module.



Conceptual interface:



```text

LLMClient



complete(request, provider\_target, context)

```



The rest of the gateway should not depend directly on LiteLLM-specific implementation details.



This makes provider execution replaceable and testable.



\---



\# 50. Provider Execution Result



Provider execution should return a normalized internal result.



Conceptual:



```text

ProviderResult



success

provider\_id

model

response

input\_tokens

output\_tokens

total\_tokens

latency\_ms

error

```



This allows reliability and observability layers to work independently of provider-specific response formats.



\---



\# 51. Service Dependency Flow



The logical dependency direction should be:



```text

API

&#x20;↓

Gateway Orchestrator

&#x20;↓

Routing / Reliability

&#x20;↓

Provider Client

&#x20;↓

LiteLLM

```



Supporting systems:



```text

Gateway

&#x20;├── Redis

&#x20;├── PostgreSQL

&#x20;├── Metrics

&#x20;├── Tracing

&#x20;└── Logging

```



Lower-level modules should not import API route modules.



Avoid circular dependencies.



\---



\# 52. Gateway Orchestrator



A central orchestration service should coordinate the request.



Conceptual:



```text

GatewayService.handle\_chat(request)

```



Responsibilities:



1\. Create request context.

2\. Check rate limit.

3\. Obtain provider candidates.

4\. Check circuit state.

5\. Execute provider attempts.

6\. Apply retry policy.

7\. Apply failover.

8\. Update health.

9\. Record usage.

10\. Emit observability events.

11\. Return normalized response.



The orchestrator should coordinate components rather than implementing every algorithm itself.



\---



\# 53. Separation of Algorithms



The following logic should remain independently testable:



```text

Routing

Circuit Breaking

Retry

Error Classification

Backoff

Health Calculation

Cost Calculation

Rate Limiting

```



For example, circuit-breaker tests should not require a real Ollama instance.



\---



\# 54. Failure Handling Order



When a provider fails:



```text

Provider Error

&#x20;     ↓

Normalize Error

&#x20;     ↓

Record Failure

&#x20;     ↓

Update Health

&#x20;     ↓

Update Circuit

&#x20;     ↓

Retry Decision

&#x20;     ↓

Failover Decision

```



The exact ordering may be adjusted when implementation reveals concurrency requirements, but the system must preserve consistent state.



\---



\# 55. All Providers Failed



If every eligible provider fails:



```text

Client

&#x20; ↓

Gateway

&#x20; ↓

Provider A → failure

&#x20; ↓

Provider B → failure

&#x20; ↓

Provider C → failure

&#x20; ↓

No provider available

```



The gateway should return a controlled error response.



The response should not expose internal secrets or unnecessary infrastructure details.



The event should be observable.



\---



\# 56. Graceful Degradation



The gateway should distinguish between:



```text

No LLM provider available

```



and:



```text

Gateway itself is broken

```



For example, if all providers are unavailable but the gateway process is functioning:



```text

Gateway health = alive

LLM availability = degraded

```



This distinction should be reflected in health and readiness design.



\---



\# 57. Concurrency Considerations



Multiple requests may access the same provider simultaneously.



The design must account for:



\* Concurrent circuit updates

\* Concurrent failure counters

\* Concurrent half-open probes

\* Concurrent rate-limit updates

\* Multiple gateway instances



The implementation must not rely on Python in-memory state for shared operational decisions.



\---



\# 58. Testing Design



Testing is divided into:



```text

Unit Tests

Integration Tests

Failure Tests

Load Tests

```



\---



\# 59. Unit Tests



Unit tests should cover isolated logic.



Examples:



```text

test\_error\_classifier.py

test\_retry.py

test\_backoff.py

test\_circuit\_breaker.py

test\_router.py

test\_health\_tracker.py

test\_cost\_calculator.py

```



Unit tests should use mocks/fakes instead of real external services where practical.



\---



\# 60. Integration Tests



Integration tests verify real component interactions.



Examples:



```text

FastAPI + Redis

FastAPI + PostgreSQL

FastAPI + LiteLLM

Gateway + Ollama

```



Docker Compose can provide integration dependencies.



\---



\# 61. Failure Tests



Failure tests are a major part of the project.



Examples:



\### Timeout



```text

Provider A

&#x20;  ↓

Timeout

&#x20;  ↓

Retry

&#x20;  ↓

Failover

```



\### Server Error



```text

Provider A

&#x20;  ↓

500

&#x20;  ↓

Circuit failure count

&#x20;  ↓

Failover

```



\### Repeated Failure



```text

A fails repeatedly

&#x20;  ↓

threshold reached

&#x20;  ↓

Circuit OPEN

```



\### Recovery



```text

OPEN

&#x20;  ↓

cooldown

&#x20;  ↓

HALF\_OPEN

&#x20;  ↓

probe success

&#x20;  ↓

CLOSED

```



\---



\# 62. Load Test Design



k6 will generate controlled traffic.



Load tests should measure:



```text

RPS

p50

p95

p99

error rate

retry rate

failover rate

gateway overhead

```



Load-test results must be recorded with:



\* Test configuration

\* Number of virtual users

\* Duration

\* Provider configuration

\* Hardware/environment

\* Observed results



No performance number should be claimed without a corresponding measurement.



\---



\# 63. Local Development



The initial development environment should support:



```text

FastAPI

Redis

PostgreSQL

Prometheus

Grafana

Ollama

```



Docker Compose should eventually provide the complete local environment.



During early development, individual components may be run manually to simplify debugging.



\---



\# 64. Development Order



Implementation should proceed incrementally.



Recommended order:



```text

1\. FastAPI foundation

2\. Request models

3\. Basic LiteLLM/Ollama call

4\. Provider registry

5\. Routing

6\. Error classification

7\. Retry

8\. Timeout

9\. Circuit breaker

10\. Failover

11\. Redis state

12\. PostgreSQL persistence

13\. Usage/cost tracking

14\. Structured logging

15\. Prometheus metrics

16\. Grafana dashboards

17\. OpenTelemetry tracing

18\. Chaos injection

19\. Automated tests

20\. k6 load tests

21\. Docker Compose

22\. Nginx

23\. CI/CD

```



Each stage should be working before adding the next major layer.



\---



\# 65. Implementation Rule



Do not implement the entire system in one step.



Every major feature should follow:



```text

Design

&#x20; ↓

Implementation

&#x20; ↓

Unit Test

&#x20; ↓

Integration Test

&#x20; ↓

Manual Verification

&#x20; ↓

Documentation

&#x20; ↓

Next Feature

```



This prevents hidden failures from accumulating.



\---



\# 66. Definition of a Reliable Provider Attempt



A provider attempt is considered successful only when:



1\. The request reaches the provider.

2\. The provider returns a valid response.

3\. The response can be normalized.

4\. Usage information is extracted when available.

5\. Latency is recorded.

6\. Provider health is updated.

7\. Observability events are emitted.



A failure must similarly update the appropriate reliability state.



\---



\# 67. Design Constraints



The implementation must not:



\* Introduce Kubernetes without a demonstrated requirement.

\* Introduce Kafka without a demonstrated requirement.

\* Introduce unnecessary microservices.

\* Store all state in Python memory.

\* Retry indefinitely.

\* Fail over indefinitely.

\* Expose chaos controls publicly.

\* Log secrets.

\* Invent cost data.

\* Claim performance without testing.

\* Couple business logic directly to HTTP routes.

\* Couple reliability logic directly to a specific provider.



\---



\# 68. Future Extension Points



The design should allow future additions without rewriting the core gateway.



Possible extensions:



```text

External LLM providers

Additional routing strategies

Provider quality scoring

Dynamic routing

Per-tenant budgets

Advanced authentication

Distributed gateway instances

Cloud deployment

Advanced anomaly detection

```



These are not required for version 1.



\---



\# 69. Final Design Principle



The gateway should be understandable as the following pipeline:



```text

REQUEST

&#x20;  ↓

VALIDATE

&#x20;  ↓

IDENTIFY

&#x20;  ↓

LIMIT

&#x20;  ↓

ROUTE

&#x20;  ↓

PROTECT

&#x20;  ↓

EXECUTE

&#x20;  ↓

RECOVER

&#x20;  ↓

OBSERVE

&#x20;  ↓

RECORD

&#x20;  ↓

RESPOND

```



Every major responsibility should have:



\* A clear owner

\* A defined interface

\* A testable behavior

\* Observable results

\* Explicit failure handling



The implementation should favor correctness and demonstrable reliability over unnecessary architectural complexity.



