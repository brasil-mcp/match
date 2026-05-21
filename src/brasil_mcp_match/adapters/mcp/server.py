"""MCP server — registra 4 tools v0.1.0.

Pra dev local roda stdio (sem auth — single-user). Em prod o FastAPI app monta
SSE com auth de API key via header.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from brasil_mcp_match.adapters.mcp.tools import RequestContext, register_tools


def build_server(ctx_resolver: Callable[[], RequestContext]) -> FastMCP:
    """Build a FastMCP server with the 4 v0.1.0 tools.

    `ctx_resolver` is invoked at each tool call to fetch the per-request
    context (repo, auth, base_updated_at). For stdio (single-user dev) the
    resolver returns a constant; for SSE the resolver inspects the HTTP request.
    """
    mcp = FastMCP("brasil-mcp-match")
    register_tools(mcp, ctx_resolver)
    return mcp


def main() -> None:  # pragma: no cover - thin entry
    # Stdio dev mode requires a configured RequestContext — not used in prod.
    raise NotImplementedError(
        "Stdio entry not implemented for brasil-mcp-match. "
        "Use the REST API (brasil-mcp-match serve) or mount the MCP SSE app "
        "from your FastAPI deployment."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
