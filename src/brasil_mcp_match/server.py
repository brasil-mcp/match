"""MCP stdio server — forwards each tool call to the Brasil MCP Match REST API.

Tools mirror the upstream 4 v0.1.0 verifiers plus 2 v0.4.0 self-service signup
tools plus 5 v0.5.0 sócio-verification tools. Each call is an HTTPS POST
against ``BRASIL_MCP_MATCH_URL``. Verifier and sócio calls send
``X-Brasil-MCP-Key``; signup calls are unauthenticated.

``BRASIL_MCP_MATCH_KEY`` is optional. Without it the signup tools still work,
and the verifier + sócio tools return a ``MISSING_API_KEY`` error envelope
guiding the user to ``request_api_key``.
"""
# pyright: reportUnusedFunction=false
# (FastMCP collects the @mcp.tool() decorated inner functions via side effect.)

from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from brasil_mcp_match.client import ClientConfig, MatchHttpClient, load_config_from_env


def _missing_key_envelope() -> dict[str, Any]:
    return {
        "error": {
            "code": "MISSING_API_KEY",
            "message_pt": (
                "Configure BRASIL_MCP_MATCH_KEY no seu cliente MCP. Use a tool "
                "'request_api_key' pra solicitar uma chave."
            ),
            "message_en": (
                "BRASIL_MCP_MATCH_KEY is not configured. Use the 'request_api_key' "
                "tool to get one."
            ),
        }
    }


_STORE_KEY_WARNING_FREE = {
    "code": "STORE_KEY_NOW",
    "message_pt": (
        "Salve esta API key AGORA — não será enviada novamente. Plano free não "
        "permite regenerar (1 key por email lifetime)."
    ),
    "message_en": (
        "Save this API key NOW — will not be sent again. Free plan does not "
        "allow regeneration (1 key per email lifetime)."
    ),
}

_STORE_KEY_WARNING_PAID = {
    "code": "STORE_KEY_NOW",
    "message_pt": (
        "Salve esta API key AGORA — não será enviada novamente. Backup foi "
        "enviado pro seu email. Se perder, refaça signup com novo pagamento."
    ),
    "message_en": (
        "Save this API key NOW — will not be returned again. A backup was "
        "emailed to you. If lost, sign up again with a new payment."
    ),
}


def _augment_signup_start(result: dict[str, Any]) -> dict[str, Any]:
    """Add a friendly ``next_steps`` field + ``warning`` to a signup_start response.

    Error envelopes (i.e. dicts with an ``error`` key) pass through unchanged.
    Server-provided ``warning`` is preserved (no double-wrapping).
    """
    if "error" in result:
        return result

    status = result.get("status")
    if status == "delivered":
        result.setdefault(
            "next_steps",
            (
                "CRITICAL: Store this api_key NOW (e.g., in a password manager). "
                "It will NOT be returned again. Add it as BRASIL_MCP_MATCH_KEY in "
                "your MCP client config (e.g., claude_desktop_config.json) and "
                "restart the client to enable the verifier tools."
            ),
        )
        result.setdefault("warning", _STORE_KEY_WARNING_FREE)
    elif status == "pending":
        polling_token = result.get("polling_token")
        result.setdefault(
            "next_steps",
            (
                "Open the checkout_url in your browser to pay. After payment, call "
                f"check_signup_status({polling_token!r}) to retrieve your API key."
            ),
        )
    return result


def _augment_signup_status(result: dict[str, Any]) -> dict[str, Any]:
    """Add ``warning`` + leading-CRITICAL ``next_steps`` when status returns the key.

    Only paid first-call (status=paid with api_key) gets augmented. Pending,
    delivered (no key), and error envelopes pass through unchanged.
    Server-provided fields are preserved.
    """
    if "error" in result:
        return result

    if result.get("status") == "paid" and "api_key" in result:
        result.setdefault(
            "next_steps",
            (
                "CRITICAL: Store this api_key NOW. It will NOT be returned again "
                "— subsequent calls to check_signup_status with this token will "
                "return status=delivered without the key. (Backup: the same key "
                "was also emailed to your address on file.) Add it as "
                "BRASIL_MCP_MATCH_KEY in your MCP client config."
            ),
        )
        result.setdefault("warning", _STORE_KEY_WARNING_PAID)
    return result


