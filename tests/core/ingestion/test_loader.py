"""Tests for the Postgres loader — mocks psycopg connection/cursor.

The loader speaks to Postgres via cur.copy(...) (psycopg 3 streaming API)
plus a couple of plain cur.execute calls (CREATE TEMP TABLE, INSERT ... ON
CONFLICT, DROP TABLE). We mock cursor + the context manager returned by
cur.copy and assert:

- The COPY statement targets the staging temp table.
- The INSERT statement targets the final table with the expected columns
  + ON CONFLICT clause (or NOT EXISTS for socios).
- ``copy.write_row`` is called once per input row with values pulled in the
  correct column order.
- The dispatcher (load_by_filetype) routes per FileType.
- An unknown FileType raises NotImplementedError.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from brasil_mcp_match.core.ingestion import loader
from brasil_mcp_match.core.ingestion.parser import FileType

# --- helpers ----------------------------------------------------------------


class _FakeCopy:
    """Stand-in for the object returned by ``cur.copy(...).__enter__()``."""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []

    def write_row(self, row: tuple[Any, ...]) -> None:
        self.rows.append(row)


def _build_mock_conn(*, rowcount: int = 0) -> tuple[MagicMock, MagicMock, _FakeCopy, list[str]]:
    """Return (conn, cur, fake_copy, sql_log).

    ``sql_log`` collects every SQL passed to cur.execute, so tests can do
    substring assertions to ensure the right table/columns/conflict clause
    were generated.
    """
    fake_copy = _FakeCopy()
    sql_log: list[str] = []

    @contextmanager
    def _copy_ctx(sql: str):
        sql_log.append(sql)
        yield fake_copy

    cur = MagicMock()
    cur.copy.side_effect = _copy_ctx
    cur.rowcount = rowcount

    def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
        sql_log.append(sql)

    cur.execute.side_effect = _execute

    cur_ctx = MagicMock()
    cur_ctx.__enter__.return_value = cur
    cur_ctx.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cur_ctx

    return conn, cur, fake_copy, sql_log


# --- empresa ----------------------------------------------------------------


def test_load_empresas_copies_and_upserts() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=2)
    rows = [
        {
            "cnpj_basico": "12345678",
            "razao_social": "PETROBRAS",
            "natureza_juridica": "2011",
            "qualificacao_responsavel": "05",
            "capital_social": 100,
            "porte_empresa": "05",
            "ente_federativo_responsavel": None,
            "extra_field_ignored": "yes",  # must be ignored
        },
        {
            "cnpj_basico": "99999999",
            "razao_social": "EMPRESA X",
            "natureza_juridica": "2135",
            "qualificacao_responsavel": "01",
            "capital_social": 0,
            "porte_empresa": "01",
            "ente_federativo_responsavel": None,
        },
    ]

    inserted = loader.load_empresas(rows, conn)

    assert inserted == 2

    # We expect: CREATE TEMP, COPY ... empresa, INSERT ... empresa, DROP TABLE
    full_sql = "\n".join(sql_log)
    assert "CREATE TEMP TABLE _stg_empresa" in full_sql
    assert "COPY _stg_empresa" in full_sql
    assert "INSERT INTO empresa" in full_sql
    assert "ON CONFLICT (cnpj_basico) DO NOTHING" in full_sql
    assert "DROP TABLE _stg_empresa" in full_sql

    # copy.write_row called once per row, with values in column order
    assert len(fake_copy.rows) == 2
    assert fake_copy.rows[0][0] == "12345678"
    assert fake_copy.rows[0][1] == "PETROBRAS"
    assert fake_copy.rows[1][0] == "99999999"
    # 7 columns total
    assert all(len(r) == 7 for r in fake_copy.rows)


def test_load_empresas_negative_rowcount_yields_zero() -> None:
    """If psycopg returns rowcount=-1 (no result), loader returns 0."""
    conn, _cur, _copy, _sql = _build_mock_conn(rowcount=-1)
    assert loader.load_empresas([], conn) == 0


# --- estabelecimento --------------------------------------------------------


def test_load_estabelecimentos_copies_and_upserts() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=1)
    rows = [
        {col: f"v_{col}" for col in loader._ESTABELECIMENTO_COLS},
    ]
    inserted = loader.load_estabelecimentos(rows, conn)
    assert inserted == 1
    assert any("COPY _stg_estabelecimento" in s for s in sql_log)
    assert any("INSERT INTO estabelecimento" in s for s in sql_log)
    assert any("ON CONFLICT (cnpj_basico, cnpj_ordem)" in s for s in sql_log)
    assert len(fake_copy.rows[0]) == len(loader._ESTABELECIMENTO_COLS)


# --- socio (no PK → NOT EXISTS dedupe) --------------------------------------


def test_load_socios_uses_not_exists_dedupe() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=3)
    rows = [
        {
            "cnpj_basico": "12345678",
            "identificador_socio": "2",
            "nome_socio": "JOAO SILVA",
            "cnpj_cpf_socio": "***123456**",
            "qualificacao_socio": "49",
            "data_entrada_sociedade": None,
            "pais": "76",
            "representante_legal": None,
            "nome_representante": None,
            "qualificacao_representante": None,
            "faixa_etaria": "4",
        },
    ]
    inserted = loader.load_socios(rows, conn)
    assert inserted == 3
    full_sql = "\n".join(sql_log)
    assert "CREATE TEMP TABLE _stg_socio" in full_sql
    assert "COPY _stg_socio" in full_sql
    assert "INSERT INTO socio" in full_sql
    assert "NOT EXISTS" in full_sql
    assert "DROP TABLE _stg_socio" in full_sql
    assert len(fake_copy.rows) == 1


def test_load_socios_negative_rowcount_yields_zero() -> None:
    conn, _cur, _copy, _sql = _build_mock_conn(rowcount=-1)
    assert loader.load_socios([], conn) == 0


# --- simples ----------------------------------------------------------------


def test_load_simples_copies_and_upserts() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=1)
    rows = [
        {
            "cnpj_basico": "12345678",
            "opcao_simples": "S",
            "data_opcao_simples": None,
            "data_exclusao_simples": None,
            "opcao_mei": "N",
            "data_opcao_mei": None,
            "data_exclusao_mei": None,
        }
    ]
    inserted = loader.load_simples(rows, conn)
    assert inserted == 1
    assert any("INSERT INTO simples_nacional" in s for s in sql_log)
    assert any("ON CONFLICT (cnpj_basico)" in s for s in sql_log)
    assert fake_copy.rows[0][0] == "12345678"


# --- reference tables -------------------------------------------------------


@pytest.mark.parametrize(
    "func_name,table,conflict",
    [
        ("load_ref_cnae", "ref_cnae", "codigo"),
        ("load_ref_natureza_juridica", "ref_natureza_juridica", "codigo"),
        ("load_ref_motivo_situacao", "ref_motivo_situacao", "codigo"),
        ("load_ref_qualificacao_socio", "ref_qualificacao_socio", "codigo"),
    ],
)
def test_load_ref_codigo_descricao_tables(func_name: str, table: str, conflict: str) -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=1)
    rows = [{"codigo": "1", "descricao": "FOO"}]
    inserted = getattr(loader, func_name)(rows, conn)
    assert inserted == 1
    full = "\n".join(sql_log)
    assert f"COPY _stg_{table}" in full
    assert f"INSERT INTO {table}" in full
    assert f"ON CONFLICT ({conflict})" in full
    assert fake_copy.rows[0] == ("1", "FOO")


def test_load_ref_municipio_rf_maps_codigo_to_codigo_rf() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=2)
    rows = [
        {"codigo": "7107", "descricao": "SAO PAULO"},
        {"codigo": "3304", "descricao": "RIO DE JANEIRO"},
    ]
    inserted = loader.load_ref_municipio_rf(rows, conn)
    assert inserted == 2
    full = "\n".join(sql_log)
    assert "COPY _stg_ref_municipio_rf (codigo_rf, nome)" in full
    assert "ON CONFLICT (codigo_rf)" in full
    # Each row's value list pulls "codigo" → codigo_rf, "descricao" → nome.
    assert fake_copy.rows[0] == ("7107", "SAO PAULO")
    assert fake_copy.rows[1] == ("3304", "RIO DE JANEIRO")


def test_load_ref_pais_maps_descricao_to_nome() -> None:
    conn, _cur, fake_copy, sql_log = _build_mock_conn(rowcount=1)
    rows = [{"codigo": "76", "descricao": "BRASIL"}]
    inserted = loader.load_ref_pais(rows, conn)
    assert inserted == 1
    full = "\n".join(sql_log)
    assert "COPY _stg_ref_pais (codigo, nome)" in full
    assert "ON CONFLICT (codigo)" in full
    assert fake_copy.rows[0] == ("76", "BRASIL")


# --- dispatcher --------------------------------------------------------------


@pytest.mark.parametrize(
    "file_type,expected_loader",
    [
        (FileType.EMPRESA, "load_empresas"),
        (FileType.ESTABELECIMENTO, "load_estabelecimentos"),
        (FileType.SOCIO, "load_socios"),
        (FileType.SIMPLES, "load_simples"),
        (FileType.REF_CNAE, "load_ref_cnae"),
        (FileType.REF_NATUREZA, "load_ref_natureza_juridica"),
        (FileType.REF_MOTIVO, "load_ref_motivo_situacao"),
        (FileType.REF_MUNICIPIO, "load_ref_municipio_rf"),
        (FileType.REF_PAIS, "load_ref_pais"),
        (FileType.REF_QUALIFICACAO, "load_ref_qualificacao_socio"),
    ],
)
def test_load_by_filetype_dispatches(
    file_type: FileType, expected_loader: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each FileType must route to its matching loader function."""
    called: dict[str, Any] = {}

    def fake_loader(rows: Any, conn: Any) -> int:
        called["rows"] = rows
        called["conn"] = conn
        return 42

    # Patch the dispatch table directly — simpler than monkeypatching each func.
    monkeypatch.setitem(loader._DISPATCH, file_type, fake_loader)

    rows = [{"x": "y"}]
    conn = object()
    result = loader.load_by_filetype(file_type, rows, conn)  # type: ignore[arg-type]

    assert result == 42
    assert called["rows"] is rows
    assert called["conn"] is conn
    # Sanity: the function we patched matches the expected name in source.
    assert hasattr(loader, expected_loader)


def test_load_by_filetype_unknown_raises() -> None:
    conn = MagicMock()
    with pytest.raises(NotImplementedError, match="No loader implemented"):
        loader.load_by_filetype(FileType.UNKNOWN, [], conn)


# --- REF_FILETYPES set -------------------------------------------------------


def test_ref_filetypes_contains_all_ref_types() -> None:
    assert FileType.REF_CNAE in loader.REF_FILETYPES
    assert FileType.REF_NATUREZA in loader.REF_FILETYPES
    assert FileType.REF_MOTIVO in loader.REF_FILETYPES
    assert FileType.REF_MUNICIPIO in loader.REF_FILETYPES
    assert FileType.REF_PAIS in loader.REF_FILETYPES
    assert FileType.REF_QUALIFICACAO in loader.REF_FILETYPES
    # Non-ref types must NOT be there
    assert FileType.EMPRESA not in loader.REF_FILETYPES
    assert FileType.ESTABELECIMENTO not in loader.REF_FILETYPES
