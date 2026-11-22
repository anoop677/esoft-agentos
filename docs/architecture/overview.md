# eSoft AgentOS — Architecture Overview

**Status:** Proposed baseline for MVP implementation  
**Last updated:** 2026-11-22

## 1. Goal

Build a tenant-aware enterprise agent platform that connects business systems, runs bounded AI workflows, and makes consequential actions observable and governable.

The first product slice is inventory analysis through procurement draft and human approval.

## 2. System boundaries

### User experience
The web dashboard provides organization/workspace context, agent configuration, workflow visibility, connector setup, approvals, execution history, and operational health.

### API and identity boundary
The API authenticates users, resolves tenant context, validates requests, and applies authorization before dispatching work. Identity is delegated to Keycloak; the application remains responsible for mapping identity claims to tenant and application permissions.

### Orchestration and runtime
LangGraph is the proposed workflow orchestration layer. Agent execution uses typed inputs/outputs and a constrained tool registry. The orchestrator coordinates steps; it is not itself an authorization authority.

### Policy and approvals
A deterministic policy layer checks tenant, user, agent, tool, resource, and action permissions. High-impact actions create approval requests. Approval decisions are recorded and revalidated at execution time.

### Data and memory
PostgreSQL is the system of record. pgvector may support semantic retrieval. Redis is used for cache and transient coordination, not as the authoritative audit store. Tenant scoping must be enforced in application queries and, where appropriate, database row-level security.

### Integrations
Connectors expose typed, explicitly permissioned capabilities. Each connector declares supported operations, required scopes, health checks, timeouts, and error behavior. MCP can be used where it provides a useful interoperability boundary; it is not a reason to weaken typed contracts or permission checks.

### Model gateway
A provider-independent gateway handles model selection, credentials, budgets, timeouts, retries, and usage telemetry. Model output is untrusted input and must be validated before it can influence tool execution.

### Observability
Execution traces, tool calls, policy decisions, approval events, errors, and costs should be correlated with tenant, workflow, agent version, and execution identifiers without logging secrets or unnecessary personal data.

## 3. Logical request flow

1. User submits a request through the dashboard or an authorized channel.
2. API authenticates the user and establishes tenant/workspace context.
3. Policy layer validates access to the requested agent and data.
4. Orchestrator creates an execution with a bounded budget and deadline.
5. Agent requests tools through the registry using typed contracts.
6. Policy layer authorizes each tool call and resource scope.
7. Read operations execute through connectors; results are validated and recorded.
8. Deterministic business services calculate quantities and enforce business constraints.
9. Sensitive write operations become approval requests rather than immediate actions.
10. On approval, the system rechecks authorization and business state before execution.
11. Execution outcome, audit events, and metrics are persisted.

## 4. Initial deployment topology

For local development, use Docker Compose with separate API, worker, dashboard, PostgreSQL, Redis, and Keycloak services as the application is implemented.

Keep configuration twelve-factor and secrets external to source control. AWS deployment should use managed equivalents where practical (for example, managed PostgreSQL, Redis-compatible cache, object storage, and container orchestration). Avoid coupling core domain logic to AWS-specific services.

## 5. MVP vertical slice

**Inventory → Procurement → Purchase-order draft → Human approval**

Required inputs include on-hand quantity, reserved quantity, open purchase orders, demand history or forecast, lead time, minimum order quantity, supplier terms, and budget policy. Missing or stale inputs must be surfaced; agents must not silently invent them.

Replenishment quantities are calculated by deterministic, tested domain logic. The model may explain results and coordinate tools, but must not be the source of truth for arithmetic or policy decisions.

## 6. Autonomy levels

- **A0 — Observe:** read and summarize authorized data.
- **A1 — Recommend:** propose actions without changing external systems.
- **A2 — Approval-gated execution:** prepare actions and execute only after an authorized approval.
- **A3 — Bounded autonomy:** execute only within explicit limits, allowlists, budgets, and rollback/reconciliation procedures.

MVP starts at A0/A1 and introduces A2 for the purchase-order draft workflow. A3 is out of initial scope.

## 7. Non-functional requirements

- Tenant isolation and least privilege.
- Typed contracts and schema validation.
- Idempotency for retried mutations.
- Explicit deadlines, rate limits, and cost budgets.
- Durable execution state and recoverable failures.
- Auditability without leaking secrets.
- Automated unit, integration, security, and regression tests.
- Health checks and actionable operational telemetry.
- Versioned agents, tools, workflows, and configuration.

## 8. Open decisions

The following must be validated during implementation rather than assumed:
- Exact Keycloak realm/client and tenant-claim model.
- Durable workflow persistence and recovery strategy.
- Whether Celery is sufficient for the MVP or a durable workflow engine is required.
- Model gateway choice and supported provider matrix.
- Database tenancy strategy and row-level security boundaries.
- Secret-management implementation for local and AWS deployments.
- Connector packaging, trust model, and upgrade lifecycle.
