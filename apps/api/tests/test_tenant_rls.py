# ruff: noqa: I001

import os
from uuid import uuid4

import asyncpg
import pytest


APP_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgres_rls_isolates_tenant_records():
    if not APP_DATABASE_URL or not ADMIN_DATABASE_URL:
        pytest.skip(
            "Set DATABASE_URL and MIGRATION_DATABASE_URL to run PostgreSQL RLS integration tests"
        )

    admin = await asyncpg.connect(ADMIN_DATABASE_URL)
    app_connection = await asyncpg.connect(APP_DATABASE_URL)
    tenant_a = f"rls-test-a-{uuid4()}"
    tenant_b = f"rls-test-b-{uuid4()}"

    try:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2), ($3, $4)",
                tenant_a,
                "RLS Test Tenant A",
                tenant_b,
                "RLS Test Tenant B",
            )
            await admin.execute(
                """
                INSERT INTO public.tenant_memberships (tenant_id, subject_id, role)
                VALUES ($1, 'rls-test-user-a', 'owner'),
                       ($2, 'rls-test-user-b', 'owner')
                """,
                tenant_a,
                tenant_b,
            )
            agent_a_id = await admin.fetchval(
                """
                INSERT INTO public.agent_definitions (tenant_id, slug, name, created_by)
                VALUES ($1, 'inventory-agent', 'Inventory Agent', 'rls-test-user-a')
                RETURNING id
                """,
                tenant_a,
            )
            await admin.execute(
                """
                INSERT INTO public.agent_versions
                    (tenant_id, agent_id, version, manifest, created_by)
                VALUES ($1, $2, 1, '{"purpose":"inventory"}'::jsonb, 'rls-test-user-a')
                """,
                tenant_a,
                agent_a_id,
            )
            await admin.execute(
                """
                INSERT INTO public.agent_definitions (tenant_id, slug, name, created_by)
                VALUES ($1, 'inventory-agent', 'Inventory Agent', 'rls-test-user-b')
                """,
                tenant_b,
            )

        async with app_connection.transaction():
            await app_connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                tenant_a,
            )
            assert await app_connection.fetchval(
                "SELECT count(*) FROM public.agent_definitions"
            ) == 1
            assert await app_connection.fetchval(
                "SELECT count(*) FROM public.agent_versions"
            ) == 1

            await app_connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                tenant_b,
            )
            assert await app_connection.fetchval(
                "SELECT count(*) FROM public.agent_definitions WHERE id = $1",
                agent_a_id,
            ) == 0
            assert await app_connection.fetchval(
                "SELECT count(*) FROM public.agent_versions WHERE agent_id = $1",
                agent_a_id,
            ) == 0

            update_result = await app_connection.execute(
                "UPDATE public.agent_definitions SET name = 'cross-tenant-write' WHERE id = $1",
                agent_a_id,
            )
            assert update_result == "UPDATE 0"

            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with app_connection.transaction():
                    await app_connection.execute(
                        """
                        INSERT INTO public.agent_definitions
                            (tenant_id, slug, name, created_by)
                        VALUES ($1, 'forbidden-agent', 'Forbidden Agent', 'rls-test-user-b')
                        """,
                        tenant_a,
                    )

            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with app_connection.transaction():
                    await app_connection.execute(
                        """
                        INSERT INTO public.tenants (id, display_name)
                        VALUES ('tenant-created-by-app', 'Forbidden Tenant')
                        """
                    )

            await app_connection.execute(
                "SELECT set_config('app.tenant_id', '', true)"
            )
            assert await app_connection.fetchval(
                "SELECT count(*) FROM public.agent_definitions"
            ) == 0
    finally:
        await admin.execute(
            "DELETE FROM public.tenants WHERE id = ANY($1::text[])",
            [tenant_a, tenant_b],
        )
        await app_connection.close()
        await admin.close()
