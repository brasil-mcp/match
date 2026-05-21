"""Opt-out (LGPD Art. 18) — registro + bloqueio de queries.

Quando um titular pede remoção, gravamos `opt_out_request` com `effective_at`
= now() + 15 dias úteis. A partir de `effective_at`, queries sobre esse CNPJ
retornam OPT_OUT_RECORD em vez do match real.

A tabela `opt_out_request` (criada no schema.sql) é a fonte da verdade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class OptOutRecord:
    cnpj: str
    requested_at: datetime
    effective_at: datetime
    proof: dict[str, Any] | None


def fifteen_business_days_after(start: datetime) -> datetime:
    """Add 15 business days (skip Sat/Sun). No holiday calendar in v0.1.0 — just weekdays."""
    cursor = start
    remaining = 15
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return cursor


_SQL_INSERT = """
INSERT INTO opt_out_request (cnpj, requested_at, effective_at, proof)
VALUES (%s, %s, %s, %s)
ON CONFLICT (cnpj) DO UPDATE
    SET requested_at = excluded.requested_at,
        effective_at = excluded.effective_at,
        proof = excluded.proof
"""

_SQL_IS_BLOCKED = """
SELECT cnpj, requested_at, effective_at, proof
FROM opt_out_request
WHERE cnpj = %s AND effective_at <= %s
"""


def register(
    cnpj: str, proof: dict[str, Any] | None, cursor: Any, now: datetime | None = None
) -> OptOutRecord:
    """Register or upsert an opt-out request. Caller commits the cursor's tx."""
    import json

    now = now or datetime.now(UTC)
    effective = fifteen_business_days_after(now)
    cursor.execute(
        _SQL_INSERT,
        (cnpj, now, effective, json.dumps(proof) if proof else None),
    )
    return OptOutRecord(cnpj=cnpj, requested_at=now, effective_at=effective, proof=proof)


def is_blocked(cnpj: str, cursor: Any, now: datetime | None = None) -> bool:
    """True if there's an effective opt-out for this CNPJ."""
    now = now or datetime.now(UTC)
    cursor.execute(_SQL_IS_BLOCKED, (cnpj, now))
    row = cursor.fetchone()
    return row is not None
