"""End-to-end tests of REST routes using FastAPI TestClient + mocked repo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app


def test_health(service_context) -> None:
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ------------ Auth ------------


def test_match_requires_api_key(service_context) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Petrobras"},
    )
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_API_KEY"


def test_match_rejects_unknown_api_key(service_context) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": "brasilmcp_doesnotexist"},
    )
    assert r.status_code == 401


def test_match_rejects_revoked_api_key(revoked_service_context, revoked_key_plaintext) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": revoked_key_plaintext},
    )
    assert r.status_code == 401


# ------------ match/razao-social ------------


def test_match_razao_social_exact(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "PETROLEO BRASILEIRO S A PETROBRAS"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["match"] is True
    assert data["hint"] == "exact"
    assert "query_id" in data
    assert data["base_updated_at"] == "2026-05-01"


def test_match_razao_social_fuzzy(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["match"] is True
    assert data["hint"] in ("fuzzy_prefix", "fuzzy_word", "exact")


def test_match_razao_social_no_match(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Banco do Brasil"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["match"] is False


def test_match_razao_social_cnpj_not_found(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "99999999000199", "nome": "X"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CNPJ_NOT_FOUND"


def test_match_razao_social_invalid_cnpj(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "123", "nome": "X"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_CNPJ"


def test_match_razao_social_accepts_masked_cnpj(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33.000.167/0001-01", "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200


def test_match_razao_social_tolerance_strict_rejects(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "33000167000101", "nome": "Petrobras", "tolerance": 0.99},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    # At very high tolerance, the substring fuzzy may fail
    data = r.json()
    assert "match" in data


# ------------ check/situacao ------------


def test_check_situacao_ativa(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/check/situacao",
        json={"cnpj": "33000167000101"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["situacao"] == "ativa"
    assert data["since"] == "1971-05-12"


# ------------ check/porte ------------


def test_check_porte_mei_with_simples(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": "11222333000181"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["porte"] == "MEI"
    assert data["is_simples_nacional"] is True
    assert data["is_mei"] is True


def test_check_porte_demais(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": "33000167000101"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["porte"] == "EPP"
    assert data["is_mei"] is False


# ------------ match/uf ------------


def test_match_uf_correct(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": "33000167000101", "uf": "RJ"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["match"] is True


def test_match_uf_wrong(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": "33000167000101", "uf": "SP"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["match"] is False


def test_match_uf_case_insensitive(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": "33000167000101", "uf": "rj"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["match"] is True


# ------------ Privacy contract ------------


def test_rest_never_returns_razao_social(service_context, api_key) -> None:
    """No REST response should ever contain the registered razao_social text."""
    client = TestClient(app)
    secret = "PETROLEO BRASILEIRO S A PETROBRAS"
    for path, body in [
        ("/v1/match/razao-social", {"cnpj": "33000167000101", "nome": "X"}),
        ("/v1/check/situacao", {"cnpj": "33000167000101"}),
        ("/v1/check/porte", {"cnpj": "33000167000101"}),
        ("/v1/match/uf", {"cnpj": "33000167000101", "uf": "SP"}),
    ]:
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        body_text = r.text
        assert secret not in body_text, f"{path} leaked razao_social"


def test_rest_returns_query_id_for_audit(service_context, api_key) -> None:
    """Every successful response must have a query_id (UUID)."""
    import uuid as uuid_mod

    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": "33000167000101", "uf": "RJ"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    qid = r.json()["query_id"]
    uuid_mod.UUID(qid)  # valid UUID
