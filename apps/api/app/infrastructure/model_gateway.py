import json
from typing import Any

import httpx


class ModelGatewayError(Exception):
    """A safe, provider-neutral model invocation failure."""


class ModelGateway:
    """Small provider adapter. Tool calling is intentionally not enabled."""

    def __init__(self, *, openai_api_key: str | None, timeout_seconds: float = 45.0):
        self.openai_api_key = openai_api_key
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        *,
        provider: str,
        model: str,
        instructions: str,
        input_payload: dict[str, Any],
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        if provider != "openai":
            raise ModelGatewayError(f"Provider '{provider}' is not enabled in this runtime")
        if not self.openai_api_key:
            raise ModelGatewayError("OpenAI credentials are not configured")

        request_body = {
            "model": model,
            "instructions": (
                instructions
                + "\n\nYou are running in a restricted AgentOS execution environment. "
                "You cannot call tools, access external systems, or perform side effects. "
                "Return a concise answer based only on the supplied input."
            ),
            "input": json.dumps(input_payload, ensure_ascii=False),
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
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
        if not output_text:
            raise ModelGatewayError("Model provider returned no text output")

        usage = data.get("usage") or {}
        return {
            "text": output_text,
            "provider": "openai",
            "model": data.get("model", model),
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
        }