def build_server(client: MatchHttpClient, config: ClientConfig) -> FastMCP:
    """Construct a FastMCP server and register the 4 verifier + 2 signup + 5 sócio tools."""
    mcp = FastMCP("brasil-mcp-match-client")

    def _require_key_or_error() -> dict[str, Any] | None:
        if not config.has_api_key:
            return _missing_key_envelope()
        return None

    @mcp.tool()
    async def match_razao_social_tool(
        cnpj: str, nome: str, tolerance: float = 0.85
    ) -> dict[str, Any]:
        """Verifica se o nome informado bate com a razão social registrada na Receita Federal pro CNPJ, sem expor a razão social registrada. Retorna match boolean + confidence + hint.

        NOTA: A base do Match exclui empresas MEI e CNPJs com situação cadastral não-ativa (suspensa/inapta/baixada/nula). CNPJs nesses casos retornam CNPJ_NOT_FOUND."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.match_razao_social(cnpj, nome, tolerance)

    @mcp.tool()
    async def check_situacao_cadastral_tool(cnpj: str) -> dict[str, Any]:
        """Retorna a situação cadastral do CNPJ. A base do Match só inclui empresas ATIVAS — qualquer outro estado (suspensa/inapta/baixada/nula) ou empresa MEI retorna CNPJ_NOT_FOUND."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.check_situacao_cadastral(cnpj)

    @mcp.tool()
    async def check_porte_empresa_tool(cnpj: str) -> dict[str, Any]:
        """Retorna o porte da empresa (MEI/ME/EPP/DEMAIS) + flag is_simples."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.check_porte_empresa(cnpj)

    @mcp.tool()
    async def match_uf_tool(cnpj: str, uf: str) -> dict[str, Any]:
        """Verifica se a UF informada bate com a UF do endereço registrado pro CNPJ. Retorna boolean (sem expor o endereço)."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.match_uf(cnpj, uf)

    @mcp.tool()
    async def request_api_key(
        email: str, plan: str = "free", cpf_cnpj: str | None = None
    ) -> dict[str, Any]:
        """Solicita uma nova API key Brasil MCP Match. Plans: 'free' (50 queries/mês, 10/dia, sem cartão) | 'starter' | 'pro' | 'enterprise'. Free: retorna a key direto. Paid: retorna checkout_url + polling_token; pague na URL, depois chame check_signup_status(polling_token) pra retrieve a key."""
        result = await client.signup_start(email, plan, cpf_cnpj)
        return _augment_signup_start(result)

    @mcp.tool()
    async def check_signup_status(polling_token: str) -> dict[str, Any]:
        """Consulta status de um signup pendente. Use o polling_token que veio do request_api_key. Retorna {status:'pending'} (aguarde pagamento), {status:'paid', api_key, plan} (1ª chamada após Asaas confirmar — copie a key pra BRASIL_MCP_MATCH_KEY no seu config), {status:'delivered'} (key já entregue, regenere via novo request_api_key) ou 410 SIGNUP_EXPIRED."""
        result = await client.signup_status(polling_token)
        return _augment_signup_status(result)

    @mcp.tool()
    async def match_nome_socio_tool(
        cnpj: str, nome: str, tolerance: float = 0.85
    ) -> dict[str, Any]:
        """Verifica se o nome informado bate com algum sócio registrado do CNPJ. Retorna match boolean + hint + confidence, sem revelar QUAL sócio nem o nome completo do sócio matched. Útil pra KYC ('o cliente que está abrindo conta é sócio?'), anti-fraude e due diligence. Hints: 'exact' | 'fuzzy_prefix' | 'fuzzy_word' | 'fuzzy_phonetic' | 'no_match'. NOTA: a base do Match exclui MEI e CNPJs não-ativos — CNPJs nesses casos retornam CNPJ_NOT_FOUND."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.socio_match_nome(cnpj, nome, tolerance)

    @mcp.tool()
    async def match_cpf_socio_tool(cnpj: str, cpf: str) -> dict[str, Any]:
        """Verifica se o CPF informado é de algum sócio PF do CNPJ. Retorna match boolean. Privacy-preserving: a base só armazena CPF mascarado pela RF (***XXXXXX**), e a tool compara o middle do CPF informado sem revelar dados. Útil pra KYC, anti-fraude e validação de assinatura. CNPJs MEI ou não-ativos retornam CNPJ_NOT_FOUND."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.socio_match_cpf(cnpj, cpf)

    @mcp.tool()
    async def match_cnpj_socio_tool(cnpj: str, cnpj_socio: str) -> dict[str, Any]:
        """Verifica se a empresa cnpj_socio está registrada como sócia PJ do CNPJ pai. Útil pra detectar holdings, estrutura societária e cross-ownership. Retorna match boolean. CNPJs MEI ou não-ativos retornam CNPJ_NOT_FOUND."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.socio_match_cnpj_socio(cnpj, cnpj_socio)

    @mcp.tool()
    async def check_qualificacao_socio_tool(
        cnpj: str, qualificacao: int
    ) -> dict[str, Any]:
        """Verifica se o CNPJ tem algum sócio com a qualificação informada (código RF — ver tabela ref_qualificacao_socio: 5=Administrador, 22=Sócio, 49=Sócio-Administrador, 65=Titular Pessoa Física Residente ou Domiciliado no Brasil, etc.). Retorna {exists: bool, count: int} sem revelar quem. Útil pra compliance e validação de assinatura autorizada."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.socio_check_qualificacao(cnpj, qualificacao)

    @mcp.tool()
    async def count_socios_tool(cnpj: str) -> dict[str, Any]:
        """Retorna o total de sócios do CNPJ + breakdown por identificador (PF/PJ/estrangeiro). Apenas agregação numérica — nenhum dado pessoal exposto. Útil pra entender porte do quadro societário antes de outras checks."""
        err = _require_key_or_error()
        if err is not None:
            return err
        return await client.socio_count(cnpj)

    return mcp


def main() -> None:  # pragma: no cover - thin entry, exercised via stdio runtime
    """Read env, build the FastMCP server, serve stdio. Never returns."""
    try:
        config = load_config_from_env()
    except RuntimeError as exc:
        print(f"[brasil-mcp-match-client] {exc}", file=sys.stderr)
        sys.exit(2)

    client = MatchHttpClient(config)
    mcp = build_server(client, config)
    try:
        mcp.run(transport="stdio")
    finally:
        asyncio.run(client.aclose())


if __name__ == "__main__":  # pragma: no cover
    main()
