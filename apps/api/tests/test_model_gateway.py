import pytest

from app.infrastructure.model_gateway import ModelGateway, ModelGatewayError


@pytest.mark.asyncio
async def test_model_gateway_requires_openai_credentials():
    gateway = ModelGateway(openai_api_key=None)
    with pytest.raises(ModelGatewayError, match="credentials are not configured"):
        await gateway.generate(
            provider="openai",
            model="gpt-4.1-mini",
            instructions="Answer questions",
            input_payload={"question": "hello"},
            max_output_tokens=100,
            temperature=0,
        )


@pytest.mark.asyncio
async def test_model_gateway_rejects_unenabled_provider():
    gateway = ModelGateway(openai_api_key="test-key")
    with pytest.raises(ModelGatewayError, match="not enabled"):
        await gateway.generate(
            provider="anthropic",
            model="claude",
            instructions="Answer questions",
            input_payload={"question": "hello"},
            max_output_tokens=100,
            temperature=0,
        )


@pytest.mark.asyncio
async def test_model_gateway_parses_responses_api_text_and_usage():
    import json

    import httpx

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4.1-mini"
        assert "cannot call tools" in body["instructions"]
        return httpx.Response(
            200,
            json={
                "model": "gpt-4.1-mini",
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "Inventory looks healthy."}
                        ]
                    }
                ],
                "usage": {"input_tokens": 23, "output_tokens": 8},
            },
        )

    gateway = ModelGateway(
        openai_api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.generate(
        provider="openai",
        model="gpt-4.1-mini",
        instructions="Review inventory",
        input_payload={"sku": "SKU-1"},
        max_output_tokens=100,
        temperature=0,
    )

    assert result["text"] == "Inventory looks healthy."
    assert result["usage"] == {"input_tokens": 23, "output_tokens": 8}

@pytest.mark.asyncio
async def test_model_gateway_exposes_only_fixed_tools_and_parses_proposal():
    import json

    import httpx

    def handler(request):
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto"
        assert "A proposal is not execution" in body["instructions"]
        assert {tool["name"] for tool in body["tools"]} == {
            "erpnext_items_list",
            "erpnext_warehouses_list",
            "erpnext_stock_bins_list",
            "erpnext_suppliers_list",
        }
        assert all(
            tool["parameters"]["additionalProperties"] is False
            for tool in body["tools"]
        )
        return httpx.Response(
            200,
            json={
                "id": "resp_tool_123",
                "model": "gpt-4.1-mini",
                "output": [
                    {
                        "type": "function_call",
                        "name": "erpnext_items_list",
                        "call_id": "call_123",
                        "arguments": json.dumps({
                            "connector_id": "11111111-1111-4111-8111-111111111111",
                            "limit": 25,
                            "start": 0,
                        }),
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 4},
            },
        )

    gateway = ModelGateway(
        openai_api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.generate(
        provider="openai",
        model="gpt-4.1-mini",
        instructions="Inspect inventory",
        input_payload={"question": "List inventory"},
        max_output_tokens=100,
        temperature=0,
        enable_tools=True,
    )

    assert result["text"] == ""
    assert result["tool_calls"] == [{
        "name": "erpnext_items_list",
        "tool_id": "erpnext.items.list",
        "arguments": {
            "connector_id": "11111111-1111-4111-8111-111111111111",
            "limit": 25,
            "start": 0,
        },
        "call_id": "call_123",
    }]
    assert result["response_id"] == "resp_tool_123"


@pytest.mark.asyncio
async def test_model_gateway_rejects_tool_call_when_disabled():
    import httpx

    gateway = ModelGateway(
        openai_api_key="test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "output": [{
                        "type": "function_call",
                        "name": "erpnext_items_list",
                        "arguments": "{}",
                    }]
                },
            )
        ),
    )
    with pytest.raises(ModelGatewayError, match="disabled"):
        await gateway.generate(
            provider="openai",
            model="gpt-4.1-mini",
            instructions="Do not use tools",
            input_payload={},
            max_output_tokens=100,
            temperature=0,
        )



@pytest.mark.asyncio
async def test_model_gateway_sends_function_output_to_previous_response():
    import json

    import httpx

    def handler(request):
        body = json.loads(request.content)
        assert body["previous_response_id"] == "resp_previous"
        assert body["input"] == [{
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "[{\"item_code\":\"SKU-1\"}]",
        }]
        assert body["parallel_tool_calls"] is False
        return httpx.Response(
            200,
            json={
                "id": "resp_followup",
                "model": "gpt-4.1-mini",
                "output": [{
                    "content": [{
                        "type": "output_text",
                        "text": "SKU-1 is available.",
                    }]
                }],
                "usage": {"input_tokens": 30, "output_tokens": 10},
            },
        )

    gateway = ModelGateway(
        openai_api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.generate(
        provider="openai",
        model="gpt-4.1-mini",
        instructions="Analyze inventory",
        input_payload=[{
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "[{\"item_code\":\"SKU-1\"}]",
        }],
        max_output_tokens=100,
        temperature=0,
        enable_tools=True,
        previous_response_id="resp_previous",
    )

    assert result["text"] == "SKU-1 is available."
    assert result["response_id"] == "resp_followup"
    assert result["tool_calls"] == []
