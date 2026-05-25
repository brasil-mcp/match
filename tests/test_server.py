"""Tests for the FastMCP server registration — uses a stub client (no httpx)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from brasil_mcp_match.client import ClientConfig
from brasil_mcp_match.server import (
    _augment_signup_start,
    _augment_signup_status,
    _missing_key_envelope,
    build_server,
)


class _StubClient:
    """Captures the last tool call. No real HTTP."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.signup_start_response: dict[str, Any] = {
            "status": "delivered",
            "polling_token": "pt_xyz",
            "api_key": "bmm_live_test",
            "plan": "free",
            "queries_per_day": 10,
            "queries_per_month": 50,
        }
        self.signup_status_response: dict[str, Any] = {"status": "pending"}

    async def match_razao_social(
        self, cnpj: str, nome: str, tolerance: float
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "match_razao_social",
                {"cnpj": cnpj, "nome": nome, "tolerance": tolerance},
            )
        )
        return {"match": True, "query_id": "q1"}

    async def check_situacao_cadastral(self, cnpj: str) -> dict[str, Any]:
        self.calls.append(("check_situacao_cadastral", {"cnpj": cnpj}))
        return {"situacao": "ativa", "query_id": "q2"}

    async def check_porte_empresa(self, cnpj: str) -> dict[str, Any]:
        self.calls.append(("check_porte_empresa", {"cnpj": cnpj}))
        return {"porte": "ME", "query_id": "q3"}

    async def match_uf(self, cnpj: str, uf: str) -> dict[str, Any]:
        self.calls.append(("match_uf", {"cnpj": cnpj, "uf": uf}))
        return {"match": True, "query_id": "q4"}

    async def signup_start(
        self, email: str, plan: str, cpf_cnpj: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "signup_start",
                {"email": email, "plan": plan, "cpf_cnpj": cpf_cnpj},
            )
        )
        return self.signup_start_response

    async def signup_status(self, polling_token: str) -> dict[str, Any]:
        self.calls.append(("signup_status", {"polling_token": polling_token}))
        return self.signup_status_response

    async def socio_match_nome(
        self, cnpj: str, nome: str, tolerance: float
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "socio_match_nome",
                {"cnpj": cnpj, "nome": nome, "tolerance": tolerance},
            )
        )
        return {
            "match": True,
            "hint": "fuzzy_word",
            "confidence": 0.92,
            "query_id": "qs1",
        }

    async def socio_match_cpf(self, cnpj: str, cpf: str) -> dict[str, Any]:
        self.calls.append(("socio_match_cpf", {"cnpj": cnpj, "cpf": cpf}))
        return {"match": False, "query_id": "qs2"}

    async def socio_match_cnpj_socio(
        self, cnpj: str, cnpj_socio: str
    ) -> dict[str, Any]:
        self.calls.append(
            ("socio_match_cnpj_socio", {"cnpj": cnpj, "cnpj_socio": cnpj_socio})
        )
        return {"match": True, "query_id": "qs3"}

    async def socio_check_qualificacao(
        self, cnpj: str, qualificacao: int
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "socio_check_qualificacao",
                {"cnpj": cnpj, "qualificacao": qualificacao},
            )
        )
        return {"exists": True, "count": 2, "query_id": "qs4"}

    async def socio_count(self, cnpj: str) -> dict[str, Any]:
        self.calls.append(("socio_count", {"cnpj": cnpj}))
        return {
            "total": 5,
            "pf": 3,
            "pj": 1,
            "estrangeiro": 1,
            "query_id": "qs5",
        }


def _config_with_key() -> ClientConfig:
    return ClientConfig(base_url="https://example.test", api_key="k", timeout=5.0)


def _config_without_key() -> ClientConfig:
    return ClientConfig(base_url="https://example.test", api_key="", timeout=5.0)


@pytest.fixture
def stub_client():
    return _StubClient()


@pytest.fixture
def server(stub_client):
    return build_server(stub_client, _config_with_key())  # type: ignore[arg-type]


@pytest.fixture
def server_no_key(stub_client):
    return build_server(stub_client, _config_without_key())  # type: ignore[arg-type]


def _payload_dict(call_result: Any) -> dict[str, Any]:
    """Extract the JSON payload from a FastMCP `call_tool` result.

    FastMCP returns `(list[TextContent], dict)` — the dict is the structured
    output; otherwise we parse the text content as JSON.
    """
    if isinstance(call_result, tuple) and len(call_result) == 2:
        contents, structured = call_result
        if isinstance(structured, dict) and structured:
            return structured
        if contents:
            text = contents[0].text  # type: ignore[attr-defined]
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result: {call_result!r}")


