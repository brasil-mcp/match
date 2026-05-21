"""API key validation + quota tracking.

API keys são strings opacas geradas no momento da criação do cliente.
Nunca armazenamos a key em si — só sha256 hash. Lookup é por hash.

Quota: increment + check no mesmo trip pro DB (transação) pra evitar race.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date
from typing import Any

from brasil_mcp_match.core.auth.plan import Plan, PlanConfig, get_plan_config
from brasil_mcp_match.core.errors import ErrorCode, ErrorObj


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    id: int
    key_hash: str
    plan: Plan
    customer_email: str | None
    is_revoked: bool

    @property
    def plan_config(self) -> PlanConfig:
        return get_plan_config(self.plan)


# Format: brasilmcp_<32 hex chars>
_KEY_PREFIX = "brasilmcp_"


def generate_api_key() -> tuple[str, str]:
    """Generate a fresh API key. Returns (plaintext_key, sha256_hash)."""
    raw = secrets.token_hex(16)  # 32 hex chars
    plaintext = f"{_KEY_PREFIX}{raw}"
    return plaintext, hash_key(plaintext)


def hash_key(plaintext: str) -> str:
    """Return sha256 hex digest of a plaintext API key."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Either a valid record OR an ErrorObj. Never both."""

    record: ApiKeyRecord | None
    error: ErrorObj | None

    @property
    def authenticated(self) -> bool:
        return self.record is not None


def authenticate(plaintext_key: str | None, lookup: Any) -> AuthResult:
    """Validate an incoming API key.

    `lookup` is a callable `(key_hash: str) -> ApiKeyRecord | None` — usually
    a repository function but in tests we pass a mock.
    """
    if not plaintext_key or not plaintext_key.startswith(_KEY_PREFIX):
        return AuthResult(
            None,
            ErrorObj(
                ErrorCode.INVALID_API_KEY,
                "API key ausente ou formato inválido.",
                "API key missing or malformed.",
            ),
        )

    key_hash = hash_key(plaintext_key)
    record = lookup(key_hash)
    if record is None:
        return AuthResult(
            None,
            ErrorObj(
                ErrorCode.INVALID_API_KEY,
                "API key não reconhecida.",
                "API key not recognized.",
            ),
        )
    if record.is_revoked:
        return AuthResult(
            None,
            ErrorObj(
                ErrorCode.INVALID_API_KEY,
                "API key revogada.",
                "API key revoked.",
            ),
        )
    return AuthResult(record, None)


@dataclass(frozen=True, slots=True)
class QuotaCheck:
    allowed: bool
    used: int
    remaining: int  # -1 = unlimited
    error: ErrorObj | None = None


def current_period(today: date | None = None) -> str:
    """Return current billing period as YYYY-MM."""
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def check_quota(used_in_period: int, plan_config: PlanConfig) -> QuotaCheck:
    """Returns whether one more call is allowed given current period usage."""
    if plan_config.unlimited:
        return QuotaCheck(allowed=True, used=used_in_period, remaining=-1)
    remaining = plan_config.monthly_quota - used_in_period
    if remaining <= 0:
        return QuotaCheck(
            allowed=False,
            used=used_in_period,
            remaining=0,
            error=ErrorObj(
                ErrorCode.QUOTA_EXCEEDED,
                f"Quota do plano {plan_config.name} esgotada para o período.",
                f"Plan {plan_config.name} quota exhausted for the period.",
                suggestion="Considere upgrade para um plano superior.",
            ),
        )
    return QuotaCheck(allowed=True, used=used_in_period, remaining=remaining)
