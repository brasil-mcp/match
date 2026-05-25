"""Shared fixtures for adapter tests — mock repo + API key lookup + audit/opt-out."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from brasil_mcp_match_server.adapters.rest.dependencies import (
    ServiceContext,
    configure_service,
)
from brasil_mcp_match_server.core.auth.api_key import ApiKeyRecord, hash_key
from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.lgpd.opt_out import OptOutRecord, fifteen_business_days_after
from brasil_mcp_match_server.core.repository.cnpj_repo import EmpresaRecord, SocioRecord
from brasil_mcp_match_server.core.signup.models import SignupRequest

_PETROBRAS = EmpresaRecord(
    cnpj_basico="33000167",
    razao_social="PETROLEO BRASILEIRO S A PETROBRAS",
    porte_empresa_codigo="05",
    capital_social=Decimal("205431960490.00"),
    situacao_cadastral_codigo="2",
    data_situacao_cadastral=date(1971, 5, 12),
    data_inicio_atividade=date(1953, 10, 3),
    uf="RJ",
    municipio_nome="RIO DE JANEIRO",
    cep="20031170",
    cnae_principal="1921700",
    nome_fantasia=None,
    opcao_simples="N",
    opcao_mei="N",
)

_MEI_DEMO = EmpresaRecord(
    cnpj_basico="11222333",
    razao_social="JOAO DA SILVA 12345678900",
    porte_empresa_codigo="01",
    capital_social=Decimal("1000.00"),
    situacao_cadastral_codigo="2",
    data_situacao_cadastral=date(2020, 1, 15),
    data_inicio_atividade=date(2020, 1, 15),
    uf="SP",
    municipio_nome="SAO PAULO",
    cep="01310200",
    cnae_principal="9609299",
    nome_fantasia=None,
    opcao_simples="S",
    opcao_mei="S",
)


_PETROBRAS_SOCIOS: list[SocioRecord] = [
    SocioRecord(
        nome_socio="JOSE PINHEIRO SILVA",
        cnpj_cpf_socio="***123456**",
        identificador_socio=1,
        qualificacao_socio=10,
    ),
    SocioRecord(
        nome_socio="MARIA APARECIDA SOUZA",
        cnpj_cpf_socio="***987654**",
        identificador_socio=1,
        qualificacao_socio=22,
    ),
    SocioRecord(
        nome_socio="HOLDING ENERGETICA S.A.",
        cnpj_cpf_socio="11444777000161",
        identificador_socio=2,
        qualificacao_socio=22,
    ),
    SocioRecord(
        nome_socio="JOHN SMITH",
        cnpj_cpf_socio=None,
        identificador_socio=3,
        qualificacao_socio=10,
    ),
]


class FakeRepo:
    def __init__(self) -> None:
        self.empresas: dict[str, EmpresaRecord] = {
            "33000167000101": _PETROBRAS,
            "11222333000181": _MEI_DEMO,
        }
        # Keyed by cnpj_basico (first 8 digits of cnpj_completo).
        self.socios: dict[str, list[SocioRecord]] = {
            "33000167": list(_PETROBRAS_SOCIOS),
            "11222333": [],  # MEI demo has no sócios
        }
        # Known qualificacao codes (subset of real ref_qualificacao_socio).
        self.qualificacao_codes: set[int] = {5, 10, 16, 22, 49, 65}

    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        return self.empresas.get(cnpj_completo)

    def find_socios_by_cnpj_basico(self, cnpj_basico: str) -> list[SocioRecord]:
        return list(self.socios.get(cnpj_basico, []))

    def qualificacao_codigo_exists(self, codigo: int) -> bool:
        return codigo in self.qualificacao_codes


class FakeAuditStore:
    """In-memory replacement for the audit_log table.

    Test code can pre-populate entries; the lookup respects api_key_hash
    boundary (same RBAC behavior as the SQL query)."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}

    def add(self, query_id: str, api_key_hash: str, **fields: Any) -> None:
        self.entries[query_id] = {
            "query_id": query_id,
            "api_key_hash": api_key_hash,
            **fields,
        }

    def lookup(self, query_id: str, api_key_hash: str) -> dict[str, Any] | None:
        entry = self.entries.get(query_id)
        if entry is None or entry["api_key_hash"] != api_key_hash:
            return None
        # Strip the api_key_hash from the returned shape — caller shouldn't see other keys' hashes
        out = {k: v for k, v in entry.items() if k != "api_key_hash"}
        return out


class FakeOptOutStore:
    def __init__(self) -> None:
        self.records: dict[str, OptOutRecord] = {}

    def register(self, cnpj: str, proof: dict[str, Any] | None) -> OptOutRecord:
        now = datetime.now(UTC)
        rec = OptOutRecord(
            cnpj=cnpj,
            requested_at=now,
            effective_at=fifteen_business_days_after(now),
            proof=proof,
        )
        self.records[cnpj] = rec
        return rec

    def force_block(self, cnpj: str) -> None:
        """Test helper: bypass the 15-day delay."""
        past = datetime(2020, 1, 1, tzinfo=UTC)
        self.records[cnpj] = OptOutRecord(
            cnpj=cnpj, requested_at=past, effective_at=past, proof=None
        )

    def is_blocked(self, cnpj: str) -> bool:
        rec = self.records.get(cnpj)
        if rec is None:
            return False
        return rec.effective_at <= datetime.now(UTC)


