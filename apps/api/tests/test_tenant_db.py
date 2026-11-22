from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth import Principal
from app.infrastructure.tenant_db import (
    get_tenant_admin_connection,
    get_tenant_connection,
)


class FakeConnection:
    def __init__(self, tenant_active=True, membership_active=True, role="admin"):
        self.executed = []
        self.tenant_active = tenant_active
        self.membership_active = membership_active
        self.role = role

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, query, tenant_id):
        self.executed.append((query, tenant_id))

    async def fetchval(self, query, *args):
        self.executed.append((query, *args))
        if "FROM public.tenants" in query:
            return 1 if self.tenant_active else None
        raise AssertionError("Unexpected fetchval query")

    async def fetchrow(self, query, *args):
        self.executed.append((query, *args))
        if "FROM public.tenant_memberships" in query:
            return {"role": self.role} if self.membership_active else None
        raise AssertionError("Unexpected fetchrow query")


class FakePool:
    def __init__(self, connection):
        self.connection = connection
        self.released = False

    @asynccontextmanager
    async def acquire(self):
        try:
            yield self.connection
        finally:
            self.released = True


def make_request(connection):
    pool = FakePool(connection)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)),
        state=SimpleNamespace(),
    )
    return request, pool


def make_principal():
    return Principal(
        subject="user-123",
        tenant_id="tenant-from-verified-token",
        roles=frozenset({"member"}),
    )


@pytest.mark.asyncio
async def test_tenant_connection_checks_membership_sets_rls_and_resolves_role():
    connection = FakeConnection(role="admin")
    request, pool = make_request(connection)
    dependency = get_tenant_connection(request, make_principal())

    yielded_connection = await anext(dependency)

    assert yielded_connection is connection
    assert connection.executed[0] == (
        "SELECT set_config('app.tenant_id', $1, true)",
        "tenant-from-verified-token",
    )
    assert "FROM public.tenants" in connection.executed[1][0]
    assert connection.executed[2][1:] == (
        "tenant-from-verified-token",
        "user-123",
    )
    assert request.state.tenant_role == "admin"

    await dependency.aclose()
    assert pool.released is True


@pytest.mark.asyncio
async def test_tenant_admin_dependency_rejects_member_role():
    connection = FakeConnection(role="member")
    request, _ = make_request(connection)
    dependency = get_tenant_connection(request, make_principal())
    await anext(dependency)

    with pytest.raises(HTTPException) as error:
        await get_tenant_admin_connection(request, connection)

    assert error.value.status_code == 403
    assert error.value.detail == "Tenant administrator role required"
    await dependency.aclose()


@pytest.mark.asyncio
async def test_tenant_connection_rejects_inactive_membership():
    connection = FakeConnection(membership_active=False)
    request, pool = make_request(connection)
    dependency = get_tenant_connection(request, make_principal())

    with pytest.raises(HTTPException) as error:
        await anext(dependency)

    assert error.value.status_code == 403
    assert error.value.detail == "User is not an active member of this tenant"
    assert pool.released is True


@pytest.mark.asyncio
async def test_tenant_connection_rejects_suspended_tenant():
    connection = FakeConnection(tenant_active=False)
    request, pool = make_request(connection)
    dependency = get_tenant_connection(request, make_principal())

    with pytest.raises(HTTPException) as error:
        await anext(dependency)

    assert error.value.status_code == 403
    assert error.value.detail == "Tenant is unavailable"
    assert pool.released is True
