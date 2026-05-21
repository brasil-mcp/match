"""FastAPI app — REST API do brasil-mcp-match.

Endpoints de health, admin, match e check. ServiceContext (repo + auth lookup)
é injetado via dependency override em tests.
"""

from __future__ import annotations

import sys

import uvicorn
from fastapi import FastAPI

import brasil_mcp_match
from brasil_mcp_match.adapters.rest.routes_match import router as match_router

app = FastAPI(
    title="Brasil MCP Match",
    description="Verificação privacy-preserving contra base Receita Federal. Match, don't reveal.",
    version=brasil_mcp_match.__version__,
)

app.include_router(match_router)


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": brasil_mcp_match.__version__}


@app.get("/v1/admin/refresh-status")
def refresh_status() -> dict[str, object]:
    # Lazy import — manifest reads from Postgres; only invoked when this endpoint
    # is hit. Tests that don't exercise this endpoint don't need a live DB.
    from brasil_mcp_match.core.ingestion.manifest import latest_successful

    run = latest_successful()
    if run is None:
        return {"loaded": False, "rf_release": None, "loaded_at": None}
    return {
        "loaded": True,
        "rf_release": run.rf_release,
        "loaded_at": run.completed_at.isoformat() if run.completed_at else None,
        "row_count": run.row_count,
        "file_count": run.file_count,
    }


def cli() -> None:
    """Entry point for `brasil-mcp-match` console script."""
    args = sys.argv[1:]
    if args and args[0] == "serve":
        port = int(args[1]) if len(args) > 1 else 8000
        uvicorn.run(
            "brasil_mcp_match.adapters.rest.app:app", host="0.0.0.0", port=port, reload=False
        )
    elif args and args[0] == "version":
        print(brasil_mcp_match.__version__)
    else:
        print(
            f"brasil-mcp-match {brasil_mcp_match.__version__}\n"
            "Usage:\n"
            "  brasil-mcp-match serve [port]   # start REST API (default port 8000)\n"
            "  brasil-mcp-match version\n"
            "  brasil-mcp-match-server         # start MCP SSE server\n"
            "  brasil-mcp-match-ingest         # run RF ingestion job\n"
        )
