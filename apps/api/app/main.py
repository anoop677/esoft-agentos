from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.agents import router as agents_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.connectors import router as connectors_router
from app.api.v1.identity import router as identity_router
from app.api.v1.runs import router as runs_router
from app.core.config import get_settings
from app.health import router as health_router
from app.infrastructure.connections import close_connections, open_connections

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    await open_connections(application, settings)
    try:
        yield
    finally:
        await close_connections(application)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Enterprise agent platform API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Dev-Tenant-ID"],
)

app.include_router(health_router)
app.include_router(identity_router)
app.include_router(agents_router)
app.include_router(runs_router)
app.include_router(approvals_router)
app.include_router(connectors_router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "version": settings.app_version, "status": "running"}
