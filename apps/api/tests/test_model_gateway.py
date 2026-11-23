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
