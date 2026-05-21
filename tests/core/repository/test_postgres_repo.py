"""Tests for PostgresCnpjRepo — mocks the psycopg connection.

We don't need a real DB: the repo accepts an already-open connection. We feed
it a MagicMock cursor whose ``fetchone`` returns a pre-canned dict (matching
the dict_row factory shape) and assert the resulting EmpresaRecord.

We also assert the SQL contains the expected joins so any future schema change
breaks here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from brasil_mcp_match.core.repository.cnpj_repo import EmpresaRecord
from brasil_mcp_match.core.repository.postgres_repo import PostgresCnpjRepo


def _build_conn_returning(row: dict | None) -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur_ctx = MagicMock()
    cur_ctx.__enter__.return_value = cur
    cur_ctx.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    return conn, cur


def _matriz_row() -> dict:
    return {
        "cnpj_basico": "12345678",
        "razao_social": "PETROBRAS",
        "porte_empresa_codigo": 5,  # SMALLINT in schema
        "capital_social": Decimal("100.00"),
        "situacao_cadastral_codigo": 2,
        "data_situacao_cadastral": date(2024, 1, 15),
        "data_inicio_atividade": date(2010, 2, 1),
        "uf": "SP",
        "municipio_nome": "SAO PAULO",
        "cep": "01310200",
        "cnae_principal": "4731800",
        "nome_fantasia": "POSTO DA ESQUINA",
        "opcao_simples": "S",
        "opcao_mei": "N",
    }


# --- matriz -----------------------------------------------------------------


def test_find_by_cnpj_matriz_returns_record() -> None:
    conn, cur = _build_conn_returning(_matriz_row())
    repo = PostgresCnpjRepo(conn)
    rec = repo.find_by_cnpj("12345678000195")

    assert isinstance(rec, EmpresaRecord)
    assert rec.cnpj_basico == "12345678"
    assert rec.razao_social == "PETROBRAS"
    assert rec.porte_empresa_codigo == "5"
    assert rec.capital_social == Decimal("100.00")
    assert rec.situacao_cadastral_codigo == "2"
    assert rec.data_situacao_cadastral == date(2024, 1, 15)
    assert rec.data_inicio_atividade == date(2010, 2, 1)
    assert rec.uf == "SP"
    assert rec.municipio_nome == "SAO PAULO"
    assert rec.cep == "01310200"
    assert rec.cnae_principal == "4731800"
    assert rec.nome_fantasia == "POSTO DA ESQUINA"
    assert rec.opcao_simples == "S"
    assert rec.opcao_mei == "N"

    # Verify the SQL contains expected joins + uses cnpj_completo binding.
    sql = cur.execute.call_args[0][0]
    assert "FROM estabelecimento" in sql
    assert "JOIN empresa" in sql
    assert "LEFT JOIN ref_municipio_rf" in sql
    assert "LEFT JOIN simples_nacional" in sql
    assert "est.cnpj_completo" in sql
    # And the CNPJ was passed as the bound param.
    assert cur.execute.call_args[0][1] == ("12345678000195",)


# --- filial -----------------------------------------------------------------


def test_find_by_cnpj_filial_works_via_cnpj_completo() -> None:
    row = _matriz_row()
    row["nome_fantasia"] = "FILIAL RIO"
    conn, cur = _build_conn_returning(row)
    repo = PostgresCnpjRepo(conn)
    rec = repo.find_by_cnpj("12345678000201")  # filial (0002)
    assert rec is not None
    assert rec.nome_fantasia == "FILIAL RIO"
    # SQL is the same — the lookup is purely on cnpj_completo.
    assert cur.execute.call_args[0][1] == ("12345678000201",)


# --- not found --------------------------------------------------------------


def test_find_by_cnpj_returns_none_when_no_row() -> None:
    conn, _cur = _build_conn_returning(None)
    repo = PostgresCnpjRepo(conn)
    assert repo.find_by_cnpj("00000000000000") is None


# --- null-handling on optional ints ----------------------------------------


def test_find_by_cnpj_null_porte_and_situacao_yield_none() -> None:
    row = _matriz_row()
    row["porte_empresa_codigo"] = None
    row["situacao_cadastral_codigo"] = None
    conn, _cur = _build_conn_returning(row)
    repo = PostgresCnpjRepo(conn)
    rec = repo.find_by_cnpj("12345678000195")
    assert rec is not None
    assert rec.porte_empresa_codigo is None
    assert rec.situacao_cadastral_codigo is None
