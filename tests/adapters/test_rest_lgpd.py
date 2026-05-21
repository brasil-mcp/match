"""Tests for LGPD endpoints — opt-out + audit retrieve + opt-out blocking."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from brasil_mcp_match.adapters.rest.app import app

# ------------ POST /v1/opt-out/{cnpj} ------------


def test_opt_out_registers_request(service_context, fake_opt_out) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/opt-out/33000167000101",
        json={
            "proof_type": "email",
            "proof_value": "owner@example.com",
            "contact_email": "owner@example.com",
        },
    )
    assert r.status_code == 202
    data = r.json()
    assert data["cnpj"] == "33000167000101"
    assert "requested_at" in data
    assert "effective_at" in data
    # Should be in the store
    assert "33000167000101" in fake_opt_out.records


def test_opt_out_proof_hashed_not_plaintext(service_context, fake_opt_out) -> None:
    """The proof_value plaintext must never be stored — only a sha256 hash."""
    client = TestClient(app)
    plaintext = "verysecret-doc-12345"
    client.post(
        "/v1/opt-out/33000167000101",
        json={"proof_type": "document_id", "proof_value": plaintext},
    )
    record = fake_opt_out.records["33000167000101"]
    assert record.proof is not None
    proof: Any = record.proof
    assert plaintext not in str(proof)
    # value_hash should be 64-char hex
    assert len(proof["value_hash"]) == 64


def test_opt_out_does_not_require_api_key(service_context) -> None:
    """Titular pode pedir opt-out sem ter conta — não exige API key."""
    client = TestClient(app)
    r = client.post(
        "/v1/opt-out/33000167000101",
        json={"proof_type": "email", "proof_value": "owner@example.com"},
    )
    assert r.status_code == 202


def test_opt_out_rejects_invalid_cnpj(service_context) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/opt-out/123",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    assert r.status_code == 422


# ------------ Opt-out blocks subsequent match calls ------------


def test_opt_out_blocks_match_endpoints(service_context, fake_opt_out, api_key) -> None:
    """After opt-out is effective, match/check endpoints return 410 GONE."""
    fake_opt_out.force_block("33000167000101")
    client = TestClient(app)
    for path, body in [
        ("/v1/match/razao-social", {"cnpj": "33000167000101", "nome": "X"}),
        ("/v1/check/situacao", {"cnpj": "33000167000101"}),
        ("/v1/check/porte", {"cnpj": "33000167000101"}),
        ("/v1/match/uf", {"cnpj": "33000167000101", "uf": "RJ"}),
    ]:
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        assert r.status_code == 410, f"{path} should be blocked"
        assert r.json()["detail"]["code"] == "OPT_OUT_RECORD"


def test_non_opt_out_cnpj_still_works(service_context, fake_opt_out, api_key) -> None:
    """Opt-out is per-CNPJ; other CNPJs continue working."""
    fake_opt_out.force_block("33000167000101")
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": "11222333000181", "uf": "SP"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200


# ------------ GET /v1/audit/{query_id} ------------


def test_audit_retrieve_existing(service_context, fake_audit, api_key, api_key_hash) -> None:
    fake_audit.add(
        query_id="aaaa-bbbb-cccc-dddd",
        api_key_hash=api_key_hash,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
        created_at=date(2026, 5, 21).isoformat(),
    )
    client = TestClient(app)
    r = client.get(
        "/v1/audit/aaaa-bbbb-cccc-dddd",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["query_id"] == "aaaa-bbbb-cccc-dddd"
    assert data["tool"] == "match_uf"
    # api_key_hash should NOT be exposed
    assert "api_key_hash" not in data


def test_audit_retrieve_wrong_key_returns_404(service_context, fake_audit, api_key) -> None:
    """Audit entries belonging to other keys return 404 (no enumeration)."""
    fake_audit.add(
        query_id="other-key-query",
        api_key_hash="z" * 64,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    client = TestClient(app)
    r = client.get(
        "/v1/audit/other-key-query",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404


def test_audit_retrieve_nonexistent_returns_404(service_context, api_key) -> None:
    client = TestClient(app)
    r = client.get(
        "/v1/audit/does-not-exist",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404


def test_audit_retrieve_requires_api_key(service_context) -> None:
    client = TestClient(app)
    r = client.get("/v1/audit/something")
    assert r.status_code == 401
