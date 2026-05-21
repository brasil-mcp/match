"""Opt-out cannot be circumvented.

Once an opt-out is effective for a CNPJ:
- ALL four match/check tools return 410 GONE with OPT_OUT_RECORD.
- A different CNPJ is unaffected.
- The opt-out registration response is minimal (no leak of other personal data).
- Opt-out applies regardless of casing/format of the CNPJ (matriz/filial).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brasil_mcp_match.adapters.rest.app import app

_OPTED_OUT = "33000167000101"
_FREE = "11222333000181"

ALL_ENDPOINTS: list[tuple[str, dict[str, object]]] = [
    ("/v1/match/razao-social", {"cnpj": _OPTED_OUT, "nome": "X"}),
    ("/v1/check/situacao", {"cnpj": _OPTED_OUT}),
    ("/v1/check/porte", {"cnpj": _OPTED_OUT}),
    ("/v1/match/uf", {"cnpj": _OPTED_OUT, "uf": "RJ"}),
]


def test_opt_out_blocks_all_four_endpoints(service_context, fake_opt_out, api_key: str) -> None:
    fake_opt_out.force_block(_OPTED_OUT)
    client = TestClient(app)
    for path, body in ALL_ENDPOINTS:
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        assert r.status_code == 410, f"{path} should be blocked but got {r.status_code}"
        assert r.json()["detail"]["code"] == "OPT_OUT_RECORD"


def test_opt_out_does_not_affect_other_cnpj(service_context, fake_opt_out, api_key: str) -> None:
    fake_opt_out.force_block(_OPTED_OUT)
    client = TestClient(app)
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": _FREE},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200


def test_opt_out_returns_202_not_409(service_context) -> None:
    """The opt-out request is REGISTERED with status 202 Accepted — even on
    duplicate requests (re-arming the 15-day clock is idempotent)."""
    client = TestClient(app)
    r1 = client.post(
        f"/v1/opt-out/{_OPTED_OUT}",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    r2 = client.post(
        f"/v1/opt-out/{_OPTED_OUT}",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    assert r1.status_code == 202
    assert r2.status_code == 202


def test_opt_out_response_shape_minimal(service_context) -> None:
    """The 202 body does NOT expose unrelated personal data (other CNPJs,
    contact emails of OTHERS, etc.)."""
    client = TestClient(app)
    r = client.post(
        f"/v1/opt-out/{_OPTED_OUT}",
        json={
            "proof_type": "email",
            "proof_value": "owner@example.com",
            "contact_email": "owner@example.com",
        },
    )
    data = r.json()
    # Allowed keys only.
    assert set(data.keys()) == {"cnpj", "requested_at", "effective_at", "message_pt", "message_en"}
    # The proof plaintext must not be echoed.
    assert "owner@example.com" not in r.text


def test_opt_out_invalid_cnpj_returns_422(service_context) -> None:
    """Bad CNPJ on opt-out → 422, not 500."""
    client = TestClient(app)
    r = client.post(
        "/v1/opt-out/notacnpj",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    assert r.status_code == 422


def test_opt_out_does_not_require_api_key(service_context) -> None:
    """LGPD Art. 18 — the titular doesn't have an API key. Endpoint is public."""
    client = TestClient(app)
    r = client.post(
        f"/v1/opt-out/{_OPTED_OUT}",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    assert r.status_code == 202


def test_opt_out_not_yet_effective_does_not_block(
    service_context, fake_opt_out, api_key: str
) -> None:
    """An opt-out registered NOW (15-day delay) does NOT block immediately."""
    client = TestClient(app)
    # Register normally (effective_at = now + 15 business days).
    client.post(
        f"/v1/opt-out/{_OPTED_OUT}",
        json={"proof_type": "email", "proof_value": "x@example.com"},
    )
    # Match still works.
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": _OPTED_OUT},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "masked_cnpj",
    ["33.000.167/0001-01", "33000167/0001-01", "33000167-000101"],
)
def test_opt_out_normalized_cnpj_blocks_match(
    service_context, fake_opt_out, api_key: str, masked_cnpj: str
) -> None:
    """An opt-out for normalized 14-digit CNPJ must block masked variants
    after they're normalized by the endpoint."""
    fake_opt_out.force_block(_OPTED_OUT)
    client = TestClient(app)
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": masked_cnpj},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "OPT_OUT_RECORD"
