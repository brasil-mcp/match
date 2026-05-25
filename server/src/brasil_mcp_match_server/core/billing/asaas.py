"""Asaas API client — customers, checkouts (payment links), webhook signature.

Ported from ``brasil_mcp_leads.core.billing.asaas`` with adjustments for the
self-service signup use case:

* Subscriptions/recurring billing are not used here — we only need a one-shot
  checkout URL the end user can pay via card/PIX in the browser.
* ``create_checkout`` uses the Asaas Payment Link API
  (``POST /v3/paymentLinks``), which returns a hosted URL the customer opens
  in the browser. The corresponding ``payment.id`` arrives on the
  ``PAYMENT_CONFIRMED`` webhook (the link generates a payment under the hood).
* Sandbox vs production switched via ``ASAAS_ENV`` env var (default sandbox).
* Webhook signature is HMAC-SHA256 with shared secret ``ASAAS_WEBHOOK_SECRET``.

All public functions accept an optional ``client: httpx.AsyncClient`` for
dependency injection in tests; if omitted the function builds and closes
its own client.
"""

from __future__ import annotations

import hmac
import os
from decimal import Decimal
from typing import Any

import httpx

ASAAS_PROD = "https://api.asaas.com/v3"
ASAAS_SANDBOX = "https://api-sandbox.asaas.com/v3"


def _base_url() -> str:
    """Return prod URL if ASAAS_ENV=production, otherwise sandbox."""
    if os.environ.get("ASAAS_ENV", "sandbox") == "production":
        return ASAAS_PROD
    return ASAAS_SANDBOX


def _headers() -> dict[str, str]:
    """Build request headers; raises if ASAAS_API_KEY is missing."""
    key = os.environ.get("ASAAS_API_KEY")
    if not key:
        raise RuntimeError("ASAAS_API_KEY not set")
    return {
        "access_token": key,
        "Content-Type": "application/json",
        "User-Agent": "brasil-mcp-match-server",
    }


async def create_customer(
    name: str,
    email: str,
    cpf_cnpj: str | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """POST /customers — returns the new customer id.

    ``cpf_cnpj`` is optional: Asaas accepts customer records without a tax id
    in sandbox, and downstream payment-link checkouts can prompt the payer to
    enter their CPF on the hosted page if absent here.
    """
    own = client is None
    cli = client or httpx.AsyncClient(timeout=15.0)
    try:
        body: dict[str, Any] = {"name": name, "email": email}
        if cpf_cnpj:
            body["cpfCnpj"] = cpf_cnpj
        r = await cli.post(
            f"{_base_url()}/customers",
            json=body,
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()["id"]
    finally:
        if own:
            await cli.aclose()


async def create_checkout(
    customer_id: str,
    value_brl: Decimal,
    description: str,
    *,
    billing_types: list[str] | None = None,
    expires_in_days: int = 1,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """Create a Payment Link for one-shot checkout.

    Uses ``POST /v3/paymentLinks`` (the customer-facing hosted checkout URL)
    rather than ``POST /v3/payments`` (which returns a bare invoice and would
    require manual UI assembly). The returned ``url`` is the link the end user
    opens in their browser to pay. ``payment_id`` here is the paymentLink id —
    Asaas emits a ``PAYMENT_CONFIRMED`` webhook with a ``payment.id`` once a
    transaction settles. We persist the payment-link id and match incoming
    webhook events on the inner ``payment`` object's ``id`` once a payment
    record is generated. (The signup table indexes both.)

    Args:
        customer_id: Asaas customer id (from create_customer).
        value_brl: amount in BRL.
        description: free-form description shown on the link page.
        billing_types: payment methods to allow on the link. Defaults to
            ``["CREDIT_CARD"]`` per the spec; pass ``["CREDIT_CARD", "PIX"]``
            for both, or leave None to accept the default.
        expires_in_days: how many days the link is valid (Asaas uses
            ``dueDateLimitDays``).

    Returns:
        ``{"url": "<hosted-checkout-url>", "payment_id": "<paymentLink id>"}``.
    """
    own = client is None
    cli = client or httpx.AsyncClient(timeout=15.0)
    types = billing_types or ["CREDIT_CARD"]
    # Payment Link API takes a single billingType; if caller wants multiple
    # methods, Asaas exposes "UNDEFINED" which lets the payer choose.
    billing_type = types[0] if len(types) == 1 else "UNDEFINED"
    try:
        r = await cli.post(
            f"{_base_url()}/paymentLinks",
            json={
                "name": description,
                "description": description,
                "value": float(value_brl),
                "billingType": billing_type,
                "chargeType": "DETACHED",  # one-shot, not recurring
                "dueDateLimitDays": expires_in_days,
                "customer": customer_id,
            },
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
        return {"url": data["url"], "payment_id": data["id"]}
    finally:
        if own:
            await cli.aclose()


def verify_webhook_signature(
    payload_raw: bytes, signature: str, secret: str | None = None
) -> bool:
    """HMAC-SHA256 signature check.

    Asaas sends a hex digest in a header (e.g. ``asaas-access-token``). The
    shared secret comes from ``ASAAS_WEBHOOK_SECRET`` env var if not passed
    explicitly. Returns False if no secret is configured — fail-closed for
    safety.
    """
    secret = secret if secret is not None else os.environ.get("ASAAS_WEBHOOK_SECRET", "")
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload_raw, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
