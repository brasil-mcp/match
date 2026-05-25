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


@dataclass(frozen=True, slots=True)
class SocioRecord:
    """Subset of the ``socio`` row needed for the privacy-preserving matchers.

    Carries only fields the match/check/count functions consume — names and
    CPF mask digits stay inside the matcher and are NEVER returned to the
    caller. ``cnpj_cpf_socio`` arrives already masked by RF as ``***DDDDDD**``
    when ``identificador_socio == 1`` (PF) and as the full 14-digit CNPJ
    string when ``identificador_socio == 2`` (PJ).
    """

    nome_socio: str
    cnpj_cpf_socio: str | None
    identificador_socio: int | None  # 1 = PF, 2 = PJ, 3 = estrangeiro
    qualificacao_socio: int | None


class CnpjRepo(Protocol):
    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        """Look up an empresa by full 14-char CNPJ. Returns None if not found."""
        ...

    def find_socios_by_cnpj_basico(self, cnpj_basico: str) -> list[SocioRecord]:
        """Return all sócios for the given 8-char CNPJ básico. Empty list if none."""
        ...

    def qualificacao_codigo_exists(self, codigo: int) -> bool:
        """True if ``codigo`` is a known ``ref_qualificacao_socio`` code."""
        ...
