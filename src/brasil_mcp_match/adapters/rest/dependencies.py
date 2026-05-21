"""FastAPI dependencies — auth, rate limit, repo injection.

DI shape: a single ``ServiceContext`` is built at app startup; routes depend on
``get_service`` which returns the same instance. Tests override via
``app.dependency_overrides[get_service] = ...`` to inject mocks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from brasil_mcp_match.core.auth.api_key import (
    ApiKeyRecord,
    AuthResult,
    authenticate,
)
from brasil_mcp_match.core.lgpd.opt_out import OptOutRecord
from brasil_mcp_match.core.repository.cnpj_repo import CnpjRepo


@dataclass(frozen=True, slots=True)
class ServiceContext:
    """Bundle of services injected into routes. Replaceable in tests.

    `audit_lookup(query_id, api_key_hash)` → audit row dict or None
    `opt_out_register(cnpj, proof)` → OptOutRecord
    `is_opt_out_blocked(cnpj)` → bool
    """

    repo: CnpjRepo
    api_key_lookup: Callable[[str], ApiKeyRecord | None]
    audit_lookup: Callable[[str, str], dict[str, Any] | None]
    opt_out_register: Callable[..., OptOutRecord]
    is_opt_out_blocked: Callable[[str], bool]
    base_updated_at: date


# Filled at app startup. Tests override.
_service: ServiceContext | None = None


def configure_service(ctx: ServiceContext) -> None:
    """Install the singleton ServiceContext. Called at startup."""
    global _service
    _service = ctx


def get_service() -> ServiceContext:
    if _service is None:
        raise RuntimeError(
            "ServiceContext not configured. Call configure_service() at app startup."
        )
    return _service


def require_api_key(
    x_brasil_mcp_key: str | None = Header(default=None, alias="X-Brasil-MCP-Key"),
    service: ServiceContext = Depends(get_service),  # noqa: B008  — FastAPI Depends-as-default convention
) -> AuthResult:
    """FastAPI dependency that validates the API key header."""
    result = authenticate(x_brasil_mcp_key, lookup=service.api_key_lookup)
    if not result.authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.error.to_dict() if result.error else None,
        )
    return result
