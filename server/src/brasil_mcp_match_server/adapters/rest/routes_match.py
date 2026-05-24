"""REST endpoints — match e check tools.

Cada endpoint: valida API key → busca empresa no repo → roda match → audita
→ retorna resposta com query_id. Falha de lookup retorna 404 com OPT_OUT_RECORD
ou CNPJ_NOT_FOUND.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from brasil_mcp_match_server.adapters.rest.dependencies import (
    ServiceContext,
    get_service,
    require_api_key,
)
from brasil_mcp_match_server.core.audit import log as audit_log
from brasil_mcp_match_server.core.errors import ErrorCode, ErrorObj
from brasil_mcp_match_server.core.matching.localizacao import match_uf
from brasil_mcp_match_server.core.matching.porte import check_porte_empresa
from brasil_mcp_match_server.core.matching.razao_social import match_razao_social
from brasil_mcp_match_server.core.matching.situacao import check_situacao_cadastral

router = APIRouter(prefix="/v1")

_CNPJ_RE = re.compile(r"\D")


def _normalize_cnpj(cnpj: str) -> str:
    return _CNPJ_RE.sub("", cnpj)


def _validate_cnpj_shape(cnpj: str) -> str:
    """Returns the normalized cnpj or raises 422 with INVALID_CNPJ."""
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


def _envelope(query_id: str, base_updated_at: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Standard envelope returned by every match/check endpoint."""
    return {"query_id": query_id, "base_updated_at": base_updated_at, **payload}


def _record_or_404(cnpj_completo: str, service: ServiceContext) -> Any:
    """Look up CNPJ, but first enforce opt-out (LGPD Art. 18)."""
    if service.is_opt_out_blocked(cnpj_completo):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=ErrorObj(
                ErrorCode.OPT_OUT_RECORD,
                "Este CNPJ optou por remoção (Art. 18 LGPD). Contate-nos.",
                "This CNPJ opted out (LGPD Art. 18). Contact us.",
            ).to_dict(),
        )
    record = service.repo.find_by_cnpj(cnpj_completo)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorObj(
                ErrorCode.CNPJ_NOT_FOUND,
                "CNPJ não encontrado na base atual da Receita Federal.",
                "CNPJ not found in current Receita Federal base.",
            ).to_dict(),
        )
    return record


# ------------ Request models ------------


class MatchRazaoRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")
    nome: str = Field(..., description="Nome a verificar")
    tolerance: float = Field(0.85, ge=0.5, le=1.0, description="0.5-1.0; default 0.85")


class CnpjOnlyRequest(BaseModel):
    cnpj: str


class MatchUfRequest(BaseModel):
    cnpj: str
    uf: str = Field(..., min_length=2, max_length=2)


# ------------ Endpoints ------------


@router.post("/match/razao-social")
def post_match_razao_social(
    body: MatchRazaoRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    record = _record_or_404(cnpj, service)

    result = match_razao_social(body.nome, record.razao_social, tolerance=body.tolerance)
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="match_razao_social",
        payload={"cnpj": cnpj, "nome": body.nome, "tolerance": body.tolerance},
        cnpj_basico=cnpj[:8],
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )

    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/check/situacao")
def post_check_situacao(
    body: CnpjOnlyRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    record = _record_or_404(cnpj, service)

    result = check_situacao_cadastral(
        record.situacao_cadastral_codigo,
        since=record.data_situacao_cadastral,
    )
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="check_situacao_cadastral",
        payload={"cnpj": cnpj},
        cnpj_basico=cnpj[:8],
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/check/porte")
def post_check_porte(
    body: CnpjOnlyRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    record = _record_or_404(cnpj, service)

    result = check_porte_empresa(
        record.porte_empresa_codigo,
        opcao_simples=record.opcao_simples,
        opcao_mei=record.opcao_mei,
    )
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="check_porte_empresa",
        payload={"cnpj": cnpj},
        cnpj_basico=cnpj[:8],
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/match/uf")
def post_match_uf(
    body: MatchUfRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    record = _record_or_404(cnpj, service)

    result = match_uf(body.uf, record.uf)
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="match_uf",
        payload={"cnpj": cnpj, "uf": body.uf},
        cnpj_basico=cnpj[:8],
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)