# ----- registration -----


@pytest.mark.asyncio
async def test_server_registers_all_tools(server) -> None:
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        # verifiers
        "match_razao_social_tool",
        "check_situacao_cadastral_tool",
        "check_porte_empresa_tool",
        "match_uf_tool",
        # signup
        "request_api_key",
        "check_signup_status",
        # sócio
        "match_nome_socio_tool",
        "match_cpf_socio_tool",
        "match_cnpj_socio_tool",
        "check_qualificacao_socio_tool",
        "count_socios_tool",
    }
    assert expected.issubset(names)
    assert len(names) == 11


# ----- verifier tools (key present) -----


@pytest.mark.asyncio
async def test_match_razao_social_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "PETROBRAS", "tolerance": 0.9},
    )
    assert stub_client.calls == [
        (
            "match_razao_social",
            {"cnpj": "33000167000101", "nome": "PETROBRAS", "tolerance": 0.9},
        )
    ]


@pytest.mark.asyncio
async def test_match_razao_social_tool_default_tolerance(server, stub_client) -> None:
    await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "PETROBRAS"},
    )
    assert stub_client.calls[0][1]["tolerance"] == 0.85


@pytest.mark.asyncio
async def test_check_situacao_tool_forwards(server, stub_client) -> None:
    await server.call_tool("check_situacao_cadastral_tool", {"cnpj": "33000167000101"})
    assert stub_client.calls == [
        ("check_situacao_cadastral", {"cnpj": "33000167000101"})
    ]


@pytest.mark.asyncio
async def test_check_porte_tool_forwards(server, stub_client) -> None:
    await server.call_tool("check_porte_empresa_tool", {"cnpj": "33000167000101"})
    assert stub_client.calls == [("check_porte_empresa", {"cnpj": "33000167000101"})]


@pytest.mark.asyncio
async def test_match_uf_tool_forwards(server, stub_client) -> None:
    await server.call_tool("match_uf_tool", {"cnpj": "33000167000101", "uf": "RJ"})
    assert stub_client.calls == [("match_uf", {"cnpj": "33000167000101", "uf": "RJ"})]


# ----- verifier tools (no key) — MISSING_API_KEY gate -----


@pytest.mark.asyncio
async def test_match_razao_social_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "PETROBRAS"},
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    # Client method must NOT have been called.
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_check_situacao_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "check_situacao_cadastral_tool", {"cnpj": "33000167000101"}
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_check_porte_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "check_porte_empresa_tool", {"cnpj": "33000167000101"}
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_match_uf_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "match_uf_tool", {"cnpj": "33000167000101", "uf": "SP"}
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


# ----- signup tools (work with OR without key) -----


@pytest.mark.asyncio
async def test_request_api_key_free_returns_key_with_next_steps(
    server, stub_client
) -> None:
    result = await server.call_tool(
        "request_api_key", {"email": "u@example.com", "plan": "free"}
    )
    payload = _payload_dict(result)
    assert payload["api_key"] == "bmm_live_test"
    assert payload["status"] == "delivered"
    assert payload["next_steps"].startswith("CRITICAL: Store this api_key NOW")
    assert "BRASIL_MCP_MATCH_KEY" in payload["next_steps"]
    assert payload["warning"]["code"] == "STORE_KEY_NOW"
    assert "lifetime" in payload["warning"]["message_en"]
    assert stub_client.calls == [
        (
            "signup_start",
            {"email": "u@example.com", "plan": "free", "cpf_cnpj": None},
        )
    ]


@pytest.mark.asyncio
async def test_request_api_key_paid_returns_checkout_with_next_steps(
    server, stub_client
) -> None:
    stub_client.signup_start_response = {
        "status": "pending",
        "polling_token": "pt_paid",
        "checkout_url": "https://asaas.com/checkout/abc",
        "plan": "pro",
        "amount_brl": "199.00",
    }
    result = await server.call_tool(
        "request_api_key",
        {"email": "u@example.com", "plan": "pro", "cpf_cnpj": "12345678901"},
    )
    payload = _payload_dict(result)
    assert payload["checkout_url"] == "https://asaas.com/checkout/abc"
    assert payload["status"] == "pending"
    assert "checkout_url" in payload["next_steps"]
    assert "pt_paid" in payload["next_steps"]
    assert stub_client.calls == [
        (
            "signup_start",
            {"email": "u@example.com", "plan": "pro", "cpf_cnpj": "12345678901"},
        )
    ]


