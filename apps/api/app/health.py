from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Indicate that the API process is running."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(request: Request):
    """Check required infrastructure dependencies before receiving traffic."""
    db_pool = getattr(request.app.state, "db_pool", None)
    redis_client = getattr(request.app.state, "redis", None)

    if db_pool is None or redis_client is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"database": "unavailable", "redis": "unavailable"}},
        )

    checks = {"database": "ok", "redis": "ok"}
    try:
        async with db_pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except Exception:
        checks["database"] = "error"

    try:
        await redis_client.ping()
    except Exception:
        checks["redis"] = "error"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )
