"""MCP tools — wrappers thin sobre o core, com authn delegada ao caller.

Diferente de essentials (stdio anônimo), aqui cada call do MCP precisa de
API key. O FastMCP por si só não tem header auth — quem faz a authn é o
transport SSE (HTTP-level via FastAPI). Quando essentialsv2 (Streamable HTTP)
estiver mais maduro, migraremos.

Pra v0.1.0: as tools assumem que o caller (REST adapter ou FastAPI mount)
já validou o API key e passou um `RequestContext` com `repo`, `auth.record`,
e `base_updated_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP

from brasil_mcp_match_server.core.audit import log as audit_log
from brasil_mcp_match_server.core.auth.api_key import ApiKeyRecord
from brasil_mcp_match_server.core.matching.localizacao import match_uf
from brasil_mcp_match_server.core.matching.porte import check_porte_empresa
from brasil_mcp_match_server.core.matching.razao_social import match_razao_social
from brasil_mcp_match_server.core.matching.situacao import check_situacao_cadastral
from brasil_mcp_match_server.core.repository.cnpj_repo import CnpjRepo


@dataclass(frozen=True, slots=True)
class RequestContext:
    repo: CnpjRepo
    auth_record: ApiKeyRecord
    base_updated_at: date


def _envelope(query_id: str, base_updated_at: date, payload: dict[str, Any]) -> dict[str, Any]:
    return {"query_id": query_id, "base_updated_at": base_updated_at.isoformat(), **payload}


def _normalize_cnpj(cnpj: str) -> str:
    import re

    return re.sub(r"\D", "", cnpj or "")


def register_tools(mcp: FastMCP, ctx_resolver: Any) -> None:
    """Register the 4 v0.1.0 match/check MCP tools.

    `ctx_resolver` is a callable `() -> RequestContext` invoked at tool-call time.
    The transport adapter (SSE in prod, test fixture in dev) is responsible for
    plumbing the per-request context.
    """

    @mcp.tool()
    def match_razao_social_tool(cnpj: str, nome: str, tolerance: float = 0.85) -> dict[str, Any]:
        """Verifica se o nome informado bate com a razão social registrada na Receita Federal pro CNPJ, sem expor a razão social registrada. Retorna match boolean + confidence + hint.

        NOTA: A base do Match exclui empresas MEI e CNPJs com situação cadastral não-ativa (suspensa/inapta/baixada/nula). CNPJs nesses casos retornam CNPJ_NOT_FOUND."""
        ctx = ctx_resolver()
        cnpj_norm = _normalize_cnpj(cnpj)
        record = ctx.repo.find_by_cnpj(cnpj_norm)
        if record is None:
            return _error_payload("CNPJ_NOT_FOUND")

        result = match_razao_social(nome, record.razao_social, tolerance=tolerance)
        summary = result.to_dict()
        entry = audit_log.build_entry(
            api_key_hash=ctx.auth_record.key_hash,
            tool="match_razao_social",
            payload={"cnpj": cnpj_norm, "nome": nome, "tolerance": tolerance},
            cnpj_basico=cnpj_norm[:8],
            response_summary=summary,
            base_updated_at=ctx.base_updated_at,
        )
        return _envelope(entry.query_id, ctx.base_updated_at, summary)

    @mcp.tool()
    def check_situacao_cadastral_tool(cnpj: str) -> dict[str, Any]:
        """Retorna a situação cadastral do CNPJ. A base do Match só inclui empresas ATIVAS — qualquer outro estado (suspensa/inapta/baixada/nula) ou empresa MEI retorna CNPJ_NOT_FOUND."""
        ctx = ctx_resolver()
        cnpj_norm = _normalize_cnpj(cnpj)
        record = ctx.repo.find_by_cnpj(cnpj_norm)
        if record is None:
            return _error_payload("CNPJ_NOT_FOUND")

        result = check_situacao_cadastral(
            record.situacao_cadastral_codigo,
            since=record.data_situacao_cadastral,
        )
        summary = result.to_dict()
        entry = audit_log.build_entry(
            api_key_hash=ctx.auth_record.key_hash,
            tool="check_situacao_cadastral",
            payload={"cnpj": cnpj_norm},
            cnpj_basico=cnpj_norm[:8],
            response_summary=summary,
            base_updated_at=ctx.base_updated_at,
        )
        return _envelope(entry.query_id, ctx.base_updated_at, summary)

    @mcp.tool()
    def check_porte_empresa_tool(cnpj: str) -> dict[str, Any]:
        """Retorna o porte (ME/EPP/DEMAIS) + flag de Simples Nacional do CNPJ. NOTA: A base do Match exclui MEI por design — is_mei sempre false."""
        ctx = ctx_resolver()
        cnpj_norm = _normalize_cnpj(cnpj)
        record = ctx.repo.find_by_cnpj(cnpj_norm)
        if record is None:
            return _error_payload("CNPJ_NOT_FOUND")

        result = check_porte_empresa(
            record.porte_empresa_codigo,
            opcao_simples=record.opcao_simples,
            opcao_mei=record.opcao_mei,
        )
        summary = result.to_dict()
        entry = audit_log.build_entry(
            api_key_hash=ctx.auth_record.key_hash,
            tool="check_porte_empresa",
            payload={"cnpj": cnpj_norm},
            cnpj_basico=cnpj_norm[:8],
            response_summary=summary,
            base_updated_at=ctx.base_updated_at,
        )
        return _envelope(entry.query_id, ctx.base_updated_at, summary)

    @mcp.tool()
    def match_uf_tool(cnpj: str, uf: str) -> dict[str, Any]:
        """Verifica se a UF informada bate com a UF registrada na Receita Federal pro CNPJ.

        NOTA: Base exclui MEI e CNPJs não-ativos — CNPJs nesses casos retornam CNPJ_NOT_FOUND."""
        ctx = ctx_resolver()
        cnpj_norm = _normalize_cnpj(cnpj)
        record = ctx.repo.find_by_cnpj(cnpj_norm)
        if record is None:
            return _error_payload("CNPJ_NOT_FOUND")

        result = match_uf(uf, record.uf)
        summary = result.to_dict()
        entry = audit_log.build_entry(
            api_key_hash=ctx.auth_record.key_hash,
            tool="match_uf",
            payload={"cnpj": cnpj_norm, "uf": uf},
            cnpj_basico=cnpj_norm[:8],
            response_summary=summary,
            base_updated_at=ctx.base_updated_at,
        )
        return _envelope(entry.query_id, ctx.base_updated_at, summary)


def _error_payload(code: str) -> dict[str, Any]:
    messages = {
        "CNPJ_NOT_FOUND": (
            "CNPJ não encontrado na base atual da Receita Federal.",
            "CNPJ not found in current Receita Federal base.",
        ),
    }
    pt, en = messages.get(code, (code, code))
    return {"error": {"code": code, "message_pt": pt, "message_en": en}}
