"""Tests for app-level paths not covered by route-focused tests.

Covers:
- /v1/admin/refresh-status (both no-runs + populated branches).
- The CLI entrypoint (`serve`, `version`, default-help).
- Rate-limit exception handler (forced by patching the limiter).
- The dependency-injection error when ServiceContext is not configured.
"""

from __future__ import annotations

import sys
from datetime import datetime
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest import app as app_mod
from brasil_mcp_match_server.adapters.rest.app import app, cli
from brasil_mcp_match_server.adapters.rest.dependencies import (
    _service,  # noqa: F401  (we reference the module global indirectly)
)

# ------------ /v1/admin/refresh-status ------------


def test_refresh_status_when_no_runs(service_context, monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns loaded=False when no successful manifest row exists."""
    monkeypatch.setattr(
        "brasil_mcp_match_server.core.ingestion.manifest.latest_successful",
        lambda: None,
    )
    client = TestClient(app)
    r = client.get("/v1/admin/refresh-status")
    assert r.status_code == 200
    assert r.json() == {"loaded": False, "rf_release": None, "loaded_at": None}


def test_refresh_status_when_populated(service_context, monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns full run metadata when a successful run exists."""
    from brasil_mcp_match_server.core.ingestion.manifest import IngestionRun

    fake_run = IngestionRun(
        id=7,
        rf_release="202604",
        started_at=datetime(2026, 5, 1, 3, 0),
        completed_at=datetime(2026, 5, 1, 4, 15),
        status="success",
        file_count=30,
        row_count=60_000_000,
        error_message=None,
    )
    monkeypatch.setattr(
        "brasil_mcp_match_server.core.ingestion.manifest.latest_successful",
        lambda: fake_run,
    )
    client = TestClient(app)
    r = client.get("/v1/admin/refresh-status")
    assert r.status_code == 200
    data = r.json()
    assert data["loaded"] is True
    assert data["rf_release"] == "202604"
    assert data["loaded_at"] == "2026-05-01T04:15:00"
    assert data["row_count"] == 60_000_000
    assert data["file_count"] == 30


# ------------ Rate-limit handler ------------


def test_rate_limit_handler_returns_429() -> None:
    """The exception handler maps RateLimitExceeded → 429 with PLAN_LIMIT detail."""
    from slowapi.errors import RateLimitExceeded

    handler = app_mod._rate_limit_handler
    # RateLimitExceeded takes a `Limit` object; passing a stub since the handler
    # ignores the exception body — it just builds a fixed JSON response.
    fake_limit = MagicMock()
    fake_limit.error_message = "120/minute"
    exc = RateLimitExceeded(fake_limit)

    request = MagicMock()
    resp = handler(request, exc)
    assert resp.status_code == 429
    import json as json_mod

    payload = json_mod.loads(resp.body)
    assert payload["detail"]["code"] == "PLAN_LIMIT"
    assert "Limite" in payload["detail"]["message_pt"]


# ------------ CLI ------------


def test_cli_default_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """No args → prints help, no uvicorn invocation."""
    with patch.object(sys, "argv", ["brasil-mcp-match"]):
        cli()
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "brasil-mcp-match serve" in out


def test_cli_version_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    import brasil_mcp_match_server

    with patch.object(sys, "argv", ["brasil-mcp-match", "version"]):
        cli()
    out = capsys.readouterr().out.strip()
    assert out == brasil_mcp_match_server.__version__


def test_cli_serve_invokes_uvicorn() -> None:
    """`brasil-mcp-match serve` calls uvicorn.run with the app and given port."""
    with patch.object(sys, "argv", ["brasil-mcp-match", "serve", "9000"]):
        with patch.object(app_mod, "uvicorn") as uvicorn_mod:
            cli()
            uvicorn_mod.run.assert_called_once()
            _args, kwargs = uvicorn_mod.run.call_args
            assert kwargs["port"] == 9000


def test_cli_serve_default_port() -> None:
    """`brasil-mcp-match serve` (no port) defaults to 8000."""
    with patch.object(sys, "argv", ["brasil-mcp-match", "serve"]):
        with patch.object(app_mod, "uvicorn") as uvicorn_mod:
            cli()
            _args, kwargs = uvicorn_mod.run.call_args
            assert kwargs["port"] == 8000


# ------------ Dependency wiring: missing ServiceContext ------------


def test_get_service_raises_when_unconfigured() -> None:
    """get_service() must raise RuntimeError when the singleton is None.

    This is purely defensive: routes always run with a configured singleton
    in production. We patch the module global to simulate the unset state.
    """
    from brasil_mcp_match_server.adapters.rest import dependencies as deps_mod

    saved = deps_mod._service
    try:
        deps_mod._service = None
        with pytest.raises(RuntimeError, match="ServiceContext not configured"):
            deps_mod.get_service()
    finally:
        deps_mod._service = saved


def test_require_api_key_passes_through_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When authenticate() returns a result with `error is None` somehow, the
    HTTPException raised by the dependency should still serialize cleanly."""
    # Construct an AuthResult with no record + no error — defensive branch in
    # require_api_key uses `result.error.to_dict() if result.error else None`.
    from brasil_mcp_match_server.adapters.rest import dependencies as deps_mod
    from brasil_mcp_match_server.core.auth.api_key import AuthResult

    def authenticate_returns_none_error(_key: Any, lookup: Any) -> AuthResult:
        return AuthResult(record=None, error=None)

    monkeypatch.setattr(deps_mod, "authenticate", authenticate_returns_none_error)
    # Stub the service so require_api_key can fetch it.
    from dataclasses import replace

    saved = deps_mod._service
    if saved is None:
        # Construct minimal stub
        from datetime import date

        from brasil_mcp_match_server.adapters.rest.dependencies import ServiceContext

        ctx = ServiceContext(
            repo=MagicMock(),
            api_key_lookup=lambda h: None,
            audit_lookup=lambda q, h: None,
            opt_out_register=lambda **k: MagicMock(),
            is_opt_out_blocked=lambda c: False,
            base_updated_at=date(2026, 5, 1),
        )
        deps_mod._service = ctx

    try:
        with pytest.raises(HTTPException) as exc_info:
            deps_mod.require_api_key(
                x_brasil_mcp_key="brasilmcp_x",
                service=deps_mod._service,  # type: ignore[arg-type]
            )
        assert exc_info.value.status_code == 401
        # detail is either None (we passed None) or FastAPI's default
        # placeholder when None is given — both acceptable, just not a
        # leaked error dict.
        assert exc_info.value.detail in (None, "Unauthorized")
    finally:
        deps_mod._service = saved
        # Workaround: replace makes new instance to ensure it stays consistent
        _ = replace


# ------------ Test entrypoint guard ------------

# `if __name__ == "__main__":` guards in server.py / refresh_job.py are marked
# `# pragma: no cover` upstream — we deliberately don't test them.

if False:  # pragma: no cover
    import io  # noqa: F401

    _ = StringIO
