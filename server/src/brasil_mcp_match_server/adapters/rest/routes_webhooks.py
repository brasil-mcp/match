"""Asaas webhook handler — verifies HMAC signature, applies idempotently.

Body is parsed *after* signature verification to avoid leaking parse errors
to unauthenticated callers. Always returns 200 once the signature checks out
(even on unknown payment ids) so Asaas doesn't retry indefinitely. The only
non-200 response is 401 on signature mismatch.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from brasil_mcp_match_server.adapters.rest.dependencies import (
    ServiceContext,
    get_service,
)
from brasil_mcp_match_server.core.billing import asaas as asaas_client
from brasil_mcp_match_server.core.notifications import brevo
from brasil_mcp_match_server.core.signup import service as signup_service

router = APIRouter(prefix="/v1")
_LOGGER = logging.getLogger(__name__)


def _signature_header(request: Request) -> str:
    """Asaas sends the signature under one of a handful of header names.

    We accept the common variants (vendor docs have shifted between
    ``asaas-access-token``, ``asaas-signature`` and ``X-Asaas-Signature``).
    """
    for name in ("asaas-signature", "X-Asaas-Signature", "asaas-access-token"):
        v = request.headers.get(name)
        if v:
            return v
    return ""


@router.post("/webhooks/asaas")
async def post_asaas_webhook(
    request: Request,
    service: Annotated[ServiceContext, Depends(get_service)],
) -> JSONResponse:
    raw = await request.body()
    signature = _signature_header(request)

    secret = os.environ.get("ASAAS_WEBHOOK_SECRET", "")
    if not asaas_client.verify_webhook_signature(raw, signature, secret=secret or None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_SIGNATURE",
                "message_pt": "Assinatura do webhook inválida.",
                "message_en": "Invalid webhook signature.",
            },
        )

    try:
        event = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        # Signature was valid but body wasn't JSON — log and 200 (don't retry).
        _LOGGER.warning("Asaas webhook: signature OK but body is not JSON")
        return JSONResponse(content={"status": "ignored"})

    if service.signup_repo is None or service.api_key_repo is None:
        # Signup not configured on this instance — accept + drop. (Shouldn't
        # happen in production; defensive.)
        _LOGGER.warning("Asaas webhook arrived but signup repos not wired")
        return JSONResponse(content={"status": "ignored"})

    event_type = event.get("event", "")
    payment = event.get("payment") or {}
    asaas_payment_id = payment.get("id")
    if not asaas_payment_id:
        return JSONResponse(content={"status": "ignored"})

    if event_type in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"):
        outcome = signup_service.apply_payment_confirmed(
            asaas_payment_id=asaas_payment_id,
            signup_repo=service.signup_repo,
            api_key_repo=service.api_key_repo,
        )
        if outcome.delivered_key and outcome.email and outcome.plan:
            # Fire-and-forget email — failures must not 5xx the webhook.
            try:
                await brevo.send_api_key_email(
                    outcome.email, outcome.delivered_key, str(outcome.plan)
                )
            except Exception as e:
                _LOGGER.error(
                    "Brevo delivery failed for %s: %s", outcome.email, e, exc_info=True
                )
        return JSONResponse(content={"status": "ok"})

    if event_type == "PAYMENT_REFUNDED":
        signup_service.apply_payment_refunded(
            asaas_payment_id=asaas_payment_id,
            signup_repo=service.signup_repo,
            api_key_repo=service.api_key_repo,
        )
        return JSONResponse(content={"status": "ok"})

    # Other events — acknowledged, no-op.
    return JSONResponse(content={"status": "ignored"})


def _unused() -> Any:  # pragma: no cover — keep the FastAPI re-export tidy
    return None
