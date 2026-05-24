"""MCP server — registra 4 tools v0.1.0.

Pra dev local roda stdio (sem auth — single-user, ApiKeyRecord sintético com
plano ENTERPRISE). Em prod o FastAPI app monta SSE com auth de API key via
header.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import date

from mcp.server.fastmcp import FastMCP

from brasil_mcp_match.adapters.mcp.tools import RequestContext, register_tools
from brasil_mcp_match.core.auth.api_key import ApiKeyRecord
from brasil_mcp_match.core.auth.plan import Plan
from brasil_mcp_match.core.repository.cnpj_repo import CnpjRepo


def build_server(ctx_resolver: Callable[[], RequestContext]) -> FastMCP:
    """Build a FastMCP server with the 4 v0.1.0 tools.

    `ctx_resolver` is invoked at each tool call to fetch the per-request
    context (repo, auth, base_updated_at). For stdio (single-user dev) the
    resolver returns a constant; for SSE the resolver inspects the HTTP request.
    """
    mcp = FastMCP("brasil-mcp-match")
    register_tools(mcp, ctx_resolver)
    return mcp


def build_stdio_context(repo: CnpjRepo, base_updated_at: date) -> RequestContext:
    """Build a single-user RequestContext for stdio dev mode.

    No authentication — uses a synthetic ApiKeyRecord with plan=ENTERPRISE.
    Stdio is single-process single-user; the key_hash is a stable sentinel
    that shows up in audit logs as `stdio-local-dev`.
    """
    auth = ApiKeyRecord(
        id=0,
        key_hash="stdio-local-dev",
        plan=Plan.ENTERPRISE,
        customer_email=None,
        is_revoked=False,
    )
    return RequestContext(repo=repo, auth_record=auth, base_updated_at=base_updated_at)


def main() -> None:  # pragma: no cover - thin entry, exercised manually via stdio
    """Run the MCP server on stdio. Reads `BRASIL_MCP_MATCH_DATABASE_URL`."""
    from brasil_mcp_match.core.ingestion.manifest import base_updated_date
    from brasil_mcp_match.core.repository.connection import connect, get_database_url
    from brasil_mcp_match.core.repository.postgres_repo import PostgresCnpjRepo

    conn_cm = connect()
    conn = conn_cm.__enter__()
    try:
        repo: CnpjRepo = PostgresCnpjRepo(conn)
        base_at = base_updated_date()
        if base_at is None:
            print(
                f"[brasil-mcp-match-server] WARNING: no successful ingestion in DB "
                f"({get_database_url()}). Using today's date as base_updated_at.",
                file=sys.stderr,
            )
            base_at = date.today()

        ctx = build_stdio_context(repo, base_at)
        mcp = build_server(lambda: ctx)
        mcp.run(transport="stdio")
    finally:
        conn_cm.__exit__(None, None, None)


if __name__ == "__main__":  # pragma: no cover
    main()
