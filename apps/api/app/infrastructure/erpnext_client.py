"""Constrained, read-only ERPNext HTTP client.

Only fixed DocType list endpoints are exposed. The caller cannot provide a URL,
path, HTTP method, or arbitrary query expression.
"""
import json
from urllib.parse import urlsplit

import httpx


class ERPNextConnectorError(RuntimeError):
    """Safe connector error that never includes response bodies or credentials."""


class ERPNextReadOnlyClient:
    _RESOURCES = {
        "items": (
            "Item",
            ("name", "item_code", "item_name", "item_group", "stock_uom", "disabled"),
        ),
        "warehouses": (
            "Warehouse",
            ("name", "warehouse_name", "parent_warehouse", "company", "is_group", "disabled"),
        ),
        "stock_bins": (
            "Bin",
            ("item_code", "warehouse", "actual_qty", "reserved_qty", "projected_qty", "valuation_rate"),
        ),
        "suppliers": (
            "Supplier",
            ("name", "supplier_name", "supplier_group", "supplier_type", "disabled"),
        ),
    }

    def __init__(
        self,
        *,
        site_url: str,
        api_key: str,
        api_secret: str,
        allowed_hosts: set[str] | list[str] | tuple[str, ...],
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(site_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        approved_hosts = {value.strip().lower().rstrip(".") for value in allowed_hosts if value.strip()}
        if parsed.scheme != "https" or not host:
            raise ValueError("ERPNext site URL must be an HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ERPNext site URL must not contain credentials, query, or fragment")
        if parsed.port not in (None, 443) or parsed.path not in ("", "/"):
            raise ValueError("ERPNext site URL must use the default HTTPS port and site root")
        if host not in approved_hosts:
            raise ValueError("ERPNext host is not in the platform connector allowlist")
        if not api_key or not api_secret:
            raise ValueError("ERPNext API credentials are required")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("ERPNext timeout must be between 0 and 30 seconds")
        if max_response_bytes < 1024 or max_response_bytes > 10_000_000:
            raise ValueError("ERPNext response limit must be between 1 KiB and 10 MB")

        self._max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            base_url=f"https://{host}",
            headers={
                "Authorization": f"token {api_key}:{api_secret}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            follow_redirects=False,
            trust_env=False,
            verify=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def list_items(self, *, limit: int = 50, start: int = 0) -> list[dict]:
        return await self._list("items", limit=limit, start=start)

    async def list_warehouses(self, *, limit: int = 50, start: int = 0) -> list[dict]:
        return await self._list("warehouses", limit=limit, start=start)

    async def list_stock_bins(self, *, limit: int = 50, start: int = 0) -> list[dict]:
        return await self._list("stock_bins", limit=limit, start=start)

    async def list_suppliers(self, *, limit: int = 50, start: int = 0) -> list[dict]:
        return await self._list("suppliers", limit=limit, start=start)

    async def _list(self, resource_key: str, *, limit: int, start: int) -> list[dict]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("ERPNext page size must be between 1 and 100")
        if not isinstance(start, int) or isinstance(start, bool) or not 0 <= start <= 1_000_000:
            raise ValueError("ERPNext page offset is out of range")

        doctype, fields = self._RESOURCES[resource_key]
        try:
            async with self._client.stream(
                "GET",
                f"/api/resource/{doctype}",
                params={
                    "fields": json.dumps(list(fields), separators=(",", ":")),
                    "limit_page_length": limit,
                    "limit_start": start,
                },
            ) as response:
                if 300 <= response.status_code < 400:
                    raise ERPNextConnectorError("ERPNext returned an unexpected redirect")
                if response.status_code >= 400:
                    raise ERPNextConnectorError(
                        f"ERPNext request failed with HTTP {response.status_code}"
                    )

                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > self._max_response_bytes:
                        raise ERPNextConnectorError("ERPNext response exceeded the configured size limit")
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ERPNextConnectorError("ERPNext request timed out") from exc
        except httpx.RequestError as exc:
            raise ERPNextConnectorError("ERPNext request could not be completed") from exc

        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ERPNextConnectorError("ERPNext returned invalid JSON") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) > limit:
            raise ERPNextConnectorError("ERPNext response did not match the expected list format")
        if any(not isinstance(item, dict) for item in data):
            raise ERPNextConnectorError("ERPNext response contained an invalid record")
        return data