@pytest.mark.asyncio
async def test_request_api_key_works_without_configured_key(
    server_no_key, stub_client
) -> None:
    """Signup must be callable when BRASIL_MCP_MATCH_KEY is unset."""
    result = await server_no_key.call_tool(
        "request_api_key", {"email": "u@example.com"}
    )
    payload = _payload_dict(result)
    assert payload["status"] == "delivered"
    assert stub_client.calls[0][0] == "signup_start"


@pytest.mark.asyncio
async def test_request_api_key_passes_error_envelope_through(
    server, stub_client
) -> None:
    stub_client.signup_start_response = {
        "error": {
            "code": "EMAIL_ALREADY_HAS_KEY",
            "message_pt": "x",
            "message_en": "x",
        }
    }
    result = await server.call_tool(
        "request_api_key", {"email": "u@example.com", "plan": "free"}
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "EMAIL_ALREADY_HAS_KEY"
    # No next_steps glued onto an error envelope.
    assert "next_steps" not in payload


@pytest.mark.asyncio
async def test_check_signup_status_forwards(server, stub_client) -> None:
    stub_client.signup_status_response = {
        "status": "paid",
        "api_key": "bmm_live_paid",
        "plan": "pro",
    }
    result = await server.call_tool(
        "check_signup_status", {"polling_token": "pt_paid"}
    )
    payload = _payload_dict(result)
    assert payload["status"] == "paid"
    assert payload["api_key"] == "bmm_live_paid"
    assert payload["next_steps"].startswith("CRITICAL: Store this api_key NOW")
    assert payload["warning"]["code"] == "STORE_KEY_NOW"
    assert "emailed" in payload["warning"]["message_en"].lower()
    assert stub_client.calls == [("signup_status", {"polling_token": "pt_paid"})]


@pytest.mark.asyncio
async def test_check_signup_status_works_without_configured_key(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "check_signup_status", {"polling_token": "pt_xyz"}
    )
    payload = _payload_dict(result)
    assert payload["status"] == "pending"


# ----- unit helpers -----


def test_missing_key_envelope_shape():
    env = _missing_key_envelope()
    assert env["error"]["code"] == "MISSING_API_KEY"
    assert "BRASIL_MCP_MATCH_KEY" in env["error"]["message_pt"]
    assert "request_api_key" in env["error"]["message_en"]


def test_augment_signup_start_delivered_adds_next_steps():
    out = _augment_signup_start(
        {"status": "delivered", "api_key": "k", "polling_token": "p"}
    )
    assert out["next_steps"].startswith("CRITICAL: Store this api_key NOW")
    assert "BRASIL_MCP_MATCH_KEY" in out["next_steps"]
    assert out["warning"]["code"] == "STORE_KEY_NOW"
    assert "lifetime" in out["warning"]["message_en"]


def test_augment_signup_start_preserves_server_warning():
    """If server already provides warning, client passes through without double-wrap."""
    server_warning = {
        "code": "SERVER_CUSTOM",
        "message_pt": "s",
        "message_en": "s",
    }
    out = _augment_signup_start(
        {"status": "delivered", "api_key": "k", "warning": server_warning}
    )
    assert out["warning"] is server_warning  # same object, not overwritten


def test_augment_signup_start_pending_adds_next_steps_with_token():
    out = _augment_signup_start(
        {
            "status": "pending",
            "polling_token": "pt_xyz",
            "checkout_url": "https://asaas/abc",
        }
    )
    assert "pt_xyz" in out["next_steps"]
    assert "checkout_url" in out["next_steps"]


def test_augment_signup_start_error_passes_through():
    inp = {"error": {"code": "INVALID_EMAIL", "message_pt": "x", "message_en": "x"}}
    out = _augment_signup_start(inp)
    assert out == inp
    assert "next_steps" not in out


def test_augment_signup_start_unknown_status_unchanged():
    """Unknown status (defensive): we don't add next_steps but don't crash."""
    inp = {"status": "something_else"}
    out = _augment_signup_start(inp)
    assert "next_steps" not in out


def test_augment_signup_start_preserves_existing_next_steps():
    inp = {"status": "delivered", "api_key": "k", "next_steps": "custom"}
    out = _augment_signup_start(inp)
    assert out["next_steps"] == "custom"


# ---- _augment_signup_status ----


def test_augment_signup_status_paid_adds_warning_and_next_steps():
    out = _augment_signup_status({"status": "paid", "api_key": "k", "plan": "pro"})
    assert out["next_steps"].startswith("CRITICAL: Store this api_key NOW")
    assert out["warning"]["code"] == "STORE_KEY_NOW"
    assert "emailed" in out["warning"]["message_en"].lower()


def test_augment_signup_status_pending_unchanged():
    inp = {"status": "pending"}
    out = _augment_signup_status(inp)
    assert "next_steps" not in out
    assert "warning" not in out


def test_augment_signup_status_delivered_no_key_unchanged():
    """status=delivered (subsequent call, no api_key in response) shouldn't be augmented."""
    inp = {"status": "delivered"}
    out = _augment_signup_status(inp)
    assert "next_steps" not in out
    assert "warning" not in out


def test_augment_signup_status_paid_without_key_unchanged():
    """Defensive: paid status without api_key shouldn't trigger augmentation."""
    inp = {"status": "paid"}
    out = _augment_signup_status(inp)
    assert "warning" not in out


def test_augment_signup_status_error_passes_through():
    inp = {"error": {"code": "SIGNUP_NOT_FOUND", "message_pt": "x", "message_en": "x"}}
    out = _augment_signup_status(inp)
    assert out == inp
    assert "warning" not in out


def test_augment_signup_status_preserves_server_warning():
    server_warning = {"code": "FROM_SERVER", "message_pt": "s", "message_en": "s"}
    out = _augment_signup_status(
        {"status": "paid", "api_key": "k", "warning": server_warning}
    )
    assert out["warning"] is server_warning


# ----- sócio tools (v0.5.0) — key present -----


@pytest.mark.asyncio
async def test_match_nome_socio_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "match_nome_socio_tool",
        {"cnpj": "33000167000101", "nome": "JOSE DA SILVA", "tolerance": 0.9},
    )
    assert stub_client.calls == [
        (
            "socio_match_nome",
            {"cnpj": "33000167000101", "nome": "JOSE DA SILVA", "tolerance": 0.9},
        )
    ]