_API_KEY_PLAINTEXT = "brasilmcp_testkey1234567890abcdef"
_API_KEY_HASH = hash_key(_API_KEY_PLAINTEXT)
_API_KEY_RECORD = ApiKeyRecord(
    id=1,
    key_hash=_API_KEY_HASH,
    plan=Plan.STARTER,
    customer_email="test@example.com",
    is_revoked=False,
)


def _key_lookup(h: str) -> ApiKeyRecord | None:
    return _API_KEY_RECORD if h == _API_KEY_HASH else None


@pytest.fixture
def api_key() -> str:
    return _API_KEY_PLAINTEXT


@pytest.fixture
def api_key_hash() -> str:
    return _API_KEY_HASH


class FakeSignupRepo:
    """In-memory SignupRepo for adapter tests."""

    def __init__(self) -> None:
        self.rows: dict[str, SignupRequest] = {}
        self._id_counter = itertools.count(start=1)

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
            id=next(self._id_counter),
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
        for rec in sorted(self.rows.values(), key=lambda r: r.created_at):
            if rec.email == email:
                return rec
        return None

    def count_free_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r.ip_address == ip_address and r.plan == Plan.FREE and r.created_at >= since
        )

    def count_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        return sum(
            1 for r in self.rows.values()
            if r.ip_address == ip_address and r.created_at >= since
        )

    def find_by_asaas_payment_id(self, asaas_payment_id: str) -> SignupRequest | None:
        for r in self.rows.values():
            if r.asaas_payment_id == asaas_payment_id:
                return r
        return None

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
            rec,
            status="delivered",
            delivered_at=datetime.now(UTC),
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
    """In-memory ApiKeyRepo for adapter tests."""

    def __init__(self) -> None:
        self.rows: dict[int, ApiKeyRecord] = {}
        self._id_counter = itertools.count(start=100)

    def insert(
        self,
        *,
        key_hash: str,
        plan: Plan,
        customer_email: str | None,
    ) -> ApiKeyRecord:
        new_id = next(self._id_counter)
        rec = ApiKeyRecord(
            id=new_id,
            key_hash=key_hash,
            plan=plan,
            customer_email=customer_email,
            is_revoked=False,
        )
        self.rows[new_id] = rec
        return rec

    def revoke(self, api_key_id: int) -> None:
        rec = self.rows.get(api_key_id)
        if rec is not None:
            self.rows[api_key_id] = replace(rec, is_revoked=True)


@pytest.fixture
def fake_repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def fake_signup_repo() -> FakeSignupRepo:
    return FakeSignupRepo()


@pytest.fixture
def fake_api_key_repo() -> FakeApiKeyRepo:
    return FakeApiKeyRepo()


@pytest.fixture
def fake_audit() -> FakeAuditStore:
    return FakeAuditStore()


@pytest.fixture
def fake_opt_out() -> FakeOptOutStore:
    return FakeOptOutStore()


@pytest.fixture
def service_context(
    fake_repo: FakeRepo,
    fake_audit: FakeAuditStore,
    fake_opt_out: FakeOptOutStore,
    fake_signup_repo: FakeSignupRepo,
    fake_api_key_repo: FakeApiKeyRepo,
) -> ServiceContext:
    ctx = ServiceContext(
        repo=fake_repo,
        api_key_lookup=_key_lookup,
        audit_lookup=fake_audit.lookup,
        opt_out_register=fake_opt_out.register,
        is_opt_out_blocked=fake_opt_out.is_blocked,
        base_updated_at=date(2026, 5, 1),
        signup_repo=fake_signup_repo,
        api_key_repo=fake_api_key_repo,
    )
    configure_service(ctx)
    return ctx


@pytest.fixture
def revoked_key_plaintext() -> str:
    return "brasilmcp_revoked0000000000000000"


@pytest.fixture
def revoked_service_context(
    fake_repo: FakeRepo,
    fake_audit: FakeAuditStore,
    fake_opt_out: FakeOptOutStore,
    revoked_key_plaintext: str,
) -> ServiceContext:
    rev_hash = hash_key(revoked_key_plaintext)
    revoked = replace(_API_KEY_RECORD, key_hash=rev_hash, is_revoked=True)

    def lookup(h: str) -> ApiKeyRecord | None:
        if h == _API_KEY_HASH:
            return _API_KEY_RECORD
        if h == rev_hash:
            return revoked
        return None

    ctx = ServiceContext(
        repo=fake_repo,
        api_key_lookup=lookup,
        audit_lookup=fake_audit.lookup,
        opt_out_register=fake_opt_out.register,
        is_opt_out_blocked=fake_opt_out.is_blocked,
        base_updated_at=date(2026, 5, 1),
    )
    configure_service(ctx)
    return ctx
