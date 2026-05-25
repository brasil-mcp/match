"""Unit tests for the signup service — pure logic, fake repos."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from brasil_mcp_match_server.core.auth.api_key import ApiKeyRecord
from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.signup import service
from brasil_mcp_match_server.core.signup.models import PLAN_PRICES_BRL, SignupRequest
from brasil_mcp_match_server.core.signup.service import (
    FREE_QUERIES_PER_DAY,
    PAID_EXPIRY,
    ErrCode,
    SignupError,
)


class FakeSignupRepo:
    def __init__(self) -> None:
        self.rows: dict[str, SignupRequest] = {}
        self._id = itertools.count(start=1)

    def create(
        self,
        *,
        polling_token: str,
        email: str,
        cpf_cnpj: str | None,
        plan: Plan,
        ip_address: str,
        status: str,
        asaas_customer_id: str | None,
        asaas_payment_id: str | None,
        api_key_id: int | None,
        key_plaintext_once: str | None,
        delivered_at: datetime | None,
        created_at: datetime,
        expires_at: datetime,
    ) -> SignupRequest:
        rec = SignupRequest(
            id=next(self._id),
            polling_token=polling_token,
            email=email,
            cpf_cnpj=cpf_cnpj,
            plan=plan,
            ip_address=ip_address,
            status=status,
            asaas_customer_id=asaas_customer_id,
            asaas_payment_id=asaas_payment_id,
            api_key_id=api_key_id,
            key_plaintext_once=key_plaintext_once,
            delivered_at=delivered_at,
            created_at=created_at,
            expires_at=expires_at,
        )
        self.rows[polling_token] = rec
        return rec

    def find_by_token(self, polling_token: str) -> SignupRequest | None:
        return self.rows.get(polling_token)

    def find_by_email(self, email: str) -> SignupRequest | None:
        for r in sorted(self.rows.values(), key=lambda x: x.created_at):
            if r.email == email:
                return r
        return None

    def count_free_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        return sum(
            1 for r in self.rows.values()
            if r.ip_address == ip_address and r.plan == Plan.FREE and r.created_at >= since
        )

    def count_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        return sum(
            1 for r in self.rows.values()
            if r.ip_address == ip_address and r.created_at >= since
        )

    def find_by_asaas_payment_id(self, asaas_payment_id: str) -> SignupRequest | None:
        return next(
            (r for r in self.rows.values() if r.asaas_payment_id == asaas_payment_id),
            None,
        )

    def mark_paid(
        self, polling_token: str, *, api_key_id: int, key_plaintext_once: str
    ) -> SignupRequest | None:
        rec = self.rows.get(polling_token)
        if rec is None or rec.status != "pending":
            return None
        updated = replace(
            rec,
            status="paid",
            api_key_id=api_key_id,
            key_plaintext_once=key_plaintext_once,
        )
        self.rows[polling_token] = updated
        return updated

    def mark_delivered(self, polling_token: str) -> SignupRequest | None:
        rec = self.rows.get(polling_token)
        if rec is None or rec.status != "paid":
            return None
        updated = replace(
            rec, status="delivered", delivered_at=datetime.now(UTC),
            key_plaintext_once=None,
        )
        self.rows[polling_token] = updated
        return updated

    def mark_expired(self, polling_token: str) -> None:
        rec = self.rows.get(polling_token)
        if rec is not None and rec.status == "pending":
            self.rows[polling_token] = replace(rec, status="expired")

    def mark_cancelled(self, polling_token: str) -> None:
        rec = self.rows.get(polling_token)
        if rec is not None:
            self.rows[polling_token] = replace(rec, status="cancelled")


class FakeApiKeyRepo:
    def __init__(self) -> None:
        self.rows: dict[int, ApiKeyRecord] = {}
        self._id = itertools.count(start=100)

    def insert(
        self,
        *,
        key_hash: str,
        plan: Plan,
        customer_email: str | None,
    ) -> ApiKeyRecord:
        new_id = next(self._id)
        rec = ApiKeyRecord(
            id=new_id, key_hash=key_hash, plan=plan,
            customer_email=customer_email, is_revoked=False,
        )
        self.rows[new_id] = rec
        return rec

    def revoke(self, api_key_id: int) -> None:
        rec = self.rows.get(api_key_id)
        if rec is not None:
            self.rows[api_key_id] = replace(rec, is_revoked=True)


# ---------- Validators ----------


@pytest.mark.parametrize("good", ["a@b.co", "RICK@X.COM", "  user@example.org  "])
def test_validate_email_accepts_well_formed(good):
    out = service.validate_email(good)
    assert "@" in out and out == out.lower().strip()


@pytest.mark.parametrize("bad", [None, "", "no-at", "no@dot", "spaces in@x.com"])
def test_validate_email_rejects_malformed(bad):
    with pytest.raises(SignupError) as exc:
        service.validate_email(bad)
    assert exc.value.code == ErrCode.INVALID_EMAIL
    assert exc.value.http_status == 400


@pytest.mark.parametrize("good,expected", [
    ("free", Plan.FREE),
    ("STARTER", Plan.STARTER),
    ("  pro  ", Plan.PRO),
    ("enterprise", Plan.ENTERPRISE),
])
def test_validate_plan_accepts_known(good, expected):
    assert service.validate_plan(good) == expected


@pytest.mark.parametrize("bad", [None, "", "platinum", "freemium"])
def test_validate_plan_rejects_unknown(bad):
    with pytest.raises(SignupError) as exc:
        service.validate_plan(bad)
    assert exc.value.code == ErrCode.INVALID_PLAN
    assert exc.value.http_status == 400


def test_validate_cpf_cnpj_accepts_11_digits():
    assert service.validate_cpf_cnpj("123.456.789-01") == "12345678901"


def test_validate_cpf_cnpj_accepts_14_digits():
    assert service.validate_cpf_cnpj("11.222.333/0001-81") == "11222333000181"


def test_validate_cpf_cnpj_accepts_none():
    assert service.validate_cpf_cnpj(None) is None


def test_validate_cpf_cnpj_accepts_empty_string():
    assert service.validate_cpf_cnpj("") is None


def test_validate_cpf_cnpj_rejects_short():
    with pytest.raises(SignupError) as exc:
        service.validate_cpf_cnpj("123")
    assert exc.value.code == ErrCode.INVALID_CPF_CNPJ
    assert exc.value.http_status == 400


def test_validate_cpf_cnpj_rejects_wrong_length():
    with pytest.raises(SignupError):
        service.validate_cpf_cnpj("123456789012")  # 12 digits — neither


def test_normalize_cpf_cnpj_none_input():
    assert service.normalize_cpf_cnpj(None) is None


def test_normalize_cpf_cnpj_empty_input():
    assert service.normalize_cpf_cnpj("") is None


def test_normalize_cpf_cnpj_pure_garbage_returns_none():
    """All non-digits collapse to empty → None."""
    assert service.normalize_cpf_cnpj("abc---") is None


# ---------- check_ip_rate_limits ----------


def test_check_ip_rate_limits_ok_under_threshold():
    repo = FakeSignupRepo()
    # 4 prior signups in last hour — below the 5/h cap.
    now = datetime.now(UTC)
    for i in range(4):
        repo.create(
            polling_token=f"t{i}", email=f"u{i}@x.com", cpf_cnpj=None,
            plan=Plan.FREE, ip_address="1.1.1.1", status="delivered",
            asaas_customer_id=None, asaas_payment_id=None, api_key_id=None,
            key_plaintext_once=None, delivered_at=now,
            created_at=now - timedelta(minutes=10),
            expires_at=now + timedelta(hours=24),
        )
    # No raise.
    service.check_ip_rate_limits(repo, "1.1.1.1", now=now)


def test_check_ip_rate_limits_blocks_at_threshold():
    repo = FakeSignupRepo()
    now = datetime.now(UTC)
    for i in range(5):
        repo.create(
            polling_token=f"t{i}", email=f"u{i}@x.com", cpf_cnpj=None,
            plan=Plan.FREE, ip_address="1.1.1.1", status="delivered",
            asaas_customer_id=None, asaas_payment_id=None, api_key_id=None,
            key_plaintext_once=None, delivered_at=now,
            created_at=now - timedelta(minutes=10),
            expires_at=now + timedelta(hours=24),
        )
    with pytest.raises(SignupError) as exc:
        service.check_ip_rate_limits(repo, "1.1.1.1", now=now)
    assert exc.value.code == ErrCode.IP_RATE_LIMITED
    assert exc.value.http_status == 429


def test_check_ip_rate_limits_old_rows_dont_count():
    repo = FakeSignupRepo()
    now = datetime.now(UTC)
    # 5 signups but >1h old — shouldn't trigger.
    for i in range(5):
        repo.create(
            polling_token=f"t{i}", email=f"u{i}@x.com", cpf_cnpj=None,
            plan=Plan.FREE, ip_address="1.1.1.1", status="delivered",
            asaas_customer_id=None, asaas_payment_id=None, api_key_id=None,
            key_plaintext_once=None, delivered_at=now,
            created_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=24),
        )
    service.check_ip_rate_limits(repo, "1.1.1.1", now=now)


# ---------- start_free_signup ----------


def test_start_free_signup_happy_path():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)

    result = service.start_free_signup(
        email="ricardo@example.com",
        cpf_cnpj=None,
        ip_address="9.9.9.9",
        signup_repo=signup_repo,
        api_key_repo=ak_repo,
        now=now,
    )
    assert result.plan == Plan.FREE
    assert result.api_key.startswith("brasilmcp_")
    assert result.queries_per_day == FREE_QUERIES_PER_DAY
    assert result.queries_per_month == 50
    # Persisted as delivered (free returns key inline).
    row = signup_repo.find_by_token(result.polling_token)
    assert row is not None
    assert row.status == "delivered"
    assert row.delivered_at is not None
    assert row.email == "ricardo@example.com"
    # API key row inserted.
    assert len(ak_repo.rows) == 1
    inserted = next(iter(ak_repo.rows.values()))
    assert inserted.plan == Plan.FREE
    assert inserted.customer_email == "ricardo@example.com"


def test_start_free_signup_blocks_repeat_email():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    service.start_free_signup(
        email="dup@example.com", cpf_cnpj=None, ip_address="1.1.1.1",
        signup_repo=signup_repo, api_key_repo=ak_repo, now=now,
    )
    with pytest.raises(SignupError) as exc:
        service.start_free_signup(
            email="dup@example.com", cpf_cnpj=None, ip_address="2.2.2.2",
            signup_repo=signup_repo, api_key_repo=ak_repo,
            now=now + timedelta(seconds=1),
        )
    assert exc.value.code == ErrCode.EMAIL_ALREADY_HAS_KEY
    assert exc.value.http_status == 409
    # No new api_key insert.
    assert len(ak_repo.rows) == 1


def test_start_free_signup_blocks_repeat_ip_within_30d():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    service.start_free_signup(
        email="a@example.com", cpf_cnpj=None, ip_address="3.3.3.3",
        signup_repo=signup_repo, api_key_repo=ak_repo, now=now,
    )
    with pytest.raises(SignupError) as exc:
        service.start_free_signup(
            email="b@example.com", cpf_cnpj=None, ip_address="3.3.3.3",
            signup_repo=signup_repo, api_key_repo=ak_repo,
            now=now + timedelta(days=29),
        )
    assert exc.value.code == ErrCode.IP_RATE_LIMITED
    assert exc.value.http_status == 429


def test_start_free_signup_allows_same_ip_after_30d():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    service.start_free_signup(
        email="a@example.com", cpf_cnpj=None, ip_address="3.3.3.3",
        signup_repo=signup_repo, api_key_repo=ak_repo, now=now,
    )
    # 31 days later — should be allowed.
    result = service.start_free_signup(
        email="b@example.com", cpf_cnpj=None, ip_address="3.3.3.3",
        signup_repo=signup_repo, api_key_repo=ak_repo,
        now=now + timedelta(days=31),
    )
    assert result.api_key.startswith("brasilmcp_")
    assert len(signup_repo.rows) == 2


# ---------- start_paid_signup ----------


async def test_start_paid_signup_happy_path():
    signup_repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)

    calls = {"customer": [], "checkout": []}

    async def fake_create_customer(name, email, cpf):
        calls["customer"].append((name, email, cpf))
        return "cus_test"

    async def fake_create_checkout(customer_id, value, description):
        calls["checkout"].append((customer_id, value, description))
        return {"url": "https://checkout.asaas.com/abc", "payment_id": "pay_abc"}

    paid = await service.start_paid_signup(
        email="paid@example.com",
        cpf_cnpj="12345678901",
        ip_address="5.5.5.5",
        plan=Plan.STARTER,
        signup_repo=signup_repo,
        asaas_create_customer=fake_create_customer,
        asaas_create_checkout=fake_create_checkout,
        now=now,
    )
    assert paid.checkout_url == "https://checkout.asaas.com/abc"
    assert paid.plan == Plan.STARTER
    assert paid.amount_brl == PLAN_PRICES_BRL[Plan.STARTER]
    assert calls["customer"] == [("paid@example.com", "paid@example.com", "12345678901")]
    assert calls["checkout"][0][0] == "cus_test"
    row = signup_repo.find_by_token(paid.polling_token)
    assert row is not None
    assert row.status == "pending"
    assert row.asaas_payment_id == "pay_abc"
    assert row.expires_at == now + PAID_EXPIRY


# ---------- poll_status ----------


def test_poll_status_unknown_token():
    repo = FakeSignupRepo()
    with pytest.raises(SignupError) as exc:
        service.poll_status(polling_token="nope", signup_repo=repo)
    assert exc.value.code == ErrCode.SIGNUP_NOT_FOUND
    assert exc.value.http_status == 404


def test_poll_status_pending():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    out = service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert out.status == "pending"
    assert out.api_key is None


def test_poll_status_pending_expired_transitions_to_410():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now - timedelta(days=2), expires_at=now - timedelta(days=1),
    )
    with pytest.raises(SignupError) as exc:
        service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert exc.value.code == ErrCode.SIGNUP_EXPIRED
    assert exc.value.http_status == 410
    # And the row is now expired.
    assert repo.rows["tok"].status == "expired"


def test_poll_status_expired_status_raises():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="expired",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now - timedelta(days=2), expires_at=now - timedelta(days=1),
    )
    with pytest.raises(SignupError) as exc:
        service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert exc.value.code == ErrCode.SIGNUP_EXPIRED


def test_poll_status_paid_returns_key_and_marks_delivered():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="paid",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=42,
        key_plaintext_once="brasilmcp_xxx", delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    out = service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert out.status == "paid"
    assert out.api_key == "brasilmcp_xxx"
    assert out.plan == Plan.STARTER
    # Subsequent call returns 'delivered' without key.
    out2 = service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert out2.status == "delivered"
    assert out2.api_key is None


def test_poll_status_delivered_returns_no_key():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.PRO, ip_address="1.1.1.1", status="delivered",
        asaas_customer_id=None, asaas_payment_id=None, api_key_id=42,
        key_plaintext_once=None, delivered_at=now,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    out = service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert out.status == "delivered"
    assert out.api_key is None
    assert out.plan == Plan.PRO


def test_poll_status_cancelled():
    repo = FakeSignupRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="cancelled",
        asaas_customer_id="c", asaas_payment_id="p", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    out = service.poll_status(polling_token="tok", signup_repo=repo, now=now)
    assert out.status == "cancelled"
    assert out.api_key is None


# ---------- apply_payment_confirmed ----------


def test_apply_payment_confirmed_mints_key_and_flips_status():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    signup_repo.create(
        polling_token="tok", email="paid@x.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c1", asaas_payment_id="pay_1", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    outcome = service.apply_payment_confirmed(
        asaas_payment_id="pay_1",
        signup_repo=signup_repo,
        api_key_repo=ak_repo,
    )
    assert outcome.delivered_key is not None
    assert outcome.delivered_key.startswith("brasilmcp_")
    assert outcome.email == "paid@x.com"
    assert outcome.plan == Plan.STARTER
    row = signup_repo.rows["tok"]
    assert row.status == "paid"
    assert row.api_key_id is not None
    assert row.key_plaintext_once == outcome.delivered_key


def test_apply_payment_confirmed_idempotent_on_replay():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    signup_repo.create(
        polling_token="tok", email="paid@x.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c1", asaas_payment_id="pay_1", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    out1 = service.apply_payment_confirmed(
        asaas_payment_id="pay_1", signup_repo=signup_repo, api_key_repo=ak_repo,
    )
    out2 = service.apply_payment_confirmed(
        asaas_payment_id="pay_1", signup_repo=signup_repo, api_key_repo=ak_repo,
    )
    assert out1.delivered_key is not None
    assert out2.delivered_key is None  # No duplicate key minted.
    assert len(ak_repo.rows) == 1


def test_apply_payment_confirmed_unknown_payment_noop():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    outcome = service.apply_payment_confirmed(
        asaas_payment_id="pay_unknown",
        signup_repo=signup_repo,
        api_key_repo=ak_repo,
    )
    assert outcome.delivered_key is None
    assert outcome.email is None
    assert len(ak_repo.rows) == 0


# ---------- apply_payment_refunded ----------


def test_apply_payment_refunded_revokes_key():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    # Pre-existing paid row with a key.
    api_key_id = ak_repo.insert(
        key_hash="h", plan=Plan.STARTER, customer_email="paid@x.com",
    ).id
    signup_repo.create(
        polling_token="tok", email="paid@x.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="paid",
        asaas_customer_id="c1", asaas_payment_id="pay_1", api_key_id=api_key_id,
        key_plaintext_once="brasilmcp_x", delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    ok = service.apply_payment_refunded(
        asaas_payment_id="pay_1", signup_repo=signup_repo, api_key_repo=ak_repo,
    )
    assert ok is True
    assert ak_repo.rows[api_key_id].is_revoked is True
    assert signup_repo.rows["tok"].status == "cancelled"


def test_apply_payment_refunded_unknown_payment():
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    ok = service.apply_payment_refunded(
        asaas_payment_id="missing",
        signup_repo=signup_repo, api_key_repo=ak_repo,
    )
    assert ok is False


def test_apply_payment_refunded_pending_row_skips_revoke():
    """Refund before the key was minted — no api_key to revoke, but still mark cancelled."""
    signup_repo = FakeSignupRepo()
    ak_repo = FakeApiKeyRepo()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    signup_repo.create(
        polling_token="tok", email="x@y.com", cpf_cnpj=None,
        plan=Plan.STARTER, ip_address="1.1.1.1", status="pending",
        asaas_customer_id="c", asaas_payment_id="pay_1", api_key_id=None,
        key_plaintext_once=None, delivered_at=None,
        created_at=now, expires_at=now + PAID_EXPIRY,
    )
    ok = service.apply_payment_refunded(
        asaas_payment_id="pay_1",
        signup_repo=signup_repo, api_key_repo=ak_repo,
    )
    assert ok is True
    assert signup_repo.rows["tok"].status == "cancelled"
    assert len(ak_repo.rows) == 0  # No key existed, none revoked.
