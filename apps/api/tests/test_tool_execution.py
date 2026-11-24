from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.tool_executions import ToolExecutionRequest
from app.core.config import Settings
from app.infrastructure.tool_execution import (
    TOOL_METHODS,
    ToolExecutionError,
    validate_tool_arguments,
)


def test_registry_contains_only_fixed_erpnext_read_resources():
    assert TOOL_METHODS == {
        "erpnext.items.list": "list_items",
        "erpnext.warehouses.list": "list_warehouses",
        "erpnext.stock_bins.list": "list_stock_bins",
        "erpnext.suppliers.list": "list_suppliers",
    }


def test_validates_connector_and_default_pagination():
    connector_id = uuid4()
    parsed_id, limit, start = validate_tool_arguments(
        "erpnext.items.list", "list", {"connector_id": str(connector_id)}
    )
    assert parsed_id == connector_id
    assert limit == 50
    assert start == 0


@pytest.mark.parametrize(
    ("tool_id", "action", "arguments", "message"),
    [
        ("erpnext.items.delete", "delete", {"connector_id": str(uuid4())}, "not registered"),
        ("erpnext.items.list", "write", {"connector_id": str(uuid4())}, "read-only"),
        ("erpnext.items.list", "list", {"connector_id": str(uuid4()), "doctype": "User"}, "unsupported fields"),
        ("erpnext.items.list", "list", {"connector_id": "not-a-uuid"}, "valid connector_id"),
        ("erpnext.items.list", "list", {"connector_id": str(uuid4()), "limit": 101}, "limit"),
        ("erpnext.items.list", "list", {"connector_id": str(uuid4()), "start": -1}, "start"),
        ("erpnext.items.list", "list", {"connector_id": str(uuid4()), "limit": True}, "limit"),
    ],
)
def test_rejects_unknown_tools_write_actions_and_unbounded_arguments(
    tool_id, action, arguments, message
):
    with pytest.raises(ToolExecutionError, match=message):
        validate_tool_arguments(tool_id, action, arguments)


def test_request_model_rejects_extra_fields_and_oversized_arguments():
    with pytest.raises(ValidationError):
        ToolExecutionRequest(
            tool_id="erpnext.items.list",
            action="list",
            arguments={"connector_id": str(uuid4()), "url": "https://attacker.invalid"},
        )

    with pytest.raises(Exception):
        ToolExecutionRequest(
            tool_id="erpnext.items.list",
            action="list",
            arguments={"connector_id": str(uuid4()), "note": "x" * 17000},
        )


def test_tool_execution_feature_flag_defaults_off():
    assert Settings().tool_execution_enabled is False
