"""Tests for /v1/webhooks/asaas — signature + idempotent event handling."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app
from brasil_mcp_match_server.core.auth.plan import Plan


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setenv("ASAAS_WEBHOOK_SECRET", "shh")
    yield


def _sign(body: bytes, secret: str = "shh") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client: TestClient, payload: dict, *, header_name: str = "asaas-signature") -> TestClient.response_class:  # type: ignore[name-defined]
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)
    return client.post(
        "/v1/webhooks/asaas",
        content=body,
        headers={header_name: sig, "Content-Type": "application/json"},
    )


# ---------- Signature ----------


def test_webhook_invalid_signature_returns_401(service_context):
    client = TestClient(app)
    body = json.dumps({"event": "PAYMENT_CONFIRMED"}).encode()
    r = client.post(
        "/v1/webhooks/asaas",
        content=body,
        headers={"asaas-signature": "deadbeef"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_SIGNATURE"


def test_webhook_missing_signature_returns_401(service_context):
    client = TestClient(app)
    r = client.post("/v1/webhooks/asaas", content=b'{"event":"x"}')
    assert r.status_code == 401


def test_webhook_accepts_x_asaas_signature_header(monkeypatch, service_context):
    """Vendor docs have used multiple header names; accept the common variants."""
    client = TestClient(app)
    r = _post(client, {"event": "OTHER"}, header_name="X-Asaas-Signature")
    assert r.status_code == 200


def test_webhook_accepts_asaas_access_token_header(service_context):
    client = TestClient(app)
    r = _post(client, {"event": "OTHER"}, header_name="asaas-access-token")
    assert r.status_code == 200


def test_webhook_signature_valid_but_body_not_json_returns_200(service_context):
    client = TestClient(app)
    body = b"not json{"
    sig = _sign(body)
    r = client.post(
        "/v1/webhooks/asaas",
        content=body,
        headers={"asaas-signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


# ---------- Event routing ----------


def test_webhook_unknown_event_type_ignored(service_context):
    client = TestClient(app)
    r = _post(client, {"event": "ACCOUNT_UPDATED", "payment": {"id": "p1"}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_event_with_no_payment_id_ignored(service_context):
    client = TestClient(app)
    r = _post(client, {"event": "PAYMENT_CONFIRMED"})  # no payment block
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_unknown_payment_id_returns_200_noop(service_context):
    """Don't reveal whether an id exists — just 200 + noop."""
    client = TestClient(app)
    r = _post(
        client,
        {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_unknown"}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------- PAYMENT_CONFIRMED happy path ----------


def test_webhook_payment_confirmed_mints_key_and_sends_email(
    monkeypatch, service_context, fake_signup_repo, fake_api_key_repo,
):
    # Seed a pending signup.
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="t1", email="paid@example.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="pay_match", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )

    fake_send = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_webhooks.brevo.send_api_key_email",
        fake_send,
    )

    client = TestClient(app)
    r = _post(
        client,
        {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_match"}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    row = fake_signup_repo.rows["t1"]
    assert row.status == "paid"
    assert row.api_key_id is not None
    assert row.key_plaintext_once is not None
    assert row.key_plaintext_once.startswith("brasilmcp_")
    # Brevo called with the right args.
    fake_send.assert_awaited_once()
    args = fake_send.await_args
    assert args is not None
    assert args.args[0] == "paid@example.com"
    assert args.args[1] == row.key_plaintext_once
    assert args.args[2] == "starter"


def test_webhook_payment_received_alias_handled(
    monkeypatch, service_context, fake_signup_repo,
):
    """PAYMENT_RECEIVED is treated the same as PAYMENT_CONFIRMED."""
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="t1", email="x@y.com", cpf_cnpj=None,
        plan=Plan.PRO, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="pay_recv", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    fake_send = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_webhooks.brevo.send_api_key_email",
        fake_send,
    )

    client = TestClient(app)
    r = _post(
        client, {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_recv"}},
    )
    assert r.status_code == 200
    assert fake_signup_repo.rows["t1"].status == "paid"


def test_webhook_payment_confirmed_idempotent_on_replay(
    monkeypatch, service_context, fake_signup_repo, fake_api_key_repo,
):
    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="t1", email="paid@x.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="pay_rep", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    fake_send = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_webhooks.brevo.send_api_key_email",
        fake_send,
    )

    client = TestClient(app)
    r1 = _post(
        client, {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_rep"}}
    )
    r2 = _post(
        client, {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_rep"}}
    )
    assert r1.status_code == 200 and r2.status_code == 200
    # Exactly one api_key minted.
    assert len(fake_api_key_repo.rows) == 1
    # Brevo called only once (replay was a no-op).
    fake_send.assert_awaited_once()


def test_webhook_brevo_failure_still_returns_200(
    monkeypatch, service_context, fake_signup_repo, caplog,
):
    """If Brevo blows up, log + still 200 (don't trigger Asaas retries)."""
    import httpx

    now = datetime.now(UTC)
    fake_signup_repo.create(
        polling_token="t1", email="paid@x.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="pay_boom", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )

    async def boom(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "https://api.brevo.com"),
            response=httpx.Response(500),
        )

    monkeypatch.setattr(
        "brasil_mcp_match_server.adapters.rest.routes_webhooks.brevo.send_api_key_email",
        boom,
    )

    client = TestClient(app)
    import logging

    with caplog.at_level(logging.ERROR):
        r = _post(
            client,
            {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_boom"}},
        )
    assert r.status_code == 200
    assert fake_signup_repo.rows["t1"].status == "paid"
    assert any("Brevo delivery failed" in rec.message for rec in caplog.records)


# ---------- PAYMENT_REFUNDED ----------


def test_webhook_payment_refunded_revokes_key(
    service_context, fake_signup_repo, fake_api_key_repo,
):
    # Existing paid signup with key.
    now = datetime.now(UTC)
    api_key_id = fake_api_key_repo.insert(
        key_hash="h", plan=Plan.STARTER, customer_email="x@y.com"
    ).id
    fake_signup_repo.create(
        polling_token="t1", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="paid",
        asaas_customer_id="c", asaas_payment_id="pay_ref", api_key_id=api_key_id,
        key_plaintext_once="brasilmcp_x", delivered_at=None,
        created_at=now, expires_at=now + timedelta(hours=24),
    )
    client = TestClient(app)
    r = _post(
        client, {"event": "PAYMENT_REFUNDED", "payment": {"id": "pay_ref"}}
    )
    assert r.status_code == 200
    assert fake_signup_repo.rows["t1"].status == "cancelled"
    assert fake_api_key_repo.rows[api_key_id].is_revoked is True


# ---------- Defensive: no repos wired ----------


def test_webhook_without_repos_logs_and_200(monkeypatch, fake_repo, fake_audit, fake_opt_out):
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
    r = _post(client, {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_x"}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
