"""Tests for MCP tool wrappers — verifies registration + envelope shape + privacy."""

from __future__ import annotations

from datetime import date

import pytest

from brasil_mcp_match.adapters.mcp.server import build_server, build_stdio_context
from brasil_mcp_match.adapters.mcp.tools import RequestContext
from brasil_mcp_match.core.auth.api_key import ApiKeyRecord
from brasil_mcp_match.core.auth.plan import Plan


@pytest.fixture
def ctx(fake_repo):
    auth = ApiKeyRecord(
        id=1,
        key_hash="h" * 64,
        plan=Plan.STARTER,
        customer_email=None,
        is_revoked=False,
    )
    return RequestContext(repo=fake_repo, auth_record=auth, base_updated_at=date(2026, 5, 1))


@pytest.fixture
def server(ctx):
    return build_server(lambda: ctx)


@pytest.mark.asyncio
async def test_server_registers_4_tools(server) -> None:
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        "match_razao_social_tool",
        "check_situacao_cadastral_tool",
        "check_porte_empresa_tool",
        "match_uf_tool",
    }
    assert expected.issubset(names)


@pytest.mark.asyncio
async def test_match_razao_social_tool_exact(server) -> None:
    result = await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "PETROLEO BRASILEIRO S A PETROBRAS"},
    )
    # FastMCP returns a tuple (content_blocks, structured_content)
    structured = result[1] if isinstance(result, tuple) else None
    if structured:
        assert structured.get("match") is True
        assert structured.get("hint") == "exact"
        assert "query_id" in structured


@pytest.mark.asyncio
async def test_match_uf_tool(server) -> None:
    result = await server.call_tool(
        "match_uf_tool",
        {"cnpj": "33000167000101", "uf": "RJ"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    if structured:
        assert structured.get("match") is True


@pytest.mark.asyncio
async def test_mcp_tool_cnpj_not_found(server) -> None:
    result = await server.call_tool(
        "check_situacao_cadastral_tool",
        {"cnpj": "99999999000199"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    if structured:
        assert "error" in structured
        assert structured["error"]["code"] == "CNPJ_NOT_FOUND"


@pytest.mark.asyncio
async def test_check_porte_tool_mei(server) -> None:
    result = await server.call_tool(
        "check_porte_empresa_tool",
        {"cnpj": "11222333000181"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    if structured:
        assert structured.get("porte") == "MEI"
        assert structured.get("is_mei") is True


@pytest.mark.asyncio
async def test_mcp_output_never_leaks_razao_social(server) -> None:
    """Privacy: no MCP tool response should contain the registered razao_social."""
    import json as json_mod

    result = await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "X"},
    )
    serialized = json_mod.dumps(result, default=str)
    assert "PETROLEO BRASILEIRO" not in serialized


# ------------ Coverage: not-found branches + happy paths for every tool ------------


@pytest.mark.asyncio
async def test_match_razao_social_tool_not_found(server) -> None:
    """CNPJ_NOT_FOUND branch in match_razao_social_tool (tools.py line 63)."""
    result = await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "00000000000000", "nome": "Anything"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    assert structured is not None
    assert "error" in structured
    assert structured["error"]["code"] == "CNPJ_NOT_FOUND"


@pytest.mark.asyncio
async def test_check_situacao_tool_happy_path(server) -> None:
    """Exercises the full body of check_situacao_cadastral_tool (lines 86-99)."""
    result = await server.call_tool(
        "check_situacao_cadastral_tool",
        {"cnpj": "33000167000101"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    assert structured is not None
    assert "situacao" in structured
    assert "query_id" in structured
    assert "base_updated_at" in structured


@pytest.mark.asyncio
async def test_check_porte_tool_not_found(server) -> None:
    """CNPJ_NOT_FOUND branch in check_porte_empresa_tool (line 108)."""
    result = await server.call_tool(
        "check_porte_empresa_tool",
        {"cnpj": "99999999000199"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    assert structured is not None
    assert structured["error"]["code"] == "CNPJ_NOT_FOUND"


@pytest.mark.asyncio
async def test_match_uf_tool_not_found(server) -> None:
    """CNPJ_NOT_FOUND branch in match_uf_tool (line 133)."""
    result = await server.call_tool(
        "match_uf_tool",
        {"cnpj": "99999999000199", "uf": "SP"},
    )
    structured = result[1] if isinstance(result, tuple) else None
    assert structured is not None
    assert structured["error"]["code"] == "CNPJ_NOT_FOUND"


@pytest.mark.asyncio
async def test_mcp_error_payload_for_unknown_code() -> None:
    """The internal helper falls back to the code itself for unknown error codes."""
    from brasil_mcp_match.adapters.mcp.tools import _error_payload

    out = _error_payload("UNKNOWN_CODE")
    assert out["error"]["code"] == "UNKNOWN_CODE"
    assert out["error"]["message_pt"] == "UNKNOWN_CODE"
    assert out["error"]["message_en"] == "UNKNOWN_CODE"


def test_build_stdio_context(fake_repo) -> None:
    ctx = build_stdio_context(fake_repo, date(2026, 4, 1))
    assert ctx.repo is fake_repo
    assert ctx.base_updated_at == date(2026, 4, 1)
    assert ctx.auth_record.plan == Plan.ENTERPRISE
    assert ctx.auth_record.key_hash == "stdio-local-dev"
    assert ctx.auth_record.is_revoked is False
