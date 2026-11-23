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
