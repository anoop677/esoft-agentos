# ruff: noqa: I001

import os
from uuid import uuid4

import asyncpg
import pytest


APP_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL")


@pytest.mark.asyncio
async def test_approval_rls_and_audit_log_immutability():
    if not APP_DATABASE_URL or not ADMIN_DATABASE_URL:
        pytest.skip(
            "Set DATABASE_URL and MIGRATION_DATABASE_URL to run PostgreSQL approval tests"
        )

    admin = await asyncpg.connect(ADMIN_DATABASE_URL)
    app = await asyncpg.connect(APP_DATABASE_URL)
    tenant_a = f"approval-a-{uuid4()}"
    tenant_b = f"approval-b-{uuid4()}"

    try:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2), ($3, $4)",
                tenant_a, "Approval Tenant A", tenant_b, "Approval Tenant B",
            )
            agent_id = await admin.fetchval(
                """
                INSERT INTO public.agent_definitions
                    (tenant_id, slug, name, status, created_by)
                VALUES ($1, $2, 'Approval test agent', 'active', 'approval-test')
                RETURNING id
                """,
                tenant_a, f"approval-{uuid4().hex[:12]}",
            )
            version_id = await admin.fetchval(
                """
                INSERT INTO public.agent_versions
                    (tenant_id, agent_id, version, manifest, created_by)
                VALUES ($1, $2, 1, '{"purpose":"test"}'::jsonb, 'approval-test')
                RETURNING id
                """,
                tenant_a, agent_id,
            )
            run_id = await admin.fetchval(
                """
                INSERT INTO public.agent_runs
                    (tenant_id, agent_id, agent_version_id, requested_by, input)
                VALUES ($1, $2, $3, 'approval-test', '{}'::jsonb)
                RETURNING id
                """,
                tenant_a, agent_id, version_id,
            )
            approval_id = await admin.fetchval(
                """
                INSERT INTO public.agent_approvals
                    (tenant_id, run_id, tool_id, action, requested_by)
                VALUES ($1, $2, 'inventory.lookup', 'read', 'approval-test')
                RETURNING id
                """,
                tenant_a, run_id,
            )

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_a)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_approvals WHERE id = $1", approval_id
            ) == 1
            await app.execute(
                """
                INSERT INTO public.agent_run_events
                    (tenant_id, run_id, event_type, actor, details)
                VALUES ($1, $2, 'test_event', 'approval-test', '{}'::jsonb)
                """,
                tenant_a, run_id,
            )
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_b)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_approvals WHERE id = $1", approval_id
            ) == 0
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_run_events WHERE run_id = $1", run_id
            ) == 0

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_a)
            # Each expected database error must run in a savepoint: PostgreSQL
            # marks the current transaction as failed after a statement error.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with app.transaction():
                    await app.execute(
                        "UPDATE public.agent_run_events SET event_type = 'tampered' WHERE run_id = $1",
                        run_id,
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with app.transaction():
                    await app.execute(
                        "DELETE FROM public.agent_run_events WHERE run_id = $1", run_id
                    )
    finally:
        await admin.execute(
            "DELETE FROM public.tenants WHERE id = ANY($1::text[])", [tenant_a, tenant_b]
        )
        await app.close()
        await admin.close()
