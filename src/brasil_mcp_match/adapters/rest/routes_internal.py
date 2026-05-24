"""Internal REST router for sister services (e.g., brasil-mcp-leads).

WARNING: These endpoints return RAW Receita Federal data and bypass the
public "match, don't reveal" contract. They are gated by a separate
``X-Internal-Service-Token`` header (env ``MATCH_INTERNAL_SERVICE_TOKEN``)
and MUST NOT be exposed to end users.

The token is provisioned out-of-band (operator-managed env on the server).
It is NOT stored in any database and has no quota — sister services are
trusted. If the env var is unset, every request is refused with 503
``INTERNAL_DISABLED``.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from brasil_mcp_match.adapters.rest.dependencies import (
    ServiceContext,
    get_service,
)
from brasil_mcp_match.core.errors import ErrorCode, ErrorObj

router = APIRouter(prefix="/v1/internal")

_INTERNAL_TOKEN_ENV = "MATCH_INTERNAL_SERVICE_TOKEN"
_CNPJ_RE = re.compile(r"\D")


def _normalize_cnpj(cnpj: str) -> str:
    return _CNPJ_RE.sub("", cnpj)


def _validate_cnpj_shape(cnpj: str) -> str:
    """Reuse public validation: returns the normalized cnpj or raises 422 with INVALID_CNPJ."""
    norm = _normalize_cnpj(cnpj)
    if len(norm) != 14:
        raise HTTPException(
            status_code=422,
            detail=ErrorObj(
                ErrorCode.INVALID_CNPJ,
                f"CNPJ deve ter 14 dígitos; recebido {len(norm)}.",
                f"CNPJ must have 14 digits; got {len(norm)}.",
            ).to_dict(),
        )
    return norm


def require_internal_token(
    x_internal_service_token: str | None = Header(default=None, alias="X-Internal-Service-Token"),
) -> str:
    """FastAPI dependency that validates the internal sister-service token.

    - Env ``MATCH_INTERNAL_SERVICE_TOKEN`` unset → 503 INTERNAL_DISABLED.
    - Header missing or token mismatch → 401 INTERNAL_AUTH_REQUIRED.
    """
    expected = os.environ.get(_INTERNAL_TOKEN_ENV)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorObj(
                ErrorCode.INTERNAL_DISABLED,
                "Endpoint interno desabilitado: MATCH_INTERNAL_SERVICE_TOKEN não configurado.",
                "Internal endpoint disabled: MATCH_INTERNAL_SERVICE_TOKEN not configured.",
            ).to_dict(),
        )
    if x_internal_service_token is None or not hmac.compare_digest(
        x_internal_service_token, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorObj(
                ErrorCode.INTERNAL_AUTH_REQUIRED,
                "Token interno ausente ou inválido (X-Internal-Service-Token).",
                "Internal token missing or invalid (X-Internal-Service-Token).",
            ).to_dict(),
        )
    return x_internal_service_token


# ------------ Models ------------


class InternalEmpresaRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")


class InternalEmpresaResponse(BaseModel):
    """Raw ``EmpresaRecord`` mirror for trusted sister services.

    Fields match ``brasil_mcp_match.core.repository.cnpj_repo.EmpresaRecord``
    plus the derived ``cnpj_completo`` (the 14-digit lookup key).
    ``capital_social`` is serialized as ``str`` to preserve Decimal precision
    over JSON. Dates are ISO-formatted strings.
    """

    cnpj_completo: str
    cnpj_basico: str
    razao_social: str
    nome_fantasia: str | None = None
    porte_empresa_codigo: str | None = None
    capital_social: str | None = None
    data_inicio_atividade: str | None = None
    situacao_cadastral_codigo: str | None = None
    data_situacao_cadastral: str | None = None
    opcao_simples: str | None = None
    opcao_mei: str | None = None
    uf: str | None = None
    municipio_nome: str | None = None
    cep: str | None = None
    cnae_principal: str | None = None


# ------------ Endpoint ------------


@router.post("/empresa", response_model=InternalEmpresaResponse)
def post_internal_empresa(
    body: InternalEmpresaRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    _token: Annotated[str, Depends(require_internal_token)],
) -> InternalEmpresaResponse:
    """Return the full ``EmpresaRecord`` for a CNPJ — internal sister-service only."""
    cnpj = _validate_cnpj_shape(body.cnpj)
    record = service.repo.find_by_cnpj(cnpj)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorObj(
                ErrorCode.CNPJ_NOT_FOUND,
                "CNPJ não encontrado na base atual da Receita Federal.",
                "CNPJ not found in current Receita Federal base.",
            ).to_dict(),
        )

    capital: str | None = str(record.capital_social) if record.capital_social is not None else None

    def _iso(d: Any) -> str | None:
        return d.isoformat() if d is not None else None

    return InternalEmpresaResponse(
        cnpj_completo=cnpj,
        cnpj_basico=record.cnpj_basico,
        razao_social=record.razao_social,
        nome_fantasia=record.nome_fantasia,
        porte_empresa_codigo=record.porte_empresa_codigo,
        capital_social=capital,
        data_inicio_atividade=_iso(record.data_inicio_atividade),
        situacao_cadastral_codigo=record.situacao_cadastral_codigo,
        data_situacao_cadastral=_iso(record.data_situacao_cadastral),
        opcao_simples=record.opcao_simples,
        opcao_mei=record.opcao_mei,
        uf=record.uf,
        municipio_nome=record.municipio_nome,
        cep=record.cep,
        cnae_principal=record.cnae_principal,
    )
