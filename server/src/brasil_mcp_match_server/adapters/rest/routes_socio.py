"""REST endpoints — sócio verification (privacy-preserving).

Five POST endpoints under ``/v1/socio/`` that mirror the structure of
:mod:`routes_match`: validate API key → enforce opt-out on parent CNPJ →
fetch sócios → run matcher → audit → return envelope.

Privacy contract (ENFORCED, do not relax):
- Responses NEVER contain ``nome_socio`` raw text, the masked
  ``cnpj_cpf_socio``, qualificacao descriptions, or any other PII.
- The audit log payload NEVER contains the user-supplied ``nome`` or ``cpf``
  in plaintext — both are sha256-hashed and stored as ``nome_hash``/``cpf_hash``.
- The masked-CPF window extracted internally for comparison NEVER leaves
  this module — it's not in the response, not in the audit, not in any log.
"""

from __future__ import annotations

import hashlib
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
from brasil_mcp_match_server.core.matching.socio import (
    check_qualificacao,
    count_socios,
    match_cnpj_socio,
    match_cpf_socio,
    match_nome_socio,
)

router = APIRouter(prefix="/v1")

_NON_DIGIT_RE = re.compile(r"\D")


# ----------------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------------


def _normalize_digits(value: str) -> str:
    return _NON_DIGIT_RE.sub("", value or "")


def _validate_cnpj_shape(cnpj: str) -> str:
    norm = _normalize_digits(cnpj)
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


def _validate_cpf_shape(cpf: str) -> str:
    norm = _normalize_digits(cpf)
    if len(norm) != 11:
        # NEVER echo the user's CPF in the error message — privacy contract.
        raise HTTPException(
            status_code=422,
            detail=ErrorObj(
                ErrorCode.INVALID_CPF,
                f"CPF deve ter 11 dígitos; recebido {len(norm)}.",
                f"CPF must have 11 digits; got {len(norm)}.",
            ).to_dict(),
        )
    return norm


def _envelope(query_id: str, base_updated_at: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"query_id": query_id, "base_updated_at": base_updated_at, **payload}


def _empresa_or_404(cnpj_completo: str, service: ServiceContext) -> Any:
    """Enforce opt-out first, then look up the parent empresa.

    Same shape/codes as :mod:`routes_match` — 410 GONE OPT_OUT_RECORD beats
    404 CNPJ_NOT_FOUND so opt-out can't be probed via 404/410 distinguishing.
    """
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


def _hash_sensitive(value: str) -> str:
    """sha256 hex digest of a sensitive string — used in audit payloads so we
    don't persist the user's ``nome`` / ``cpf`` plaintext."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------------


class MatchNomeRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")
    nome: str = Field(..., min_length=1, description="Nome do sócio a verificar")
    tolerance: float = Field(0.85, ge=0.5, le=1.0, description="0.5-1.0; default 0.85")


class MatchCpfRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")
    cpf: str = Field(..., description="11-dígito CPF (com ou sem pontuação)")


class MatchCnpjSocioRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ pai (com ou sem máscara)")
    cnpj_socio: str = Field(..., description="14-dígito CNPJ candidato a sócio")


class CheckQualificacaoRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")
    qualificacao: int = Field(..., ge=1, description="Código de ref_qualificacao_socio")


class CountRequest(BaseModel):
    cnpj: str = Field(..., description="14-dígito CNPJ (com ou sem máscara)")


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------


@router.post("/socio/match-nome")
def post_match_nome_socio(
    body: MatchNomeRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    _empresa_or_404(cnpj, service)
    cnpj_basico = cnpj[:8]
    socios = service.repo.find_socios_by_cnpj_basico(cnpj_basico)

    result = match_nome_socio(socios, body.nome, tolerance=body.tolerance)
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="match_nome_socio",
        # NEVER persist the user's nome plaintext.
        payload={"cnpj": cnpj, "nome_hash": _hash_sensitive(body.nome), "tolerance": body.tolerance},
        cnpj_basico=cnpj_basico,
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/socio/match-cpf")
def post_match_cpf_socio(
    body: MatchCpfRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    cpf = _validate_cpf_shape(body.cpf)
    _empresa_or_404(cnpj, service)
    cnpj_basico = cnpj[:8]
    socios = service.repo.find_socios_by_cnpj_basico(cnpj_basico)

    matched = match_cpf_socio(socios, cpf)
    summary: dict[str, Any] = {"match": matched}

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="match_cpf_socio",
        # NEVER persist the user's CPF plaintext (masked or otherwise).
        payload={"cnpj": cnpj, "cpf_hash": _hash_sensitive(cpf)},
        cnpj_basico=cnpj_basico,
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/socio/match-cnpj-socio")
def post_match_cnpj_socio(
    body: MatchCnpjSocioRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    cnpj_socio = _validate_cnpj_shape(body.cnpj_socio)
    _empresa_or_404(cnpj, service)
    cnpj_basico = cnpj[:8]
    socios = service.repo.find_socios_by_cnpj_basico(cnpj_basico)

    matched = match_cnpj_socio(socios, cnpj_socio)
    summary: dict[str, Any] = {"match": matched}

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="match_cnpj_socio",
        # cnpj_socio is a CNPJ (legal entity) — public information, not PII.
        # Safe to log as-is for ops correlation.
        payload={"cnpj": cnpj, "cnpj_socio": cnpj_socio},
        cnpj_basico=cnpj_basico,
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/socio/check-qualificacao")
def post_check_qualificacao(
    body: CheckQualificacaoRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    if not service.repo.qualificacao_codigo_exists(body.qualificacao):
        # Not a known qualificacao — surface early so callers don't get
        # mysteriously empty results.
        raise HTTPException(
            status_code=422,
            detail=ErrorObj(
                ErrorCode.INVALID_QUALIFICACAO,
                f"Código de qualificação desconhecido: {body.qualificacao}.",
                f"Unknown qualificacao code: {body.qualificacao}.",
            ).to_dict(),
        )
    _empresa_or_404(cnpj, service)
    cnpj_basico = cnpj[:8]
    socios = service.repo.find_socios_by_cnpj_basico(cnpj_basico)

    result = check_qualificacao(socios, body.qualificacao)
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="check_qualificacao",
        payload={"cnpj": cnpj, "qualificacao": body.qualificacao},
        cnpj_basico=cnpj_basico,
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)


@router.post("/socio/count")
def post_count_socios(
    body: CountRequest,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    cnpj = _validate_cnpj_shape(body.cnpj)
    _empresa_or_404(cnpj, service)
    cnpj_basico = cnpj[:8]
    socios = service.repo.find_socios_by_cnpj_basico(cnpj_basico)

    result = count_socios(socios)
    summary = result.to_dict()

    entry = audit_log.build_entry(
        api_key_hash=auth.record.key_hash,
        tool="count_socios",
        payload={"cnpj": cnpj},
        cnpj_basico=cnpj_basico,
        response_summary=summary,
        base_updated_at=service.base_updated_at,
    )
    return _envelope(entry.query_id, service.base_updated_at.isoformat(), summary)
