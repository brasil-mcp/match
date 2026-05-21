"""Shared fixtures for adapter tests — mock repo + API key lookup + audit/opt-out."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from brasil_mcp_match.adapters.rest.dependencies import (
    ServiceContext,
    configure_service,
)
from brasil_mcp_match.core.auth.api_key import ApiKeyRecord, hash_key
from brasil_mcp_match.core.auth.plan import Plan
from brasil_mcp_match.core.lgpd.opt_out import OptOutRecord, fifteen_business_days_after
from brasil_mcp_match.core.repository.cnpj_repo import EmpresaRecord

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


class FakeRepo:
    def __init__(self) -> None:
        self.empresas: dict[str, EmpresaRecord] = {
            "33000167000101": _PETROBRAS,
            "11222333000181": _MEI_DEMO,
        }

    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        return self.empresas.get(cnpj_completo)


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


@pytest.fixture
def fake_repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def fake_audit() -> FakeAuditStore:
    return FakeAuditStore()


@pytest.fixture
def fake_opt_out() -> FakeOptOutStore:
    return FakeOptOutStore()


@pytest.fixture
def service_context(
    fake_repo: FakeRepo, fake_audit: FakeAuditStore, fake_opt_out: FakeOptOutStore
) -> ServiceContext:
    ctx = ServiceContext(
        repo=fake_repo,
        api_key_lookup=_key_lookup,
        audit_lookup=fake_audit.lookup,
        opt_out_register=fake_opt_out.register,
        is_opt_out_blocked=fake_opt_out.is_blocked,
        base_updated_at=date(2026, 5, 1),
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
