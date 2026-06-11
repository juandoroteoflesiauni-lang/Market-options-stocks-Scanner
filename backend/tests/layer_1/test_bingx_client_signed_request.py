from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
import pytest

from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient


def _expected_signature(secret: str, query_string: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@pytest.fixture
def signed_client() -> BingXClient:
    return BingXClient(
        api_key="test-api-key",
        secret_key="test-secret-key",
        base_url=BINGX_REST_VST_BASE,
        dry_run=False,
        allow_env_dry_run_override=False,
        recv_window_ms=5000,
        source_key="BX-AI-SKILL",
    )


def test_build_query_and_signature_uses_ascii_sort_and_hex_hmac(signed_client: BingXClient) -> None:
    params = {
        "symbol": "BTC-USDT",
        "timestamp": 1_706_731_500_000,
        "recvWindow": 5000,
    }
    query_string, signature = signed_client._build_query_and_signature(params)

    assert query_string == "recvWindow=5000&symbol=BTC-USDT&timestamp=1706731500000"
    assert signature == _expected_signature("test-secret-key", query_string)


def test_format_signed_url_query_encodes_json_like_values() -> None:
    client = BingXClient(
        api_key="k",
        secret_key="s",
        dry_run=True,
        allow_env_dry_run_override=False,
    )
    normalized = {
        "data": '[{"symbol":"BTC-USDT"}]',
        "recvWindow": "5000",
        "timestamp": "1706731500000",
    }
    query_string = 'data=[{"symbol":"BTC-USDT"}]&recvWindow=5000&timestamp=1706731500000'
    url_query = client._format_signed_url_query(query_string, "abc123", normalized)

    assert "data=%5B%7B%22symbol%22%3A%22BTC-USDT%22%7D%5D" in url_query
    assert url_query.endswith("signature=abc123")


@pytest.mark.asyncio
async def test_signed_get_uses_query_string_and_required_headers(
    signed_client: BingXClient,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content
        return httpx.Response(
            200, json={"code": 0, "data": {"balance": {"availableBalance": "100"}}}
        )

    transport = httpx.MockTransport(handler)
    signed_client._client = httpx.AsyncClient(  # type: ignore[assignment]
        base_url=BINGX_REST_VST_BASE,
        transport=transport,
    )

    await signed_client.fetch_perp_balance()

    assert captured["method"] == "GET"
    assert captured["content"] == b""
    assert "/openApi/swap/v2/user/balance?" in captured["url"]
    assert "signature=" in captured["url"]
    assert "timestamp=" in captured["url"]
    assert captured["headers"]["x-bx-apikey"] == "test-api-key"
    assert captured["headers"]["x-source-key"] == "BX-AI-SKILL"
    assert "x-bx-signature" not in captured["headers"]
    assert "x-bx-timestamp" not in captured["headers"]


@pytest.mark.asyncio
async def test_signed_post_uses_form_body_without_query_string(
    signed_client: BingXClient,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"code": 0, "data": {"orderId": "1"}})

    transport = httpx.MockTransport(handler)
    signed_client._client = httpx.AsyncClient(  # type: ignore[assignment]
        base_url=BINGX_REST_VST_BASE,
        transport=transport,
    )

    await signed_client.set_leverage_perp("BTC-USDT", 5, side="LONG")

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/openApi/swap/v2/user/leverage")
    assert "?" not in captured["url"].split("/openApi/swap/v2/user/leverage", maxsplit=1)[-1]
    assert "signature=" in captured["content"]
    assert "symbol=BTC-USDT" in captured["content"]
    assert captured["headers"]["content-type"] == "application/x-www-form-urlencoded"
    assert captured["headers"]["x-source-key"] == "BX-AI-SKILL"
