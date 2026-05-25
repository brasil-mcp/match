"""Public signup endpoints — no auth, IP rate-limited.

Free plan delivers a key immediately. Paid plans return an Asaas checkout
URL; the actual key is generated when Asaas webhook confirms payment, then
delivered via /v1/signup/status (plaintext-once) AND emailed via Brevo.

This module surfaces the HTTP contract; the actual orchestration lives in
:mod:`brasil_mcp_match_server.core.signup.service`. Validation errors raised
by the service are translated to FastAPI ``HTTPException`` using the standard
``{"detail": {"code": ..., "message_pt": ..., "message_en": ...}}`` envelope.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from brasil_mcp_match_server.adapters.rest.dependencies import (
    ServiceContext,
    get_service,
)
from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.billing import asaas as asaas_client
from brasil_mcp_match_server.core.signup import service as signup_service
from brasil_mcp_match_server.core.signup.service import (
    ErrCode,
    SignupError,
)

router = APIRouter(prefix="/v1")


def _ip_address(request: Request) -> str:
    """Extract caller IP. Prefer first X-Forwarded-For hop; fall back to socket."""
    fwd = request.headers.get("X-Forwarded-For") or request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return "0.0.0.0"  # pragma: no cover — TestClient always provides a client


def _signup_enabled() -> bool:
    """Master gate: ``SIGNUP_ENABLED`` env var must be set (any non-empty value)."""
    return bool(os.environ.get("SIGNUP_ENABLED"))


def _envelope(err: SignupError) -> dict[str, Any]:
    return {
        "code": err.code,
        "message_pt": err.message_pt,
        "message_en": err.message_en,
    }


_STORE_KEY_WARNING_FREE: dict[str, str] = {
    "code": "STORE_KEY_NOW",
    "message_pt": (
        "Salve esta API key AGORA. Ela NÃO será enviada novamente — o servidor "
        "descarta a versão plaintext após esta resposta. Se perder, o plano "
        "free não permite regenerar (1 key por email lifetime)."
    ),
    "message_en": (
        "Save this API key NOW. It will NOT be returned again — the server "
        "discards the plaintext after this response. If lost, the free plan "
        "does not allow regeneration (1 key per email lifetime)."
    ),
}

_STORE_KEY_WARNING_PAID: dict[str, str] = {
    "code": "STORE_KEY_NOW",
    "message_pt": (
        "Salve esta API key AGORA. Ela NÃO será enviada novamente nesta rota — "
        "o servidor descarta a versão plaintext. Uma cópia foi enviada por email; "
        "se perder ambas, refaça signup com novo pagamento usando o mesmo email."
    ),
    "message_en": (
        "Save this API key NOW. It will NOT be returned again on this route — "
        "the server discards the plaintext. A copy was also sent by email; if "
        "you lose both, sign up again with a new payment using the same email."
    ),
}


def _raise(err: SignupError) -> None:
    raise HTTPException(status_code=err.http_status, detail=_envelope(err))


def _require_signup_enabled() -> None:
    if not _signup_enabled():
        _raise(
            SignupError(
                code=ErrCode.SIGNUP_DISABLED,
                message_pt="Signup desabilitado neste servidor.",
                message_en="Signup is disabled on this server.",
                http_status=503,
            )
        )


def _require_paid_env() -> None:
    if not os.environ.get("ASAAS_API_KEY"):
        _raise(
            SignupError(
                code=ErrCode.SIGNUP_DISABLED,
                message_pt="Signup pago não configurado (ASAAS_API_KEY ausente).",
                message_en="Paid signup not configured (ASAAS_API_KEY missing).",
                http_status=503,
            )
        )


def _require_signup_repos(service: ServiceContext) -> tuple[Any, Any]:
    """Return (signup_repo, api_key_repo); 503 if not wired (shouldn't happen)."""
    if service.signup_repo is None or service.api_key_repo is None:
        _raise(
            SignupError(
                code=ErrCode.SIGNUP_DISABLED,
                message_pt="Signup desabilitado (repositórios não configurados).",
                message_en="Signup disabled (repositories not wired).",
                http_status=503,
            )
        )
    return service.signup_repo, service.api_key_repo


# ---------- Models ----------


class SignupStartBody(BaseModel):
    email: str
    plan: str
    cpf_cnpj: str | None = None


class SignupStatusBody(BaseModel):
    polling_token: str


# ---------- Endpoints ----------


@router.post("/signup/start")
async def post_signup_start(
    body: SignupStartBody,
    request: Request,
    service: Annotated[ServiceContext, Depends(get_service)],
) -> dict[str, Any]:
    _require_signup_enabled()
    signup_repo, api_key_repo = _require_signup_repos(service)

    try:
        email = signup_service.validate_email(body.email)
        plan = signup_service.validate_plan(body.plan)
        cpf_cnpj = signup_service.validate_cpf_cnpj(body.cpf_cnpj)
    except SignupError as e:
        _raise(e)
        return {}  # pragma: no cover — _raise always raises

    ip_address = _ip_address(request)

    try:
        signup_service.check_ip_rate_limits(signup_repo, ip_address)
    except SignupError as e:
        _raise(e)
        return {}  # pragma: no cover

    if plan == Plan.FREE:
        try:
            result = signup_service.start_free_signup(
                email=email,
                cpf_cnpj=cpf_cnpj,
                ip_address=ip_address,
                signup_repo=signup_repo,
                api_key_repo=api_key_repo,
            )
        except SignupError as e:
            _raise(e)
            return {}  # pragma: no cover
        return {
            "status": "delivered",
            "polling_token": result.polling_token,
            "api_key": result.api_key,
            "plan": str(result.plan),
            "queries_per_day": result.queries_per_day,
            "queries_per_month": result.queries_per_month,
            "warning": _STORE_KEY_WARNING_FREE,
        }

    # Paid flow — start_paid_signup does not itself raise SignupError; httpx
    # failures bubble as 500 (caller can retry).
    _require_paid_env()
    paid = await signup_service.start_paid_signup(
        email=email,
        cpf_cnpj=cpf_cnpj,
        ip_address=ip_address,
        plan=plan,
        signup_repo=signup_repo,
        asaas_create_customer=asaas_client.create_customer,
        asaas_create_checkout=asaas_client.create_checkout,
    )
    return {
        "status": "pending",
        "polling_token": paid.polling_token,
        "checkout_url": paid.checkout_url,
        "plan": str(paid.plan),
        "amount_brl": format(paid.amount_brl, ".2f"),
    }


@router.post("/signup/status")
def post_signup_status(
    body: SignupStatusBody,
    service: Annotated[ServiceContext, Depends(get_service)],
) -> dict[str, Any]:
    _require_signup_enabled()
    signup_repo, _ = _require_signup_repos(service)

    try:
        result = signup_service.poll_status(
            polling_token=body.polling_token,
            signup_repo=signup_repo,
        )
    except SignupError as e:
        _raise(e)
        return {}  # pragma: no cover

    if result.status == "pending":
        return {"status": "pending"}
    if result.status == "paid":
        # First poll after the webhook — return the plaintext key, mark delivered.
        return {
            "status": "paid",
            "api_key": result.api_key,
            "plan": str(result.plan) if result.plan else None,
            "warning": _STORE_KEY_WARNING_PAID,
        }
    if result.status == "delivered":
        return {"status": "delivered"}
    # cancelled (rare — refund happened before delivery)
    return {"status": result.status}
