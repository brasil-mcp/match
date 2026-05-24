"""Tests for the FastMCP server registration — uses a stub client (no httpx)."""

from __future__ import annotations

from typing import Any

import pytest

from brasil_mcp_match.server import build_server


class _StubClient:
    """Captures the last tool call. No real HTTP."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def match_razao_social(self, cnpj: str, nome: str, tolerance: float) -> dict[str, Any]:
        self.calls.append(("match_razao_social", {"cnpj": cnpj, "nome": nome, "tolerance": tolerance}))
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


@pytest.fixture
def stub_client():
    return _StubClient()


@pytest.fixture
def server(stub_client):
    return build_server(stub_client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_server_registers_4_tools(server) -> None:
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {
        "match_razao_social_tool",
        "check_situacao_cadastral_tool",
        "check_porte_empresa_tool",
        "match_uf_tool",
    }.issubset(names)


@pytest.mark.asyncio
async def test_match_razao_social_tool_forwards(server, stub_client) -> None:
    await server.call_tool(
        "match_razao_social_tool",
        {"cnpj": "33000167000101", "nome": "PETROBRAS", "tolerance": 0.9},
    )
    assert stub_client.calls == [
        ("match_razao_social", {"cnpj": "33000167000101", "nome": "PETROBRAS", "tolerance": 0.9})
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
    assert stub_client.calls == [("check_situacao_cadastral", {"cnpj": "33000167000101"})]


@pytest.mark.asyncio
async def test_check_porte_tool_forwards(server, stub_client) -> None:
    await server.call_tool("check_porte_empresa_tool", {"cnpj": "33000167000101"})
    assert stub_client.calls == [("check_porte_empresa", {"cnpj": "33000167000101"})]


@pytest.mark.asyncio
async def test_match_uf_tool_forwards(server, stub_client) -> None:
    await server.call_tool("match_uf_tool", {"cnpj": "33000167000101", "uf": "RJ"})
    assert stub_client.calls == [("match_uf", {"cnpj": "33000167000101", "uf": "RJ"})]
