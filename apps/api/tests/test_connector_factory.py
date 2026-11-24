import base64
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.infrastructure.connector_factory import (
    ConnectorNotReadyError,
    build_erpnext_readonly_client,
)
from app.infrastructure.credential_vault import encrypt_credentials
from app.infrastructure.erpnext_client import ERPNextReadOnlyClient


class FakeConnection:
    def __init__(self, connector_row, secret_row):
        self.connector_row = connector_row
        self.secret_row = secret_row

    async def fetchrow(self, query, *args):
        if "FROM public.agent_connectors" in query:
            return self.connector_row
        if "FROM public.agent_connector_secrets" in query:
            return self.secret_row
        raise AssertionError("Unexpected query")


@pytest.mark.asyncio
async def test_factory_builds_client_from_active_tenant_connector_and_encrypted_secret():
    tenant_id = "tenant-factory-test"
    connector_id = uuid4()
    encryption_key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
    encrypted = encrypt_credentials(
        {"api_key": "test-key", "api_secret": "test-secret"},
        encryption_key=encryption_key,
        key_version="v1",
        tenant_id=tenant_id,
        connector_id=str(connector_id),
    )
    connection = FakeConnection(
        {
            "connector_type": "erpnext",
            "status": "active",
            "configuration": {"site_url": "https://erp.example.com"},
        },
        {
            "ciphertext": encrypted.ciphertext,
            "nonce": encrypted.nonce,
            "key_version": encrypted.key_version,
        },
    )
    settings = Settings(
        connector_encryption_key=encryption_key,
        connector_allowed_hosts="erp.example.com",
    )

    client = await build_erpnext_readonly_client(
        connection,
        settings=settings,
        tenant_id=tenant_id,
        connector_id=connector_id,
    )
    assert isinstance(client, ERPNextReadOnlyClient)
    await client.aclose()


@pytest.mark.asyncio
async def test_factory_rejects_inactive_connector_before_loading_secrets():
    connection = FakeConnection(
        {
            "connector_type": "erpnext",
            "status": "disabled",
            "configuration": {"site_url": "https://erp.example.com"},
        },
        None,
    )
    settings = Settings(
        connector_encryption_key="bm90LWEtcmVhbC1rZXk=",
        connector_allowed_hosts="erp.example.com",
    )

    with pytest.raises(ConnectorNotReadyError, match="not active"):
        await build_erpnext_readonly_client(
            connection,
            settings=settings,
            tenant_id="tenant-a",
            connector_id=uuid4(),
        )
