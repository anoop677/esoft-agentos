from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.auth import Principal, get_current_principal


async def get_tenant_connection(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AsyncIterator[asyncpg.Connection]:
    """Yield a transaction-scoped connection with PostgreSQL RLS tenant context.

    The tenant identifier comes only from the verified Principal. It is never
    accepted from a query parameter or request body. SET LOCAL semantics are
    provided by set_config(..., true), so the setting is cleared at transaction
    end before the pooled connection can be reused.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection pool is unavailable",
        )

    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT set_config('app.tenant_id', $1, true)",
            principal.tenant_id,
        )
        yield connection
