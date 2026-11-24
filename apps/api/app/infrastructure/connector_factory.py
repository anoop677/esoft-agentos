"""Create tenant-scoped connector clients from persisted configuration and secrets."""
import json
from uuid import UUID

from app.core.config import Settings
from app.infrastructure.credential_vault import load_connector_credentials
from app.infrastructure.erpnext_client import ERPNextReadOnlyClient


class ConnectorNotReadyError(RuntimeError):
    """Raised when a connector is not configured for safe use."""


async def build_erpnext_readonly_client(
    connection,
    *,
    settings: Settings,
    tenant_id: str,
    connector_id: UUID,
) -> ERPNextReadOnlyClient:
    """Build a client only for an active, tenant-owned ERPNext connector."""
    if not settings.connector_encryption_key:
        raise ConnectorNotReadyError("Connector credential encryption is unavailable")
    if not settings.allowed_connector_hosts:
        raise ConnectorNotReadyError("No ERPNext hosts are approved by platform operations")

    row = await connection.fetchrow(
        """
        SELECT connector_type, status, configuration
        FROM public.agent_connectors
        WHERE tenant_id = $1 AND id = $2
        """,
        tenant_id, connector_id,
    )
    if row is None:
        raise ConnectorNotReadyError("Connector was not found")
    if row["connector_type"] != "erpnext":
        raise ConnectorNotReadyError("Connector type is not supported by this client")
    if row["status"] != "active":
        raise ConnectorNotReadyError("Connector is not active")

    configuration = row["configuration"]
    if isinstance(configuration, str):
        configuration = json.loads(configuration)
    site_url = configuration.get("site_url")
    if not isinstance(site_url, str):
        raise ConnectorNotReadyError("Connector site URL is not configured")

    credentials = await load_connector_credentials(
        connection,
        tenant_id=tenant_id,
        connector_id=str(connector_id),
        encryption_key=settings.connector_encryption_key,
    )
    if not credentials or not credentials.get("api_key") or not credentials.get("api_secret"):
        raise ConnectorNotReadyError("Connector credentials are not configured")

    try:
        return ERPNextReadOnlyClient(
            site_url=site_url,
            api_key=credentials["api_key"],
            api_secret=credentials["api_secret"],
            allowed_hosts=settings.allowed_connector_hosts,
        )
    except ValueError as exc:
        raise ConnectorNotReadyError("Connector is not permitted by platform security settings") from exc
