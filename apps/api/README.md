# AgentOS API

FastAPI service for eSoft AgentOS.

## Requirements

- Docker Engine and Docker Compose plugin
- Or Python 3.12+ for running the API outside Docker

## Run with Docker Compose

From the repository root:

```bash
cp .env.example .env
docker compose up --build
```

API: http://localhost:8000  
OpenAPI docs: http://localhost:8000/docs  
Liveness: http://localhost:8000/health/live  
Readiness: http://localhost:8000/health/ready  
Current principal: http://localhost:8000/api/v1/me

The readiness endpoint checks PostgreSQL and Redis. The liveness endpoint only checks that the API process is responding.

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

## Run tests locally

From `apps/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests cover the liveness endpoint and tenant identity resolution in local and mocked-Keycloak modes. They do not require a live Keycloak server.

## Configuration

Configuration is supplied through environment variables. See `app/core/config.py` for the current settings. Do not commit real credentials or production secrets.
