"""CNPJ repository interface — abstrai acesso a Postgres.

A interface é definida como Protocol pra permitir injetar mocks em tests
sem subir DB real. A implementação concreta (`PostgresCnpjRepo`) usa
psycopg via `connect()`.

Os adapters (REST/MCP) recebem um Protocol e não conhecem psycopg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmpresaRecord:
    """Subset of empresa + matriz estabelecimento needed for match/check tools.

    Composição: empresa (cnpj_basico, razao_social, capital_social, porte) +
    estabelecimento matriz (situacao_cadastral, data_situacao, uf, municipio_nome,
    cep, cnae_principal, data_inicio_atividade) + simples_nacional (opcao_simples,
    opcao_mei).
    """

    cnpj_basico: str
    razao_social: str
    porte_empresa_codigo: str | None
    capital_social: Decimal | None
    situacao_cadastral_codigo: str | None
    data_situacao_cadastral: date | None
    data_inicio_atividade: date | None
    uf: str | None
    municipio_nome: str | None
    cep: str | None
    cnae_principal: str | None
    nome_fantasia: str | None
    opcao_simples: str | None
    opcao_mei: str | None


class CnpjRepo(Protocol):
    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        """Look up an empresa by full 14-char CNPJ. Returns None if not found."""
        ...
