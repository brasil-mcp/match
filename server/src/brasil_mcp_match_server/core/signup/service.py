"""Signup orchestration — free vs paid flow + status polling + webhook apply.

The service is pure-logic on top of:
  * ``SignupRepo`` (write side: ``signup_request`` table)
  * ``ApiKeyRepo`` (write side: ``api_key`` table)
  * ``AsaasClient`` (paid only — checkout link creation)
  * ``BrevoSender`` (paid only — backup email after webhook)

It does **not** know about HTTP / FastAPI — that lives in the routes module.
Validation errors are returned as :class:`SignupError`; callers translate them
to the right HTTP status code.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from brasil_mcp_match_server.core.auth.api_key import generate_api_key
from brasil_mcp_match_server.core.auth.api_key_repo import ApiKeyRepo
from brasil_mcp_match_server.core.auth.plan import Plan, get_plan_config
from brasil_mcp_match_server.core.signup.models import PLAN_PRICES_BRL
from brasil_mcp_match_server.core.signup.repo import SignupRepo

# RFC-5322-ish — good enough for our use case (we don't deliver mail directly,
# we hand the address to Brevo which does its own validation).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# CPF (11 digits) or CNPJ (14 digits) — we only validate the *shape*, not the
# verifier digit. Asaas does the actual validation.
_CPF_CNPJ_DIGITS_RE = re.compile(r"^\d{11}$|^\d{14}$")

# Free signup rate-limit window (IP-30day uniqueness)
FREE_IP_WINDOW = timedelta(days=30)
# Per-IP hourly rate limit (defense-in-depth alongside slowapi)
HOURLY_IP_WINDOW = timedelta(hours=1)
HOURLY_IP_LIMIT = 5

# Paid signups time out after 24h without payment.
PAID_EXPIRY = timedelta(hours=24)


class ErrCode:
    INVALID_EMAIL = "INVALID_EMAIL"
    INVALID_PLAN = "INVALID_PLAN"
    INVALID_CPF_CNPJ = "INVALID_CPF_CNPJ"
    EMAIL_ALREADY_HAS_KEY = "EMAIL_ALREADY_HAS_KEY"
    IP_RATE_LIMITED = "IP_RATE_LIMITED"
    SIGNUP_DISABLED = "SIGNUP_DISABLED"
    SIGNUP_NOT_FOUND = "SIGNUP_NOT_FOUND"
    SIGNUP_EXPIRED = "SIGNUP_EXPIRED"


@dataclass(frozen=True, slots=True)
class SignupError(Exception):
    """Domain-level signup failure. Routes map ``code`` to an HTTP status."""

    code: str
    message_pt: str
    message_en: str
    http_status: int

    def __str__(self) -> str:  # pragma: no cover — never displayed
        return f"{self.code}: {self.message_en}"


def normalize_cpf_cnpj(value: str | None) -> str | None:
    """Strip non-digit chars; return None for empty input."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def validate_email(raw: str | None) -> str:
    """Lower-case and pattern-check; raises SignupError on bad input."""
    email = (raw or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise SignupError(
            code=ErrCode.INVALID_EMAIL,
            message_pt="E-mail inválido.",
            message_en="Invalid email address.",
            http_status=400,
        )
    return email


def validate_plan(raw: str | None) -> Plan:
    """Parse the plan string; raises SignupError on bad input."""
    if not raw:
        raise SignupError(
            code=ErrCode.INVALID_PLAN,
            message_pt="Plano não informado.",
            message_en="Plan is required.",
            http_status=400,
        )
    try:
        return Plan(raw.lower().strip())
    except ValueError as e:
        raise SignupError(
            code=ErrCode.INVALID_PLAN,
            message_pt=f"Plano inválido: {raw!r}. Opções: free, starter, pro, enterprise.",
            message_en=f"Invalid plan {raw!r}. Choices: free, starter, pro, enterprise.",
            http_status=400,
        ) from e


def validate_cpf_cnpj(value: str | None) -> str | None:
    """Validate shape only (11 or 14 digits). None is allowed (optional field)."""
    normalized = normalize_cpf_cnpj(value)
    if normalized is None:
        return None
    if not _CPF_CNPJ_DIGITS_RE.match(normalized):
        raise SignupError(
            code=ErrCode.INVALID_CPF_CNPJ,
            message_pt="CPF/CNPJ inválido — deve ter 11 ou 14 dígitos.",
            message_en="Invalid CPF/CNPJ — must be 11 or 14 digits.",
            http_status=400,
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FreeSignupResult:
    polling_token: str
    api_key: str
    plan: Plan
    queries_per_day: int
    queries_per_month: int


@dataclass(frozen=True, slots=True)
class PaidSignupStart:
    polling_token: str
    checkout_url: str
    plan: Plan
    amount_brl: Decimal


def _now() -> datetime:
    return datetime.now(UTC)


# Free-plan daily quota — not in PlanConfig (which only encodes monthly).
# The signup response surfaces it to the client; the server enforces only the
# monthly limit (50/mo for free per plan.py).
FREE_QUERIES_PER_DAY = 10


def check_ip_rate_limits(
    signup_repo: SignupRepo, ip_address: str, now: datetime | None = None
) -> None:
    """Enforce the 5/hour total cap. Raises ``IP_RATE_LIMITED`` on breach."""
    now = now or _now()
    recent_total = signup_repo.count_signups_for_ip_since(
        ip_address, now - HOURLY_IP_WINDOW
    )
    if recent_total >= HOURLY_IP_LIMIT:
        raise SignupError(
            code=ErrCode.IP_RATE_LIMITED,
            message_pt="Muitas tentativas de signup neste IP. Aguarde 1h.",
            message_en="Too many signup attempts from this IP. Wait 1 hour.",
            http_status=429,
        )


def start_free_signup(
    *,
    email: str,
    cpf_cnpj: str | None,
    ip_address: str,
    signup_repo: SignupRepo,
    api_key_repo: ApiKeyRepo,
    now: datetime | None = None,
) -> FreeSignupResult:
    """Mint a free-plan key inline. Enforces email-lifetime + IP-30d uniqueness."""
    now = now or _now()

    # Email-lifetime block — one free key per email, forever.
    existing = signup_repo.find_by_email(email)
    if existing is not None:
        raise SignupError(
            code=ErrCode.EMAIL_ALREADY_HAS_KEY,
            message_pt="Este e-mail já tem uma chave gratuita. Use o plano pago para outra.",
            message_en="This email already has a free key. Use a paid plan for another.",
            http_status=409,
        )

    # IP 30-day block — one free key per IP per 30 days.
    free_recent = signup_repo.count_free_signups_for_ip_since(
        ip_address, now - FREE_IP_WINDOW
    )
    if free_recent >= 1:
        raise SignupError(
            code=ErrCode.IP_RATE_LIMITED,
            message_pt="Já existe uma chave gratuita ativa deste IP. Aguarde 30 dias ou use plano pago.",
            message_en="A free key from this IP is still active. Wait 30 days or use a paid plan.",
            http_status=429,
        )

    # Generate the key + persist
    plaintext, key_hash = generate_api_key()
    api_key_record = api_key_repo.insert(
        key_hash=key_hash,
        plan=Plan.FREE,
        customer_email=email,
    )

    token = str(uuid.uuid4())
    signup_repo.create(
        polling_token=token,
        email=email,
        cpf_cnpj=cpf_cnpj,
        plan=Plan.FREE,
        ip_address=ip_address,
        status="delivered",
        asaas_customer_id=None,
        asaas_payment_id=None,
        api_key_id=api_key_record.id,
        # Free flow returns plaintext inline — no need to persist it.
        key_plaintext_once=None,
        delivered_at=now,
        created_at=now,
        expires_at=now + PAID_EXPIRY,
    )

    free_cfg = get_plan_config(Plan.FREE)
    return FreeSignupResult(
        polling_token=token,
        api_key=plaintext,
        plan=Plan.FREE,
        queries_per_day=FREE_QUERIES_PER_DAY,
        queries_per_month=free_cfg.monthly_quota,
    )


async def start_paid_signup(
    *,
    email: str,
    cpf_cnpj: str | None,
    ip_address: str,
    plan: Plan,
    signup_repo: SignupRepo,
    asaas_create_customer,  # type: ignore[no-untyped-def]
    asaas_create_checkout,  # type: ignore[no-untyped-def]
    now: datetime | None = None,
) -> PaidSignupStart:
    """Create Asaas customer + checkout link; persist pending signup row.

    ``asaas_create_customer`` and ``asaas_create_checkout`` are passed in as
    awaitables so tests can inject mocks without monkeypatching httpx.
    """
    now = now or _now()
    amount = PLAN_PRICES_BRL[plan]

    customer_id = await asaas_create_customer(email, email, cpf_cnpj)
    checkout = await asaas_create_checkout(
        customer_id, amount, f"Brasil MCP Match — plano {plan.value}"
    )

    token = str(uuid.uuid4())
    signup_repo.create(
        polling_token=token,
        email=email,
        cpf_cnpj=cpf_cnpj,
        plan=plan,
        ip_address=ip_address,
        status="pending",
        asaas_customer_id=customer_id,
        asaas_payment_id=checkout["payment_id"],
        api_key_id=None,
        key_plaintext_once=None,
        delivered_at=None,
        created_at=now,
        expires_at=now + PAID_EXPIRY,
    )
    return PaidSignupStart(
        polling_token=token,
        checkout_url=checkout["url"],
        plan=plan,
        amount_brl=amount,
    )


@dataclass(frozen=True, slots=True)
class StatusResult:
    status: str
    api_key: str | None
    plan: Plan | None


def poll_status(
    *, polling_token: str, signup_repo: SignupRepo, now: datetime | None = None
) -> StatusResult:
    """Return current status; transitions paid→delivered on first call."""
    now = now or _now()
    row = signup_repo.find_by_token(polling_token)
    if row is None:
        raise SignupError(
            code=ErrCode.SIGNUP_NOT_FOUND,
            message_pt="Signup não encontrado.",
            message_en="Signup not found.",
            http_status=404,
        )

    if row.status == "pending" and now >= row.expires_at:
        signup_repo.mark_expired(row.polling_token)
        raise SignupError(
            code=ErrCode.SIGNUP_EXPIRED,
            message_pt="Signup expirado — abra um novo POST /v1/signup/start.",
            message_en="Signup expired — start a new POST /v1/signup/start.",
            http_status=410,
        )

    if row.status == "pending":
        return StatusResult(status="pending", api_key=None, plan=None)

    if row.status == "paid":
        # First poll after webhook — deliver the key once.
        delivered = signup_repo.mark_delivered(row.polling_token)
        # On a race, mark_delivered might return None (already transitioned by
        # a concurrent poll); fall back to the row we already have.
        key = row.key_plaintext_once
        if delivered is None or key is None:  # pragma: no cover — defensive
            return StatusResult(status="delivered", api_key=None, plan=row.plan)
        return StatusResult(status="paid", api_key=key, plan=row.plan)

    if row.status == "delivered":
        return StatusResult(status="delivered", api_key=None, plan=row.plan)

    if row.status == "expired":
        raise SignupError(
            code=ErrCode.SIGNUP_EXPIRED,
            message_pt="Signup expirado.",
            message_en="Signup expired.",
            http_status=410,
        )

    # cancelled
    return StatusResult(status=row.status, api_key=None, plan=row.plan)


# ---------- Webhook helpers ----------


@dataclass(frozen=True, slots=True)
class WebhookApplyResult:
    """Outcome of applying a webhook event."""

    delivered_key: str | None  # plaintext, for Brevo email; None if no-op
    email: str | None
    plan: Plan | None


def apply_payment_confirmed(
    *,
    asaas_payment_id: str,
    signup_repo: SignupRepo,
    api_key_repo: ApiKeyRepo,
) -> WebhookApplyResult:
    """Idempotent — mint api_key + flip signup_request to 'paid'.

    Returns the plaintext key (for the backup email) on first transition,
    or ``WebhookApplyResult(None, None, None)`` if the row is unknown or
    already past 'pending'.
    """
    row = signup_repo.find_by_asaas_payment_id(asaas_payment_id)
    if row is None or row.status != "pending":
        return WebhookApplyResult(delivered_key=None, email=None, plan=None)

    plaintext, key_hash = generate_api_key()
    api_key_record = api_key_repo.insert(
        key_hash=key_hash,
        plan=row.plan,
        customer_email=row.email,
    )
    updated = signup_repo.mark_paid(
        row.polling_token,
        api_key_id=api_key_record.id,
        key_plaintext_once=plaintext,
    )
    if updated is None:  # pragma: no cover — race with another webhook delivery
        return WebhookApplyResult(delivered_key=None, email=None, plan=None)
    return WebhookApplyResult(delivered_key=plaintext, email=row.email, plan=row.plan)


def apply_payment_refunded(
    *,
    asaas_payment_id: str,
    signup_repo: SignupRepo,
    api_key_repo: ApiKeyRepo,
) -> bool:
    """Revoke the issued key + flip signup to 'cancelled'.

    Returns True if a refund was applied, False if the row is unknown or
    has no api_key issued yet.
    """
    row = signup_repo.find_by_asaas_payment_id(asaas_payment_id)
    if row is None:
        return False
    if row.api_key_id is not None:
        api_key_repo.revoke(row.api_key_id)
    signup_repo.mark_cancelled(row.polling_token)
    return True
