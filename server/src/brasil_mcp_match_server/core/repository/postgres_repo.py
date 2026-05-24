"""Concrete CnpjRepo backed by Postgres.

Joins ``empresa`` + ``estabelecimento`` + ``simples_nacional`` and resolves
``municipio_nome`` via ``ref_municipio_rf`` to materialize an
``EmpresaRecord``. The query receives the full 14-digit CNPJ and uses
``estabelecimento.cnpj_completo`` (a STORED generated column) to locate the
right row — works for both matriz (xxxx0001xx) and filiais (xxxx0002xx etc).
"""

from __future__ import annotations

from typing import Any

import psycopg

from brasil_mcp_match_server.core.repository.cnpj_repo import EmpresaRecord

_FIND_BY_CNPJ_SQL = """
    SELECT
        e.cnpj_basico,
        e.razao_social,
        e.porte_empresa AS porte_empresa_codigo,
        e.capital_social,
        est.situacao_cadastral AS situacao_cadastral_codigo,
        est.data_situacao_cadastral,
        est.data_inicio_atividade,
        est.uf,
        m.nome AS municipio_nome,
        est.cep,
        est.cnae_principal,
        est.nome_fantasia,
        s.opcao_simples,
        s.opcao_mei
    FROM estabelecimento est
    JOIN empresa e ON e.cnpj_basico = est.cnpj_basico
    LEFT JOIN ref_municipio_rf m ON m.codigo_rf = est.municipio
    LEFT JOIN simples_nacional s ON s.cnpj_basico = e.cnpj_basico
    WHERE est.cnpj_completo = %s
    LIMIT 1
"""


class PostgresCnpjRepo:
    """Concrete ``CnpjRepo`` using psycopg. Receives an open connection."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def find_by_cnpj(self, cnpj_completo: str) -> EmpresaRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(_FIND_BY_CNPJ_SQL, (cnpj_completo,))
            row = cur.fetchone()
        if row is None:
            return None
        # ``dict_row`` factory yields a mapping; convert porte to str (schema
        # stores it as SMALLINT but the dataclass wants str | None to keep
        # downstream matchers stable).
        porte_raw = row["porte_empresa_codigo"]
        situacao_raw = row["situacao_cadastral_codigo"]
        return EmpresaRecord(
            cnpj_basico=row["cnpj_basico"],
            razao_social=row["razao_social"],
            porte_empresa_codigo=str(porte_raw) if porte_raw is not None else None,
            capital_social=row["capital_social"],
            situacao_cadastral_codigo=(str(situacao_raw) if situacao_raw is not None else None),
            data_situacao_cadastral=row["data_situacao_cadastral"],
            data_inicio_atividade=row["data_inicio_atividade"],
            uf=row["uf"],
            municipio_nome=row["municipio_nome"],
            cep=row["cep"],
            cnae_principal=row["cnae_principal"],
            nome_fantasia=row["nome_fantasia"],
            opcao_simples=row["opcao_simples"],
            opcao_mei=row["opcao_mei"],
        )
