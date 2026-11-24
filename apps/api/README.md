# AgentOS API

FastAPI service for eSoft AgentOS.

## Requirements

- Docker Engine and Docker Compose plugin
- Or Python 3.12+ for running the API outside Docker

## Run with Docker Compose

From the repository root:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python scripts/apply_migrations.py
```

API: http://localhost:8000  
OpenAPI docs: http://localhost:8000/docs  
Liveness: http://localhost:8000/health/live  
Readiness: http://localhost:8000/health/ready  
Current principal: http://localhost:8000/api/v1/me

The Compose startup runs a one-shot database bootstrap service before the API. It creates or updates the restricted `agentos_app` role without deleting an existing database volume.

## Identity and tenant context

### Local identity mode

For local development only, authentication is disabled by default. The identity endpoint requires an explicit tenant header:

```bash
curl -H "X-Dev-Tenant-ID: demo" http://localhost:8000/api/v1/me
```

This development header is rejected outside `development`, `local`, or `test` environments. Do not use it as a production authentication mechanism.

### Keycloak configuration

Set these environment variables when connecting a Keycloak realm:

- `AUTH_ENABLED=true`
- `KEYCLOAK_ISSUER=https://<keycloak-host>/realms/<realm>`
- `KEYCLOAK_JWKS_URL=https://<keycloak-host>/realms/<realm>/protocol/openid-connect/certs`
- `KEYCLOAK_AUDIENCE=agentos-api`

The Keycloak access token must include a tenant claim named `tenant_id` by default. The claim name can be changed using `TENANT_CLAIM`. The API validates the token signature, issuer, audience, expiry, and subject using the realm's JWKS. Configure a dedicated API audience mapper in Keycloak rather than relying on an unverified client-side value.

## Database migrations and tenant isolation

SQL migrations live in `migrations/` and are tracked in `public.schema_migrations`. Apply pending migrations with:

```bash
docker compose exec api python scripts/apply_migrations.py
```

The migrations create:

- Tenant directory and tenant membership tables.
- Tenant-scoped agent definitions and immutable agent version records.
- Durable agent run records pinned to an immutable agent version.
- PostgreSQL row-level security (RLS) policies on tenant-owned tables.
- Forced RLS, including for the table owner.

Database identities are deliberately separated:

- `agentos` is the local database administrator used only by the migration runner.
- `agentos_app` is the API runtime role. It is `NOSUPERUSER`, `NOBYPASSRLS`, and does not own tenant tables.
- The migration grants the runtime role only the required table operations.

Every tenant-scoped query must use `get_tenant_connection` from `app/infrastructure/tenant_db.py`. It opens a transaction, sets `app.tenant_id` from the verified principal using transaction-local configuration, and confirms the tenant and caller membership are active. The setting is cleared when the transaction ends and the pooled connection is returned.

Do not run the application as a PostgreSQL superuser or a role with `BYPASSRLS`. Tenant provisioning must be performed by a trusted platform provisioning process; do not let a client choose an arbitrary tenant ID and create its own tenant.

## Agent Registry and execution queue

Agent definitions are managed through `/api/v1/agents`. Configuration writes require an active tenant membership with the database role `owner` or `admin`; reads are tenant-scoped. Each agent starts in `draft` and stores an immutable version 1 configuration.

Execution requests are accepted only for agents in `active` status. `POST /api/v1/agents/{agent_id}/runs` validates a bounded JSON input payload and creates a durable `queued` run pinned to the latest immutable agent version. The response is HTTP 202. Use `GET /api/v1/runs/{run_id}` to inspect its state or `GET /api/v1/agents/{agent_id}/runs` to list runs. Run creation does not execute model calls or tools yet; the worker and model gateway are the next runtime milestone. No tool permission is bypassed and no external action is performed by these endpoints.

## Background worker

Docker Compose starts a separate `worker` process alongside the API. It claims jobs with PostgreSQL `FOR UPDATE SKIP LOCKED`, uses a five-minute lease to recover abandoned work, pins execution to the agent version selected when the run was queued, and records output, model name, token usage, and bounded failure details. Failed model calls are retried up to the run's configured attempt limit. Set `OPENAI_API_KEY` in `.env` to enable model execution. The worker uses the restricted `agentos_app` database role; it does not use migration/admin credentials.

The worker remains a text-only model execution path. Tool permissions are not executable adapters. The worker re-checks the tenant kill switch and agent status immediately before starting a run; runs blocked by either control are cancelled and recorded in the run-event log.

## Security, approvals and audit

Migration `005_approval_policy_audit.sql` adds a platform tool catalog, tenant-scoped approval records, tenant-scoped append-only run events, and a tenant-level agent kill switch. The initial catalog contains only the placeholder `inventory.lookup` read action; no connector or tool adapter is wired to it.

The policy engine checks the tenant kill switch, agent status, catalog enablement, immutable agent-version tool allowlist, action, and autonomy level. LLM output is treated only as a proposal, never as authorization. Configured actions requiring approval are added to the tenant approval inbox. Only tenant owners/admins can approve or reject. Approval records expire after 30 minutes and decisions are audited. Even an approved record does not execute a tool in this release.

