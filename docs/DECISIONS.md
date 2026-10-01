\# Architecture Decision Records



\## 1. Purpose



This document records important technical and architectural decisions made during the development of the Self-Healing LLM Gateway.



The purpose is to preserve:



\* What was decided

\* Why it was decided

\* What alternatives were considered

\* What trade-offs were accepted

\* Whether the decision is still active



This prevents important architectural decisions from being forgotten or silently changed.



\---



\# 2. Decision Status



Use the following status values:



```text

PROPOSED   → Decision is being considered

ACCEPTED   → Decision is currently active

SUPERSEDED → Replaced by a newer decision

REJECTED   → Considered but intentionally not selected

```



\---



\# 3. Decision Index



| ID      | Decision                                                     | Status   |

| ------- | ------------------------------------------------------------ | -------- |

| ADR-001 | Use Python for the gateway                                   | ACCEPTED |

| ADR-002 | Use FastAPI for the API layer                                | ACCEPTED |

| ADR-003 | Use LiteLLM for provider abstraction                         | ACCEPTED |

| ADR-004 | Use Ollama for the local-first LLM path                      | ACCEPTED |

| ADR-005 | Use Redis for operational state                              | ACCEPTED |

| ADR-006 | Use PostgreSQL for durable data                              | ACCEPTED |

| ADR-007 | Use a circuit breaker with CLOSED/OPEN/HALF-OPEN             | ACCEPTED |

| ADR-008 | Use retry + timeout + backoff + jitter                       | ACCEPTED |

| ADR-009 | Use automatic provider failover                              | ACCEPTED |

| ADR-010 | Use Prometheus and Grafana for metrics/visualization         | ACCEPTED |

| ADR-011 | Use OpenTelemetry for distributed tracing                    | ACCEPTED |

| ADR-012 | Use structured JSON logging                                  | ACCEPTED |

| ADR-013 | Use Docker Compose for local orchestration                   | ACCEPTED |

| ADR-014 | Keep the core project at ₹0 mandatory cost                   | ACCEPTED |

| ADR-015 | Do not introduce Kubernetes in v1                            | ACCEPTED |

| ADR-016 | Do not introduce Kafka in v1                                 | ACCEPTED |

| ADR-017 | Use PostgreSQL + SQLAlchemy + Alembic                        | ACCEPTED |

| ADR-018 | Use Python 3.12 instead of targeting Python 3.14             | ACCEPTED |
| ADR-019 | Develop incrementally instead of building everything at once | ACCEPTED |
| ADR-020 | Treat documentation as the engineering source of truth       | ACCEPTED |
| ADR-021 | Use Redis Read-Through Cache for API-Key Authentication      | ACCEPTED |



\---



\# ADR-001 — Use Python for the Gateway



\*\*Status:\*\* ACCEPTED



\## Decision



Use Python as the primary implementation language.



\## Reason



The gateway is primarily an API, orchestration, reliability, and infrastructure application.



Python provides mature libraries for:



\* FastAPI

\* HTTP clients

\* LLM integrations

\* Redis

\* PostgreSQL

\* Prometheus

\* OpenTelemetry

\* Testing



The project also needs to remain understandable during development.



\## Alternatives Considered



\### Java



Strong backend ecosystem and suitable for production systems.



However, Python provides a more direct development path for this particular LLM infrastructure project.



\### Go



Strong choice for infrastructure and networking.



However, introducing Go would increase the learning and implementation overhead for this project.



\## Trade-off



Python may not provide the same low-level performance characteristics as Go for certain infrastructure workloads.



For this project, measured reliability behavior and architecture are more important than maximizing raw gateway throughput.



\---



\# ADR-002 — Use FastAPI for the API Layer



\*\*Status:\*\* ACCEPTED



\## Decision



Use FastAPI as the gateway's HTTP API framework.



\## Reason



The gateway needs:



\* HTTP APIs

\* Request validation

\* Async support

\* Automatic API documentation

\* Clear endpoint definitions

\* Good Python integration



FastAPI provides these capabilities with relatively little framework overhead.



\## Alternatives Considered



\### Flask



Simple and mature, but FastAPI provides stronger built-in request validation and async-oriented development.



\### Django



Too large for the gateway's API-focused requirements.



\## Trade-off



