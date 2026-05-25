"""Signup domain models — SignupRequest record + plan-price table.

The pricing here mirrors :mod:`brasil_mcp_match_server.core.auth.plan` (which
only encodes quotas / rate limits). The BRL amounts are placeholders until
billing is finalized; change them here and in marketing copy together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from brasil_mcp_match_server.core.auth.plan import Plan

# BRL prices per plan, used to build the Asaas checkout amount.
# Free is 0 (no checkout). Update with billing team before launch.
PLAN_PRICES_BRL: dict[Plan, Decimal] = {
    Plan.FREE: Decimal("0.00"),
    Plan.STARTER: Decimal("49.00"),
    Plan.PRO: Decimal("199.00"),
    Plan.ENTERPRISE: Decimal("999.00"),
}


@dataclass(frozen=True, slots=True)
class SignupRequest:
    """In-memory mirror of a ``signup_request`` row.

    ``key_plaintext_once`` is set only between key generation and the
    moment the plaintext is delivered to the user. Persisted briefly on
    paid flows to bridge the webhook → status-poll handoff.
    """

    id: int
    polling_token: str
    email: str
    cpf_cnpj: str | None
    plan: Plan
    ip_address: str
    status: str  # 'pending' | 'paid' | 'delivered' | 'expired' | 'cancelled'
    asaas_customer_id: str | None
    asaas_payment_id: str | None
    api_key_id: int | None
    key_plaintext_once: str | None
    delivered_at: datetime | None
    created_at: datetime
    expires_at: datetime
