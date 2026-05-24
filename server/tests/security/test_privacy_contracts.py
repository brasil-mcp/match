"""Privacy contracts — output schemas + LGPD-sensitive field exclusion.

These tests enforce the "match, don't reveal" promise:
- Output schema fields are a documented subset per tool.
- razao_social, capital_social, full address, full CPF are NEVER present.
- api_key_hash never appears in any HTTP response body.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app

_CNPJ = "33000167000101"

# Documented response shape per endpoint (envelope keys + payload keys).
# The envelope is `query_id`, `base_updated_at`. The payload differs per tool.
DOCUMENTED_FIELDS: dict[str, set[str]] = {
    "/v1/match/razao-social": {"query_id", "base_updated_at", "match", "confidence", "hint"},
    "/v1/check/situacao": {"query_id", "base_updated_at", "situacao", "since"},
    "/v1/check/porte": {
        "query_id",
        "base_updated_at",
        "porte",
        "is_simples_nacional",
        "is_mei",
    },
    "/v1/match/uf": {"query_id", "base_updated_at", "match"},
}

# These RF fields must never leak in any output.
FORBIDDEN_SUBSTRINGS = [
    "PETROLEO BRASILEIRO",  # razao_social
    "205431960490",  # capital_social
    "20031170",  # full CEP
    "RIO DE JANEIRO",  # municipio
    "1921700",  # cnae_principal
]


def _all_endpoints(api_key: str) -> list[tuple[str, dict[str, object]]]:
    return [
        ("/v1/match/razao-social", {"cnpj": _CNPJ, "nome": "Petrobras"}),
        ("/v1/check/situacao", {"cnpj": _CNPJ}),
        ("/v1/check/porte", {"cnpj": _CNPJ}),
        ("/v1/match/uf", {"cnpj": _CNPJ, "uf": "RJ"}),
    ]


def test_response_keys_are_documented_subset(service_context, api_key: str) -> None:
    """For each endpoint, response keys MUST be a subset of DOCUMENTED_FIELDS."""
    client = TestClient(app)
    for path, body in _all_endpoints(api_key):
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        data = r.json()
        actual_keys = set(data.keys())
        documented = DOCUMENTED_FIELDS[path]
        extra = actual_keys - documented
        assert not extra, f"{path} returned undocumented fields: {extra}"


def test_response_never_contains_razao_social_or_pii(service_context, api_key: str) -> None:
    """Sweep through every endpoint and ensure NO known-RF-secret appears."""
    client = TestClient(app)
    for path, body in _all_endpoints(api_key):
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        text = r.text
        for secret in FORBIDDEN_SUBSTRINGS:
            assert secret not in text, f"{path} leaked {secret!r}"


def test_api_key_hash_never_in_response_body(
    service_context, fake_audit, api_key: str, api_key_hash: str
) -> None:
    """api_key_hash must NEVER appear in any HTTP response body."""
    client = TestClient(app)

    # Match endpoints
    for path, body in _all_endpoints(api_key):
        r = client.post(path, json=body, headers={"X-Brasil-MCP-Key": api_key})
        assert api_key_hash not in r.text, f"{path} leaked api_key_hash"

    # Audit retrieve
    fake_audit.add(
        query_id="qid-1",
        api_key_hash=api_key_hash,
        tool="match_uf",
        cnpj_basico="33000167",
        response_summary={"match": True},
    )
    r = client.get("/v1/audit/qid-1", headers={"X-Brasil-MCP-Key": api_key})
    assert api_key_hash not in r.text


def test_full_cpf_never_in_response(service_context, api_key: str) -> None:
    """The MEI fixture's razao_social embeds a CPF — even masked, it must not leak."""
    client = TestClient(app)
    # The MEI fixture is 11222333000181 with razao_social embedding "12345678900"
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": "11222333000181", "nome": "Joao da Silva"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    assert "12345678900" not in r.text


def test_opt_out_response_minimal(service_context) -> None:
    """The opt-out endpoint's response must NOT expose other personal fields."""
    client = TestClient(app)
    r = client.post(
        "/v1/opt-out/33000167000101",
        json={"proof_type": "email", "proof_value": "secret-12345@example.com"},
    )
    assert r.status_code == 202
    data = r.json()
    # Allowed keys only.
    allowed = {"cnpj", "requested_at", "effective_at", "message_pt", "message_en"}
    assert set(data.keys()) <= allowed
    # proof_value (which could be PII) must NOT be echoed.
    assert "secret-12345@example.com" not in r.text


def test_health_does_not_leak_internals() -> None:
    """The unauthenticated /health endpoint returns minimal info."""
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"status", "version"}
    # No database URL, no secrets.
    assert "postgresql" not in r.text.lower()
    assert "password" not in r.text.lower()


def test_error_responses_never_echo_input_pii(service_context, api_key: str) -> None:
    """Error bodies for malformed input must not echo a CPF/CNPJ verbatim."""
    client = TestClient(app)
    pii_like = "98765432100"  # CPF-shaped
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": pii_like, "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    # 422 INVALID_CNPJ — the message references "14 digits" but not the input.
    assert r.status_code == 422
    assert pii_like not in r.text


def test_match_razao_social_response_shape_fields_typed(service_context, api_key: str) -> None:
    """Specific type assertions on match output."""
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": _CNPJ, "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    data = r.json()
    assert isinstance(data["match"], bool)
    assert isinstance(data["confidence"], (int, float))
    assert isinstance(data["hint"], str)
    assert isinstance(data["query_id"], str)
    assert isinstance(data["base_updated_at"], str)
