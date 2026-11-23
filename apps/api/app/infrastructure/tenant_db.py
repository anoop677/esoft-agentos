from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.auth import Principal, get_current_principal


async def get_tenant_connection(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AsyncIterator[asyncpg.Connection]:
    """Yield a tenant-authorized connection with transaction-local RLS context.

    Tenant scope is derived only from the verified Principal. Before exposing
    the connection, the active tenant and caller membership are checked inside
    the same transaction and under the same RLS policy as subsequent queries.
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

        active_tenant = await connection.fetchval(
            "SELECT 1 FROM public.tenants WHERE id = $1 AND status = 'active'",
            principal.tenant_id,
        )
        if not active_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant is unavailable",
            )

        active_membership = await connection.fetchval(
            """
            SELECT 1
            FROM public.tenant_memberships
            WHERE tenant_id = $1 AND subject_id = $2 AND status = 'active'
            """,
            principal.tenant_id,
            principal.subject,
        )
        if not active_membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not an active member of this tenant",
            )

        yield connection
