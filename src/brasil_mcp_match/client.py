"""Thin async HTTP client for the brasil-mcp-match REST API.

Reads ``BRASIL_MCP_MATCH_URL`` and ``BRASIL_MCP_MATCH_KEY`` from environment.
Each call POSTs JSON to a REST endpoint and returns the parsed envelope dict
(`{"query_id": ..., "base_updated_at": ..., ...}` on success or
`{"error": {"code": ..., "message_pt": ..., "message_en": ...}}` on failure).

Network/HTTP failures are mapped to a synthetic error envelope so the MCP
tools always return a JSON-serializable dict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

import httpx

_DEFAULT_TIMEOUT = 10.0
_API_KEY_HEADER = "X-Brasil-MCP-Key"


@dataclass(frozen=True, slots=True)
class ClientConfig:
    base_url: str
    api_key: str
    timeout: float = _DEFAULT_TIMEOUT

    @property
    def has_api_key(self) -> bool:
        """True when an API key is configured (non-empty)."""
        return bool(self.api_key)


def load_config_from_env() -> ClientConfig:
    """Load `ClientConfig` from process env.

    `BRASIL_MCP_MATCH_URL` is required. `BRASIL_MCP_MATCH_KEY` is OPTIONAL: when
    unset/empty, the resulting config has `has_api_key=False`, the signup tools
    still work, and the verifier tools should short-circuit with a
    `MISSING_API_KEY` envelope.
    """
    base_url = os.environ.get("BRASIL_MCP_MATCH_URL")
    if not base_url:
        raise RuntimeError(
            "BRASIL_MCP_MATCH_URL is required. "
            "Example: https://server.solidapps.tech/brasil-mcp/match"
        )
    api_key = os.environ.get("BRASIL_MCP_MATCH_KEY") or ""
    timeout_raw = os.environ.get("BRASIL_MCP_MATCH_TIMEOUT")
    timeout = float(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT
    return ClientConfig(base_url=base_url.rstrip("/"), api_key=api_key, timeout=timeout)


def _error_envelope(code: str, message_pt: str, message_en: str) -> dict[str, Any]:
    return {"error": {"code": code, "message_pt": message_pt, "message_en": message_en}}


class MatchHttpClient:
    """Async HTTP wrapper over the brasil-mcp-match REST API."""

    def __init__(self, config: ClientConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout),
            headers={_API_KEY_HEADER: config.api_key},
        )
        self._owns_http = http_client is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._config.base_url}{path}"
        try:
            resp = await self._http.post(url, json=payload)
        except httpx.TimeoutException:
            return _error_envelope(
                "UPSTREAM_TIMEOUT",
                "Tempo de resposta do servidor Match excedido.",
                "Upstream Match server timed out.",
            )
        except httpx.HTTPError as exc:
            return _error_envelope(
                "UPSTREAM_NETWORK",
                f"Falha de rede chamando o servidor Match: {exc}",
                f"Network error calling Match upstream: {exc}",
            )

        # 4xx/5xx envelopes from the REST API already follow the spec shape.
        try:
            raw = resp.json()
        except ValueError:
            return _error_envelope(
                "UPSTREAM_BAD_RESPONSE",
                f"Resposta inválida do servidor Match (HTTP {resp.status_code}).",
                f"Invalid response from Match upstream (HTTP {resp.status_code}).",
            )

        if not isinstance(raw, dict):
            return _error_envelope(
                "UPSTREAM_BAD_RESPONSE",
                "Resposta inesperada do servidor Match (esperava objeto JSON).",
                "Unexpected response from Match upstream (expected JSON object).",
            )

        body = cast(dict[str, Any], raw)
        # FastAPI exception handlers wrap errors under `detail`; unwrap so tools
        # see the same shape as a happy-path envelope.
        if resp.status_code >= 400 and "detail" in body and "error" not in body:
            detail = body["detail"]
            if isinstance(detail, dict):
                return {"error": detail}
        return body

    async def match_razao_social(self, cnpj: str, nome: str, tolerance: float) -> dict[str, Any]:
        return await self._post(
            "/v1/match/razao-social",
            {"cnpj": cnpj, "nome": nome, "tolerance": tolerance},
        )

    async def check_situacao_cadastral(self, cnpj: str) -> dict[str, Any]:
        return await self._post("/v1/check/situacao", {"cnpj": cnpj})

    async def check_porte_empresa(self, cnpj: str) -> dict[str, Any]:
        return await self._post("/v1/check/porte", {"cnpj": cnpj})

    async def match_uf(self, cnpj: str, uf: str) -> dict[str, Any]:
        return await self._post("/v1/match/uf", {"cnpj": cnpj, "uf": uf})

    async def signup_start(
        self, email: str, plan: str, cpf_cnpj: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": email, "plan": plan}
        if cpf_cnpj is not None:
            payload["cpf_cnpj"] = cpf_cnpj
        return await self._post("/v1/signup/start", payload)

    async def signup_status(self, polling_token: str) -> dict[str, Any]:
        return await self._post("/v1/signup/status", {"polling_token": polling_token})

    async def socio_match_nome(
        self, cnpj: str, nome: str, tolerance: float
    ) -> dict[str, Any]:
        return await self._post(
            "/v1/socio/match-nome",
            {"cnpj": cnpj, "nome": nome, "tolerance": tolerance},
        )

    async def socio_match_cpf(self, cnpj: str, cpf: str) -> dict[str, Any]:
        return await self._post("/v1/socio/match-cpf", {"cnpj": cnpj, "cpf": cpf})

    async def socio_match_cnpj_socio(
        self, cnpj: str, cnpj_socio: str
    ) -> dict[str, Any]:
        return await self._post(
            "/v1/socio/match-cnpj-socio",
            {"cnpj": cnpj, "cnpj_socio": cnpj_socio},
        )

    async def socio_check_qualificacao(
        self, cnpj: str, qualificacao: int
    ) -> dict[str, Any]:
        return await self._post(
            "/v1/socio/check-qualificacao",
            {"cnpj": cnpj, "qualificacao": qualificacao},
        )

    async def socio_count(self, cnpj: str) -> dict[str, Any]:
        return await self._post("/v1/socio/count", {"cnpj": cnpj})
