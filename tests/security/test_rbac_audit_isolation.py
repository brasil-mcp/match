"""RBAC isolation for audit endpoint.

Audit entries are scoped per `api_key_hash`. A caller MUST NOT be able to fetch
audit rows belonging to a different api key:
- 404 (not 403, not 500) when query_id belongs to another key — no enumeration.
- 404 also when query_id does not exist.
- The response body never leaks the hash of the actual owner.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from brasil_mcp_match.adapters.rest.app import app


def test_audit_other_key_returns_404_not_403(service_context, fake_audit, api_key: str) -> None:
    """Audit row belonging to other API key → 404 (not 403). No enumeration."""
    fake_audit.add(
        query_id="other-owner-query",
        api_key_hash="OTHER" * 12 + "1234",
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    client = TestClient(app)
    r = client.get(
        "/v1/audit/other-owner-query",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404
    # Status code is the SAME for "not exists" and "exists, wrong owner".


def test_audit_nonexistent_returns_404(service_context, api_key: str) -> None:
    client = TestClient(app)
    r = client.get(
        "/v1/audit/nope-nope",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404


def test_audit_existing_for_same_key_returns_200(
    service_context, fake_audit, api_key: str, api_key_hash: str
) -> None:
    fake_audit.add(
        query_id="mine",
        api_key_hash=api_key_hash,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    client = TestClient(app)
    r = client.get(
        "/v1/audit/mine",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["query_id"] == "mine"


def test_audit_response_never_leaks_other_owner_hash(
    service_context, fake_audit, api_key: str
) -> None:
    """Even in 404 case, the body must not echo the other owner's hash."""
    other_hash = "OWNER" * 12 + "AABB"
    fake_audit.add(
        query_id="leaked-attempt",
        api_key_hash=other_hash,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    client = TestClient(app)
    r = client.get(
        "/v1/audit/leaked-attempt",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 404
    assert other_hash not in r.text


def test_audit_404_body_constant_shape(service_context, api_key: str) -> None:
    """404 body for wrong-owner and not-found is structurally identical."""
    client = TestClient(app)

    r_missing = client.get(
        "/v1/audit/missing-id",
        headers={"X-Brasil-MCP-Key": api_key},
    )

    # Now add an entry owned by another key, look it up, expect same 404 shape.
    from brasil_mcp_match.adapters.rest.dependencies import get_service

    svc = get_service()
    svc.audit_lookup.__self__.add(  # type: ignore[attr-defined]
        query_id="other-id",
        api_key_hash="X" * 64,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    r_wrong = client.get(
        "/v1/audit/other-id",
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r_missing.status_code == 404
    assert r_wrong.status_code == 404
    # Structural equality of the detail keys — same code on both responses.
    assert r_missing.json()["detail"]["code"] == r_wrong.json()["detail"]["code"]


def test_audit_requires_authentication(service_context, fake_audit, api_key_hash: str) -> None:
    """No API key → 401, NOT 404 (we want to clearly indicate auth required)."""
    fake_audit.add(
        query_id="my-id",
        api_key_hash=api_key_hash,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    client = TestClient(app)
    r = client.get("/v1/audit/my-id")  # no header
    assert r.status_code == 401
