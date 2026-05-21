"""MCP SSE server — stub.

Diferente do essentials (stdio), aqui o transport é SSE: multi-tenant, autenticado
por API key, exposto via HTTP. Implementação concreta entra após as match tools
estarem prontas.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def build_server() -> FastMCP:
    mcp = FastMCP("brasil-mcp-match")
    # Tools serão registradas em adapters/mcp/tools.py após o core de match estar pronto.
    return mcp


def main() -> None:  # pragma: no cover - thin entry
    server = build_server()
    # FastMCP default run() = stdio. Para SSE: server.sse_app() wrapped em uvicorn.
    # Por enquanto rodamos stdio só pra dev local; produção usa SSE via FastAPI mount.
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
