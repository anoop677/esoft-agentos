"""Allowlisted, bounded ERPNext tool dispatch. No model-supplied URL or DocType is accepted."""
from uuid import UUID

from app.infrastructure.connector_factory import (
    ConnectorNotReadyError,
    build_erpnext_readonly_client,
)
from app.infrastructure.erpnext_client import ERPNextConnectorError
from app.core.config import Settings


TOOL_METHODS = {
    "erpnext.items.list": "list_items",
    "erpnext.warehouses.list": "list_warehouses",
    "erpnext.stock_bins.list": "list_stock_bins",
    "erpnext.suppliers.list": "list_suppliers",
}
_ALLOWED_ARGUMENTS = {"connector_id", "limit", "start"}


class ToolExecutionError(RuntimeError):
    """Safe error suitable for returning to an API caller."""


def validate_tool_arguments(tool_id: str, action: str, arguments: dict) -> tuple[UUID, int, int]:
    if tool_id not in TOOL_METHODS:
        raise ToolExecutionError("Tool is not registered for execution")
    if action != "list":
        raise ToolExecutionError("Only read-only list actions are supported")
    if not isinstance(arguments, dict) or set(arguments) - _ALLOWED_ARGUMENTS:
        raise ToolExecutionError("Tool arguments contain unsupported fields")

    try:
        connector_id = UUID(str(arguments["connector_id"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ToolExecutionError("A valid connector_id is required") from exc

    limit = arguments.get("limit", 50)
    start = arguments.get("start", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ToolExecutionError("limit must be an integer between 1 and 100")
    if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 1_000_000:
        raise ToolExecutionError("start must be an integer between 0 and 1000000")
    return connector_id, limit, start


async def execute_erpnext_tool(
    connection,
    *,
    settings: Settings,
    tenant_id: str,
    tool_id: str,
    action: str,
    arguments: dict,
) -> list[dict]:
    connector_id, limit, start = validate_tool_arguments(tool_id, action, arguments)
    try:
        client = await build_erpnext_readonly_client(
            connection,
            settings=settings,
            tenant_id=tenant_id,
            connector_id=connector_id,
        )
    except ConnectorNotReadyError as exc:
        raise ToolExecutionError(str(exc)) from exc

    async with client:
        method = getattr(client, TOOL_METHODS[tool_id])
        try:
            return await method(limit=limit, start=start)
        except (ERPNextConnectorError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