FastAPI does not solve the gateway's reliability problems by itself.



The reliability logic must remain in dedicated gateway modules.



\---



\# ADR-003 — Use LiteLLM for Provider Abstraction



\*\*Status:\*\* ACCEPTED



\## Decision



Use LiteLLM as the LLM provider/model abstraction layer.



\## Reason



The gateway needs to communicate with multiple LLM providers without implementing completely separate integration logic for every provider.



LiteLLM provides a common interface across supported providers/models.



The gateway should therefore focus on:



```text

Routing

Health

Retries

Circuit breaking

Failover

Observability

Usage

Cost attribution

```



rather than duplicating provider-specific API handling.



\## Important Boundary



LiteLLM is not the self-healing layer.



The project remains responsible for reliability behavior around provider calls.



Conceptually:



```text

Gateway Reliability Logic

&#x20;       ↓

&#x20;    LiteLLM

&#x20;       ↓

Provider / Model

```



\---



\# ADR-004 — Use Ollama for the Local-First LLM Path



\*\*Status:\*\* ACCEPTED



\## Decision



Use Ollama as the primary local inference path during development and demonstration.



\## Reason



The project must be usable without mandatory paid APIs.



Ollama allows local models to be used without requiring a paid cloud provider for the core demonstration.



The intended path is:



```text

Client

&#x20; ↓

FastAPI

&#x20; ↓

LiteLLM

&#x20; ↓

Ollama

&#x20; ↓

Local Model

```



\## Trade-off



Local inference depends on the developer's available hardware and may be slower than cloud inference.



That is acceptable because the primary objective is demonstrating gateway reliability rather than benchmarking LLM inference speed.



\---



\# ADR-005 — Use Redis for Operational State



\*\*Status:\*\* ACCEPTED



\## Decision



Use Redis for fast-changing operational state.



Examples:



\* Circuit-breaker state

\* Provider health counters

\* Rate-limit counters

\* Temporary coordination state

\* Short-lived operational information



\## Reason



These values change frequently and need fast access.



Redis is appropriate for this type of runtime state.



\## Why Not PostgreSQL for Everything?



PostgreSQL is the durable database.



Using it for every rapidly changing operational counter could unnecessarily increase database load and complicate runtime operations.



\## Trade-off



Redis introduces another infrastructure dependency.



The benefit is a clearer separation between:



```text

Operational runtime state → Redis

Durable application data → PostgreSQL

```



\---



\# ADR-006 — Use PostgreSQL for Durable Data



\*\*Status:\*\* ACCEPTED



\## Decision



Use PostgreSQL for persistent application data.



Examples:



\* Tenants

\* API-key metadata

\* Usage records

\* Token usage

\* Cost records

\* Audit records

\* Persistent configuration where appropriate



\## Reason



These records need durability and queryability.



PostgreSQL is well suited for structured relational data.



\## Trade-off



The project now has both Redis and PostgreSQL.



This is intentional because they serve different purposes.



\---



\# ADR-007 — Use a Three-State Circuit Breaker



\*\*Status:\*\* ACCEPTED



\## Decision



Implement:



```text

CLOSED

OPEN

HALF-OPEN

```



\## Reason



A simple "provider healthy/unhealthy" flag is insufficient for recovery.



The gateway needs to distinguish between:



\### CLOSED



Normal traffic is allowed.



\### OPEN



Provider is considered unhealthy and normal traffic is blocked.



\### HALF-OPEN



A controlled recovery probe tests whether the provider has recovered.



Successful probe:



```text

HALF-OPEN → CLOSED

```



Failed probe:



```text

HALF-OPEN → OPEN

```



\## Why This Matters



Half-open behavior is central to the project's self-healing mechanism.



\---



\# ADR-008 — Use Timeout + Retry + Backoff + Jitter



\*\*Status:\*\* ACCEPTED



\## Decision



Provider calls should use:



\* Explicit timeouts

\* Bounded retries

\* Exponential backoff

\* Jitter

\* Error classification



\## Reason



LLM providers can experience:



\* Temporary network problems

\* Rate limiting

\* Timeouts

\* Server errors



Immediate unlimited retries can make the problem worse.



The intended behavior is:



```text

Failure

&#x20; ↓

Is retryable?

&#x20; ↓

Yes

&#x20; ↓

Wait with backoff + jitter

&#x20; ↓

Bounded retry

```



