# ruff: noqa: I001

import os
from uuid import uuid4

import asyncpg
import pytest


APP_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL")


@pytest.mark.asyncio
async def test_connector_secret_storage_is_tenant_isolated():
    if not APP_DATABASE_URL or not ADMIN_DATABASE_URL:
        pytest.skip("Set DATABASE_URL and MIGRATION_DATABASE_URL to run connector secret RLS tests")

    admin = await asyncpg.connect(ADMIN_DATABASE_URL)
    app = await asyncpg.connect(APP_DATABASE_URL)
    tenant_a = f"connector-secret-a-{uuid4()}"
    tenant_b = f"connector-secret-b-{uuid4()}"

    try:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2), ($3, $4)",
                tenant_a, "Connector Secret Tenant A", tenant_b, "Connector Secret Tenant B",
            )
            connector_id = await admin.fetchval(
                """
                INSERT INTO public.agent_connectors
                    (tenant_id, connector_key, connector_type, display_name, created_by)
                VALUES ($1, $2, 'erpnext', 'ERPNext test', 'secret-test')
                RETURNING id
                """,
                tenant_a, f"erpnext-{uuid4().hex[:12]}",
            )
            await admin.execute(
                """
                INSERT INTO public.agent_connector_secrets
                    (tenant_id, connector_id, ciphertext, nonce, key_version, updated_by)
                VALUES ($1, $2, $3, $4, 'v1', 'secret-test')
                """,
                tenant_a, connector_id, b"ciphertext", b"123456789012",
            )

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_a)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_connector_secrets WHERE connector_id = $1",
                connector_id,
            ) == 1

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_b)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_connector_secrets WHERE connector_id = $1",
                connector_id,
            ) == 0
    finally:
        await admin.execute(
            "DELETE FROM public.tenants WHERE id = ANY($1::text[])", [tenant_a, tenant_b]
        )
        await app.close()
        await admin.close()
