"""End-to-end tests for /v1/signup/* using FastAPI TestClient + mocked repos."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app
from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.signup.models import PLAN_PRICES_BRL


@pytest.fixture(autouse=True)
def _enable_signup(monkeypatch):
    monkeypatch.setenv("SIGNUP_ENABLED", "1")


# ---------- Gate: SIGNUP_ENABLED ----------


def test_signup_disabled_returns_503(monkeypatch, service_context):
    monkeypatch.delenv("SIGNUP_ENABLED", raising=False)
    client = TestClient(app)
    r = client.post("/v1/signup/start", json={"email": "x@y.com", "plan": "free"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "SIGNUP_DISABLED"


def test_signup_status_disabled_returns_503(monkeypatch, service_context):
    monkeypatch.delenv("SIGNUP_ENABLED", raising=False)
    client = TestClient(app)
    r = client.post("/v1/signup/status", json={"polling_token": "x"})
    assert r.status_code == 503


# ---------- Validation errors ----------


def test_signup_invalid_email_returns_400(service_context):
    client = TestClient(app)
    r = client.post("/v1/signup/start", json={"email": "no-at", "plan": "free"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_EMAIL"


def test_signup_invalid_plan_returns_400(service_context):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start", json={"email": "a@b.com", "plan": "platinum"}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_PLAN"


def test_signup_invalid_cpf_cnpj_returns_400(service_context):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start",
        json={"email": "a@b.com", "plan": "starter", "cpf_cnpj": "123"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_CPF_CNPJ"


# ---------- Free flow ----------


def test_signup_free_happy_path(service_context, fake_signup_repo, fake_api_key_repo):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start", json={"email": "freebie@example.com", "plan": "free"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "delivered"
    assert data["plan"] == "free"
    assert data["api_key"].startswith("brasilmcp_")
    assert data["queries_per_day"] == 10
    assert data["queries_per_month"] == 50
    assert data["warning"]["code"] == "STORE_KEY_NOW"
    assert "lifetime" in data["warning"]["message_en"]
    assert "polling_token" in data
    # Persisted.
    row = fake_signup_repo.find_by_token(data["polling_token"])
    assert row is not None
    assert row.status == "delivered"
    # api_key row inserted.
    assert len(fake_api_key_repo.rows) == 1


def test_signup_free_blocks_repeat_email(service_context):
    client = TestClient(app)
    r1 = client.post("/v1/signup/start", json={"email": "dup@x.com", "plan": "free"})
    assert r1.status_code == 200
    r2 = client.post("/v1/signup/start", json={"email": "dup@x.com", "plan": "free"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "EMAIL_ALREADY_HAS_KEY"


def test_signup_free_blocks_repeat_ip(service_context):
    client = TestClient(app)
    client.post("/v1/signup/start", json={"email": "a@x.com", "plan": "free"})
    r = client.post("/v1/signup/start", json={"email": "b@x.com", "plan": "free"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "IP_RATE_LIMITED"


def test_signup_free_with_cpf_persists_normalized(service_context, fake_signup_repo):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start",
        json={"email": "z@x.com", "plan": "free", "cpf_cnpj": "123.456.789-01"},
    )
    assert r.status_code == 200
    row = fake_signup_repo.find_by_token(r.json()["polling_token"])
    assert row is not None
    assert row.cpf_cnpj == "12345678901"


def test_signup_free_extracts_xff_ip(service_context, fake_signup_repo):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start",
        json={"email": "xff@x.com", "plan": "free"},
        headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
    )
    assert r.status_code == 200
    row = fake_signup_repo.find_by_token(r.json()["polling_token"])
    assert row is not None
    assert row.ip_address == "203.0.113.5"


def test_signup_xff_with_empty_first_hop_falls_back_to_client(
    service_context, fake_signup_repo,
):
    """XFF header present but first hop empty (e.g. ``,1.2.3.4``) → fall back."""
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start",
        json={"email": "fallback@x.com", "plan": "free"},
        headers={"X-Forwarded-For": ", 10.0.0.1"},
    )
    assert r.status_code == 200
    row = fake_signup_repo.find_by_token(r.json()["polling_token"])
    assert row is not None
    # TestClient default client.host is testclient/127.0.0.1
    assert row.ip_address in {"testclient", "127.0.0.1"}


# ---------- Paid flow ----------


def test_signup_paid_503_when_asaas_env_missing(monkeypatch, service_context):
    monkeypatch.delenv("ASAAS_API_KEY", raising=False)
    client = TestClient(app)
    r = client.post(
        "/v1/signup/start", json={"email": "paid@x.com", "plan": "starter"}
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "SIGNUP_DISABLED"


def test_signup_paid_happy_path(monkeypatch, service_context, fake_signup_repo):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    # Patch the asaas_client funcs the route imports.
    fake_customer = AsyncMock(return_value="cus_x")
    fake_checkout = AsyncMock(
        return_value={"url": "https://checkout.asaas.com/abc", "payment_id": "pay_x"}
    )
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_signup.asaas_client.create_customer",
        fake_customer,
    )
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_signup.asaas_client.create_checkout",
        fake_checkout,
    )

    client = TestClient(app)
    r = client.post(
        "/v1/signup/start",
        json={
            "email": "paid@example.com",
            "plan": "starter",
            "cpf_cnpj": "12345678901",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "pending"
    assert data["checkout_url"] == "https://checkout.asaas.com/abc"
    assert data["plan"] == "starter"
    assert data["amount_brl"] == format(PLAN_PRICES_BRL[Plan.STARTER], ".2f")
    # asaas_create_customer called with (email, email, cpf_cnpj)
    fake_customer.assert_awaited_once_with(
        "paid@example.com", "paid@example.com", "12345678901"
    )
    fake_checkout.assert_awaited_once()
    # Persisted as pending with asaas_payment_id.
    row = fake_signup_repo.find_by_token(data["polling_token"])
    assert row is not None
    assert row.status == "pending"
    assert row.asaas_customer_id == "cus_x"
    assert row.asaas_payment_id == "pay_x"


# ---------- Status flow ----------


def test_status_unknown_token_404(service_context):
    client = TestClient(app)
    r = client.post(
        "/v1/signup/status", json={"polling_token": "00000000-nope"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SIGNUP_NOT_FOUND"


def test_status_pending(service_context, fake_signup_repo):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="tok-pending", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    client = TestClient(app)
    r = client.post("/v1/signup/status", json={"polling_token": "tok-pending"})
    assert r.status_code == 200
    assert r.json() == {"status": "pending"}


def test_status_paid_returns_key_and_marks_delivered(
    service_context, fake_signup_repo,
):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="tok-paid", email="x@y.com", cpf_cnpj=None,
        plan=Plan.PRO, ip_address="1.1.1.1", status="paid",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=99,
        key_plaintext_once="brasilmcp_paidkey", delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    client = TestClient(app)
    r = client.post("/v1/signup/status", json={"polling_token": "tok-paid"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paid"
    assert body["api_key"] == "brasilmcp_paidkey"
    assert body["plan"] == "pro"
    assert body["warning"]["code"] == "STORE_KEY_NOW"
    assert "email" in body["warning"]["message_en"].lower()
    # Second call returns delivered without the key (and without warning).
    r2 = client.post("/v1/signup/status", json={"polling_token": "tok-paid"})
    assert r2.status_code == 200
    assert r2.json() == {"status": "delivered"}
    assert "warning" not in r2.json()


def test_status_delivered_no_key(service_context, fake_signup_repo):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="tok-delivered", email="x@y.com", cpf_cnpj=None,
        plan=Plan.FREE, ip_address="1.1.1.1", status="delivered",
        asaas_customer_id=None, asaas_payment_id=None, api_key_id=42,
        key_plaintext_once=None, delivered_at=now,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    client = TestClient(app)
    r = client.post("/v1/signup/status", json={"polling_token": "tok-delivered"})
    assert r.status_code == 200
    assert r.json() == {"status": "delivered"}


def test_status_expired_returns_410(service_context, fake_signup_repo):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    # Pending but past expires_at — first poll transitions to expired + 410.
    fake_signup_repo.create(
        polling_token="tok-expired", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now - timedelta(days=2), expires_at=now - timedelta(days=1),
    )
    client = TestClient(app)
    r = client.post("/v1/signup/status", json={"polling_token": "tok-expired"})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "SIGNUP_EXPIRED"
    # And the row is now expired.
    assert fake_signup_repo.rows["tok-expired"].status == "expired"


def test_status_cancelled(service_context, fake_signup_repo):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="tok-cancelled", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="cancelled",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    client = TestClient(app)
    r = client.post(
        "/v1/signup/status", json={"polling_token": "tok-cancelled"}
    )
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}


# ---------- IP rate-limit (5/h total) ----------


def test_signup_rate_limited_after_5_signups(monkeypatch, service_context):
    monkeypatch.setenv("ASAAS_API_KEY", "sk-test")

    fake_customer = AsyncMock(return_value="cus_x")
    fake_checkout = AsyncMock(
        return_value={"url": "https://x", "payment_id": "pay_x"}
    )
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_signup.asaas_client.create_customer",
        fake_customer,
    )
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_signup.asaas_client.create_checkout",
        fake_checkout,
    )

    client = TestClient(app)
    # 5 paid signups from same IP (TestClient sends 127.0.0.1).
    for i in range(5):
        r = client.post(
            "/v1/signup/start",
            json={"email": f"u{i}@x.com", "plan": "starter"},
        )
        assert r.status_code == 200, r.text
    # 6th — blocked.
    r = client.post(
        "/v1/signup/start", json={"email": "u6@x.com", "plan": "starter"}
    )
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "IP_RATE_LIMITED"


# ---------- Defensive: no repos wired ----------


def test_signup_without_repos_503(monkeypatch, fake_repo, fake_audit, fake_opt_out):
    """If the ServiceContext was built without signup repos, return 503 cleanly."""
    from datetime import date

    from brasil_mcp_match_server.adapters.rest.dependencies import (
        ServiceContext,
        configure_service,
    )

    def _key_lookup(h):
        return None

    ctx = ServiceContext(
        repo=fake_repo,
        api_key_lookup=_key_lookup,
        audit_lookup=fake_audit.lookup,
        opt_out_register=fake_opt_out.register,
        is_opt_out_blocked=fake_opt_out.is_blocked,
        base_updated_at=date(2026, 5, 1),
        signup_repo=None,
        api_key_repo=None,
    )
    configure_service(ctx)
    client = TestClient(app)
    r = client.post("/v1/signup/start", json={"email": "a@b.com", "plan": "free"})
    assert r.status_code == 503
    r2 = client.post("/v1/signup/status", json={"polling_token": "x"})
    assert r2.status_code == 503
