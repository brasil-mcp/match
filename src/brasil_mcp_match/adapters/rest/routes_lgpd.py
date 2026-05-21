"""REST endpoints LGPD — opt-out + audit retrieve.

GET /v1/audit/{query_id} — qualquer cliente autenticado pode buscar o resumo
da sua chamada via query_id. RBAC: caller só vê queries com mesmo api_key_hash
(prevents enumeration de queries de outros clientes).

POST /v1/opt-out/{cnpj} — público (sem API key). Acoplado a validação manual
de prova humana antes de marcar como `effective`. v0.1.0: aceita prova bruta
e grava — a revisão humana é manual via dashboard interno (não exposto aqui).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from brasil_mcp_match.adapters.rest.dependencies import (
    ServiceContext,
    get_service,
    require_api_key,
)
from brasil_mcp_match.core.errors import ErrorCode, ErrorObj

router = APIRouter(prefix="/v1")


# ------------ GET /v1/audit/{query_id} ------------


@router.get("/audit/{query_id}")
def get_audit(
    query_id: str,
    service: Annotated[ServiceContext, Depends(get_service)],
    auth: Annotated[Any, Depends(require_api_key)],
) -> dict[str, Any]:
    """Return audit entry for query_id. Returns 404 if not found OR if it
    belongs to a different API key (no leak about existence)."""
    fetched = service.audit_lookup(query_id, auth.record.key_hash)
    if fetched is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorObj(
                ErrorCode.INVALID_FORMAT,
                "Audit entry não encontrada.",
                "Audit entry not found.",
            ).to_dict(),
        )
    return fetched


# ------------ POST /v1/opt-out/{cnpj} ------------


class OptOutBody(BaseModel):
    proof_type: str  # "email" | "document_id" | "other"
    proof_value: str  # opaque (email, doc number, etc.)
    contact_email: str | None = None


@router.post("/opt-out/{cnpj}", status_code=status.HTTP_202_ACCEPTED)
def post_opt_out(
    cnpj: str,
    body: OptOutBody,
    service: Annotated[ServiceContext, Depends(get_service)],
) -> dict[str, Any]:
    """Registra pedido de opt-out. NÃO requer API key — qualquer titular pode acionar."""
    import re

    cnpj_norm = re.sub(r"\D", "", cnpj or "")
    if len(cnpj_norm) != 14:
        raise HTTPException(
            status_code=422,
            detail=ErrorObj(
                ErrorCode.INVALID_CNPJ,
                f"CNPJ deve ter 14 dígitos; recebido {len(cnpj_norm)}.",
                f"CNPJ must have 14 digits; got {len(cnpj_norm)}.",
            ).to_dict(),
        )

    record = service.opt_out_register(
        cnpj=cnpj_norm,
        proof={
            "type": body.proof_type,
            "value_hash": _hash_proof(body.proof_value),
            "contact_email": body.contact_email,
        },
    )
    return {
        "cnpj": record.cnpj,
        "requested_at": record.requested_at.isoformat(),
        "effective_at": record.effective_at.isoformat(),
        "message_pt": "Pedido de remoção registrado. Vigência em até 15 dias úteis.",
        "message_en": "Opt-out request recorded. Effective within 15 business days.",
    }


def _hash_proof(value: str) -> str:
    """Hash the proof value — we don't want PII (emails, doc numbers) sitting
    in plaintext in our DB. We only need to verify, not retrieve."""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
