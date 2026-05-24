"""Brasil MCP Match — Fase 2. Verificação privacy-preserving contra base RF."""

from __future__ import annotations

__version__ = "0.2.1"


def lookup_cnpj(cnpj_completo: str):
    """Look up an empresa by full 14-char CNPJ.

    Returns an EmpresaRecord or None if the CNPJ is not in the base
    (inexistente, MEI, or não-ativa — all map to None per v0.1.1+ filters).

    Public API for other packages in the Brasil MCP family (notably
    `brasil-mcp-leads`) to consume RFB data without coupling to the internal
    PostgresCnpjRepo class.
    """
    from brasil_mcp_match_server.core.repository.connection import connect
    from brasil_mcp_match_server.core.repository.postgres_repo import PostgresCnpjRepo

    with connect() as conn:
        repo = PostgresCnpjRepo(conn)
        return repo.find_by_cnpj(cnpj_completo)
