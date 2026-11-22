import asyncpg
from redis.asyncio import Redis

from app.core.config import Settings


async def open_connections(app, settings: Settings) -> None:
    """Create shared infrastructure clients during application startup."""
    app.state.db_pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
        command_timeout=10,
    )
    app.state.redis = Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


async def close_connections(app) -> None:
    """Close shared infrastructure clients during application shutdown."""
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()

    db_pool = getattr(app.state, "db_pool", None)
    if db_pool is not None:
        await db_pool.close()
