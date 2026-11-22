# ruff: noqa: I001

import os
from uuid import uuid4

import asyncpg
import pytest


DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


@pytest.mark.asyncio
async def test_postgres_rls_isolates_tenant_records():
    if not DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL RLS integration tests")

    connection = await asyncpg.connect(DATABASE_URL)
    transaction = connection.transaction()
    await transaction.start()

    tenant_a = f"rls-test-a-{uuid4()}"
    tenant_b = f"rls-test-b-{uuid4()}"

    async def set_tenant(tenant_id: str | None):
        await connection.fetchval(
            "SELECT set_config('app.tenant_id', $1, true)",
            tenant_id or "",
        )

    try:
        await set_tenant(tenant_a)
        await connection.execute(
            "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2)",
            tenant_a,
            "RLS Test Tenant A",
        )
        await connection.execute(
            """
            INSERT INTO public.tenant_memberships (tenant_id, subject_id, role)
            VALUES ($1, $2, 'owner')
            """,
            tenant_a,
            "rls-test-user-a",
        )
        agent_a_id = await connection.fetchval(
            """
            INSERT INTO public.agent_definitions (tenant_id, slug, name, created_by)
            VALUES ($1, 'inventory-agent', 'Inventory Agent', 'rls-test-user-a')
            RETURNING id
            """,
            tenant_a,
        )

        await set_tenant(tenant_b)
        await connection.execute(
            "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2)",
            tenant_b,
            "RLS Test Tenant B",
        )
        await connection.execute(
            """
            INSERT INTO public.tenant_memberships (tenant_id, subject_id, role)
            VALUES ($1, $2, 'owner')
            """,
            tenant_b,
            "rls-test-user-b",
        )
        await connection.execute(
            """
            INSERT INTO public.agent_definitions (tenant_id, slug, name, created_by)
            VALUES ($1, 'inventory-agent', 'Inventory Agent', 'rls-test-user-b')
            """,
            tenant_b,
        )

        await set_tenant(tenant_a)
        visible_a = await connection.fetchval(
            "SELECT count(*) FROM public.agent_definitions WHERE id = $1",
            agent_a_id,
        )
        assert visible_a == 1

        await set_tenant(tenant_b)
        visible_from_b = await connection.fetchval(
            "SELECT count(*) FROM public.agent_definitions WHERE id = $1",
            agent_a_id,
        )
        assert visible_from_b == 0

        update_result = await connection.execute(
            "UPDATE public.agent_definitions SET name = 'cross-tenant-write' WHERE id = $1",
            agent_a_id,
        )
        assert update_result == "UPDATE 0"

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO public.agent_definitions
                        (tenant_id, slug, name, created_by)
                    VALUES ($1, 'forbidden-agent', 'Forbidden Agent', 'rls-test-user-b')
                    """,
                    tenant_a,
                )

        await set_tenant(None)
        no_context_count = await connection.fetchval(
            "SELECT count(*) FROM public.agent_definitions"
        )
        assert no_context_count == 0
    finally:
        await transaction.rollback()
        await connection.close()
