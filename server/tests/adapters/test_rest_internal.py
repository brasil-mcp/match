"""Tests for the internal sister-service router (``routes_internal.py``).

Covers:
- Happy path: valid X-Internal-Service-Token + known CNPJ → 200 with full record.
- Wrong token → 401 INTERNAL_AUTH_REQUIRED.
- Missing token header → 401 INTERNAL_AUTH_REQUIRED.
- Env not configured → 503 INTERNAL_DISABLED.
- Invalid CNPJ format → 422 INVALID_CNPJ (auth happens first; this exercises the
  request-validation path with a valid token).
- Unknown CNPJ → 404 CNPJ_NOT_FOUND.
- Optional/null fields serialize correctly.
- Decimal ``capital_social`` is preserved as a string (JSON precision).
- Dates serialize as ISO strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app
from brasil_mcp_match_server.core.repository.cnpj_repo import EmpresaRecord

_TOKEN = "test-internal-token-deadbeef"
_HEADERS = {"X-Internal-Service-Token": _TOKEN}


@pytest.fixture
def _internal_token_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("MATCH_INTERNAL_SERVICE_TOKEN", _TOKEN)
    return _TOKEN


@pytest.fixture
def _unset_internal_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATCH_INTERNAL_SERVICE_TOKEN", raising=False)


# ------------ Happy path ------------


def test_internal_empresa_happy_path(service_context, _internal_token_env) -> None:
    """Valid token + known CNPJ returns the full record with all fields."""
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33000167000101"},
        headers=_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["cnpj_completo"] == "33000167000101"
    assert data["cnpj_basico"] == "33000167"
    assert data["razao_social"] == "PETROLEO BRASILEIRO S A PETROBRAS"
    assert data["porte_empresa_codigo"] == "05"
    # Decimal preserved as string for precision
    assert data["capital_social"] == "205431960490.00"
    assert isinstance(data["capital_social"], str)
    assert data["situacao_cadastral_codigo"] == "2"
    assert data["data_situacao_cadastral"] == "1971-05-12"
    assert data["data_inicio_atividade"] == "1953-10-03"
    assert data["uf"] == "RJ"
    assert data["municipio_nome"] == "RIO DE JANEIRO"
    assert data["cep"] == "20031170"
    assert data["cnae_principal"] == "1921700"
    assert data["nome_fantasia"] is None
    assert data["opcao_simples"] == "N"
    assert data["opcao_mei"] == "N"


def test_internal_empresa_accepts_masked_cnpj(service_context, _internal_token_env) -> None:
    """Masked CNPJ is normalized like the public routes."""
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33.000.167/0001-01"},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["cnpj_completo"] == "33000167000101"


# ------------ Auth: token wrong / missing / unset ------------


def test_internal_empresa_wrong_token(service_context, _internal_token_env) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33000167000101"},
        headers={"X-Internal-Service-Token": "wrong-token"},
    )
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["code"] == "INTERNAL_AUTH_REQUIRED"
    assert "message_pt" in detail
    assert "message_en" in detail


def test_internal_empresa_missing_token_header(service_context, _internal_token_env) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33000167000101"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INTERNAL_AUTH_REQUIRED"


def test_internal_empresa_env_unset_returns_503(
    service_context, _unset_internal_token_env
) -> None:
    """If the operator hasn't provisioned the token, every request fails 503."""
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33000167000101"},
        headers=_HEADERS,
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "INTERNAL_DISABLED"


def test_internal_empresa_env_empty_string_treated_as_unset(
    service_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty string env value is treated like missing — never matches any token."""
    monkeypatch.setenv("MATCH_INTERNAL_SERVICE_TOKEN", "")
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "33000167000101"},
        headers=_HEADERS,
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "INTERNAL_DISABLED"


# ------------ Validation: bad CNPJ ------------


def test_internal_empresa_invalid_cnpj_format(service_context, _internal_token_env) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "123"},
        headers=_HEADERS,
    )
    # Reuses the public validator, which returns 422 with INVALID_CNPJ.
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_CNPJ"


def test_internal_empresa_cnpj_not_found(service_context, _internal_token_env) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "99999999000199"},
        headers=_HEADERS,
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "CNPJ_NOT_FOUND"


# ------------ Optional/null fields ------------


def test_internal_empresa_serializes_nulls(service_context, _internal_token_env, fake_repo) -> None:
    """A record with mostly-null optionals serializes correctly (no field omission)."""
    sparse = EmpresaRecord(
        cnpj_basico="12345678",
        razao_social="EMPRESA TESTE SPARSE LTDA",
        porte_empresa_codigo=None,
        capital_social=None,
        situacao_cadastral_codigo=None,
        data_situacao_cadastral=None,
        data_inicio_atividade=None,
        uf=None,
        municipio_nome=None,
        cep=None,
        cnae_principal=None,
        nome_fantasia=None,
        opcao_simples=None,
        opcao_mei=None,
    )
    fake_repo.empresas["12345678000190"] = sparse

    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "12345678000190"},
        headers=_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["cnpj_completo"] == "12345678000190"
    assert data["cnpj_basico"] == "12345678"
    assert data["razao_social"] == "EMPRESA TESTE SPARSE LTDA"
    # Every optional field present in the response with a None value.
    for k in (
        "nome_fantasia",
        "porte_empresa_codigo",
        "capital_social",
        "data_inicio_atividade",
        "situacao_cadastral_codigo",
        "data_situacao_cadastral",
        "opcao_simples",
        "opcao_mei",
        "uf",
        "municipio_nome",
        "cep",
        "cnae_principal",
    ):
        assert k in data, f"missing field {k}"
        assert data[k] is None, f"expected null for {k}, got {data[k]!r}"


def test_internal_empresa_capital_social_precision(
    service_context, _internal_token_env, fake_repo
) -> None:
    """Capital social with high precision survives the JSON round-trip via str."""
    weird = EmpresaRecord(
        cnpj_basico="22222222",
        razao_social="PRECISION TEST",
        porte_empresa_codigo="01",
        capital_social=Decimal("1234567890.123456789"),
        situacao_cadastral_codigo="2",
        data_situacao_cadastral=date(2024, 6, 1),
        data_inicio_atividade=date(2024, 6, 1),
        uf="SP",
        municipio_nome="SAO PAULO",
        cep="01310200",
        cnae_principal="6201500",
        nome_fantasia="PRECISION",
        opcao_simples="N",
        opcao_mei="N",
    )
    fake_repo.empresas["22222222000100"] = weird

    client = TestClient(app)
    r = client.post(
        "/v1/internal/empresa",
        json={"cnpj": "22222222000100"},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    data = r.json()
    # Decimal serialized via str() preserves the full literal.
    assert data["capital_social"] == "1234567890.123456789"
    assert data["nome_fantasia"] == "PRECISION"


# ------------ Direct dependency tests ------------


def test_require_internal_token_returns_token_on_match(_internal_token_env) -> None:
    """Direct unit test of the dependency happy path."""
    from brasil_mcp_match_server.adapters.rest.routes_internal import require_internal_token

    out = require_internal_token(x_internal_service_token=_TOKEN)
    assert out == _TOKEN
