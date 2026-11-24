import pytest
from pydantic import ValidationError

from app.api.v1.connectors import ERPNextConfiguration
from app.infrastructure.connectors import (
    ERPNextReadOnlyAdapter,
    get_connector_adapter,
    list_connector_descriptors,
)


def test_erpnext_adapter_is_registered_as_read_only_contract():
    adapter = get_connector_adapter("erpnext")
    assert isinstance(adapter, ERPNextReadOnlyAdapter)
    assert adapter.descriptor.mode == "configuration_only"
    assert all(capability.endswith(".read") for capability in adapter.descriptor.capabilities)


def test_erpnext_configuration_requires_https_site_url():
    adapter = ERPNextReadOnlyAdapter()
    assert adapter.validate_configuration({"site_url": "http://erp.example.com"}) == [
        "site_url must use HTTPS"
    ]


def test_erpnext_configuration_rejects_unknown_fields():
    adapter = ERPNextReadOnlyAdapter()
    errors = adapter.validate_configuration({
        "site_url": "https://erp.example.com",
        "api_secret": "must-not-be-accepted",
    })
    assert "configuration contains unsupported fields" in errors


def test_connector_catalog_is_not_empty():
    assert [item.connector_type for item in list_connector_descriptors()] == ["erpnext"]



def test_erpnext_configuration_requires_https_and_rejects_embedded_secrets():
    with pytest.raises(ValidationError):
        ERPNextConfiguration(site_url="http://erp.example.com")

    with pytest.raises(ValidationError):
        ERPNextConfiguration(site_url="https://user:password@erp.example.com")

    with pytest.raises(ValidationError):
        ERPNextConfiguration(site_url="https://erp.example.com/?api_key=secret")


def test_erpnext_configuration_accepts_https_site_url():
    configuration = ERPNextConfiguration(site_url="https://erp.example.com")
    assert str(configuration.site_url).startswith("https://erp.example.com")
