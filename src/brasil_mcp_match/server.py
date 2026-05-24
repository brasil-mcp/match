"""MCP stdio server — forwards each tool call to the Brasil MCP Match REST API.

Tools mirror the upstream 4 v0.1.0 verifiers. Each call is an HTTPS POST with
``X-Brasil-MCP-Key`` header against ``BRASIL_MCP_MATCH_URL``.
"""
# pyright: reportUnusedFunction=false
# (FastMCP collects the @mcp.tool() decorated inner functions via side effect.)

from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from brasil_mcp_match.client import MatchHttpClient, load_config_from_env


def build_server(client: MatchHttpClient) -> FastMCP:
    """Construct a FastMCP server and register the 4 forwarding tools."""
    mcp = FastMCP("brasil-mcp-match-client")

    @mcp.tool()
    async def match_razao_social_tool(
        cnpj: str, nome: str, tolerance: float = 0.85
    ) -> dict[str, Any]:
        """Verifica se o nome informado bate com a razão social registrada na Receita Federal pro CNPJ, sem expor a razão social registrada. Retorna match boolean + confidence + hint.

        NOTA: A base do Match exclui empresas MEI e CNPJs com situação cadastral não-ativa (suspensa/inapta/baixada/nula). CNPJs nesses casos retornam CNPJ_NOT_FOUND."""
        return await client.match_razao_social(cnpj, nome, tolerance)

    @mcp.tool()
    async def check_situacao_cadastral_tool(cnpj: str) -> dict[str, Any]:
        """Retorna a situação cadastral do CNPJ. A base do Match só inclui empresas ATIVAS — qualquer outro estado (suspensa/inapta/baixada/nula) ou empresa MEI retorna CNPJ_NOT_FOUND."""
        return await client.check_situacao_cadastral(cnpj)

    @mcp.tool()
    async def check_porte_empresa_tool(cnpj: str) -> dict[str, Any]:
        """Retorna o porte da empresa (MEI/ME/EPP/DEMAIS) + flag is_simples."""
        return await client.check_porte_empresa(cnpj)

    @mcp.tool()
    async def match_uf_tool(cnpj: str, uf: str) -> dict[str, Any]:
        """Verifica se a UF informada bate com a UF do endereço registrado pro CNPJ. Retorna boolean (sem expor o endereço)."""
        return await client.match_uf(cnpj, uf)

    return mcp


def main() -> None:  # pragma: no cover - thin entry, exercised via stdio runtime
    """Read env, build the FastMCP server, serve stdio. Never returns."""
    try:
        config = load_config_from_env()
    except RuntimeError as exc:
        print(f"[brasil-mcp-match-client] {exc}", file=sys.stderr)
        sys.exit(2)

    client = MatchHttpClient(config)
    mcp = build_server(client)
    try:
        mcp.run(transport="stdio")
    finally:
        asyncio.run(client.aclose())


if __name__ == "__main__":  # pragma: no cover
    main()
