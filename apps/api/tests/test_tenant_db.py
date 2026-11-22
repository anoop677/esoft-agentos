from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.auth import Principal
from app.infrastructure.tenant_db import get_tenant_connection


class FakeConnection:
    def __init__(self):
        self.executed = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, query, tenant_id):
        self.executed.append((query, tenant_id))


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


@pytest.mark.asyncio
async def test_tenant_connection_uses_verified_principal_and_releases_pool_connection():
    connection = FakeConnection()
    pool = FakePool(connection)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)))
    principal = Principal(
        subject="user-123",
        tenant_id="tenant-from-verified-token",
        roles=frozenset({"member"}),
    )

    dependency = get_tenant_connection(request, principal)
    yielded_connection = await anext(dependency)

    assert yielded_connection is connection
    assert connection.executed == [
        ("SELECT set_config('app.tenant_id', $1, true)", "tenant-from-verified-token")
    ]

    await dependency.aclose()
    assert pool.released is True
