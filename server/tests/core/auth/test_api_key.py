"""Tests for API key auth — pure fns + mocked lookup."""

from __future__ import annotations

from datetime import date

import pytest

from brasil_mcp_match_server.core.auth.api_key import (
    ApiKeyRecord,
    authenticate,
    check_quota,
    current_period,
    generate_api_key,
    hash_key,
)
from brasil_mcp_match_server.core.auth.plan import Plan, get_plan_config

# ------------ Generation ------------


def test_generate_returns_prefixed_plaintext_and_hash() -> None:
    plaintext, h = generate_api_key()
    assert plaintext.startswith("brasilmcp_")
    assert len(plaintext) == len("brasilmcp_") + 32  # hex chars
    assert len(h) == 64  # sha256 hex
    assert hash_key(plaintext) == h


def test_generated_keys_are_unique() -> None:
    a, _ = generate_api_key()
    b, _ = generate_api_key()
    assert a != b


def test_hash_is_deterministic() -> None:
    assert hash_key("brasilmcp_deadbeef") == hash_key("brasilmcp_deadbeef")


# ------------ Authentication ------------


def _mk_record(plan: Plan = Plan.STARTER, is_revoked: bool = False) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=1,
        key_hash="x" * 64,
        plan=plan,
        customer_email="test@example.com",
        is_revoked=is_revoked,
    )


def test_authenticate_missing_key() -> None:
    r = authenticate(None, lookup=lambda h: None)
    assert not r.authenticated
    assert r.error is not None
    assert str(r.error.code) == "INVALID_API_KEY"


def test_authenticate_malformed_prefix() -> None:
    r = authenticate("not_a_valid_key", lookup=lambda h: None)
    assert not r.authenticated
    assert r.error is not None


def test_authenticate_unknown_key() -> None:
    r = authenticate("brasilmcp_unknown", lookup=lambda h: None)
    assert not r.authenticated
    assert r.error is not None


def test_authenticate_revoked() -> None:
    revoked = _mk_record(is_revoked=True)
    r = authenticate("brasilmcp_abc", lookup=lambda h: revoked)
    assert not r.authenticated
    assert r.error is not None
    assert "revogada" in r.error.message_pt.lower()


def test_authenticate_success() -> None:
    record = _mk_record()
    r = authenticate("brasilmcp_abc", lookup=lambda h: record)
    assert r.authenticated
    assert r.error is None
    assert r.record == record


def test_authenticate_passes_correct_hash_to_lookup() -> None:
    captured: list[str] = []
    authenticate("brasilmcp_xyz", lookup=lambda h: captured.append(h) or None)  # type: ignore[func-returns-value]
    assert captured[0] == hash_key("brasilmcp_xyz")


# ------------ Quota ------------


def test_quota_unlimited_for_enterprise() -> None:
    cfg = get_plan_config(Plan.ENTERPRISE)
    r = check_quota(used_in_period=100_000, plan_config=cfg)
    assert r.allowed is True
    assert r.remaining == -1


def test_quota_within_limit() -> None:
    cfg = get_plan_config(Plan.STARTER)  # 500/mo
    r = check_quota(used_in_period=100, plan_config=cfg)
    assert r.allowed is True
    assert r.remaining == 400


def test_quota_exhausted() -> None:
    cfg = get_plan_config(Plan.FREE)  # 50/mo
    r = check_quota(used_in_period=50, plan_config=cfg)
    assert r.allowed is False
    assert r.remaining == 0
    assert r.error is not None
    assert str(r.error.code) == "QUOTA_EXCEEDED"


def test_quota_exhausted_with_over_usage() -> None:
    cfg = get_plan_config(Plan.FREE)
    r = check_quota(used_in_period=99, plan_config=cfg)
    assert r.allowed is False
    assert r.remaining == 0


# ------------ Period ------------


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 1, 1), "2026-01"),
        (date(2026, 12, 31), "2026-12"),
        (date(2030, 7, 15), "2030-07"),
    ],
)
def test_current_period(today: date, expected: str) -> None:
    assert current_period(today) == expected


def test_current_period_default_uses_today() -> None:
    # Just verify it returns YYYY-MM shape
    p = current_period()
    assert len(p) == 7
    assert p[4] == "-"


def test_api_key_record_plan_config_property() -> None:
    """ApiKeyRecord.plan_config delegates to get_plan_config (line 31)."""
    record = _mk_record(plan=Plan.STARTER)
    cfg = record.plan_config
    assert cfg == get_plan_config(Plan.STARTER)


def test_api_key_record_plan_config_free() -> None:
    record = _mk_record(plan=Plan.FREE)
    assert record.plan_config == get_plan_config(Plan.FREE)
