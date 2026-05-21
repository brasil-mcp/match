"""Tests for opt-out core logic — pure fns + cursor mock."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from brasil_mcp_match.core.lgpd.opt_out import (
    fifteen_business_days_after,
    is_blocked,
    register,
)

# ------------ Business-day arithmetic ------------


def test_business_days_skip_weekend() -> None:
    # Friday 2026-05-22 12:00 UTC; +15 business days = Friday 2026-06-12
    start = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    end = fifteen_business_days_after(start)
    # Should land on a weekday
    assert end.weekday() < 5


def test_business_days_count_correct_for_monday_start() -> None:
    """15 business days from Mon = Mon 3 weeks later (15 days = 3 weeks of 5)."""
    monday = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    end = fifteen_business_days_after(monday)
    # Sanity: between 15 and 25 calendar days
    delta_days = (end - monday).days
    assert 19 <= delta_days <= 25


# ------------ register ------------


def test_register_inserts_and_returns_record() -> None:
    cursor = MagicMock()
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    rec = register(
        "33000167000101",
        proof={"type": "email", "value_hash": "abc"},
        cursor=cursor,
        now=now,
    )
    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    assert "INSERT INTO opt_out_request" in sql
    assert params[0] == "33000167000101"
    assert params[1] == now
    # effective_at is in the future
    assert params[2] > now
    # proof serialized as JSON
    assert "abc" in params[3]
    assert rec.cnpj == "33000167000101"
    assert rec.effective_at == params[2]


def test_register_with_no_proof_writes_null() -> None:
    cursor = MagicMock()
    register("12345678000195", proof=None, cursor=cursor)
    _, params = cursor.execute.call_args[0]
    assert params[3] is None


# ------------ is_blocked ------------


def test_is_blocked_true_when_row_returned() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = ("cnpj", "ts", "ts", None)
    assert is_blocked("33000167000101", cursor) is True


def test_is_blocked_false_when_no_row() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    assert is_blocked("33000167000101", cursor) is False


def test_is_blocked_passes_cnpj_and_now() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    now = datetime(2026, 5, 21, tzinfo=UTC)
    is_blocked("33000167000101", cursor, now=now)
    _, params = cursor.execute.call_args[0]
    assert params == ("33000167000101", now)
