"""FastAPI app — REST API do brasil-mcp-match.

Endpoints de health, admin, match e check. ServiceContext (repo + auth lookup)
é injetado via dependency override em tests.
"""

from __future__ import annotations

import sys

import uvicorn
from fastapi import FastAPI, Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import brasil_mcp_match_server
from brasil_mcp_match_server.adapters.rest.routes_internal import router as internal_router
from brasil_mcp_match_server.adapters.rest.routes_lgpd import router as lgpd_router
from brasil_mcp_match_server.adapters.rest.routes_match import router as match_router
from brasil_mcp_match_server.adapters.rest.routes_signup import router as signup_router
from brasil_mcp_match_server.adapters.rest.routes_webhooks import router as webhooks_router


def _rate_key(request: Request) -> str:
    """Rate-limit key: API key hash if present, else IP. Avoids one client
    starving others by sharing IPs (NAT / Cloudflare)."""
    key = request.headers.get("X-Brasil-MCP-Key")
    if key:
        import hashlib

        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_key, default_limits=["120/minute"])

app = FastAPI(
    title="Brasil MCP Match",
    description="Verificação privacy-preserving contra base Receita Federal. Match, don't reveal.",
    version=brasil_mcp_match_server.__version__,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "PLAN_LIMIT",
                "message_pt": "Limite de requisições por minuto excedido.",
                "message_en": "Rate limit per minute exceeded.",
            }
        },
    )


app.include_router(match_router)
app.include_router(lgpd_router)
app.include_router(internal_router)
# Self-service signup + Asaas webhook. The signup endpoints are gated by the
# ``SIGNUP_ENABLED`` env var (free flow + paid bootstrap) and additionally by
# ``ASAAS_API_KEY`` (paid flow). The webhook handler always mounts so Asaas
# retries succeed once env is set; signature verification fails closed if
# ``ASAAS_WEBHOOK_SECRET`` is unset. Brevo delivery (paid only) needs
# ``BREVO_API_KEY``, optional ``BREVO_FROM_EMAIL``, ``BREVO_FROM_NAME``.
#
# Per-IP rate-limit on /v1/signup/start: enforced inside the route
# (5 starts / hour total, plus 1 free / 30d per IP and 1 free per email
# lifetime — see core.signup.service). slowapi's global default
# (120/minute by key) still applies on top.
app.include_router(signup_router)
app.include_router(webhooks_router)


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": brasil_mcp_match_server.__version__}


@app.get("/v1/admin/refresh-status")
def refresh_status() -> dict[str, object]:
    # Lazy import — manifest reads from Postgres; only invoked when this endpoint
    # is hit. Tests that don't exercise this endpoint don't need a live DB.
    from brasil_mcp_match_server.core.ingestion.manifest import latest_successful

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
            "brasil_mcp_match_server.adapters.rest.app:app", host="0.0.0.0", port=port, reload=False
        )
    elif args and args[0] == "version":
        print(brasil_mcp_match_server.__version__)
    else:
        print(
            f"brasil-mcp-match {brasil_mcp_match_server.__version__}\n"
            "Usage:\n"
            "  brasil-mcp-match serve [port]   # start REST API (default port 8000)\n"
            "  brasil-mcp-match version\n"
            "  brasil-mcp-match-server         # start MCP SSE server\n"
            "  brasil-mcp-match-ingest         # run RF ingestion job\n"
        )