\## Trade-off



Retries increase request latency.



Therefore retries must be bounded and observable.



\---



\# ADR-009 — Use Automatic Provider Failover



\*\*Status:\*\* ACCEPTED



\## Decision



The gateway should automatically route requests to another eligible provider/model when the preferred provider cannot serve the request.



\## Reason



The central purpose of the gateway is to prevent a single provider failure from unnecessarily taking down the application.



Example:



```text

Provider A

&#x20;  ↓

Failure

&#x20;  ↓

Provider B

&#x20;  ↓

Success

```



\## Requirements



Failover should consider:



\* Provider health

\* Circuit state

\* Provider/model availability

\* Routing configuration

\* Error type



Failover events must be recorded for observability.



\---



\# ADR-010 — Use Prometheus and Grafana



\*\*Status:\*\* ACCEPTED



\## Decision



Use Prometheus for metrics collection and Grafana for visualization.



\## Reason



The project needs measurable evidence of reliability behavior.



Important metrics include:



\* Request rate

\* Success rate

\* Error rate

\* Latency

\* p50/p95/p99

\* Retry count

\* Failover count

\* Circuit state

\* Provider availability

\* Token usage

\* Estimated cost



Grafana will make these measurements easier to inspect during demonstrations.



\## Trade-off



Prometheus and Grafana add infrastructure components.



They are justified because observability is a core project requirement.



\---



\# ADR-011 — Use OpenTelemetry for Tracing



\*\*Status:\*\* ACCEPTED



\## Decision



Use OpenTelemetry for distributed/request tracing.



\## Reason



Metrics answer questions such as:



> How many requests failed?



Tracing helps answer:



> What happened to this specific request?



A request may pass through:



```text

Nginx

&#x20;↓

FastAPI

&#x20;↓

Retry logic

&#x20;↓

LiteLLM

&#x20;↓

Provider

```



Tracing can connect these operations through a request lifecycle.



\---



\# ADR-012 — Use Structured JSON Logging



\*\*Status:\*\* ACCEPTED



\## Decision



Use structured JSON logs instead of relying only on plain text logs.



\## Reason



Machine-readable logs make it easier to:



\* Search

\* Filter

\* Correlate

\* Analyze

\* Process logs automatically



Important fields include:



```text

timestamp

level

request\_id

tenant\_id

provider

model

event

latency

error\_type

retry\_count

failover\_count

circuit\_state

```



Sensitive information must not be logged.



\---



\# ADR-013 — Use Docker Compose for Local Orchestration



\*\*Status:\*\* ACCEPTED



\## Decision



Use Docker Compose to run the local multi-service environment.



Expected services include:



```text

Gateway

Redis

PostgreSQL

Prometheus

Grafana

Ollama

Nginx

```



\## Reason



The project contains several infrastructure components.



Docker Compose provides a reproducible local environment without requiring a full container orchestration platform.



\## Trade-off



Docker Compose is not a replacement for Kubernetes in large production environments.



That is acceptable because Kubernetes is outside the v1 scope.



\---



\# ADR-014 — Keep the Core Project at ₹0 Mandatory Cost



\*\*Status:\*\* ACCEPTED



\## Decision



The core system must run locally without requiring paid APIs or infrastructure.



\## Reason



The project should be reproducible by:



\* The developer

\* Interviewers

\* Reviewers

\* Other developers



without requiring cloud billing.



\## Implementation Direction



Use:



```text

FastAPI

\+

LiteLLM

\+

Ollama

\+

Redis

\+

PostgreSQL

\+

Prometheus

\+

Grafana

\+

Docker Compose

```



locally.



External providers can be optional.



\## Trade-off



Local models may have lower capability or slower inference than some cloud models.



The project prioritizes reproducibility and reliability engineering.



\---



\# ADR-015 — Do Not Introduce Kubernetes in v1



\*\*Status:\*\* ACCEPTED



\## Decision



Do not use Kubernetes for the first version.



\## Reason



Kubernetes would add significant operational complexity:



\* Cluster configuration

\* Deployments

\* Services

\* Ingress

\* ConfigMaps

\* Secrets

\* Health probes

\* Scaling

\* Networking

\* Persistent storage