@pytest.mark.asyncio
async def test_match_nome_socio_tool_default_tolerance(server, stub_client) -> None:
    await server.call_tool(
        "match_nome_socio_tool",
        {"cnpj": "33000167000101", "nome": "JOSE DA SILVA"},
    )
    assert stub_client.calls[0][1]["tolerance"] == 0.85


@pytest.mark.asyncio
async def test_match_cpf_socio_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "match_cpf_socio_tool", {"cnpj": "33000167000101", "cpf": "12345678901"}
    )
    assert stub_client.calls == [
        ("socio_match_cpf", {"cnpj": "33000167000101", "cpf": "12345678901"})
    ]


@pytest.mark.asyncio
async def test_match_cnpj_socio_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "match_cnpj_socio_tool",
        {"cnpj": "33000167000101", "cnpj_socio": "00000000000191"},
    )
    assert stub_client.calls == [
        (
            "socio_match_cnpj_socio",
            {"cnpj": "33000167000101", "cnpj_socio": "00000000000191"},
        )
    ]


@pytest.mark.asyncio
async def test_check_qualificacao_socio_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "check_qualificacao_socio_tool",
        {"cnpj": "33000167000101", "qualificacao": 49},
    )
    assert stub_client.calls == [
        (
            "socio_check_qualificacao",
            {"cnpj": "33000167000101", "qualificacao": 49},
        )
    ]


@pytest.mark.asyncio
async def test_count_socios_tool_forwards(server, stub_client) -> None:
    await server.call_tool("count_socios_tool", {"cnpj": "33000167000101"})
    assert stub_client.calls == [("socio_count", {"cnpj": "33000167000101"})]


# ----- sócio tools — no key — MISSING_API_KEY gate -----


@pytest.mark.asyncio
async def test_match_nome_socio_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "match_nome_socio_tool",
        {"cnpj": "33000167000101", "nome": "JOSE DA SILVA"},
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_match_cpf_socio_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "match_cpf_socio_tool",
        {"cnpj": "33000167000101", "cpf": "12345678901"},
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_match_cnpj_socio_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "match_cnpj_socio_tool",
        {"cnpj": "33000167000101", "cnpj_socio": "00000000000191"},
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_check_qualificacao_socio_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "check_qualificacao_socio_tool",
        {"cnpj": "33000167000101", "qualificacao": 49},
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []


@pytest.mark.asyncio
async def test_count_socios_without_key_returns_missing_envelope(
    server_no_key, stub_client
) -> None:
    result = await server_no_key.call_tool(
        "count_socios_tool", {"cnpj": "33000167000101"}
    )
    payload = _payload_dict(result)
    assert payload["error"]["code"] == "MISSING_API_KEY"
    assert stub_client.calls == []
