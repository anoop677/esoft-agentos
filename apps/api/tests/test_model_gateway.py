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
