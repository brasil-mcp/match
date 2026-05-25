"""Tests for notifications.brevo — httpx.MockTransport + env-var control."""

from __future__ import annotations

import json

import httpx
import pytest

from brasil_mcp_match_server.core.notifications import brevo


def _mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


def test_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-abc")
    assert brevo._api_key() == "xkeysib-abc"


def test_api_key_missing_raises(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BREVO_API_KEY"):
        brevo._api_key()


def test_from_email_default(monkeypatch):
    monkeypatch.delenv("BREVO_FROM_EMAIL", raising=False)
    assert brevo._from_email() == "noreply@brasil-mcp.dev"


def test_from_email_override(monkeypatch):
    monkeypatch.setenv("BREVO_FROM_EMAIL", "hi@x.com")
    assert brevo._from_email() == "hi@x.com"


def test_from_name_default(monkeypatch):
    monkeypatch.delenv("BREVO_FROM_NAME", raising=False)
    assert brevo._from_name() == "Brasil MCP"


def test_from_name_override(monkeypatch):
    monkeypatch.setenv("BREVO_FROM_NAME", "Match")
    assert brevo._from_name() == "Match"


def test_render_text_contains_key_and_plan():
    out = brevo._render_text("brasilmcp_xxx", "starter")
    assert "brasilmcp_xxx" in out
    assert "starter" in out
    assert "BRASIL_MCP_MATCH_KEY" in out
    assert "SAVE THIS KEY NOW" in out
    assert "only backup" in out


def test_render_html_contains_key_and_plan():
    out = brevo._render_html("brasilmcp_yyy", "pro")
    assert "brasilmcp_yyy" in out
    assert "pro" in out
    assert "SAVE THIS KEY NOW" in out
    assert "only backup" in out


async def test_send_api_key_email_posts_expected_body(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "key-1")
    monkeypatch.setenv("BREVO_FROM_EMAIL", "noreply@x.com")
    monkeypatch.setenv("BREVO_FROM_NAME", "MyMatch")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "m-1"})

    async with _mock_client(handler) as cli:
        await brevo.send_api_key_email(
            "user@example.com", "brasilmcp_abc", "starter", client=cli
        )

    assert captured["url"] == brevo.BREVO_ENDPOINT
    assert captured["headers"]["api-key"] == "key-1"
    body = captured["body"]
    assert body["sender"] == {"name": "MyMatch", "email": "noreply@x.com"}
    assert body["to"] == [{"email": "user@example.com"}]
    assert "Brasil MCP Match" in body["subject"]
    assert "brasilmcp_abc" in body["textContent"]
    assert "brasilmcp_abc" in body["htmlContent"]
    assert "starter" in body["textContent"]


async def test_send_api_key_email_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "key-1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async with _mock_client(handler) as cli:
        with pytest.raises(httpx.HTTPStatusError):
            await brevo.send_api_key_email("u@x.com", "k", "free", client=cli)


async def test_send_api_key_email_owns_client_when_none_passed(monkeypatch):
    """No injected client → function builds + closes its own."""
    monkeypatch.setenv("BREVO_API_KEY", "key-1")
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"messageId": "m-own"})

    def factory(*args, **kwargs):
        return original(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(
        "brasil_mcp_match_server.core.notifications.brevo.httpx.AsyncClient", factory
    )
    # Should not raise.
    await brevo.send_api_key_email("u@x.com", "k", "pro")
