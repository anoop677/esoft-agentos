# eSoft AgentOS

**An enterprise agent platform for building, governing, and operating AI-powered business workflows.**

eSoft AgentOS is a modular platform intended to help organizations connect business systems, create specialized AI agents, coordinate workflows, and operate them with clear permissions, human approvals, and auditability.

> **Project status:** Initial project setup. The repository is being established; capabilities described below are the intended architecture and roadmap, not claims of implemented functionality.

## Product direction

AgentOS is designed around a simple principle: AI agents can reason and propose actions, while deterministic services and policy controls enforce what they are allowed to do.

The long-term platform direction includes:

- A common control plane for creating, configuring, deploying, and monitoring agents.
- Reusable connectors for ERP, WMS, CRM, databases, messaging, cloud services, and custom APIs.
- Multi-step workflows with human approval gates and execution history.
- Tenant-aware knowledge and memory with access controls.
- Model-provider abstraction, observability, evaluation, and cost controls.
- Edge and hardware integrations for supported protocols and devices.

Hardware integrations will be explicitly driver- and protocol-based. Device discovery does not imply universal compatibility, and physical control actions must be separately authorized and safety-checked.

## Initial MVP

The first end-to-end business scenario is:

**Inventory analysis → procurement recommendation → purchase-order draft → human approval**

The initial vertical slice is expected to:

1. Read inventory and related business data through explicitly authorized connectors.
2. Calculate replenishment needs using deterministic business logic.
3. Compare supplier options where data is available.
4. Prepare a purchase-order draft.
5. Route the draft for an authorized person's approval.
6. Record actions and outcomes for audit and review.

The first release should prioritize read-only integrations and draft actions. External writes and financial or operational actions must remain approval-gated until their policies and tests are in place.

## Planned technology baseline

The current proposed stack is:

| Area | Planned technology |
| --- | --- |
| API | Python, FastAPI |
| Web dashboard | React, TypeScript, Tailwind CSS |
| Agent orchestration | LangGraph |
| Primary data store | PostgreSQL |
| Vector search | pgvector |
| Cache and queues | Redis |
| Identity | Keycloak |
| Model gateway | Provider-independent gateway |
| Integration protocol | MCP, plus typed native connectors |
| Local development | Docker Compose |
| Observability | OpenTelemetry, Prometheus, Grafana |
| Object storage | S3-compatible storage |

Technology choices will be validated against implementation needs before they become hard dependencies.

## Architecture principles

- **Policy before execution:** agents never bypass authorization and business rules.
- **Least privilege:** every agent and connector receives only the permissions it needs.
- **Human oversight:** sensitive actions require explicit approval.
- **Tenant isolation:** business data, credentials, and memory are isolated by tenant.
- **Traceability:** executions, tool calls, approvals, and failures are auditable.
- **Reliability:** typed contracts, timeouts, retries, idempotency, and regression tests are standard.
- **Provider independence:** agent definitions should not be tied to one model provider.
- **Safe hardware access:** supported protocols, device-specific adapters, and explicit control policies.

## Development approach

Development is organized around small, testable vertical slices:

1. Establish repository standards and architecture decisions.
2. Build the API, identity, persistence, and configuration foundation.
3. Add agent registration, execution, and tool contracts.
4. Implement connector health checks and permission enforcement.
5. Deliver the inventory-to-procurement approval workflow.
6. Add the dashboard, observability, and deployment packaging.
7. Expand through automated functional, security, and regression testing.

A short MVP milestone is not equivalent to enterprise production readiness. Security review, operational hardening, and customer validation remain explicit release gates.

## Repository structure (target)

```text
apps/
  api/             # FastAPI application
  dashboard/       # React web application
  worker/          # Background job worker
core/
  agent_runtime/
  orchestrator/
  model_gateway/
  tool_registry/
  memory/
  policy_engine/
  approval_engine/
  event_manager/
agents/
  inventory/
  procurement/
  finance/
  sales/
integrations/
  erpnext/
  smartwms/
  crm/
  messaging/
  mcp_servers/
security/
  identity/
  permissions/
  secrets/
  audit/
evaluation/
  datasets/
  test_cases/
  regression/
infrastructure/
  docker/
  aws/
  monitoring/
docs/
  architecture/
  api/
  standards/
```

This is a target structure; directories will be introduced as their components are implemented.

## Getting started

The repository is currently at its bootstrap stage. Setup instructions will be added alongside the first runnable application and Docker Compose environment.

## Contribution and engineering standards

Before implementation, each component should have:

- A documented purpose and boundary.
- Typed input and output contracts.
- Explicit permissions and tenant context.
- Defined timeout, retry, and idempotency behavior where applicable.
- Unit and integration tests.
- Structured logs and audit events for consequential actions.
- Security and failure-mode review.

All feature work should be developed on a feature branch and merged through reviewed pull requests. The `main` branch is reserved for stable, reviewed work.

## License

No open-source license has been assigned yet. All rights remain with the repository owner unless a license is added.
