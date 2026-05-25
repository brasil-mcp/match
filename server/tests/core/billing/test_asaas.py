"""Tests for billing.asaas — httpx.MockTransport + env-var control."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from brasil_mcp_match_server.core.billing import asaas


def _mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


# ---------- _base_url / _headers ----------


def test_base_url_sandbox_default(monkeypatch):
    monkeypatch.delenv("ASAAS_ENV", raising=False)
    assert asaas._base_url() == asaas.ASAAS_SANDBOX


def test_base_url_sandbox_explicit(monkeypatch):
    monkeypatch.setenv("ASAAS_ENV", "sandbox")
    assert asaas._base_url() == asaas.ASAAS_SANDBOX


def test_base_url_production(monkeypatch):
    monkeypatch.setenv("ASAAS_ENV", "production")
    assert asaas._base_url() == asaas.ASAAS_PROD


def test_headers_includes_token(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    headers = asaas._headers()
    assert headers["access_token"] == "sk-test"
    assert headers["Content-Type"] == "application/json"
    assert "User-Agent" in headers


def test_headers_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ASAAS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ASAAS_API_KEY"):
        asaas._headers()


# ---------- create_customer ----------


async def test_create_customer_success_with_cpf(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"id": "cus_abc"})

    async with _mock_client(handler) as cli:
        cust_id = await asaas.create_customer(
            "Ricardo", "r@x.com", "12345678901", client=cli
        )
    assert cust_id == "cus_abc"
    assert captured["url"].endswith("/customers")
    assert captured["body"] == {
        "name": "Ricardo",
        "email": "r@x.com",
        "cpfCnpj": "12345678901",
    }
    assert captured["headers"]["access_token"] == "sk-test"


async def test_create_customer_omits_cpf_when_none(monkeypatch):
    """``cpf_cnpj`` is optional — sending None must not include the field."""
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "cus_nocpf"})

    async with _mock_client(handler) as cli:
        cust_id = await asaas.create_customer("X", "y@z.com", None, client=cli)
    assert cust_id == "cus_nocpf"
    assert "cpfCnpj" not in captured["body"]


async def test_create_customer_omits_cpf_when_empty(monkeypatch):
    """Empty string treated like None — no cpfCnpj field."""
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "cus_empty"})

    async with _mock_client(handler) as cli:
        await asaas.create_customer("X", "y@z.com", "", client=cli)
    assert "cpfCnpj" not in captured["body"]


async def test_create_customer_owns_client_when_none_passed(monkeypatch):
    """When no client is injected, the function creates and closes its own."""
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "cus_own"})

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(
        "brasil_mcp_match_server.core.billing.asaas.httpx.AsyncClient", factory
    )
    cust_id = await asaas.create_customer("X", "y@z.com", "111", client=None)
    assert cust_id == "cus_own"


async def test_create_customer_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": ["bad"]})

    async with _mock_client(handler) as cli:
        with pytest.raises(httpx.HTTPStatusError):
            await asaas.create_customer("X", "y@z.com", "111", client=cli)


# ---------- create_checkout ----------


async def test_create_checkout_success_single_billing_type(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "link_xyz",
                "url": "https://checkout.asaas.com/link_xyz",
            },
        )

    async with _mock_client(handler) as cli:
        result = await asaas.create_checkout(
            "cus_abc",
            Decimal("49.00"),
            "Brasil MCP Match — plano starter",
            client=cli,
        )
    assert result == {
        "url": "https://checkout.asaas.com/link_xyz",
        "payment_id": "link_xyz",
    }
    assert captured["url"].endswith("/paymentLinks")
    body = captured["body"]
    assert body["customer"] == "cus_abc"
    assert body["value"] == 49.0
    assert body["billingType"] == "CREDIT_CARD"
    assert body["chargeType"] == "DETACHED"
    assert body["dueDateLimitDays"] == 1
    assert body["name"] == "Brasil MCP Match — plano starter"
    assert body["description"] == "Brasil MCP Match — plano starter"


async def test_create_checkout_multiple_billing_types_uses_undefined(monkeypatch):
    """When caller allows more than one billing type, Asaas wants ``UNDEFINED``."""
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "l", "url": "https://x"})

    async with _mock_client(handler) as cli:
        await asaas.create_checkout(
            "c",
            Decimal("10"),
            "d",
            billing_types=["CREDIT_CARD", "PIX"],
            client=cli,
        )
    assert captured["body"]["billingType"] == "UNDEFINED"


async def test_create_checkout_respects_expires_in_days(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "l", "url": "https://x"})

    async with _mock_client(handler) as cli:
        await asaas.create_checkout(
            "c", Decimal("10"), "d", expires_in_days=7, client=cli
        )
    assert captured["body"]["dueDateLimitDays"] == 7


async def test_create_checkout_owns_client_when_none_passed(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "l_own", "url": "https://own"})

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(
        "brasil_mcp_match_server.core.billing.asaas.httpx.AsyncClient", factory
    )
    result = await asaas.create_checkout("c", Decimal("10"), "d")
    assert result == {"url": "https://own", "payment_id": "l_own"}


async def test_create_checkout_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": ["boom"]})

    async with _mock_client(handler) as cli:
        with pytest.raises(httpx.HTTPStatusError):
            await asaas.create_checkout("c", Decimal("10"), "d", client=cli)


# ---------- verify_webhook_signature ----------


def test_verify_webhook_signature_valid_with_explicit_secret():
    payload = b'{"event":"PAYMENT_CONFIRMED"}'
    secret = "supersecret"
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert asaas.verify_webhook_signature(payload, expected, secret=secret) is True


def test_verify_webhook_signature_invalid_with_explicit_secret():
    payload = b"{}"
    assert (
        asaas.verify_webhook_signature(payload, "deadbeef", secret="supersecret")
        is False
    )


def test_verify_webhook_signature_uses_env_when_no_secret_arg(monkeypatch):
    payload = b'{"x":1}'
    monkeypatch.setenv("ASAAS_WEBHOOK_SECRET", "env-secret")
    expected = hmac.new(b"env-secret", payload, hashlib.sha256).hexdigest()
    assert asaas.verify_webhook_signature(payload, expected) is True


def test_verify_webhook_signature_no_secret_returns_false(monkeypatch):
    """Fail-closed when no secret configured (neither arg nor env)."""
    monkeypatch.delenv("ASAAS_WEBHOOK_SECRET", raising=False)
    assert asaas.verify_webhook_signature(b"{}", "anysig") is False


def test_verify_webhook_signature_empty_string_secret_returns_false():
    """Explicit empty-string secret is treated as fail-closed."""
    assert asaas.verify_webhook_signature(b"{}", "x", secret="") is False