Endpoints:
- `POST /api/v1/runs/{run_id}/tool-proposals`
- `GET /api/v1/approvals`
- `POST /api/v1/approvals/{approval_id}/decision`
- `GET /api/v1/runs/{run_id}/events`
- `GET/PATCH /api/v1/security/tenant-controls`

Audit events are tenant-isolated and database-enforced append-only. Tool execution, connector credentials, external side effects, and autonomy beyond approval recording remain out of scope until the adapter runtime receives its own security review and integration tests.

## Run tests locally

From `apps/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The PostgreSQL RLS integration test runs when `TEST_DATABASE_URL` or `DATABASE_URL` is set and the schema migration has been applied. It verifies that tenant A cannot read or update records belonging to tenant B and cannot insert rows for another tenant. GitHub Actions runs these tests using the restricted application role, not the database administrator.

## Configuration

Configuration is supplied through environment variables. See `app/core/config.py` for the current settings. Do not commit real credentials or production secrets.

## Connector registry (initial foundation)

Migration `006_connector_registry.sql` adds tenant-scoped connector definitions with forced PostgreSQL RLS. The initial registry advertises an ERPNext read-only adapter contract with item, warehouse, stock-bin, and supplier read capabilities.

Endpoints:
- `GET /api/v1/connectors/catalog` — supported connector types and declared capabilities.
- `POST /api/v1/connectors` — create a tenant connector (owner/admin only).
- `GET /api/v1/connectors` and `GET /api/v1/connectors/{connector_id}` — list and inspect tenant connectors.
- `PATCH /api/v1/connectors/{connector_id}/status` — update connector lifecycle (owner/admin only).
- `POST /api/v1/connectors/{connector_id}/validate` — validate stored configuration.

Important current limitation: this is a registry and adapter-contract milestone, not a live ERPNext integration. Validation does not contact the remote site; credentials are not accepted or stored; no external reads or writes occur. The next milestone must add a tenant-isolated secret vault/resolver and a constrained HTTP client with host allowlisting, timeouts, response limits, read-only resource allowlisting, and integration tests before live ERPNext access is enabled.

## Local connector example

Create a connector after migrations have been applied and a local tenant has been provisioned:

```bash
curl -X POST http://localhost:8000/api/v1/connectors \
  -H "X-Dev-Tenant-ID: demo" \
  -H "Content-Type: application/json" \
  -d '{
    "connector_key": "erpnext-demo",
    "connector_type": "erpnext",
    "display_name": "ERPNext Demo",
    "configuration": {"site_url": "https://erp.example.com", "api_version": "v1"}
  }'
```

This example registers configuration only. It does not authenticate or connect to ERPNext.

## Encrypted connector credentials

Migration `007_connector_secret_vault.sql` adds a tenant-isolated secret table. Connector API credentials are encrypted using AES-256-GCM before persistence. The authenticated additional data binds ciphertext to the tenant ID, connector ID, and key version. The application never returns stored secret values.

Configure `CONNECTOR_ENCRYPTION_KEY` as a URL-safe base64-encoded 32-byte key, and `CONNECTOR_ENCRYPTION_KEY_VERSION` as its version label. Generate a key with:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Keep this key in a deployment secret manager, not in Git or the database. Back it up securely: losing the key makes existing encrypted credentials unrecoverable. The current implementation uses one configured active key; key rotation and multi-key migration need a separate controlled rollout.

Credential endpoints (tenant owner/admin only for changes):
- `PUT /api/v1/connectors/{connector_id}/credentials` — encrypt and store ERPNext API key/secret.
- `GET /api/v1/connectors/{connector_id}/credentials` — return configuration status only, never secret values.
- `DELETE /api/v1/connectors/{connector_id}/credentials` — revoke stored credentials.

The worker-side `load_connector_credentials` helper decrypts only within a tenant-scoped database transaction. No live ERPNext requests are enabled by this change. Do not enable a connector for production use until outbound host controls, timeouts, response-size limits, read-only resource allowlists, and end-to-end integration tests are in place.

## ERPNext read-only HTTP client (not yet exposed as an agent tool)

The internal `ERPNextReadOnlyClient` supports only fixed GET operations for Items, Warehouses, Stock Bins, and Suppliers. It does not accept caller-provided URLs, HTTP methods, DocTypes, or arbitrary filters. It enforces:
- Exact hostname matching against platform-controlled `CONNECTOR_ALLOWED_HOSTS` (comma-separated; no wildcard entries).
- HTTPS only, default port 443, no URL credentials/query/fragment/path.
- TLS verification, no environment proxy inheritance, and no redirects.
- Bounded request timeout, page size (maximum 100), and response size (maximum 2 MB).
- Sanitized errors that do not include response bodies or authorization headers.

Set `CONNECTOR_ALLOWED_HOSTS` in the deployment environment. The host list must be controlled by platform operations, not by tenant connector configuration. Network egress controls must independently restrict outbound destinations to approved public ERPNext hosts and block loopback, private, link-local, and metadata-service ranges; application hostname allowlisting alone is not a substitute for network egress policy.

The internal `build_erpnext_readonly_client` factory checks that the connector belongs to the current tenant, is active, has stored credentials, and targets a platform-approved host before constructing the client. The client is an internal service only. No HTTP endpoint or agent tool invokes it yet. Live ERPNext access remains disabled until egress controls and end-to-end integration tests are deployed and reviewed.
