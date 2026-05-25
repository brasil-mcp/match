"""Tests for PostgresSignupRepo + PostgresApiKeyRepo using MagicMock cursors.

Mirrors the pattern in ``tests/core/repository/test_postgres_repo.py``: we
mock the psycopg connection + cursor so the test doesn't need a live DB
but still exercises every SQL path + dict_row → dataclass mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from brasil_mcp_match_server.core.auth.api_key_repo import PostgresApiKeyRepo
from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.signup.repo import PostgresSignupRepo


def _build_conn_returning(*rows) -> tuple[MagicMock, MagicMock]:
    """Build a fake conn whose cursor returns the given rows from fetchone() in order."""
    cur = MagicMock()
    cur.fetchone.side_effect = list(rows)
    cur_ctx = MagicMock()
    cur_ctx.__enter__.return_value = cur
    cur_ctx.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    return conn, cur


def _signup_row(**overrides) -> dict:
    base = {
        "id": 1,
        "polling_token": "11111111-2222-3333-4444-555555555555",
        "email": "a@b.com",
        "cpf_cnpj": None,
        "plan": "starter",
        "ip_address": "1.1.1.1",
        "status": "pending",
        "asaas_customer_id": "c",
        "asaas_payment_id": "p",
        "api_key_id": None,
        "key_plaintext_once": None,
        "delivered_at": None,
        "created_at": datetime(2026, 5, 25, tzinfo=UTC),
        "expires_at": datetime(2026, 5, 26, tzinfo=UTC),
    }
    base.update(overrides)
    return base


# ---------- PostgresSignupRepo.create ----------


def test_signup_create_inserts_and_returns_record() -> None:
    conn, cur = _build_conn_returning(_signup_row())
    repo = PostgresSignupRepo(conn)
    now = datetime(2026, 5, 25, tzinfo=UTC)
    rec = repo.create(
        polling_token="11111111-2222-3333-4444-555555555555",
        email="a@b.com",
        cpf_cnpj=None,
        plan=Plan.STARTER,
        ip_address="1.1.1.1",
        status="pending",
        asaas_customer_id="c",
        asaas_payment_id="p",
        api_key_id=None,
        key_plaintext_once=None,
        delivered_at=None,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    assert rec.email == "a@b.com"
    assert rec.plan == Plan.STARTER
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO signup_request" in sql
    assert "RETURNING" in sql


# ---------- find_by_token ----------


def test_signup_find_by_token_hit() -> None:
    conn, _ = _build_conn_returning(_signup_row(status="paid", api_key_id=42))
    repo = PostgresSignupRepo(conn)
    rec = repo.find_by_token("11111111-2222-3333-4444-555555555555")
    assert rec is not None
    assert rec.status == "paid"
    assert rec.api_key_id == 42


def test_signup_find_by_token_miss() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.find_by_token("nope") is None


# ---------- find_by_email ----------


def test_signup_find_by_email_hit() -> None:
    conn, cur = _build_conn_returning(_signup_row(email="x@y.com"))
    repo = PostgresSignupRepo(conn)
    rec = repo.find_by_email("x@y.com")
    assert rec is not None
    assert rec.email == "x@y.com"
    sql = cur.execute.call_args[0][0]
    assert "email = %s" in sql


def test_signup_find_by_email_miss() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.find_by_email("nope@x.com") is None


# ---------- count helpers ----------


def test_signup_count_free_signups_for_ip_since() -> None:
    conn, cur = _build_conn_returning({"n": 3})
    repo = PostgresSignupRepo(conn)
    out = repo.count_free_signups_for_ip_since(
        "1.1.1.1", datetime(2026, 5, 1, tzinfo=UTC)
    )
    assert out == 3
    sql = cur.execute.call_args[0][0]
    assert "plan = 'free'" in sql


def test_signup_count_free_signups_no_rows_returns_zero() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.count_free_signups_for_ip_since(
        "1.1.1.1", datetime(2026, 5, 1, tzinfo=UTC)
    ) == 0


def test_signup_count_signups_for_ip_since() -> None:
    conn, cur = _build_conn_returning({"n": 7})
    repo = PostgresSignupRepo(conn)
    out = repo.count_signups_for_ip_since(
        "1.1.1.1", datetime(2026, 5, 1, tzinfo=UTC)
    )
    assert out == 7
    sql = cur.execute.call_args[0][0]
    assert "ip_address = %s" in sql


def test_signup_count_signups_no_rows_returns_zero() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.count_signups_for_ip_since(
        "1.1.1.1", datetime(2026, 5, 1, tzinfo=UTC)
    ) == 0


# ---------- mark_paid ----------


def test_signup_mark_paid_returns_updated_row() -> None:
    conn, cur = _build_conn_returning(
        _signup_row(status="paid", api_key_id=99, key_plaintext_once="brasilmcp_x")
    )
    repo = PostgresSignupRepo(conn)
    rec = repo.mark_paid("tok", api_key_id=99, key_plaintext_once="brasilmcp_x")
    assert rec is not None
    assert rec.status == "paid"
    assert rec.api_key_id == 99
    sql = cur.execute.call_args[0][0]
    assert "status = 'paid'" in sql
    assert "status = 'pending'" in sql  # WHERE clause for idempotency


def test_signup_mark_paid_returns_none_when_no_pending_row() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.mark_paid("tok", api_key_id=1, key_plaintext_once="k") is None


# ---------- find_by_asaas_payment_id ----------


def test_signup_find_by_asaas_payment_id_hit() -> None:
    conn, cur = _build_conn_returning(_signup_row(asaas_payment_id="pay_x"))
    repo = PostgresSignupRepo(conn)
    rec = repo.find_by_asaas_payment_id("pay_x")
    assert rec is not None
    assert rec.asaas_payment_id == "pay_x"
    sql = cur.execute.call_args[0][0]
    assert "asaas_payment_id = %s" in sql


def test_signup_find_by_asaas_payment_id_miss() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.find_by_asaas_payment_id("nope") is None


# ---------- mark_delivered ----------


def test_signup_mark_delivered_returns_updated() -> None:
    conn, cur = _build_conn_returning(
        _signup_row(status="delivered", key_plaintext_once=None,
                    delivered_at=datetime(2026, 5, 25, tzinfo=UTC))
    )
    repo = PostgresSignupRepo(conn)
    rec = repo.mark_delivered("tok")
    assert rec is not None
    assert rec.status == "delivered"
    sql = cur.execute.call_args[0][0]
    assert "key_plaintext_once = NULL" in sql


def test_signup_mark_delivered_returns_none_when_not_paid() -> None:
    conn, _ = _build_conn_returning(None)
    repo = PostgresSignupRepo(conn)
    assert repo.mark_delivered("tok") is None


# ---------- mark_expired / mark_cancelled ----------


def test_signup_mark_expired_executes_update() -> None:
    conn, cur = _build_conn_returning()
    repo = PostgresSignupRepo(conn)
    repo.mark_expired("tok")
    sql = cur.execute.call_args[0][0]
    assert "status = 'expired'" in sql
    assert "status = 'pending'" in sql


def test_signup_mark_cancelled_executes_update() -> None:
    conn, cur = _build_conn_returning()
    repo = PostgresSignupRepo(conn)
    repo.mark_cancelled("tok")
    sql = cur.execute.call_args[0][0]
    assert "status = 'cancelled'" in sql


# ---------- PostgresApiKeyRepo ----------


def test_api_key_repo_insert_returns_record() -> None:
    conn, cur = _build_conn_returning(
        {
            "id": 7,
            "key_hash": "abc",
            "plan": "starter",
            "customer_email": "x@y.com",
            "revoked_at": None,
        }
    )
    repo = PostgresApiKeyRepo(conn)
    rec = repo.insert(key_hash="abc", plan=Plan.STARTER, customer_email="x@y.com")
    assert rec.id == 7
    assert rec.plan == Plan.STARTER
    assert rec.is_revoked is False
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO api_key" in sql
    # quota_per_month from PLAN config passed through
    args = cur.execute.call_args[0][1]
    assert args[1] == "starter"
    assert args[3] == 500  # starter monthly_quota


def test_api_key_repo_insert_marks_revoked_when_revoked_at_present() -> None:
    """Defensive: if a freshly inserted row somehow has revoked_at, is_revoked is True."""
    conn, _ = _build_conn_returning(
        {
            "id": 7, "key_hash": "abc", "plan": "free",
            "customer_email": "x@y.com",
            "revoked_at": datetime(2026, 5, 25, tzinfo=UTC),
        }
    )
    repo = PostgresApiKeyRepo(conn)
    rec = repo.insert(key_hash="abc", plan=Plan.FREE, customer_email="x@y.com")
    assert rec.is_revoked is True


def test_api_key_repo_revoke_executes_update() -> None:
    conn, cur = _build_conn_returning()
    repo = PostgresApiKeyRepo(conn)
    repo.revoke(42)
    sql = cur.execute.call_args[0][0]
    assert "UPDATE api_key" in sql
    assert "revoked_at = NOW()" in sql
    assert cur.execute.call_args[0][1] == (42,)
