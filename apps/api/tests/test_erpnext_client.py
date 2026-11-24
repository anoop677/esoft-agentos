import json

import httpx
import pytest

from app.infrastructure.erpnext_client import ERPNextConnectorError, ERPNextReadOnlyClient


def _client(handler, **overrides):
    values = {
        "site_url": "https://erp.example.com",
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "allowed_hosts": {"erp.example.com"},
        "transport": httpx.MockTransport(handler),
    }
    values.update(overrides)
    return ERPNextReadOnlyClient(**values)


@pytest.mark.asyncio
async def test_items_use_fixed_read_only_endpoint_and_bounded_query():
    captured = {}

    async def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [{"name": "ITEM-001"}]})

    async with _client(handler) as client:
        result = await client.list_items(limit=20, start=10)

    assert result == [{"name": "ITEM-001"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/resource/Item"
    assert captured["authorization"] == "token test-api-key:test-api-secret"
    assert captured["params"]["limit_page_length"] == "20"
    assert captured["params"]["limit_start"] == "10"
    assert json.loads(captured["params"]["fields"]) == [
        "name", "item_code", "item_name", "item_group", "stock_uom", "disabled"
    ]


def test_client_rejects_hosts_outside_platform_allowlist():
    with pytest.raises(ValueError, match="allowlist"):
        ERPNextReadOnlyClient(
            site_url="https://unapproved.example.com",
            api_key="key",
            api_secret="secret",
            allowed_hosts={"erp.example.com"},
        )


@pytest.mark.parametrize(
    "site_url",
    [
        "http://erp.example.com",
        "https://user:password@erp.example.com",
        "https://erp.example.com:8443",
        "https://erp.example.com/path",
        "https://erp.example.com/?token=secret",
    ],
)
def test_client_rejects_unsafe_site_urls(site_url):
    with pytest.raises(ValueError):
        ERPNextReadOnlyClient(
            site_url=site_url,
            api_key="key",
            api_secret="secret",
            allowed_hosts={"erp.example.com"},
        )


@pytest.mark.asyncio
async def test_client_does_not_follow_redirects():
    async def handler(request):
        return httpx.Response(302, headers={"Location": "https://attacker.example/"})

    async with _client(handler) as client:
        with pytest.raises(ERPNextConnectorError, match="redirect"):
            await client.list_items()


@pytest.mark.asyncio
async def test_client_rejects_oversized_response():
    async def handler(request):
        return httpx.Response(200, json={"data": [{"name": "x" * 2048}]})

    async with _client(handler, max_response_bytes=1024) as client:
        with pytest.raises(ERPNextConnectorError, match="size limit"):
            await client.list_items()


@pytest.mark.asyncio
async def test_client_rejects_invalid_pagination():
    async def handler(request):
        return httpx.Response(200, json={"data": []})

    async with _client(handler) as client:
        with pytest.raises(ValueError):
            await client.list_items(limit=101)