The project can demonstrate its primary engineering concepts without Kubernetes.



\## Reconsider When



Kubernetes may become relevant if the project later needs to demonstrate:



\* Multi-node deployment

\* Horizontal scaling

\* Production orchestration

\* Container scheduling

\* Kubernetes-specific resilience



Until then, Docker Compose is sufficient.



\---



\# ADR-016 — Do Not Introduce Kafka in v1



\*\*Status:\*\* ACCEPTED



\## Decision



Do not use Kafka initially.



\## Reason



The gateway does not currently require an event-streaming platform.



Adding Kafka would introduce:



\* Additional infrastructure

\* More operational complexity

\* More failure modes

\* More concepts to maintain



The project's core reliability mechanisms can operate without Kafka.



\## Reconsider When



Kafka could become relevant if the system later requires:



\* High-volume asynchronous event processing

\* Durable event streams

\* Large-scale analytics pipelines

\* Event-driven architecture



No such requirement currently exists.



\---



\# ADR-017 — Use SQLAlchemy + Alembic with PostgreSQL



\*\*Status:\*\* ACCEPTED



\## Decision



Use:



```text

PostgreSQL

&#x20;   ↓

SQLAlchemy

&#x20;   ↓

Alembic

```



\## Reason



SQLAlchemy provides the application database abstraction.



Alembic provides controlled schema migrations.



This allows database changes to be versioned rather than manually recreated.



\## Example



Instead of manually changing every database:



```text

Create migration

&#x20;     ↓

Commit migration

&#x20;     ↓

Run migration

&#x20;     ↓

Database schema updated

```



\---



\# ADR-018 — Target Python 3.12



\*\*Status:\*\* ACCEPTED



\## Decision



Use Python 3.12 as the project's target runtime.



\## Reason



The development environment has also used Python 3.14, but the project should prioritize dependency compatibility and ecosystem stability.



Python 3.12 provides a mature target for the selected ecosystem.



\## Trade-off



The project does not target the newest Python release immediately.



That is intentional.



The goal is reliable dependency compatibility rather than using the newest version simply because it exists.



\---



\# ADR-019 — Develop Incrementally



\*\*Status:\*\* ACCEPTED



\## Decision



Build the system in small verified phases.



\## Reason



The project contains many interacting components.



Building everything simultaneously makes debugging difficult.



The development process is:



```text

Documentation

&#x20;   ↓

Small implementation

&#x20;   ↓

Test

&#x20;   ↓

Verify

&#x20;   ↓

Commit

&#x20;   ↓

Next task

```



\## Example



Do not implement:



```text

FastAPI

Redis

PostgreSQL

LiteLLM

Circuit breaker

Grafana

Tracing

Docker

CI

```



all at once.



Instead, establish a working foundation and add one capability at a time.



\---



\# ADR-020 — Documentation Is the Engineering Source of Truth



\*\*Status:\*\* ACCEPTED



\## Decision



The `docs/` directory is the project's architectural source of truth.



Current documentation:



```text

docs/

├── PRD.md

├── ARCHITECTURE.md

├── DESIGN.md

├── RULES.md

├── TASKS.md

├── MEMORY.md

├── SECURITY.md

└── DECISIONS.md

```



\## Reason



The project is being developed with an AI coding agent.



Without persistent architectural documentation, an agent may:



\* Change technologies

\* Duplicate functionality

\* Introduce unnecessary services

\* Forget constraints

\* Implement features in the wrong order

\* Break existing design decisions



Documentation reduces this risk.



\## Rule



Before making a significant architectural change:



```text

Read documentation

&#x20;     ↓

Identify existing decision

&#x20;     ↓

Evaluate proposed change

&#x20;     ↓

Record new decision

&#x20;     ↓

Update affected documentation

&#x20;     ↓

```



---



# ADR-021 — Use Redis Read-Through Cache for API-Key Authentication



**Status:** ACCEPTED



## Decision



Use Redis to provide a short-lived, read-through authentication cache (`api_key_cache:{sha256_hash}`) with a bounded TTL (default 180 seconds).



## Reason



PostgreSQL is the durable source of truth for API keys and tenant records. However, querying PostgreSQL synchronously on every request made authentication vulnerable to total failure during transient database outages.



