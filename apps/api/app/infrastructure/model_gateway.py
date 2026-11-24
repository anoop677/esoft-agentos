import json
from typing import Any

import httpx


class ModelGatewayError(Exception):
    """A safe, provider-neutral model invocation failure."""


# These names are the only functions exposed to the model. They map to the
# fixed server-side ERPNext registry; the model never supplies URLs or methods.
ERPNEXT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "erpnext_items_list",
        "description": "List a bounded page of ERPNext items for a configured connector.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            },
            "required": ["connector_id", "limit", "start"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "erpnext_warehouses_list",
        "description": "List a bounded page of ERPNext warehouses for a configured connector.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            },
            "required": ["connector_id", "limit", "start"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "erpnext_stock_bins_list",
        "description": "List a bounded page of ERPNext stock bins for a configured connector.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            },
            "required": ["connector_id", "limit", "start"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "erpnext_suppliers_list",
        "description": "List a bounded page of ERPNext suppliers for a configured connector.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            },
            "required": ["connector_id", "limit", "start"],
            "additionalProperties": False,
        },
    },
]

MODEL_TOOL_MAP = {
    "erpnext_items_list": "erpnext.items.list",
    "erpnext_warehouses_list": "erpnext.warehouses.list",
    "erpnext_stock_bins_list": "erpnext.stock_bins.list",
    "erpnext_suppliers_list": "erpnext.suppliers.list",
}


class ModelGateway:
    """Provider adapter with an explicitly opt-in, fixed function catalog."""

    def __init__(
        self,
        *,
        openai_api_key: str | None,
        timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.openai_api_key = openai_api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def generate(
        self,
        *,
        provider: str,
        model: str,
        instructions: str,
        input_payload: dict[str, Any],
        max_output_tokens: int,
        temperature: float,
        enable_tools: bool = False,
    ) -> dict[str, Any]:
        if provider != "openai":
            raise ModelGatewayError(f"Provider '{provider}' is not enabled in this runtime")
        if not self.openai_api_key:
            raise ModelGatewayError("OpenAI credentials are not configured")

        request_body = {
            "model": model,
            "instructions": (
                instructions
                + (
                    "\n\nYou are running in AgentOS. You may propose only the fixed "
                    "read-only ERPNext functions supplied in the tool catalog. Never "
                    "invent connectors, identifiers, URLs, HTTP methods, filters, or actions. "
                    "A proposal is not execution; a tenant administrator must approve it."
                    if enable_tools
                    else "\n\nYou are running in a restricted AgentOS execution environment. "
                    "You cannot call tools, access external systems, or perform side effects. "
                    "Return a concise answer based only on the supplied input."
                )
            ),
            "input": json.dumps(input_payload, ensure_ascii=False),
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        }
        if enable_tools:
            request_body["tools"] = ERPNEXT_TOOL_DEFINITIONS
            request_body["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.openai_api_key}"},
                    json=request_body,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelGatewayError("Model provider request failed") from exc

        output_text = data.get("output_text")
        if not isinstance(output_text, str):
            fragments = []
            for item in data.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        fragments.append(content.get("text", ""))
            output_text = "\n".join(fragments)
        tool_calls = []
        for item in data.get("output", []):
            if item.get("type") != "function_call":
                continue
            if not enable_tools:
                raise ModelGatewayError("Model returned a tool call while tool execution is disabled")
            name = item.get("name")
            if name not in MODEL_TOOL_MAP:
                raise ModelGatewayError("Model requested an unregistered tool")
            raw_arguments = item.get("arguments", "{}")
            try:
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
            except (TypeError, ValueError) as exc:
                raise ModelGatewayError("Model returned invalid tool arguments") from exc
            if not isinstance(arguments, dict):
                raise ModelGatewayError("Model returned invalid tool arguments")
            tool_calls.append({
                "name": name,
                "tool_id": MODEL_TOOL_MAP[name],
                "arguments": arguments,
                "call_id": item.get("call_id"),
            })

        if not output_text and not tool_calls:
            raise ModelGatewayError("Model provider returned no text or tool proposal")

        usage = data.get("usage") or {}
        return {
            "text": output_text or "",
            "tool_calls": tool_calls,
            "provider": "openai",
            "model": data.get("model", model),
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
        }
