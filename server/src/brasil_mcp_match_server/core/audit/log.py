"""Audit log — append-only, LGPD-compliant.

Cada chamada de tool registra:
- query_id (UUID4, retornado ao cliente)
- timestamp
- api_key_hash (NUNCA a key em si)
- tool name
- input_hash (sha256 do payload, NUNCA o payload)
- cnpj_basico (8 chars, pra opt-out reverse-lookup)
- response_summary (apenas {match: bool, situacao: enum, ...} — nunca a razao_social ou outros campos da RF)
- base_updated_at
- ip_address
- latency_ms

Retention mínima: 6 meses (configurável).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEntry:
    query_id: str
    api_key_hash: str
    tool: str
    input_hash: str
    cnpj_basico: str
    response_summary: dict[str, Any]
    base_updated_at: date
    ip_address: str | None
    latency_ms: int | None


def hash_input(payload: dict[str, Any]) -> str:
    """Deterministic sha256 of a JSON-serializable payload (sorted keys)."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_query_id() -> str:
    return str(uuid.uuid4())


def build_entry(
    *,
    api_key_hash: str,
    tool: str,
    payload: dict[str, Any],
    cnpj_basico: str,
    response_summary: dict[str, Any],
    base_updated_at: date,
    ip_address: str | None = None,
    latency_ms: int | None = None,
    query_id: str | None = None,
) -> AuditEntry:
    """Construct a fresh AuditEntry — caller persists via repository."""
    return AuditEntry(
        query_id=query_id or new_query_id(),
        api_key_hash=api_key_hash,
        tool=tool,
        input_hash=hash_input(payload),
        cnpj_basico=cnpj_basico,
        response_summary=response_summary,
        base_updated_at=base_updated_at,
        ip_address=ip_address,
        latency_ms=latency_ms,
    )


_SQL_INSERT = """
INSERT INTO audit_log (
    query_id, api_key_hash, tool, input_hash, cnpj_basico,
    response_summary, base_updated_at, ip_address, latency_ms
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def insert(entry: AuditEntry, cursor: Any) -> None:
    """Persist an AuditEntry via a psycopg cursor. Caller manages commit."""
    cursor.execute(
        _SQL_INSERT,
        (
            entry.query_id,
            entry.api_key_hash,
            entry.tool,
            entry.input_hash,
            entry.cnpj_basico,
            json.dumps(entry.response_summary, default=str),
            entry.base_updated_at,
            entry.ip_address,
            entry.latency_ms,
        ),
    )


def older_than(cutoff: datetime, cursor: Any) -> int:
    """Delete audit entries older than cutoff. Returns rows deleted.

    Used by retention job (default 6 months). Idempotent.
    """
    cursor.execute("DELETE FROM audit_log WHERE created_at < %s", (cutoff,))
    return cursor.rowcount or 0