By caching validated identity metadata (tenant identity, tenant status, key status, expiration) in Redis:

* Active authenticated requests continue to succeed during PostgreSQL downtime.

* PostgreSQL remains the authoritative store when available.

* Cold misses during a database outage fail closed (HTTP 503 Service Unavailable).

* Plaintext API keys and secrets are never stored in cache.



## Invariants & Trade-offs

* Cache TTL is strictly bounded (180s).

* Key and tenant status (`status == "active"`, `tenant_status == "active"`, `expires_at > utc_now()`) are re-validated on every cache hit.

* Key revocation invalidates the Redis cache entry immediately during normal operation.

* During a database outage, revoked keys remain usable only until TTL expiry.

* System never fails open.



\---



# 4. Rejected Architectural Directions



The following approaches are intentionally not part of v1 unless a concrete requirement appears.



\## Kubernetes



Reason:



```text

Complexity > Current Requirement

```



Docker Compose is sufficient for the current project.



\---



\## Kafka



Reason:



```text

No current event-streaming requirement

```



Adding it would increase infrastructure complexity without improving the core demonstration.



\---



\## Multiple Microservices



The gateway should initially remain a reasonably modular application rather than being split into many independently deployed services.



Reason:



```text

Modular monolith

&#x20;       >

Unnecessary microservices

```



for the current project scope.



\---



\## Building a Custom LLM Provider SDK



LiteLLM already provides provider abstraction.



The project should focus on reliability behavior rather than rebuilding provider integrations unnecessarily.



\---



\## Building a Custom Metrics System



Prometheus already provides the metrics infrastructure needed.



The project should expose meaningful application metrics rather than create a separate metrics platform.



\---



\## Building a Custom Tracing System



OpenTelemetry already provides standardized tracing mechanisms.



The project should instrument the gateway rather than create its own tracing protocol.



\---



\# 5. Decision Change Process



An accepted decision should not be changed silently.



When a significant decision needs to change:



```text

Current Decision

&#x20;     ↓

Identify Problem

&#x20;     ↓

Consider Alternatives

&#x20;     ↓

Select New Direction

&#x20;     ↓

Create New ADR

&#x20;     ↓

Mark Old ADR SUPERSEDED

&#x20;     ↓

Update Architecture/Design/Tasks

&#x20;     ↓

Implement

```



Example:



```text

ADR-005

Redis for operational state

&#x20;       ↓

New requirement discovered

&#x20;       ↓

Evaluate alternatives

&#x20;       ↓

ADR-021

New decision

&#x20;       ↓

ADR-005 → SUPERSEDED

```



Old decisions should remain in this document for historical context.



\---



\# 6. Decision Quality Rules



A decision should be based on:



\* Actual project requirements

\* Technical constraints

\* Maintainability

\* Reliability

\* Security

\* Complexity

\* Cost

\* Testability

\* Developer understanding



Do not choose a technology simply because:



\* It is popular.

\* It appears in a tutorial.

\* It looks impressive on a resume.

\* An AI coding agent recommends it.

\* It adds more components to the architecture.



Every significant technology should have a reason to exist.



\---



\# 7. Current Architectural Direction



The current system is intentionally designed around:



```text

Simple

&#x20;       +

Reliable

&#x20;       +

Observable

&#x20;       +

Testable

&#x20;       +

Local-first

```



rather than:



```text

Maximum number of technologies

```



The project should demonstrate engineering judgment through the architecture itself.



\---



\# 8. Future Decisions



Future ADRs should be added when significant questions arise.



Potential examples:



```text

ADR-021  Provider registry format

ADR-022  Circuit-breaker threshold strategy

ADR-023  Redis key design

ADR-024  PostgreSQL schema design

ADR-025  API-key storage strategy

ADR-026  Rate-limiting algorithm

ADR-027  Cost calculation strategy

ADR-028  Provider routing strategy

ADR-029  Health-score calculation

ADR-030  Deployment strategy

```



These should only be added when the corresponding design decision is actually made.



Do not create decisions merely for the sake of increasing the number of ADRs.



\---



\# 9. Final Principle



The purpose of this document is not to prevent change.



It is to ensure that important changes are:



```text

Intentional

Documented

Justified

Traceable

Tested

```



The architecture can evolve.



But it should never evolve accidentally.



