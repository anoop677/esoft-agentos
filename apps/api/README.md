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

The readiness endpoint checks PostgreSQL and Redis. The liveness endpoint only checks that the API process is responding.

## Run tests locally

From `apps/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite currently covers the process liveness endpoint. Integration tests for database and Redis readiness will be added with the infrastructure test harness.

## Configuration

Configuration is supplied through environment variables. See `app/core/config.py` for the current settings. Do not commit real credentials or production secrets.
