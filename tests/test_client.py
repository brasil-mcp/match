"""Tests for the HTTP client — uses httpx.MockTransport, no real network."""

from __future__ import annotations

import httpx
import pytest

from brasil_mcp_match.client import (
    ClientConfig,
    MatchHttpClient,
    load_config_from_env,
)


def _make_client(handler, *, base_url: str = "https://example.test/match") -> MatchHttpClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"X-Brasil-MCP-Key": "test-key"})
    config = ClientConfig(base_url=base_url, api_key="test-key", timeout=5.0)
    return MatchHttpClient(config, http_client=http)


# ----- env loading -----


def test_load_config_from_env_happy(monkeypatch):
    monkeypatch.setenv("BRASIL_MCP_MATCH_URL", "https://api.example/")
    monkeypatch.setenv("BRASIL_MCP_MATCH_KEY", "k1")
    monkeypatch.delenv("BRASIL_MCP_MATCH_TIMEOUT", raising=False)
    cfg = load_config_from_env()
    assert cfg.base_url == "https://api.example"  # trailing slash stripped
    assert cfg.api_key == "k1"
    assert cfg.timeout == 10.0


def test_load_config_custom_timeout(monkeypatch):
    monkeypatch.setenv("BRASIL_MCP_MATCH_URL", "https://api.example")
    monkeypatch.setenv("BRASIL_MCP_MATCH_KEY", "k1")
    monkeypatch.setenv("BRASIL_MCP_MATCH_TIMEOUT", "3.5")
    cfg = load_config_from_env()
    assert cfg.timeout == 3.5


def test_load_config_missing_url(monkeypatch):
    monkeypatch.delenv("BRASIL_MCP_MATCH_URL", raising=False)
    monkeypatch.setenv("BRASIL_MCP_MATCH_KEY", "k")
    with pytest.raises(RuntimeError, match="BRASIL_MCP_MATCH_URL"):
        load_config_from_env()


def test_load_config_missing_key(monkeypatch):
    monkeypatch.setenv("BRASIL_MCP_MATCH_URL", "https://api.example")
    monkeypatch.delenv("BRASIL_MCP_MATCH_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BRASIL_MCP_MATCH_KEY"):
        load_config_from_env()


# ----- happy-path forwarding -----


async def test_match_razao_social_forwards_payload_and_header():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = req.read().decode()
        return httpx.Response(
            200,
            json={
                "query_id": "q1",
                "base_updated_at": "2026-04-01",
                "match": True,
                "hint": "exact",
            },
        )

    client = _make_client(handler)
    try:
        out = await client.match_razao_social("33000167000101", "PETROBRAS", 0.9)
    finally:
        await client.aclose()

    assert out["match"] is True
    assert out["hint"] == "exact"
    assert seen["url"] == "https://example.test/match/v1/match/razao-social"
    assert seen["headers"]["x-brasil-mcp-key"] == "test-key"
    assert '"tolerance":0.9' in seen["body"]


async def test_check_situacao_cadastral_routes_correct_path():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"situacao": "ativa", "query_id": "q"})

    client = _make_client(handler)
    try:
        out = await client.check_situacao_cadastral("33000167000101")
    finally:
        await client.aclose()
    assert out["situacao"] == "ativa"
    assert captured["url"].endswith("/v1/check/situacao")


async def test_check_porte_empresa_routes_correct_path():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"porte": "ME"})

    client = _make_client(handler)
    try:
        out = await client.check_porte_empresa("33000167000101")
    finally:
        await client.aclose()
    assert out["porte"] == "ME"
    assert captured["url"].endswith("/v1/check/porte")


async def test_match_uf_routes_correct_path():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"match": True})

    client = _make_client(handler)
    try:
        out = await client.match_uf("33000167000101", "RJ")
    finally:
        await client.aclose()
    assert out["match"] is True
    assert captured["url"].endswith("/v1/match/uf")


# ----- error envelopes -----


async def test_timeout_returns_envelope():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated", request=req)

    client = _make_client(handler)
    try:
        out = await client.check_situacao_cadastral("33000167000101")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "UPSTREAM_TIMEOUT"


async def test_network_error_returns_envelope():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    client = _make_client(handler)
    try:
        out = await client.match_uf("33000167000101", "SP")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "UPSTREAM_NETWORK"


async def test_invalid_json_response_returns_envelope():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>oops</html>")

    client = _make_client(handler)
    try:
        out = await client.check_porte_empresa("33000167000101")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "UPSTREAM_BAD_RESPONSE"


async def test_non_dict_json_returns_envelope():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    client = _make_client(handler)
    try:
        out = await client.check_situacao_cadastral("33000167000101")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "UPSTREAM_BAD_RESPONSE"


async def test_fastapi_detail_error_unwrapped():
    """REST returns {"detail": {"code": "CNPJ_NOT_FOUND", ...}} — client unwraps to {"error": ...}."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": {"code": "CNPJ_NOT_FOUND", "message_pt": "x", "message_en": "x"}},
        )

    client = _make_client(handler)
    try:
        out = await client.check_situacao_cadastral("99999999000199")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "CNPJ_NOT_FOUND"


async def test_pre_wrapped_error_envelope_passthrough():
    """If REST already returns an `error` envelope, client does not re-wrap."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "BAD_INPUT", "message_pt": "x", "message_en": "x"}},
        )

    client = _make_client(handler)
    try:
        out = await client.check_porte_empresa("123")
    finally:
        await client.aclose()
    assert out["error"]["code"] == "BAD_INPUT"


async def test_fastapi_detail_string_not_unwrapped():
    """If FastAPI returns `{"detail": "some string"}`, client passes body through unchanged."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "validation failed"})

    client = _make_client(handler)
    try:
        out = await client.check_situacao_cadastral("bad")
    finally:
        await client.aclose()
    # No `error` key synthesized because detail isn't a dict.
    assert out == {"detail": "validation failed"}


async def test_aclose_with_owned_client_does_not_raise():
    cfg = ClientConfig(base_url="https://api.example", api_key="k", timeout=1.0)
    c = MatchHttpClient(cfg)
    await c.aclose()
