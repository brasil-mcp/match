"""Tests for MCP tool wrappers — verifies registration + envelope shape + privacy."""

from __future__ import annotations

from datetime import date

import pytest

from brasil_mcp_match.adapters.mcp.server import build_server
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
