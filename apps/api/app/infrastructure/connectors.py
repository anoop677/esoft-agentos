"""Connector adapter contracts and the initial ERPNext read-only capability catalog.

No network requests or external side effects are performed in this milestone.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectorDescriptor:
    connector_type: str
    display_name: str
    description: str
    capabilities: tuple[str, ...]
    mode: str


class ConnectorAdapter(Protocol):
    descriptor: ConnectorDescriptor

    def validate_configuration(self, configuration: dict) -> list[str]:
        """Return configuration errors without contacting the remote system."""


class ERPNextReadOnlyAdapter:
    """Initial ERPNext adapter contract; intentionally does not make HTTP calls."""

    descriptor = ConnectorDescriptor(
        connector_type="erpnext",
        display_name="ERPNext (read-only)",
        description="Fixed ERPNext read-only list tools; execution is gated by platform policy and a default-off feature flag.",
        capabilities=("items.read", "warehouses.read", "stock_bins.read", "suppliers.read"),
        mode="policy_controlled_read_only",
    )

    def validate_configuration(self, configuration: dict) -> list[str]:
        errors: list[str] = []
        site_url = configuration.get("site_url")
        if not isinstance(site_url, str) or not site_url.strip():
            errors.append("site_url is required")
        elif not site_url.startswith("https://"):
            errors.append("site_url must use HTTPS")
        if set(configuration) - {"site_url", "api_version"}:
            errors.append("configuration contains unsupported fields")
        return errors


_ADAPTERS: dict[str, ConnectorAdapter] = {
    "erpnext": ERPNextReadOnlyAdapter(),
}


def get_connector_adapter(connector_type: str) -> ConnectorAdapter | None:
    return _ADAPTERS.get(connector_type)


def list_connector_descriptors() -> list[ConnectorDescriptor]:
    return [adapter.descriptor for adapter in _ADAPTERS.values()]
