"""Shared fixtures for adapter tests — mock repo + API key lookup."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from brasil_mcp_match.adapters.rest.dependencies import (
    ServiceContext,
    configure_service,
)
from brasil_mcp_match.core.auth.api_key import ApiKeyRecord, hash_key
from brasil_mcp_match.core.auth.plan import Plan
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
            "33000167000101": _PETROBRAS,  # full 14-digit
            "11222333000181": _MEI_DEMO,
        }

    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        return self.empresas.get(cnpj_completo)


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
def fake_repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def service_context(fake_repo: FakeRepo) -> ServiceContext:
    ctx = ServiceContext(
        repo=fake_repo,
        api_key_lookup=_key_lookup,
        base_updated_at=date(2026, 5, 1),
    )
    configure_service(ctx)
    return ctx


@pytest.fixture
def revoked_key_plaintext() -> str:
    return "brasilmcp_revoked0000000000000000"


@pytest.fixture
def revoked_service_context(fake_repo: FakeRepo, revoked_key_plaintext: str) -> ServiceContext:
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
        base_updated_at=date(2026, 5, 1),
    )
    configure_service(ctx)
    return ctx
